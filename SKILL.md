---
name: ky-ppt
description: 科研图/示意图本地免费重建为可编辑 PowerPoint(PPTX) 或 Adobe Illustrator(AI) 文件；另支持 PDF→科研汇报 PPT（深紫文献解读风：编号徽章、结论式标题、并排证据卡、已有/待证局限页、页底总结条；多子图 A/B/C/D 自动拆分分组进卡）。vtracer 平滑矢量化（默认）/ OpenCV 量化双引擎 + RapidOCR 自动文字清单（可选小 VLM 分区语义）+ PyMuPDF 解析 + 内置开源绘图后端，无需 API Key、零费用。当用户要求"把这张图转成可编辑PPT/AI"、"科研图矢量化"、"PDF 转汇报 PPT"、"文献解读 PPT"、"图片转原生形状"、"用 ky_ppt/ky-ppt 处理这张图"时使用。扁平色块与密集科研流程图均适用，照片类复杂图像效果有限。
---

# ky_ppt

图片 → 可编辑 PPTX / AI，全流程本地运行、零费用。三层架构：

1. **几何引擎**（`--engine`）：默认 **vtracer**（`vectorize_vtracer.py`，Rust O(n) 彩色堆叠+样条拟合，曲线平滑、渐变保真）；
   可选 **opencv**（`vectorize_local.py`，双阶段调色板量化+轮廓，适合纯扁平色块、可用 `--merge-colors` 极简形状）。
2. **文字/语义层**（可选）：默认 **RapidOCR**（`ocr_manifest.py`，`--ocr-manifest`，CPU 秒级）
   做精确 OCR+文字定位，自动生成 text-manifest，免去手工记坐标；
   可选本地 Ollama 小 VLM（`vlm_semantics.py`，默认 qwen3-vl:2b）做分区/标题等**短语义**任务
   （`--mode regions`）。注意：2B 级 VLM 对密集图的逐字 bbox OCR 不稳定，精确文字清单优先用 RapidOCR。
3. **绘图后端**：内置绘图后端（MIT，见 LICENSE），几何+文字清单 → 原生可编辑 PPTX/AI。

## 标准流程（带文字的科研图）

1. **文字清单**（三选一）：
   - **自动 OCR（推荐）**：第 3 步加 `--ocr-manifest`，由 RapidOCR 在**含文字的原图**上精确识别
     文字+bbox，自动生成 `text-manifest.json`（CPU 秒级，自动估算字号/颜色）。
     若几何用去字图，则先单独跑 `ocr_manifest.py --input-image 原图.png --output text-manifest.json`
     再用 `--text-manifest` 传入，避免文字被重复 baked。
   - **自动 VLM（短语义）**：本地 Ollama 已启动并 `ollama pull qwen3-vl:2b` 后加 `--vlm-manifest`
     （CPU 分钟级，适合标题/分区等短文本；密集逐字 OCR 不如 RapidOCR 稳）。
   - **手工**：读取 [references/text-workflow.md](references/text-workflow.md)，
     把每段文字的内容、像素坐标（y 为基线）、字号、颜色记入 `text-manifest.json`
     （schema_version "1.0"，source_canvas 填**原图**宽高，coordinate_space "pixels"）。
2. **去字**：用可用的图像编辑模型只清除文字（含水印），得到 cleaned 图。
   对照原图检查图形、布局、配色、连线、画布未变；变了就重新清理。
3. **本地重建**（零费用，无需付费确认）：

```powershell
py -3 -X utf8 <skill>/scripts/run_local.py --input-image cleaned.png --text-manifest text-manifest.json --output-root <输出目录> --application ppt
```

   - 默认 `--engine vtracer`（曲线/彩色更好）；纯扁平色块想要极简形状数可 `--engine opencv --merge-colors`。
   - 无手工清单时加 `--ocr-manifest`（推荐，RapidOCR）或 `--vlm-manifest`（Ollama）自动生成。

   - **用去字模型输出的原始分辨率**，不要先缩小（细线和小细节会糊）。
     分辨率与原图不同时，manifest 的 source_canvas 仍填原图尺寸，坐标按比例自动映射。
   - 文字层序由代码强制置顶，manifest 的 paint_order 无需人工保证。
   - 产物：`<输出目录>\shibielujingN\shibielujingN.pptx`（原生可编辑形状+文本框），
     同目录保留 master SVG、几何缓存和 text-manifest.json。
4. **验证**：python-pptx 统计 freeform/文本框；视觉核对用 PowerPoint COM 导出 PNG
   与原图对比。画布发白先读
   [references/white-overlay-diagnostics.md](references/white-overlay-diagnostics.md)。

无文字图片：跳过 1-2 直接第 3 步。已有认可 SVG：`scripts/run_from_svg.py --input-svg ...`。

## PDF → 科研汇报 PPT（pdf_to_deck 分支）

把一篇 PDF 整理成一套白底学术蓝汇报 PPT，与上面的"单图→可编辑形状"管线相互独立。

- **入口** `scripts/pdf_to_deck.py`：PyMuPDF 提取标题/章节/段落/嵌入图/表 → 提纲 → python-pptx 学术模板出稿
  （封面 / 目录 / 章节页 / 要点页 / 结果页 / 小结）。
- **多子图拆分** `scripts/split_panels.py`：白边投影按行列把 A/B/C/D 组合图切成独立面板；
  RapidOCR 仅辅助识别面板字母，认不出时按行列顺序编号。面板之间需有白边/留白分隔。
  **拆分仅用于分组进卡，严禁一图一页。**
- **默认风格（从用户参考稿学习固化）**：深紫文献解读风（主色 `#7D2E68`、浅紫 `#F3E8F2`、白底），
  详见 [references/deck-style-guide.md](references/deck-style-guide.md)。主力页型：
  - **evidence 证据页**：深紫通栏标题栏+白圈编号徽章+白色主题词 → 黑体加粗导语（关键词紫色强调）→
    **1–3 张并排证据卡**（紫底白字关键词标签 + 整幅多子图组合图 + 浅紫结论条）→ 页底浅紫总结条
    （流程式"装载→迁移→定位→治疗"或样本量口径）。
  - **limitations 局限页**：箭头形紫标签 + "已有：…（黑）/ 待证：…（紫）"对仗行。
  - 标题必须结论式带数字（"梗死体积降低26.7%"），保留"未量化/仍待"等限定词。
- **图文规则**：卡内组合图位图嵌入、等比缩放不裁剪；`--vectorize-figures` 为预留钩子，
  后续可把面板走 vtracer 转可编辑形状。
- **提纲来源**：
  - `--outline outline.json`：助手读完 PDF 后填写精炼提纲（标题/要点/图注/讲稿），质量最好；
  - 不传 `--outline`：离线按章节摘句兜底，忠实但偏干。

```powershell
# 离线兜底：PDF 直接出稿
py -3 -X utf8 scripts\pdf_to_deck.py --pdf paper.pdf --output out\deck.pptx

# 助手提纲驱动：先写 outline.json，再出稿
py -3 -X utf8 scripts\pdf_to_deck.py --outline outline.json --output out\deck.pptx
```

outline schema（v1.0）：`{"title","authors","color":{"primary",...},"slides":[{"type":"cover|toc|section|content|evidence|limitations|result|figure|table|conclusion","title","bullets","image","image_caption","table","notes"}]}`。
evidence 页：`{"type":"evidence","number","title","lead","cards":[{"label","image","caption"}],"summary"}`；
limitations 页：`{"type":"limitations","number","title","cards":[{"label","have","todo"}]}`。
助手写 outline 时按**语义**把同一论证的 panel 分进同一卡，并为每卡提炼关键词标签与结论短句。

限制：长论文的要点精炼依赖助手/LLM，纯离线摘句不做润色；复杂彩图、无白边分隔的密排面板拆分效果有限。

## Illustrator 分支

`--application ai`。需本机安装 Illustrator 并打开目标文档（Windows COM / macOS AppleScript），
详见 [references/illustrator-workflow.md](references/illustrator-workflow.md)。
未安装时可用 `scripts/run_illustrator.py --dry-run` 验证几何，或告知用户直接用
Illustrator 打开 master SVG 另存为 .ai。

## 质量调节

- **`--preset auto|flat|dense`**：自动选参数。`auto` 按细节密度/颜色数判定；
  扁平色块示意图用 `flat`（24色），密集科研流程图/含位图小图用 `dense`（128色、min-area 0.5、epsilon 0.3）。
  显式 `--max-colors/--min-area/--epsilon/--fit-tol` 覆盖预设。
- `--max-colors`（默认 24，上限 256）：复杂图/渐变背景用 96-128
- `--min-area`（默认 6 px²）：细线断线时调小到 0.5-2
- `--epsilon`（默认 0.6 px）：调小更精细
- `--no-smooth`：纯折线输出
- **`--merge-colors`**：同色区域合并为单条复合路径，PPT 形状数从数万降到约等于颜色数（~130），
  打开/编辑更流畅；代价是按颜色而非按单个元素编辑。密集图交付 PPT 时推荐。
- 小图（最长边 <1600px）引擎自动 2x 超采样再矢量化、坐标缩回原画布，避免 1px 笔画丢失。

## 质量门（交付前必跑）

```powershell
py -3 -X utf8 <skill>/scripts/check_quality.py --svg out.svg --source original.png
```

渲染回比源图，输出 `mae` 与 `ink_recall`（暗墨召回，文字/细线是否丢失）。
`ok:true` 才交付；`ink_recall` 低说明文字丢了，按 suggestions 调参重跑。
渲染器优先 resvg-js（`npm install --prefix <skill> @resvg/resvg-js`，setup.ps1 自动装），
回退 svglib（对复杂路径会低估保真度，其 WARN 仅供参考）。

## 约束与安全

- SVG 契约：纯矢量、稳定 id、viewBox=画布、复合路径保留镂空（nonzero）、从底到前景；
  禁止渐变/CSS/栅格/use。`validate_vector_svg.py` 自动校验。
- 输入防护：仅 PNG/JPEG/WebP，≤40MP；含 `<!ENTITY` 的 SVG 一律拒绝（实体膨胀防护）。
- 本技能不含任何付费接口与网络调用；不要索取 API Key。
- 依赖：Python 3.11+，**vtracer**、**rapidocr-onnxruntime**（自动文字清单）、**pymupdf**（PDF 解析）、opencv-python、numpy、Pillow、
  python-pptx、fonttools、shapely、pypinyin、pywin32（Windows）。安装：运行仓库根目录 `setup.ps1`（Windows）。
- 可选语义层：本地 **Ollama** + `ollama pull qwen3-vl:2b`（CPU 可跑，约 2GB）；仅 `--vlm-manifest`/分区时需要。
- Windows PowerPoint 演示回放（可选）：`scripts/run_ppt_path_playback.ps1`，见
  [references/powerpoint-workflow.md](references/powerpoint-workflow.md)。