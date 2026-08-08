(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    activeView: "chat",
    chatSession: crypto.randomUUID(),
    chatBusy: false,
    aiopsBusy: false,
    aiopsMode: "realtime",
    sourceStatus: null,
    reportReceived: false,
    faultBusy: false,
    faultScenarios: [],
    faultExpiryTimer: null,
    incidentBusy: false,
    lastDiagnosisReport: "",
    lastDiagnosisSessionId: "",
    lastDiagnosisMode: "realtime",
  };

  const sourceMeta = {
    prometheus: ["Prometheus", "实时告警源"],
    cls: ["Log MCP", "本地结构化日志"],
    monitor: ["Monitor MCP", "真实 PromQL 查询"],
    milvus: ["Milvus", "向量知识库"],
    fault_lab: ["Fault Lab", "受控故障实验服务"],
  };

  function switchView(view) {
    state.activeView = view;
    $("chatView").classList.toggle("active", view === "chat");
    $("aiopsView").classList.toggle("active", view === "aiops");
    $("navChatAgent").classList.toggle("active", view === "chat");
    $("navAiopsAgent").classList.toggle("active", view === "aiops");
    if (view === "aiops") Promise.all([refreshSources(), loadFaultLab(), loadIncidentMemories()]);
  }

  async function refreshSources() {
    const button = $("sourceRefresh");
    button.disabled = true;
    $("sourceSummary").className = "status-pill checking";
    $("sourceSummary").textContent = "检查中";
    try {
      const response = await fetch("/api/aiops/sources", { cache: "no-store" });
      if (!response.ok) throw new Error(await apiError(response));
      renderSources(await response.json());
    } catch (error) {
      renderSourceFailure();
      toast(error.message || "数据源检查失败", true);
    } finally {
      button.disabled = false;
    }
  }

  function renderSources(data) {
    state.sourceStatus = data;
    Object.entries(sourceMeta).forEach(([key, meta]) => {
      const value = data.sources?.[key] || { available: false, message: "未返回状态" };
      const card = document.querySelector(`[data-source="${key}"]`);
      card.className = `source-card ${value.available ? "available" : "unavailable"}`;
      card.querySelector("strong").textContent = meta[0];
      card.querySelector("small").textContent = meta[1];
      card.querySelector("p").textContent = value.message;
      card.querySelector(".source-state").textContent = value.available ? "可用" : "不可用";
    });
    const ready = data.overall === "ready";
    $("sourceSummary").className = `status-pill ${ready ? "ready" : "degraded"}`;
    $("sourceSummary").textContent = ready ? "全部可用" : "部分能力降级";
    updateRealtimeAvailability(Boolean(data.modes?.realtime?.available), data.modes?.realtime?.reason);
  }

  function renderSourceFailure() {
    state.sourceStatus = null;
    Object.keys(sourceMeta).forEach((key) => {
      const card = document.querySelector(`[data-source="${key}"]`);
      card.className = "source-card unavailable";
      card.querySelector("p").textContent = "状态检查失败";
      card.querySelector(".source-state").textContent = "未知";
    });
    $("sourceSummary").className = "status-pill degraded";
    $("sourceSummary").textContent = "检查失败";
    updateRealtimeAvailability(false, "无法确认 Prometheus 状态，请刷新后重试");
  }

  function updateRealtimeAvailability(available, reason = "") {
    const notice = $("realtimeNotice");
    const run = $("realtimeRun");
    run.disabled = !available || state.aiopsBusy;
    notice.className = `notice ${available ? "success" : "warning"}`;
    notice.innerHTML = available
      ? "<span>✓</span><div><strong>实时告警源已就绪</strong><p>将读取 Prometheus 当前告警，并结合可用工具补充证据。</p></div>"
      : `<span>!</span><div><strong>实时诊断暂不可用</strong><p>${escapeHtml(reason || "Prometheus 不可用")}</p></div>`;
  }

  function setMode(mode) {
    state.aiopsMode = mode;
    const realtime = mode === "realtime";
    $("modeRealtime").classList.toggle("active", realtime);
    $("modeManual").classList.toggle("active", !realtime);
    $("modeRealtime").setAttribute("aria-selected", String(realtime));
    $("modeManual").setAttribute("aria-selected", String(!realtime));
    $("realtimePanel").classList.toggle("active", realtime);
    $("manualPanel").classList.toggle("active", !realtime);
  }

  async function loadFaultLab() {
    try {
      const response = await fetch("/api/fault-lab", { cache: "no-store" });
      if (!response.ok) throw new Error(await apiError(response));
      const data = await response.json();
      state.faultScenarios = data.scenarios || [];
      const select = $("faultScenarioSelect");
      const previous = select.value;
      select.innerHTML = state.faultScenarios.map((scenario) =>
        `<option value="${escapeHtml(scenario.id)}">${scenario.data_origin === "replay_external" ? "[外部基准] " : "[本地] "}${escapeHtml(scenario.title)}</option>`
      ).join("");
      const activeId = data.state?.active_scenario?.id || "normal";
      select.value = previous && state.faultScenarios.some((item) => item.id === previous) ? previous : activeId;
      $("faultActiveScenario").textContent = data.state?.active_scenario?.title || "未知";
      startFaultCountdown(data.state?.expires_at || null);
      renderFaultScenarioDetail();
      setFaultControls(false);
    } catch (error) {
      $("faultActiveScenario").textContent = "服务不可用";
      startFaultCountdown(null, "无法读取自动恢复状态");
      $("faultScenarioDetail").textContent = error.message || "无法连接 Fault Lab";
      setFaultControls(true);
    }
  }

  function startFaultCountdown(expiresAt, fallback = "正常模式，无需自动恢复") {
    clearInterval(state.faultExpiryTimer);
    state.faultExpiryTimer = null;
    const node = $("faultExpiry");
    if (!expiresAt) {
      node.textContent = fallback;
      return;
    }
    const deadline = Date.parse(expiresAt);
    const update = () => {
      const remaining = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      if (remaining <= 0) {
        node.textContent = "安全时限已到，正在恢复正常…";
        clearInterval(state.faultExpiryTimer);
        state.faultExpiryTimer = null;
        window.setTimeout(loadFaultLab, 1000);
        return;
      }
      const minutes = Math.floor(remaining / 60);
      const seconds = String(remaining % 60).padStart(2, "0");
      node.textContent = `自动恢复倒计时 ${minutes}:${seconds}`;
    };
    update();
    state.faultExpiryTimer = window.setInterval(update, 1000);
  }

  function renderFaultScenarioDetail() {
    const selected = state.faultScenarios.find((item) => item.id === $("faultScenarioSelect").value);
    if (!selected) return;
    const alerts = selected.expected_alerts?.length ? selected.expected_alerts.join("、") : "无预期告警";
    const external = selected.data_origin === "replay_external";
    const sourceUrl = external && /^https:\/\/github\.com\//.test(selected.source_url || "")
      ? selected.source_url
      : "";
    const provenance = external
      ? `<span>场景来源：${escapeHtml(selected.benchmark || "外部故障基准")} · ${escapeHtml(selected.license || "许可未知")} · 本地产生实时回放信号</span>`
      : `<span>场景来源：本项目设计 · 本地产生实时信号</span>`;
    const sourceLink = sourceUrl
      ? `<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer">查看固定版本来源</a>`
      : "";
    $("faultScenarioDetail").innerHTML = `<strong>${escapeHtml(selected.description)}</strong>${provenance}<span>预期：${escapeHtml(alerts)}</span><span>标准根因：${escapeHtml(selected.ground_truth)}</span>${sourceLink}`;
  }

  function setFaultControls(disabled) {
    state.faultBusy = disabled;
    ["faultActivate", "faultRun", "faultReset", "faultScenarioSelect"].forEach((id) => { $(id).disabled = disabled; });
  }

  async function faultAction(action) {
    if (state.faultBusy) return;
    setFaultControls(true);
    try {
      let url;
      let body;
      if (action === "activate") {
        const scenarioId = $("faultScenarioSelect").value;
        if (!scenarioId) throw new Error("请选择实验场景");
        url = `/api/fault-lab/scenarios/${encodeURIComponent(scenarioId)}/activate`;
      } else if (action === "run") {
        url = "/api/fault-lab/run";
        body = JSON.stringify({ requests: 12, concurrency: 4 });
      } else {
        url = "/api/fault-lab/reset";
      }
      const response = await fetch(url, {
        method: "POST",
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body,
      });
      if (!response.ok) throw new Error(await apiError(response));
      const result = await response.json();
      if (action === "run") {
        const summary = Object.entries(result.status_counts || {}).map(([code, count]) => `${code}: ${count}`).join(" · ");
        $("faultRunSummary").textContent = `已产生 ${result.completed} 次请求 · ${summary}`;
        toast("真实实验流量已完成，等待 Prometheus 评估告警");
      } else {
        toast(action === "reset" ? "Fault Lab 已恢复正常" : "故障场景已启用");
      }
      await Promise.all([loadFaultLab(), refreshSources()]);
    } catch (error) {
      toast(error.message || "Fault Lab 操作失败", true);
      setFaultControls(false);
    }
  }
  async function runAIOps(mode) {
    if (state.aiopsBusy) return;
    const sessionId = crypto.randomUUID();
    const payload = { session_id: sessionId, mode };
    state.lastDiagnosisSessionId = sessionId;
    state.lastDiagnosisMode = mode;
    if (mode === "manual") {
      payload.service_name = $("manualService").value.trim() || null;
      payload.description = $("manualDescription").value.trim();
      if (!payload.description) {
        toast("请先填写故障现象", true);
        $("manualDescription").focus();
        return;
      }
    } else if (!state.sourceStatus?.modes?.realtime?.available) {
      toast("Prometheus 不可用，无法开始实时诊断", true);
      return;
    }

    state.aiopsBusy = true;
    state.reportReceived = false;
    setAIOpsBusy(true);
    resetResult(mode);
    try {
      const response = await fetch("/api/aiops", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error(await apiError(response));
      await readSSE(response, handleAIOpsEvent);
    } catch (error) {
      appendProgress(error.message || "诊断请求失败", "error");
      toast(error.message || "诊断请求失败", true);
    } finally {
      state.aiopsBusy = false;
      setAIOpsBusy(false);
    }
  }

  function setAIOpsBusy(busy) {
    $("manualRun").disabled = busy;
    const realtimeAvailable = Boolean(state.sourceStatus?.modes?.realtime?.available);
    $("realtimeRun").disabled = busy || !realtimeAvailable;
    $("sourceRefresh").disabled = busy;
    $("manualRun").textContent = busy && state.aiopsMode === "manual" ? "诊断进行中…" : "开始手工诊断";
    $("realtimeRun").textContent = busy && state.aiopsMode === "realtime" ? "诊断进行中…" : "开始实时诊断";
  }

  function resetResult(mode) {
    const result = $("aiopsResult");
    state.lastDiagnosisReport = "";
    $("incidentReview").hidden = true;
    [
      "memoryIncidentTitle",
      "memoryService",
      "memorySymptoms",
      "memoryAlerts",
      "memoryEvidence",
      "memoryRootCause",
      "memoryResolution",
      "memoryVerification",
    ].forEach((id) => { $(id).value = ""; });
    result.classList.remove("empty");
    result.querySelector(".empty-result").hidden = true;
    $("aiopsProgress").hidden = false;
    $("aiopsProgress").innerHTML = `<div class="progress-title">${mode === "realtime" ? "实时告警诊断" : "手工故障诊断"}</div><div class="progress-list"></div>`;
    $("aiopsReport").hidden = true;
    $("aiopsReport").innerHTML = "";
    appendProgress("正在检查诊断所需的数据源");
  }

  function handleAIOpsEvent(event) {
    if (event.type === "source_status") {
      renderSources(event);
      appendProgress("数据源预检完成");
    } else if (event.type === "plan") {
      appendProgress(event.message || "诊断计划已生成");
      (event.plan || []).forEach((step) => appendProgress(step));
    } else if (event.type === "step_complete" || event.type === "status") {
      appendProgress(event.current_step || event.message || "诊断步骤已完成");
    } else if (event.type === "blocked") {
      appendProgress(event.message || "诊断被数据源预检阻止", "error");
      toast(event.message || "诊断未启动", true);
    } else if (event.type === "report") {
      state.reportReceived = true;
      showReport(event.report || "");
      appendProgress("诊断报告已生成");
    } else if (event.type === "complete") {
      const report = event.diagnosis?.report || "";
      if (report && !state.reportReceived) showReport(report);
      appendProgress(event.message || "诊断流程完成");
    } else if (event.type === "error") {
      appendProgress(event.message || "诊断失败", "error");
      toast(event.message || "诊断失败", true);
    }
  }

  function appendProgress(message, type = "normal") {
    const list = $("aiopsProgress").querySelector(".progress-list");
    if (!list) return;
    const item = document.createElement("div");
    item.className = `progress-item ${type}`;
    item.textContent = message;
    list.appendChild(item);
  }

  function showReport(markdown) {
    state.lastDiagnosisReport = markdown || "";
    $("aiopsReport").hidden = false;
    $("aiopsReport").innerHTML = renderMarkdown(markdown || "未生成报告内容");
    prepareIncidentReview();
  }

  function prepareIncidentReview() {
    if (!state.lastDiagnosisReport) return;
    $("incidentReview").hidden = false;
    const service = state.lastDiagnosisMode === "manual"
      ? ($("manualService").value.trim() || "未指定服务")
      : "fault-lab";
    if (!$("memoryService").value.trim()) $("memoryService").value = service;
    if (!$("memoryIncidentTitle").value.trim()) $("memoryIncidentTitle").value = `${service} 故障复盘`;
  }

  function lines(value) {
    return String(value || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
  }

  async function confirmIncidentMemory() {
    if (state.incidentBusy || !state.lastDiagnosisReport) return;
    const payload = {
      session_id: state.lastDiagnosisSessionId,
      title: $("memoryIncidentTitle").value.trim(),
      service_name: $("memoryService").value.trim(),
      symptoms: lines($("memorySymptoms").value),
      alerts: lines($("memoryAlerts").value),
      key_evidence: lines($("memoryEvidence").value),
      confirmed_root_cause: $("memoryRootCause").value.trim(),
      resolution: $("memoryResolution").value.trim(),
      verification: $("memoryVerification").value.trim(),
      diagnosis_report: state.lastDiagnosisReport,
      source: "live_local",
      reviewer: "local_operator",
      confirmed: true,
    };
    if (!payload.title || !payload.service_name || !payload.symptoms.length || !payload.key_evidence.length || !payload.confirmed_root_cause || !payload.resolution || !payload.verification) {
      toast("请完整填写现象、证据、已确认根因、实际处置和恢复验证", true);
      return;
    }
    state.incidentBusy = true;
    $("memoryConfirm").disabled = true;
    $("memoryConfirm").textContent = "正在写入 Milvus…";
    try {
      const response = await fetch("/api/incidents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error(await apiError(response));
      const memory = await response.json();
      toast(`故障记忆 ${memory.incident_id} 已确认入库`);
      $("incidentReview").hidden = true;
      await loadIncidentMemories();
    } catch (error) {
      toast(error.message || "故障记忆入库失败", true);
    } finally {
      state.incidentBusy = false;
      $("memoryConfirm").disabled = false;
      $("memoryConfirm").textContent = "确认并写入故障记忆";
    }
  }

  async function loadIncidentMemories() {
    const list = $("memoryList");
    try {
      const response = await fetch("/api/incidents?limit=12", { cache: "no-store" });
      if (!response.ok) throw new Error(await apiError(response));
      const memories = await response.json();
      if (!memories.length) {
        list.innerHTML = '<div class="memory-empty">还没有已确认的故障记忆。完成一次诊断并经人工确认后会显示在这里。</div>';
        return;
      }
      list.innerHTML = memories.map((memory) => `
        <article class="memory-card">
          <div><span>${escapeHtml(memory.source)}</span><strong>${escapeHtml(memory.title)}</strong><small>${escapeHtml(memory.incident_id)} · ${escapeHtml(memory.service_name)}</small></div>
          <p><b>已确认根因：</b>${escapeHtml(memory.confirmed_root_cause)}</p>
          <p><b>已验证处置：</b>${escapeHtml(memory.resolution)}</p>
          <button type="button" data-delete-memory="${escapeHtml(memory.incident_id)}">删除错误记忆</button>
        </article>`).join("");
    } catch (error) {
      list.innerHTML = `<div class="memory-empty error">${escapeHtml(error.message || "故障记忆读取失败")}</div>`;
    }
  }

  async function deleteIncidentMemory(incidentId) {
    if (!window.confirm(`确定删除故障记忆 ${incidentId}？该操作会同时删除 Milvus 向量。`)) return;
    try {
      const response = await fetch(`/api/incidents/${encodeURIComponent(incidentId)}`, { method: "DELETE" });
      if (!response.ok) throw new Error(await apiError(response));
      toast("错误故障记忆已删除");
      await loadIncidentMemories();
    } catch (error) {
      toast(error.message || "删除失败", true);
    }
  }

  async function sendChat() {
    const input = $("chatInput");
    const question = input.value.trim();
    if (!question || state.chatBusy) return;
    state.chatBusy = true;
    $("chatSend").disabled = true;
    input.value = "";
    resizeInput();
    addChatMessage("user", question);
    const answerNode = addChatMessage("assistant", "", true);
    let answer = "";
    try {
      const response = await fetch("/api/chat_stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ Id: state.chatSession, Question: question }),
      });
      if (!response.ok) throw new Error(await apiError(response));
      await readSSE(response, (event) => {
        if (event.type === "content") {
          answer += extractText(event.data);
          answerNode.innerHTML = renderMarkdown(answer);
        } else if (event.type === "tool_call") {
          answerNode.dataset.status = "正在调用工具…";
        } else if (event.type === "error") {
          throw new Error(extractError(event.data));
        }
      });
      if (!answer) answerNode.textContent = "处理完成，但没有返回文本内容。";
    } catch (error) {
      answerNode.textContent = error.message || "对话请求失败";
      toast(error.message || "对话请求失败", true);
    } finally {
      state.chatBusy = false;
      $("chatSend").disabled = false;
      input.focus();
    }
  }

  function addChatMessage(role, text, streaming = false) {
    const article = document.createElement("article");
    article.className = `message ${role}`;
    if (role === "assistant") {
      const avatar = document.createElement("div");
      avatar.className = "avatar";
      avatar.textContent = "O";
      article.appendChild(avatar);
    }
    const body = document.createElement("div");
    body.className = "message-body";
    if (role === "assistant") {
      const label = document.createElement("span");
      label.className = "message-role";
      label.textContent = "对话 Agent";
      body.appendChild(label);
    }
    const content = document.createElement("div");
    content.className = "message-content";
    content.innerHTML = streaming && !text ? "<p>正在思考…</p>" : renderMarkdown(text);
    body.appendChild(content);
    article.appendChild(body);
    $("chatMessages").appendChild(article);
    article.scrollIntoView({ behavior: "smooth", block: "end" });
    return content;
  }

  function newChat() {
    state.chatSession = crypto.randomUUID();
    const messages = $("chatMessages");
    [...messages.querySelectorAll(".message:not(.welcome-card)")].forEach((node) => node.remove());
    toast("已创建新对话");
  }

  async function uploadFile(file) {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    $("uploadStatus").textContent = `正在上传 ${file.name}`;
    try {
      const response = await fetch("/api/upload", { method: "POST", body: form });
      if (!response.ok) throw new Error(await apiError(response));
      $("uploadStatus").textContent = `${file.name} 已加入知识库`;
      toast("文件上传并索引成功");
    } catch (error) {
      $("uploadStatus").textContent = "上传失败";
      toast(error.message || "文件上传失败", true);
    } finally {
      $("uploadInput").value = "";
    }
  }

  async function readSSE(response, onEvent) {
    if (!response.body) throw new Error("浏览器不支持流式响应");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done }).replace(/\r/g, "");
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() || "";
      for (const block of blocks) {
        const data = block.split("\n").filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart()).join("\n");
        if (!data) continue;
        onEvent(JSON.parse(data));
      }
      if (done) break;
    }
  }

  function renderMarkdown(markdown) {
    const source = String(markdown || "");
    if (!window.marked) return `<p>${escapeHtml(source).replace(/\n/g, "<br>")}</p>`;
    const parsed = window.marked.parse(source, { gfm: true, breaks: true });
    const doc = new DOMParser().parseFromString(`<div>${parsed}</div>`, "text/html");
    doc.querySelectorAll("script,style,iframe,object,embed,link,meta,form").forEach((node) => node.remove());
    doc.querySelectorAll("*").forEach((node) => {
      [...node.attributes].forEach((attr) => {
        const value = attr.value.trim().toLowerCase();
        if (attr.name.toLowerCase().startsWith("on") || (["href", "src"].includes(attr.name.toLowerCase()) && value.startsWith("javascript:"))) node.removeAttribute(attr.name);
      });
    });
    return doc.body.firstElementChild?.innerHTML || "";
  }

  function extractText(data) {
    if (typeof data === "string") return data;
    return data?.content || data?.text || "";
  }

  function extractError(data) {
    if (typeof data === "string") return data;
    return data?.message || "对话处理失败";
  }

  async function apiError(response) {
    try {
      const body = await response.json();
      const detail = body.detail;
      if (typeof detail === "string") return detail;
      if (Array.isArray(detail)) return detail.map((item) => item.msg).join("；");
      return detail?.message || body.message || `请求失败 (${response.status})`;
    } catch (_) {
      return `请求失败 (${response.status})`;
    }
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
  }

  let toastTimer;
  function toast(message, error = false) {
    const node = $("toast");
    node.textContent = message;
    node.className = `toast show${error ? " error" : ""}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { node.className = "toast"; }, 3200);
  }

  function resizeInput() {
    const input = $("chatInput");
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 150)}px`;
  }

  $("navChatAgent").addEventListener("click", () => switchView("chat"));
  $("navAiopsAgent").addEventListener("click", () => switchView("aiops"));
  $("sourceRefresh").addEventListener("click", refreshSources);
  $("faultScenarioSelect").addEventListener("change", renderFaultScenarioDetail);
  $("faultActivate").addEventListener("click", () => faultAction("activate"));
  $("faultRun").addEventListener("click", () => faultAction("run"));
  $("faultReset").addEventListener("click", () => faultAction("reset"));
  $("memoryRefresh").addEventListener("click", loadIncidentMemories);
  $("memoryConfirm").addEventListener("click", confirmIncidentMemory);
  $("memoryList").addEventListener("click", (event) => {
    const button = event.target.closest("[data-delete-memory]");
    if (button) deleteIncidentMemory(button.dataset.deleteMemory);
  });
  $("modeRealtime").addEventListener("click", () => setMode("realtime"));
  $("modeManual").addEventListener("click", () => setMode("manual"));
  $("realtimeRun").addEventListener("click", () => runAIOps("realtime"));
  $("manualRun").addEventListener("click", () => runAIOps("manual"));
  $("manualDescription").addEventListener("input", (event) => { $("manualCount").textContent = event.target.value.length; });
  $("chatSend").addEventListener("click", sendChat);
  $("chatInput").addEventListener("input", resizeInput);
  $("chatInput").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); sendChat(); } });
  $("newChatButton").addEventListener("click", newChat);
  $("uploadButton").addEventListener("click", () => $("uploadInput").click());
  $("uploadInput").addEventListener("change", (event) => uploadFile(event.target.files?.[0]));
  document.querySelectorAll("[data-prompt]").forEach((button) => button.addEventListener("click", () => { $("chatInput").value = button.dataset.prompt; resizeInput(); $("chatInput").focus(); }));
})();