import { api, post, put } from "./api.js";
import {
  $,
  escape as e,
  icon,
  hydrateIcons,
  time,
  price,
  toast,
  busy,
  logMessage,
} from "./ui.js";
import { openRules, setupRules } from "./rules.js";
import { ResourceExplorer } from "./resources.js";
import { InstanceManager } from "./instances.js";

let state = null,
  cursor = 0,
  polling = false,
  loginConnecting = false;
const instances = new InstanceManager({ onSaved: refreshState });
const resources = new ResourceExplorer({
  onConfigure: ({ provider, catalog, gpus, regions }) =>
    openRules(provider, catalog, gpus, refreshState, null, regions),
  onCatalog: (catalog) => {
    $("#stat-gpus").textContent = catalog.gpus.length;
    $("#nav-gpu-count").textContent = catalog.gpus.length;
  },
  onConnect: async () => {
    if (loginConnecting) return;
    loginConnecting = true;
    try {
      const result = await post("/connection/browser/connect");
      toast(result.detail);
    } catch (error) {
      toast(error.message, true);
      location.hash = "#settings";
    } finally {
      loginConnecting = false;
      try {
        await refreshState();
      } catch (error) {
        toast(error.message, true);
      }
    }
  },
});
const names = {
  dashboard: "监控概览",
  resources: "GPU 资源",
  instances: "账号实例",
  tasks: "监控任务",
  logs: "运行日志",
  settings: "连接与通知",
};
const descriptions = {
  dashboard: [
    "让算力等待，变成算力就绪。",
    "选择 GPU、设定限制，剩下的交给持续监控。",
  ],
  resources: [
    "找到适合你的下一份算力。",
    "读取平台实时型号目录，一次选择，批量配置。",
  ],
  instances: [
    "管理已有的算力环境。",
    "查看账号实例，按需关机或加入有卡开机监控。",
  ],
  tasks: [
    "每一个算力目标，都有进度。",
    "监控在服务后台运行，达到目标后自动停止。",
  ],
  logs: [
    "让每一次检测，都有迹可循。",
    "资源检测、创建结果与异常，都记录在这里。",
  ],
  settings: [
    "按你的方式，连接与提醒。",
    "管理 AutoDL 登录方式，以及实例就绪后的通知。",
  ],
};
const statuses = {
  draft: ["未开始", "neutral"],
  running: ["监控中", "green"],
  paused: ["已暂停", "neutral"],
  attention: ["需要关注", "orange"],
  completed: ["已完成", "green"],
};

function route() {
  const view = names[location.hash.slice(1)]
    ? location.hash.slice(1)
    : "dashboard";
  document
    .querySelectorAll("[data-views]")
    .forEach((el) => (el.hidden = !el.dataset.views.split(" ").includes(view)));
  document.querySelectorAll("[data-nav]").forEach((el) => {
    el.classList.toggle("active", el.dataset.nav === view);
    if (el.dataset.nav === view) el.setAttribute("aria-current", "page");
    else el.removeAttribute("aria-current");
  });
  $("#page-name").textContent = names[view];
  $("#page-title").textContent = descriptions[view][0];
  $("#page-description").textContent = descriptions[view][1];
  if (view === "instances") instances.open();
}

function taskControls(task) {
  const running = task.status === "running";
  return `${running ? `<button class="button secondary small" data-action="pause" data-task="${task.id}">暂停监控</button>` : task.status === "completed" ? "" : `<button class="button primary small" data-action="start" data-task="${task.id}">${task.status === "draft" ? "开始监控" : "恢复监控"}</button>`}
    ${task.status === "attention" ? `<button class="button secondary small" data-action="pending" data-task="${task.id}">核对结果</button>` : ""}
    <button class="text-button" data-action="edit" data-task="${task.id}" ${running ? 'disabled title="请先暂停监控"' : ""}>修改</button>
    <button class="text-button danger-text" data-action="delete" data-task="${task.id}" ${running ? 'disabled title="请先暂停监控"' : ""}>删除</button>
    ${task.action === "start" ? "" : `<button class="text-button" data-action="copy" data-task="${task.id}">另建同配置任务</button>`}`;
}

function targetDetails(task) {
  if (task.action === "start")
    return `<div class="table-wrap"><table><thead><tr><th>GPU 型号</th><th>已有实例</th><th>卡数</th><th>地区</th><th>进度</th></tr></thead><tbody>${task.start_targets.map((target) => `<tr><td>${e(target.gpu_name)}</td><td>${e(target.name)}<br><code>${e(target.instance_id)}</code></td><td>${target.gpu_count}</td><td>${e(target.region_name || "—")}</td><td>${task.instances.some((row) => row.id === target.instance_id) ? "已确认有卡运行" : "等待有卡开机"}</td></tr>`).join("")}</tbody></table></div>`;
  return `<div class="table-wrap"><table><thead><tr><th>GPU 型号</th><th>卡数 / 实例</th><th>目标进度</th><th>价格限制</th><th>镜像 / 地区</th></tr></thead><tbody>${task.rules.map((rule) => `<tr><td>${e(rule.gpu_name)}</td><td>${rule.gpu_count}</td><td>${task.instances.filter((instance) => instance.rule_id === rule.id).length} / ${rule.instance_count}</td><td>¥ ${price(rule.max_price)} / ${rule.price_scope === "gpu" ? "卡" : "实例"} / 小时${rule.include_disk ? "（含磁盘）" : ""}</td><td class="small-text">${e(rule.image_id)}<br>${e(rule.regions.join("、") || "不限地区")}</td></tr>`).join("")}</tbody></table></div>`;
}

function renderTasks() {
  const tasks = state.tasks;
  $("#task-total").textContent = tasks.length;
  $("#nav-task-count").textContent = tasks.length;
  $("#stat-running").innerHTML =
    `${tasks.filter((task) => task.status === "running").length}<span>个任务</span>`;
  $("#stat-created").innerHTML =
    `${tasks.filter((task) => task.action !== "start").reduce((total, task) => total + task.instances.length, 0)}<span>个实例</span>`;
  $("#stat-attention").innerHTML =
    `${tasks.filter((task) => task.status === "attention").length}<span>个任务</span>`;
  const signature = JSON.stringify(tasks);
  if ($("#task-list").dataset.signature === signature) return;
  $("#task-list").dataset.signature = signature;
  $("#task-list").innerHTML = tasks.length
    ? tasks
        .map((task) => {
          const target =
            task.action === "start"
              ? task.start_targets.length
              : task.rules.reduce(
                  (total, rule) => total + rule.instance_count,
                  0,
                );
          const completed = task.instances.length,
            status = statuses[task.status];
          const providerName =
            state.providers.find((item) => item.id === task.provider)?.name ||
            task.provider;
          return `<article class="task-card" data-task-card="${task.id}"><div class="task-heading"><div class="task-icon ${task.status === "running" ? "active-task" : ""}">${icon("radar")}</div><div class="task-name"><h3>${e(task.name)} ${task.provider === "demo" ? '<span class="tag neutral">模拟</span>' : ""}</h3><p>${e(providerName)} <span>·</span> ${task.action === "start" ? "已有实例有卡开机" : "创建新实例"} <span>·</span> 每 ${task.interval} 秒检测 <span>·</span> 已检测 ${task.checks} 轮</p></div><span class="tag ${status[1]}">${task.status === "running" ? '<span class="live-dot"></span>' : ""}${status[0]}</span></div><div class="task-progress"><div><span>${task.action === "start" ? "有卡开机进度" : "实例创建进度"}</span><strong>${completed} <small>/ ${target}</small></strong></div><progress value="${completed}" max="${target}"></progress></div>${task.last_error ? `<div class="task-error">${e(task.last_error)}</div>` : ""}<div class="task-actions"><span class="muted">${task.next_check_at ? `下次检测 ${time(task.next_check_at)}` : `创建于 ${time(task.created_at)}`}</span><div>${taskControls(task)}</div></div><details class="task-details"><summary>规则与实例详情 <span>${task.action === "start" ? `${target} 个已有实例` : `${task.rules.length} 条规则`}</span></summary>${targetDetails(task)}${task.instances.length ? `<div class="instance-list">${task.instances.map((instance) => `<div><span class="success-text">✓ ${task.action === "start" ? "已开机" : "已创建"}</span><code>${e(instance.id)}</code><span>${e(instance.gpu_name)} × ${instance.gpu_count}</span><small>${time(instance.created_at)}</small></div>`).join("")}</div>` : ""}</details></article>`;
        })
        .join("")
    : `<div class="empty-state task-empty"><span class="empty-icon">${icon("radar")}</span><h3>你的第一个算力目标，从这里开始</h3><p>在上方选择 GPU，批量设置限制后即可开始监控。</p></div>`;
  const value = $("#log-task").value;
  $("#log-task").innerHTML =
    '<option value="">全部任务</option>' +
    tasks
      .map((task) => `<option value="${task.id}">${e(task.name)}</option>`)
      .join("");
  $("#log-task").value = value;
}

function renderLogs() {
  const term = $("#log-search").value.toLowerCase(),
    level = $("#log-level").value,
    task = $("#log-task").value;
  const logs = state.events.filter(
    (log) =>
      (!level || log.level === level) &&
      (!task || log.task_id === task) &&
      log.message.toLowerCase().includes(term),
  );
  const labels = {
    info: "信息",
    success: "成功",
    warning: "警告",
    error: "错误",
  };
  $("#log-list").innerHTML = logs.length
    ? logs
        .slice()
        .reverse()
        .map(
          (log) =>
            `<div class="log-line"><time datetime="${e(log.created_at)}">${time(log.created_at)}</time><span class="log-level ${log.level}">${labels[log.level] || e(log.level)}</span><span>${logMessage(log)}</span></div>`,
        )
        .join("")
    : '<div class="empty-state log-empty">暂无匹配日志。开始监控后，检测过程会显示在这里。</div>';
}

function notify(event) {
  if (!["instance_created", "instance_started"].includes(event.kind)) return;
  toast(event.message);
  if (state.notifications.speech && "speechSynthesis" in window) {
    const speech = new SpeechSynthesisUtterance(event.message);
    speech.lang = "zh-CN";
    speechSynthesis.speak(speech);
  }
  if (
    state.notifications.desktop &&
    !state.native_notifications &&
    "Notification" in window &&
    Notification.permission === "granted"
  )
    new Notification("CatchGPU · 实例已就绪", {
      body: event.message,
      tag: `instance-${event.id}`,
    });
}

async function refreshState(initial = false) {
  const next = await api("/state");
  if (!initial && state) {
    const events = await api(`/events?after=${cursor}`);
    for (const event of events) {
      notify(event);
      cursor = Math.max(cursor, event.id);
    }
  } else cursor = Math.max(0, ...next.events.map((event) => event.id));
  state = next;
  resources.syncState(next);
  instances.syncState(next);
  if (initial && location.hash === "#instances") instances.open();
  $("#service-status").textContent = "服务已连接";
  $("#service-status").classList.remove("error-text");
  $("#connection-badge").textContent =
    state.connection.api && state.connection.browser
      ? "API 与网页登录已就绪"
      : state.connection.api
        ? "已配置开发者 API"
        : state.connection.browser
          ? "网页登录已连接"
          : "尚未连接 AutoDL";
  $("#connection-badge").classList.toggle(
    "connected",
    state.connection.api || state.connection.browser,
  );
  const login = state.connection.browser_login;
  const loginOptions = JSON.stringify(login.options);
  if ($("#login-browser").dataset.options !== loginOptions) {
    const selectedBrowser = $("#login-browser").value;
    $("#login-browser").innerHTML =
      '<option value="auto">自动选择本机 Chrome / Edge</option>' +
      login.options
        .map(
          (browser) =>
            `<option value="${e(browser.id)}">${e(browser.name)}</option>`,
        )
        .join("");
    $("#login-browser").value = login.options.some(
      (browser) => browser.id === selectedBrowser,
    )
      ? selectedBrowser
      : "auto";
    $("#login-browser").dataset.options = loginOptions;
  }
  $("#browser-connect").disabled = loginConnecting || !login.can_connect;
  $("#login-status").textContent =
    login.error ||
    (state.connection.browser
      ? `${login.name} 网页登录已校验，请保留该窗口`
      : login.state === "connecting"
        ? "正在连接并校验登录状态…"
        : login.can_connect
          ? `${login.name} 窗口已打开，请完成登录和安全认证，再点击连接工具`
          : "尚未连接：先打开本机浏览器登录，再确认连接");
  $("#notify-desktop").checked = state.notifications.desktop;
  $("#notify-speech").checked = state.notifications.speech;
  $("#desktop-description").textContent = state.native_notifications
    ? "Windows 原生通知；关闭网页后也可提醒。"
    : "浏览器桌面通知；需要授权并保持网页打开。";
  $("#global-error").hidden = true;
  renderTasks();
  renderLogs();
}

async function showPending(taskId) {
  const startup =
    state.tasks.find((task) => task.id === taskId)?.action === "start";
  const pending = await api(`/tasks/${taskId}/pending`);
  $("#pending-dialog h2").textContent = startup
    ? "核对已有实例开机状态"
    : "核对创建结果";
  $("#pending-dialog .dialog-header p").textContent = startup
    ? "核对只读取实例状态，不会重复发送开机请求。"
    : "平台响应不明确时，先检查实例列表与待支付订单。";
  $("#pending-content").innerHTML =
    `<p class="field-help">${startup ? "请在官网检查目标实例。只有确认实例已关机且本次请求未生效，才能清除该请求并重新尝试。恢复监控会先继续读取待核对实例的状态。" : "按下方名称在 AutoDL 实例列表和待支付订单中核对。自动核对不会重新提交创建请求。"}</p><button class="button primary" data-reconcile="${taskId}">从平台重新核对</button>${pending.map((attempt) => `<div class="pending-item"><code>${e(startup ? attempt.rule_id : attempt.name)}</code><p class="field-help">提交于 ${time(attempt.created_at)}</p><button class="button secondary small" data-missing="${attempt.id}" data-task="${taskId}" data-startup="${startup}">${startup ? "已确认实例关机且开机请求未生效" : "已确认没有实例和未支付订单"}</button></div>`).join("") || "<p>没有待核对请求。处理登录或配置问题后，可恢复监控。</p>"}`;
  $("#pending-dialog").showModal();
}

hydrateIcons();
setupRules();
route();
window.addEventListener("hashchange", route);
window.addEventListener("access-required", () => {
  if (!$("#access-dialog").open) $("#access-dialog").showModal();
});
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-close]");
  if (button) document.getElementById(button.dataset.close).close();
});
$("#open-settings").onclick = () => (location.hash = "#settings");
$("#go-resources").onclick = () => (location.hash = "#resources");
for (const id of ["log-task", "log-level", "log-search"])
  $(`#${id}`).addEventListener(
    id === "log-search" ? "input" : "change",
    renderLogs,
  );
$("#task-list").addEventListener("click", (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  busy(button, async () => {
    const id = button.dataset.task,
      action = button.dataset.action;
    if (action === "pending") return showPending(id);
    if (action === "delete") {
      if (
        !confirm(
          "确认删除这个监控任务？只删除本地监控配置，不会关机或释放 AutoDL 实例；运行日志保留。",
        )
      )
        return;
      await api(`/tasks/${id}`, { method: "DELETE" });
      await refreshState();
      toast("监控任务已删除，平台实例保持不变");
      return;
    }
    if (action === "copy" || action === "edit") {
      const task = state.tasks.find((task) => task.id === id);
      if (task.action === "start") {
        instances.editTask(task);
        return;
      }
      const data = await api(`/catalog/${task.provider}`);
      openRules(
        task.provider,
        data,
        [],
        refreshState,
        task,
        [],
        action === "edit",
      );
      return;
    }
    await post(`/tasks/${id}/${action}`);
    await refreshState();
    toast(action === "pause" ? "已暂停，正在记录在途请求结果" : "监控已开始");
  });
});
$("#pending-content").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  busy(button, async () => {
    if (button.dataset.reconcile)
      await post(`/tasks/${button.dataset.reconcile}/reconcile`);
    if (button.dataset.missing) {
      if (
        !confirm(
          button.dataset.startup === "true"
            ? "是否已在 AutoDL 确认该实例已关机，且开机请求没有生效？清除后恢复监控会再次尝试开机。"
            : "你是否已在 AutoDL 确认：该请求没有创建实例，也没有未支付订单？确认后恢复任务可能再次创建实例。",
        )
      )
        return;
      await post(
        `/tasks/${button.dataset.task}/attempts/${button.dataset.missing}/missing`,
        { confirmed: true },
      );
    }
    $("#pending-dialog").close();
    await refreshState();
    toast("核对结果已更新");
  });
});
$("#token-form").onsubmit = (event) => {
  event.preventDefault();
  busy(event.submitter, async () => {
    await put("/connection/token", { token: $("#autodl-token").value });
    $("#autodl-token").value = "";
    await refreshState();
    toast("Token 已保存到当前服务进程");
  });
};
$("#clear-token").onclick = (event) =>
  busy(event.currentTarget, async () => {
    await put("/connection/token", { token: "" });
    await refreshState();
    toast("当前进程中的 Token 已清除");
  });
$("#test-connection").onclick = (event) =>
  busy(event.currentTarget, async () => {
    const result = await post("/connection/test");
    toast(result.detail);
    await refreshState();
  });
$("#browser-login").onclick = (event) =>
  busy(event.currentTarget, async () => {
    const result = await post("/connection/login", {
      browser: $("#login-browser").value,
    });
    toast(result.detail);
    await refreshState();
  });
$("#browser-connect").onclick = async (event) => {
  const button = event.currentTarget;
  if (button.disabled || loginConnecting) return;
  loginConnecting = true;
  button.disabled = true;
  try {
    const result = await post("/connection/browser/connect");
    toast(result.detail);
  } catch (error) {
    toast(error.message, true);
  } finally {
    loginConnecting = false;
    try {
      await refreshState();
    } catch (error) {
      toast(error.message, true);
    }
  }
};
async function setNotifications() {
  try {
    if (
      $("#notify-desktop").checked &&
      !state.native_notifications &&
      "Notification" in window &&
      Notification.permission === "default"
    )
      await Notification.requestPermission();
    await put("/notifications", {
      desktop: $("#notify-desktop").checked,
      speech: $("#notify-speech").checked,
    });
    await refreshState();
    toast("通知偏好已保存");
  } catch (error) {
    toast(error.message, true);
  }
}
$("#notify-desktop").onchange = setNotifications;
$("#notify-speech").onchange = setNotifications;
$("#test-speech").onclick = () => {
  if (!("speechSynthesis" in window)) {
    toast("当前浏览器不支持语音播报", true);
    return;
  }
  speechSynthesis.cancel();
  const speech = new SpeechSynthesisUtterance("CatchGPU，实例已创建成功。");
  speech.lang = "zh-CN";
  speechSynthesis.speak(speech);
};
$("#access-form").onsubmit = async (event) => {
  event.preventDefault();
  sessionStorage.setItem("catchgpu-access", $("#access-token").value);
  try {
    await refreshState(true);
    $("#access-token").value = "";
    $("#access-dialog").close();
    await initCatalog();
  } catch (error) {
    $("#access-error").textContent = error.message;
  }
};
async function initCatalog() {
  await resources.init(state.providers);
}

try {
  await refreshState(true);
  await initCatalog();
} catch (error) {
  $("#global-error").textContent = error.message;
  $("#global-error").hidden = false;
}
setInterval(async () => {
  if (polling || $("#access-dialog").open) return;
  polling = true;
  try {
    await refreshState(!state);
    if (!resources.initialized) await initCatalog();
  } catch (error) {
    $("#service-status").textContent = "服务连接中断";
    $("#service-status").classList.add("error-text");
    $("#global-error").textContent = "工具服务暂时无法连接，请检查启动窗口。";
    $("#global-error").hidden = false;
  } finally {
    polling = false;
  }
}, 3000);
