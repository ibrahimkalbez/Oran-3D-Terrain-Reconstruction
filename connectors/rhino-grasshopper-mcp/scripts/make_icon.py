"""Draws the connector icon (assets/icon.png): white isometric building blocks on a dark
background with a red parametric curve. Generic artwork, no third-party logo."""
from PIL import Image, ImageDraw
import math, sys

S = 512
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([0, 0, S - 1, S - 1], radius=96, fill=(30, 36, 48, 255))

def iso(x, y, z, ox=256, oy=330, k=34):
    return (ox + (x - y) * k * math.cos(math.radians(30)), oy + (x + y) * k * 0.5 - z * k)

def block(x, y, w, dpt, h):
    top = [iso(x, y, h), iso(x + w, y, h), iso(x + w, y + dpt, h), iso(x, y + dpt, h)]
    left = [iso(x, y + dpt, 0), iso(x + w, y + dpt, 0), iso(x + w, y + dpt, h), iso(x, y + dpt, h)]
    right = [iso(x + w, y, 0), iso(x + w, y + dpt, 0), iso(x + w, y + dpt, h), iso(x + w, y, h)]
    d.polygon(left, fill=(205, 208, 214), outline=(30, 36, 48))
    d.polygon(right, fill=(170, 175, 184), outline=(30, 36, 48))
    d.polygon(top, fill=(250, 250, 248), outline=(30, 36, 48))

for x, y, w, dp, h in [(-3.2, -2.2, 1.6, 1.6, 2.2), (-1.0, -2.6, 1.6, 1.6, 4.4), (1.2, -2.2, 1.6, 1.6, 3.1), (-2.2, 0.2, 1.6, 1.6, 1.4), (0.2, 0.0, 1.6, 1.6, 2.6)]:
    block(x, y, w, dp, h)

pts = [(60 + t * 3.9, 420 - 38 * math.sin(t / 18.0) - t * 0.35) for t in range(0, 101)]
d.line(pts, fill=(227, 6, 19), width=12, joint="curve")
for p in (pts[0], pts[50], pts[100]):
    d.ellipse([p[0] - 14, p[1] - 14, p[0] + 14, p[1] + 14], fill=(227, 6, 19), outline=(255, 255, 255), width=4)

img.save(sys.argv[1] if len(sys.argv) > 1 else "assets/icon.png")
