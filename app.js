/**
 * WhatTheManDoing WebUI — public monitor + admin sheet.
 * Browser only talks to this WebUI; device tokens never leave the server.
 */

const state = {
  publicConfig: {
    page_title: "在干什么",
    page_subtitle: "What The Man Doing",
    refresh_interval_seconds: 5,
    show_history: true,
  },
  devices: [],
  selectedId: null,
  sessionToken: sessionStorage.getItem("wtmd_admin_session") || "",
  timer: null,
  eventSource: null,
  adminConfig: null,
};

const el = {
  pageTitle: document.getElementById("page-title"),
  pageSubtitle: document.getElementById("page-subtitle"),
  connPill: document.getElementById("conn-pill"),
  connLabel: document.getElementById("conn-label"),
  heroMeta: document.getElementById("hero-meta"),
  statOnline: document.getElementById("stat-online"),
  statTotal: document.getElementById("stat-total"),
  deviceGrid: document.getElementById("device-grid"),
  deviceCount: document.getElementById("device-count"),
  devicesEmpty: document.getElementById("devices-empty"),
  devicesError: document.getElementById("devices-error"),
  devicesErrorText: document.getElementById("devices-error-text"),
  historySection: document.getElementById("history-section"),
  historyDeviceLabel: document.getElementById("history-device-label"),
  timeline: document.getElementById("timeline"),
  historyEmpty: document.getElementById("history-empty"),
  adminModal: document.getElementById("admin-modal"),
  adminScrim: document.getElementById("admin-scrim"),
  adminOpen: document.getElementById("admin-open"),
  adminClose: document.getElementById("admin-close"),
  adminSubtitle: document.getElementById("admin-subtitle"),
  loginPanel: document.getElementById("login-panel"),
  dashPanel: document.getElementById("dash-panel"),
  loginToken: document.getElementById("login-token"),
  loginError: document.getElementById("login-error"),
  loginSubmit: document.getElementById("login-submit"),
  toast: document.getElementById("toast"),
};

function setConn(mode, label) {
  el.connPill.dataset.state = mode;
  el.connLabel.textContent = label;
}

function showToast(message) {
  el.toast.textContent = message;
  el.toast.hidden = false;
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => {
    el.toast.hidden = true;
  }, 2400);
}

function authHeaders() {
  return {
    Accept: "application/json",
    ...(state.sessionToken ? { Authorization: `Bearer ${state.sessionToken}` } : {}),
  };
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.admin ? authHeaders() : { Accept: "application/json" }),
      ...(options.headers || {}),
    },
  });
  let body = null;
  try {
    body = await res.json();
  } catch {
    body = { code: -1, message: `HTTP ${res.status}`, data: null };
  }
  return { res, body };
}

function formatTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function formatUpdated(epochSeconds) {
  if (!epochSeconds) return "—";
  return new Date(epochSeconds * 1000).toLocaleTimeString();
}

function statusBadge(device) {
  if (device.status === "paused") {
    return { cls: "warn", label: "已暂停" };
  }
  if (device.online && device.healthy !== false && !device.error) {
    return { cls: "ok", label: "在线" };
  }
  return { cls: "danger", label: device.error ? "异常" : "离线" };
}

function appLabel(device) {
  const app = device.app;
  if (!app) {
    if (device.status === "paused") return "已暂停分享";
    return "无前台应用";
  }
  return app.display_name || app.process_name || "未知应用";
}

function renderPublicConfig() {
  el.pageTitle.textContent = state.publicConfig.page_title || "在干什么";
  el.pageSubtitle.textContent = state.publicConfig.page_subtitle || "";
  document.title = state.publicConfig.page_title || "在干什么";
  el.historySection.hidden = !state.publicConfig.show_history;
}

function renderDevices() {
  const devices = state.devices || [];
  el.deviceGrid.innerHTML = "";
  const online = devices.filter((d) => d.online).length;
  el.statOnline.textContent = String(online);
  el.statTotal.textContent = String(devices.length);
  el.deviceCount.textContent = devices.length ? `${devices.length} 台` : "";
  el.heroMeta.textContent = devices.length
    ? `${online} / ${devices.length} 在线`
    : "暂无设备上报";

  if (!devices.length) {
    el.devicesEmpty.hidden = false;
    el.devicesError.hidden = true;
    return;
  }
  el.devicesEmpty.hidden = true;

  const fragment = document.createDocumentFragment();
  for (const device of devices) {
    const badge = statusBadge(device);
    const card = document.createElement("article");
    card.className = "device-card" + (state.selectedId === device.id ? " is-selected" : "");
    card.tabIndex = 0;
    card.dataset.id = device.id;
    card.innerHTML = `
      <div class="device-card-top">
        <div>
          <h3 class="device-name"></h3>
          <p class="device-id"></p>
        </div>
        <span class="badge ${badge.cls}"><span class="dot"></span><span class="badge-label"></span></span>
      </div>
      <p class="app-name"></p>
      <p class="app-meta"></p>
      <div class="health-row">
        <span>延迟 <strong class="latency"></strong></span>
        <span>HTTP <strong class="http"></strong></span>
        <span>更新 <strong class="updated"></strong></span>
      </div>
    `;
    card.querySelector(".device-name").textContent = device.name || device.id;
    card.querySelector(".device-id").textContent = device.id;
    card.querySelector(".badge-label").textContent = badge.label;
    card.querySelector(".app-name").textContent = appLabel(device);
    const meta = device.app?.window_title || device.timestamp;
    card.querySelector(".app-meta").textContent = meta
      ? (device.app?.window_title ? `标题：${device.app.window_title}` : `时间：${formatTime(device.timestamp)}`)
      : "";
    card.querySelector(".latency").textContent =
      device.latency_ms != null ? `${device.latency_ms}ms` : "—";
    card.querySelector(".http").textContent = device.http_status != null ? String(device.http_status) : "—";
    card.querySelector(".updated").textContent = formatUpdated(device.updated_at);
    card.addEventListener("click", () => selectDevice(device.id));
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        selectDevice(device.id);
      }
    });
    fragment.appendChild(card);
  }
  el.deviceGrid.appendChild(fragment);
}

async function loadPublicConfig() {
  const { body } = await api("/api/public/config");
  if (body?.code === 0 && body.data) {
    state.publicConfig = { ...state.publicConfig, ...body.data };
    renderPublicConfig();
  }
}

async function loadDevices() {
  const { body } = await api("/api/public/devices");
  if (body?.code !== 0) {
    el.devicesError.hidden = false;
    el.devicesErrorText.textContent = body?.message || "服务返回错误";
    setConn("offline", "异常");
    return;
  }
  el.devicesError.hidden = true;
  state.devices = body.data?.devices || [];
  renderDevices();
  setConn("online", "已连接");
}

function selectDevice(id) {
  state.selectedId = state.selectedId === id ? null : id;
  renderDevices();
  if (state.selectedId && state.publicConfig.show_history) {
    loadHistory(state.selectedId);
  } else {
    el.timeline.innerHTML = "";
    el.historyEmpty.hidden = false;
    el.historyDeviceLabel.textContent = "选择一台设备查看";
  }
}

async function loadHistory(deviceId) {
  el.historyDeviceLabel.textContent = deviceId;
  el.historyEmpty.hidden = true;
  el.timeline.innerHTML = `<li class="empty">加载中…</li>`;
  const { body } = await api(`/api/public/devices/${encodeURIComponent(deviceId)}/history?limit=20`);
  if (body?.code !== 0) {
    el.timeline.innerHTML = "";
    el.historyEmpty.hidden = false;
    el.historyEmpty.textContent = body?.message || "无法加载历史";
    return;
  }
  const history = body.data?.history || [];
  if (!history.length) {
    el.timeline.innerHTML = "";
    el.historyEmpty.hidden = false;
    el.historyEmpty.textContent = "暂无历史记录";
    return;
  }
  el.historyEmpty.hidden = true;
  el.timeline.innerHTML = "";
  for (const item of history) {
    const li = document.createElement("li");
    const left = document.createElement("span");
    left.className = "t-app";
    left.textContent = item.display_name || item.process_name || item.status || "—";
    const right = document.createElement("span");
    right.className = "t-time";
    right.textContent = formatTime(item.timestamp);
    li.append(left, right);
    el.timeline.appendChild(li);
  }
}

function stopStream() {
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
}

function startPolling() {
  stopStream();
  if (state.timer) clearInterval(state.timer);
  const ms = Math.max(1, state.publicConfig.refresh_interval_seconds || 5) * 1000;
  state.timer = setInterval(() => {
    loadDevices().catch(() => {});
  }, ms);
  loadDevices().catch(() => {});
}

function startStream() {
  stopStream();
  if (!window.EventSource) {
    startPolling();
    return;
  }
  const es = new EventSource("/api/public/stream");
  state.eventSource = es;
  es.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === "devices" && Array.isArray(msg.data)) {
        state.devices = msg.data;
        renderDevices();
        setConn("online", "实时");
      }
    } catch {
      /* ignore malformed frame */
    }
  };
  es.onerror = () => {
    es.close();
    state.eventSource = null;
    setConn("connecting", "重连中…");
    startPolling();
  };
}

/* ---------------- Admin ---------------- */

function openAdmin() {
  el.adminModal.hidden = false;
  if (state.sessionToken) {
    refreshAdminSession();
  } else {
    showLogin();
  }
}

function closeAdmin() {
  el.adminModal.hidden = true;
}

function showLogin() {
  el.loginPanel.hidden = false;
  el.dashPanel.hidden = true;
  el.adminSubtitle.textContent = "使用配置文件中的管理员 Token 登录";
  el.loginError.hidden = true;
}

function showDash() {
  el.loginPanel.hidden = true;
  el.dashPanel.hidden = false;
  el.adminSubtitle.textContent = "管理设备、WebUI 配置与访问统计";
  loadAdminAll();
}

async function refreshAdminSession() {
  const { res, body } = await api("/api/admin/me", { admin: true });
  if (res.status === 401 || body?.code === 40100) {
    state.sessionToken = "";
    sessionStorage.removeItem("wtmd_admin_session");
    showLogin();
    return;
  }
  if (body?.code === 0) showDash();
  else showLogin();
}

async function doLogin() {
  const token = el.loginToken.value.trim();
  el.loginError.hidden = true;
  const { res, body } = await api("/api/admin/login", {
    method: "POST",
    body: JSON.stringify({ token }),
    headers: { Accept: "application/json", "Content-Type": "application/json" },
  });
  if (body?.code === 0 && body.data?.session_token) {
    state.sessionToken = body.data.session_token;
    sessionStorage.setItem("wtmd_admin_session", state.sessionToken);
    el.loginToken.value = "";
    showDash();
    showToast("登录成功");
    return;
  }
  el.loginError.hidden = false;
  if (res.status === 403) {
    el.loginError.textContent = `IP 已被封禁${body?.data?.expires_at ? `至 ${body.data.expires_at}` : ""}`;
  } else if (res.status === 429) {
    el.loginError.textContent = "尝试过于频繁，请稍后再试";
  } else {
    el.loginError.textContent = body?.message || "登录失败";
  }
}

async function doLogout() {
  await api("/api/admin/logout", { method: "POST", admin: true });
  state.sessionToken = "";
  sessionStorage.removeItem("wtmd_admin_session");
  showLogin();
  showToast("已退出登录");
}

function bindTabs() {
  const tabs = document.querySelectorAll("#admin-tabs .tab");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => t.classList.remove("is-active"));
      tab.classList.add("is-active");
      const name = tab.dataset.tab;
      document.querySelectorAll(".tab-panel").forEach((panel) => {
        panel.hidden = panel.dataset.panel !== name;
      });
    });
  });
}

function fillSettingsForm(cfg) {
  state.adminConfig = cfg;
  document.getElementById("set-page-title").value = cfg.page_title || "";
  document.getElementById("set-page-subtitle").value = cfg.page_subtitle || "";
  document.getElementById("set-refresh").value = cfg.refresh_interval_seconds ?? 5;
  document.getElementById("set-per-page").value = cfg.devices_per_page ?? 12;
  document.getElementById("set-show-history").checked = !!cfg.show_history;
  document.getElementById("set-default-scheme").value = cfg.default_device_scheme || "http";
  document.getElementById("set-trust-proxy").checked = !!cfg.trust_proxy;
  document.getElementById("set-serve-mode").value = cfg.serve?.mode || "http";
  document.getElementById("set-serve-host").value = cfg.serve?.host || "127.0.0.1";
  document.getElementById("set-serve-port").value = cfg.serve?.port ?? 8080;
  document.getElementById("set-ssl-cert").value = cfg.serve?.ssl_certfile || "";
  document.getElementById("set-ssl-key").value = cfg.serve?.ssl_keyfile || "";
  document.getElementById("set-admin-token").value = "";
}

async function loadAdminConfig() {
  const { body } = await api("/api/admin/config", { admin: true });
  if (body?.code === 0) fillSettingsForm(body.data);
}

async function saveSettings() {
  const payload = {
    page_title: document.getElementById("set-page-title").value.trim(),
    page_subtitle: document.getElementById("set-page-subtitle").value.trim(),
    refresh_interval_seconds: Number(document.getElementById("set-refresh").value),
    devices_per_page: Number(document.getElementById("set-per-page").value),
    show_history: document.getElementById("set-show-history").checked,
    default_device_scheme: document.getElementById("set-default-scheme").value,
    trust_proxy: document.getElementById("set-trust-proxy").checked,
    serve: {
      mode: document.getElementById("set-serve-mode").value,
      host: document.getElementById("set-serve-host").value.trim(),
      port: Number(document.getElementById("set-serve-port").value),
      ssl_certfile: document.getElementById("set-ssl-cert").value.trim() || null,
      ssl_keyfile: document.getElementById("set-ssl-key").value.trim() || null,
    },
  };
  const newToken = document.getElementById("set-admin-token").value;
  if (newToken) payload.admin_token = newToken;

  const { body } = await api("/api/admin/config", {
    method: "PATCH",
    body: JSON.stringify(payload),
    admin: true,
  });
  if (body?.code === 0) {
    showToast("设置已保存");
    await loadPublicConfig();
    await loadAdminConfig();
    startStream();
  } else {
    showToast(body?.message || "保存失败");
  }
}

function renderAdminDevices(devices) {
  const root = document.getElementById("device-admin-list");
  root.innerHTML = "";
  if (!devices?.length) {
    root.innerHTML = `<p class="muted">尚未添加设备</p>`;
    return;
  }
  for (const device of devices) {
    const item = document.createElement("div");
    item.className = "device-admin-item";
    const health = device.health;
    const badge = health ? statusBadge(health) : { cls: "warn", label: "未采样" };
    item.innerHTML = `
      <div class="meta">
        <strong></strong>
        <span class="url"></span>
      </div>
      <div class="actions">
        <span class="badge ${badge.cls}"><span class="dot"></span><span class="badge-label"></span></span>
        <button type="button" class="btn btn-ghost btn-small" data-act="test">测试</button>
        <button type="button" class="btn btn-ghost btn-small" data-act="edit">编辑</button>
        <button type="button" class="btn btn-danger btn-small" data-act="del">删除</button>
      </div>
    `;
    item.querySelector("strong").textContent = `${device.name}（${device.id}）`;
    item.querySelector(".url").textContent = device.api_base_url;
    item.querySelector(".badge-label").textContent = badge.label;
    item.addEventListener("click", async (e) => {
      const btn = e.target.closest("button[data-act]");
      if (!btn) return;
      const act = btn.dataset.act;
      if (act === "test") await testDevice(device.id);
      if (act === "edit") openDeviceForm(device);
      if (act === "del") await deleteDevice(device.id);
    });
    root.appendChild(item);
  }
}

function openDeviceForm(device) {
  const form = document.getElementById("device-form");
  form.hidden = false;
  document.getElementById("device-form-title").textContent = device ? "编辑设备" : "添加设备";
  document.getElementById("dev-original-id").value = device?.id || "";
  document.getElementById("dev-id").value = device?.id || "";
  document.getElementById("dev-id").disabled = !!device;
  document.getElementById("dev-name").value = device?.name || "";
  document.getElementById("dev-url").value = device?.api_base_url || "";
  document.getElementById("dev-token").value = device?.viewer_token || "";
  document.getElementById("dev-enabled").checked = device ? !!device.enabled : true;
}

function closeDeviceForm() {
  document.getElementById("device-form").hidden = true;
  document.getElementById("dev-id").disabled = false;
}

async function loadAdminDevices() {
  const { body } = await api("/api/admin/devices", { admin: true });
  if (body?.code === 0) renderAdminDevices(body.data?.devices || []);
}

async function saveDevice() {
  const id = document.getElementById("dev-id").value.trim();
  const originalId = document.getElementById("dev-original-id").value.trim();
  const payload = {
    id,
    name: document.getElementById("dev-name").value.trim(),
    api_base_url: document.getElementById("dev-url").value.trim(),
    viewer_token: document.getElementById("dev-token").value,
    enabled: document.getElementById("dev-enabled").checked,
  };
  const path = originalId
    ? `/api/admin/devices/${encodeURIComponent(originalId)}`
    : "/api/admin/devices";
  const { body } = await api(path, {
    method: originalId ? "PUT" : "POST",
    body: JSON.stringify(payload),
    admin: true,
  });
  if (body?.code === 0) {
    showToast(originalId ? "设备已更新" : "设备已添加");
    closeDeviceForm();
    await loadAdminDevices();
  } else {
    showToast(body?.message || "保存设备失败");
  }
}

async function deleteDevice(id) {
  if (!window.confirm(`删除设备 ${id}？`)) return;
  const { body } = await api(`/api/admin/devices/${encodeURIComponent(id)}`, {
    method: "DELETE",
    admin: true,
  });
  if (body?.code === 0) {
    showToast("设备已删除");
    await loadAdminDevices();
  } else {
    showToast(body?.message || "删除失败");
  }
}

async function testDevice(id) {
  showToast("正在测试连接…");
  const { body } = await api(`/api/admin/devices/${encodeURIComponent(id)}/test`, {
    method: "POST",
    admin: true,
  });
  if (body?.code === 0) {
    const report = body.data || {};
    showToast(report.ok ? `连接成功 ${report.latency_ms ?? "?"}ms` : `失败：${report.error || "unknown"}`);
  } else {
    showToast(body?.message || "测试失败");
  }
}

async function exportDevices() {
  const { body } = await api("/api/admin/devices/export", { admin: true });
  if (body?.code !== 0) {
    showToast(body?.message || "导出失败");
    return;
  }
  const blob = new Blob([JSON.stringify(body.data, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "devices.json";
  a.click();
  URL.revokeObjectURL(a.href);
  showToast("已导出 devices.json");
}

async function loadStats() {
  const { body } = await api("/api/admin/stats", { admin: true });
  if (body?.code !== 0) return;
  const data = body.data || {};
  document.getElementById("stat-total-visits").textContent = String(data.total_visits || 0);
  const dayList = document.getElementById("day-list");
  dayList.innerHTML = "";
  const entries = Object.entries(data.visits_by_date || {}).sort((a, b) => b[0].localeCompare(a[0]));
  if (!entries.length) {
    dayList.innerHTML = `<p class="muted">暂无按日数据</p>`;
    return;
  }
  for (const [day, count] of entries) {
    const row = document.createElement("div");
    row.className = "day-row";
    row.innerHTML = `<span></span><strong></strong>`;
    row.querySelector("span").textContent = day;
    row.querySelector("strong").textContent = String(count);
    dayList.appendChild(row);
  }
}

async function loadBans() {
  const { body } = await api("/api/admin/bans", { admin: true });
  const root = document.getElementById("ban-list");
  root.innerHTML = "";
  const bans = body?.data?.bans || [];
  if (!bans.length) {
    root.innerHTML = `<p class="muted">当前无封禁</p>`;
    return;
  }
  for (const ban of bans) {
    const row = document.createElement("div");
    row.className = "ban-row";
    row.innerHTML = `
      <div><strong class="ip"></strong><div class="muted until"></div></div>
      <button type="button" class="btn btn-ghost btn-small">解封</button>
    `;
    row.querySelector(".ip").textContent = ban.ip;
    row.querySelector(".until").textContent = `至 ${ban.expires_at}`;
    row.querySelector("button").addEventListener("click", async () => {
      await api(`/api/admin/bans/${encodeURIComponent(ban.ip)}`, { method: "DELETE", admin: true });
      showToast(`已解封 ${ban.ip}`);
      loadBans();
    });
    root.appendChild(row);
  }
}

async function loadAudit() {
  const { body } = await api("/api/admin/audit?limit=50", { admin: true });
  const root = document.getElementById("audit-list");
  root.innerHTML = "";
  const entries = body?.data?.entries || [];
  if (!entries.length) {
    root.innerHTML = `<p class="muted">暂无审计记录</p>`;
    return;
  }
  for (const entry of entries.slice().reverse()) {
    const row = document.createElement("div");
    row.className = "audit-row";
    row.innerHTML = `<div><strong class="act"></strong> <span class="muted ts"></span></div><div class="detail"></div>`;
    row.querySelector(".act").textContent = entry.action;
    row.querySelector(".ts").textContent = entry.ts;
    row.querySelector(".detail").textContent = `${entry.ip}${entry.detail ? " · " + entry.detail : ""}`;
    root.appendChild(row);
  }
}

function loadAdminAll() {
  loadAdminConfig();
  loadAdminDevices();
  loadStats();
  loadBans();
  loadAudit();
}

function bindEvents() {
  el.adminOpen.addEventListener("click", openAdmin);
  el.adminClose.addEventListener("click", closeAdmin);
  el.adminScrim.addEventListener("click", closeAdmin);
  el.loginSubmit.addEventListener("click", doLogin);
  el.loginToken.addEventListener("keydown", (e) => {
    if (e.key === "Enter") doLogin();
  });
  document.getElementById("admin-logout").addEventListener("click", doLogout);
  document.getElementById("save-settings").addEventListener("click", saveSettings);
  document.getElementById("device-add").addEventListener("click", () => openDeviceForm(null));
  document.getElementById("device-cancel").addEventListener("click", closeDeviceForm);
  document.getElementById("device-form").addEventListener("submit", (e) => {
    e.preventDefault();
    saveDevice();
  });
  document.getElementById("export-devices").addEventListener("click", exportDevices);
  document.getElementById("refresh-bans").addEventListener("click", loadBans);
  document.getElementById("refresh-audit").addEventListener("click", loadAudit);
  bindTabs();
}

async function boot() {
  bindEvents();
  await loadPublicConfig();
  startStream();
  // Fallback refresh also keeps history-less pages feeling live if SSE is blocked
  if (state.timer) clearInterval(state.timer);
  state.timer = setInterval(() => {
    if (!state.eventSource) loadDevices().catch(() => {});
  }, Math.max(3, state.publicConfig.refresh_interval_seconds || 5) * 1000);
}

boot().catch((err) => {
  setConn("offline", "启动失败");
  console.error(err);
});
