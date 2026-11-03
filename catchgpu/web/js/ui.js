// 动态文本统一转义，平台名称、日志及镜像名称均视为外部数据。
export const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ],
  );
export const $ = (selector) => document.querySelector(selector);
export const time = (value) =>
  value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
export const price = (value) =>
  Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 4 });

const paths = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  chip: '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="10" y="10" width="4" height="4"/><path d="M9 3v3m6-3v3M9 18v3m6-3v3M3 9h3m-3 6h3m12-6h3m-3 6h3"/>',
  activity: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  terminal:
    '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="m7 9 3 3-3 3m6 0h4"/>',
  settings:
    '<path d="m10 3-.6 2.3-2 .9-2.2-.6-2 3.5L5 11v2l-1.8 1.9 2 3.5 2.2-.6 2 .9L10 21h4l.6-2.3 2-.9 2.2.6 2-3.5L19 13v-2l1.8-1.9-2-3.5-2.2.6-2-.9L14 3z"/><circle cx="12" cy="12" r="3"/>',
  monitor:
    '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4"/>',
  external:
    '<path d="M14 3h7v7m0-7-11 11M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5"/>',
  refresh:
    '<path d="M20 10a8 8 0 0 0-14-5L3 8m0-5v5h5m-4 6a8 8 0 0 0 14 5l3-3m0 5v-5h-5"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  check: '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
  bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
  radar:
    '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><path d="m12 12 7-7"/><circle cx="12" cy="12" r="1"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  layers: '<path d="m12 3 10 5-10 5L2 8zm-10 9 10 5 10-5M2 16l10 5 10-5"/>',
  link: '<path d="m10 13 4-4m-6 6-2 2a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m4 2 2-2a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0" transform="translate(1 1) scale(.9)"/>',
};
export const icon = (name) =>
  `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.chip}</svg>`;
export function hydrateIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach((el) => {
    el.innerHTML = icon(el.dataset.icon);
  });
}
export function toast(message, error = false) {
  const el = document.createElement("div");
  el.className = `toast ${error ? "toast-error" : ""}`;
  el.textContent = message;
  $("#toast-stack").append(el);
  setTimeout(() => el.remove(), error ? 9000 : 5000);
}
export async function busy(button, action) {
  if (button.disabled) return;
  button.disabled = true;
  try {
    return await action();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

export function logMessage(log) {
  const subject =
    log.subject ||
    /^((?:RTX|GTX|NVIDIA|Tesla|vGPU|GPU|CPU|A\d|H\d)[^：:]{0,110})[：:]/i.exec(
      log.message,
    )?.[1] ||
    "";
  const index = subject ? log.message.indexOf(subject) : -1;
  if (index < 0) return escape(log.message);
  let hash = 0;
  for (const character of subject)
    hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  return `${escape(log.message.slice(0, index))}<strong class="log-gpu gpu-color-${hash % 4}">${escape(subject)}</strong>${escape(log.message.slice(index + subject.length))}`;
}
