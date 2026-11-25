#!/usr/bin/env python3
"""ky_ppt local image-to-SVG vectorizer — free, offline, no API key.

Pipeline (mimics the Xiaomiao "visible paths + canvas coordinates" contract):
  1. Load PNG/JPEG/WebP, quantize to a small solid-color palette (anti-alias cleanup).
  2. Per color: extract contours WITH hole hierarchy (OpenCV RETR_CCOMP).
  3. Simplify contours, optionally fit cubic Beziers (M/L/C/Z only).
  4. Emit one compound <path> per region (outer + reversed holes, nonzero winding),
     ordered back-to-front by area, viewBox identical to the source pixel canvas,
     every element with a stable id, solid hex fills, inline attributes only.

Output passes backend validate_vector_svg.py and prepare_geometry_cache.py.
CLI: --input-image / --output-svg (legacy credit args accepted
and ignored so existing callers keep working).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)


# ---------------------------------------------------------------- palette
def quantize(rgb: np.ndarray, max_colors: int) -> tuple[np.ndarray, np.ndarray]:
    """Return (indexed image HxW int32, palette Kx3 uint8). Exact colors kept if few."""
    h, w, _ = rgb.shape
    flat = rgb.reshape(-1, 3)
    uniq = np.unique(flat, axis=0)
    if len(uniq) <= max_colors:
        palette = uniq.astype(np.uint8)
        # map every pixel to its exact palette index
        keys = (flat[:, 0].astype(np.int64) << 16) | (flat[:, 1].astype(np.int64) << 8) | flat[:, 2]
        pkeys = (palette[:, 0].astype(np.int64) << 16) | (palette[:, 1].astype(np.int64) << 8) | palette[:, 2]
        order = np.argsort(pkeys)
        idx = order[np.searchsorted(pkeys[order], keys)]
        return idx.reshape(h, w).astype(np.int32), palette
    # Two-stage palette: detail pixels (thin lines, teeth, outlines) are a tiny
    # minority and their colors drift over the gradient background, so a single
    # k-means loses them. Cluster details and background separately, then assign
    # every pixel to the nearest center of the combined palette.
    rng = np.random.default_rng(7)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    local = cv2.blur(gray, (7, 7))
    detail = (np.abs(gray - local) > 8).reshape(-1)
    detail_idx = np.flatnonzero(detail)
    plain_idx = np.flatnonzero(~detail)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.4)
    k_detail = max(8, min(48, max_colors // 2))
    k_bg = max(4, max_colors - k_detail)
    centers_list = []
    if len(detail_idx) >= k_detail:
        s = flat[rng.choice(detail_idx, min(60000, len(detail_idx)), replace=False)]
        _, _, cd = cv2.kmeans(s.astype(np.float32), k_detail, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
        centers_list.append(cd)
    if len(plain_idx) >= k_bg:
        s = flat[rng.choice(plain_idx, min(40000, len(plain_idx)), replace=False)]
        _, _, cb = cv2.kmeans(s.astype(np.float32), k_bg, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
        centers_list.append(cb)
    if not centers_list:
        centers_list.append(flat[rng.choice(len(flat), min(1000, len(flat)), replace=False)].astype(np.float32))
    centers = np.concatenate(centers_list)
    palette = np.clip(np.rint(centers), 0, 255).astype(np.uint8)
    idx = np.empty(len(flat), dtype=np.int32)
    pf = palette.astype(np.int32)
    # Background pixels choose only from background centers so near-white detail
    # centers cannot steal them (speckle); detail pixels use the full palette.
    bg_start = len(centers_list[0]) if len(centers_list) > 1 else 0
    bg_pf = pf[bg_start:] if len(centers_list) > 1 else pf
    for start in range(0, len(flat), 200000):
        chunk = flat[start:start + 200000].astype(np.int32)
        is_detail = detail[start:start + len(chunk)]
        dist_all = ((chunk[:, None, :] - pf[None, :, :]) ** 2).sum(axis=2)
        assigned = dist_all.argmin(axis=1)
        if len(centers_list) > 1:
            dist_bg = ((chunk[:, None, :] - bg_pf[None, :, :]) ** 2).sum(axis=2)
            bg_assigned = dist_bg.argmin(axis=1) + bg_start
            dark_plain = (~is_detail) & (chunk.mean(axis=1) < 128)
            assigned = np.where(is_detail | dark_plain, assigned, bg_assigned)
        idx[start:start + len(chunk)] = assigned
    # Consolidate gray connector-line pixels into dedicated colors so a line
    # cannot alternate between nearby clusters (dashed appearance). Split by
    # luminance band (dark arrows vs light dashed borders keep separate colors)
    # and protect pixels adjacent to dark ink so text anti-aliasing halos are
    # never repainted with a lighter line median.
    mx = flat.max(axis=1).astype(np.int32)
    mn = flat.min(axis=1).astype(np.int32)
    mean = flat.mean(axis=1)
    grayish = detail & ((mx - mn) < 32) & (mean > 80) & (mean < 210)
    dark_ink = (mean < 110).reshape(h, w).astype(np.uint8)
    near_ink = cv2.dilate(dark_ink, np.ones((5, 5), np.uint8)).reshape(-1).astype(bool)
    threshold = max(500, int(0.0002 * len(flat)))
    for lo, hi in ((80, 140), (140, 210)):
        band = grayish & (mean >= lo) & (mean < hi) & ~near_ink
        if band.sum() > threshold:
            med = np.median(flat[band], axis=0)
            palette = np.vstack([palette, np.clip(np.rint(med), 0, 255).astype(np.uint8)])
            idx[band] = len(palette) - 1
    return idx.reshape(h, w), palette


# ---------------------------------------------------------------- geometry
def signed_area(pts: np.ndarray) -> float:
    x, y = pts[:, 0], pts[:, 1]
    return float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y)) / 2.0


def fmt(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def fit_cubic(seg: np.ndarray, t0: np.ndarray, t1: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """Least-squares cubic between seg[0], seg[-1] with unit tangents t0/t1."""
    p0, p3 = seg[0], seg[-1]
    n = len(seg)
    if n < 3:
        return None
    # chord-length parameterization
    d = np.linalg.norm(np.diff(seg, axis=0), axis=1)
    total = d.sum()
    if total <= 1e-9:
        return None
    u = np.concatenate([[0.0], np.cumsum(d)]) / total
    b0 = (1 - u) ** 3
    b1 = 3 * u * (1 - u) ** 2
    b2 = 3 * u ** 2 * (1 - u)
    b3 = u ** 3
    a1 = t0[None, :] * b1[:, None]
    a2 = t1[None, :] * b2[:, None]
    rhs = seg - (b0[:, None] * p0 + b3[:, None] * p3)
    m00 = float((a1 * a1).sum()); m01 = float((a1 * a2).sum()); m11 = float((a2 * a2).sum())
    r0 = float((a1 * rhs).sum()); r1 = float((a2 * rhs).sum())
    det = m00 * m11 - m01 * m01
    if abs(det) < 1e-12:
        return None
    alpha0 = (r0 * m11 - r1 * m01) / det
    alpha1 = (r1 * m00 - r0 * m01) / det
    span = np.linalg.norm(p3 - p0)
    if alpha0 <= 0 or alpha1 <= 0 or alpha0 > span * 3 or alpha1 > span * 3:
        return None
    return p0 + alpha0 * t0, p3 + alpha1 * t1


def bezier_points(p0, c0, c1, p3, u):
    v = 1 - u
    return (v ** 3)[:, None] * p0 + (3 * v ** 2 * u)[:, None] * c0 + (3 * v * u ** 2)[:, None] * c1 + (u ** 3)[:, None] * p3


def unit(v: np.ndarray) -> np.ndarray | None:
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else None


def segment_to_commands(seg: np.ndarray, tol: float, depth: int = 0) -> list[str]:
    """Convert an open polyline segment (endpoints included) to L or C commands.
    The caller has already emitted M for seg[0]."""
    if len(seg) <= 2:
        return [f"L{fmt(seg[-1][0])} {fmt(seg[-1][1])}"] if len(seg) == 2 else []
    chord = unit(seg[-1] - seg[0])
    if chord is None:
        return [f"L{fmt(seg[-1][0])} {fmt(seg[-1][1])}"]
    t0 = unit(seg[min(2, len(seg) - 1)] - seg[0])
    if t0 is None:
        t0 = chord
    t1 = unit(seg[-1] - seg[max(0, len(seg) - 3)])
    if t1 is None:
        t1 = chord
    fit = fit_cubic(seg, t0, t1)
    if fit is not None:
        c0, c1 = fit
        d = np.linalg.norm(np.diff(seg, axis=0), axis=1)
        u = np.concatenate([[0.0], np.cumsum(d)]) / d.sum()
        approx = bezier_points(seg[0], c0, c1, seg[-1], u)
        if float(np.abs(approx - seg).max()) <= tol:
            return [f"C{fmt(c0[0])} {fmt(c0[1])} {fmt(c1[0])} {fmt(c1[1])} {fmt(seg[-1][0])} {fmt(seg[-1][1])}"]
    if depth >= 5 or len(seg) < 6:
        return [f"L{fmt(p[0])} {fmt(p[1])}" for p in seg[1:]]
    mid = len(seg) // 2
    return segment_to_commands(seg[:mid + 1], tol, depth + 1) + segment_to_commands(seg[mid:], tol, depth + 1)


def contour_to_path_commands(pts: np.ndarray, smooth: bool, tol: float) -> str:
    """Closed contour -> M ... Z. pts has no duplicated closing point."""
    if len(pts) < 3:
        return ""
    if not smooth:
        body = "".join(f"L{fmt(p[0])} {fmt(p[1])}" for p in pts[1:])
        return f"M{fmt(pts[0][0])} {fmt(pts[0][1])}" + body + "Z"
    # split the closed ring at the two farthest-apart anchor points to avoid a
    # seam artifact, fit each half as an open segment
    i0 = 0
    i1 = len(pts) // 2
    ring = np.vstack([pts, pts[0]])
    seg_a = ring[i0:i1 + 1]
    seg_b = ring[i1:len(ring)]
    cmds = [f"M{fmt(pts[0][0])} {fmt(pts[0][1])}"]
    cmds += segment_to_commands(seg_a, tol)
    cmds += segment_to_commands(seg_b, tol)
    cmds.append("Z")
    return "".join(cmds)


def build_region_path(outer: np.ndarray, holes: list[np.ndarray], smooth: bool, tol: float) -> str:
    """Compound path, nonzero winding: outer positive area, holes negative."""
    parts = []
    if signed_area(outer) < 0:
        outer = outer[::-1]
    d = contour_to_path_commands(outer, smooth, tol)
    if d:
        parts.append(d)
    for hole in holes:
        if len(hole) < 3:
            continue
        if signed_area(hole) > 0:
            hole = hole[::-1]
        d = contour_to_path_commands(hole, smooth, tol)
        if d:
            parts.append(d)
    return "".join(parts)


# ---------------------------------------------------------------- main
def vectorize(image_path: Path, output_svg: Path, max_colors: int, min_area: float,
              epsilon: float, smooth: bool, fit_tol: float, merge_colors: bool = False) -> dict:
    with Image.open(image_path) as im:
        im = im.convert("RGB")
        w, h = im.size
        rgb = np.asarray(im, dtype=np.uint8)
    # Supersample small images: a 1px-wide stroke encloses ~zero contour area and
    # vanishes after filtering/fill, so give thin ink real interior first.
    S = 2 if max(w, h) < 1600 else 1
    if S > 1:
        rgb = cv2.resize(rgb, (w * S, h * S), interpolation=cv2.INTER_NEAREST)
    indexed, palette = quantize(rgb, max_colors)
    areas = np.bincount(indexed.ravel(), minlength=len(palette))
    # Back-to-front: light colors first, dark ink last (area breaks ties), so
    # anti-aliasing halo blobs can never cover text strokes or dark connectors.
    lum = palette.astype(np.float64).mean(axis=1)
    order = np.lexsort((-areas, -lum))
    # Protection map: gap-bridging must never grow over dark ink (text strokes).
    near_ink = cv2.dilate((rgb.mean(axis=2) < 110).astype(np.uint8), np.ones((5, 5), np.uint8))
    min_area_eff = min_area * S * S
    epsilon_eff = epsilon * S

    root = ET.Element(f"{{{SVG_NS}}}svg", {
        "viewBox": f"0 0 {w} {h}", "width": str(w), "height": str(h),
    })
    path_count = 0
    for ci in order:
        if areas[ci] < min_area_eff:
            continue
        mask = (indexed == ci).astype(np.uint8) * 255
        # Bridge small gaps in low-saturation mid-value "connector line" colors
        # (gray arrows). Saturated details (teeth, curves) are left untouched.
        r, g, b = (int(v) for v in palette[ci])
        if max(r, g, b) - min(r, g, b) < 28 and 80 < (r + g + b) / 3 < 205:
            closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8), iterations=2)
            mask = np.where(near_ink > 0, mask, closed)
        contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        if hierarchy is None:
            continue
        hierarchy = hierarchy[0]
        regions = []
        for i, cnt in enumerate(contours):
            if hierarchy[i][3] != -1:
                continue  # hole; attached to its outer below
            area = cv2.contourArea(cnt)
            if area < min_area_eff:
                continue
            holes = []
            j = hierarchy[i][2]
            while j != -1:
                if cv2.contourArea(contours[j]) >= min_area_eff:
                    holes.append(contours[j])
                j = hierarchy[j][0]
            regions.append((area, cnt, holes))
        regions.sort(key=lambda r: -r[0])
        color = "#{:02x}{:02x}{:02x}".format(*palette[ci])
        merged_parts = []
        for area, cnt, holes in regions:
            simp = cv2.approxPolyDP(cnt, epsilon_eff, True).reshape(-1, 2).astype(np.float64) / S
            hole_pts = [cv2.approxPolyDP(hc, epsilon_eff, True).reshape(-1, 2).astype(np.float64) / S for hc in holes]
            d = build_region_path(simp, hole_pts, smooth, fit_tol)
            if not d:
                continue
            if merge_colors:
                merged_parts.append(d)
                continue
            path_count += 1
            ET.SubElement(root, f"{{{SVG_NS}}}path", {
                "id": f"path_{path_count:04d}",
                "d": d,
                "fill": color,
                "fill-rule": "nonzero",
                "stroke": "none",
            })
        if merge_colors and merged_parts:
            path_count += 1
            ET.SubElement(root, f"{{{SVG_NS}}}path", {
                "id": f"path_{path_count:04d}",
                "d": "".join(merged_parts),
                "fill": color,
                "fill-rule": "nonzero",
                "stroke": "none",
            })
    if path_count == 0:
        raise SystemExit("LOCAL_VECTORIZE_ERROR|no drawable regions found")
    output_svg.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output_svg, encoding="utf-8", xml_declaration=True)
    return {"paths": path_count, "colors": int((areas >= min_area).sum()), "width": w, "height": h}


def main() -> int:
    parser = argparse.ArgumentParser(description="ky_ppt local image-to-SVG vectorizer (free, offline).")
    parser.add_argument("--input-image", required=True, type=Path)
    parser.add_argument("--output-svg", required=True, type=Path)
    parser.add_argument("--estimated-credits", type=int, default=1)  # ignored, local is free
    parser.add_argument("--approve-high-cost", action="store_true")   # ignored
    parser.add_argument("--max-colors", type=int, default=None)
    parser.add_argument("--min-area", type=float, default=None, help="speckle filter, px^2")
    parser.add_argument("--epsilon", type=float, default=None, help="contour simplification, px")
    parser.add_argument("--fit-tol", type=float, default=None, help="cubic fit tolerance, px")
    parser.add_argument("--no-smooth", action="store_true", help="polyline output instead of cubics")
    parser.add_argument("--merge-colors", action="store_true",
                        help="one compound path per color (fewer, larger shapes; per-color editing)")
    parser.add_argument("--preset", choices=["auto", "flat", "dense"], default=None,
                        help="auto-pick quality params; explicit flags override the preset")
    parser.add_argument("--validator", type=Path, help="optional validate_vector_svg.py to run after")
    args = parser.parse_args()

    image = args.input_image.resolve()
    if not image.is_file():
        raise SystemExit("LOCAL_VECTORIZE_ERROR|input image not found")
    if image.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise SystemExit("LOCAL_VECTORIZE_ERROR|unsupported format (PNG/JPEG/WebP only)")
    with Image.open(image) as probe:
        if probe.width * probe.height > 40_000_000:
            raise SystemExit("LOCAL_VECTORIZE_ERROR|image exceeds the 40 MP safety limit")
        probe_rgb = np.asarray(probe.convert("RGB"), dtype=np.uint8)

    preset = args.preset
    if preset == "auto":
        g = cv2.cvtColor(probe_rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
        detail_frac = float((np.abs(g - cv2.blur(g, (7, 7))) > 8).mean())
        uniq = len(np.unique(probe_rgb.reshape(-1, 3)[::37], axis=0))
        preset = "dense" if (detail_frac > 0.18 or uniq > 4000) else "flat"
    presets = {"flat": (24, 6.0, 0.6, 0.6), "dense": (128, 0.5, 0.3, 0.35)}
    base = presets.get(preset, (24, 6.0, 0.6, 0.6))
    max_colors = args.max_colors if args.max_colors is not None else base[0]
    min_area = args.min_area if args.min_area is not None else base[1]
    epsilon = args.epsilon if args.epsilon is not None else base[2]
    fit_tol = args.fit_tol if args.fit_tol is not None else base[3]
    if not (2 <= max_colors <= 256):
        raise SystemExit("LOCAL_VECTORIZE_ERROR|--max-colors must be within 2..256")
    if min_area < 0 or epsilon <= 0 or fit_tol <= 0:
        raise SystemExit("LOCAL_VECTORIZE_ERROR|invalid --min-area/--epsilon/--fit-tol")

    stats = vectorize(image, args.output_svg.resolve(),
                      max_colors, min_area, epsilon,
                      not args.no_smooth, fit_tol, args.merge_colors)
    stats["preset"] = preset or "manual"
    if args.validator and args.validator.is_file():
        import subprocess
        proc = subprocess.run([sys.executable, str(args.validator), "--svg", str(args.output_svg.resolve())],
                              capture_output=True, text=True)
        if proc.returncode:
            raise SystemExit(f"LOCAL_VECTORIZE_ERROR|validation failed: {proc.stdout}")
    print("剩余额度：本地模式，不计费", file=sys.stderr)
    print(json.dumps({"ok": True, "image_id": f"local_{uuid.uuid4().hex[:10]}",
                      "output_svg": str(args.output_svg.resolve()), "credits_left": None,
                      "engine": "local-opencv", **stats}, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        print(f"LOCAL_VECTORIZE_ERROR|{exc}", file=sys.stderr)
        raise SystemExit(1)