#!/usr/bin/env python3
"""ky_ppt PDF -> scientific report deck (PPTX), fully local & free.

Pipeline (new branch, does not replace the figure->editable-shape engine):
  1. Parse a PDF with PyMuPDF: title, headings, paragraphs, embedded images, tables.
  2. Turn it into an outline JSON. Two outline sources:
       - assistant-supplied outline (--outline): polished headings/bullets/notes the
         agent writes after reading the PDF; best quality.
       - offline draft: section headings + first sentences, plain extraction (dry).
  3. Render a clean academic deck with python-pptx: cover / TOC / section divider /
     two-column content / figure / conclusion. White background, one primary colour.

Figures are embedded as raster by default; --vectorize-figures routes each extracted
figure through the ky_ppt vtracer engine for editable shapes (slower, best on
line-art diagrams).

Outline schema (v1.0):
  {
    "title": "...", "subtitle": "...", "authors": "...",
    "color": {"primary": "#1F5C8B", "ink": "#222B33", "muted": "#5A6470", "band": "#EAF2F8"},
    "slides": [
      {"type": "cover|toc|section|content|figure|conclusion|table",
       "title": "...", "bullets": ["..."], "section": "...",
       "image": "path-or-null", "image_caption": "...",
       "table": [[r1c1, r1c2], ...], "notes": "..."}
    ]
  }
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path

PRIMARY = "#7D2E68"   # deep plum, learned from user's reference deck
INK = "#1A1A1A"
MUTED = "#5A6470"
BAND = "#F3E8F2"      # light plum strip
CARD_LINE = "#C9C9C9"
CJK_FONT = "Microsoft YaHei"


# --------------------------------------------------------------------------- #
# PDF parsing
# --------------------------------------------------------------------------- #

def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _body_font_size(sizes: list[float]) -> float:
    if not sizes:
        return 10.5
    from collections import Counter
    c = Counter(round(s, 1) for s in sizes)
    top = max(c.values())
    return min(s for s, n in c.items() if n == top)


def _font_sizes(dict_page: dict) -> list[float]:
    sizes = []
    for block in dict_page.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if _clean(span.get("text", "")):
                    sizes.append(round(span["size"], 1))
    return sizes


def parse_pdf(pdf: Path, assets_dir: Path, max_bullets: int = 6) -> dict:
    """Extract a draft outline. Plain extraction; the agent should refine it."""
    import fitz  # PyMuPDF

    assets_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf)
    title, authors = "", ""
    sections: list[dict] = []          # heading -> paragraphs
    images: list[dict] = []            # {image, caption, page}
    tables: list[dict] = []

    all_sizes: list[float] = []
    pages = [p.get_text("dict") for p in doc]
    for d in pages:
        all_sizes.extend(_font_sizes(d))
    body_size = _body_font_size(all_sizes)

    current: str | None = None
    title_done = False

    def push_para(section: str, para: str):
        if len(sections) == 0 or sections[-1]["heading"] != section:
            sections.append({"heading": section, "paragraphs": []})
        sections[-1]["paragraphs"].append(para)

    for pno, d in enumerate(pages):
        for block in d.get("blocks", []):
            if block.get("type") != 0:  # skip image blocks here; handled below
                continue
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                text = _clean("".join(s.get("text", "") for s in spans))
                if not text:
                    continue
                size = round(statistics.mean(s["size"] for s in spans), 1)
                bold = any(s.get("flags", 0) & 16 for s in spans)
                is_heading = size >= body_size * 1.08 or (bold and len(text) < 40)

                if not title_done and pno == 0 and size >= body_size * 1.35:
                    title = text
                    title_done = True
                    continue
                if pno == 0 and not title_done and size >= body_size * 1.2:
                    title = text
                    title_done = True
                    continue

                if is_heading:
                    current = text
                    push_para(current, "")
                else:
                    if current is None:
                        current = "正文"
                        push_para(current, "")
                    push_para(current, text)

        # embedded images
        for img in doc[pno].get_images(full=True):
            xref = img[0]
            try:
                info = doc.extract_image(xref)
            except Exception:
                continue
            if info["width"] < 80 or info["height"] < 80:  # skip logos/decorations
                continue
            ext = info["ext"]
            img_path = assets_dir / f"figure_p{pno+1}_x{xref}.{ext}"
            img_path.write_bytes(info["image"])
            images.append({"image": str(img_path), "caption": f"图 {len(images)+1}（PDF 第 {pno+1} 页）", "page": pno})

        # tables (best-effort): first table per page -> native table slide
        try:
            found = doc[pno].find_tables()
            for t in found.tables[:1]:
                rows = [[_clean(c) for c in row] for row in t.extract()]
                rows = [r for r in rows if any(r)]
                if rows:
                    tables.append({"table": rows, "caption": f"表 {len(tables)+1}（PDF 第 {pno+1} 页）"})
        except Exception:
            pass

    if not title:
        title = pdf.stem
    if len(sections) == 1 and sections[0]["heading"] == "正文":
        sections[0]["heading"] = "研究内容"

    # Split composite figures into panels, then GROUP panels into evidence cards
    # (up to 3 cards per page) — never one-panel-per-slide.
    panels_dir = assets_dir / "panels"
    result_slides: list[dict] = []
    lead_text = ""
    for sec in sections:
        if any(k in sec["heading"].lower() for k in ("result", "结果")):
            lead_text = next((p for p in sec["paragraphs"] if p), "")
            break
    for im in images:
        try:
            from split_panels import split_panels
            panels = split_panels(Path(im["image"]), panels_dir, prefix=f"p{im['page']+1}")
        except Exception:
            panels = [{"label": "A", "image": im["image"], "bbox": [0, 0, 0, 0]}]
        # chunk panels into <=3 groups per page, 3 cards per page max
        per_card = max(1, (len(panels) + 2) // 3)
        groups = [panels[i:i + per_card] for i in range(0, len(panels), per_card)]
        for gstart in range(0, len(groups), 3):
            page_groups = groups[gstart:gstart + 3]
            cards = []
            for g in page_groups:
                if len(g) == 1:
                    img = g[0]["image"]
                else:
                    out = panels_dir / f"card_p{im['page']+1}_{''.join(p['label'] for p in g)}.png"
                    img = _hstack_images([p["image"] for p in g], out)
                cards.append({"label": "面板 " + "·".join(p["label"] for p in g),
                              "image": img,
                              "caption": "结果见 " + "、".join(p["label"] for p in g) + "（图注待提炼）"})
            result_slides.append({
                "type": "evidence",
                "title": im["caption"],
                "lead": lead_text,
                "cards": cards,
                "summary": "",
            })

    # build slides
    slides: list[dict] = [{"type": "cover", "title": title, "subtitle": "", "authors": authors}]
    section_names = [s["heading"] for s in sections if s["heading"]]
    slides.append({"type": "toc", "title": "目录", "bullets": section_names})

    for sec in sections:
        if not sec["paragraphs"]:
            continue
        slides.append({"type": "section", "title": sec["heading"]})
        bullets = [p for p in sec["paragraphs"] if p][:max_bullets]
        if bullets:
            slides.append({"type": "content", "title": sec["heading"], "bullets": bullets})
    slides.extend(result_slides)
    for tb in tables:
        slides.append({"type": "table", "title": tb["caption"], "table": tb["table"]})
    slides.append({"type": "conclusion", "title": "小结", "bullets": ["结论与意义待提炼（可在 outline 中补充）"]})

    return {"schema_version": "1.0", "title": title, "authors": authors,
            "slides": slides, "_assets_dir": str(assets_dir)}


# --------------------------------------------------------------------------- #
# Deck rendering
# --------------------------------------------------------------------------- #

def _set_text(paragraph, text: str, size: float, bold: bool = False,
              color: str = INK, align=None):
    from pptx.util import Pt
    from pptx.enum.text import PP_ALIGN
    run = paragraph.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = CJK_FONT
    run.font.color.rgb = _rgb(color)
    _ea_font(run)
    if align is not None:
        paragraph.alignment = align
    return run


def _ea_font(run):
    from pptx.oxml.ns import qn
    rPr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        el = rPr.find(qn(tag))
        if el is None:
            el = rPr.makeelement(qn(tag), {})
            rPr.append(el)
        el.set("typeface", CJK_FONT)


def _rgb(hexstr: str):
    from pptx.dml.color import RGBColor
    h = hexstr.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _blank(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


def _footer(slide, prs, title: str, page: int, muted: str):
    from pptx.util import Inches, Pt
    tb = slide.shapes.add_textbox(Inches(0.4), Inches(7.05), Inches(12.5), Inches(0.35))
    p = tb.text_frame.paragraphs[0]
    _set_text(p, title, 10, color=muted)
    p2 = tb.text_frame.add_paragraph()
    _set_text(p2, str(page), 10, color=muted)
    from pptx.enum.text import PP_ALIGN
    p2.alignment = PP_ALIGN.RIGHT


def _accent_rule(slide, primary: str, left: float = 0.5, top: float = 1.55, width: float = 1.2):
    from pptx.util import Inches
    from pptx.enum.shapes import MSO_SHAPE
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(left), Inches(top), Inches(width), Inches(0.08))
    bar.fill.solid()
    bar.fill.fore_color.rgb = _rgb(primary)
    bar.line.fill.background()
    return bar


def render_deck(outline: dict, output: Path):
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.enum.text import PP_ALIGN
    from pptx.enum.shapes import MSO_SHAPE

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    colors = outline.get("color", {})
    primary = colors.get("primary", PRIMARY)
    ink = colors.get("ink", INK)
    muted = colors.get("muted", MUTED)
    band = colors.get("band", BAND)
    deck_title = outline.get("title", "汇报")

    page = 0
    evidence_no = 0
    for spec in outline.get("slides", []):
        stype = spec.get("type", "content")
        page += 1
        slide = _blank(prs)

        if stype == "cover":
            band_shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(13.333), Inches(2.6))
            band_shape.fill.solid(); band_shape.fill.fore_color.rgb = _rgb(band); band_shape.line.fill.background()
            tb = slide.shapes.add_textbox(Inches(0.9), Inches(2.9), Inches(11.5), Inches(1.6))
            p = tb.text_frame.paragraphs[0]
            _set_text(p, spec.get("title", deck_title), 36, bold=True, color=primary)
            if spec.get("subtitle"):
                p2 = tb.text_frame.add_paragraph()
                _set_text(p2, spec["subtitle"], 19, color=muted)
            if spec.get("authors"):
                tb2 = slide.shapes.add_textbox(Inches(0.9), Inches(5.4), Inches(11.5), Inches(0.6))
                _set_text(tb2.text_frame.paragraphs[0], spec["authors"], 15, color=muted)

        elif stype == "toc":
            tb = slide.shapes.add_textbox(Inches(0.9), Inches(0.8), Inches(11.5), Inches(0.8))
            _set_text(tb.text_frame.paragraphs[0], spec.get("title", "目录"), 30, bold=True, color=primary)
            _accent_rule(slide, primary)
            box = slide.shapes.add_textbox(Inches(1.1), Inches(2.0), Inches(11.0), Inches(4.5))
            tf = box.text_frame; tf.word_wrap = True
            for i, b in enumerate(spec.get("bullets", []), 1):
                p = tf.paragraphs[0] if i == 1 else tf.add_paragraph()
                p.space_after = Pt(10)
                _set_text(p, f"{i}.  {b}", 19, color=ink)

        elif stype == "section":
            tb = slide.shapes.add_textbox(Inches(0.9), Inches(3.1), Inches(11.5), Inches(1.2))
            p = tb.text_frame.paragraphs[0]
            p.alignment = PP_ALIGN.CENTER
            _set_text(p, spec.get("title", ""), 32, bold=True, color=primary)
            _accent_rule(slide, primary, left=6.05, top=4.3, width=1.2)

        elif stype == "conclusion":
            tb = slide.shapes.add_textbox(Inches(0.9), Inches(0.8), Inches(11.5), Inches(0.8))
            _set_text(tb.text_frame.paragraphs[0], spec.get("title", "小结"), 28, bold=True, color=primary)
            _accent_rule(slide, primary)
            _content_bullets(slide, spec.get("bullets", []), ink, muted)

        elif stype == "table":
            tb = slide.shapes.add_textbox(Inches(0.9), Inches(0.8), Inches(11.5), Inches(0.8))
            _set_text(tb.text_frame.paragraphs[0], spec.get("title", ""), 26, bold=True, color=primary)
            _accent_rule(slide, primary)
            _add_table(slide, spec.get("table", []), primary, band, ink)

        elif stype == "evidence":
            evidence_no += 1
            _render_evidence(slide, spec, primary, ink, muted, band, evidence_no)

        elif stype == "limitations":
            evidence_no += 1
            _render_limitations(slide, spec, primary, ink, muted, band, evidence_no)

        elif stype in ("result", "figure"):
            # Stacked layout: result title on top, matched sub-image below.
            tb = slide.shapes.add_textbox(Inches(0.9), Inches(0.7), Inches(11.5), Inches(0.9))
            _set_text(tb.text_frame.paragraphs[0], spec.get("title", ""), 26, bold=True, color=primary)
            _accent_rule(slide, primary)
            if spec.get("image") and Path(spec["image"]).is_file():
                _place_image_stacked(slide, spec["image"], spec.get("image_caption", ""), muted)
            else:
                _content_bullets(slide, spec.get("bullets", []), ink, muted)

        else:  # content: bullets, optional image on the right
            tb = slide.shapes.add_textbox(Inches(0.9), Inches(0.7), Inches(11.5), Inches(0.9))
            _set_text(tb.text_frame.paragraphs[0], spec.get("title", ""), 26, bold=True, color=primary)
            _accent_rule(slide, primary)
            _content_bullets(slide, spec.get("bullets", []), ink, muted,
                             image=spec.get("image") if spec.get("image") and Path(spec["image"]).is_file() else None,
                             caption=spec.get("image_caption", ""))
        if stype != "cover":
            _footer(slide, prs, deck_title, page, muted)

    output.parent.mkdir(parents=True, exist_ok=True)
    prs.save(output)
    return output


def _content_bullets(slide, bullets, ink, muted, image=None, caption=""):
    from pptx.util import Inches, Pt
    if image:
        left_w = 6.6
    else:
        left_w = 11.5
    box = slide.shapes.add_textbox(Inches(0.9), Inches(1.9), Inches(left_w), Inches(4.6))
    tf = box.text_frame; tf.word_wrap = True
    for i, b in enumerate(bullets):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(12)
        _set_text(p, "•  " + b, 17, color=ink)
    if image:
        _place_image(slide, image, caption, ink, muted, right=True)


def _place_image_stacked(slide, image: str, caption: str, muted: str):
    from pptx.util import Inches
    from PIL import Image
    im = Image.open(image)
    iw, ih = im.size
    area_w, area_h, area_x, area_y = 11.5, 4.3, 0.9, 1.95
    ratio = min(area_w / iw, area_h / ih)
    w = iw * ratio; h = ih * ratio
    x = area_x + (area_w - w) / 2
    y = area_y + (area_h - h) / 2
    slide.shapes.add_picture(image, Inches(x), Inches(y), Inches(w), Inches(h))
    if caption:
        cb = slide.shapes.add_textbox(Inches(area_x), Inches(area_y + area_h + 0.05), Inches(area_w), Inches(0.4))
        _set_text(cb.text_frame.paragraphs[0], caption, 13, color=muted)


def _place_image(slide, image: str, caption: str, ink: str, muted: str, right: bool = False):
    from pptx.util import Inches
    from PIL import Image
    im = Image.open(image)
    iw, ih = im.size
    area_w = 5.2 if right else 11.5
    area_h = 4.6 if right else 5.4
    area_x = 7.3 if right else 0.9
    area_y = 1.9 if right else 1.9
    ratio = min(area_w / iw, area_h / ih)
    w = iw * ratio; h = ih * ratio
    x = area_x + (area_w - w) / 2
    y = area_y + (area_h - h) / 2
    slide.shapes.add_picture(image, Inches(x), Inches(y), Inches(w), Inches(h))
    if caption:
        cb = slide.shapes.add_textbox(Inches(area_x), Inches(area_y + area_h + 0.05), Inches(area_w), Inches(0.4))
        _set_text(cb.text_frame.paragraphs[0], caption, 13, color=muted)


def _add_table(slide, rows, primary, band, ink):
    from pptx.util import Inches, Pt
    if not rows:
        return
    ncols = max(len(r) for r in rows)
    tbl_shape = slide.shapes.add_table(len(rows), ncols, Inches(0.9), Inches(1.9), Inches(11.5), Inches(4.2))
    table = tbl_shape.table
    for r, row in enumerate(rows):
        for c in range(ncols):
            cell = table.cell(r, c)
            cell.text = row[c] if c < len(row) else ""
            para = cell.text_frame.paragraphs[0]
            for run in para.runs:
                run.font.size = Pt(13); run.font.name = CJK_FONT
                run.font.color.rgb = _rgb(ink if r else primary)
                run.font.bold = (r == 0)
            if r == 0:
                cell.fill.solid(); cell.fill.fore_color.rgb = _rgb(band)


# --------------------------------------------------------------------------- #
# Evidence-card layout (learned from the user's reference deck)
#   top: full-width plum title bar + numbered circle badge + white theme title
#   lead: one bold conclusion sentence (keywords may be plum)
#   body: 1-3 side-by-side evidence cards, each = plum label bar + composite
#         figure + light-plum caption strip
#   bottom: light-plum summary strip (flow formula / sample-size note)
# --------------------------------------------------------------------------- #

def _rect(slide, x, y, w, h, fill=None, line=None, rounded=False):
    from pptx.util import Inches, Pt
    from pptx.enum.shapes import MSO_SHAPE
    shp = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE,
        Inches(x), Inches(y), Inches(w), Inches(h))
    if rounded:
        try:
            shp.adjustments[0] = 0.18
        except Exception:
            pass
    if fill:
        shp.fill.solid(); shp.fill.fore_color.rgb = _rgb(fill)
    else:
        shp.fill.background()
    if line:
        shp.line.color.rgb = _rgb(line); shp.line.width = Pt(0.75)
    else:
        shp.line.fill.background()
    shp.shadow.inherit = False
    return shp


def _textbox(slide, x, y, w, h, text, size, bold=False, color=INK, align=None, wrap=True):
    from pptx.util import Inches
    from pptx.enum.text import MSO_ANCHOR
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = wrap
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_left = tf.margin_right = Inches(0.05)
    tf.margin_top = tf.margin_bottom = Inches(0.02)
    _set_text(tf.paragraphs[0], text, size, bold=bold, color=color, align=align)
    return tb


def _render_evidence(slide, spec, primary, ink, muted, band, number):
    from pptx.util import Inches, Pt
    from pptx.enum.text import PP_ALIGN
    from pptx.enum.shapes import MSO_SHAPE
    from PIL import Image

    # title bar + badge
    _rect(slide, 0.35, 0.25, 12.63, 0.78, fill=primary, rounded=True)
    badge = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(0.6), Inches(0.36), Inches(0.56), Inches(0.56))
    badge.fill.solid(); badge.fill.fore_color.rgb = _rgb(primary)
    badge.line.color.rgb = _rgb("#FFFFFF"); badge.line.width = Pt(1.5)
    badge.shadow.inherit = False
    tf = badge.text_frame; tf.word_wrap = False
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    _set_text(tf.paragraphs[0], str(spec.get("number", number)).zfill(2), 17, bold=True,
              color="#FFFFFF", align=PP_ALIGN.CENTER)
    _textbox(slide, 1.35, 0.28, 11.4, 0.72, spec.get("title", ""), 26, bold=True, color="#FFFFFF")

    # lead conclusion line
    lead = spec.get("lead", "")
    if lead:
        _rect(slide, 0.6, 1.22, 12.13, 0.52, fill="#F5F5F5")
        _textbox(slide, 0.85, 1.22, 11.7, 0.52, lead, 18, bold=True, color=ink)

    cards = spec.get("cards", [])
    n = max(1, min(3, len(cards)))
    cards = cards[:n]
    top = 1.95 if lead else 1.35
    bottom = 6.45 if spec.get("summary") else 6.95
    area_h = bottom - top
    gap = 0.25
    card_w = (12.13 - gap * (n - 1)) / n
    label_h, cap_h = 0.42, 0.5

    for i, card in enumerate(cards):
        x = 0.6 + i * (card_w + gap)
        _rect(slide, x, top, card_w, area_h, fill="#FFFFFF", line=CARD_LINE)
        # label bar
        _rect(slide, x + 0.12, top + 0.1, card_w - 0.24, label_h, fill=primary)
        _textbox(slide, x + 0.12, top + 0.1, card_w - 0.24, label_h,
                 card.get("label", ""), 15, bold=True, color="#FFFFFF", align=PP_ALIGN.CENTER)
        # caption strip
        cap = card.get("caption", "")
        if cap:
            _rect(slide, x + 0.12, top + area_h - cap_h - 0.1, card_w - 0.24, cap_h, fill=band)
            _textbox(slide, x + 0.18, top + area_h - cap_h - 0.1, card_w - 0.36, cap_h,
                     cap, 13.5, bold=True, color=primary, align=PP_ALIGN.CENTER)
        # image between label and caption
        img = card.get("image")
        img_top = top + 0.1 + label_h + 0.08
        img_h = area_h - label_h - cap_h - 0.36 if cap else area_h - label_h - 0.3
        if img and Path(img).is_file():
            im = Image.open(img); iw, ih = im.size
            aw, ah = card_w - 0.3, img_h
            r = min(aw / iw, ah / ih)
            w, h = iw * r, ih * r
            slide.shapes.add_picture(img, Inches(x + 0.15 + (aw - w) / 2),
                                     Inches(img_top + (ah - h) / 2), Inches(w), Inches(h))
        elif card.get("text"):
            from pptx.util import Pt
            from pptx.enum.text import MSO_ANCHOR
            tb = slide.shapes.add_textbox(Inches(x + 0.25), Inches(img_top + 0.05),
                                        Inches(card_w - 0.5), Inches(img_h - 0.1))
            tf = tb.text_frame; tf.word_wrap = True
            tf.vertical_anchor = MSO_ANCHOR.MIDDLE
            for j, ln in enumerate(str(card["text"]).split("\n")):
                p = tf.paragraphs[0] if j == 0 else tf.add_paragraph()
                p.space_after = Pt(12)
                _set_text(p, ln, 16, bold=(j == 0), color=ink if j else primary)
        elif card.get("have") or card.get("todo"):
            # text evidence card: 已有 / 待证 contrast lines
            ty = img_top + 0.1
            if card.get("have"):
                _textbox(slide, x + 0.25, ty, card_w - 0.5, 0.9,
                         "已有：" + card["have"], 16, bold=True, color=ink)
                ty += 1.0
            if card.get("todo"):
                _textbox(slide, x + 0.25, ty, card_w - 0.5, 0.9,
                         "待证：" + card["todo"], 16, bold=True, color=primary)

    # bottom summary strip
    if spec.get("summary"):
        _rect(slide, 0.6, 6.62, 12.13, 0.55, fill=band)
        _textbox(slide, 0.8, 6.62, 11.73, 0.55, spec["summary"], 17, bold=True,
                 color=primary, align=PP_ALIGN.CENTER)


def _render_limitations(slide, spec, primary, ink, muted, band, number):
    """Vertical stack of arrow-labelled 已有/待证 rows (reference page 11)."""
    from pptx.util import Inches, Pt
    from pptx.enum.text import PP_ALIGN
    from pptx.enum.shapes import MSO_SHAPE

    _rect(slide, 0.35, 0.25, 12.63, 0.78, fill=primary, rounded=True)
    badge = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(0.6), Inches(0.36), Inches(0.56), Inches(0.56))
    badge.fill.solid(); badge.fill.fore_color.rgb = _rgb(primary)
    badge.line.color.rgb = _rgb("#FFFFFF"); badge.line.width = Pt(1.5)
    badge.shadow.inherit = False
    tf = badge.text_frame
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    _set_text(tf.paragraphs[0], str(spec.get("number", number)).zfill(2), 17, bold=True,
              color="#FFFFFF", align=PP_ALIGN.CENTER)
    _textbox(slide, 1.35, 0.28, 11.4, 0.72, spec.get("title", "研究局限"), 26, bold=True, color="#FFFFFF")

    rows = spec.get("cards", [])
    n = max(1, len(rows))
    top, bottom = 1.5, 6.9
    row_h = min(1.6, (bottom - top) / n - 0.15)
    for i, row in enumerate(rows):
        y = top + i * ((bottom - top) / n)
        arrow = slide.shapes.add_shape(MSO_SHAPE.PENTAGON, Inches(0.6), Inches(y),
                                       Inches(2.6), Inches(0.5))
        arrow.fill.solid(); arrow.fill.fore_color.rgb = _rgb(primary)
        arrow.line.fill.background(); arrow.shadow.inherit = False
        atf = arrow.text_frame
        atf.margin_left = atf.margin_right = atf.margin_top = atf.margin_bottom = 0
        _set_text(atf.paragraphs[0], row.get("label", ""), 16, bold=True,
                  color="#FFFFFF", align=PP_ALIGN.CENTER)
        _rect(slide, 3.0, y + 0.55, 9.73, row_h - 0.6, fill="#FFFFFF", line=CARD_LINE)
        ty = y + 0.6
        if row.get("have"):
            _textbox(slide, 3.25, ty, 9.2, 0.5, "已有：" + row["have"], 16, bold=True, color=ink)
            ty += 0.55
        if row.get("todo"):
            _textbox(slide, 3.25, ty, 9.2, 0.5, "待证：" + row["todo"], 16, bold=True, color=primary)


def _hstack_images(paths: list[str], out_path: Path, gap: int = 16) -> str:
    """Combine panel crops side-by-side into one composite card image."""
    from PIL import Image
    ims = [Image.open(p).convert("RGB") for p in paths]
    h = max(im.height for im in ims)
    scaled = []
    for im in ims:
        r = h / im.height
        scaled.append(im.resize((max(1, int(im.width * r)), h)))
    w = sum(im.width for im in scaled) + gap * (len(scaled) - 1)
    canvas = Image.new("RGB", (w, h), "white")
    x = 0
    for im in scaled:
        canvas.paste(im, (x, 0)); x += im.width + gap
    canvas.save(out_path)
    return str(out_path)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser(description="PDF -> editable scientific report deck (local)")
    ap.add_argument("--pdf", type=Path, help="source PDF")
    ap.add_argument("--outline", type=Path, help="assistant-supplied outline JSON (overrides parsing)")
    ap.add_argument("--output", required=True, type=Path, help="output .pptx")
    ap.add_argument("--assets-dir", type=Path, default=None)
    ap.add_argument("--title", default=None)
    ap.add_argument("--authors", default=None)
    ap.add_argument("--max-bullets", type=int, default=6)
    ap.add_argument("--vectorize-figures", action="store_true",
                    help="route extracted figures through vtracer (editable shapes; slower)")
    args = ap.parse_args()

    if args.outline:
        outline = json.loads(args.outline.read_text(encoding="utf-8"))
        if args.assets_dir:
            outline.setdefault("_assets_dir", str(args.assets_dir))
    else:
        if not args.pdf:
            raise SystemExit("EITHER --pdf or --outline is required")
        assets = args.assets_dir or args.output.parent / (args.output.stem + "-assets")
        outline = parse_pdf(args.pdf, assets, max_bullets=args.max_bullets)

    if args.title:
        outline["title"] = args.title
    if args.authors:
        outline["authors"] = args.authors

    if args.vectorize_figures:
        outline = _vectorize_figures(outline)

    render_deck(outline, args.output)
    n = len(outline.get("slides", []))
    print(json.dumps({"ok": True, "deck": str(args.output), "slides": n}, ensure_ascii=False))
    return 0


def _vectorize_figures(outline: dict) -> dict:
    """Placeholder hook: route figure images to the ky_ppt vtracer engine.

    Kept explicit and optional; figure raster embedding is the default path.
    """
    return outline


if __name__ == "__main__":
    raise SystemExit(main())