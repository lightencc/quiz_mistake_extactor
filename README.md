# quiz_mistake_extractor

把孩子数学错题照片整理成 Markdown（多图手动标注 + 题目级识别编辑版）。

## 功能

- 支持多图上传，手动切换轮播图
- 手动绘制题目框/图形框，支持拖拽移动与缩放
- 列表和画布都支持删除框
- 每道题在右侧都有：
  - 裁剪预览图
  - 自动 OCR 结果（Gemini，画完题目框后自动触发）
  - 可编辑富文本输入区
  - 数学表达式预览（支持 `$...$` / `$$...$$`）
- 导出时每道题生成一个独立 Markdown（固定复盘模板）
- 导出阶段调用 Google GenAI（`gemini-3-flash-preview`）自动填充：题干、错误答案、正确答案、解题思路
- 导出前自动上传题目裁剪图到 GitHub 仓库，Markdown 使用 `raw.githubusercontent.com` 外链
- 导出后自动把每道题 Markdown 上传到 Notion 数据库（Martian 转 blocks + notion-client 写入）

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install
```

## 配置 `.env`（推荐）

```env
# Google GenAI（导出阶段）
GOOGLE_API_KEY=你的GoogleAIKey
GEMINI_MODEL=gemini-3-flash-preview
GEMINI_OCR_MODEL=gemini-3-flash-preview
# 可选：使用代理或兼容网关时配置
GEMINI_BASE_URL=
GEMINI_API_VERSION=
GEMINI_REQUEST_TIMEOUT_SECONDS=90
# 兼容旧模型名变量（仅模型名可回退）
MODEL=

# GitHub 图床（导出前上传裁剪图）
GITHUB_TOKEN=ghp_xxx
GITHUB_REPO=lightencc/quiz_content
GITHUB_BRANCH=main
GITHUB_IMAGE_DIR=images
GITHUB_RAW_BASE=https://raw.githubusercontent.com
# 上传前压缩（只上传压缩图）
UPLOAD_COMPRESS_MAX_SIDE=1800
UPLOAD_COMPRESS_JPEG_QUALITY=82

# Notion（导出完成后自动上传）
NOTION_API_KEY=secret_xxx
NOTION_DATABASE_ID=你的数据库ID
# 可选：新版 Notion 建议直接配置数据源 ID（优先级更高）
NOTION_DATA_SOURCE_ID=
# 可选：标题属性名，默认自动识别
NOTION_TITLE_PROPERTY=
# 可选：ID 字段名，默认 ID；找不到时自动回退 unique_id
NOTION_ID_PROPERTY=ID
# 可选：标题前缀；最终标题为 YYYY-MMDD-<prefix>-<ID值>
NOTION_TITLE_PREFIX=
```

说明：

- `GEMINI_API_KEY` 未配置时，会回退 `GOOGLE_API_KEY`。
- `GEMINI_BASE_URL` 只读取本字段；不会再回退 `BASE_URL`（避免 `/v1/v1beta` 路径冲突）。
- 题目框 OCR 也使用 Gemini（`GEMINI_OCR_MODEL`），便于统一对比速度与识别效果。
- 导出时会将每道题裁剪图/图形图上传到 GitHub 仓库，图片链接会写成 `https://raw.githubusercontent.com/<repo>/refs/heads/<branch>/<path>`。
  例如：`https://github.com/lightencc/quiz_content/blob/main/images/img1_q1_question.png` 对应 `https://raw.githubusercontent.com/lightencc/quiz_content/refs/heads/main/images/img1_q1_question.png`。
- Notion 上传流程：`创建页面 -> 读取 ID 字段 -> 更新标题(YYYY-MMDD-ID) -> 写入 Martian 转换后的 blocks`。
- 若你的库是新版 `data source` 结构，推荐配置 `NOTION_DATA_SOURCE_ID`。

## 启动

```bash
python web_app.py
```

浏览器打开：

```text
http://127.0.0.1:7860
```

## Web 使用流程

1. 上传一批图片
2. 画题目框（会自动识别并回填题目文本）
3. 必要时画图形框
4. 在右侧编辑题目文本，检查公式预览
5. 如识别不理想，删除该框并重新框选触发识别
6. 直接导出 Markdown（AI 自动写入模板内容）
7. 导出完成后自动上传 Notion，界面显示按题进度与完成结果

## 导出结果

- `web_data/exports/<session_id>/mistakes.md`
- `web_data/exports/<session_id>/q1.md`, `q2.md`, ...
- `web_data/exports/<session_id>/img*_q*_question.png`
- `web_data/exports/<session_id>/img*_q*_fig*.png`

## 常见问题

- OCR 报鉴权失败：检查 `GOOGLE_API_KEY / GEMINI_API_KEY` 是否有效，且为 Google AI Studio API Key。
- 识别文本不理想：可直接在每题编辑区手动修正，或删框后重画触发重识别。
- 导出报 GitHub 配置错误：检查 `GITHUB_TOKEN/GITHUB_REPO/GITHUB_BRANCH`。
