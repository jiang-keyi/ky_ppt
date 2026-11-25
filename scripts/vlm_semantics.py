#!/usr/bin/env python3
"""ky_ppt small-VLM semantics module — CPU, via local Ollama.

Two light jobs that a 2B-class VLM can do on CPU (heavy vectorization stays with
vtracer):
  manifest : OCR + text localization -> text-manifest.json (text-manifest schema 1.0)
  regions  : panel / element bounding boxes -> layering hints JSON

 Talks to Ollama HTTP API (default http://localhost:11434). No cloud, no key.
Start Ollama and pull a model first:  ollama pull qwen3-vl:2b
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import urllib.request
from pathlib import Path

from PIL import Image

MANIFEST_PROMPT = (
    "You are an OCR engine for scientific figures. List EVERY visible text label in this image. "
    "Return ONLY a JSON object: {\"texts\":[{\"content\":\"...\",\"bbox\":[x0,y0,x1,y1],\"color\":\"#RRGGBB\"}]}. "
    "bbox is the tight pixel bounding box (origin top-left). Include titles, axis labels, legends, "
    "box captions and node labels. Do not invent text that is not visible. Keep content verbatim."
)
REGIONS_PROMPT = (
    "Identify the major panels/regions of this scientific figure. Return ONLY JSON: "
    "{\"regions\":[{\"label\":\"...\",\"bbox\":[x0,y0,x1,y1]}]}. bbox in pixels, origin top-left."
)


def ollama(host: str, path: str, payload: dict | None, timeout: int) -> dict:
    url = host.rstrip("/") + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group())
        raise


def clamp_box(box, w, h):
    x0, y0, x1, y1 = [float(v) for v in box]
    x0, x1 = sorted((max(0.0, min(x0, w)), max(0.0, min(x1, w))))
    y0, y1 = sorted((max(0.0, min(y0, h)), max(0.0, min(y1, h))))
    return x0, y0, x1, y1


def main() -> int:
    parser = argparse.ArgumentParser(description="ky_ppt VLM semantics (local Ollama, CPU).")
    parser.add_argument("--input-image", required=True, type=Path)
    parser.add_argument("--mode", choices=["manifest", "regions", "check"], default="manifest")
    parser.add_argument("--output", type=Path, help="output JSON path (manifest/regions)")
    parser.add_argument("--model", default="qwen3-vl:2b")
    parser.add_argument("--host", default="http://localhost:11434")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    try:
        tags = ollama(args.host, "/api/tags", None, 15)
    except Exception as exc:
        raise SystemExit(f"VLM_ERROR|Ollama not reachable at {args.host}: {exc}. Start Ollama first.")
    names = [m.get("name", "") for m in tags.get("models", [])]
    if args.mode == "check":
        print(json.dumps({"ok": True, "models": names,
                          "model_present": any(args.model.split(":")[0] in n for n in names)},
                         ensure_ascii=False))
        return 0
    if not any(args.model.split(":")[0] in n for n in names):
        raise SystemExit(f"VLM_ERROR|model {args.model} not pulled. Run: ollama pull {args.model}. Have: {names}")

    image = args.input_image.resolve()
    if not image.is_file():
        raise SystemExit("VLM_ERROR|input image not found")
    with Image.open(image) as im:
        w, h = im.size
    b64 = base64.b64encode(image.read_bytes()).decode()
    prompt = MANIFEST_PROMPT if args.mode == "manifest" else REGIONS_PROMPT
    # Qwen3 soft switch: stop unbounded thinking so the answer lands in `content`
    # (on a 4-core CPU, long thinking otherwise exhausts the token budget -> empty content).
    prompt = "/no_think\n" + prompt
    payload = {"model": args.model, "stream": False,
               "options": {"temperature": 0, "num_predict": 3072},
               "messages": [{"role": "user", "content": prompt, "images": [b64]}]}
    resp = ollama(args.host, "/api/chat", payload, args.timeout)
    msg = resp.get("message", {})
    content = msg.get("content", "") or msg.get("thinking", "")
    try:
        data = extract_json(content)
    except Exception as exc:
        raise SystemExit(f"VLM_ERROR|could not parse model JSON: {exc}; raw={content[:300]}")

    if args.mode == "regions":
        regions = []
        for i, r in enumerate(data.get("regions", [])):
            box = clamp_box(r.get("bbox", [0, 0, w, h]), w, h)
            regions.append({"id": f"region_{i+1:03d}", "label": str(r.get("label", "")),
                            "bbox": [round(v, 1) for v in box]})
        out = {"schema_version": "1.0", "source_canvas": {"width": w, "height": h}, "regions": regions}
    else:
        elements = []
        for i, t in enumerate(data.get("texts", [])):
            content_txt = str(t.get("content", "")).strip()
            if not content_txt or "bbox" not in t:
                continue
            x0, y0, x1, y1 = clamp_box(t["bbox"], w, h)
            if x1 - x0 < 1 or y1 - y0 < 1:
                continue
            color = t.get("color", "#000000")
            if not re.fullmatch(r"#[0-9A-Fa-f]{6}", str(color)):
                color = "#000000"
            elements.append({
                "id": f"text_{i+1:03d}", "content": content_txt,
                "x": round((x0 + x1) / 2, 1), "y": round(y1, 1),
                "coordinate_space": "pixels",
                "font_size": round(max(6.0, (y1 - y0) * 0.8), 1),
                "font_family": "Arial", "fill": color, "text_anchor": "middle",
                "rotation": 0, "paint_order": 1000000000,
                "source_bbox": [round(v, 1) for v in (x0, y0, x1, y1)],
            })
        out = {"schema_version": "1.0", "source_canvas": {"width": w, "height": h},
               "text_elements": elements}

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    n = len(out.get("text_elements", out.get("regions", [])))
    print(json.dumps({"ok": True, "mode": args.mode, "model": args.model, "count": n,
                      "output": str(args.output) if args.output else None}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        print(f"VLM_ERROR|{exc}", file=sys.stderr)
        raise SystemExit(1)