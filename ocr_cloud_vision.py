#!/usr/bin/env python3
"""
ocr_cloud_vision.py

Tiny wrapper around Google Cloud Vision OCR that returns a list of text lines.
Usage:
  python ocr_cloud_vision.py /path/to/image.png --langs en,bn
"""

from __future__ import annotations
import argparse
import json
from typing import List, Optional

from google.cloud import vision
from google.api_core.client_options import ClientOptions


def ocr_image_texts(
    image_path: str,
    *,
    language_hints: Optional[list[str]] = None,
    endpoint: Optional[str] = None,
) -> List[str]:
    """
    Run OCR on an image and return a list of text lines (top-to-bottom order).
    """
    client_options = ClientOptions(api_endpoint=endpoint) if endpoint else None
    client = vision.ImageAnnotatorClient(client_options=client_options)

    with open(image_path, "rb") as f:
        content = f.read()

    image = vision.Image(content=content)
    image_context = {"language_hints": language_hints} if language_hints else None
    response = client.text_detection(image=image, image_context=image_context)

    if response.error and response.error.message:
        raise RuntimeError(
            f"Vision API error: {response.error.message}\n"
            "Check billing/quotas/permissions and GOOGLE_APPLICATION_CREDENTIALS."
        )

    lines: List[str] = []
    if response.full_text_annotation and response.full_text_annotation.text:
        for line in response.full_text_annotation.text.splitlines():
            line = line.strip()
            if line:
                lines.append(line)
    elif response.text_annotations:
        full_text = response.text_annotations[0].description
        for line in full_text.splitlines():
            line = line.strip()
            if line:
                lines.append(line)

    return lines


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="OCR an image with Cloud Vision and print lines as JSON."
    )
    p.add_argument("image_path", help="Path to an image (PNG/JPG/etc.)")
    p.add_argument(
        "--langs",
        type=str,
        default="",
        help="Comma-separated language hints (e.g., 'en,bn').",
    )
    p.add_argument(
        "--endpoint",
        type=str,
        default="",
        help="Optional regional endpoint, e.g. 'us-vision.googleapis.com'.",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    lang_list = (
        [s.strip() for s in args.langs.split(",") if s.strip()] if args.langs else None
    )
    endpoint = args.endpoint or None
    lines = ocr_image_texts(
        args.image_path, language_hints=lang_list, endpoint=endpoint
    )
    print(json.dumps(lines, ensure_ascii=False, indent=2))
