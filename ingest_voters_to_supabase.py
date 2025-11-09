#!/usr/bin/env python3
"""
ingest_voters_to_supabase.py

Process a folder of PDFs:
- Upload full PDF to Supabase Storage
- Split into single-page PDFs and upload each
- OCR page images (optionally from a crop rect) to extract voter IDs
- Clean IDs (remove '/')
- Insert rows into `2002_voter_details`:
    voter_id, single_page_pdf_link, full_pdf_link

Env vars required:
  SUPABASE_URL
  SUPABASE_ANON_KEY
  SUPABASE_BUCKET   (e.g., 'voters')

Usage:
  python ingest_voters_to_supabase.py /path/to/folder \
      --table 2002_voter_details \
      --storage-prefix voters_2002 \
      --dpi 200 \
      --ocr-crop 457.44,108,105.6,702   # (x,y,w,h) in points by default
# If you prefer pixels, add: --units px --dpi 300
"""

from __future__ import annotations
import argparse
import os
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple
from dotenv import load_dotenv

load_dotenv()
import fitz  # PyMuPDF
from supabase import create_client, Client

from ocr_cloud_vision import ocr_image_texts


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingest voter IDs from PDFs into Supabase.")
    p.add_argument("pdf_folder", help="Folder containing PDFs.")
    p.add_argument("--table", default="2002_voter_details", help="Supabase table name.")
    p.add_argument(
        "--storage-prefix", default="voters", help="Prefix/path in the bucket."
    )
    p.add_argument("--dpi", type=int, default=200, help="Render DPI for OCR images.")
    p.add_argument(
        "--ocr-crop",
        type=str,
        default="",
        help="Crop rect for OCR as 'x,y,w,h'. Default: full page.",
    )
    p.add_argument(
        "--units",
        choices=["pt", "px"],
        default="pt",
        help="Units for --ocr-crop. 'pt' (PDF points) or 'px' (pixels).",
    )
    p.add_argument(
        "--langs",
        type=str,
        default="en",
        help="Comma-separated language hints for OCR (e.g., 'en,bn').",
    )
    p.add_argument(
        "--regex",
        type=str,
        default=r"[A-Za-z0-9/]+",
        help="Regex to extract candidate voter IDs from OCR lines.",
    )
    return p.parse_args()


def supabase_client_from_env() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_ANON_KEY")
    bucket = os.getenv("SUPABASE_BUCKET")
    missing = [
        k
        for k, v in [
            ("SUPABASE_URL", url),
            ("SUPABASE_ANON_KEY", key),
            ("SUPABASE_BUCKET", bucket),
        ]
        if not v
    ]
    if missing:
        print(f"Missing env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    return create_client(url, key)


def upload_and_get_url(
    supa: Client, bucket: str, storage_path: str, local_path: Path
) -> str:
    # Choose content type based on file extension
    ext = local_path.suffix.lower()
    if ext == ".pdf":
        content_type = "application/pdf"
    elif ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"):
        content_type = f"image/{ext.lstrip('.') if ext != '.jpg' else 'jpeg'}"
    else:
        content_type = "application/octet-stream"

    with open(local_path, "rb") as f:
        # NOTE: storage3 expects 'upsert' as a STRING header value ("true"/"false"), not a boolean.
        supa.storage.from_(bucket).upload(
            path=storage_path,
            file=f,
            file_options={
                "upsert": "true",  # must be a string
                "contentType": content_type,  # set a sensible content type
            },
        )
    # Return a public URL (if the bucket is public). For private buckets, switch to signed URLs.
    return supa.storage.from_(bucket).get_public_url(storage_path)


def render_page_for_ocr(
    page: fitz.Page, dpi: int, crop_rect_pt: Optional[Tuple[float, float, float, float]]
) -> bytes:
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    clip = fitz.Rect(*crop_rect_pt) if crop_rect_pt else None
    pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
    return pix.tobytes("png")


def convert_crop_units(
    crop_str: str, units: str, dpi: int
) -> Optional[Tuple[float, float, float, float]]:
    if not crop_str:
        return None
    try:
        x, y, w, h = [float(s.strip()) for s in crop_str.split(",")]
    except Exception:
        raise ValueError("Invalid --ocr-crop. Expected 'x,y,w,h' numbers.")
    if units == "px":
        scale = 72.0 / dpi
        x, y, w, h = x * scale, y * scale, w * scale, h * scale
    return (x, y, x + w, y + h)


def clean_voter_id(raw: str) -> str:
    return raw.replace("/", "").strip()


def extract_ids_from_lines(lines: List[str], pattern: str) -> List[str]:
    import re

    rx = re.compile(pattern)
    ids: List[str] = []
    for ln in lines:
        for m in rx.findall(ln):
            cleaned = clean_voter_id(m)
            if cleaned:
                ids.append(cleaned)
    seen = set()
    uniq: List[str] = []
    for vid in ids:
        if vid not in seen:
            seen.add(vid)
            uniq.append(vid)
    return uniq


def ensure_pdf_suffix(path: Path) -> Path:
    return path if path.suffix.lower() == ".pdf" else path.with_suffix(".pdf")


def main():
    args = parse_args()
    folder = Path(args.pdf_folder)
    if not folder.exists() or not folder.is_dir():
        print(f"Folder not found: {folder}", file=sys.stderr)
        sys.exit(1)

    supa = supabase_client_from_env()
    bucket = os.getenv("SUPABASE_BUCKET")
    table = args.table
    storage_prefix = args.storage_prefix.strip("/")

    crop_rect_pt = convert_crop_units(args.ocr_crop, args.units, args.dpi)
    lang_list = [s.strip() for s in args.langs.split(",") if s.strip()]

    pdf_files = sorted([p for p in folder.iterdir() if p.suffix.lower() == ".pdf"])
    if not pdf_files:
        print("No PDFs found.", file=sys.stderr)
        sys.exit(1)

    for pdf_path in pdf_files:
        print(f"Processing: {pdf_path.name}")

        # Upload full PDF
        full_storage_path = f"{storage_prefix}/{pdf_path.stem}/{pdf_path.name}"
        full_url = upload_and_get_url(supa, bucket, full_storage_path, pdf_path)
        print(f"  Uploaded full PDF -> {full_url}")

        # Iterate pages and build single-page PDFs
        doc = fitz.open(pdf_path)
        for i in range(doc.page_count):
            page_num = i + 1
            page = doc.load_page(i)

            # Single-page PDF temp save + upload
            single_doc = fitz.open()
            single_doc.insert_pdf(doc, from_page=i, to_page=i)
            with tempfile.TemporaryDirectory() as td:
                single_local = Path(td) / f"{pdf_path.stem}_p{page_num:04d}.pdf"
                single_doc.save(single_local)
                single_doc.close()

                single_storage_path = (
                    f"{storage_prefix}/{pdf_path.stem}/pages/{single_local.name}"
                )
                single_url = upload_and_get_url(
                    supa, bucket, single_storage_path, single_local
                )
                print(f"    Page {page_num}: uploaded single-page -> {single_url}")

            # OCR (optionally using crop)
            img_bytes = render_page_for_ocr(page, args.dpi, crop_rect_pt)
            try:
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                    tf.write(img_bytes)
                    tmp_png = Path(tf.name)
                lines = ocr_image_texts(str(tmp_png), language_hints=lang_list)
            finally:
                try:
                    tmp_png.unlink(missing_ok=True)
                except Exception:
                    pass

            voter_ids = extract_ids_from_lines(lines, args.regex)
            if not voter_ids:
                print(f"      (no voter IDs detected on page {page_num})")
                continue

            rows = [
                {
                    "voter_id": vid,
                    "single_page_pdf_link": single_url,
                    "full_pdf_link": full_url,
                }
                for vid in voter_ids
            ]

            resp = supa.table(table).insert(rows).execute()
            if getattr(resp, "error", None):
                print(f"      Insert error: {resp.error}", file=sys.stderr)
            else:
                print(f"      Inserted {len(rows)} rows for page {page_num}")

        doc.close()

    print("Done.")


if __name__ == "__main__":
    main()
