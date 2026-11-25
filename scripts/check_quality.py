#!/usr/bin/env python3
"""ky_ppt quality gate: render an SVG back to raster and compare with the source.

Reports mean absolute error (MAE) and dark-ink recall (fraction of source dark
pixels reproduced as dark in the render). Low ink recall means text/thin lines
were lost — the failure class this gate exists to catch.

Usage:
  python check_quality.py --svg out.svg --source original.png [--json report.json]

Renderer: prefers resvg-js (spec-compliant; npm i @resvg/resvg-js, discovered via
KY_PPT_RESVG_DIR / skill-root node_modules / cwd / temp resvg-render). Falls back
to svglib, which under-reports fidelity on complex nonzero paths.
Exit code 0 = PASS, 2 = WARN (fidelity below thresholds), 1 = error.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image


def _resvg_dirs() -> list[Path]:
    here = Path(__file__).resolve().parent
    cands = []
    if os.environ.get("KY_PPT_RESVG_DIR"):
        cands.append(Path(os.environ["KY_PPT_RESVG_DIR"]))
    cands += [here.parent / "node_modules", here / "node_modules",
              Path.cwd() / "node_modules", Path(tempfile.gettempdir()) / "resvg-render"]
    return [c for c in cands if (c / "@resvg" / "resvg-js").is_dir()]


def render_resvg(svg_path: Path, width: int, height: int) -> np.ndarray | None:
    dirs = _resvg_dirs()
    if not dirs:
        return None
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    out = Path(tmp)
    code = (
        "const {Resvg}=require(process.argv[1]);const fs=require('fs');"
        "const r=new Resvg(fs.readFileSync(process.argv[2]),{fitTo:{mode:'width',value:parseInt(process.argv[3])}});"
        "fs.writeFileSync(process.argv[4],r.render().asPng());"
    )
    try:
        proc = subprocess.run(["node", "-e", code, str(dirs[0] / "@resvg" / "resvg-js"),
                               str(svg_path), str(width), str(out)],
                              capture_output=True, text=True, timeout=120)
        if proc.returncode != 0 or not out.is_file():
            return None
        import io
        data = out.read_bytes()
        return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), dtype=np.uint8)
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        out.unlink(missing_ok=True)


def render_svglib(svg_path: Path, width: int, height: int) -> np.ndarray:
    from svglib.svglib import svg2rlg
    from reportlab.graphics import renderPM
    import io
    drawing = svg2rlg(str(svg_path))
    if drawing is None:
        raise SystemExit("QUALITY_ERROR|svglib could not parse the SVG")
    drawing.scale(width / drawing.width, height / drawing.height)
    drawing.width = width
    drawing.height = height
    buf = io.BytesIO()
    renderPM.drawToFile(drawing, buf, fmt="PNG")
    buf.seek(0)
    return np.asarray(Image.open(buf).convert("RGB"), dtype=np.uint8)


def main() -> int:
    parser = argparse.ArgumentParser(description="ky_ppt SVG fidelity gate.")
    parser.add_argument("--svg", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--json", type=Path, help="optional report output path")
    parser.add_argument("--mae-threshold", type=float, default=18.0)
    parser.add_argument("--ink-threshold", type=float, default=0.85)
    args = parser.parse_args()

    if not args.svg.is_file() or not args.source.is_file():
        raise SystemExit("QUALITY_ERROR|svg or source not found")
    with Image.open(args.source) as im:
        src = np.asarray(im.convert("RGB"), dtype=np.uint8)
    h, w, _ = src.shape
    renderer = "resvg"
    ren = render_resvg(args.svg, w, h)
    if ren is None:
        renderer = "svglib"
        try:
            ren = render_svglib(args.svg, w, h)
        except ImportError:
            raise SystemExit("QUALITY_ERROR|no renderer: npm i @resvg/resvg-js (preferred) "
                             "or pip install svglib rlPyCairo pycairo")
    if ren.shape != src.shape:
        ren = np.asarray(Image.fromarray(ren).resize((w, h)), dtype=np.uint8)

    mae = float(np.abs(src.astype(np.int32) - ren.astype(np.int32)).mean())
    src_dark = src.mean(axis=2) < 110
    ren_dark = ren.mean(axis=2) < 110
    ink_recall = float((src_dark & ren_dark).sum() / max(1, src_dark.sum()))
    # Smooth spline renderers (vtracer) trade hard-dark pixels for anti-aliased
    # curves, so accept excellent color fidelity (low MAE) with reasonable ink.
    passed = (mae <= args.mae_threshold and ink_recall >= args.ink_threshold) or \
             (mae <= 8.0 and ink_recall >= 0.75)
    suggestions = []
    if ink_recall < args.ink_threshold:
        suggestions.append("dark ink lost: use --preset dense (or lower --min-area / --epsilon); "
                           "small images are auto-supersampled, verify source resolution")
    if mae > args.mae_threshold:
        suggestions.append("color drift: raise --max-colors (96-128) for gradients/dense figures")
    report = {"ok": passed, "renderer": renderer, "mae": round(mae, 2),
              "ink_recall": round(ink_recall, 3),
              "width": w, "height": h, "suggestions": suggestions}
    if renderer == "svglib":
        report["note"] = "svglib under-reports fidelity on complex paths; verify with resvg before acting on WARN"
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0 if passed else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        print(f"QUALITY_ERROR|{exc}", file=sys.stderr)
        raise SystemExit(1)