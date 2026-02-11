const uploadDropzone = document.getElementById("upload-dropzone");
const imageInput = document.getElementById("image-input");
const statusEl = document.getElementById("status");
const exportStatusEl = document.getElementById("export-status");
const exportLinksEl = document.getElementById("export-links");
const notionProgressWrapEl = document.getElementById("notion-progress-wrap");
const notionProgressBarEl = document.getElementById("notion-progress-bar");
const notionProgressTextEl = document.getElementById("notion-progress-text");
const questionListEl = document.getElementById("question-list");
const promptTemplateEl = document.getElementById("prompt-template");
const aiHealthCheckBtn = document.getElementById("ai-health-check-btn");
const aiHealthDotEl = document.getElementById("ai-health-dot");
const aiHealthTextEl = document.getElementById("ai-health-text");
const aiHealthLatencyEl = document.getElementById("ai-health-latency");

const canvas = document.getElementById("canvas");
const ctx = canvas.getContext("2d");

const toolSelectBtn = document.getElementById("tool-select-btn");
const drawQuestionBtn = document.getElementById("draw-question-btn");
const drawFigureBtn = document.getElementById("draw-figure-btn");

const prevImageBtn = document.getElementById("prev-image-btn");
const nextImageBtn = document.getElementById("next-image-btn");
const imageIndicatorEl = document.getElementById("image-indicator");
const imageTabsEl = document.getElementById("image-tabs");

const resetPromptBtn = document.getElementById("reset-prompt-btn");
const exportBtn = document.getElementById("export-btn");

const HANDLE_SIZE = 10;
const DELETE_SIZE = 20;
const MIN_BOX_SIZE = 0.01;

const state = {
  sessionId: "",
  images: [],
  currentIndex: 0,
  selected: null,
  tool: "select", // select | draw_question | draw_figure
  interaction: {
    mode: "idle", // idle | move | resize | draw
    handle: null,
    startPoint: null,
    startRect: null,
    previewRect: null,
  },
  hover: {
    kind: "none", // none | delete | resize | move | box
    handle: null,
    selection: null,
  },
  ui: {
    deleteIcon: null,
  },
  defaultPromptTemplate: "",
  notionEnabled: false,
  notionTaskId: "",
  aiHealthTimerId: null,
};

function setStatus(text, isError = false) {
  statusEl.textContent = text || "";
  statusEl.style.color = isError ? "#c53030" : "#276749";
}

function setExportStatus(text, isError = false) {
  exportStatusEl.textContent = text || "";
  exportStatusEl.style.color = isError ? "#c53030" : "#276749";
}

function resetNotionProgress() {
  state.notionTaskId = "";
  if (notionProgressWrapEl) notionProgressWrapEl.hidden = true;
  if (notionProgressBarEl) notionProgressBarEl.style.width = "0%";
  if (notionProgressTextEl) notionProgressTextEl.textContent = "";
}

function setNotionProgress(percent, text = "") {
  if (!notionProgressWrapEl || !notionProgressBarEl || !notionProgressTextEl) return;
  notionProgressWrapEl.hidden = false;
  const safePercent = Number.isFinite(percent) ? Math.max(0, Math.min(100, percent)) : 0;
  notionProgressBarEl.style.width = `${safePercent.toFixed(1)}%`;
  notionProgressTextEl.textContent = text || `上传进度 ${safePercent.toFixed(1)}%`;
}

function setAiHealthState(kind, text, latencyMs = null, detail = "") {
  if (!aiHealthDotEl || !aiHealthTextEl || !aiHealthLatencyEl || !aiHealthCheckBtn) return;

  aiHealthDotEl.classList.remove("ai-health-loading", "ai-health-ok", "ai-health-error");
  if (kind === "ok") aiHealthDotEl.classList.add("ai-health-ok");
  else if (kind === "error") aiHealthDotEl.classList.add("ai-health-error");
  else aiHealthDotEl.classList.add("ai-health-loading");

  aiHealthTextEl.textContent = text || "AI 状态未知";
  aiHealthLatencyEl.textContent = Number.isFinite(latencyMs) ? `${Math.round(latencyMs)}ms` : "";
  aiHealthCheckBtn.title = detail ? `AI 状态：${detail}` : "点击手动检测 AI 接口连通性";
}

async function checkAiHealth(options = {}) {
  const { silent = false } = options;
  if (!aiHealthCheckBtn) return;

  if (!silent) setAiHealthState("loading", "AI 检测中...");
  aiHealthCheckBtn.disabled = true;

  try {
    const res = await fetch("/api/ai-health", { method: "GET" });
    const data = await res.json();
    const latency = Number(data.latency_ms);
    if (!res.ok || !data.ok) {
      const detail = String(data.error || "未知错误");
      setAiHealthState("error", "AI 接口异常", latency, detail);
      return;
    }
    setAiHealthState("ok", "AI 接口正常", latency, `${data.base_url || ""} / ${data.model || ""}`.trim());
  } catch (error) {
    setAiHealthState("error", "AI 检测失败", null, String(error.message || error));
  } finally {
    aiHealthCheckBtn.disabled = false;
  }
}

function initAiHealthChecker() {
  if (!aiHealthCheckBtn) return;
  aiHealthCheckBtn.addEventListener("click", () => checkAiHealth({ silent: false }));
  checkAiHealth({ silent: false });
  if (state.aiHealthTimerId) window.clearInterval(state.aiHealthTimerId);
  state.aiHealthTimerId = window.setInterval(() => {
    checkAiHealth({ silent: true });
  }, 120000);
}

function clamp01(v) {
  return Math.max(0, Math.min(1, v));
}

function clampRange(v, min, max) {
  return Math.max(min, Math.min(max, v));
}

function sanitizeRect(rect) {
  if (!Array.isArray(rect) || rect.length !== 4) return [0, 0, 0, 0];
  let [x1, y1, x2, y2] = rect.map((v) => clamp01(Number(v)));
  if (x2 < x1) [x1, x2] = [x2, x1];
  if (y2 < y1) [y1, y2] = [y2, y1];
  return [x1, y1, x2, y2];
}

function makeSelection(type, qi, fi = null) {
  return type === "figure" ? { type, qi, fi } : { type, qi };
}

function isSameSelection(a, b) {
  if (!a || !b) return false;
  if (a.type !== b.type) return false;
  if (a.qi !== b.qi) return false;
  if (a.type === "figure") return a.fi === b.fi;
  return true;
}

function getCurrentSlide() {
  return state.images[state.currentIndex] || null;
}

function getCurrentQuestions() {
  const slide = getCurrentSlide();
  return slide ? slide.questions : [];
}

function getSelectedQuestionIndex() {
  if (!state.selected) return -1;
  return state.selected.qi;
}

function getRectBySelection(sel) {
  if (!sel) return null;
  const questions = getCurrentQuestions();
  const q = questions[sel.qi];
  if (!q) return null;
  if (sel.type === "question") return q.question_bbox;
  if (sel.type === "figure") return q.figure_bboxes[sel.fi] || null;
  return null;
}

function setRectBySelection(sel, rect) {
  if (!sel) return;
  const questions = getCurrentQuestions();
  const q = questions[sel.qi];
  if (!q) return;
  const safe = sanitizeRect(rect);
  if (sel.type === "question") {
    q.question_bbox = safe;
    return;
  }
  if (sel.type === "figure" && typeof sel.fi === "number" && q.figure_bboxes[sel.fi]) {
    q.figure_bboxes[sel.fi] = safe;
  }
}

function createQuestion(rect) {
  return {
    question_no: "",
    question_bbox: sanitizeRect(rect),
    figure_bboxes: [],
    has_figure: false,
    ocr_text: "",
    ocr_loading: false,
    ocr_error: "",
    ocr_preview: "",
    ocr_elapsed_ms: 0,
    ocr_model: "",
    ocr_req_id: 0,
  };
}

function clearCanvasState() {
  state.selected = null;
  state.interaction = { mode: "idle", handle: null, startPoint: null, startRect: null, previewRect: null };
  state.hover = { kind: "none", handle: null, selection: null };
  state.ui.deleteIcon = null;
}

function resetWorkspace() {
  state.images = [];
  state.currentIndex = 0;
  state.notionEnabled = false;
  state.notionTaskId = "";
  clearCanvasState();
  imageTabsEl.innerHTML = "";
  questionListEl.innerHTML = "";
  imageIndicatorEl.textContent = "未上传图片";
  exportLinksEl.innerHTML = "";
  resetNotionProgress();
  canvas.width = 1;
  canvas.height = 1;
  draw();
}

async function uploadImages(filesInput) {
  exportLinksEl.innerHTML = "";
  resetNotionProgress();
  setExportStatus("");

  const files = Array.from(filesInput || []);
  if (!files.length) {
    setStatus("请至少选择一张图片。", true);
    return;
  }

  const formData = new FormData();
  files.forEach((f) => formData.append("images", f));

  setStatus("正在上传图片，请稍候...");
  resetWorkspace();

  try {
    const res = await fetch("/api/upload", { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || "上传失败");

    state.sessionId = data.session_id;
    state.images = (data.images || []).map((img) => ({ ...img, questions: [], imageObj: null }));
    state.defaultPromptTemplate = data.default_prompt_template || "";
    state.notionEnabled = Boolean(data.notion_enabled);
    promptTemplateEl.value = state.defaultPromptTemplate;

    await switchToImage(0);
    setTool("draw_question");

    setStatus(`上传完成：${state.images.length} 张图片。请先绘制题目框。`);
  } catch (error) {
    setStatus(String(error.message || error), true);
  } finally {
    if (imageInput) imageInput.value = "";
  }
}

function markdownNameFromUrl(url) {
  const raw = String(url || "");
  if (!raw) return "";
  const parts = raw.split("/");
  const last = parts[parts.length - 1] || "";
  return last.split("?")[0];
}

async function runExportTask(payload, fallbackTotal = 0) {
  setNotionProgress(1, "导出任务准备中...");
  setExportStatus("正在创建导出任务...", false);

  const createRes = await fetch("/api/export/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const createData = await createRes.json();
  if (!createRes.ok || !createData.ok) {
    throw new Error(createData.error || "创建导出任务失败");
  }
  const taskId = String(createData.task_id || "");
  if (!taskId) {
    throw new Error("创建导出任务失败：缺少 task_id");
  }

  const sleep = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));
  let latestTask = createData.task || {};

  while (true) {
    const statusRes = await fetch(`/api/export/tasks/${encodeURIComponent(taskId)}`, { method: "GET" });
    const statusData = await statusRes.json();
    if (!statusRes.ok || !statusData.ok) {
      throw new Error(statusData.error || "获取导出任务状态失败");
    }
    const task = statusData.task || {};
    latestTask = task;

    const percent = Number(task.progress_percent || 0);
    const total = Number(task.question_total || fallbackTotal || 0);
    const done = Number(task.question_done || 0);
    const current = String(task.current || "");
    const lastAi = Number(task.last_ai_elapsed_sec || 0);
    const aiTotal = Number(task.ai_elapsed_total_sec || 0);
    const avgAi = done > 0 ? aiTotal / done : 0;
    const aiInfo = done > 0
      ? `，AI ${done}/${total || done}（最近 ${lastAi.toFixed(1)}s，累计 ${aiTotal.toFixed(1)}s，均值 ${avgAi.toFixed(1)}s）`
      : "";
    const progressText = current
      ? `导出进度 ${percent.toFixed(1)}%${aiInfo}，当前：${current}`
      : `导出进度 ${percent.toFixed(1)}%${aiInfo}`;
    setNotionProgress(percent, progressText);
    setExportStatus(current || "正在导出 Markdown...", false);

    const status = String(task.status || "");
    if (status === "completed" || status === "completed_with_errors") {
      return task.result || {};
    }
    if (status === "failed") {
      throw new Error(String(task.error || "导出任务失败"));
    }
    await sleep(700);
  }
}

async function uploadMarkdownsToNotion(markdownItems) {
  const payloadItems = [];
  const localErrors = [];
  markdownItems.forEach((item, idx) => {
    const title = String(item.title || `错题 ${idx + 1}`);
    const mdName = markdownNameFromUrl(item.url);
    if (!mdName) {
      localErrors.push(`${title}：缺少 markdown 文件名`);
      return;
    }
    payloadItems.push({ title, markdown_name: mdName });
  });

  if (payloadItems.length === 0) {
    return { notionLinks: [], notionErrors: localErrors };
  }

  setNotionProgress(0, `Notion 上传准备中（0/${payloadItems.length}）`);
  setExportStatus("已提交 Notion 后台任务，正在上传...", false);

  const createRes = await fetch("/api/notion-upload/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: state.sessionId,
      items: payloadItems,
    }),
  });
  const createData = await createRes.json();
  if (!createRes.ok || !createData.ok) {
    throw new Error(createData.error || "创建 Notion 上传任务失败");
  }
  const taskId = String(createData.task_id || "");
  if (!taskId) {
    throw new Error("创建 Notion 上传任务失败：缺少 task_id");
  }
  state.notionTaskId = taskId;

  const invalidItems = Array.isArray(createData.invalid_items) ? createData.invalid_items : [];
  if (invalidItems.length > 0) {
    invalidItems.forEach((msg) => localErrors.push(String(msg)));
  }

  const sleep = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

  let latestTask = null;
  while (true) {
    const statusRes = await fetch(`/api/notion-upload/tasks/${encodeURIComponent(taskId)}`, { method: "GET" });
    const statusData = await statusRes.json();
    if (!statusRes.ok || !statusData.ok) {
      throw new Error(statusData.error || "获取 Notion 上传任务状态失败");
    }
    const task = statusData.task || {};
    latestTask = task;

    const total = Number(task.total || payloadItems.length || 0);
    const completed = Number(task.completed || 0);
    const percent = Number(task.progress_percent || 0);
    const current = String(task.current || "");
    const detail = current ? `，当前：${current}` : "";
    setNotionProgress(percent, `Notion 上传进度 ${completed}/${total}${detail}`);
    setExportStatus(`Notion 后台上传中（${completed}/${total}）${detail}`, false);

    const status = String(task.status || "");
    if (status === "completed" || status === "completed_with_errors" || status === "failed") {
      break;
    }
    await sleep(900);
  }

  const notionLinks = [];
  const notionErrors = [...localErrors];
  const taskItems = Array.isArray(latestTask?.items) ? latestTask.items : [];
  taskItems.forEach((row) => {
    const status = String(row.status || "");
    const title = String(row.final_title || row.title || row.markdown_name || "错题");
    if (status === "success") {
      notionLinks.push({
        title,
        url: String(row.page_url || ""),
        markdownName: String(row.markdown_name || ""),
        steps: Array.isArray(row.steps) ? row.steps : [],
      });
    } else if (status === "failed") {
      notionErrors.push(`${title}：${String(row.error || "上传 Notion 失败")}`);
    }
  });
  if (String(latestTask?.status || "") === "failed" && notionErrors.length === localErrors.length) {
    const detail = String(latestTask?.error || "Notion 上传任务执行失败");
    notionErrors.push(detail);
  }

  return { notionLinks, notionErrors };
}

function resizeCanvas() {
  const slide = getCurrentSlide();
  if (!slide || !slide.imageObj) {
    canvas.width = 1;
    canvas.height = 1;
    return;
  }

  const parentWidth = canvas.parentElement.clientWidth;
  const maxWidth = Math.max(320, parentWidth - 2);
  const scale = Math.min(maxWidth / slide.imageObj.width, 1);
  canvas.width = Math.round(slide.imageObj.width * scale);
  canvas.height = Math.round(slide.imageObj.height * scale);
}

function rectToPxObject(rect) {
  const [x1, y1, x2, y2] = sanitizeRect(rect);
  return {
    x: x1 * canvas.width,
    y: y1 * canvas.height,
    w: (x2 - x1) * canvas.width,
    h: (y2 - y1) * canvas.height,
  };
}

function pointInRect(point, rect) {
  return point.x >= rect.x && point.x <= rect.x + rect.w && point.y >= rect.y && point.y <= rect.y + rect.h;
}

function getHandleCenters(rectPx) {
  const { x, y, w, h } = rectPx;
  return {
    nw: { x, y },
    n: { x: x + w / 2, y },
    ne: { x: x + w, y },
    e: { x: x + w, y: y + h / 2 },
    se: { x: x + w, y: y + h },
    s: { x: x + w / 2, y: y + h },
    sw: { x, y: y + h },
    w: { x, y: y + h / 2 },
  };
}

function getHandleHit(point, rectPx) {
  const centers = getHandleCenters(rectPx);
  const half = HANDLE_SIZE / 2;
  const order = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
  for (const key of order) {
    const c = centers[key];
    const hitRect = { x: c.x - half, y: c.y - half, w: HANDLE_SIZE, h: HANDLE_SIZE };
    if (pointInRect(point, hitRect)) return key;
  }
  return null;
}

function cursorForHandle(handle) {
  if (handle === "nw" || handle === "se") return "nwse-resize";
  if (handle === "ne" || handle === "sw") return "nesw-resize";
  if (handle === "n" || handle === "s") return "ns-resize";
  if (handle === "e" || handle === "w") return "ew-resize";
  return "default";
}

function drawRect(rect, color, lineWidth = 2, dashed = false) {
  const p = rectToPxObject(rect);
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  if (dashed) ctx.setLineDash([8, 6]);
  ctx.strokeRect(p.x, p.y, p.w, p.h);
  ctx.restore();
}

function drawHandles(rectPx) {
  const centers = getHandleCenters(rectPx);
  const half = HANDLE_SIZE / 2;
  for (const key of Object.keys(centers)) {
    const c = centers[key];
    ctx.save();
    ctx.fillStyle = "#ffffff";
    ctx.strokeStyle = "#c53030";
    ctx.lineWidth = 2;
    ctx.fillRect(c.x - half, c.y - half, HANDLE_SIZE, HANDLE_SIZE);
    ctx.strokeRect(c.x - half, c.y - half, HANDLE_SIZE, HANDLE_SIZE);
    ctx.restore();
  }
}

function drawDeleteIcon(rectPx, hovered) {
  let x = rectPx.x + rectPx.w - DELETE_SIZE / 2;
  let y = rectPx.y - DELETE_SIZE - 6;
  x = clampRange(x, 2, canvas.width - DELETE_SIZE - 2);
  y = clampRange(y, 2, canvas.height - DELETE_SIZE - 2);
  const iconRect = { x, y, w: DELETE_SIZE, h: DELETE_SIZE };

  ctx.save();
  ctx.fillStyle = hovered ? "#9b2c2c" : "#c53030";
  ctx.fillRect(iconRect.x, iconRect.y, iconRect.w, iconRect.h);
  ctx.fillStyle = "#ffffff";
  ctx.font = "bold 14px sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText("x", iconRect.x + iconRect.w / 2, iconRect.y + iconRect.h / 2 + 0.5);
  ctx.restore();

  state.ui.deleteIcon = iconRect;
}

function pickBoxAtPoint(point) {
  const questions = getCurrentQuestions();
  for (let qi = questions.length - 1; qi >= 0; qi -= 1) {
    const q = questions[qi];
    for (let fi = q.figure_bboxes.length - 1; fi >= 0; fi -= 1) {
      const figPx = rectToPxObject(q.figure_bboxes[fi]);
      if (pointInRect(point, figPx)) return makeSelection("figure", qi, fi);
    }
    const qPx = rectToPxObject(q.question_bbox);
    if (pointInRect(point, qPx)) return makeSelection("question", qi);
  }
  return null;
}

function getHover(point) {
  if (state.tool !== "select") {
    return { kind: "none", handle: null, selection: null };
  }

  const selectedRect = getRectBySelection(state.selected);
  if (selectedRect) {
    const selectedPx = rectToPxObject(selectedRect);
    if (state.ui.deleteIcon && pointInRect(point, state.ui.deleteIcon)) {
      return { kind: "delete", handle: null, selection: state.selected };
    }
    const handle = getHandleHit(point, selectedPx);
    if (handle) {
      return { kind: "resize", handle, selection: state.selected };
    }
    if (pointInRect(point, selectedPx)) {
      return { kind: "move", handle: null, selection: state.selected };
    }
  }

  const hit = pickBoxAtPoint(point);
  if (hit) return { kind: "box", handle: null, selection: hit };
  return { kind: "none", handle: null, selection: null };
}

function applyCursor() {
  if (state.tool === "draw_question" || state.tool === "draw_figure") {
    canvas.style.cursor = "crosshair";
    return;
  }

  switch (state.hover.kind) {
    case "delete":
      canvas.style.cursor = "pointer";
      break;
    case "resize":
      canvas.style.cursor = cursorForHandle(state.hover.handle);
      break;
    case "move":
      canvas.style.cursor = "move";
      break;
    case "box":
      canvas.style.cursor = "pointer";
      break;
    default:
      canvas.style.cursor = "default";
  }
}

function draw() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  const slide = getCurrentSlide();
  if (!slide || !slide.imageObj) {
    state.ui.deleteIcon = null;
    return;
  }

  ctx.drawImage(slide.imageObj, 0, 0, canvas.width, canvas.height);

  slide.questions.forEach((q) => {
    drawRect(q.question_bbox, "#2b6cb0", 2, false);
    q.figure_bboxes.forEach((fig) => drawRect(fig, "#c05621", 2, false));
  });

  state.ui.deleteIcon = null;
  const selectedRect = getRectBySelection(state.selected);
  if (selectedRect) {
    drawRect(selectedRect, "#c53030", 3, true);
    const selectedPx = rectToPxObject(selectedRect);
    drawHandles(selectedPx);
    drawDeleteIcon(selectedPx, state.hover.kind === "delete");
  }

  if (state.interaction.mode === "draw" && state.interaction.previewRect) {
    drawRect(state.interaction.previewRect, "#2f855a", 2, true);
  }
}

function renderToolButtons() {
  toolSelectBtn.classList.toggle("active", state.tool === "select");
  drawQuestionBtn.classList.toggle("active", state.tool === "draw_question");
  drawFigureBtn.classList.toggle("active", state.tool === "draw_figure");
}

function setTool(tool) {
  state.tool = tool;
  state.hover = { kind: "none", handle: null, selection: null };
  state.interaction = { mode: "idle", handle: null, startPoint: null, startRect: null, previewRect: null };
  renderToolButtons();
  applyCursor();
  draw();
}

function renderTextForPreview(text) {
  const raw = String(text || "").trim();
  if (!raw) return `<p class="hint">暂无内容</p>`;
  const escaped = raw
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\n/g, "<br />");
  return escaped;
}

function applyMathPreview(previewEl, text) {
  if (!previewEl) return;
  previewEl.innerHTML = renderTextForPreview(text);
  if (window.renderMathInElement) {
    try {
      window.renderMathInElement(previewEl, {
        delimiters: [
          { left: "$$", right: "$$", display: true },
          { left: "$", right: "$", display: false },
          { left: "\\(", right: "\\)", display: false },
          { left: "\\[", right: "\\]", display: true },
        ],
        throwOnError: false,
      });
    } catch (_e) {
      // ignore render failure
    }
  }
}

function renderQuestionList() {
  const slide = getCurrentSlide();
  questionListEl.innerHTML = "";

  if (!slide) {
    questionListEl.innerHTML = `<p class="hint">请先上传图片。</p>`;
    return;
  }

  if (!slide.questions.length) {
    questionListEl.innerHTML = `<p class="hint">当前图片还没有标注。选择“绘制题目框”后在图片上拖拽。</p>`;
    return;
  }

  slide.questions.forEach((q, qi) => {
    const qSel = makeSelection("question", qi);
    const qBlock = document.createElement("div");
    qBlock.className = "question-block";

    const qItem = document.createElement("div");
    qItem.className = "list-item";
    if (isSameSelection(state.selected, qSel)) qItem.classList.add("active");
    qItem.innerHTML = `
      <button class="list-select" type="button" data-action="select" data-type="question" data-qi="${qi}">
        题目 ${qi + 1}
      </button>
      <button class="list-delete" type="button" data-action="delete" data-type="question" data-qi="${qi}">删除</button>
    `;
    qBlock.appendChild(qItem);

    if (q.ocr_loading) {
      const pending = document.createElement("p");
      pending.className = "hint";
      pending.textContent = "识别中...";
      qBlock.appendChild(pending);
    }

    if (q.ocr_error) {
      const err = document.createElement("p");
      err.className = "hint error-text";
      err.textContent = q.ocr_error;
      qBlock.appendChild(err);
    }

    if (q.ocr_preview) {
      const previewImageWrap = document.createElement("div");
      previewImageWrap.className = "ocr-image-wrap";
      previewImageWrap.innerHTML = `<img class="ocr-image" alt="题目预览" src="${q.ocr_preview}" />`;
      qBlock.appendChild(previewImageWrap);
    }

    const editorLabel = document.createElement("p");
    editorLabel.className = "hint";
    editorLabel.textContent = "题目文本（可编辑，支持 $...$ / $$...$$ 公式）";
    qBlock.appendChild(editorLabel);

    const editor = document.createElement("div");
    editor.className = "ocr-editor";
    editor.contentEditable = "true";
    editor.dataset.action = "edit";
    editor.dataset.qi = String(qi);
    editor.textContent = q.ocr_text || "";
    qBlock.appendChild(editor);

    const previewLabel = document.createElement("p");
    previewLabel.className = "hint";
    previewLabel.textContent = "数学表达式预览";
    qBlock.appendChild(previewLabel);

    const preview = document.createElement("div");
    preview.className = "ocr-preview";
    preview.dataset.qi = String(qi);
    qBlock.appendChild(preview);
    applyMathPreview(preview, q.ocr_text || "");

    if (q.figure_bboxes.length) {
      const figTitle = document.createElement("p");
      figTitle.className = "hint";
      figTitle.textContent = "图形框";
      qBlock.appendChild(figTitle);

      q.figure_bboxes.forEach((_, fi) => {
        const fSel = makeSelection("figure", qi, fi);
        const figItem = document.createElement("div");
        figItem.className = "list-item list-sub";
        if (isSameSelection(state.selected, fSel)) figItem.classList.add("active");
        figItem.innerHTML = `
          <button class="list-select" type="button" data-action="select" data-type="figure" data-qi="${qi}" data-fi="${fi}">
            图形 ${qi + 1}-${fi + 1}
          </button>
          <button class="list-delete" type="button" data-action="delete" data-type="figure" data-qi="${qi}" data-fi="${fi}">删除</button>
        `;
        qBlock.appendChild(figItem);
      });
    }

    questionListEl.appendChild(qBlock);
  });
}

function renderCarousel() {
  if (!state.images.length) {
    imageIndicatorEl.textContent = "未上传图片";
    imageTabsEl.innerHTML = "";
    return;
  }

  const slide = getCurrentSlide();
  imageIndicatorEl.textContent = `${state.currentIndex + 1} / ${state.images.length} - ${slide.image_name}`;

  imageTabsEl.innerHTML = "";
  state.images.forEach((img, idx) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "image-tab";
    if (idx === state.currentIndex) btn.classList.add("active");
    btn.dataset.index = String(idx);
    btn.textContent = `${idx + 1}. ${img.image_name}`;
    imageTabsEl.appendChild(btn);
  });
}

function adjustSelectionAfterDelete(selection) {
  const questions = getCurrentQuestions();
  if (!state.selected) return;

  if (selection.type === "question") {
    if (state.selected.qi === selection.qi) {
      if (!questions.length) {
        state.selected = null;
      } else {
        state.selected = makeSelection("question", Math.min(selection.qi, questions.length - 1));
      }
      return;
    }

    if (state.selected.qi > selection.qi) {
      if (state.selected.type === "question") {
        state.selected = makeSelection("question", state.selected.qi - 1);
      } else {
        state.selected = makeSelection("figure", state.selected.qi - 1, state.selected.fi);
      }
    }
    return;
  }

  if (selection.type === "figure" && state.selected.type === "figure" && state.selected.qi === selection.qi) {
    if (state.selected.fi === selection.fi) {
      state.selected = makeSelection("question", selection.qi);
    } else if (state.selected.fi > selection.fi) {
      state.selected = makeSelection("figure", selection.qi, state.selected.fi - 1);
    }
  }
}

function deleteSelection(selection, sourceLabel) {
  const questions = getCurrentQuestions();
  if (!selection) return;

  if (selection.type === "question") {
    if (!questions[selection.qi]) return;
    questions.splice(selection.qi, 1);
    adjustSelectionAfterDelete(selection);
    setExportStatus(`${sourceLabel}已删除题目框。`);
  } else {
    const q = questions[selection.qi];
    if (!q || !q.figure_bboxes[selection.fi]) return;
    q.figure_bboxes.splice(selection.fi, 1);
    q.has_figure = q.figure_bboxes.length > 0;
    adjustSelectionAfterDelete(selection);
    setExportStatus(`${sourceLabel}已删除图形框。`);
  }

  if (!questions.length) state.selected = null;
  renderQuestionList();
  draw();
}

function canvasPointFromEvent(event) {
  const rect = canvas.getBoundingClientRect();
  const x = ((event.clientX - rect.left) * canvas.width) / rect.width;
  const y = ((event.clientY - rect.top) * canvas.height) / rect.height;
  return {
    x: clampRange(x, 0, canvas.width),
    y: clampRange(y, 0, canvas.height),
  };
}

function normRectFromPoints(p1, p2) {
  const x1 = clamp01(Math.min(p1.x, p2.x) / canvas.width);
  const y1 = clamp01(Math.min(p1.y, p2.y) / canvas.height);
  const x2 = clamp01(Math.max(p1.x, p2.x) / canvas.width);
  const y2 = clamp01(Math.max(p1.y, p2.y) / canvas.height);
  return sanitizeRect([x1, y1, x2, y2]);
}

function updateHoverAndCursor(point) {
  state.hover = getHover(point);
  applyCursor();
}

function moveRect(startRect, dx, dy) {
  const [x1, y1, x2, y2] = sanitizeRect(startRect);
  const w = x2 - x1;
  const h = y2 - y1;
  const nx1 = clampRange(x1 + dx, 0, 1 - w);
  const ny1 = clampRange(y1 + dy, 0, 1 - h);
  return [nx1, ny1, nx1 + w, ny1 + h];
}

function resizeRect(startRect, handle, dx, dy) {
  let [x1, y1, x2, y2] = sanitizeRect(startRect);

  if (handle.includes("w")) x1 += dx;
  if (handle.includes("e")) x2 += dx;
  if (handle.includes("n")) y1 += dy;
  if (handle.includes("s")) y2 += dy;

  if (handle.includes("w")) x1 = Math.min(x1, x2 - MIN_BOX_SIZE);
  if (handle.includes("e")) x2 = Math.max(x2, x1 + MIN_BOX_SIZE);
  if (handle.includes("n")) y1 = Math.min(y1, y2 - MIN_BOX_SIZE);
  if (handle.includes("s")) y2 = Math.max(y2, y1 + MIN_BOX_SIZE);

  x1 = clamp01(x1);
  y1 = clamp01(y1);
  x2 = clamp01(x2);
  y2 = clamp01(y2);

  if (x2 - x1 < MIN_BOX_SIZE) {
    if (handle.includes("w")) x1 = Math.max(0, x2 - MIN_BOX_SIZE);
    else x2 = Math.min(1, x1 + MIN_BOX_SIZE);
  }
  if (y2 - y1 < MIN_BOX_SIZE) {
    if (handle.includes("n")) y1 = Math.max(0, y2 - MIN_BOX_SIZE);
    else y2 = Math.min(1, y1 + MIN_BOX_SIZE);
  }

  return sanitizeRect([x1, y1, x2, y2]);
}

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("图片加载失败"));
    img.src = `${url}?t=${Date.now()}`;
  });
}

async function ensureSlideImageLoaded(slide) {
  if (!slide) return;
  if (!slide.imageObj) {
    slide.imageObj = await loadImage(slide.image_url);
  }
}

async function switchToImage(index) {
  if (!state.images.length) return;
  const nextIndex = clampRange(index, 0, state.images.length - 1);
  state.currentIndex = nextIndex;
  clearCanvasState();
  await ensureSlideImageLoaded(getCurrentSlide());
  resizeCanvas();
  renderCarousel();
  renderQuestionList();
  draw();
  applyCursor();
}

async function recognizeQuestion(qi, options = {}) {
  const { silent = false } = options;
  const slide = getCurrentSlide();
  if (!slide) return;
  const q = slide.questions[qi];
  if (!q) return;
  if (!state.sessionId) return;

  q.ocr_req_id += 1;
  const reqId = q.ocr_req_id;
  q.ocr_loading = true;
  q.ocr_error = "";
  renderQuestionList();

  try {
    const res = await fetch("/api/recognize-question", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: state.sessionId,
        image_id: slide.image_id,
        question_bbox: sanitizeRect(q.question_bbox),
      }),
    });

    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || "识别失败");

    if (q.ocr_req_id !== reqId) return;
    q.ocr_text = String(data.ocr_text || "");
    q.ocr_preview = String(data.crop_data_url || "");
    q.ocr_elapsed_ms = Number(data.ocr_elapsed_ms || 0);
    q.ocr_model = String(data.ocr_model || "");
    q.ocr_loading = false;
    q.ocr_error = "";

    renderQuestionList();
    if (!silent) {
      const speed = q.ocr_elapsed_ms > 0 ? `（${q.ocr_elapsed_ms}ms）` : "";
      const model = q.ocr_model ? `，模型：${q.ocr_model}` : "";
      setExportStatus(`题目 ${qi + 1} 已完成识别${speed}${model}，可直接编辑题干。`, false);
    }
  } catch (error) {
    if (q.ocr_req_id !== reqId) return;
    q.ocr_loading = false;
    q.ocr_error = String(error.message || error);
    renderQuestionList();
    if (!silent) setExportStatus(`题目 ${qi + 1} 识别失败：${q.ocr_error}`, true);
  }
}

if (uploadDropzone && imageInput) {
  uploadDropzone.addEventListener("click", () => imageInput.click());
  uploadDropzone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      imageInput.click();
    }
  });

  uploadDropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    uploadDropzone.classList.add("dragover");
  });
  uploadDropzone.addEventListener("dragleave", () => {
    uploadDropzone.classList.remove("dragover");
  });
  uploadDropzone.addEventListener("drop", async (event) => {
    event.preventDefault();
    uploadDropzone.classList.remove("dragover");
    const dropped = event.dataTransfer ? event.dataTransfer.files : null;
    await uploadImages(dropped);
  });

  imageInput.addEventListener("change", async () => {
    await uploadImages(imageInput.files);
  });
}

questionListEl.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (target.tagName !== "BUTTON") return;

  const action = target.dataset.action;
  const qi = Number(target.dataset.qi);
  if (Number.isNaN(qi)) return;

  const slide = getCurrentSlide();
  if (!slide) return;

  const type = target.dataset.type;
  const fi = Number(target.dataset.fi);
  if (type === "figure" && Number.isNaN(fi)) return;

  const selection = type === "figure" ? makeSelection("figure", qi, fi) : makeSelection("question", qi);
  if (action === "delete") {
    deleteSelection(selection, "列表");
    return;
  }

  if (action === "select") {
    state.selected = selection;
    setTool("select");
    renderQuestionList();
    draw();
  }
});

questionListEl.addEventListener("input", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (!target.classList.contains("ocr-editor")) return;

  const qi = Number(target.dataset.qi);
  if (Number.isNaN(qi)) return;

  const slide = getCurrentSlide();
  if (!slide || !slide.questions[qi]) return;
  const q = slide.questions[qi];

  q.ocr_text = target.innerText || "";

  const block = target.closest(".question-block");
  if (!block) return;

  const previewEl = block.querySelector(".ocr-preview");
  if (previewEl instanceof HTMLElement) applyMathPreview(previewEl, q.ocr_text);
});

imageTabsEl.addEventListener("click", async (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (!target.classList.contains("image-tab")) return;

  const idx = Number(target.dataset.index);
  if (Number.isNaN(idx)) return;
  await switchToImage(idx);
});

prevImageBtn.addEventListener("click", async () => {
  if (!state.images.length) return;
  await switchToImage(state.currentIndex - 1);
});

nextImageBtn.addEventListener("click", async () => {
  if (!state.images.length) return;
  await switchToImage(state.currentIndex + 1);
});

toolSelectBtn.addEventListener("click", () => setTool("select"));
drawQuestionBtn.addEventListener("click", () => setTool("draw_question"));
drawFigureBtn.addEventListener("click", () => setTool("draw_figure"));

resetPromptBtn.addEventListener("click", () => {
  promptTemplateEl.value = state.defaultPromptTemplate || "";
  setExportStatus("已恢复默认提示词模板。", false);
});

canvas.addEventListener("mousedown", (event) => {
  const slide = getCurrentSlide();
  if (!slide || !slide.imageObj) return;

  const point = canvasPointFromEvent(event);

  if (state.tool === "draw_question" || state.tool === "draw_figure") {
    state.interaction = {
      mode: "draw",
      handle: null,
      startPoint: point,
      startRect: null,
      previewRect: [
        point.x / canvas.width,
        point.y / canvas.height,
        point.x / canvas.width,
        point.y / canvas.height,
      ],
    };
    draw();
    return;
  }

  updateHoverAndCursor(point);

  if (state.hover.kind === "delete") {
    deleteSelection(state.selected, "画布");
    return;
  }

  if (state.hover.kind === "resize" && state.selected) {
    state.interaction = {
      mode: "resize",
      handle: state.hover.handle,
      startPoint: point,
      startRect: [...getRectBySelection(state.selected)],
      previewRect: null,
    };
    return;
  }

  if (state.hover.kind === "move" && state.selected) {
    state.interaction = {
      mode: "move",
      handle: null,
      startPoint: point,
      startRect: [...getRectBySelection(state.selected)],
      previewRect: null,
    };
    return;
  }

  if (state.hover.kind === "box" && state.hover.selection) {
    state.selected = state.hover.selection;
    renderQuestionList();
    draw();
    updateHoverAndCursor(point);
    return;
  }

  state.selected = null;
  renderQuestionList();
  draw();
  updateHoverAndCursor(point);
});

canvas.addEventListener("mousemove", (event) => {
  const slide = getCurrentSlide();
  if (!slide || !slide.imageObj) return;

  const point = canvasPointFromEvent(event);

  if (state.interaction.mode === "draw" && state.interaction.startPoint) {
    state.interaction.previewRect = normRectFromPoints(state.interaction.startPoint, point);
    draw();
    applyCursor();
    return;
  }

  if (state.interaction.mode === "move" && state.selected) {
    const dx = (point.x - state.interaction.startPoint.x) / canvas.width;
    const dy = (point.y - state.interaction.startPoint.y) / canvas.height;
    const nextRect = moveRect(state.interaction.startRect, dx, dy);
    setRectBySelection(state.selected, nextRect);
    draw();
    updateHoverAndCursor(point);
    return;
  }

  if (state.interaction.mode === "resize" && state.selected) {
    const dx = (point.x - state.interaction.startPoint.x) / canvas.width;
    const dy = (point.y - state.interaction.startPoint.y) / canvas.height;
    const nextRect = resizeRect(state.interaction.startRect, state.interaction.handle, dx, dy);
    setRectBySelection(state.selected, nextRect);
    draw();
    updateHoverAndCursor(point);
    return;
  }

  updateHoverAndCursor(point);
  if (state.selected) draw();
});

window.addEventListener("mouseup", (event) => {
  const slide = getCurrentSlide();
  if (!slide) return;

  if (state.interaction.mode === "draw") {
    const point = canvasPointFromEvent(event);
    const rect = normRectFromPoints(state.interaction.startPoint, point);
    state.interaction = { mode: "idle", handle: null, startPoint: null, startRect: null, previewRect: null };

    if (Math.abs(rect[2] - rect[0]) < MIN_BOX_SIZE || Math.abs(rect[3] - rect[1]) < MIN_BOX_SIZE) {
      setExportStatus("框选区域太小，请重新绘制。", true);
      draw();
      applyCursor();
      return;
    }

    if (state.tool === "draw_question") {
      slide.questions.push(createQuestion(rect));
      const newIndex = slide.questions.length - 1;
      state.selected = makeSelection("question", newIndex);
      setExportStatus("已新增题目框，正在自动识别...", false);
      renderQuestionList();
      draw();
      applyCursor();
      recognizeQuestion(newIndex, { silent: true });
      return;
    }

    if (state.tool === "draw_figure") {
      const qi = getSelectedQuestionIndex();
      if (qi < 0 || !slide.questions[qi]) {
        setExportStatus("绘制图形框前请先选中一个题目框。", true);
        draw();
        applyCursor();
        return;
      }
      const q = slide.questions[qi];
      q.figure_bboxes.push(rect);
      q.has_figure = true;
      state.selected = makeSelection("figure", qi, q.figure_bboxes.length - 1);
      setExportStatus("已新增图形框。", false);
      renderQuestionList();
      draw();
      applyCursor();
      return;
    }
  }

  if (state.interaction.mode === "move" || state.interaction.mode === "resize") {
    const movedSelection = state.selected;
    state.interaction = { mode: "idle", handle: null, startPoint: null, startRect: null, previewRect: null };
    setExportStatus("框选已更新。", false);

    if (typeof event.clientX === "number" && typeof event.clientY === "number") {
      const point = canvasPointFromEvent(event);
      updateHoverAndCursor(point);
    }
    draw();
    renderQuestionList();

    if (movedSelection && movedSelection.type === "question") {
      setExportStatus("框选已更新，正在重新识别题干...", false);
      recognizeQuestion(movedSelection.qi, { silent: true });
    }
  }
});

canvas.addEventListener("mouseleave", () => {
  if (state.interaction.mode !== "idle") return;
  if (state.tool !== "select") {
    applyCursor();
    return;
  }
  state.hover = { kind: "none", handle: null, selection: null };
  canvas.style.cursor = "default";
  if (state.selected) draw();
});

function serializeQuestionsForExport(questions) {
  return questions.map((q, idx) => ({
    question_no: q.question_no || String(idx + 1),
    question_bbox: sanitizeRect(q.question_bbox),
    figure_bboxes: (q.figure_bboxes || []).map(sanitizeRect),
    has_figure: (q.figure_bboxes || []).length > 0,
    ocr_text: String(q.ocr_text || "").trim(),
  }));
}

exportBtn.addEventListener("click", async () => {
  if (!state.sessionId) {
    setExportStatus("请先上传图片。", true);
    return;
  }

  const questionCount = state.images.reduce((acc, img) => acc + img.questions.length, 0);
  if (questionCount === 0) {
    setExportStatus("请先手动标注至少一个题目框。", true);
    return;
  }

  const promptTemplate = (promptTemplateEl.value || "").trim();

  exportBtn.disabled = true;
  setExportStatus("正在导出 Markdown...");
  exportLinksEl.innerHTML = "";
  resetNotionProgress();
  setNotionProgress(1, "导出任务准备中...");

  const payload = {
    session_id: state.sessionId,
    prompt_template: promptTemplate || state.defaultPromptTemplate || "",
    images: state.images.map((img) => ({
      image_id: img.image_id,
      questions: serializeQuestionsForExport(img.questions),
    })),
  };

  try {
    const data = await runExportTask(payload, questionCount);
    if (!data || !data.ok) throw new Error(data?.error || "导出失败");

    const warnings = Array.isArray(data.warnings) ? data.warnings.filter(Boolean) : [];
    const markdownItems = Array.isArray(data.markdown_urls) ? data.markdown_urls : [];
    const statusText =
      warnings.length > 0
        ? `导出完成，共 ${data.question_count} 道错题（${warnings.length} 道使用了模板占位）。`
        : `导出完成，共 ${data.question_count} 道错题。`;
    setNotionProgress(100, `Markdown 导出完成 ${data.question_count || 0} 题`);
    setExportStatus(statusText, false);
    exportLinksEl.innerHTML = "";

    if (markdownItems.length > 0 && state.notionEnabled) {
      const { notionLinks, notionErrors } = await uploadMarkdownsToNotion(markdownItems);
      setNotionProgress(100, `Notion 上传完成 ${notionLinks.length}/${markdownItems.length}`);
      if (notionErrors.length > 0) {
        setExportStatus(
          `Notion 上传完成 ${notionLinks.length}/${markdownItems.length}，${notionErrors.length} 条失败。`,
          true
        );
      } else {
        setExportStatus(`Notion 上传完成，共 ${notionLinks.length} 条。`, false);
      }
      const summaryParts = [`Notion 成功 ${notionLinks.length} 条`];
      if (notionErrors.length > 0) summaryParts.push(`失败 ${notionErrors.length} 条`);
      exportLinksEl.innerHTML = `<span class="hint">${summaryParts.join("，")}</span>`;
    } else if (markdownItems.length > 0 && !state.notionEnabled) {
      exportLinksEl.innerHTML = `<span class="hint">Notion 未配置，已跳过上传。</span>`;
      resetNotionProgress();
    }
  } catch (error) {
    setExportStatus(String(error.message || error), true);
  } finally {
    exportBtn.disabled = false;
  }
});

window.addEventListener("resize", () => {
  if (!getCurrentSlide()) return;
  resizeCanvas();
  draw();
});

renderToolButtons();
applyCursor();
initAiHealthChecker();
draw();
