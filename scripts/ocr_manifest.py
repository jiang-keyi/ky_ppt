#!/usr/bin/env python3
"""ky_ppt precise text-manifest generator — RapidOCR (CPU, local, free).

A 2B VLM is unreliable at dense pixel-accurate OCR; a dedicated lightweight OCR
model (RapidOCR / PP-OCR onnxruntime) gives exact text + tight bounding boxes in
seconds on CPU. This script turns those detections into the text-manifest
(schema 1.0) so labels become editable PPT/AI text instead of baked-in geometry.

Usage:
  py -3 ocr_manifest.py --input-image fig.png --output text-manifest.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def hex_color(rgb) -> str:
    r, g, b = (int(max(0, min(255, round(c)))) for c in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def estimate_fill(arr: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> str:
    """Text stroke colour = pixels that differ most from the local background."""
    h, w = arr.shape[:2]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return "#000000"
    crop = arr[y0:y1, x0:x1].reshape(-1, 3).astype(np.float64)
    # background = most frequent (quantised) colour in the crop, not the border
    quant = (crop // 24).astype(np.int32)
    keys = quant[:, 0] * 10000 + quant[:, 1] * 100 + quant[:, 2]
    vals, counts = np.unique(keys, return_counts=True)
    bg = crop[keys == vals[counts.argmax()]].mean(axis=0)
    dist = np.linalg.norm(crop - bg, axis=1)
    thr = max(45.0, np.percentile(dist, 75))
    ink = crop[dist >= thr]
    if len(ink) < 3:
        return "#000000"
    return hex_color(np.median(ink, axis=0))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-image", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--min-score", type=float, default=0.5)
    ap.add_argument("--font-family", default="Arial")
    args = ap.parse_args()

    from rapidocr_onnxruntime import RapidOCR
    image = args.input_image.resolve()
    if not image.is_file():
        raise SystemExit("OCR_ERROR|input image not found")
    with Image.open(image) as im:
        im = im.convert("RGB")
        w, h = im.size
        arr = np.asarray(im)

    engine = RapidOCR()
    result, _ = engine(str(image))
    elements = []
    if result:
        for i, det in enumerate(result):
            box, text, score = det[0], det[1], float(det[2])
            text = str(text).strip()
            if not text or score < args.min_score:
                continue
            pts = np.asarray(box, dtype=np.float64)
            x0, y0 = pts[:, 0].min(), pts[:, 1].min()
            x1, y1 = pts[:, 0].max(), pts[:, 1].max()
            x0, x1 = sorted((max(0.0, min(x0, w)), max(0.0, min(x1, w))))
            y0, y1 = sorted((max(0.0, min(y0, h)), max(0.0, min(y1, h))))
            if x1 - x0 < 2 or y1 - y0 < 2:
                continue
            fill = estimate_fill(arr, int(x0), int(y0), int(x1), int(y1))
            elements.append({
                "id": f"text_{i + 1:03d}", "content": text,
                "x": round((x0 + x1) / 2, 1), "y": round(y1, 1),
                "coordinate_space": "pixels",
                "font_size": round(max(6.0, (y1 - y0) * 0.82), 1),
                "font_family": args.font_family, "fill": fill,
                "text_anchor": "middle", "rotation": 0,
                "paint_order": 1000000000,
                "source_bbox": [round(v, 1) for v in (x0, y0, x1, y1)],
            })
    out = {"schema_version": "1.0", "source_canvas": {"width": w, "height": h},
           "text_elements": elements}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "engine": "rapidocr", "count": len(elements),
                      "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        print(f"OCR_ERROR|{exc}", file=sys.stderr)
        raise SystemExit(1)