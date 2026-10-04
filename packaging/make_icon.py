"""Turn simplenotesiconimage.jpg into a transparent, square macOS-style icon PNG.

Icon images often come with a "transparent" checkerboard baked into the pixels
(JPEG has no alpha). Light, grey-ish pixels connected to the image border are
treated as background and made transparent; anything enclosed by the icon's own
colours (e.g. a white glyph on a blue tile) is kept. The result is cropped to the
icon and centred on a 1024×1024 canvas at Apple's 824px icon-body size.

Usage: python make_icon.py SOURCE.jpg OUT.png
"""
import sys

from PIL import Image, ImageDraw, ImageFilter

CANVAS = 1024
BODY = 824          # macOS icon grid: the artwork's size inside the 1024 canvas
WORK = 1600         # longest side used for background detection (finer = smoother edge)


def is_background_colour(rgb) -> bool:
    r, g, b = rgb[:3]
    return max(r, g, b) > 185 and max(r, g, b) - min(r, g, b) < 28  # light and unsaturated


def make_icon(source: str, out: str) -> None:
    image = Image.open(source).convert("RGB")

    # Background detection on a small copy: flood-fill light, neutral pixels from the border.
    scale = WORK / max(image.size)
    small = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))))
    mask = Image.new("L", small.size, 0)  # 255 = could be background
    pixels, mask_px = small.load(), mask.load()
    for y in range(small.height):
        for x in range(small.width):
            if is_background_colour(pixels[x, y]):
                mask_px[x, y] = 255
    for x in range(small.width):  # fill from every border pixel that is background-coloured
        for y in (0, small.height - 1):
            if mask_px[x, y] == 255:
                ImageDraw.floodfill(mask, (x, y), 128)
    for y in range(small.height):
        for x in (0, small.width - 1):
            if mask_px[x, y] == 255:
                ImageDraw.floodfill(mask, (x, y), 128)
    alpha_small = mask.point(lambda v: 0 if v == 128 else 255)

    bbox = alpha_small.getbbox()
    if bbox is None or alpha_small.getextrema() == (255, 255):
        alpha = Image.new("L", image.size, 255)  # no baked-in background: keep everything
        bbox_full = (0, 0, image.width, image.height)
    else:
        # Pull the edge in a few pixels before softening it: the outermost pixels of a
        # JPEG are blended with the light checkerboard and would show as a pale rim.
        alpha = (alpha_small.resize(image.size, Image.LANCZOS)
                 .filter(ImageFilter.MinFilter(7)).filter(ImageFilter.GaussianBlur(2)))
        bbox_full = tuple(round(v / scale) for v in bbox)

    icon = image.convert("RGBA")
    icon.putalpha(alpha)
    icon = icon.crop(bbox_full)

    # Fit into the icon body, centred on a transparent square canvas.
    ratio = BODY / max(icon.size)
    icon = icon.resize((round(icon.width * ratio), round(icon.height * ratio)), Image.LANCZOS)
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    canvas.paste(icon, ((CANVAS - icon.width) // 2, (CANVAS - icon.height) // 2), icon)
    canvas.save(out)


if __name__ == "__main__":
    make_icon(sys.argv[1], sys.argv[2])
