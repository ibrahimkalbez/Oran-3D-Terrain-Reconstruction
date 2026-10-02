"""Draws the Revit Dynamo connector icon (assets/icon.png): white massing blocks on a level
grid and a small visual-programming graph (nodes and wires). Generic artwork, no third-party logo."""
from PIL import Image, ImageDraw
import math, sys

S = 512
FUSION = "--fusion" in sys.argv
args = [a for a in sys.argv[1:] if not a.startswith("--")]
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([0, 0, S - 1, S - 1], radius=96, fill=(16, 52, 86, 255))

def iso(x, y, z, ox=250, oy=360, k=30):
    return (ox + (x - y) * k * math.cos(math.radians(30)), oy + (x + y) * k * 0.5 - z * k)

# level grid
for i in range(-4, 5):
    d.line([iso(i, -4, 0), iso(i, 4, 0)], fill=(60, 100, 140), width=2)
    d.line([iso(-4, i, 0), iso(4, i, 0)], fill=(60, 100, 140), width=2)

def block(x, y, w, dp, h):
    top = [iso(x, y, h), iso(x + w, y, h), iso(x + w, y + dp, h), iso(x, y + dp, h)]
    left = [iso(x, y + dp, 0), iso(x + w, y + dp, 0), iso(x + w, y + dp, h), iso(x, y + dp, h)]
    right = [iso(x + w, y, 0), iso(x + w, y + dp, 0), iso(x + w, y + dp, h), iso(x + w, y, h)]
    d.polygon(left, fill=(205, 212, 222), outline=(16, 52, 86))
    d.polygon(right, fill=(165, 176, 190), outline=(16, 52, 86))
    d.polygon(top, fill=(250, 250, 248), outline=(16, 52, 86))
    for lv in range(1, int(h)):  # floor lines
        d.line([iso(x, y + dp, lv), iso(x + w, y + dp, lv)], fill=(150, 160, 175), width=2)
        d.line([iso(x + w, y, lv), iso(x + w, y + dp, lv)], fill=(130, 140, 155), width=2)

for x, y, w, dp, h in [(-2.8, -1.2, 1.8, 1.8, 4), (-0.6, -2.6, 1.8, 1.8, 6), (0.2, 0.6, 1.8, 1.8, 3)]:
    block(x, y, w, dp, h)

# visual-programming graph: three nodes and wires
nodes = [(40, 46, 150, 92), (40, 112, 150, 158), (300, 72, 440, 128)]
for x0, y0, x1, y1 in nodes:
    d.rounded_rectangle([x0, y0, x1, y1], radius=10, fill=(236, 240, 245), outline=(255, 255, 255), width=2)
    d.rectangle([x0, y0, x1, y0 + 12], fill=(250, 180, 40))
for (sx, sy) in [(150, 75), (150, 141)]:
    ex, ey = 300, 100
    pts = [(sx + (ex - sx) * t / 40, sy + (ey - sy) * (3 * (t / 40) ** 2 - 2 * (t / 40) ** 3)) for t in range(41)]
    d.line(pts, fill=(250, 180, 40), width=5, joint="curve")

if FUSION:
    tx, ty = 410, 340
    d.rectangle([tx - 7, ty - 10, tx + 7, ty + 55], fill=(120, 85, 50))
    d.ellipse([tx - 48, ty - 90, tx + 48, ty + 2], fill=(70, 170, 90), outline=(16, 52, 86), width=4)
    ramp = [(40, 30, 120), (30, 120, 170), (80, 190, 90), (250, 220, 40), (227, 6, 19)]
    for i, c in enumerate(ramp):
        d.rectangle([60 + i * 78, 446, 60 + (i + 1) * 78, 468], fill=c)

img.save(args[0] if args else "assets/icon.png")
