#!/usr/bin/env python3
"""Render MIB Trader desktop icon at multiple sizes."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path("/home/workdir/artifacts/icons")
OUT.mkdir(parents=True, exist_ok=True)

BG = (8, 11, 16, 255)
LETTER = (226, 232, 240, 255)
CYAN = (127, 217, 255, 255)
GREEN = (0, 245, 155, 255)
RED = (255, 59, 86, 255)


def find_font(size: int):
    candidates = [
        "/usr/share/fonts/SlidesCarnival/google/Barlow Condensed/BarlowCondensed-Black.ttf",
        "/usr/share/fonts/SlidesCarnival/google/Barlow Condensed/BarlowCondensed-ExtraBold.ttf",
        "/usr/share/fonts/SlidesCarnival/google/Saira/static/Saira_Condensed-ExtraBold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def draw_candle(draw, cx, top, body_h, wick_top, wick_bot, w, color):
    draw.rectangle([cx - 1, wick_top, cx + 1, wick_bot], fill=color)
    draw.rectangle([cx - w // 2, top, cx + w // 2, top + body_h], fill=color)


def render(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    # rounded plate
    plate = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pd = ImageDraw.Draw(plate)
    radius = int(size * 0.18)
    pd.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=BG)
    img.alpha_composite(plate)

    d = ImageDraw.Draw(img)
    font = find_font(int(size * 0.42))
    text = "MIB"
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = (size - tw) // 2 - bbox[0]
    ty = int(size * 0.16) - bbox[1]

    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.text((tx, ty), text, font=font, fill=CYAN)
    glow = glow.filter(ImageFilter.GaussianBlur(radius=max(1, size // 48)))
    img.alpha_composite(glow)
    d = ImageDraw.Draw(img)
    d.text((tx, ty), text, font=font, fill=LETTER)

    # candles sit just under the letters
    letter_bottom = ty + bbox[3]
    mid = size // 2
    gap = int(size * 0.06)
    body_h = int(size * 0.16)
    wick = int(size * 0.05)
    body_w = max(3, int(size * 0.055))
    green_w = max(4, int(size * 0.07))
    green_h = int(size * 0.20)
    cx_gap = int(size * 0.12)
    top_g = letter_bottom + gap
    if size >= 32:
        draw_candle(d, mid - cx_gap, top_g + int(size * 0.04), body_h, top_g + int(size * 0.01), top_g + body_h + wick * 2, body_w, RED)
        gglow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        gg = ImageDraw.Draw(gglow)
        draw_candle(gg, mid, top_g, green_h, top_g - wick, top_g + green_h + wick, green_w, GREEN)
        if size >= 64:
            gglow = gglow.filter(ImageFilter.GaussianBlur(radius=max(1, size // 70)))
            img.alpha_composite(gglow)
            d = ImageDraw.Draw(img)
        else:
            d = ImageDraw.Draw(img)
            img.alpha_composite(gglow)
        draw_candle(d, mid, top_g, green_h, top_g - wick, top_g + green_h + wick, green_w, GREEN)
        draw_candle(d, mid + cx_gap, top_g + int(size * 0.06), int(body_h * 0.82), top_g + int(size * 0.03), top_g + body_h + wick, body_w, RED)
    return img


def main():
    master = render(1024)
    master.save(OUT / "mib-trader-1024.png")
    sizes = [16, 24, 32, 48, 64, 128, 256, 512, 1024]
    for s in sizes:
        im = render(s)
        im.save(OUT / f"mib-trader-{s}.png")
    # Windows ico
    ico_imgs = [render(s) for s in (256, 128, 64, 48, 32, 24, 16)]
    ico_imgs[0].save(
        OUT / "mib-trader.ico",
        format="ICO",
        sizes=[(im.width, im.height) for im in ico_imgs],
        append_images=ico_imgs[1:],
    )
    print("wrote", OUT)
    print("\n".join(p.name for p in sorted(OUT.iterdir())))


if __name__ == "__main__":
    main()
