// 资源区独立管理地区、型号选择、按需详情和服务端收藏。
import { api, put } from "./api.js";
import { $, escape as e, price, time, toast } from "./ui.js";

const favoriteKey = (provider, gpu) => `${provider}:${gpu}`;
const stock = (gpu) => `${gpu.available ?? "?"}/${gpu.total ?? "?"}`;

export class ResourceExplorer {
  constructor({ onConfigure, onCatalog, onConnect }) {
    this.onConfigure = onConfigure;
    this.onCatalog = onCatalog;
    this.onConnect = onConnect;
    this.provider = "autodl";
    this.catalog = null;
    this.region = "";
    this.selected = new Map();
    this.focused = "";
    this.favorites = [];
    this.connection = {};
    this.catalogVersion = 0;
    this.detailVersion = 0;
    this.detailKey = "";
    this.page = 1;
    this.loading = false;
    this.initialized = false;
    this.lastFavoriteWrite = 0;
    this.bind();
  }

  bind() {
    $("#provider").onchange = async (event) => {
      this.provider = event.target.value;
      this.region = "";
      this.selected.clear();
      this.focused = "";
      this.catalog = null;
      await this.reload();
    };
    $("#refresh-catalog").onclick = () => this.reload(true);
    $("#gpu-search").oninput = () => this.renderChoices();
    $("#available-only").onchange = () => this.renderChoices();
    $("#favorite-only").onchange = () => this.renderChoices();
    $("#region-options").onclick = async (event) => {
      const button = event.target.closest("[data-region]");
      if (!button || this.loading || button.dataset.region === this.region)
        return;
      this.region = button.dataset.region;
      await this.reload();
    };
    $("#gpu-grid").addEventListener("change", (event) => {
      if (event.target.type !== "checkbox") return;
      this.toggle(event.target.value, event.target.checked);
    });
    for (const id of ["gpu-grid", "favorite-strip"]) {
      $(`#${id}`).addEventListener("click", (event) => {
        const star = event.target.closest("[data-favorite]");
        if (star) this.toggleFavorite(star.dataset.favorite, star);
        const quick = event.target.closest("[data-quick]");
        if (quick)
          this.toggle(
            quick.dataset.quick,
            !this.selected.has(quick.dataset.quick),
          );
      });
    }
    $("#select-visible").onchange = (event) => {
      for (const gpu of this.visibleGPUs()) {
        if (event.target.checked) this.selected.set(gpu.id, gpu);
        else this.selected.delete(gpu.id);
      }
      this.focused = this.selected.has(this.focused)
        ? this.focused
        : this.selected.keys().next().value || "";
      this.renderChoices();
      this.renderSelection();
    };
    $("#clear-selection").onclick = () => {
      this.selected.clear();
      this.focused = "";
      this.renderChoices();
      this.renderSelection();
    };
    $("#configure-rules").onclick = () =>
      this.onConfigure({
        provider: this.provider,
        catalog: this.catalog,
        gpus: [...this.selected.values()],
        regions:
          this.catalog.region_groups.find((group) => group.id === this.region)
            ?.region_ids || this.catalog.regions.map((region) => region.id),
      });
    $("#selected-gpu-tabs").onclick = (event) => {
      const button = event.target.closest("[data-focus]");
      if (!button) return;
      this.focused = button.dataset.focus;
      this.page = 1;
      this.renderSelection();
    };
    $("#refresh-details").onclick = () => this.loadDetails(true);
    $("#resource-prev").onclick = () => {
      this.page -= 1;
      this.loadDetails();
    };
    $("#resource-next").onclick = () => {
      this.page += 1;
      this.loadDetails();
    };
    $("#resource-connection").onclick = (event) => {
      if (event.target.closest("[data-connect]")) this.onConnect();
      if (event.target.closest("[data-reload]")) this.reload(true);
    };
  }

  async init(providers) {
    $("#provider").innerHTML = providers
      .map(
        (item) =>
          `<option value="${e(item.id)}">${e(item.name)}${item.demo ? "（模拟数据）" : ""}</option>`,
      )
      .join("");
    if (!providers.some((item) => item.id === this.provider))
      this.provider = providers[0].id;
    $("#provider").value = this.provider;
    this.initialized = true;
    await this.reload();
  }

  syncState(state) {
    const signature = `${state.connection.browser}:${state.connection.api}:${state.connection.browser_login?.revision || 0}`;
    const changed =
      this.connectionSignature && this.connectionSignature !== signature;
    this.connectionSignature = signature;
    this.connection = state.connection;
    if (Date.now() - this.lastFavoriteWrite > 4000) {
      const next = state.favorites || [];
      if (JSON.stringify(next) !== JSON.stringify(this.favorites)) {
        this.favorites = next;
        if (this.catalog) this.renderChoices();
      }
    }
    this.renderConnection();
    if (changed && this.initialized) this.reload(true);
  }

  renderConnection() {
    const demo = this.provider === "demo";
    const connected = this.connection.browser;
    $("#resource-connection").classList.toggle(
      "is-connected",
      connected || demo,
    );
    $("#resource-connection").innerHTML = demo
      ? "<span>演示环境 · 所有资源及创建结果均为模拟</span>"
      : connected
        ? '<span><span class="live-dot"></span> 网页登录已连接 · 选择型号后读取账号资源和报价</span><button class="text-button" data-reload>刷新账号资源</button>'
        : this.provider === "autodl-pro" && this.connection.api
          ? '<span>已配置 Pro API · 当前显示公开参考资源，创建前校验限制</span><button class="text-button" data-connect>连接网页登录</button>'
          : '<span>尚未连接网页登录 · 可先选择和收藏型号</span><button class="button secondary small" data-connect>同步已登录窗口</button>';
  }

  async reload(refresh = false) {
    const version = ++this.catalogVersion;
    this.detailVersion += 1;
    this.detailKey = "";
    this.loading = true;
    this.renderConnection();
    $("#configure-rules").disabled = true;
    $("#gpu-grid").innerHTML =
      '<div class="market-empty"><span class="spinner"></span>正在读取当前地区的型号…</div>';
    $("#resource-details").hidden = true;
    try {
      const query = new URLSearchParams({
        refresh: String(refresh),
        region: this.region,
      });
      const result = await api(`/catalog/${this.provider}?${query}`);
      if (version !== this.catalogVersion) return;
      result.region_groups ||= result.regions.map((region) => ({
        ...region,
        region_ids: [region.id],
      }));
      this.catalog = result;
      this.region =
        result.active_region || result.region_groups[0]?.id || "all";
      for (const id of this.selected.keys()) {
        const gpu = result.gpus.find((item) => item.id === id);
        if (gpu) this.selected.set(id, gpu);
        else this.selected.delete(id);
      }
      this.focused = this.selected.has(this.focused)
        ? this.focused
        : this.selected.keys().next().value || "";
      this.page = 1;
      $("#catalog-count").textContent =
        `${result.gpus.length} 种型号${result.demo ? " · 模拟" : ""}`;
      $("#catalog-notices").innerHTML = result.notices
        .map((notice) => `<span>${e(notice)}</span>`)
        .join("");
      $("#catalog-notices").classList.toggle("demo-notice", result.demo);
      $("#catalog-time").textContent = `目录更新于 ${time(result.fetched_at)}`;
      this.onCatalog(result);
      this.renderRegions();
      this.renderChoices();
      this.renderSelection();
    } catch (error) {
      if (version !== this.catalogVersion) return;
      $("#gpu-grid").innerHTML =
        `<div class="market-empty error-text">${e(error.message)}<p>点击刷新重试。</p></div>`;
      $("#catalog-time").textContent = "目录读取失败";
    } finally {
      if (version === this.catalogVersion) this.loading = false;
    }
  }

  renderRegions() {
    const normal = this.catalog.region_groups.filter((group) => !group.special);
    const special = this.catalog.region_groups.filter((group) => group.special);
    const buttons = (groups) =>
      groups
        .map(
          (group) =>
            `<button class="region-button ${this.region === group.id ? "active" : ""}" data-region="${e(group.id)}" aria-pressed="${this.region === group.id}">${e(group.name)}</button>`,
        )
        .join("");
    $("#region-options").innerHTML =
      `<div class="region-group"><button class="region-button ${this.region === "all" ? "active" : ""}" data-region="all" aria-pressed="${this.region === "all"}">全部地区</button>${buttons(normal)}</div>${special.length ? `<div class="region-group special-regions">${buttons(special)}</div>` : ""}`;
  }

  isFavorite(id) {
    return this.favorites.some(
      (item) =>
        favoriteKey(item.provider, item.gpu_id) ===
        favoriteKey(this.provider, id),
    );
  }

  visibleGPUs() {
    const term = $("#gpu-search").value.trim().toLowerCase();
    return (this.catalog?.gpus || []).filter(
      (gpu) =>
        gpu.name.toLowerCase().includes(term) &&
        (!$("#available-only").checked || gpu.available > 0) &&
        (!$("#favorite-only").checked || this.isFavorite(gpu.id)),
    );
  }

  renderChoices() {
    const visible = this.visibleGPUs();
    $("#gpu-grid").innerHTML = visible.length
      ? visible
          .map(
            (gpu) => `
      <div class="gpu-option ${this.selected.has(gpu.id) ? "selected" : ""}" data-gpu="${e(gpu.id)}">
        <label><input type="checkbox" value="${e(gpu.id)}" ${this.selected.has(gpu.id) ? "checked" : ""} aria-label="选择 ${e(gpu.name)}"><span>${e(gpu.name)}</span><span class="gpu-stock" title="空闲 / 总量">(${stock(gpu)})</span></label>
        <button class="favorite-button ${this.isFavorite(gpu.id) ? "is-favorite" : ""}" data-favorite="${e(gpu.id)}" aria-label="${this.isFavorite(gpu.id) ? "取消收藏" : "收藏"} ${e(gpu.name)}" aria-pressed="${this.isFavorite(gpu.id)}">${this.isFavorite(gpu.id) ? "★" : "☆"}</button>
      </div>`,
          )
          .join("")
      : '<div class="market-empty">没有符合筛选条件的型号，请调整地区、搜索或收藏筛选。</div>';
    const favorites = this.favorites.filter(
      (item) => item.provider === this.provider,
    );
    $("#favorite-strip").innerHTML = favorites.length
      ? favorites
          .map((item) => {
            const present = this.catalog?.gpus.some(
              (gpu) => gpu.id === item.gpu_id,
            );
            return `<span class="favorite-shortcut"><button data-quick="${e(item.gpu_id)}" ${present ? "" : "disabled"} aria-pressed="${this.selected.has(item.gpu_id)}" title="${present ? "点击选择或取消选择" : "当前地区未列出该型号，可切换地区查看"}">★ ${e(item.name)}</button><button data-favorite="${e(item.gpu_id)}" aria-label="取消收藏 ${e(item.name)}">×</button></span>`;
          })
          .join("")
      : '<span class="market-muted">点击型号旁的 ☆ 收藏，常用显卡会显示在这里。</span>';
    $("#select-visible").checked =
      visible.length > 0 && visible.every((gpu) => this.selected.has(gpu.id));
    $("#select-visible").indeterminate =
      visible.some((gpu) => this.selected.has(gpu.id)) &&
      !$("#select-visible").checked;
  }

  toggle(id, checked) {
    const gpu = this.catalog?.gpus.find((gpu) => gpu.id === id);
    if (!gpu) return;
    if (checked) {
      this.selected.set(id, gpu);
      this.focused = id;
    } else {
      this.selected.delete(id);
      if (this.focused === id)
        this.focused = this.selected.keys().next().value || "";
    }
    this.page = 1;
    this.renderChoices();
    this.renderSelection();
  }

  async toggleFavorite(id, button) {
    const gpu = this.catalog?.gpus.find((gpu) => gpu.id === id);
    const saved = this.favorites.find(
      (item) => item.provider === this.provider && item.gpu_id === id,
    );
    if ((!gpu && !saved) || button.disabled) return;
    const wasFavorite = this.isFavorite(id);
    button.disabled = true;
    this.lastFavoriteWrite = Date.now();
    try {
      this.favorites = wasFavorite
        ? await api(
            `/favorites/${this.provider}?${new URLSearchParams({ gpu_id: id })}`,
            { method: "DELETE" },
          )
        : await put("/favorites", {
            provider: this.provider,
            gpu_id: id,
            name: gpu.name,
          });
      this.lastFavoriteWrite = Date.now();
      this.renderChoices();
      toast(wasFavorite ? "已取消收藏" : "已收藏，刷新和重启后仍会保留");
    } catch (error) {
      toast(error.message, true);
      button.disabled = false;
    }
  }

  renderSelection() {
    $("#selected-count").textContent = this.selected.size;
    $("#configure-rules").disabled = this.selected.size === 0;
    $("#resource-details").hidden = this.selected.size === 0;
    if (!this.selected.size) {
      this.detailVersion += 1;
      this.detailKey = "";
      return;
    }
    $("#selected-gpu-tabs").innerHTML = [...this.selected.values()]
      .map(
        (gpu) =>
          `<button data-focus="${e(gpu.id)}" class="detail-tab ${gpu.id === this.focused ? "active" : ""}" aria-pressed="${gpu.id === this.focused}">${e(gpu.name)}</button>`,
      )
      .join("");
    const gpu = this.selected.get(this.focused);
    $("#resource-detail-title").textContent = `${gpu.name} · 资源详情`;
    $("#resource-detail-description").textContent =
      `${gpu.memory_gb ? gpu.memory_gb + " GB 显存 · " : ""}${this.regionName()} · 空闲/总量 ${stock(gpu)}`;
    this.loadDetails();
  }

  regionName() {
    return (
      this.catalog.region_groups.find((group) => group.id === this.region)
        ?.name || "全部地区"
    );
  }

  async loadDetails(force = false) {
    if (!this.focused || !this.selected.has(this.focused)) return;
    const key = `${this.provider}:${this.region}:${this.focused}:${this.page}:${this.connectionSignature}`;
    if (!force && key === this.detailKey) return;
    this.detailKey = key;
    const version = ++this.detailVersion;
    $("#resource-prev").disabled = $("#resource-next").disabled = true;
    $("#resource-detail-content").innerHTML =
      '<div class="market-empty"><span class="spinner"></span>正在读取所选型号的资源与价格…</div>';
    $("#resource-page-info").textContent = "";
    try {
      const query = new URLSearchParams({
        gpu_id: this.focused,
        region: this.region,
        page: String(this.page),
      });
      const result = await api(`/resources/${this.provider}?${query}`);
      if (version !== this.detailVersion) return;
      $("#resource-detail-content").innerHTML = result.offers.length
        ? `<div class="table-wrap"><table class="resource-table"><thead><tr><th>${this.provider === "autodl-pro" ? "规格" : "主机"}</th><th>地区</th><th>GPU 显存</th><th>CPU / 内存</th><th>空闲 / 总量</th><th>每卡 / 小时</th></tr></thead><tbody>${result.offers.map((offer) => `<tr><td><code>${e(offer.id)}</code></td><td>${e(offer.region_name)}</td><td>${offer.memory_gb ? e(offer.memory_gb) + " GB" : "—"}</td><td>${e(offer.cpu)}<br><span class="market-muted">${e(offer.ram)}</span></td><td><span class="${offer.available > 0 ? "success-text" : ""}">${stock(offer)}</span></td><td>${offer.price_hour == null ? '<span class="market-muted">报价暂不可用</span>' : `<strong>¥ ${price(offer.price_hour)}</strong><small class="price-source">${{ account: "账号报价", public: "公开参考价", demo: "模拟报价" }[offer.price_source] || "参考价"}</small>`}</td></tr>`).join("")}</tbody></table></div>`
        : '<div class="market-empty">当前地区暂未查到该型号的主机。仍可配置监控，等待资源满足条件。</div>';
      $("#resource-detail-content").insertAdjacentHTML(
        "beforeend",
        `<p class="resource-detail-note">${result.notices.map(e).join(" ")}</p>`,
      );
      $("#resource-page-info").textContent =
        `共 ${result.total} 条资源 · 第 ${this.page} 页`;
      $("#resource-prev").disabled = this.page <= 1;
      $("#resource-next").disabled =
        this.page * result.page_size >= result.total;
    } catch (error) {
      if (version !== this.detailVersion) return;
      const message =
        error.status === 404
          ? "资源详情接口尚未更新，请重启工具服务并刷新页面。"
          : error.message;
      $("#resource-detail-content").innerHTML =
        `<div class="market-empty error-text">${e(message)}</div>`;
    }
  }
}
