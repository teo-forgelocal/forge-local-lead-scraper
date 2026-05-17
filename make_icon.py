"""
Build Forge Local app icon from a custom source image.

Reads icon-source.png from the project root, auto-crops empty background
around the subject, scales it up to fill the canvas, then resizes to all
the sizes Apple requires and builds icon.icns.

The auto-crop step is the key for icons that have a small subject on a
large empty background — it makes the subject fill the icon like Apple's
own apps do.

Usage:
    python make_icon.py
"""

import os
import subprocess
import sys
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).parent
SOURCE_PATH = PROJECT_ROOT / "icon-source.png"
ICONSET_DIR = PROJECT_ROOT / "icon.iconset"
OUTPUT_ICNS = PROJECT_ROOT / "icon.icns"

# How much of the canvas the subject should fill (0.0-1.0).
# 0.85 = subject takes 85% of canvas, 15% breathing room around edges.
# Apple's design guidance suggests ~10% safe-area padding.
FILL_FACTOR = 0.95

# When detecting "background" vs "content," pixels darker than this are
# considered empty space. 30 is conservative for very dark backgrounds.
# Range 0-255 (RGB). Tweak if results look off.
DARKNESS_THRESHOLD = 30


def find_subject_bbox(img):
    """
    Find the bounding box of the non-background content in the image.

    Walks pixel-by-pixel looking for the brightest 'colored' area, then
    returns a rectangle (left, top, right, bottom) tightly bounding it.

    Strategy: pixels are "content" if any of R/G/B is brighter than
    DARKNESS_THRESHOLD. We expand the bbox to include all such pixels.
    """
    pixels = img.load()
    w, h = img.size

    min_x, min_y = w, h
    max_x, max_y = 0, 0
    found_any = False

    # Sample every 4th pixel for speed — still accurate enough for bbox.
    for y in range(0, h, 4):
        for x in range(0, w, 4):
            pixel = pixels[x, y]
            # Pillow returns (r, g, b, a) for RGBA images
            r, g, b = pixel[0], pixel[1], pixel[2]
            a = pixel[3] if len(pixel) > 3 else 255

            if a < 50:
                continue  # transparent → background
            if max(r, g, b) < DARKNESS_THRESHOLD:
                continue  # too dark → background

            # This is content
            if x < min_x: min_x = x
            if x > max_x: max_x = x
            if y < min_y: min_y = y
            if y > max_y: max_y = y
            found_any = True

    if not found_any:
        # Couldn't find any content — return the whole image
        return (0, 0, w, h)

    # Add small padding around the bbox so we don't crop exactly to edge
    pad = 8
    min_x = max(0, min_x - pad)
    min_y = max(0, min_y - pad)
    max_x = min(w, max_x + pad)
    max_y = min(h, max_y + pad)

    return (min_x, min_y, max_x, max_y)


def auto_crop_and_zoom(img):
    """
    Auto-detect the subject, crop empty background, and create a new
    square canvas where the subject fills FILL_FACTOR of the space.
    """
    # Find the subject bounding box
    bbox = find_subject_bbox(img)
    left, top, right, bottom = bbox
    subject = img.crop(bbox)

    sub_w, sub_h = subject.size
    print(f"   Detected subject: {sub_w}x{sub_h} at ({left}, {top})")

    # Make a square containing the subject (take longer side)
    side = max(sub_w, sub_h)

    # Now figure out the final canvas size — the subject should occupy
    # FILL_FACTOR of it, with the rest being background padding.
    canvas_size = int(side / FILL_FACTOR)

    # Create a TRANSPARENT canvas — Apple wraps the icon in its own rounded
    # square anyway, so transparency lets the system's wrapper show through
    # cleanly. The F will appear floating on Apple's auto-rounded square.
    canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))

    # Center the subject on the canvas
    paste_x = (canvas_size - sub_w) // 2
    paste_y = (canvas_size - sub_h) // 2
    canvas.paste(subject, (paste_x, paste_y), subject if subject.mode == "RGBA" else None)

    print(f"   Built canvas: {canvas_size}x{canvas_size}, subject fills "
          f"{FILL_FACTOR*100:.0f}% of frame")

    return canvas


def main():
    if not SOURCE_PATH.exists():
        print(f"❌ Source image not found at {SOURCE_PATH}")
        sys.exit(1)

    print(f"📂 Loading {SOURCE_PATH.name}...")
    img = Image.open(SOURCE_PATH).convert("RGBA")
    print(f"   Original: {img.width}x{img.height}")

    # Auto-crop and zoom so subject fills the canvas
    print(f"🔍 Auto-cropping background, zooming subject to {FILL_FACTOR*100:.0f}% fill...")
    img = auto_crop_and_zoom(img)

    # Resize to 1024 for max-quality master
    img = img.resize((1024, 1024), Image.LANCZOS)
    print(f"   Resized to 1024x1024")

    # Save a preview so you can see the result before it goes into the .icns
    preview_path = PROJECT_ROOT / "icon-preview.png"
    img.save(preview_path, "PNG")
    print(f"   Preview saved: {preview_path.name} (open this to verify before deploying)")

    # Clean iconset dir
    if ICONSET_DIR.exists():
        for f in ICONSET_DIR.iterdir():
            f.unlink()
    else:
        ICONSET_DIR.mkdir()

    # Apple's required sizes
    pairs = [
        (16, "icon_16x16.png"),
        (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"),
        (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"),
        (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"),
        (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"),
        (1024, "icon_512x512@2x.png"),
    ]

    for size, filename in pairs:
        resized = img.resize((size, size), Image.LANCZOS)
        resized.save(ICONSET_DIR / filename, "PNG")

    print(f"✅ Generated {len(pairs)} icon sizes in {ICONSET_DIR.name}/")

    print("🔨 Building icon.icns with iconutil...")
    result = subprocess.run(
        ["iconutil", "-c", "icns", str(ICONSET_DIR), "-o", str(OUTPUT_ICNS)],
        capture_output=True, text=True,
    )

    if result.returncode == 0:
        print(f"✅ {OUTPUT_ICNS.name} created.")
        size_kb = OUTPUT_ICNS.stat().st_size / 1024
        print(f"   File size: {size_kb:.0f} KB")
        print()
        print(f"💡 Open {preview_path.name} to verify the look before deploying.")
    else:
        print(f"❌ iconutil failed:")
        print(result.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()