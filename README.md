# ky_ppt

**把科研图/示意图本地免费重建为可编辑的 PowerPoint 或 Adobe Illustrator 文件。**

一张 PNG/JPG/WebP 科研示意图进去，一个全是原生可编辑形状和文本框的 PPTX（或 AI 文件）出来。
全程本地运行：不联网、不需要 API Key、零费用。

内置开源绘图后端（MIT，见 LICENSE），图片识别与矢量化全部本地完成，零费用。

## 三层架构

1. **几何引擎**（`--engine`）：默认 **vtracer**（Rust，O(n)，彩色堆叠 + 样条拟合，曲线平滑、渐变保真）；
   可选 **opencv**（双阶段调色板量化 + 轮廓，纯扁平色块可用 `--merge-colors` 把形状数压到约等于颜色数）。
2. **文字/语义层**（可选）：默认 **RapidOCR**（`--ocr-manifest`，CPU 秒级）精确识别文字 + 定位，
   自动生成文字清单，免去手工记坐标；可选本地 **Ollama** 小 VLM（`qwen3-vl:2b`）做分区/标题等短语义
   （`--vlm-manifest`）。密集图逐字 OCR 推荐 RapidOCR，2B VLM 更适合短文本与分区。
3. **绘图后端**：backend 开源代码，几何 + 文字清单 → 原生可编辑 PPTX / AI。

## 效果

- 色块、箭头、连线、曲线 → 原生可编辑自由形状（vtracer 样条更平滑）
- 文字 → 按原坐标还原的可编辑文本框，代码强制置顶（RapidOCR/VLM 自动生成或手工清单）
- 渐变、密集科研流程图 → vtracer 彩色分层保真（实测 MAE 显著低于量化引擎）
- 照片类复杂图像效果有限

## 安装

### 作为 Agent Skill（Codex、Claude Code / 其他支持 SKILL.md 的 Agent）

把本仓库整个文件夹放进 Agent 的用户技能目录（例如豆包办公的
`<workspace>/.user_skills/ky_ppt`），新会话即可通过自然语言触发：
"用 ky_ppt 把这张图转成可编辑 PPT"。

### 命令行直接使用

```powershell
# Windows：安装依赖并生成运行时配置
.\setup.ps1

# 无文字图片
py -3 -X utf8 scripts\run_local.py --input-image figure.png --output-root out --application ppt

# 带文字图片（全自动）：RapidOCR 在原图上识别文字，vtracer 矢量化，一步出可编辑 PPT
py -3 -X utf8 scripts\run_local.py --input-image figure.png --ocr-manifest --output-root out --application ppt

# 带文字图片（几何更干净）：先去字得 cleaned.png，OCR 清单单独从原图生成再传入
py -3 -X utf8 scripts\ocr_manifest.py --input-image figure.png --output text.json
py -3 -X utf8 scripts\run_local.py --input-image cleaned.png --text-manifest text.json --output-root out --application ppt
```

产物在 `out\shibielujingN\shibielujingN.pptx`。macOS/Linux 可生成 PPTX 文件
（Illustrator 自动绘制仅 Windows/macOS）。

### 验证安装

```powershell
py -3 -X utf8 examples\make_test_figure.py   # 生成测试图与文字清单
py -3 -X utf8 scripts\run_local.py --input-image test\cleaned.png --text-manifest test\text-manifest.json --output-root test\out --application ppt
```

## PDF → 科研汇报 PPT

独立分支：把一篇 PDF 整理成白底学术蓝汇报 PPT，不经过单图矢量化管线。

```powershell
# 离线兜底：PDF 直接出稿（按章节摘句）
py -3 -X utf8 scripts\pdf_to_deck.py --pdf paper.pdf --output out\deck.pptx

# 助手提纲驱动：读完 PDF 写好 outline.json，再出稿（要点质量更好）
py -3 -X utf8 scripts\pdf_to_deck.py --outline outline.json --output out\deck.pptx
```

- **默认深紫文献解读风**（从参考稿学习固化，见 [references/deck-style-guide.md](references/deck-style-guide.md)）：编号徽章+结论式标题、黑体导语、**1–3 张并排证据卡**（紫标签+整幅组合图+浅紫结论条）、页底总结条；局限页为箭头标签+"已有/待证"对仗行。
- 多子图（A/B/C/D）由 `split_panels.py` 按白边投影拆分后**分组进卡（严禁一图一页）**；组合图位图嵌入、等比不裁剪。
- 提纲 schema：`{"title","authors","color":{...},"slides":[{"type","title",...}]}`，页型含 cover/toc/section/content/**evidence**/**limitations**/result/figure/table/conclusion；evidence 页 `cards:[{label,image,caption}]`+`lead`+`summary`，limitations 页 `cards:[{label,have,todo}]`。
- 长文精炼建议由助手填写 `--outline`（按语义分组 panel、提炼卡标签与结论）；纯离线摘句忠实但偏干。面板拆分依赖面板间有白边分隔。

## 带文字图片的标准流程

1. 生成文字清单：推荐 `--ocr-manifest`（RapidOCR 自动）；或手工记录每段文字内容与像素坐标（y 为基线）
   → `text-manifest.json`（schema 见 [references/text-workflow.md](references/text-workflow.md)）
2. 用任意图像编辑模型只去掉文字，得到 cleaned 图，并对照原图检查图形未变
3. 运行 `run_local.py`（**用去字输出的原始分辨率，不要先缩小**；
   manifest 的 `source_canvas` 填原图尺寸，坐标自动按比例映射）

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--application` | ppt | `ppt` 或 `ai`（AI 需本机已打开 Illustrator 目标文档） |
| `--engine` | vtracer | 几何引擎：`vtracer`（平滑曲线）或 `opencv`（扁平量化） |
| `--ocr-manifest` | 关 | RapidOCR 自动文字清单（CPU 秒级，推荐） |
| `--vlm-manifest` | 关 | 本地 Ollama VLM 自动文字清单（短语义/分区，需 Ollama） |
| `--preset` | 无 | `auto`/`flat`/`dense` 自动选参数；显式参数覆盖预设（opencv 引擎） |
| `--max-colors` | 24 | 调色板颜色数（2-256）；复杂图/渐变背景用 96-128 |
| `--min-area` | 6 | 碎斑过滤阈值 px²；细线断线时调小到 0.5-2 |
| `--epsilon` | 0.6 | 轮廓简化容差 px |
| `--merge-colors` | 关 | 同色合并为单条复合路径，PPT 形状数降到约等于颜色数 |
| `--no-smooth` | 关 | 纯折线输出（不做贝塞尔拟合） |

小图（最长边 <1600px）自动 2x 超采样再矢量化，避免 1px 笔画丢失。

## 质量门

交付前渲染回比源图，量化保真度：

```powershell
py -3 -X utf8 scripts\check_quality.py --svg out.svg --source original.png
```

输出 `mae`（平均绝对误差）与 `ink_recall`（暗墨召回，检测文字/细线丢失）。
`ok:true` 才交付。渲染器优先 resvg-js（`setup.ps1` 自动安装），回退 svglib。

## 安全设计

- 零网络调用、零付费接口、无凭据存储
- 输入仅限 PNG/JPEG/WebP，≤40MP（防解压炸弹）
- 含 `<!ENTITY` 声明的 SVG 一律拒绝（防实体膨胀）
- 子进程全部参数列表传参，无 shell 拼接

## 目录结构

```
ky_ppt/
├── SKILL.md          # Agent 触发与流程说明
├── README.md
├── LICENSE
├── setup.ps1         # Windows 依赖安装 + 运行时配置
├── scripts/          # 矢量化引擎 + backend 绘图后端
├── references/       # 文字清单 schema、平台矩阵、诊断文档
└── examples/         # 一键生成测试图
```

## 许可

MIT。第三方代码的法定版权声明见 [LICENSE](LICENSE)。
