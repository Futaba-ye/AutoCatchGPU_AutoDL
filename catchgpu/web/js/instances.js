// 账号实例列表独立于市场目录；所有操作只针对用户明确选中的实例。
import { api, post, put } from "./api.js";
import { $, escape as e, time, toast, busy } from "./ui.js";

const statuses = {
  running: "运行中",
  starting: "开机中",
  shutting_down: "关机中",
  shutdown: "已关机",
  shutdown_by_starting_error: "开机失败已关机",
  shutdown_by_running_error: "运行异常已关机",
  creating: "创建中",
  created: "已创建待启动",
  removing: "释放中",
  removed: "已释放",
  oss_merged: "数据已归档",
  created_clone_success: "克隆完成",
  restarting: "重启中",
};

export class InstanceManager {
  constructor({ onSaved }) {
    this.onSaved = onSaved;
    this.provider = "autodl";
    this.rows = [];
    this.selected = new Set();
    this.page = 1;
    this.version = 0;
    this.initialized = false;
    this.loading = false;
    this.editing = null;
    $("#instance-provider").onchange = (event) => {
      this.provider = event.target.value;
      this.page = 1;
      this.selected.clear();
      this.reload();
    };
    $("#refresh-instances").onclick = () => this.reload();
    $("#instance-prev").onclick = () => {
      this.page -= 1;
      this.reload();
    };
    $("#instance-next").onclick = () => {
      this.page += 1;
      this.reload();
    };
    $("#instance-select-all").onchange = (event) => {
      this.selected = new Set(
        event.target.checked ? this.rows.map((row) => row.id) : [],
      );
      this.render();
    };
    $("#account-instance-list").onchange = (event) => {
      if (!event.target.matches("[data-instance]")) return;
      if (event.target.checked)
        this.selected.add(event.target.dataset.instance);
      else this.selected.delete(event.target.dataset.instance);
      this.renderActions();
    };
    $("#start-selected-instances").onclick = (event) =>
      busy(event.currentTarget, async () => {
        const result = await post(`/instances/${this.provider}/start-monitor`, {
          instance_ids: [...this.selected],
          interval: Number($("#instance-interval").value),
        });
        toast(`已创建“${result.name}”，有卡开机后自动停止监控`);
        this.selected.clear();
        await this.onSaved();
        location.hash = "#tasks";
      });
    $("#stop-selected-instances").onclick = (event) =>
      busy(event.currentTarget, async () => {
        const names = this.rows
          .filter((row) => this.selected.has(row.id))
          .map((row) => row.name)
          .join("、");
        if (
          !confirm(
            `确认关闭选中的 ${this.selected.size} 个实例（${names}）？\n运行中的程序会中断；按量实例释放 GPU 后，再次开机可能需要等待库存。相关开机监控会先暂停。`,
          )
        )
          return;
        const result = await post(`/instances/${this.provider}/stop`, {
          instance_ids: [...this.selected],
        });
        const failures = result.results.filter((row) => !row.success);
        $("#instance-action-result").textContent = result.results
          .map((row) => `${row.instance_id}：${row.message}`)
          .join("；");
        toast(
          failures.length
            ? `${failures.length} 个实例的关机结果未确认，请查看下方结果`
            : "关机请求已提交，请刷新确认状态",
          !!failures.length,
        );
        this.selected.clear();
        await this.reload();
        await this.onSaved();
      });
    $("#start-task-form").onsubmit = (event) => {
      event.preventDefault();
      busy(event.submitter, async () => {
        await put(`/tasks/${this.editing.id}`, {
          name: $("#start-task-name").value.trim(),
          interval: Number($("#start-task-interval").value),
          provider: this.editing.provider,
          action: "start",
          start_targets: this.editing.start_targets,
        });
        $("#start-task-dialog").close();
        await this.onSaved();
        toast("开机任务已修改，可手动恢复监控");
      });
    };
  }

  init(providers) {
    if (this.initialized) return;
    $("#instance-provider").innerHTML = providers
      .map(
        (provider) =>
          `<option value="${e(provider.id)}">${e(provider.name)}${provider.demo ? "（模拟）" : ""}</option>`,
      )
      .join("");
    if (!providers.some((provider) => provider.id === this.provider))
      this.provider = providers[0].id;
    $("#instance-provider").value = this.provider;
    this.initialized = true;
  }

  open() {
    if (this.initialized) this.reload();
  }

  syncState(state) {
    this.init(state.providers);
    const signature = `${state.connection.browser}:${state.connection.api}:${state.connection.browser_login?.revision || 0}`;
    if (
      this.connection &&
      this.connection !== signature &&
      location.hash === "#instances"
    )
      this.reload();
    this.connection = signature;
    this.tasks = state.tasks;
    if (location.hash === "#instances") this.renderActions();
  }

  async reload() {
    const version = ++this.version;
    this.loading = true;
    this.renderActions();
    $("#account-instance-list").innerHTML =
      '<div class="empty-state"><span class="spinner"></span>正在读取当前账号实例…</div>';
    try {
      const result = await api(
        `/instances/${this.provider}?page=${this.page}&page_size=20`,
      );
      if (version !== this.version) return;
      this.rows = result.items;
      this.selected = new Set(
        [...this.selected].filter((id) =>
          this.rows.some((row) => row.id === id),
        ),
      );
      this.total = result.total;
      $("#instance-page-info").textContent =
        `共 ${result.total} 个实例 · 第 ${this.page} / ${Math.max(1, Math.ceil(result.total / 20))} 页 · ${time(new Date().toISOString())} 更新`;
      $("#instance-notice").textContent = result.demo
        ? "演示实例，开关机均为模拟，不产生费用。"
        : "仅显示当前连接账号的实例。关机会中断实例内程序；开机成功后按平台规则计费。";
      this.render();
    } catch (error) {
      if (version !== this.version) return;
      this.rows = [];
      this.selected.clear();
      $("#account-instance-list").innerHTML =
        `<div class="empty-state error-text">${e(error.message)}<p>请在“连接与通知”中校验账号后刷新。</p></div>`;
    } finally {
      if (version === this.version) {
        this.loading = false;
        this.renderActions();
      }
    }
  }

  render() {
    $("#account-instance-list").innerHTML = this.rows.length
      ? `<div class="table-wrap"><table class="account-table"><thead><tr><th>选择</th><th>实例 / 名称</th><th>GPU 配置</th><th>地区</th><th>状态 / 模式</th><th>计费</th></tr></thead><tbody>${this.rows.map((row) => `<tr><td><input type="checkbox" data-instance="${e(row.id)}" aria-label="选择实例 ${e(row.name)}" ${this.selected.has(row.id) ? "checked" : ""}></td><td><strong>${e(row.name)}</strong><br><code>${e(row.id)}</code></td><td><span class="gpu-emphasis">${e(row.gpu_name)}</span> × ${row.gpu_count}</td><td>${e(row.region_name || "—")}</td><td><span class="tag ${row.status === "running" ? "green" : "neutral"}">${e(statuses[row.status] || row.status)}</span><br><small>${row.start_mode === "gpu" ? "有卡模式" : row.start_mode === "non_gpu" ? "无卡模式" : "模式待确认"}</small></td><td>${e({ payg: "按量计费", daily: "包日", weekly: "包周", monthly: "包月" }[row.billing] || row.billing || "—")}</td></tr>`).join("")}</tbody></table></div>`
      : '<div class="empty-state">当前账号在该平台暂无实例。</div>';
    this.renderActions();
  }

  renderActions() {
    const selected = this.rows.filter((row) => this.selected.has(row.id));
    $("#instance-selected-count").textContent = selected.length;
    const hasMonitor = (row) =>
      (this.tasks || []).some(
        (task) =>
          task.action === "start" &&
          task.provider === this.provider &&
          task.status !== "completed" &&
          task.start_targets.some((target) => target.instance_id === row.id),
      );
    const duplicate = selected.some(hasMonitor);
    $("#start-selected-instances").disabled =
      this.loading ||
      !selected.length ||
      selected.some((row) => !row.can_start) ||
      duplicate;
    $("#stop-selected-instances").disabled =
      this.loading ||
      !selected.length ||
      selected.some(
        (row) => !row.can_stop && !(row.can_start && hasMonitor(row)),
      );
    $("#instance-select-all").checked =
      this.rows.length > 0 && selected.length === this.rows.length;
    $("#instance-select-all").indeterminate =
      selected.length > 0 && selected.length < this.rows.length;
    $("#instance-prev").disabled = this.loading || this.page <= 1;
    $("#instance-next").disabled =
      this.loading || this.page * 20 >= (this.total || 0);
    $("#instance-selection-help").textContent = duplicate
      ? "选中实例已有开机任务，可在监控任务中恢复、修改或删除；关机会先暂停相关任务。"
      : "选择已关机实例后，可加入有卡开机监控；库存不足时继续重试。";
  }

  editTask(task) {
    this.editing = task;
    $("#start-task-name").value = task.name;
    $("#start-task-interval").value = task.interval;
    $("#start-task-targets").textContent = task.start_targets
      .map(
        (target) =>
          `${target.gpu_name} × ${target.gpu_count} · ${target.name}（${target.instance_id}）`,
      )
      .join("；");
    $("#start-task-dialog").showModal();
  }
}
