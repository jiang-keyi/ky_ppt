#!/usr/bin/env python3
"""Split a composite scientific figure into sub-panels (local, CPU).

Primary method: whitespace-gutter projection. Non-white pixel density is projected
onto the x and y axes; empty gutter bands separate columns and rows, and each
content cell becomes one panel. RapidOCR is used only to name panels after the
fact (A/B/C/D ...); if single-letter labels are not recognised, panels are named
in top-to-bottom, left-to-right order.

Best-effort: if no gutter structure is found, the whole composite is returned as a
single panel. Crops are raster PNG; editable-shape conversion is left to the
ky_ppt vtracer engine (optional, called separately).

Usage:
  py -3 split_panels.py --image composite.png --out-dir panels [--prefix fig]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image

LABEL_RE = re.compile(r"^(?:图|Panel|Fig\.?\s*|Figure\s*)?([A-J])$", re.I)


def _label_letter(text: str) -> str | None:
    m = LABEL_RE.match(text.strip())
    return m.group(1).upper() if m else None


def _segments(profile: np.ndarray, extent: int, min_len: int, gap_thresh: float) -> list[tuple[int, int]]:
    """Return content segments [x0,x1) separated by near-empty gutter bands."""
    content = profile >= gap_thresh
    segs: list[tuple[int, int]] = []
    start = None
    for i, ok in enumerate(content):
        if ok and start is None:
            start = i
        elif not ok and start is not None:
            if i - start >= min_len:
                segs.append((start, i))
            start = None
    if start is not None and extent - start >= min_len:
        segs.append((start, extent))
    return segs


def split_panels(image_path: Path, out_dir: Path, prefix: str = "fig",
                 min_score: float = 0.4) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    im = Image.open(image_path).convert("RGB")
    W, H = im.size
    arr = np.asarray(im)
    nonwhite = np.any(arr < 245, axis=2)  # near-white = gutter
    col_profile = nonwhite.mean(axis=0)
    row_profile = nonwhite.mean(axis=1)

    min_len = max(20, min(W, H) // 20)
    cols = _segments(col_profile, W, min_len, gap_thresh=0.02)
    rows = _segments(row_profile, H, min_len, gap_thresh=0.02)

    # Fallback: no detectable grid -> single panel.
    if len(cols) == 1 and len(rows) == 1:
        out = out_dir / f"{prefix}_A.png"
        im.save(out)
        return [{"label": "A", "image": str(out), "bbox": [0, 0, W, H]}]

    cells: list[dict] = []
    for ri, (y0, y1) in enumerate(rows):
        for ci, (x0, x1) in enumerate(cols):
            if x1 - x0 < min_len or y1 - y0 < min_len:
                continue
            cell = nonwhite[y0:y1, x0:x1]
            if cell.mean() < 0.01:  # empty cell
                continue
            cells.append({"row": ri, "col": ci, "bbox": [x0, y0, x1, y1]})

    # OCR labels (optional naming enhancement).
    ocr_labels: list[dict] = []
    try:
        from rapidocr_onnxruntime import RapidOCR
        engine = RapidOCR()
        result, _ = engine(str(image_path))
        if result:
            for box, text, score in result:
                letter = _label_letter(str(text))
                if not letter or float(score) < min_score:
                    continue
                pts = [(float(x), float(y)) for x, y in box]
                ocr_labels.append({"letter": letter,
                                    "cx": sum(p[0] for p in pts) / 4,
                                    "cy": sum(p[1] for p in pts) / 4})
    except Exception:
        ocr_labels = []

    def assign_letter(cell: dict) -> str:
        x0, y0, x1, y1 = cell["bbox"]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        best, best_d = None, 1e18
        for lab in ocr_labels:
            d = (lab["cx"] - cx) ** 2 + (lab["cy"] - cy) ** 2
            if d < best_d:
                best, best_d = lab, d
        if best and best_d < ((x1 - x0) ** 2 + (y1 - y0) ** 2):
            return best["letter"]
        return ""

    cells.sort(key=lambda c: (c["row"], c["col"]))
    panels: list[dict] = []
    used: set[str] = set()
    fallback_idx = 0
    for cell in cells:
        letter = assign_letter(cell)
        if not letter or letter in used:
            # sequential A, B, C ... in row-major order
            while chr(65 + fallback_idx) in used:
                fallback_idx += 1
            letter = chr(65 + fallback_idx)
        used.add(letter)
        fallback_idx = max(fallback_idx, ord(letter) - 65 + 1)
        x0, y0, x1, y1 = cell["bbox"]
        crop = im.crop((x0, y0, x1, y1))
        out = out_dir / f"{prefix}_{letter}.png"
        crop.save(out)
        panels.append({"label": letter, "image": str(out),
                       "bbox": [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)]})
    return panels


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--prefix", default="fig")
    ap.add_argument("--min-score", type=float, default=0.4)
    ap.add_argument("--json", type=Path, help="write panel manifest JSON")
    args = ap.parse_args()
    panels = split_panels(args.image, args.out_dir, args.prefix, args.min_score)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(panels, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "panels": len(panels), "out_dir": str(args.out_dir)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        print(f"PANEL_ERROR|{exc}", file=sys.stderr)
        raise SystemExit(1)