"""Draws Convo's app icon from mac/icon.svg into mac/icon.png, mac/icon-64.png and mac/AppIcon.icns.
Build-time only: uses the Chromium that Playwright installed (to draw the SVG) and Pillow (to write the .icns).

    python mac/make_icon.py
"""
import base64
import io
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent


def render(size: int = 1024) -> Image.Image:
    svg = base64.b64encode((HERE / "icon.svg").read_bytes()).decode()
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": size, "height": size})
        page.set_content(f"<body style='margin:0;background:transparent'><img src='data:image/svg+xml;base64,{svg}' "
                         f"width={size} height={size}></body>")
        page.wait_for_timeout(200)
        png = page.screenshot(omit_background=True)
        b.close()
    return Image.open(io.BytesIO(png)).convert("RGBA")


if __name__ == "__main__":
    im = render()
    im.save(HERE / "icon.png")
    im.save(HERE / "AppIcon.icns")
    im.resize((64, 64), Image.LANCZOS).save(HERE / "icon-64.png")
    print("wrote", HERE / "AppIcon.icns")
