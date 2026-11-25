#!/usr/bin/env python3
"""ky_ppt main entry: image -> editable PPTX / Illustrator AI, fully local & free.

Pipeline: allocate job -> geometry (vtracer default, OpenCV fallback) ->
optional small-VLM text manifest -> merge live text -> geometry cache -> PPTX/AI.
No API key, no credits, no cloud. VLM semantics use a local Ollama model on CPU.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


def run(*args: str):
    subprocess.run([sys.executable, "-X", "utf8", *args], check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--application", choices=["ppt", "ai"], default="ppt")
    parser.add_argument("--input-image", required=True, type=Path, help="text-free cleaned image")
    parser.add_argument("--text-manifest", type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--input-pptx", type=Path)
    parser.add_argument("--slide-index", type=int, default=0)
    parser.add_argument("--max-colors", type=int, default=None)
    parser.add_argument("--min-area", type=float, default=None)
    parser.add_argument("--epsilon", type=float, default=None)
    parser.add_argument("--fit-tol", type=float, default=None)
    parser.add_argument("--preset", choices=["auto", "flat", "dense"], default=None)
    parser.add_argument("--merge-colors", action="store_true")
    parser.add_argument("--no-smooth", action="store_true")
    parser.add_argument("--engine", choices=["vtracer", "opencv"], default="vtracer",
                        help="geometry engine: vtracer (smooth curves, default) or opencv (flat quantize)")
    parser.add_argument("--ocr-manifest", action="store_true",
                        help="auto-generate text manifest via RapidOCR (CPU, precise text+bbox; recommended)")
    parser.add_argument("--ocr-min-score", type=float, default=0.5)
    parser.add_argument("--vlm-manifest", action="store_true",
                        help="auto-generate text manifest via local Ollama VLM (best for short labels/regions)")
    parser.add_argument("--vlm-model", default="qwen3-vl:2b")
    parser.add_argument("--vlm-host", default="http://localhost:11434")
    args = parser.parse_args()

    allocator = SCRIPTS / "allocate_shibielujing_name.py"
    base_name = subprocess.check_output(
        [sys.executable, str(allocator), "--root", str(args.output_root.resolve())], text=True
    ).strip().splitlines()[-1]
    job = args.output_root.resolve() / base_name
    job.mkdir(parents=True, exist_ok=True)
    if args.text_manifest:
        recorded = job / "text-manifest.json"
        shutil.copyfile(args.text_manifest.resolve(), recorded)
        args.text_manifest = recorded
    elif args.ocr_manifest:
        recorded = job / "text-manifest.json"
        run(str(SCRIPTS / "ocr_manifest.py"), "--input-image", str(args.input_image.resolve()),
            "--output", str(recorded), "--min-score", str(args.ocr_min_score))
        args.text_manifest = recorded
    elif args.vlm_manifest:
        recorded = job / "text-manifest.json"
        run(str(SCRIPTS / "vlm_semantics.py"), "--input-image", str(args.input_image.resolve()),
            "--mode", "manifest", "--output", str(recorded),
            "--model", args.vlm_model, "--host", args.vlm_host)
        args.text_manifest = recorded

    raw_svg = job / f"{base_name}-vector.svg"
    master_svg = job / f"{base_name}.svg"
    if args.engine == "vtracer":
        vector_command = [
            str(SCRIPTS / "vectorize_vtracer.py"),
            "--input-image", str(args.input_image.resolve()),
            "--output-svg", str(raw_svg),
            "--validator", str(SCRIPTS / "validate_vector_svg.py"),
        ]
        if args.no_smooth:
            vector_command += ["--mode", "polygon"]
    else:
        vector_command = [
            str(SCRIPTS / "vectorize_local.py"),
            "--input-image", str(args.input_image.resolve()),
            "--output-svg", str(raw_svg),
            "--validator", str(SCRIPTS / "validate_vector_svg.py"),
        ]
        if args.preset:
            vector_command += ["--preset", args.preset]
        if args.max_colors is not None:
            vector_command += ["--max-colors", str(args.max_colors)]
        if args.min_area is not None:
            vector_command += ["--min-area", str(args.min_area)]
        if args.epsilon is not None:
            vector_command += ["--epsilon", str(args.epsilon)]
        if args.fit_tol is not None:
            vector_command += ["--fit-tol", str(args.fit_tol)]
        if args.merge_colors:
            vector_command.append("--merge-colors")
        if args.no_smooth:
            vector_command.append("--no-smooth")
    run(*vector_command)

    if args.text_manifest:
        # Enforce text-on-top in code: paint_order must exceed the geometry
        # element count, otherwise later shapes cover the restored labels.
        import xml.etree.ElementTree as ET
        geom_count = len(list(ET.parse(raw_svg).getroot()))
        manifest = json.loads(args.text_manifest.read_text(encoding="utf-8-sig"))
        for i, item in enumerate(manifest.get("text_elements", [])):
            item["paint_order"] = max(int(item.get("paint_order", 0)), geom_count + 1 + i)
        args.text_manifest.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        run(str(SCRIPTS / "merge_live_text.py"), "--input-svg", str(raw_svg),
            "--text-manifest", str(args.text_manifest.resolve()), "--output-svg", str(master_svg))
    else:
        shutil.copyfile(raw_svg, master_svg)

    command = [str(SCRIPTS / ("run_from_svg.py" if args.application == "ppt" else "run_illustrator.py")),
               "--input-svg", str(master_svg), "--output-root", str(args.output_root.resolve()),
               "--job-name", base_name]
    if args.application == "ppt":
        command.extend(["--slide-index", str(args.slide_index)])
        if args.input_pptx:
            command.extend(["--input-pptx", str(args.input_pptx.resolve())])
    elif args.input_pptx:
        raise ValueError("--input-pptx only applies to PowerPoint")
    run(*command)
    print(json.dumps({"ok": True, "mode": "local-free", "base_name": base_name,
                      "master_svg": str(master_svg), "job_dir": str(job)},
                     ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())