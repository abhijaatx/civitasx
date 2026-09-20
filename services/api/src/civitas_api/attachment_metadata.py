"""Privacy-aware inspection of image metadata without exposing file bytes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from PIL import ExifTags, Image
except ImportError:  # pragma: no cover - minimal install fallback
    Image = ExifTags = None  # type: ignore[assignment]


def inspect_image_metadata(path: Path) -> dict[str, Any]:
    if Image is None:
        return {
            "status": "unavailable",
            "message": "Install the architecture extra to inspect image metadata.",
            "public_safe": False,
        }
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            exif_keys = []
            if exif and ExifTags is not None:
                exif_keys = [str(ExifTags.TAGS.get(key, key)) for key in exif.keys()]
            return {
                "status": "ok",
                "format": image.format,
                "width": image.width,
                "height": image.height,
                "has_exif": bool(exif),
                "exif_fields": exif_keys[:40],
                "public_safe": not bool(exif),
                "privacy_note": (
                    "Remove EXIF metadata before publication."
                    if exif
                    else "No EXIF metadata was detected. Review visual content before publication."
                ),
            }
    except Exception as exc:
        return {
            "status": "unavailable",
            "message": f"Image metadata could not be inspected: {str(exc)[:200]}",
            "public_safe": False,
        }
