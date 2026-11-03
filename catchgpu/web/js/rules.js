// 批量表单与逐行规则共用字段映射，新增约束时只扩展这里和后端 Rule。
import { $, escape as e, toast } from "./ui.js";
import { post, put } from "./api.js";

let rules = [],
  provider = "",
  catalog = null,
  onSaved = () => {},
  editTask = null;
const options = (entries, selected) =>
  entries
    .map(
      (option) =>
        `<option value="${e(option.id)}" ${option.id === selected ? "selected" : ""}>${e(option.name)}</option>`,
    )
    .join("");

function fields(rule, batch = false) {
  const prefix = batch ? "batch" : rule.id;
  return `<label>每实例 GPU 卡数<input data-field="gpu_count" type="number" min="1" max="${provider === "autodl-pro" ? 4 : 12}" value="${rule.gpu_count}" required></label>
    <label>目标实例数<input data-field="instance_count" type="number" min="1" max="100" value="${rule.instance_count}" required></label>
    <label>价格上限（元/小时）<input data-field="max_price" type="number" min="0.0001" max="1000000" step="0.0001" value="${e(rule.max_price)}" placeholder="填写你的最高价格" ${batch ? "" : "required"}></label>
    <label>价格比较口径<select data-field="price_scope"><option value="gpu" ${rule.price_scope === "gpu" ? "selected" : ""}>每张 GPU / 小时</option><option value="instance" ${rule.price_scope === "instance" ? "selected" : ""}>每个实例 / 小时</option></select></label>
    <label>地区<select data-field="regions" multiple size="3" aria-label="${batch ? "批量" : e(rule.gpu_name)}地区，可多选">${catalog.regions.map((option) => `<option value="${e(option.id)}" ${rule.regions.includes(option.id) ? "selected" : ""}>${e(option.name)}</option>`).join("")}</select><small>未选择表示不限；按 Ctrl 可多选</small></label>
    <label>镜像 UUID<input data-field="image_id" list="${e(prefix)}-images" value="${e(rule.image_id)}" required placeholder="选择镜像或填写 UUID"><datalist id="${e(prefix)}-images">${options(catalog.images)}</datalist><small>支持基础镜像和私有镜像 UUID</small></label>
    <label>额外磁盘（GB）<input data-field="disk_gb" type="number" min="0" max="500" value="${rule.disk_gb}" required><small>${provider === "autodl-pro" ? "扩容系统盘，基础容量 30 GB" : "扩容数据盘，不含默认容量"}</small></label>
    <label>计费方式<select data-field="billing">${options(catalog.billing, rule.billing)}</select></label>
    <label>预付费周期数量<input data-field="duration" type="number" min="1" max="12" value="${rule.duration}" required><small>按量计费忽略此项</small></label>
    <label>CUDA 最低版本<input data-field="cuda" type="number" min="100" max="999" value="${rule.cuda}" required><small>例如 118 表示 CUDA 11.8</small></label>
    <label class="checkbox-field"><input data-field="include_disk" type="checkbox" ${rule.include_disk ? "checked" : ""}><span>价格上限包含磁盘费用</span></label>`;
}

function readFields(root, base) {
  const rule = { ...base };
  for (const input of root.querySelectorAll("[data-field]")) {
    const key = input.dataset.field;
    if (input.type === "checkbox") rule[key] = input.checked;
    else if (input.multiple)
      rule[key] = Array.from(input.selectedOptions, (option) => option.value);
    else if (input.type === "number" && key !== "max_price")
      rule[key] = Number(input.value);
    else rule[key] = input.value.trim();
  }
  return rule;
}

function renderRows() {
  $("#rule-count").textContent = `${rules.length} 条规则`;
  $("#rule-rows").innerHTML = rules
    .map(
      (rule, index) =>
        `<article class="rule-row" data-rule-id="${e(rule.id)}"><div class="rule-summary"><span class="rule-index">${String(index + 1).padStart(2, "0")}</span><strong>${e(rule.gpu_name)}</strong><span class="tag neutral">独立目标</span><button type="button" class="text-button remove-rule" data-remove="${e(rule.id)}" aria-label="移除 ${e(rule.gpu_name)}">移除</button></div><div class="form-grid rule-fields">${fields(rule)}</div></article>`,
    )
    .join("");
}

export function openRules(
  currentProvider,
  currentCatalog,
  selected,
  afterSave,
  sourceTask = null,
  initialRegions = [],
  edit = false,
) {
  provider = currentProvider;
  catalog = currentCatalog;
  onSaved = afterSave;
  editTask = edit ? sourceTask : null;
  if (editTask) {
    catalog = { ...catalog, regions: [...catalog.regions] };
    for (const id of new Set(
      sourceTask.rules.flatMap((rule) => rule.regions),
    )) {
      if (!catalog.regions.some((region) => region.id === id))
        catalog.regions.push({ id, name: `${id}（原配置）` });
    }
  }
  if (!sourceTask && !selected.length) return;
  const defaults = {
    gpu_count: 1,
    instance_count: 1,
    regions: [...initialRegions],
    image_id: catalog.images[0]?.id || "",
    disk_gb: 0,
    billing: "payg",
    duration: 1,
    max_price: "",
    price_scope: "gpu",
    include_disk: false,
    cuda: 118,
  };
  rules = sourceTask
    ? sourceTask.rules.map((rule) => ({
        ...rule,
        id: edit ? rule.id : crypto.randomUUID().replaceAll("-", ""),
      }))
    : selected.map((gpu) => ({
        ...defaults,
        id: crypto.randomUUID().replaceAll("-", ""),
        gpu_id: gpu.id,
        gpu_name: gpu.name,
      }));
  $("#batch-fields").innerHTML = fields({ ...defaults, ...rules[0] }, true);
  $("#task-name").value = sourceTask
    ? edit
      ? sourceTask.name
      : `${sourceTask.name.slice(0, 70)}（新任务）`
    : "GPU 资源监控";
  $("#task-interval").value = sourceTask?.interval || 30;
  $("#rule-dialog .eyebrow").textContent = edit
    ? "修改监控任务 · 已有结果保留"
    : "新建监控任务";
  $("#rule-provider-label").textContent =
    `${provider === "demo" ? "演示任务 · 不产生费用" : provider === "autodl-pro" ? "AutoDL Pro 实例" : "AutoDL 普通实例"}`;
  renderRows();
  $("#rule-dialog").showModal();
}

export function setupRules() {
  $("#apply-batch").addEventListener("click", () => {
    const values = readFields($("#batch-fields"), {});
    const invalid = Array.from(
      $("#batch-fields").querySelectorAll("input"),
    ).find((input) => !input.checkValidity());
    if (invalid) {
      invalid.reportValidity();
      return;
    }
    rules = rules.map((rule) => ({ ...rule, ...values }));
    renderRows();
    toast(`已应用到 ${rules.length} 条 GPU 规则`);
  });
  $("#rule-rows").addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove]");
    if (!button) return;
    rules = rules
      .map((rule) =>
        readFields(
          document.querySelector(`[data-rule-id="${CSS.escape(rule.id)}"]`),
          rule,
        ),
      )
      .filter((rule) => rule.id !== button.dataset.remove);
    renderRows();
  });
  $("#rule-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!rules.length) {
      toast("请至少保留一条 GPU 规则", true);
      return;
    }
    const buttons = Array.from(
      $("#rule-form").querySelectorAll('button[type="submit"]'),
    );
    buttons.forEach((button) => (button.disabled = true));
    try {
      rules = rules.map((rule) =>
        readFields(
          document.querySelector(`[data-rule-id="${CSS.escape(rule.id)}"]`),
          rule,
        ),
      );
      const payload = {
        name: $("#task-name").value.trim(),
        provider,
        interval: Number($("#task-interval").value),
        rules,
      };
      const task = editTask
        ? await put(`/tasks/${editTask.id}`, payload)
        : await post("/tasks", payload);
      $("#rule-dialog").close();
      if (event.submitter?.value === "start") {
        try {
          await post(`/tasks/${task.id}/start`);
          toast("任务已开始监控");
        } catch (error) {
          toast(`任务已保存；${error.message}`, true);
        }
      } else toast("任务已保存");
      await onSaved();
      location.hash = "#tasks";
    } catch (error) {
      toast(error.message, true);
    } finally {
      buttons.forEach((button) => (button.disabled = false));
    }
  });
}
