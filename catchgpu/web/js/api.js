// 所有服务请求从这里发出，服务器部署时使用同一来源与访问密钥。
export async function api(path, options = {}) {
  const headers = { ...options.headers };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  const token = sessionStorage.getItem("catchgpu-access");
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`/api${path}`, {
    ...options,
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(result.detail || "请求失败，请稍后重试");
    error.status = response.status;
    if (response.status === 401)
      window.dispatchEvent(new Event("access-required"));
    throw error;
  }
  return result;
}

export const post = (path, body) => api(path, { method: "POST", body });
export const put = (path, body) => api(path, { method: "PUT", body });
