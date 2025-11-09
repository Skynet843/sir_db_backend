#!/usr/bin/env python3
"""
pdf_cropper.py

Extract a specified rectangular area from every page of a PDF and save it as images,
and also save the full page image for each page.

Default crop: x=457.44, y=108, w=105.6, h=702  (PDF points)

Coordinates are in PDF points (1 point = 1/72 inch), with (0,0) at the top-left.
Examples:
  python pdf_crop.py input.pdf --respect
  python pdf_crop.py input.pdf --x 460 --y 110 --w 110 --h 700 --respect
"""

import argparse
import sys
from pathlib import Path
import fitz  # PyMuPDF


def parse_args():
    p = argparse.ArgumentParser(
        description="Crop an area from each page of a PDF and save as images."
    )
    p.add_argument("pdf_path", type=str, help="Path to the input PDF.")
    p.add_argument(
        "--out",
        type=str,
        default=None,
        help="Output directory (default: <pdf_name>_crops)",
    )

    # Your default crop
    p.add_argument("--x", type=float, default=457.44, help="Left (points)")
    p.add_argument("--y", type=float, default=108.0, help="Top (points)")
    p.add_argument("--w", type=float, default=105.6, help="Width (points)")
    p.add_argument("--h", type=float, default=702.0, help="Height (points)")
    p.add_argument(
        "--x2", type=float, default=None, help="Right (points). Alternative to --w."
    )
    p.add_argument(
        "--y2", type=float, default=None, help="Bottom (points). Alternative to --h."
    )

    p.add_argument(
        "--dpi",
        type=int,
        default=150,
        help="Output image DPI (rendering resolution). Default: 150",
    )
    p.add_argument(
        "--page-start",
        type=int,
        default=1,
        help="1-based start page (inclusive). Default: 1",
    )
    p.add_argument(
        "--page-end",
        type=int,
        default=None,
        help="1-based end page (inclusive). Default: all pages",
    )
    p.add_argument(
        "--fmt",
        type=str,
        default="png",
        choices=["png", "jpg", "jpeg", "tiff"],
        help="Output image format. Default: png",
    )
    p.add_argument(
        "--quality",
        type=int,
        default=95,
        help="JPEG/TIFF quality (1-100). Ignored for PNG. Default: 95",
    )
    p.add_argument(
        "--respect-rotation",
        "--respect",
        dest="respect_rotation",
        action="store_true",
        help=(
            "Apply page rotation when rendering. Crop is defined in the "
            "page’s unrotated coordinate space; then the page is rotated."
        ),
    )
    return p.parse_args()


def main():
    args = parse_args()
    pdf_path = Path(args.pdf_path)
    if not pdf_path.exists():
        print(f"Error: file not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    out_dir = (
        Path(args.out)
        if args.out
        else pdf_path.with_suffix("").parent / f"{pdf_path.stem}_crops"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    total_pages = doc.page_count
    p_start = max(1, args.page_start)
    p_end = total_pages if args.page_end is None else min(args.page_end, total_pages)
    if p_start > p_end:
        print("Error: page-start > page-end after normalization.", file=sys.stderr)
        sys.exit(1)

    x1, y1 = args.x, args.y
    if args.w is not None and args.h is not None:
        x2, y2 = x1 + args.w, y1 + args.h
    elif args.x2 is not None and args.y2 is not None:
        x2, y2 = args.x2, args.y2
    else:
        print(
            "Error: Provide either (--x,--y,--w,--h) or (--x,--y,--x2,--y2).",
            file=sys.stderr,
        )
        sys.exit(1)

    crop_rect = fitz.Rect(x1, y1, x2, y2)
    if crop_rect.is_empty or crop_rect.is_infinite:
        print("Error: Invalid crop rectangle.", file=sys.stderr)
        sys.exit(1)

    zoom = args.dpi / 72.0
    base_matrix = fitz.Matrix(zoom, zoom)

    saved = 0
    for page_number in range(p_start, p_end + 1):
        page = doc.load_page(page_number - 1)
        clip = crop_rect & page.rect
        if clip.is_empty:
            print(f"Warning: crop rect outside page {page_number}; skipping.")
            continue

        mat = (
            base_matrix.preRotate(page.rotation)
            if getattr(args, "respect_rotation", False) and page.rotation
            else base_matrix
        )

        # Cropped area
        pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
        out_crop = out_dir / f"{pdf_path.stem}_p{page_number:04d}_crop.{args.fmt}"
        if args.fmt.lower() in ("jpg", "jpeg", "tiff"):
            pix.save(str(out_crop), quality=args.quality)
        else:
            pix.save(str(out_crop))
        print(f"Saved crop: {out_crop}")

        # Full page
        pix_full = page.get_pixmap(matrix=mat, alpha=False)
        out_full = out_dir / f"{pdf_path.stem}_p{page_number:04d}_full.{args.fmt}"
        if args.fmt.lower() in ("jpg", "jpeg", "tiff"):
            pix_full.save(str(out_full), quality=args.quality)
        else:
            pix_full.save(str(out_full))
        print(f"Saved full page: {out_full}")

        saved += 1

    print(f"Done. Saved {saved} cropped + full images to: {out_dir}")


if __name__ == "__main__":
    main()
