"""Draws Convo's app icon (mac/AppIcon.icns and mac/icon.png). Build-time only: needs Pillow (python3 -m pip install pillow)."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 1024 * 4                     # drawn at 4x, then scaled down for smooth edges
HERE = Path(__file__).parent


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def icon() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # Background: a deep teal to mint diagonal, in Apple's rounded-square shape (about 80% of the canvas).
    pad, r = int(S * 0.098), int(S * 0.18)
    grad = Image.new("RGB", (S, S))
    top, bottom = (11, 61, 58), (45, 190, 150)
    px = grad.load()
    for y in range(0, S, 4):
        for x in range(0, S, 4):
            t = min(1.0, max(0.0, (x * 0.35 + y * 0.65) / S))
            c = lerp(top, bottom, t)
            for dy in range(4):
                for dx in range(4):
                    px[x + dx, y + dy] = c
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([pad, pad, S - pad, S - pad], r, fill=255)
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([pad, pad + S // 60, S - pad, S - pad + S // 60], r, fill=(0, 0, 0, 90))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(S // 80)))
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    # A soft glow, top left.
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([S * 0.05, S * 0.02, S * 0.75, S * 0.62], fill=(255, 255, 255, 38))
    glow = glow.filter(ImageFilter.GaussianBlur(S // 14))
    img.alpha_composite(Image.composite(glow, Image.new("RGBA", (S, S)), mask))
    # Two speech bubbles: a cream one in front (Luke) and a mint one behind (them).
    cream, mint = (255, 247, 236, 255), (167, 243, 208, 255)
    d.rounded_rectangle([S * 0.30, S * 0.26, S * 0.78, S * 0.56], S * 0.12, fill=mint)
    d.polygon([(S * 0.68, S * 0.53), (S * 0.76, S * 0.64), (S * 0.60, S * 0.555)], fill=mint)
    d.rounded_rectangle([S * 0.22, S * 0.40, S * 0.70, S * 0.72], S * 0.13, fill=cream)
    d.polygon([(S * 0.30, S * 0.69), (S * 0.24, S * 0.80), (S * 0.40, S * 0.71)], fill=cream)
    # Three dots: typing.
    for i, x in enumerate((0.355, 0.46, 0.565)):
        cx, cy, rr = S * x, S * 0.56, S * 0.034
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=(14, 92, 84, 255 - i * 45))
    return img.resize((1024, 1024), Image.LANCZOS)


if __name__ == "__main__":
    im = icon()
    im.save(HERE / "icon.png")
    im.save(HERE / "AppIcon.icns")
    im.resize((64, 64), Image.LANCZOS).save(HERE / "icon-64.png")
    print("wrote", HERE / "AppIcon.icns")
