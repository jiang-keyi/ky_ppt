#!/usr/bin/env python3
"""ky_ppt vtracer geometry engine — free, offline, CPU-only.

Wraps visioncortex vtracer (Rust, O(n), color stacking, spline fitting) and
normalizes its SVG to the backend contract: finite viewBox, stable unique ids,
solid hex fills, nonzero winding, absolute M/L/C/Z commands only, no CSS/raster.

vtracer's stacked mode paints solid layers back-to-front (no holes), which the
backend handles as separate shapes. Small images are optionally supersampled 2x
so thin ink survives curve fitting; coordinates are scaled back to the source
canvas so the viewBox matches the original pixel dimensions.

CLI mirrors vectorize_local.py: --input-image / --output-svg / --validator.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PIL import Image

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)
NUM = re.compile(r"-?\d*\.?\d+")


def fmt(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def bake_and_scale(d: str, tx: float, ty: float, scale: float) -> str:
    """Apply translate(tx,ty) then divide by scale. All vtracer coords are
    absolute x,y pairs (M/L/C), so numeric tokens alternate x,y."""
    counter = 0

    def repl(m: re.Match) -> str:
        nonlocal counter
        i = counter
        counter += 1
        v = float(m.group())
        v = (v + tx) / scale if i % 2 == 0 else (v + ty) / scale
        return fmt(v)

    return NUM.sub(repl, d)


def normalize(raw_svg: Path, out_svg: Path, w: int, h: int, scale: float) -> dict:
    text = raw_svg.read_text(encoding="utf-8")
    if "<!ENTITY" in text.upper():
        raise SystemExit("VTRACER_ERROR|SVG contains entity declarations (rejected)")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    root = ET.fromstring(text)
    root.set("viewBox", f"0 0 {w} {h}")
    root.set("width", str(w))
    root.set("height", str(h))
    colors = set()
    count = 0
    for el in root.iter(f"{{{SVG_NS}}}path"):
        d = el.get("d", "")
        cmds = set(re.findall(r"[A-Za-z]", d))
        if not cmds <= {"M", "L", "C", "Z"}:
            raise SystemExit(f"VTRACER_ERROR|unsupported path commands: {cmds - {'M','L','C','Z'}}")
        tx = ty = 0.0
        transform = el.get("transform", "")
        if transform:
            m = re.fullmatch(r"translate\(\s*([-\d.]+)[ ,]+([-\d.]+)\s*\)", transform.strip())
            if not m:
                raise SystemExit(f"VTRACER_ERROR|unsupported transform: {transform}")
            tx, ty = float(m.group(1)), float(m.group(2))
        d = bake_and_scale(d, tx, ty, scale)
        fill = el.get("fill", "#000000")
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", fill):
            raise SystemExit(f"VTRACER_ERROR|non-solid fill: {fill}")
        count += 1
        colors.add(fill.lower())
        el.clear()
        el.set("id", f"path_{count:04d}")
        el.set("d", d)
        el.set("fill", fill)
        el.set("fill-rule", "nonzero")
        el.set("stroke", "none")
    # drop any non-path children (vtracer emits only paths under svg)
    for child in list(root):
        if child.tag != f"{{{SVG_NS}}}path":
            root.remove(child)
    if count == 0:
        raise SystemExit("VTRACER_ERROR|no drawable paths produced")
    out_svg.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(out_svg, encoding="utf-8", xml_declaration=True)
    return {"paths": count, "colors": len(colors), "width": w, "height": h}


def main() -> int:
    parser = argparse.ArgumentParser(description="ky_ppt vtracer geometry engine (free, offline, CPU).")
    parser.add_argument("--input-image", required=True, type=Path)
    parser.add_argument("--output-svg", required=True, type=Path)
    parser.add_argument("--estimated-credits", type=int, default=1)  # ignored, local free
    parser.add_argument("--approve-high-cost", action="store_true")   # ignored
    parser.add_argument("--mode", choices=["spline", "polygon"], default="spline")
    parser.add_argument("--filter-speckle", type=int, default=2)
    parser.add_argument("--color-precision", type=int, default=6)
    parser.add_argument("--layer-difference", type=int, default=16)
    parser.add_argument("--corner-threshold", type=int, default=60)
    parser.add_argument("--length-threshold", type=float, default=4.0)
    parser.add_argument("--splice-threshold", type=int, default=45)
    parser.add_argument("--path-precision", type=int, default=3)
    parser.add_argument("--no-supersample", action="store_true")
    parser.add_argument("--validator", type=Path)
    args = parser.parse_args()

    image = args.input_image.resolve()
    if not image.is_file():
        raise SystemExit("VTRACER_ERROR|input image not found")
    if image.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise SystemExit("VTRACER_ERROR|unsupported format (PNG/JPEG/WebP only)")
    with Image.open(image) as probe:
        w, h = probe.size
        if w * h > 40_000_000:
            raise SystemExit("VTRACER_ERROR|image exceeds the 40 MP safety limit")
        rgb = probe.convert("RGB")

    import tempfile
    scale = 1.0
    work = image
    tmp_dir = Path(tempfile.mkdtemp(prefix="ky_ppt_vtracer_"))
    if not args.no_supersample and max(w, h) < 1600:
        scale = 2.0
        work = tmp_dir / "up.png"
        rgb.resize((int(w * scale), int(h * scale)), Image.NEAREST).save(work)
    raw = tmp_dir / "raw.svg"
    import vtracer
    vtracer.convert_image_to_svg_py(
        str(work), str(raw),
        colormode="color", hierarchical="stacked", mode=args.mode,
        filter_speckle=args.filter_speckle, color_precision=args.color_precision,
        layer_difference=args.layer_difference, corner_threshold=args.corner_threshold,
        length_threshold=args.length_threshold, max_iterations=10,
        splice_threshold=args.splice_threshold, path_precision=args.path_precision,
    )
    stats = normalize(raw, args.output_svg.resolve(), w, h, scale)
    stats["engine"] = "vtracer"
    stats["supersample"] = scale
    if args.validator and args.validator.is_file():
        import subprocess
        proc = subprocess.run([sys.executable, str(args.validator), "--svg", str(args.output_svg.resolve())],
                              capture_output=True, text=True)
        if proc.returncode:
            raise SystemExit(f"VTRACER_ERROR|validation failed: {proc.stdout}")
    print("剩余额度：本地模式，不计费", file=sys.stderr)
    print(json.dumps({"ok": True, "image_id": f"vtracer_{uuid.uuid4().hex[:10]}",
                      "output_svg": str(args.output_svg.resolve()), "credits_left": None,
                      **stats}, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        print(f"VTRACER_ERROR|{exc}", file=sys.stderr)
        raise SystemExit(1)