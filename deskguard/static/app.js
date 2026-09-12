"use strict";

// DeskGuard is a configuration-evidence workbench, not a host management agent.
const state = {
  user: null,
  catalog: null,
  spaces: [],
  baselines: [],
  assets: [],
  jobs: [],
  tickets: [],
  spaceId: null,
  view: "overview",
  selectedJob: null,
  generation: 0,
};
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const labels = {
  pass: "通过",
  fail: "不通过",
  unknown: "待补证",
  na: "不适用",
  disabled: "未启用",
  pending: "待执行",
  running: "执行中",
  completed: "已完成",
  failed: "失败",
  open: "待处理",
  in_progress: "处理中",
  resolved: "待验证",
  closed: "已关闭",
  active: "启用",
  archived: "已归档",
  high: "高",
  medium: "中",
};

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function shown(value) {
  if (value === null || value === undefined) return "未提供";
  if (value === true) return "是";
  if (value === false) return "否";
  return String(value);
}

function badge(status) {
  const known = Object.hasOwn(labels, status) ? status : "unknown";
  return `<span class="badge ${known}">${esc(labels[known])}</span>`;
}

function shortTime(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function notice(text, warning = false) {
  return `<div class="notice ${warning ? "warning" : ""}">${esc(text)}</div>`;
}

function pageHead(title, description, actions = "") {
  return `<div class="page-head">
    <div><h2>${esc(title)}</h2><p>${esc(description)}</p></div>
    <div class="actions">${actions}</div>
  </div>`;
}

function button(id, text, primary = false, disabled = false) {
  return `<button id="${esc(id)}" class="${primary ? "primary" : ""}"
    ${disabled ? "disabled" : ""}>${esc(text)}</button>`;
}

function empty(text) {
  return `<div class="empty">${esc(text)}</div>`;
}

function table(headers, rows) {
  return `<div class="table-wrap"><table>
    <thead><tr>${headers.map(x => `<th>${esc(x)}</th>`).join("")}</tr></thead>
    <tbody>${rows.join("")}</tbody>
  </table></div>`;
}

function tr(values) {
  return `<tr>${values.map(value => `<td>${value}</td>`).join("")}</tr>`;
}

function kv(items) {
  return `<dl class="kv">${items.map(([key, val]) =>
    `<dt>${esc(key)}</dt><dd>${esc(shown(val))}</dd>`).join("")}</dl>`;
}

function showError(error, target = "#dialog-error") {
  const element = $(target);
  element.hidden = false;
  element.textContent = error.message || String(error);
}

function toast(text) {
  const target = $("#toast");
  target.textContent = text;
  target.hidden = false;
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => { target.hidden = true; }, 2500);
}

async function api(path, options = {}) {
  const method = options.method || "GET";
  const headers = { ...options.headers };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && state.user) headers["X-CSRF-Token"] = state.user.csrf;
  const response = await fetch(path, {
    ...options,
    method,
    headers,
    credentials: "same-origin",
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401 && state.user) setLoggedOut();
    throw new Error(data.detail || `请求失败 ${response.status}`);
  }
  return data;
}

function on(selector, type, action, root = document) {
  const element = $(selector, root);
  if (!element) return;
  element.addEventListener(type, async event => {
    try {
      await action(event);
    } catch (error) {
      if ($("#editor").open) showError(error);
      else toast(error.message);
    }
  });
}

function setLoggedOut() {
  state.user = null;
  state.generation += 1;
  $("#editor").close();
  $("#app-shell").hidden = true;
  $("#login-screen").hidden = false;
  $("#password").value = "";
}

async function refreshLists() {
  [state.spaces, state.baselines, state.jobs, state.tickets] = await Promise.all([
    api("/api/spaces"),
    api("/api/baselines"),
    api("/api/jobs"),
    api("/api/tickets"),
  ]);
  if (!state.spaces.some(item => item.id === state.spaceId)) {
    state.spaceId = state.spaces[0]?.id || null;
  }
  state.assets = state.spaceId ? await api(`/api/assets?space_id=${state.spaceId}`) : [];
}

function spaceSelect(id = "space-select") {
  return `<select id="${id}" aria-label="选择管理域">
    ${state.spaces.map(item => `<option value="${item.id}"
      ${item.id === state.spaceId ? "selected" : ""}>
      ${esc(item.name)}${item.status === "archived" ? "（已归档）" : ""}
    </option>`).join("")}
  </select>`;
}

function baselineOptions(selected = "") {
  return state.baselines.map(item => `<option value="${item.id}"
    ${selected === item.id ? "selected" : ""}>${esc(item.name)}</option>`).join("");
}

function currentSpaceActive() {
  return state.spaces.find(item => item.id === state.spaceId)?.status === "active";
}

function bindSpaceSelector(view) {
  on("#space-select", "change", async event => {
    state.spaceId = event.target.value;
    await navigate(view);
  });
}

function dialog(title, body) {
  $("#dialog-title").textContent = title;
  $("#dialog-body").innerHTML = body;
  $("#dialog-error").hidden = true;
  const target = $("#editor");
  if (!target.open) target.showModal();
  target.scrollTop = 0;
}

function formField(id, label, value = "", type = "text", extra = "") {
  return `<div><label for="${id}">${esc(label)}</label>
    <input id="${id}" type="${type}" value="${esc(value)}" ${extra}></div>`;
}

function formActions(text = "保存") {
  return `<div class="form-actions">
    <button type="button" id="cancel-editor">取消</button>
    <button type="submit" class="primary" id="save-editor">${esc(text)}</button>
  </div>`;
}

function bindForm(handler) {
  on("#cancel-editor", "click", () => $("#editor").close());
  on("#edit-form", "submit", async event => {
    event.preventDefault();
    const save = $("#save-editor");
    save.disabled = true;
    $("#dialog-error").hidden = true;
    try {
      await handler();
    } finally {
      if (save.isConnected) save.disabled = false;
    }
  });
}

async function navigate(view) {
  state.view = view;
  const generation = ++state.generation;
  $$(".nav-button").forEach(item => {
    item.classList.toggle("active", item.dataset.view === view);
  });
  await refreshLists();
  if (generation !== state.generation || !state.user) return;
  const views = {
    overview: renderOverview,
    spaces: renderSpaces,
    assets: renderAssets,
    baselines: renderBaselines,
    jobs: renderJobs,
    tickets: renderTickets,
    compare: renderCompare,
    audit: renderAudit,
    system: renderSystem,
  };
  await (views[view] || renderOverview)();
  window.scrollTo(0, 0);
}

function metric(label, value, note = "") {
  return `<div class="metric"><span>${esc(label)}</span>
    <strong>${esc(value ?? "—")}</strong><small>${esc(note)}</small></div>`;
}

function summaryBlock(summary) {
  const counts = summary.counts;
  const total = Object.values(counts).reduce((a, b) => a + b, 0) || 1;
  return `<div class="summary-bar">${Object.entries(counts).map(([key, value]) =>
    `<div class="${key}" style="width:${100 * value / total}%"
      title="${esc(labels[key])}: ${value}"></div>`).join("")}</div>
    <div class="legend">${Object.entries(counts).map(([key, value]) =>
      `<span>${badge(key)}　${value}</span>`).join("")}</div>`;
}

async function renderOverview() {
  const data = await api("/api/overview");
  $("#content").innerHTML = pageHead(
    "工作概览", "登记配置证据，发现差异，跟踪整改，再用同一规则复测。",
    button("overview-refresh", "刷新"),
  ) + `<div class="card-grid">
    ${metric("管理域", data.spaces, "独立配置范围")}
    ${metric("登记资产", data.assets, "主机 / 控制器 / 桌面 / 终端")}
    ${metric("核查任务", data.jobs, "输入快照固定保存")}
    ${metric("未关闭工单", data.open_tickets, `其中逾期 ${data.overdue_tickets} 项`)}
  </div>
  <div class="card"><h3>最近一次已完成核查</h3>
    ${data.latest_summary ? summaryBlock(data.latest_summary) :
      '<p class="muted">尚未执行核查。先登记管理域和资产，再建立规则基线。</p>'}
  </div>
  <div class="two-col">
    <div class="card"><h3>建议工作顺序</h3>
      <p>① 管理域与资产登记<br>② 保存规则基线<br>③ 创建并执行核查<br>
      ④ 从不通过项建立工单<br>⑤ 更新配置并复测关闭</p>
      ${button("start-spaces", "进入管理域", true)}
    </div>
    <div class="card"><h3>证据边界</h3>
      <p>检查对象是用户录入或导入的配置记录。缺失配置标为“待补证”，
      不适用规则单独显示。评分不是系统安全等级。</p>
      <p class="muted">演示数据均为合成记录；不包含真实设备信息。</p>
    </div>
  </div>`;
  on("#overview-refresh", "click", () => navigate("overview"));
  on("#start-spaces", "click", () => navigate("spaces"));
}

function renderSpaces() {
  $("#content").innerHTML = pageHead(
    "管理域", "按办公区域或虚拟桌面环境组织资产；归档后停止新增与核查。",
    button("new-space", "新建管理域", true),
  ) + (state.spaces.length ? table(
    ["名称", "说明", "资产数", "配置集合版本", "状态", "操作"],
    state.spaces.map(item => tr([
      esc(item.name),
      esc(item.description),
      item.asset_count,
      item.revision,
      badge(item.status),
      `<button class="small" data-space="${item.id}">查看资产</button>
       <button class="small" data-state="${item.id}"
         data-next="${item.status === "active" ? "archived" : "active"}">
         ${item.status === "active" ? "归档" : "恢复启用"}</button>`,
    ])),
  ) : empty("暂无管理域。点击“新建管理域”开始。"));
  on("#new-space", "click", () => {
    dialog("新建管理域", `<form id="edit-form"><div class="form-grid">
      ${formField("space-name", "名称", "", "text", 'required minlength="2" maxlength="60"')}
      <div class="form-wide"><label for="space-desc">说明</label>
        <textarea id="space-desc" maxlength="500" placeholder="描述资产范围与配置来源"></textarea></div>
    </div>${formActions("创建管理域")}</form>`);
    bindForm(async () => {
      const created = await api("/api/spaces", { method: "POST", body: {
        name: $("#space-name").value,
        description: $("#space-desc").value,
      } });
      state.spaceId = created.id;
      $("#editor").close();
      await navigate("spaces");
      toast("管理域已创建");
    });
  });
  $$('[data-space]').forEach(element => on(
    `[data-space="${element.dataset.space}"]`, "click", async () => {
    state.spaceId = element.dataset.space;
    await navigate("assets");
  }));
  $$('[data-state]').forEach(element => on(
    `[data-state="${element.dataset.state}"]`, "click", async () => {
    await api(`/api/spaces/${element.dataset.state}`, {
      method: "PATCH", body: { status: element.dataset.next },
    });
    await navigate("spaces");
    toast("管理域状态已更新");
  }));
}

function configControls(config = {}) {
  return Object.entries(state.catalog.fields).map(([key, field]) => {
    const value = config[key];
    const input = field.type === "bool" ?
      `<select id="cfg-${key}" data-config="${key}">
        <option value="" ${value === undefined || value === null ? "selected" : ""}>未提供</option>
        <option value="true" ${value === true ? "selected" : ""}>是 / 启用</option>
        <option value="false" ${value === false ? "selected" : ""}>否 / 未启用</option>
      </select>` :
      `<input id="cfg-${key}" data-config="${key}" type="number"
        min="0" max="36500" step="1" value="${value ?? ""}" placeholder="留空为待补证">`;
    return `<div><label for="cfg-${key}">${esc(field.label)}</label>${input}</div>`;
  }).join("");
}

function readConfig() {
  const result = {};
  $$('[data-config]').forEach(element => {
    if (element.value === "") {
      result[element.dataset.config] = null;
    } else {
      const kind = state.catalog.fields[element.dataset.config].type;
      result[element.dataset.config] = kind === "bool" ?
        element.value === "true" : Number(element.value);
    }
  });
  return result;
}

function assetForm(asset = null) {
  const editable = !asset;
  const roleOptions = Object.entries(state.catalog.roles).map(([key, label]) =>
    `<option value="${key}" ${asset?.role === key ? "selected" : ""}>${esc(label)}</option>`).join("");
  dialog(asset ? "更新配置证据" : "登记资产", `<form id="edit-form">
    ${notice(asset ? "每次保存生成新配置版本，旧任务仍使用原快照。资产编号及角色不可更改。" :
      "只填写已核实的配置。不确定的字段保持未提供，不要为了评分填写为启用。")}
    <div class="form-grid">
      ${formField("asset-key", "资产编号", asset?.asset_key || "", "text",
        `required maxlength="40" ${editable ? "" : "disabled"}`)}
      ${formField("asset-name", "资产名称", asset?.name || "",
        "text", 'required minlength="2" maxlength="80"')}
      <div><label for="asset-role">角色</label>
        <select id="asset-role" ${editable ? "" : "disabled"}>${roleOptions}</select></div>
      ${formField("asset-owner", "负责人", asset?.owner || "", "text", 'required maxlength="60"')}
      ${formField("asset-zone", "所属区域", asset?.zone || "", "text", 'required maxlength="60"')}
    </div>
    <h3 style="margin-top:20px">配置证据</h3>
    <div class="config-grid">${configControls(asset?.config || {})}</div>
    ${formActions(asset ? "保存新版本" : "保存资产")}
  </form>`);
  bindForm(async () => {
    const body = {
      asset_key: $("#asset-key").value,
      name: $("#asset-name").value,
      role: $("#asset-role").value,
      owner: $("#asset-owner").value,
      zone: $("#asset-zone").value,
      config: readConfig(),
    };
    if (asset) body.expected_revision = asset.revision;
    else body.space_id = state.spaceId;
    await api(asset ? `/api/assets/${asset.id}` : "/api/assets", {
      method: asset ? "PUT" : "POST", body,
    });
    $("#editor").close();
    await navigate("assets");
    toast(asset ? "新配置版本已保存" : "资产已登记");
  });
}

async function assetDetails(key) {
  const asset = await api(`/api/assets/${key}`);
  dialog("资产配置详情", kv([
    ["资产编号", asset.asset_key], ["资产名称", asset.name],
    ["角色", state.catalog.roles[asset.role]], ["负责人", asset.owner],
    ["区域", asset.zone], ["当前配置版本", asset.revision],
  ]) + table(["配置项", "记录值", "字段"], Object.entries(state.catalog.fields).map(([key, field]) =>
    tr([esc(field.label), esc(shown(asset.config[key])), `<code>${key}</code>`]))) +
    `<div class="form-actions">${button("asset-history", "查看配置版本")}
      ${button("asset-edit", "更新配置", true, !currentSpaceActive())}</div>`);
  on("#asset-edit", "click", () => assetForm(asset));
  on("#asset-history", "click", async () => {
    const revisions = await api(`/api/assets/${key}/revisions`);
    dialog("配置版本记录", notice("每个版本保留输入内容和SHA-256摘要；历史核查任务不随更新改变。") +
      table(["版本", "时间", "操作者", "摘要校验"], revisions.map(row => tr([
        row.revision, esc(shortTime(row.created_at)), esc(row.actor), badge(row.valid ? "pass" : "fail"),
      ]))) + `<pre>${esc(JSON.stringify(revisions.map(row => ({
        revision: row.revision, content_hash: row.content_hash, config: row.payload.config,
      })), null, 2))}</pre>`);
  });
}

function importForm() {
  dialog("导入资产配置", `<form id="edit-form">
    ${notice("仅新增资产，整个文件校验成功后一次性写入；重复资产编号会使整批拒绝。单域最多200条。")}
    <div class="toolbar">${button("template-json", "下载JSON模板")}
      ${button("template-csv", "下载CSV模板")}</div>
    <label for="import-format">数据格式</label><select id="import-format">
      <option value="json">JSON资产数组</option><option value="csv">CSV固定表头</option></select>
    <label for="import-file">选择本地文件（或直接粘贴内容）</label>
    <input id="import-file" type="file" accept=".json,.csv">
    <label for="import-content">导入内容</label>
    <textarea id="import-content" class="mono" required style="width:100%;height:190px"
      maxlength="1000000" placeholder="粘贴模板格式的数据"></textarea>
    <div class="help">布尔值使用true/false；null或缺失值表示未提供。
      资产文本不能以公式触发字符开头。</div>
    ${formActions("校验并导入")}
  </form>`);
  ["json", "csv"].forEach(kind => on(`#template-${kind}`, "click", event => {
    event.preventDefault();
    window.location.assign(`/api/template/${kind}`);
  }));
  on("#import-file", "change", async event => {
    const file = event.target.files[0];
    if (!file) return;
    if (file.size > 1000000) throw new Error("文件超过1MB，请拆分后导入");
    $("#import-content").value = await file.text();
    $("#import-format").value = file.name.toLowerCase().endsWith(".csv") ? "csv" : "json";
  });
  bindForm(async () => {
    const rows = await api("/api/assets-import", { method: "POST", body: {
      space_id: state.spaceId,
      format: $("#import-format").value,
      content: $("#import-content").value,
    } });
    $("#editor").close();
    await navigate("assets");
    toast(`已导入 ${rows.length} 条资产`);
  });
}

function renderAssets() {
  if (!state.spaceId) {
    $("#content").innerHTML = pageHead("资产与配置", "先创建管理域。") +
      empty("暂无管理域，请先进入管理域页面。");
    return;
  }
  const active = currentSpaceActive();
  $("#content").innerHTML = pageHead(
    "资产与配置", "配置来源为人工录入或文件导入；系统不探测设备。",
    button("new-asset", "登记资产", true, !active) + button("import-assets", "导入", false, !active),
  ) + `<div class="toolbar">${spaceSelect()}
    ${button("demo-assets", "载入合成示例", false, !active)}
    ${button("assets-json", "导出JSON")}${button("assets-csv", "导出CSV")}
  </div>` + (!active ? notice("管理域已归档，历史配置仍可查阅和导出。", true) : "") +
    (state.assets.length ? table(
      ["资产编号 / 名称", "角色", "负责人", "区域", "配置版本", "操作"],
      state.assets.map(item => tr([
        `<strong>${esc(item.asset_key)}</strong><small>${esc(item.name)}</small>`,
        esc(state.catalog.roles[item.role]), esc(item.owner), esc(item.zone), item.revision,
        `<button class="small" data-asset="${item.id}">查看配置</button>`,
      ])),
    ) : empty("暂无资产。可以登记、导入，或载入明确标记的合成示例。"));
  bindSpaceSelector("assets");
  on("#new-asset", "click", () => assetForm());
  on("#import-assets", "click", importForm);
  on("#demo-assets", "click", async () => {
    const rows = await api("/api/demo-assets", { method: "POST", body: { space_id: state.spaceId } });
    await navigate("assets");
    toast(`已写入 ${rows.length} 条合成资产`);
  });
  ["json", "csv"].forEach(kind => on(`#assets-${kind}`, "click", () => {
    window.location.assign(`/api/spaces/${state.spaceId}/export/${kind}`);
  }));
  $$('[data-asset]').forEach(element => on(
    `[data-asset="${element.dataset.asset}"]`, "click",
    () => assetDetails(element.dataset.asset),
  ));
}

function baselineForm(source = null) {
  const rules = source?.rules || state.catalog.rules;
  const rows = rules.map(rule => {
    const field = state.catalog.fields[rule.field];
    const input = field.type === "bool" ?
      `<select data-threshold="${rule.id}">
        <option value="true" ${rule.expected === true ? "selected" : ""}>是</option>
        <option value="false" ${rule.expected === false ? "selected" : ""}>否</option>
      </select>` :
      `<input type="number" data-threshold="${rule.id}" value="${rule.expected}"
        min="1" max="36500" step="1" required>`;
    return `<tr class="rule-row">
      <td><input type="checkbox" data-enabled="${rule.id}" aria-label="启用${rule.id}"
        ${rule.enabled ? "checked" : ""}></td>
      <td><strong>${rule.id}</strong><small>${esc(rule.name)}</small></td>
      <td>${esc(rule.roles.map(x => state.catalog.roles[x]).join("、"))}</td>
      <td>${esc(rule.operator)}</td><td>${input}</td>
    </tr>`;
  });
  dialog(source ? "复制并调整规则基线" : "创建规则基线", `<form id="edit-form">
    ${notice("基线保存后不可原地修改。调整规则请复制为新基线；不同规则基线不能直接当作整改前后比较。")}
    <div class="form-grid">
      ${formField("baseline-name", "基线名称", source ? `${source.name}-副本` : "",
        "text", 'required minlength="2" maxlength="60"')}
      ${formField("baseline-desc", "说明", "自定义配置核查规则，不代表法定标准",
        "text", 'maxlength="500"')}
    </div>
    <div class="help">eq：等于；ge：大于等于；le：小于等于。空闲锁定0分钟视为未启用锁定。</div>
    ${table(["启用", "规则", "适用角色", "判断", "预期值"], rows)}
    ${formActions("保存基线")}
  </form>`);
  bindForm(async () => {
    const overrides = rules.map(rule => ({
      id: rule.id,
      enabled: $(`[data-enabled="${rule.id}"]`).checked,
      expected: state.catalog.fields[rule.field].type === "bool" ?
        $(`[data-threshold="${rule.id}"]`).value === "true" :
        Number($(`[data-threshold="${rule.id}"]`).value),
    }));
    await api("/api/baselines", { method: "POST", body: {
      name: $("#baseline-name").value,
      description: $("#baseline-desc").value,
      overrides,
    } });
    $("#editor").close();
    await navigate("baselines");
    toast("规则基线已保存，内容摘要已固定");
  });
}

function renderBaselines() {
  $("#content").innerHTML = pageHead(
    "规则基线", "12条应用内示例规则；阈值由用户决定，不映射法定测评条款。",
    button("new-baseline", "创建基线", true),
  ) + notice("布尔配置使用等值判断，周期字段使用上限或下限。缺失值不会被转换为false或0。") +
    (state.baselines.length ? table(
      ["名称 / 说明", "已启用规则", "创建时间", "内容摘要", "操作"],
      state.baselines.map(item => tr([
        `<strong>${esc(item.name)}</strong><small>${esc(item.description)}</small>`,
        `${item.rules.filter(rule => rule.enabled).length} / ${item.rules.length}`,
        esc(shortTime(item.created_at)),
        `<code title="${item.content_hash}">${item.content_hash.slice(0, 14)}…</code>`,
        `<button class="small" data-baseline="${item.id}">查看</button>
         <button class="small" data-copy-baseline="${item.id}">复制</button>`,
      ])),
    ) : empty("尚未建立基线。点击“创建基线”设置规则。"));
  on("#new-baseline", "click", () => baselineForm());
  state.baselines.forEach(item => {
    on(`[data-copy-baseline="${item.id}"]`, "click", () => baselineForm(item));
    on(`[data-baseline="${item.id}"]`, "click", () => {
      dialog("规则基线详情", kv([
        ["名称", item.name], ["说明", item.description], ["SHA-256", item.content_hash],
      ]) + table(["规则", "检查字段", "启用", "预期", "适用角色"], item.rules.map(rule => tr([
        `<strong>${rule.id}</strong><small>${esc(rule.name)}</small>`,
        esc(rule.field), badge(rule.enabled ? "active" : "disabled"),
        `${rule.operator} ${esc(shown(rule.expected))}`,
        esc(rule.roles.map(role => state.catalog.roles[role]).join("、")),
      ]))));
    });
  });
}

function jobForm() {
  dialog("创建核查任务", `<form id="edit-form">
    ${notice("点击创建时，系统固定当前资产集合和规则内容。此后修改资产不会改变该任务输入。")}
    <div class="form-grid">
      ${formField("job-name", "任务名称", "", "text", 'required minlength="2" maxlength="80"')}
      <div><label for="job-space">管理域</label>${spaceSelect("job-space")}</div>
      <div class="form-wide"><label for="job-baseline">核查基线</label>
        <select id="job-baseline" required>${baselineOptions()}</select></div>
    </div>
    ${formActions("创建快照任务")}
  </form>`);
  bindForm(async () => {
    const created = await api("/api/jobs", { method: "POST", body: {
      name: $("#job-name").value,
      space_id: $("#job-space").value,
      baseline_id: $("#job-baseline").value,
    } });
    $("#editor").close();
    state.selectedJob = created.id;
    await navigate("jobs");
    toast("任务快照已固定，可以执行核查");
  });
}

function renderJobs() {
  const available = state.spaces.some(x => x.status === "active") && state.baselines.length;
  $("#content").innerHTML = pageHead(
    "核查任务", "执行固定输入快照；已完成任务不可覆盖重跑，复测需新建任务。",
    button("new-job", "创建任务", true, !available),
  ) + (state.jobs.length ? table(
    ["任务名称", "管理域", "状态", "创建时间", "输入摘要", "操作"],
    state.jobs.map(item => tr([
      esc(item.name),
      esc(state.spaces.find(space => space.id === item.space_id)?.name),
      badge(item.status),
      esc(shortTime(item.created_at)),
      `<code>${item.snapshot_hash.slice(0, 10)}…</code>`,
      `<button class="small" data-job="${item.id}">查看</button>
       ${item.status === "pending" ?
         `<button class="small primary" data-run="${item.id}">执行</button>` : ""}`,
    ])),
  ) : empty("没有核查任务。先准备资产与基线，再创建任务。"));
  on("#new-job", "click", jobForm);
  state.jobs.forEach(item => {
    on(`[data-job="${item.id}"]`, "click", () => jobDetails(item.id));
    on(`[data-run="${item.id}"]`, "click", async event => {
      event.target.disabled = true;
      await api(`/api/jobs/${item.id}/execute`, { method: "POST" });
      await navigate("jobs");
      await jobDetails(item.id);
    });
  });
}

function findingsTable(job, status = "all") {
  const rows = job.result.findings.filter(item => status === "all" || item.status === status);
  return table(["资产", "规则", "结果", "实际 / 预期", "操作"], rows.map(item => tr([
    `<strong>${esc(item.asset_key)}</strong><small>配置v${item.asset_revision}</small>`,
    `<strong>${item.rule_id}</strong><small>${esc(item.rule_name)}</small>`,
    badge(item.status),
    `${esc(shown(item.observed))} / ${esc(shown(item.expected))}`,
    item.status === "fail" ? `<button class="small" data-ticket-asset="${item.asset_id}"
      data-ticket-rule="${item.rule_id}">建立工单</button>` :
      `<span class="help">${esc(item.reason)}</span>`,
  ])));
}

function bindTicketCreation(job) {
  $$('[data-ticket-asset]').forEach(element => {
    const asset = element.dataset.ticketAsset;
    const rule = element.dataset.ticketRule;
    on(`[data-ticket-asset="${asset}"][data-ticket-rule="${rule}"]`, "click", () => {
      const finding = job.result.findings.find(item => item.asset_id === asset && item.rule_id === rule);
      ticketForm(job, finding);
    });
  });
}

async function jobDetails(key, initialTab = "summary") {
  const job = await api(`/api/jobs/${key}`);
  state.selectedJob = key;
  const title = job.name;
  const summary = job.result?.summary;
  const tabs = [
    ["summary", "核查概览"],
    ["findings", "逐项结果"],
    ["evidence", "输入与证据"],
  ];
  const invalid = !job.snapshot_valid || (job.status === "completed" && !job.result_valid);
  const tabBody = tab => {
    if (tab === "evidence") {
      return kv([
        ["任务ID", job.id],
        ["配置集合版本", job.snapshot.space_revision],
        ["资产条数", job.snapshot.assets.length],
        ["输入SHA-256", job.snapshot_hash],
        ["基线SHA-256", job.snapshot.baseline.content_hash],
        ["结果SHA-256", job.result_hash],
        ["输入摘要一致", job.snapshot_valid],
        ["结果摘要一致", job.result_valid],
      ]) + `<pre>${esc(JSON.stringify(job.snapshot.assets.map(asset => ({
        asset_key: asset.asset_key, revision: asset.revision, config: asset.config,
      })), null, 2))}</pre>`;
    }
    if (tab === "findings") {
      return job.result ? `<div class="toolbar"><label for="finding-filter">结果筛选</label>
        <select id="finding-filter"><option value="all">全部</option>
          <option value="fail">不通过</option><option value="unknown">待补证</option>
          <option value="pass">通过</option><option value="na">不适用</option>
          <option value="disabled">未启用</option>
        </select></div><div id="finding-table">${findingsTable(job)}</div>` :
        empty("尚无结果，请先执行任务。");
    }
    return `${badge(job.status)}
      ${job.error ? notice(job.error, true) : ""}
      ${invalid ? notice("证据摘要异常，请停止引用本任务", true) : ""}
      ${summary ? `<div class="card-grid">
        ${metric("不通过项", summary.counts.fail, `高严重度 ${summary.high_failures}`)}
        ${metric("待补证项", summary.counts.unknown, "缺失值不算通过")}
        ${metric("证据完整率", `${summary.coverage_percent ?? "—"}%`, "已知 / 适用项")}
        ${metric("证据评分", summary.evidence_score, "不是产品安全等级")}
      </div>${summaryBlock(summary)}` : ""}
      ${kv([
        ["管理域", job.snapshot.space_name],
        ["基线", job.snapshot.baseline.name],
        ["创建时间", shortTime(job.created_at)],
        ["核查耗时", job.result ? `${job.result.elapsed_ms} ms` : "未执行"],
      ])}
      ${notice("结果仅针对填报配置。待补证不作为通过；不适用项不参与评分。")}
      <div class="toolbar">
        ${job.status === "pending" ? button("execute-detail", "执行核查", true) : ""}
        ${job.status === "completed" && !invalid ?
          button("report-html", "预览报告") + button("report-json", "导出JSON") +
          button("report-csv", "导出CSV") : ""}
      </div>`;
  };
  function draw(tab) {
    dialog(title, `<div class="tabs">${tabs.map(([key, label]) =>
      `<button data-tab="${key}" class="${key === tab ? "selected" : ""}">
        ${label}</button>`).join("")}</div>
      <div id="job-tab-body">${tabBody(tab)}</div>`);
    tabs.forEach(([key]) => on(`[data-tab="${key}"]`, "click", () => draw(key)));
    on("#finding-filter", "change", event => {
      $("#finding-table").innerHTML = findingsTable(job, event.target.value);
      bindTicketCreation(job);
    });
    if (job.result) bindTicketCreation(job);
    on("#execute-detail", "click", async event => {
      event.target.disabled = true;
      await api(`/api/jobs/${job.id}/execute`, { method: "POST" });
      await navigate("jobs");
      await jobDetails(job.id);
    });
    on("#report-html", "click", () =>
      window.open(`/api/jobs/${job.id}/export/html`, "_blank", "noopener"));
    ["json", "csv"].forEach(kind => on(`#report-${kind}`, "click", () => {
      window.location.assign(`/api/jobs/${job.id}/export/${kind}`);
    }));
  }
  draw(initialTab);
}

function ticketForm(job, finding) {
  dialog("建立整改工单", `<form id="edit-form">
    ${notice(`${finding.asset_key} · ${finding.rule_id} · ${finding.rule_name}`, true)}
    <p>${esc(finding.guidance)}</p>
    <div class="form-grid">
      ${formField("ticket-owner", "整改负责人", "", "text", 'required maxlength="60"')}
      ${formField("ticket-due", "计划完成日期", "", "date", "required")}
      <div class="form-wide"><label for="ticket-note">问题说明</label>
        <textarea id="ticket-note" maxlength="1000">${esc(finding.reason)}</textarea></div>
    </div>${formActions("创建工单")}
  </form>`);
  bindForm(async () => {
    await api("/api/tickets", { method: "POST", body: {
      job_id: job.id,
      asset_id: finding.asset_id,
      rule_id: finding.rule_id,
      owner: $("#ticket-owner").value,
      due_date: $("#ticket-due").value,
      note: $("#ticket-note").value,
    } });
    $("#editor").close();
    await navigate("tickets");
    toast("工单已创建；关闭需要更新后的配置复测通过");
  });
}

async function ticketDetails(key) {
  const ticket = await api(`/api/tickets/${key}`);
  const asset = await api(`/api/assets/${ticket.asset_id}`);
  const stages = {
    open: [["in_progress", "开始处理"]],
    in_progress: [["resolved", "提交待验证"], ["open", "退回待处理"]],
    resolved: [["in_progress", "返回处理中"]],
    closed: [],
  };
  const finished = state.jobs.filter(job =>
    job.status === "completed" && job.space_id === ticket.space_id);
  dialog("整改工单详情", `${badge(ticket.status)}
    ${kv([
      ["工单标题", ticket.title], ["资产", asset.asset_key],
      ["规则", ticket.rule_id], ["负责人", ticket.owner],
      ["计划完成", ticket.due_date], ["处理说明", ticket.note],
      ["验证任务", ticket.verify_job_id || "尚未完成验证"],
    ])}
    ${ticket.status !== "closed" ? `<form id="ticket-action-form">
      <label for="action-note">处理 / 验证说明（至少5个字符）</label>
      <textarea id="action-note" minlength="5" maxlength="1000" required
        style="width:100%" placeholder="记录实际完成的工作或验证结论"></textarea>
      ${ticket.status === "resolved" ? `<label for="verify-job">选择更新配置后的复测任务</label>
        <select id="verify-job" style="width:100%">${finished.map(job =>
          `<option value="${job.id}">${esc(job.name)}</option>`).join("")}</select>` : ""}
      <div class="form-actions">
        ${stages[ticket.status].map(([status, text]) =>
          `<button type="submit" data-transition="${status}">${text}</button>`).join("")}
        ${ticket.status === "resolved" ?
          '<button type="submit" id="verify-ticket" class="primary">验证并关闭</button>' : ""}
      </div>
    </form>` : notice("关闭依据已记录。原始核查结果保持不变，不被改写为通过。")}
    <h3>处理轨迹</h3><div class="timeline">${ticket.events.map(event =>
      `<div><strong>${esc(event.action)}</strong>
        <small>${esc(shortTime(event.created_at))} · ${esc(event.actor)}</small>
        <p>${esc(event.detail.note || "创建工单并关联原始不通过项")}</p></div>`).join("")}</div>`);
  on("#ticket-action-form", "submit", async event => {
    event.preventDefault();
    const note = $("#action-note").value;
    if (note.trim().length < 5) throw new Error("请填写至少5个字符的说明");
    const submitter = event.submitter;
    if (submitter?.id === "verify-ticket") {
      await api(`/api/tickets/${key}/verify`, { method: "POST", body: {
        job_id: $("#verify-job").value, note,
      } });
    } else {
      await api(`/api/tickets/${key}/transition`, { method: "POST", body: {
        status: submitter.dataset.transition, note,
      } });
    }
    await refreshLists();
    await renderTickets();
    await ticketDetails(key);
    toast("工单状态已更新");
  });
}

function renderTickets() {
  const rows = state.tickets;
  $("#content").innerHTML = pageHead(
    "整改工单", "待处理 → 处理中 → 待验证 → 复测通过后关闭。原始问题证据不变。",
    button("tickets-export", "导出工单"),
  ) + `<div class="toolbar"><select id="ticket-filter" aria-label="筛选工单">
      <option value="all">全部工单</option><option value="open">待处理</option>
      <option value="in_progress">处理中</option><option value="resolved">待验证</option>
      <option value="closed">已关闭</option><option value="overdue">逾期未关闭</option>
    </select></div><div id="tickets-table"></div>`;
  function draw(status) {
    const selected = rows.filter(row => status === "all" ||
      (status === "overdue" ? row.overdue : row.status === status));
    $("#tickets-table").innerHTML = selected.length ? table(
      ["问题 / 资产", "级别", "负责人", "计划完成", "状态", "操作"],
      selected.map(row => tr([
        `<strong>${esc(row.title)}</strong><small>${esc(row.asset_key)} · ${row.rule_id}</small>`,
        badge(row.severity), esc(row.owner),
        `${esc(row.due_date)}${row.overdue ? '<small>已逾期</small>' : ""}`,
        badge(row.status), `<button class="small" data-ticket="${row.id}">处理与验证</button>`,
      ])),
    ) : empty("暂无符合条件的工单。请从任务不通过项创建工单。");
    selected.forEach(row => on(`[data-ticket="${row.id}"]`, "click", () => ticketDetails(row.id)));
  }
  draw("all");
  on("#ticket-filter", "change", event => draw(event.target.value));
  on("#tickets-export", "click", () => window.location.assign("/api/tickets-export"));
}

function renderCompare() {
  const jobs = state.jobs.filter(item => item.status === "completed");
  const options = jobs.map(item => `<option value="${item.id}">${esc(item.name)}</option>`).join("");
  $("#content").innerHTML = pageHead(
    "复测对比", "限定同管理域、同规则内容；新增或缺少资产检查项单独统计。",
  ) + notice("降低阈值、停用规则或更换管理域，不能充当原问题整改通过。复测配置版本不能早于原始版本。") +
    `<div class="card"><form id="compare-form"><div class="form-grid">
      <div><label for="compare-before">原始核查任务</label>
        <select id="compare-before" required>${options}</select></div>
      <div><label for="compare-after">复测任务</label>
        <select id="compare-after" required>${options}</select></div>
    </div><div class="form-actions"><button class="primary" id="compare-submit"
      ${jobs.length < 2 ? "disabled" : ""}>对比结果</button></div></form>
    <div id="compare-error" class="error" hidden></div></div>
    <div id="compare-result"></div>`;
  on("#compare-form", "submit", async event => {
    event.preventDefault();
    $("#compare-error").hidden = true;
    try {
      const result = await api("/api/compare", { method: "POST", body: {
        before: $("#compare-before").value,
        after: $("#compare-after").value,
      } });
      $("#compare-result").innerHTML = `<div class="card-grid">
        ${metric("已修复", result.fixed, "不通过 → 通过")}
        ${metric("新增不通过", result.regressed, "共同检查项中")}
        ${metric("共同检查项", result.common_checks, "同资产与规则")}
        ${metric("新增 / 缺少项", `${result.added_checks} / ${result.removed_checks}`, "不计为修复")}
      </div>` + (result.transitions.length ? table(
        ["资产", "规则", "原结果", "复测结果", "记录值变化"],
        result.transitions.map(item => tr([
          esc(item.asset_key), `${item.rule_id}<small>${esc(item.rule_name)}</small>`,
          badge(item.before), badge(item.after),
          `${esc(shown(item.before_value))} → ${esc(shown(item.after_value))}`,
        ])),
      ) : empty("共同检查项的状态没有变化。"));
    } catch (error) {
      $("#compare-result").innerHTML = "";
      showError(error, "#compare-error");
    }
  });
}

async function renderAudit() {
  $("#content").innerHTML = pageHead(
    "操作审计", "展示最近最多1000条；本地摘要链可检查记录一致性，不是外部防篡改存证。",
    button("audit-verify", "校验本地审计链") + button("audit-export", "导出审计"),
  ) + `<div class="toolbar"><select id="audit-filter" aria-label="动作筛选">
    <option value="">全部动作</option><option value="asset.">资产操作</option>
    <option value="job.">任务核查</option><option value="ticket.">整改工单</option>
    <option value="auth.">账号会话</option><option value="export.">导出记录</option>
  </select>${button("audit-refresh", "刷新")}</div>
  <div id="audit-verification"></div><div id="audit-table"></div>`;
  async function load() {
    const rows = await api(`/api/audit?action=${encodeURIComponent($("#audit-filter").value)}`);
    $("#audit-table").innerHTML = table(
      ["序号", "时间", "操作者", "动作", "对象", "详情"],
      rows.map(row => tr([
        row.seq, esc(shortTime(row.timestamp)), esc(row.actor),
        `<code>${esc(row.action)}</code>`,
        `<code>${esc(row.entity)}</code>`,
        `<button class="small" data-audit="${row.seq}">查看</button>`,
      ])),
    );
    rows.forEach(row => on(`[data-audit="${row.seq}"]`, "click", () => {
      dialog("审计记录", kv([
        ["序号", row.seq], ["动作", row.action], ["操作者", row.actor],
        ["前序摘要", row.previous_hash], ["本条摘要", row.entry_hash],
      ]) + `<pre>${esc(JSON.stringify(JSON.parse(row.detail_json), null, 2))}</pre>`);
    }));
  }
  await load();
  on("#audit-filter", "change", load);
  on("#audit-refresh", "click", load);
  on("#audit-export", "click", () => window.location.assign("/api/audit-export"));
  on("#audit-verify", "click", async () => {
    const result = await api("/api/audit-verify");
    $("#audit-verification").innerHTML = notice(result.valid ?
      `本地审计链一致，共 ${result.count} 条。尾部摘要：${result.head}` :
      `审计链异常，首个不一致序号：${result.first_invalid}`, !result.valid);
  });
}

async function renderSystem() {
  const data = await api("/api/system");
  $("#content").innerHTML = pageHead(
    "系统维护", "本机数据库一致性备份、账号口令维护与能力边界。",
  ) + `<div class="two-col"><div class="card"><h3>运行信息</h3>${kv([
    ["软件版本", data.version], ["规则引擎", data.engine],
    ["数据库文件", data.database], ["规则数量", data.rule_count],
    ["单域资产上限", data.max_assets_per_space], ["能力范围", data.scope],
  ])}</div><div class="card"><h3>数据库备份</h3>
    <p>通过SQLite一致性备份接口创建副本，并计算文件摘要。
      备份包含账号哈希和业务记录，请按内部数据要求保存。</p>
    ${button("create-backup", "创建本地备份", true)}
    <div id="backup-result"></div>
  </div></div><div class="card"><h3>账号口令</h3>
    <p>修改后当前管理员的全部会话失效，需要重新登录。本版是单管理员本机工作台。</p>
    ${button("change-password", "修改管理员口令")}
  </div>${notice("无在线恢复接口，无远程主机扫描、虚拟机创建或策略下发功能。" +
    "请勿对外网开放本服务。", true)}`;
  on("#create-backup", "click", async event => {
    event.target.disabled = true;
    try {
      const result = await api("/api/backup", { method: "POST" });
      $("#backup-result").innerHTML = kv([
        ["备份文件", result.filename], ["大小", `${result.size} bytes`],
        ["SHA-256", result.sha256],
      ]);
      toast("本地备份已创建并通过一致性检查");
    } finally {
      event.target.disabled = false;
    }
  });
  on("#change-password", "click", () => {
    dialog("修改管理员口令", `<form id="edit-form">
      ${notice("新口令须12至128个字符，不能全为空白；保存成功后撤销全部会话。")}
      <div class="form-grid">
        ${formField("old-password", "当前口令", "", "password",
          'required autocomplete="current-password"')}
        ${formField("new-password", "新口令", "", "password",
          'required minlength="12" maxlength="128" autocomplete="new-password"')}
      </div>${formActions("修改并退出")}</form>`);
    bindForm(async () => {
      await api("/api/password", { method: "POST", body: {
        old_password: $("#old-password").value,
        new_password: $("#new-password").value,
      } });
      setLoggedOut();
      toast("口令已更新，请重新登录");
    });
  });
}

on("#dialog-close", "click", () => $("#editor").close());
on("#logout", "click", async () => {
  await api("/api/logout", { method: "POST" });
  setLoggedOut();
});
$$(".nav-button").forEach(element => {
  on(`[data-view="${element.dataset.view}"]`, "click", () => navigate(element.dataset.view));
});
on("#login-form", "submit", async event => {
  event.preventDefault();
  $("#login-error").hidden = true;
  $("#login-button").disabled = true;
  try {
    state.user = await api("/api/login", { method: "POST", body: {
      username: $("#username").value,
      password: $("#password").value,
    } });
    state.catalog = await api("/api/catalog");
    $("#current-user").textContent = state.user.username;
    $("#password").value = "";
    $("#login-screen").hidden = true;
    $("#app-shell").hidden = false;
    await navigate("overview");
  } catch (error) {
    showError(error, "#login-error");
  } finally {
    $("#login-button").disabled = false;
  }
});

async function restoreSession() {
  try {
    const response = await fetch("/api/me", { credentials: "same-origin" });
    if (!response.ok) return;
    state.user = await response.json();
    state.catalog = await api("/api/catalog");
    $("#current-user").textContent = state.user.username;
    $("#login-screen").hidden = true;
    $("#app-shell").hidden = false;
    await navigate("overview");
  } catch (error) {
    setLoggedOut();
  }
}
restoreSession();
