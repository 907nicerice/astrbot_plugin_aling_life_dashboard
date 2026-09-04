const state = {
  refreshIntervalSeconds: 10,
  timer: null,
  continuityContent: null,
  continuityContentLoaded: false,
  memoryManager: null,
  selectedMemory: null,
  toastTimer: null,
};

const MEMORY_TYPE_LABELS = {
  small_memory: "小记忆",
  preference_memory: "偏好",
  relationship_memory: "关系边界",
  life_signal: "生活规律",
  project_context: "项目背景",
  context_summary: "上下文摘要",
};

const MEMORY_STATUS_LABELS = {
  active: "生效中",
  stale: "待检查",
  deprecated: "已归档",
};

const $ = (id) => document.getElementById(id);

function text(value, fallback = "-") {
  if (value === null || value === undefined || value === "") return fallback;
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function boolText(value) {
  return value ? "true" : "false";
}

function statusColor(value) {
  if (value === true || value === "running") return "green";
  if (value === false || value === "" || value === null || value === undefined) return "red";
  return "yellow";
}

function renderKV(container, rows) {
  container.innerHTML = "";
  for (const [key, value] of rows) {
    const dt = document.createElement("dt");
    dt.textContent = key;
    const dd = document.createElement("dd");
    dd.textContent = text(value);
    container.append(dt, dd);
  }
}

function rawDetails(label, value) {
  if (value === null || value === undefined || value === "" || (Array.isArray(value) && !value.length)) return "";
  return `<details class="raw-preview"><summary>${escapeHtml(label)}</summary><pre class="raw-block">${escapeHtml(JSON.stringify(value, null, 2))}</pre></details>`;
}

function metricCard(label, value, note = "") {
  return `<div class="mini-metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong>${note ? `<small>${escapeHtml(note)}</small>` : ""}</div>`;
}

function statePill(label, ok, neutral = false) {
  return `<span class="badge ${neutral ? "info" : ok ? "ok" : "warn"}">${escapeHtml(label)}</span>`;
}

function countdownText(value) {
  if (!value) return "暂无计划";
  const date = new Date(String(value).split(" ")[0]);
  if (Number.isNaN(date.getTime())) return text(value);
  const seconds = Math.max(0, Math.floor((date.getTime() - Date.now()) / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours >= 24) return `${Math.floor(hours / 24)} 天 ${hours % 24} 小时后`;
  if (hours) return `${hours} 小时 ${minutes} 分钟后`;
  return `${minutes} 分钟后`;
}

function compactText(value, limit = 180) {
  const clean = String(value || "").replace(/\s+/g, " ").trim();
  return clean.length > limit ? `${clean.slice(0, limit)}…` : clean;
}

function formatNextCheckZh(value) {
  const minutes = Number(value);
  if (!Number.isFinite(minutes)) return "暂无计划";
  if (minutes <= 0) return "即将检查";
  if (minutes < 60) return `${Math.ceil(minutes)} 分钟后`;
  const hours = Math.floor(minutes / 60);
  const rest = Math.round(minutes % 60);
  return rest ? `${hours} 小时 ${rest} 分钟后` : `${hours} 小时后`;
}

function historyResultLabel(value) {
  const key = String(value || "").toLowerCase();
  const labels = {
    skip: "已跳过",
    skipped: "已跳过",
    sent: "已发送",
    success: "成功",
    posted: "已发布",
    send_failed: "发送失败",
    failed: "失败",
    error: "错误",
  };
  return labels[key] || text(value, "状态未知");
}

function historyResultBadge(value) {
  const key = String(value || "").toLowerCase();
  const style = ["sent", "success", "posted"].includes(key)
    ? "ok"
    : ["send_failed", "failed", "error"].includes(key) ? "error" : "info";
  return `<span class="badge ${style}">${escapeHtml(historyResultLabel(value))}</span>`;
}

function windowLabel(value) {
  const labels = { noon: "午间", evening: "晚间", late_night: "深夜", morning: "上午", afternoon: "下午", night: "夜间" };
  return labels[String(value || "")] || text(value, "未命名");
}

function renderStatus(status) {
  const interval = Math.max(3, Number(status.refresh_interval_seconds) || 10);
  if (interval !== state.refreshIntervalSeconds) {
    state.refreshIntervalSeconds = interval;
    if (state.timer) clearInterval(state.timer);
    state.timer = setInterval(() => {
      loadAll().catch(showError);
    }, state.refreshIntervalSeconds * 1000);
  }

  const cards = [
    ["当前时间", formatDate(status.now), "green", "Dashboard 本地时间"],
    ["WebUI", status.webui === "running" ? "运行中" : status.webui, statusColor(status.webui), `每 ${state.refreshIntervalSeconds} 秒刷新`],
    ["生活状态", status.shared_life_context_found ? "已连接" : "未连接", statusColor(status.shared_life_context_found), "当天生活舞台"],
    ["空间链路", status.bridge_enabled ? "桥接已启用" : "桥接未启用", status.bridge_enabled ? "green" : "yellow", status.dry_run ? "演练模式，不会真实发送" : (status.qzone_bridge_found ? "正式模式" : "未发现桥接插件")],
    ["发送组件", status.qzone_auto_like_found ? "已发现" : "未发现", statusColor(status.qzone_auto_like_found), "QQ 空间执行端"],
  ];
  $("status-cards").innerHTML = cards.map(([label, value, color, note]) => `
    <article class="status-card ${color}">
      <div class="label">${escapeHtml(label)}</div>
      <div class="value">${escapeHtml(value)}</div>
      <div class="status-note">${escapeHtml(note)}</div>
    </article>
  `).join("");
}

function renderLife(life) {
  const plan = life.daily?.daily_plan || {};
  const labels = [["morning", "上午"], ["afternoon", "下午"], ["evening", "晚上"], ["night", "深夜"]];
  $("daily-plan").innerHTML = labels.map(([key, label]) => `
    <div class="plan-item"><b>${label}</b><span>${escapeHtml(text(plan[key]))}</span></div>
  `).join("");
  renderKV($("daily-meta"), [
    ["最近计划刷新", formatDate(life.daily?.last_daily_plan_refresh_at)],
    ["最近自动刷新", formatDate(life.daily?.last_auto_refresh_at)],
  ]);

  const stale = life.stale_warning || {};
  const warning = $("period-warning");
  if (stale.stale) {
    warning.textContent = stale.message || "Current period may be stale";
    warning.classList.remove("hidden");
  } else {
    warning.classList.add("hidden");
  }

  const period = life.period || {};
  renderKV($("period-state"), [
    ["当前时段", period.current_period],
    ["上一时段", period.last_period_key],
    ["最近刷新", formatDate(period.last_period_refresh_at)],
    ["当前活动", period.current_activity?.value],
    ["活动模式", period.current_activity?.mode],
    ["生活片段", period.micro_experience],
    ["环境氛围", period.ambient_mood],
    ["生活状态", period.life_state],
    ["精力", period.energy_level],
    ["活动提示", period.activity_hint],
    ["情绪提示", period.mood_hint],
    ["社交提示", period.social_hint],
    ["关系提示", period.relationship_hint],
  ]);

  const refresh = life.refresh || {};
  const schedule = refresh.period_refresh_times || {};
  const scheduleLabels = { morning: "上午", afternoon: "下午", evening: "晚上", night: "深夜" };
  $("refresh-countdown").innerHTML = `
    <div class="focus-row"><div><span class="kicker">下一次时段刷新</span><strong>${escapeHtml(countdownText(refresh.next_period_refresh_at))}</strong><small>${escapeHtml(formatDate(refresh.next_period_refresh_at))}</small></div>${statePill("自动刷新", true)}</div>
    <div class="mini-metrics">
      ${metricCard("每日计划", countdownText(refresh.next_daily_refresh_at), `固定 ${refresh.auto_refresh_time || "-"}`)}
      ${metricCard("时段节点", `${Object.keys(schedule).length} 个`, "按生活节奏刷新")}
    </div>
    <div class="schedule-strip">${["morning", "afternoon", "evening", "night"].filter((key) => schedule[key]).map((key) => `<span><b>${escapeHtml(scheduleLabels[key])}</b>${escapeHtml(schedule[key])}</span>`).join("")}</div>
    ${rawDetails("查看刷新配置", refresh)}
  `;
}

function renderQzone(qzone) {
  const bridge = qzone.bridge || {};
  const windows = bridge.post_windows || [];
  $("bridge-state").innerHTML = `
    <div class="focus-row"><div><span class="kicker">运行状态</span><strong>${bridge.enabled ? "桥接已启用" : "桥接未启用"}</strong><small>${bridge.dry_run ? "当前为演练模式，不会实际发送" : "当前允许进入真实发送链路"}</small></div>${statePill(bridge.dry_run ? "演练模式" : "正式模式", !bridge.dry_run, bridge.dry_run)}</div>
    <div class="mini-metrics">
      ${metricCard("今日发布", `${bridge.today_post_count || 0} / ${bridge.max_posts_per_day || 0}`, "已用 / 上限")}
      ${metricCard("检查间隔", `${bridge.check_interval_minutes || "-"} 分钟`, "后台调度频率")}
      ${metricCard("发布冷却", `${bridge.min_hours_between_posts || "-"} 小时`, "两次发布最短间隔")}
    </div>
    <div class="window-list">${windows.map((window) => `<div class="window-chip"><div><b>${escapeHtml(windowLabel(window.name))}</b><span>${escapeHtml(window.start || "-")}–${escapeHtml(window.end || "-")}</span></div><strong>${escapeHtml(formatPercent(window.probability))}</strong></div>`).join("") || `<div class="empty-inline">暂无发布窗口</div>`}</div>
    ${bridge.last_generated_text ? `<div class="soft-callout"><span>最近生成</span><p>${escapeHtml(compactText(bridge.last_generated_text, 220))}</p></div>` : ""}
    ${rawDetails("查看桥接原始数据", bridge)}
  `;

  const prediction = qzone.prediction || {};
  const nextWindow = prediction.next_window || {};
  const chance = String(prediction.chance_score || "Low");
  const chanceLabel = { Low: "较低", Medium: "中等", High: "较高" }[chance] || chance;
  $("trigger-prediction").innerHTML = `
    <div class="focus-row"><div><span class="kicker">当前机会</span><strong>${escapeHtml(chanceLabel)}</strong><small>${prediction.in_post_window ? `正处于 ${prediction.current_window || "发布"} 窗口` : "当前不在发布窗口"}</small></div>${statePill(prediction.in_post_window ? "窗口内" : "等待中", prediction.in_post_window, !prediction.in_post_window)}</div>
    <div class="probability-track"><span style="width:${Math.max(0, Math.min(100, Number(prediction.current_window_probability || 0) * 100))}%"></span></div>
    <div class="mini-metrics">
      ${metricCard("当前概率", formatPercent(prediction.current_window_probability), "仅表示命中机会")}
      ${metricCard("下次检查", formatNextCheckZh(prediction.next_check_eta_minutes ?? prediction.next_check_in_minutes_approx), "自动执行")}
    </div>
    ${Object.keys(nextWindow).length ? `<div class="soft-callout"><span>下个窗口 · ${escapeHtml(windowLabel(nextWindow.name))}</span><p>${escapeHtml(nextWindow.start || "-")}–${escapeHtml(nextWindow.end || "-")} · 概率 ${escapeHtml(formatPercent(nextWindow.probability))} · ${escapeHtml(nextWindow.minutes_until ?? "-")} 分钟后</p></div>` : `<div class="empty-inline">今天没有后续发布窗口</div>`}
    <div class="inline-status">${statePill(prediction.quota_available_today ? "今日有额度" : "今日额度已用完", prediction.quota_available_today)}${statePill(prediction.cooldown_satisfied ? "冷却已满足" : "冷却中", prediction.cooldown_satisfied)}</div>
    ${prediction.explanation ? `<p class="helper-text">${escapeHtml(prediction.explanation)}</p>` : ""}
    ${rawDetails("查看预测原始数据", prediction)}
  `;

  const health = qzone.health || {};
  const sendReady = Boolean(health.will_use_send_path);
  const healthError = bridge.last_error || health.last_error;
  $("send-health").innerHTML = `
    <div class="focus-row"><div><span class="kicker">链路判断</span><strong>${sendReady ? "可以发送" : "暂不发送"}</strong><small>${healthError ? escapeHtml(compactText(healthError, 120)) : "未检测到近期错误"}</small></div>${statePill(sendReady ? "就绪" : "未就绪", sendReady)}</div>
    <div class="health-checks">
      <div><span>主 Cookie</span>${statePill(health.cookie_configured ? "已配置" : "未配置", health.cookie_configured)}</div>
      <div><span>p_skey</span>${statePill(health.cookie_has_p_skey ? "可用" : "缺失", health.cookie_has_p_skey)}</div>
      <div><span>备用 Cookie</span>${statePill(health.fallback_cookie_configured ? "已配置" : "未配置", health.fallback_cookie_configured)}</div>
    </div>
    ${rawDetails("查看链路诊断", health)}
  `;

  const history = qzone.history || [];
  $("history-list").innerHTML = history.length ? history.map((item) => `
    <div class="history-item">
      <div class="history-head"><span>${escapeHtml(formatDate(item.at))}</span>${historyResultBadge(item.result)}</div>
      <div class="text">${escapeHtml(compactText(item.text, 180) || historyResultLabel(item.result))}</div>
      <div class="meta">${escapeHtml([item.kind === "scheduler" ? "自动调度" : item.kind, item.window ? windowLabel(item.window) : ""].filter(Boolean).join(" · "))}</div>
      ${item.detail ? rawDetails("查看执行详情", item.detail) : ""}
    </div>
  `).join("") : `<div class="empty-state simple-empty">暂无执行记录</div>`;

  const timeline = qzone.today_check_timeline || [];
  if (timeline.length) {
    $("history-list").insertAdjacentHTML("beforeend", `
      <div class="history-item">
        <div class="history-head"><span>今日检查时间线</span><span class="badge info">${timeline.length} 次</span></div>
        <div class="timeline-strip">${timeline.slice(-12).map((item) => `<span title="${escapeHtml(item.type || "")}">${escapeHtml(new Date(item.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }))}</span>`).join("")}</div>
        ${rawDetails("查看完整时间线", timeline)}
      </div>
    `);
  }
}

function renderMemory(memory) {
  const days = memory.days || [];
  $("memory-summary").innerHTML = `
    <div class="focus-row"><div><span class="kicker">内容健康</span><strong>${memory.overfit_warning ? "需要检查重复" : "状态自然"}</strong><small>${memory.enabled ? "生活碎片正在低频积累" : "生活记忆未启用"}</small></div>${statePill(memory.overfit_warning ? "可能重复" : "正常", !memory.overfit_warning)}</div>
    <div class="mini-metrics">
      ${metricCard("近 24h 重复率", formatPercent(memory.repeat_rate_24h), memory.overfit_warning ? "建议检查来源" : "处于正常范围")}
      ${metricCard("保留天数", `${days.length} 天`, "当前可读取")}
    </div>
    <div class="day-memory-list">${days.slice(0, 5).map((day) => `<div><span>${escapeHtml(day.date || "未知日期")}</span><p>${escapeHtml(compactText(day.micro_experience || day.text || day.carry_over_trace, 120) || "暂无生活片段")}</p></div>`).join("") || `<div class="empty-inline">暂无按日生活记忆</div>`}</div>
    ${rawDetails("查看生活记忆原始数据", memory)}
  `;
  const traces = memory.recent_traces || [];
  $("memory-traces").innerHTML = traces.length ? traces.map((trace) => `
    <div class="trace-item"><span class="trace-mark"></span>${escapeHtml(compactText(typeof trace === "object" ? (trace.text || trace.micro_experience || trace.carry_over_trace || JSON.stringify(trace)) : trace, 160))}</div>
  `).join("") : `<div class="empty-inline">暂无最近生活片段</div>`;
}

function renderHealth(health) {
  const strip = $("warning-strip");
  const warnings = health.warnings || [];
  if (!warnings.length) {
    strip.classList.add("hidden");
    strip.innerHTML = "";
    return;
  }
  strip.classList.remove("hidden");
  const warningLabels = { period_stale: "状态过期", bridge_errors: "桥接异常", memory_overfit: "记忆重复" };
  strip.innerHTML = warnings.map((item) => `<span class="pill ${item.level || "yellow"}">${escapeHtml(warningLabels[item.kind] || item.kind)}</span> ${escapeHtml(item.message)}`).join("<br>");
}

async function loadAll() {
  const [status, life, qzone, memory, health] = await Promise.all([
    fetchJson("/api/status"),
    fetchJson("/api/life"),
    fetchJson("/api/qzone"),
    fetchJson("/api/memory"),
    fetchJson("/api/health"),
  ]);
  renderStatus(status);
  renderLife(life);
  renderContinuity(status.continuity || {});
  renderQzone(qzone);
  renderMemory(memory);
  renderHealth(health);
  $("last-refresh").textContent = `最近刷新：${new Date().toLocaleString()}`;
  if (!state.continuityContentLoaded) {
    loadContinuityContent().catch(showError);
  }
  if (!state.memoryManager) {
    loadMemoryManager().catch(showMemoryManagerError);
  }
}

function renderContinuity(continuity) {
  const relationship = continuity.relationship || {};
  const health = continuity.health || {};
  const slc = continuity.slc || {};
  const inner = continuity.inner_continuity || {};
  const memory = continuity.aling_memory || {};

  setBadge("continuity-overall-badge", continuity.status || health.status || "unknown");
  setBadge("slc-badge", slc.status || "unknown");
  setBadge("inner-badge", inner.status || "unknown");
  setBadge("aling-memory-badge", memory.status || "unknown");
  setBadge("continuity-health-badge", health.status || "unknown");

  $("continuity-summary").textContent = relationship.summary || "-";
  $("continuity-health-summary").textContent = health.summary || "-";

  const layers = relationship.layers || [];
  $("continuity-layers").innerHTML = layers.length ? layers.map((layer) => `
    <div class="layer-item">
      <b>${escapeHtml(layer.name)}</b>
      <div>${escapeHtml(layer.role || "-")}</div>
      <div class="meta">reads: ${escapeHtml(text(layer.reads || []))}</div>
      <div class="meta">writes: ${escapeHtml(text(layer.writes || []))}</div>
    </div>
  `).join("") : `<div class="layer-item">No relationship data</div>`;

  const matrix = relationship.matrix || [];
  $("continuity-matrix").innerHTML = matrix.map((row) => `
    <tr>
      <td>${escapeHtml(row.module)}</td>
      <td>${escapeHtml(row.responsibility)}</td>
      <td>${escapeHtml(row.reads)}</td>
      <td>${escapeHtml(row.writes)}</td>
      <td>${escapeHtml(row.injection)}</td>
      <td>${escapeHtml(row.risk)}</td>
    </tr>
  `).join("");

  renderKV($("continuity-slc"), [
    ["detected", boolText(slc.detected)],
    ["status", slc.status],
    ["last_refresh_at", slc.last_refresh_at],
    ["current_period", slc.current_period],
    ["current_activity", slc.current_activity?.value],
    ["energy_level", slc.energy_level],
    ["ambient_mood", slc.ambient_mood],
    ["data_source", slc.data_source],
    ["last_error", slc.last_error],
    ["inner reads SLC", slc.read_by?.inner_continuity_message],
    ["aling_memory reads SLC", slc.read_by?.aling_memory_message],
  ]);

  renderKV($("inner-config"), [
    ["enabled", boolText(inner.config?.enabled)],
    ["inject_enabled", boolText(inner.config?.inject_enabled)],
    ["update_enabled", boolText(inner.config?.update_enabled)],
    ["use_llm_update", boolText(inner.config?.use_llm_update)],
    ["read_shared_life_context", boolText(inner.config?.read_shared_life_context)],
    ["max_injected_chars", inner.config?.max_injected_chars],
    ["default_ttl_minutes", inner.config?.default_ttl_minutes],
    ["flashback_cooldown_seconds", inner.config?.flashback_cooldown_seconds],
  ]);
  renderKV($("inner-metrics"), [
    ["state_file_count", inner.metrics?.state_file_count],
    ["latest_state_updated_at", inner.metrics?.latest_state_updated_at],
    ["active_state_count", inner.metrics?.active_state_count],
    ["expired_or_old_state_count", inner.metrics?.expired_or_old_state_count],
    ["total_residue_items", inner.metrics?.total_residue_items],
    ["total_micro_details", inner.metrics?.total_micro_details],
    ["total_flashback_candidates", inner.metrics?.total_flashback_candidates],
    ["latest summary", inner.latest_summary],
  ]);
  renderMiniList("inner-warnings", inner.warnings || [], inner.errors || []);

  renderKV($("aling-memory-config"), [
    ["enabled", boolText(memory.config?.enabled)],
    ["auto_extract_enabled", boolText(memory.config?.auto_extract_enabled)],
    ["context_summary_enabled", boolText(memory.config?.context_summary_enabled)],
    ["mirror_enabled", boolText(memory.config?.mirror_enabled)],
    ["recent_trace_enabled", boolText(memory.config?.recent_trace_enabled)],
    ["recent_trace_ttl_hours", memory.config?.recent_trace_ttl_hours],
    ["recent_trace_inject_max_items", memory.config?.recent_trace_inject_max_items],
    ["recent_trace_inject_max_chars", memory.config?.recent_trace_inject_max_chars],
    ["flashback_min_turn_gap", memory.config?.flashback_min_turn_gap],
    ["same_memory_min_hours", memory.config?.same_memory_min_hours],
    ["max_flashback_per_day", memory.config?.max_flashback_per_day],
  ]);
  renderKV($("aling-memory-metrics"), [
    ["memory_scope_count", memory.metrics?.memory_scope_count],
    ["memory_item_count", memory.metrics?.memory_item_count],
    ["candidate_count", memory.metrics?.candidate_count],
    ["mirror_slice_count", memory.metrics?.mirror_slice_count],
    ["summary_count", memory.metrics?.summary_count],
    ["recent_trace_scope_count", memory.metrics?.recent_trace_scope_count],
    ["recent_trace_item_count", memory.metrics?.recent_trace_item_count],
    ["flashback_state_count", memory.metrics?.flashback_state_count],
    ["latest_memory_updated_at", memory.metrics?.latest_memory_updated_at],
    ["latest_recent_trace_updated_at", memory.metrics?.latest_recent_trace_updated_at],
    ["type_distribution", memory.metrics?.type_distribution],
  ]);
  renderMiniList("aling-memory-warnings", memory.warnings || [], memory.errors || []);

  const checks = health.checks || [];
  $("continuity-health-checks").innerHTML = checks.length ? checks.map((check) => `
    <div class="check-item">
      <b>${escapeHtml(check.name)} <span class="badge ${statusClass(check.status)}">${escapeHtml(check.status)}</span></b>
      <div>${escapeHtml(check.message)}</div>
    </div>
  `).join("") : `<div class="check-item">No checks</div>`;
}

function renderMiniList(id, warnings, errors) {
  const items = [
    ...warnings.map((message) => ({ type: "warn", message })),
    ...errors.map((message) => ({ type: "error", message })),
  ];
  $(id).innerHTML = items.length ? items.map((item) => `
    <div class="mini-item"><span class="badge ${statusClass(item.type)}">${escapeHtml(statusLabel(item.type))}</span> ${escapeHtml(item.message)}</div>
  `).join("") : `<div class="mini-item"><span class="badge ok">正常</span> No warnings</div>`;
}

async function loadContinuityContent() {
  setBadge("inner-content-badge", "unknown");
  setBadge("memory-content-badge", "unknown");
  $("inner-content-list").innerHTML = emptyRecord("正在读取 Inner Continuity 内容...");
  $("memory-store-content").innerHTML = emptyRecord("正在读取 Aling Memory 内容...");
  $("continuity-read-diagnostics").innerHTML = emptyRecord("正在读取扫描诊断...");
  try {
    const [payload, debug] = await Promise.all([
      fetchJson("/api/continuity-content"),
      fetchJson("/api/continuity-debug").catch((error) => ({ ok: false, error: error?.message || String(error) })),
    ]);
    state.continuityContent = payload;
    state.continuityDebug = debug;
    state.continuityContentLoaded = true;
    renderContinuityContent(payload);
    renderReadDiagnostics(payload, debug);
  } catch (error) {
    setBadge("inner-content-badge", "error");
    setBadge("memory-content-badge", "error");
    const message = `连续性内容读取失败：${error?.message || error}`;
    $("inner-content-list").innerHTML = emptyRecord(message);
    $("memory-store-content").innerHTML = emptyRecord(message);
    $("continuity-read-diagnostics").innerHTML = emptyRecord(message);
    throw error;
  }
}

function renderContinuityContent(payload) {
  const inner = payload.inner_continuity || {};
  const memory = payload.aling_memory || {};

  setBadge("inner-content-badge", inner.status || "unknown");
  setBadge("memory-content-badge", memory.status || "unknown");
  renderDiagnostics("inner-content-diagnostics", inner);
  renderDiagnostics("memory-content-diagnostics", memory);

  const states = inner.states || [];
  $("inner-content-list").innerHTML = states.length ? states.map((item) => `
    <div class="content-record">
      <h4>会话 ${escapeHtml(item.scope || "-")} <span class="badge ${item.expired ? "warn" : "ok"}">${item.expired ? "expired" : "active"}</span></h4>
      <div class="meta">updated: ${escapeHtml(text(item.updated_at))} | ttl: ${escapeHtml(text(item.ttl_minutes))} min | file: ${escapeHtml(text(item.source_file))}</div>
      <div class="body">
mood_hint: ${escapeHtml(previewToText(item.mood_hint))}
residue:
${escapeHtml(previewToLines(item.residue))}
micro_details:
${escapeHtml(previewToLines(item.micro_details))}
flashback_candidates:
${escapeHtml(previewToLines(item.flashback_candidates))}
cooldown: ${escapeHtml(previewToText(item.cooldown))}
last_flashback_at: ${escapeHtml(previewToText(item.last_flashback_at))}
      </div>
      ${renderRawPreview("原始字段预览", item.raw_preview)}
    </div>
  `).join("") : renderModuleFallback(inner, "暂无结构化数据：未识别到 Inner Continuity 状态。");

  renderAlingMemoryContent();
}

function renderAlingMemoryContent() {
  const payload = state.continuityContent || {};
  const memory = payload.aling_memory || {};
  const query = ($("memory-search")?.value || "").trim().toLowerCase();
  const matches = (item) => !query || JSON.stringify(item).toLowerCase().includes(query);

  const memoryStore = (memory.memory_store || []).filter(matches);
  $("memory-store-content").innerHTML = memoryStore.length ? memoryStore.map((item, index) => `
    <div class="content-record">
      <h4>记忆 #${index + 1} ${escapeHtml(item.memory_id || "")}</h4>
      <div class="meta">scope: ${escapeHtml(text(item.scope))} | type: ${escapeHtml(text(item.type))} | importance: ${escapeHtml(text(item.importance))} | confidence: ${escapeHtml(text(item.confidence))}</div>
      <div class="body">${escapeHtml(item.content || "暂无")}</div>
      <div class="meta">created: ${escapeHtml(text(item.created_at))} | updated: ${escapeHtml(text(item.updated_at))} | source: ${escapeHtml(text(item.source))}</div>
      <div class="meta">tags: ${escapeHtml(previewToText(item.tags))}</div>
    </div>
  `).join("") : renderRawFallback(memory.memory_store_raw, query, "暂无 memory_store 结构化数据或搜索无结果。", memory);

  renderPreviewList("mirror-content", memory.user_life_mirror?.entries || [], query, "暂无 User Life Mirror 结构化数据。", memory);
  renderSummaryList("summary-content", (memory.context_summaries || []).filter(matches), memory.context_summaries_raw, query);
  renderRecentTraceList("recent-trace-content", (memory.recent_trace || []).filter(matches), memory.recent_trace_raw, query);
  renderFlashbackState("flashback-content", memory.flashback_state || {}, memory.flashback_state_raw, query);
}

function renderDiagnostics(id, payload) {
  const items = [
    ...(payload.warnings || []).map((message) => ({ type: "warn", message })),
    ...(payload.errors || []).map((message) => ({ type: "error", message })),
    ...(payload.diagnostics || []).slice(0, 8).map((message) => ({ type: "info", message })),
  ];
  $(id).innerHTML = items.length ? items.map((item) => `
    <div class="mini-item"><span class="badge ${statusClass(item.type)}">${escapeHtml(statusLabel(item.type))}</span> ${escapeHtml(item.message)}</div>
  `).join("") : "";
}

function renderPreviewList(id, entries, query, emptyMessage, modulePayload = null) {
  const rows = entries.filter((item) => !query || JSON.stringify(item).toLowerCase().includes(query));
  $(id).innerHTML = rows.length ? rows.map((item) => `
    <div class="content-record">
      <h4>${escapeHtml(item.key || "item")}</h4>
      <div class="body">${escapeHtml(item.value || "暂无")}</div>
    </div>
  `).join("") : renderModuleFallback(modulePayload, emptyMessage);
}

function renderSummaryList(id, entries, rawPreview, query) {
  $(id).innerHTML = entries.length ? entries.map((item) => `
    <div class="content-record">
      <h4>summary ${escapeHtml(item.scope || "-")}</h4>
      <div class="body">${escapeHtml(item.summary || "暂无")}</div>
      <div class="meta">created: ${escapeHtml(text(item.created_at))} | updated: ${escapeHtml(text(item.updated_at))} | expires: ${escapeHtml(text(item.expires_at))} | turns: ${escapeHtml(text(item.turn_count))}</div>
    </div>
  `).join("") : renderRawFallback(rawPreview, query, "暂无 context_summaries 结构化数据或搜索无结果。", state.continuityContent?.aling_memory);
}

function renderRecentTraceList(id, entries, rawPreview, query) {
  $(id).innerHTML = entries.length ? entries.map((item) => `
    <div class="content-record">
      <h4>recent_trace ${escapeHtml(item.scope || "-")}</h4>
      <div class="body">${escapeHtml(item.topic || "暂无")}</div>
      <div class="meta">importance: ${escapeHtml(text(item.importance))} | last_seen: ${escapeHtml(text(item.last_seen_at))} | expires: ${escapeHtml(text(item.expires_at))} | source: ${escapeHtml(text(item.source))}</div>
    </div>
  `).join("") : renderRawFallback(rawPreview, query, "暂无 recent_trace 结构化数据或搜索无结果。", state.continuityContent?.aling_memory);
}

function renderFlashbackState(id, stateData, rawPreview, query) {
  const rows = stateData.last_flashbacks?.entries || [];
  const rawFallback = renderRawFallback(rawPreview, query, "暂无 flashback_state 明细。");
  $(id).innerHTML = `
    <div class="content-record">
      <h4>flashback limits</h4>
      <div class="body">today_flashback_count: ${escapeHtml(text(stateData.today_flashback_count))}
same_memory_min_hours: ${escapeHtml(text(stateData.same_memory_min_hours))}
max_flashback_per_day: ${escapeHtml(text(stateData.max_flashback_per_day))}
flashback_min_turn_gap: ${escapeHtml(text(stateData.flashback_min_turn_gap))}</div>
    </div>
    ${rows.length ? rows.map((item) => `
      <div class="content-record"><h4>${escapeHtml(item.key)}</h4><div class="body">${escapeHtml(item.value)}</div></div>
    `).join("") : rawFallback}
  `;
}

function renderRawFallback(rawPreview, query, emptyMessage, modulePayload = null) {
  const rows = (rawPreview?.entries || []).filter((item) => !query || JSON.stringify(item).toLowerCase().includes(query));
  return rows.length ? rows.map((item) => `
    <div class="content-record">
      <h4>${escapeHtml(item.key || "item")}</h4>
      <div class="body">${escapeHtml(item.value || "暂无")}</div>
    </div>
  `).join("") : renderModuleFallback(modulePayload, emptyMessage);
}

function renderRawPreview(title, rawPreview) {
  const rows = rawPreview?.entries || [];
  if (!rows.length) return "";
  return `
    <details class="raw-preview">
      <summary>${escapeHtml(title)}</summary>
      <pre class="raw-block">${escapeHtml(rows.map((item) => `${item.key}: ${item.value}`).join("\n"))}</pre>
    </details>
  `;
}

function emptyRecord(message) {
  return `<div class="content-record empty-state"><div class="body">${escapeHtml(message || "暂无数据")}</div></div>`;
}

function renderModuleFallback(modulePayload, emptyMessage) {
  if (!modulePayload) return emptyRecord(emptyMessage);
  const parts = [emptyRecord(emptyMessage)];
  parts.push(renderScanSummary(modulePayload));
  if (modulePayload.raw_files?.length) {
    parts.push(renderRawFiles(modulePayload.raw_files));
  }
  return parts.join("");
}

function renderScanSummary(payload) {
  const lines = [
    `状态: ${text(payload.status)}`,
    `已扫描目录: ${(payload.searched_dirs || []).join("\n  ") || "暂无"}`,
    `已存在目录: ${(payload.existing_dirs || []).join("\n  ") || "暂无"}`,
    `已发现 JSON: ${(payload.discovered_files || payload.json_files || []).join("\n  ") || "暂无"}`,
    `可读 JSON: ${(payload.readable_files || []).join("\n  ") || "暂无"}`,
    `读取错误: ${(payload.errors || []).join("\n  ") || "暂无"}`,
  ];
  return `
    <details class="scan-details" open>
      <summary>扫描结果</summary>
      <pre class="raw-block">${escapeHtml(lines.join("\n"))}</pre>
    </details>
  `;
}

function renderRawFiles(rawFiles) {
  return rawFiles.slice(0, 5).map((file) => `
    <details class="raw-preview">
      <summary>Raw Preview: ${escapeHtml(file.path || "-")}</summary>
      <pre class="raw-block">mtime: ${escapeHtml(text(file.mtime))}

${escapeHtml((file.preview?.entries || []).slice(0, 50).map((item) => `${item.key}: ${item.value}`).join("\n") || "暂无 raw preview")}</pre>
    </details>
  `).join("");
}

function renderReadDiagnostics(payload, debug) {
  const inner = debug?.inner_continuity || payload.inner_continuity || {};
  const memory = debug?.aling_memory || payload.aling_memory || {};
  const expected = memory.expected_files ? Object.entries(memory.expected_files).map(([key, value]) => `${key}: ${value}`).join("\n") : "暂无";
  $("continuity-read-diagnostics").innerHTML = `
    <div class="content-record">
      <h4>Inner Continuity</h4>
      <div class="body">${escapeHtml([
        `searched_dirs: ${(inner.searched_dirs || []).length}`,
        `existing_dirs: ${(inner.existing_dirs || []).length}`,
        `json_files: ${(inner.json_files || inner.discovered_files || []).length}`,
        `readable_files: ${(inner.readable_files || []).length}`,
        `errors: ${(inner.errors || []).join(" | ") || "暂无"}`,
        "",
        "已发现 JSON:",
        ...((inner.json_files || inner.discovered_files || []).slice(0, 20)),
      ].join("\n"))}</div>
    </div>
    <div class="content-record">
      <h4>Aling Memory</h4>
      <div class="body">${escapeHtml([
        `searched_dirs: ${(memory.searched_dirs || []).length}`,
        `existing_dirs: ${(memory.existing_dirs || []).length}`,
        `json_files: ${(memory.json_files || memory.discovered_files || []).length}`,
        `readable_files: ${(memory.readable_files || []).length}`,
        `errors: ${(memory.errors || []).join(" | ") || "暂无"}`,
        "",
        "expected_files:",
        expected,
        "",
        "已发现 JSON:",
        ...((memory.json_files || memory.discovered_files || []).slice(0, 30)),
      ].join("\n"))}</div>
    </div>
  `;
}

function previewToText(preview) {
  if (!preview) return "暂无";
  if (preview.kind === "text") return preview.text || "暂无";
  if (preview.kind === "empty") return preview.text || "暂无";
  if (preview.kind === "list") return (preview.items || []).join(" / ") + (preview.truncated ? " ... [more]" : "");
  if (preview.kind === "dict") return (preview.items || []).map((item) => `${item.key}: ${item.value}`).join(" / ") + (preview.truncated ? " ... [more]" : "");
  return text(preview);
}

function previewToLines(preview) {
  if (!preview) return "- 暂无";
  if (preview.kind === "list") return (preview.items || []).map((item) => `- ${item}`).join("\n") || "- 暂无";
  if (preview.kind === "dict") return (preview.items || []).map((item) => `- ${item.key}: ${item.value}`).join("\n") || "- 暂无";
  return `- ${previewToText(preview)}`;
}

function setBadge(id, status) {
  const el = $(id);
  const value = status || "unknown";
  el.textContent = statusLabel(value);
  el.title = value;
  el.className = `badge ${statusClass(value)}`;
}

function statusLabel(status) {
  const value = String(status || "unknown").toLowerCase();
  const labels = {
    ok: "正常",
    running: "运行中",
    warn: "警告",
    degraded: "降级",
    error: "错误",
    risk: "风险",
    missing: "缺失",
    empty: "暂无数据",
    disabled: "已禁用",
    unknown: "未知",
    info: "信息",
  };
  return labels[value] || value;
}

function statusClass(status) {
  const value = String(status || "unknown").toLowerCase();
  if (["ok"].includes(value)) return "ok";
  if (["disabled", "missing", "unknown", "empty", "info"].includes(value)) return value;
  if (["warn", "degraded"].includes(value)) return value;
  if (["risk", "error"].includes(value)) return value;
  return "unknown";
}

async function loadMemoryManager() {
  const payload = await fetchJson("/api/memories");
  state.memoryManager = payload;
  populateMemoryScopes(payload.scopes || []);
  renderMemoryManager();
}

function populateMemoryScopes(scopes) {
  const filter = $("manager-scope-filter");
  const form = $("memory-form-scope");
  const filterValue = filter.value;
  const formValue = form.value;
  filter.innerHTML = `<option value="">全部会话</option>${scopes.map((scope) => `
    <option value="${escapeHtml(scope.ref)}">${escapeHtml(scope.label)} · ${escapeHtml(scope.item_count)} 条</option>
  `).join("")}`;
  form.innerHTML = scopes.map((scope) => `
    <option value="${escapeHtml(scope.ref)}">${escapeHtml(scope.label)} · ${escapeHtml(scope.item_count)} 条</option>
  `).join("");
  if ([...filter.options].some((option) => option.value === filterValue)) filter.value = filterValue;
  if ([...form.options].some((option) => option.value === formValue)) form.value = formValue;
}

function renderMemoryManager() {
  const payload = state.memoryManager || {};
  const query = ($("manager-memory-search").value || "").trim().toLowerCase();
  const scope = $("manager-scope-filter").value;
  const typeFilter = $("manager-type-filter").value;
  const statusFilter = $("manager-status-filter").value;
  const rows = (payload.items || []).filter((item) => {
    const haystack = `${item.content || ""} ${(item.tags || []).join(" ")} ${item.use_rule || ""}`.toLowerCase();
    return (!query || haystack.includes(query))
      && (!scope || item.scope_ref === scope)
      && (!typeFilter || item.type === typeFilter)
      && (!statusFilter || item.status === statusFilter);
  });

  $("memory-count").textContent = `共 ${rows.length} 条`;
  $("new-memory").disabled = !payload.editable || !(payload.scopes || []).length;
  const notice = $("memory-manager-notice");
  if (!payload.editable) {
    notice.textContent = "当前为只读模式。如需编辑，请在插件配置中开启 memory_edit_enabled。";
    notice.classList.remove("hidden");
  } else {
    notice.classList.add("hidden");
  }

  $("memory-manager-body").innerHTML = rows.length ? rows.map((item) => `
    <tr data-memory-id="${escapeHtml(item.id)}" data-scope-ref="${escapeHtml(item.scope_ref)}" tabindex="0">
      <td class="memory-content-cell"><strong>${escapeHtml(item.content || "暂无内容")}</strong><small>${escapeHtml(item.scope_label)} · ${escapeHtml(formatDate(item.updated_at))}</small></td>
      <td><span class="badge info">${escapeHtml(memoryTypeLabel(item.type))}</span></td>
      <td><div class="tag-list">${(item.tags || []).length ? item.tags.slice(0, 4).map((tag) => `<span class="tag-chip">${escapeHtml(tag)}</span>`).join("") : `<span class="meta">无标签</span>`}</div></td>
      <td><div class="confidence-bar"><span style="width:${Math.round(Number(item.confidence || 0) * 100)}%"></span></div><span class="confidence-text">${Math.round(Number(item.confidence || 0) * 100)}%</span></td>
      <td>${escapeHtml(item.last_used_at ? formatDate(item.last_used_at) : "尚未调用")}</td>
      <td><span class="badge ${memoryStatusClass(item)}">${escapeHtml(memoryStatusLabel(item))}</span></td>
    </tr>
  `).join("") : `<tr><td colspan="6" class="table-empty">没有符合条件的记忆</td></tr>`;
  renderMemoryCandidates(
    (payload.candidates || []).filter((item) => !scope || item.scope_ref === scope),
    Boolean(payload.editable),
  );
}

function renderMemoryCandidates(candidates, editable) {
  $("memory-candidate-count").textContent = `${candidates.length} 条`;
  $("memory-candidate-list").innerHTML = candidates.length ? candidates.map((item) => `
    <article class="content-item">
      <div class="card-heading"><strong>${escapeHtml(item.content || "暂无内容")}</strong><span class="badge info">${escapeHtml(memoryTypeLabel(item.suggested_type))}</span></div>
      <p>${escapeHtml(item.reason || "等待审核")}</p>
      <div class="meta">${escapeHtml(item.scope_label)} · 可信度 ${Math.round(Number(item.confidence || 0) * 100)}% · 重要性 ${Math.round(Number(item.importance || 0) * 100)}% · 稳定性 ${Math.round(Number(item.stability || 0) * 100)}% · 证据 ${escapeHtml(item.evidence_count || 1)} 次</div>
      <div class="candidate-actions">
        <button class="secondary-button candidate-reject" type="button" data-candidate-id="${escapeHtml(item.id)}" data-scope-ref="${escapeHtml(item.scope_ref)}" ${editable ? "" : "disabled"}>拒绝</button>
        <button class="primary-button candidate-approve" type="button" data-candidate-id="${escapeHtml(item.id)}" data-scope-ref="${escapeHtml(item.scope_ref)}" ${editable ? "" : "disabled"}>确认记住</button>
      </div>
    </article>
  `).join("") : `<div class="empty-state simple-empty">暂无候选记忆</div>`;
}

function memoryTypeLabel(type) {
  return MEMORY_TYPE_LABELS[type] || type || "未知";
}

function memoryStatusLabel(item) {
  if (item.expired) return "已过期";
  return MEMORY_STATUS_LABELS[item.status] || item.status || "未知";
}

function memoryStatusClass(item) {
  if (item.expired || item.status === "stale") return "warn";
  if (item.status === "active") return "ok";
  return "disabled";
}

function formatDate(value) {
  if (!value) return "-";
  const clean = String(value).replace(/\s+\([^)]*\)\s*$/, "");
  const date = new Date(clean);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString([], { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function findMemory(memoryId, scopeRef) {
  return (state.memoryManager?.items || []).find((item) => item.id === memoryId && item.scope_ref === scopeRef);
}

function openMemoryDrawer(item = null) {
  const editable = Boolean(state.memoryManager?.editable);
  state.selectedMemory = item;
  $("memory-form-title").textContent = item ? "编辑记忆" : "新增记忆";
  $("memory-form-id").value = item?.id || "";
  $("memory-form-scope").value = item?.scope_ref || $("manager-scope-filter").value || state.memoryManager?.scopes?.[0]?.ref || "";
  $("memory-form-scope").disabled = Boolean(item) || !editable;
  $("memory-form-content").value = item?.content || "";
  $("memory-form-type").value = item?.type || "small_memory";
  $("memory-form-tags").value = (item?.tags || []).join(", ");
  $("memory-form-use-rule").value = item?.use_rule || "";
  $("memory-form-tone").value = item?.tone || "";
  $("memory-form-confidence").value = item?.confidence ?? 0.8;
  $("memory-form-importance").value = item?.importance ?? 0.6;
  $("memory-form-stability").value = item?.stability ?? 0.6;
  $("memory-form-sensitivity").value = item?.sensitivity || "low";
  $("memory-form-ttl").value = item?.ttl_days || "";
  updateMemoryScoreOutputs();
  $("memory-form-meta").textContent = item ? `来源：${item.source || "-"} · 创建：${formatDate(item.created_at)} · 到期：${formatDate(item.expires_at)} · 证据 ${item.evidence_count || 1} 次 · 已调用 ${item.used_count || 0} 次` : "新增内容会写入所选会话的长期记忆库。";

  for (const control of $("memory-form").querySelectorAll("input:not([type=hidden]), textarea, select")) {
    if (control.id !== "memory-form-scope" || item) control.disabled = !editable;
  }
  const submit = $("memory-form").querySelector('button[type="submit"]');
  submit.textContent = item ? "保存修改" : "新增记忆";
  submit.classList.toggle("hidden", !editable);
  const archive = $("memory-form-archive");
  archive.classList.toggle("hidden", !item || !editable);
  if (item) {
    archive.textContent = item.status === "deprecated" ? "恢复" : "归档";
    archive.dataset.action = item.status === "deprecated" ? "restore" : "archive";
  }
  $("memory-drawer-backdrop").classList.remove("hidden");
  $("memory-drawer").classList.add("open");
  $("memory-drawer").setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
  setTimeout(() => {
    if (window.matchMedia("(min-width: 641px)").matches) {
      $("memory-form-content").focus();
    } else {
      $("memory-drawer").scrollTop = 0;
    }
  }, 100);
}

function closeMemoryDrawer() {
  $("memory-drawer").classList.remove("open");
  $("memory-drawer").setAttribute("aria-hidden", "true");
  $("memory-drawer-backdrop").classList.add("hidden");
  document.body.style.overflow = "";
  state.selectedMemory = null;
}

function updateMemoryScoreOutputs() {
  $("memory-confidence-output").value = `${Math.round(Number($("memory-form-confidence").value || 0) * 100)}%`;
  $("memory-importance-output").value = `${Math.round(Number($("memory-form-importance").value || 0) * 100)}%`;
  $("memory-stability-output").value = `${Math.round(Number($("memory-form-stability").value || 0) * 100)}%`;
}

function memoryFormPayload() {
  return {
    scope_ref: $("memory-form-scope").value,
    content: $("memory-form-content").value.trim(),
    type: $("memory-form-type").value,
    tags: $("memory-form-tags").value,
    use_rule: $("memory-form-use-rule").value.trim(),
    tone: $("memory-form-tone").value.trim(),
    confidence: Number($("memory-form-confidence").value),
    importance: Number($("memory-form-importance").value),
    stability: Number($("memory-form-stability").value),
    sensitivity: $("memory-form-sensitivity").value,
    ttl_days: $("memory-form-ttl").value ? Number($("memory-form-ttl").value) : null,
  };
}

async function saveMemory(event) {
  event.preventDefault();
  const item = state.selectedMemory;
  const url = item ? `/api/memories/${encodeURIComponent(item.id)}` : "/api/memories";
  const method = item ? "PATCH" : "POST";
  await fetchApi(url, { method, body: JSON.stringify(memoryFormPayload()) });
  closeMemoryDrawer();
  showToast(item ? "记忆已更新" : "记忆已新增");
  await refreshMemoryViews();
}

async function toggleMemoryArchive() {
  const item = state.selectedMemory;
  if (!item) return;
  const action = $("memory-form-archive").dataset.action || "archive";
  await fetchApi(`/api/memories/${encodeURIComponent(item.id)}/${action}`, {
    method: "POST",
    body: JSON.stringify({ scope_ref: item.scope_ref }),
  });
  closeMemoryDrawer();
  showToast(action === "restore" ? "记忆已恢复" : "记忆已归档");
  await refreshMemoryViews();
}

async function refreshMemoryViews() {
  await loadMemoryManager();
  state.continuityContentLoaded = false;
  await loadContinuityContent();
}

async function previewMemoryMatch() {
  const textValue = $("memory-preview-text").value.trim();
  const scopeRef = $("manager-scope-filter").value || state.memoryManager?.scopes?.[0]?.ref || "";
  const button = $("memory-preview-button");
  button.disabled = true;
  button.textContent = "正在匹配…";
  try {
    const payload = await fetchApi("/api/memories/preview", {
      method: "POST",
      body: JSON.stringify({ scope_ref: scopeRef, text: textValue }),
    });
    const matches = payload.matches || [];
    $("memory-preview-results").innerHTML = `
      <div class="match-heading"><strong>匹配到 ${matches.length} 条记忆</strong><span class="badge info">快速预览</span></div>
      ${matches.length ? matches.map((item, index) => `
        <div class="match-card"><strong>${index + 1}. ${escapeHtml(item.content)}</strong><p>${escapeHtml(memoryTypeLabel(item.type))} · ${escapeHtml(item.match_reason || "相关内容")} · 得分 ${escapeHtml(item.match_score)}</p></div>
      `).join("") : `<div class="empty-state simple-empty">暂未匹配到相关记忆</div>`}
      <p class="meta">${escapeHtml(payload.message || "")}</p>
    `;
  } finally {
    button.disabled = false;
    button.textContent = "测试匹配";
  }
}

function showMemoryManagerError(error) {
  state.memoryManager = { editable: false, scopes: [], items: [] };
  $("memory-manager-body").innerHTML = `<tr><td colspan="6" class="table-empty">${escapeHtml(error?.message || error || "记忆读取失败")}</td></tr>`;
  $("new-memory").disabled = true;
  const notice = $("memory-manager-notice");
  notice.textContent = "未找到可管理的记忆库，或当前记忆文件无法读取。";
  notice.classList.remove("hidden");
}

function showToast(message, isError = false) {
  const toast = $("dashboard-toast");
  toast.textContent = message;
  toast.className = `toast${isError ? " error" : ""}`;
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => toast.classList.add("hidden"), 2800);
}

async function fetchApi(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    headers["X-Requested-With"] = "AlingDashboard";
  }
  const response = await fetch(url, { credentials: "same-origin", ...options, headers });
  if (response.status === 401) {
    location.href = "/login";
    throw new Error("登录已过期");
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.message || `${url} HTTP ${response.status}`);
  }
  return payload;
}

async function fetchJson(url) {
  return fetchApi(url);
}

function showError(error) {
  const strip = $("warning-strip");
  strip.classList.remove("hidden");
  strip.textContent = `Dashboard 刷新失败：${error?.message || error}`;
}

function withCountdown(value) {
  if (!value) return "-";
  const iso = String(value).split(" ")[0];
  const target = new Date(iso);
  if (Number.isNaN(target.getTime())) return value;
  const seconds = Math.max(0, Math.floor((target.getTime() - Date.now()) / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return `${value}\n${hours}h ${minutes}m left`;
}

function formatPercent(value) {
  const number = Number(value || 0);
  return `${Math.round(number * 100)}%`;
}

function formatNextCheck(value) {
  if (value === null || value === undefined || value === "") return "-";
  return `about ${Number(value)} minutes`;
}

function escapeHtml(value) {
  return text(value, "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[char]));
}

loadAll().catch(showError);

$("refresh-continuity-content")?.addEventListener("click", () => {
  loadContinuityContent().catch(showError);
});

$("memory-search")?.addEventListener("input", () => {
  renderAlingMemoryContent();
});

for (const id of ["manager-memory-search", "manager-scope-filter", "manager-type-filter", "manager-status-filter"]) {
  $(id)?.addEventListener(id.includes("search") ? "input" : "change", renderMemoryManager);
}

$("memory-manager-body")?.addEventListener("click", (event) => {
  const row = event.target.closest("tr[data-memory-id]");
  if (!row) return;
  const item = findMemory(row.dataset.memoryId, row.dataset.scopeRef);
  if (item) openMemoryDrawer(item);
});

$("memory-manager-body")?.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const row = event.target.closest("tr[data-memory-id]");
  if (!row) return;
  event.preventDefault();
  const item = findMemory(row.dataset.memoryId, row.dataset.scopeRef);
  if (item) openMemoryDrawer(item);
});

$("new-memory")?.addEventListener("click", () => openMemoryDrawer());
$("memory-drawer-close")?.addEventListener("click", closeMemoryDrawer);
$("memory-form-cancel")?.addEventListener("click", closeMemoryDrawer);
$("memory-drawer-backdrop")?.addEventListener("click", closeMemoryDrawer);
for (const id of ["memory-form-confidence", "memory-form-importance", "memory-form-stability"]) {
  $(id)?.addEventListener("input", updateMemoryScoreOutputs);
}

async function decideMemoryCandidate(candidateId, scopeRef, action) {
  await fetchApi(`/api/memory-candidates/${encodeURIComponent(candidateId)}/${action}`, {
    method: "POST",
    body: JSON.stringify({ scope_ref: scopeRef }),
  });
  showToast(action === "approve" ? "候选已转为长期记忆" : "候选已拒绝");
  await refreshMemoryViews();
}
$("memory-form")?.addEventListener("submit", (event) => {
  saveMemory(event).catch((error) => showToast(error?.message || error, true));
});
$("memory-form-archive")?.addEventListener("click", () => {
  toggleMemoryArchive().catch((error) => showToast(error?.message || error, true));
});
$("memory-candidate-list")?.addEventListener("click", (event) => {
  const button = event.target.closest(".candidate-approve, .candidate-reject");
  if (!button) return;
  const action = button.classList.contains("candidate-approve") ? "approve" : "reject";
  decideMemoryCandidate(button.dataset.candidateId, button.dataset.scopeRef, action)
    .catch((error) => showToast(error?.message || error, true));
});
$("memory-preview-button")?.addEventListener("click", () => {
  previewMemoryMatch().catch((error) => showToast(error?.message || error, true));
});
$("refresh-dashboard")?.addEventListener("click", () => {
  Promise.all([loadAll(), loadMemoryManager(), loadContinuityContent()])
    .then(() => showToast("Dashboard 已刷新"))
    .catch(showError);
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && $("memory-drawer")?.classList.contains("open")) closeMemoryDrawer();
});

for (const link of document.querySelectorAll(".side-nav a")) {
  link.addEventListener("click", () => {
    for (const item of document.querySelectorAll(".side-nav a")) item.classList.remove("active");
    link.classList.add("active");
  });
}

state.timer = setInterval(() => {
  loadAll().catch(showError);
}, state.refreshIntervalSeconds * 1000);
