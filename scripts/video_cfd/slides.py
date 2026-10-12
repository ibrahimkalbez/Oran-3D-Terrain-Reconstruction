"""Slides of the scientific video: HTML (1920x1080) rendered to PNG by headless Chromium."""
import html
import subprocess
from pathlib import Path

CSS = """
html,body{margin:0;width:1920px;height:1080px;background:#0f1720;color:#eceff1;font-family:'DejaVu Sans',sans-serif;overflow:hidden}
.top{position:absolute;left:0;right:0;top:0;height:8px;background:linear-gradient(90deg,#e53935,#fb8c00,#fdd835,#43a047,#1e88e5)}
.sec{position:absolute;left:70px;top:46px;font:600 24px 'DejaVu Sans';color:#90caf9;letter-spacing:2px;text-transform:uppercase}
h1{position:absolute;left:70px;top:84px;margin:0;font:700 54px 'DejaVu Sans';max-width:1780px}
.body{position:absolute;left:70px;top:196px;right:70px;bottom:120px;display:flex;gap:46px}
.col{flex:1;font:31px/1.45 'DejaVu Sans'} .col ul{margin:0;padding-left:28px} .col li{margin-bottom:18px}
.fig{flex:1.25;display:flex;align-items:center;justify-content:center;background:#fff;border-radius:6px}
.fig img{max-width:100%;max-height:100%;object-fit:contain}
table{border-collapse:collapse;font:24px 'DejaVu Sans Mono';width:100%} td,th{border-bottom:1px solid #37474f;padding:7px 10px;text-align:left}
th{color:#90caf9;font-family:'DejaVu Sans'} .note{position:absolute;left:70px;right:70px;bottom:118px;font:22px 'DejaVu Sans';color:#b0bec5}
.badge{display:inline-block;padding:3px 10px;border-radius:4px;font:600 22px 'DejaVu Sans';margin-right:10px}
.ok{background:#2e7d32} .est{background:#ef6c00} .plan{background:#6a1b9a} .src{font:16px 'DejaVu Sans';color:#90a4ae}
"""


def page(section, title, bullets=None, image=None, table=None, note=None, badge=None):
    b = ""
    if bullets:
        b += "<div class='col'><ul>" + "".join(f"<li>{x}</li>" for x in bullets) + "</ul>"
        if table:
            b += _table(table)
        b += "</div>"
    elif table:
        b += "<div class='col'>" + _table(table) + "</div>"
    if image:
        b += f"<div class='fig'><img src='file://{image}'></div>"
    bd = f"<span class='badge {badge[0]}'>{html.escape(badge[1])}</span>" if badge else ""
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body><div class='top'></div>"
            f"<div class='sec'>{html.escape(section)}</div><h1>{bd}{html.escape(title)}</h1><div class='body'>{b}</div>"
            + (f"<div class='note'>{note}</div>" if note else "") + "</body></html>")


def _table(rows):
    h = "".join(f"<th>{html.escape(str(c))}</th>" for c in rows[0])
    return "<table><tr>" + h + "</tr>" + "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in r) + "</tr>" for r in rows[1:]) + "</table>"


def render(html_text, png):
    png = Path(png); hp = png.with_suffix(".html"); hp.write_text(html_text, encoding="utf-8")
    js = (f"import {{chromium}} from 'playwright-core';const b=await chromium.launch({{executablePath:'/opt/pw-browsers/chromium-1194/chrome-linux/chrome'}});"
          f"const p=await b.newPage({{viewport:{{width:1920,height:1080}}}});await p.goto('file://{hp}');await p.waitForTimeout(400);"
          f"await p.screenshot({{path:'{png}'}});await b.close();")
    m = Path("/home/user/work/video/slide_render.mjs"); m.write_text(js)
    subprocess.run(["node", str(m)], cwd="/home/user/work/video", check=True, capture_output=True)
