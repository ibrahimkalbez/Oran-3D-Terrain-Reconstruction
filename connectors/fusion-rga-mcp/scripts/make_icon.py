"""Draws the Fusion connector icon (assets/icon.png): white building blocks, a tree,
wind streamlines and a simulation colour bar. Generic artwork, no third-party logo."""
from PIL import Image, ImageDraw
import math, sys

S = 512
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([0, 0, S - 1, S - 1], radius=96, fill=(24, 34, 52, 255))

def iso(x, y, z, ox=230, oy=340, k=32):
    return (ox + (x - y) * k * math.cos(math.radians(30)), oy + (x + y) * k * 0.5 - z * k)

def block(x, y, w, dp, h):
    top = [iso(x, y, h), iso(x + w, y, h), iso(x + w, y + dp, h), iso(x, y + dp, h)]
    left = [iso(x, y + dp, 0), iso(x + w, y + dp, 0), iso(x + w, y + dp, h), iso(x, y + dp, h)]
    right = [iso(x + w, y, 0), iso(x + w, y + dp, 0), iso(x + w, y + dp, h), iso(x + w, y, h)]
    d.polygon(left, fill=(205, 208, 214), outline=(24, 34, 52))
    d.polygon(right, fill=(170, 175, 184), outline=(24, 34, 52))
    d.polygon(top, fill=(250, 250, 248), outline=(24, 34, 52))

for x, y, w, dp, h in [(-2.6, -2.0, 1.6, 1.6, 3.6), (-0.4, -2.4, 1.6, 1.6, 5.0), (-1.6, 0.4, 1.6, 1.6, 2.2)]:
    block(x, y, w, dp, h)

# tree
tx, ty = 395, 330
d.rectangle([tx - 7, ty - 10, tx + 7, ty + 60], fill=(120, 85, 50))
d.ellipse([tx - 52, ty - 95, tx + 52, ty + 5], fill=(70, 170, 90), outline=(24, 34, 52), width=4)

# wind streamlines
for i, y0 in enumerate((95, 130, 165)):
    pts = [(40 + t * 4.4, y0 + 12 * math.sin(t / 14.0 + i)) for t in range(0, 101)]
    d.line(pts, fill=(120, 200, 255), width=7, joint="curve")

# simulation colour bar
ramp = [(40, 30, 120), (30, 120, 170), (80, 190, 90), (250, 220, 40), (227, 6, 19)]
for i, c in enumerate(ramp):
    d.rectangle([60 + i * 78, 438, 60 + (i + 1) * 78, 462], fill=c)

img.save(sys.argv[1] if len(sys.argv) > 1 else "assets/icon.png")
