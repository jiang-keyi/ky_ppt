#!/usr/bin/env python3
"""Build a synthetic scientific-figure test pair (original + text-free) and a text manifest."""
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
OUT = HERE / "test"
OUT.mkdir(exist_ok=True)
W, H = 1200, 800

def draw_figure(with_text: bool) -> Image.Image:
    im = Image.new("RGB", (W, H), "#ffffff")
    d = ImageDraw.Draw(im)
    # panel
    d.rounded_rectangle([60, 120, 700, 700], radius=18, fill="#eef4fb", outline="#3b6ea5", width=3)
    # donut (compound-hole case): outer blue circle, white hole, red core
    d.ellipse([140, 200, 380, 440], fill="#3b6ea5")
    d.ellipse([200, 260, 320, 380], fill="#ffffff")
    d.ellipse([235, 295, 285, 345], fill="#c0392b")
    # bar chart
    bars = [(430, 480, "#2e8b57"), (490, 380, "#e67e22"), (550, 300, "#8e44ad"), (610, 420, "#16a085")]
    for x, top, color in bars:
        d.rectangle([x, top, x + 44, 640], fill=color)
    d.line([410, 640, 680, 640], fill="#222222", width=3)
    d.line([410, 260, 410, 640], fill="#222222", width=3)
    # right side: arrow + ellipse node (pathway style)
    d.ellipse([780, 200, 1080, 320], fill="#f5d76e", outline="#7d6608", width=3)
    d.line([930, 320, 930, 480], fill="#444444", width=5)
    d.polygon([(915, 480), (945, 480), (930, 510)], fill="#444444")
    d.rounded_rectangle([790, 510, 1070, 640], radius=14, fill="#d5f5e3", outline="#1e8449", width=3)
    # smooth sine curve
    import math
    pts = [(80 + i * 4, 740 - int(24 * math.sin(i / 9))) for i in range(270)]
    d.line(pts, fill="#c0392b", width=3)
    if with_text:
        f_title = ImageFont.truetype("arial.ttf", 34)
        f_label = ImageFont.truetype("arial.ttf", 24)
        d.text((60, 40), "Figure 1. Local vectorization test", font=f_title, fill="#111111")
        d.text((180, 460), "Donut", font=f_label, fill="#222222")
        d.text((470, 660), "Group A-D", font=f_label, fill="#222222")
        d.text((855, 245), "Input node", font=f_label, fill="#5b4a05")
        d.text((855, 560), "Output node", font=f_label, fill="#145a32")
    return im

orig = draw_figure(True)
clean = draw_figure(False)
orig.save(OUT / "original.png")
clean.save(OUT / "cleaned.png")

manifest = {
    "schema_version": "1.0",
    "source_canvas": {"width": W, "height": H},
    "text_elements": [
        {"id": "txt_title", "content": "Figure 1. Local vectorization test", "x": 60, "y": 74,
         "coordinate_space": "pixels", "font_size": 34, "font_family": "Arial", "fill": "#111111"},
        {"id": "txt_donut", "content": "Donut", "x": 180, "y": 484,
         "coordinate_space": "pixels", "font_size": 24, "font_family": "Arial", "fill": "#222222"},
        {"id": "txt_bars", "content": "Group A-D", "x": 470, "y": 684,
         "coordinate_space": "pixels", "font_size": 24, "font_family": "Arial", "fill": "#222222"},
        {"id": "txt_input", "content": "Input node", "x": 855, "y": 269,
         "coordinate_space": "pixels", "font_size": 24, "font_family": "Arial", "fill": "#5b4a05"},
        {"id": "txt_output", "content": "Output node", "x": 855, "y": 584,
         "coordinate_space": "pixels", "font_size": 24, "font_family": "Arial", "fill": "#145a32"},
    ],
}
(OUT / "text-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
print("test images and manifest written to", OUT)