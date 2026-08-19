const batches = [
  {
    id: "morning",
    name: "晨间抽检",
    date: "08 · 19",
    items: [
      {
        id: "SYN-A14",
        title: "商品描述一致性",
        category: "图文内容",
        source: "合成样本",
        priority: "普通",
        summary: "一条虚构的商品介绍声称容器容量为 750 毫升；配图中的刻度标记与文字描述基本一致，需要结合补充说明判断表达是否充分。",
        evidence: [
          { time: "09:12", title: "文本片段", body: "标题与详情页均出现“750 毫升”描述。", detail: "字段：容量说明｜匹配位置：标题、详情摘要｜一致性：2/2" },
          { time: "09:13", title: "画面摘要", body: "合成示意图可见 250、500、750 三档刻度。", detail: "画面类型：产品示意｜可见区域：主体正面｜清晰度：良好" },
          { time: "09:14", title: "上下文补充", body: "说明文字注明刻度为近似值，不用于精密计量。", detail: "补充位置：使用说明｜限定语：已提供｜冲突项：无" }
        ]
      },
      {
        id: "SYN-A15",
        title: "活动时间表述",
        category: "营销文案",
        source: "合成样本",
        priority: "较高",
        summary: "虚构活动卡片写有“本周末开放”，正文给出周六 10:00 至周日 18:00 的明确区间，需要判断两处信息是否互相支持。",
        evidence: [
          { time: "09:26", title: "卡片文案", body: "主标题使用“本周末开放”。", detail: "文本位置：首屏卡片｜相对表述：本周末" },
          { time: "09:27", title: "正文时间", body: "正文列出周六 10:00 至周日 18:00。", detail: "日期类型：星期｜开始：周六 10:00｜结束：周日 18:00" }
        ]
      },
      {
        id: "SYN-A16",
        title: "材质信息核对",
        category: "商品信息",
        source: "合成样本",
        priority: "普通",
        summary: "虚构收纳包在标题中使用“再生纤维面料”，目前证据只包含外观与颜色描述，缺少能够确认材质的信息。",
        evidence: [
          { time: "09:41", title: "标题声明", body: "标题包含“再生纤维面料”字样。", detail: "声明类型：材质｜限定范围：包体面料" },
          { time: "09:42", title: "画面摘要", body: "示意图只能识别织物纹理与蓝灰色外观。", detail: "可验证项：外观、颜色｜不可验证项：纤维来源" }
        ]
      }
    ]
  },
  {
    id: "followup",
    name: "补充证据",
    date: "08 · 18",
    items: [
      {
        id: "SYN-B07",
        title: "课程范围说明",
        category: "知识内容",
        source: "合成样本",
        priority: "较高",
        summary: "一份虚构课程简介写有“覆盖基础摄影全流程”，章节列表包含曝光、构图与后期整理，但未列出器材维护相关内容。",
        evidence: [
          { time: "14:05", title: "简介摘录", body: "宣传语使用“覆盖基础摄影全流程”。", detail: "声明范围：完整流程｜表达强度：较强" },
          { time: "14:06", title: "章节目录", body: "目录包含曝光、构图、用光和基础后期整理。", detail: "章节数：8｜已覆盖主题：4 类｜器材维护：未列出" },
          { time: "14:08", title: "讲义附录", body: "附录提供器材清洁与存放的简短指引。", detail: "附录页数：2｜内容类型：维护指引" }
        ]
      },
      {
        id: "SYN-B08",
        title: "服务区域核对",
        category: "服务信息",
        source: "合成样本",
        priority: "普通",
        summary: "虚构页面称配送覆盖“城区全部街道”，区域清单目前列出六个片区，但页面没有给出完整街道列表或例外说明。",
        evidence: [
          { time: "14:22", title: "页面声明", body: "页首写有“城区全部街道可配送”。", detail: "声明类型：服务范围｜例外条件：未标注" },
          { time: "14:23", title: "区域清单", body: "下方仅列出六个片区名称。", detail: "清单粒度：片区｜街道明细：未提供" }
        ]
      }
    ]
  },
  {
    id: "calibration",
    name: "标尺校准",
    date: "08 · 17",
    items: [
      {
        id: "SYN-C03",
        title: "包装数量核验",
        category: "商品信息",
        source: "合成样本",
        priority: "普通",
        summary: "虚构茶包商品标题标注“24 袋装”，包装正面与侧面合成图均能看到 24 袋的数量标记。",
        evidence: [
          { time: "16:31", title: "标题信息", body: "商品标题标注“24 袋装”。", detail: "数量：24｜单位：袋｜位置：标题" },
          { time: "16:32", title: "正面标记", body: "包装正面右下角可见“24”数量标记。", detail: "识别结果：24｜清晰度：高" },
          { time: "16:33", title: "侧面说明", body: "侧面成分区上方写有“内含 24 袋”。", detail: "识别结果：内含 24 袋｜冲突项：无" }
        ]
      }
    ]
  }
];

const STORAGE_KEY = "crt-showcase-labels-v1";
let activeBatch = 0;
let activeItem = 0;
let labels = readLabels();

const els = {
  batchList: document.querySelector("#batch-list"),
  kicker: document.querySelector("#case-kicker"),
  title: document.querySelector("#case-title"),
  position: document.querySelector("#case-position"),
  meta: document.querySelector("#case-meta"),
  summary: document.querySelector("#case-summary"),
  evidenceCount: document.querySelector("#evidence-count"),
  evidenceList: document.querySelector("#evidence-list"),
  form: document.querySelector("#label-form"),
  note: document.querySelector("#review-note"),
  noteCount: document.querySelector("#note-count"),
  savedState: document.querySelector("#saved-state"),
  prev: document.querySelector("#prev-case"),
  next: document.querySelector("#next-case"),
  reset: document.querySelector("#reset-demo"),
  toast: document.querySelector("#toast")
};

function readLabels() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}; }
  catch { return {}; }
}

function current() {
  return batches[activeBatch].items[activeItem];
}

function completedCount(batch) {
  return batch.items.filter(item => labels[item.id]).length;
}

function renderBatches() {
  els.batchList.innerHTML = "";
  batches.forEach((batch, index) => {
    const done = completedCount(batch);
    const button = document.createElement("button");
    button.type = "button";
    button.className = `batch-button${index === activeBatch ? " active" : ""}`;
    button.setAttribute("role", "listitem");
    button.innerHTML = `<span><b>${batch.name}</b><em>${batch.date}</em></span><span><em>${done} / ${batch.items.length} 已完成</em></span><div class="progress-track"><i style="width:${(done / batch.items.length) * 100}%"></i></div>`;
    button.addEventListener("click", () => {
      activeBatch = index;
      activeItem = 0;
      render();
    });
    els.batchList.appendChild(button);
  });
}

function renderCase() {
  const batch = batches[activeBatch];
  const item = current();
  els.kicker.textContent = `${batch.name} · ${item.id}`;
  els.title.textContent = item.title;
  els.position.textContent = `${activeItem + 1} / ${batch.items.length}`;
  els.summary.textContent = item.summary;
  els.meta.innerHTML = [item.category, item.source, `优先级：${item.priority}`].map(value => `<span>${value}</span>`).join("");
  els.evidenceCount.textContent = `${item.evidence.length} 项`;
  els.evidenceList.innerHTML = "";

  item.evidence.forEach((evidence, index) => {
    const card = document.createElement("article");
    card.className = "evidence-card";
    card.tabIndex = 0;
    card.setAttribute("aria-expanded", "false");
    card.innerHTML = `<header><h4>${String(index + 1).padStart(2, "0")} · ${evidence.title}</h4><time>${evidence.time}</time></header><p>${evidence.body}</p><div class="evidence-detail">${evidence.detail}</div>`;
    const toggle = () => {
      const expanded = card.classList.toggle("expanded");
      card.setAttribute("aria-expanded", String(expanded));
    };
    card.addEventListener("click", toggle);
    card.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggle();
      }
    });
    els.evidenceList.appendChild(card);
  });

  els.prev.disabled = activeItem === 0;
  els.next.disabled = activeItem === batch.items.length - 1;
  restoreForm(item.id);
}

function restoreForm(id) {
  els.form.reset();
  const saved = labels[id];
  els.note.value = saved?.note || "";
  if (saved) {
    const decision = els.form.querySelector(`[name="decision"][value="${saved.decision}"]`);
    const confidence = els.form.querySelector(`[name="confidence"][value="${saved.confidence}"]`);
    if (decision) decision.checked = true;
    if (confidence) confidence.checked = true;
    els.savedState.textContent = `已保存：${saved.decision} · ${saved.confidence}置信度`;
  } else {
    els.savedState.textContent = "";
  }
  els.noteCount.textContent = els.note.value.length;
}

function render() {
  renderBatches();
  renderCase();
}

function move(delta) {
  const next = activeItem + delta;
  if (next >= 0 && next < batches[activeBatch].items.length) {
    activeItem = next;
    renderCase();
  }
}

function showToast(message) {
  els.toast.textContent = message;
  els.toast.classList.add("show");
  window.setTimeout(() => els.toast.classList.remove("show"), 1800);
}

els.prev.addEventListener("click", () => move(-1));
els.next.addEventListener("click", () => move(1));
els.note.addEventListener("input", () => { els.noteCount.textContent = els.note.value.length; });

els.form.addEventListener("submit", event => {
  event.preventDefault();
  const data = new FormData(els.form);
  const decision = data.get("decision");
  const confidence = data.get("confidence");
  if (!decision || !confidence) {
    showToast("请先选择标签和置信度");
    return;
  }
  const item = current();
  labels[item.id] = { decision, confidence, note: els.note.value.trim(), savedAt: new Date().toISOString() };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(labels));
  renderBatches();
  els.savedState.textContent = `已保存：${decision} · ${confidence}置信度`;
  showToast("已保存到本地演示");
  if (activeItem < batches[activeBatch].items.length - 1) {
    window.setTimeout(() => { activeItem += 1; renderCase(); }, 650);
  }
});

els.reset.addEventListener("click", () => {
  if (!window.confirm("清除当前浏览器中的全部演示标签？")) return;
  localStorage.removeItem(STORAGE_KEY);
  labels = {};
  activeItem = 0;
  render();
  showToast("演示数据已重置");
});

render();
