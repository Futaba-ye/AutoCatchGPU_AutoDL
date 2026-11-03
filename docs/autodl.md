# AutoDL 接入说明

核实日期：2026-11-02。公开文档与官网前端会变化，接口映射统一放在 `catchgpu/providers/autodl.py` 和 `transport.py`。

## 接入方式

- 普通实例没有官方创建 API。工具通过系统启动本机 Chrome 或 Edge，使用独立配置目录，由用户手动完成安全认证。用户明确点击“登录完成，连接工具”之后，才通过本机 CDP 接口连接、刷新控制台并校验登录状态，然后通过该浏览器上下文调用官网接口。该会话不读取个人日常浏览器配置。
- Pro 按量实例在已配置开发者 Token 时使用 `api.autodl.com` 官方 API 创建及核对。若没有 Token，则使用已登录网页会话。
- Pro 包日、包周、包月使用网页登录，因为官方开发者创建 API 仅支持按量计费。
- 型号和规格来自实时目录，不用固定 GPU 型号清单。Pro 新规格通过官网规格 UUID 加入目录。

参考：[普通与 Pro 的区别](https://www.autodl.com/docs/instance_pro/)、[Pro API](https://www.autodl.com/docs/instance_pro_api/)、[通用 API](https://www.autodl.com/docs/common_api/)。

## 登录兼容性修正

旧版直接通过 Playwright 启动测试用 Chromium。用户反馈该窗口在手动拼图认证时持续失败，而日常浏览器可以通过；这将问题范围缩小到专用浏览器环境，尚未取得平台具体风控错误码。

新版将人工登录和程序连接分为两个阶段，并可选择本机 Chrome 或 Edge。打开窗口时不启动 Playwright 驱动，不注入脚本，也不操作验证码。用户完成登录后，再通过仅绑定 `127.0.0.1` 的连接端口接入；Chrome 与 Edge 分别使用 `data/browser/native/<浏览器>/` 目录，连接参数文件仅保存端口号，不保存 Token。

程序不会把“捕获到请求头”直接当作登录成功。只有控制台发出的授权请求被捕获，并且只读实例列表请求返回成功，才将会话标记为已连接。连接失败时只断开工具，保留用户浏览器窗口；登录页面尚未进入控制台时不刷新该页面。

服务重启后可重新使用仍打开的专用窗口；退出服务不强制关闭浏览器。旧版的资料目录不删除，新版也不连接日常浏览器配置。相关接口能力参见 [Playwright CDP 文档](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp)，独立配置目录要求参见 [Chrome 官方说明](https://developer.chrome.com/blog/remote-debugging-port)。

## 官方开发者接口

鉴权使用 `Authorization: 开发者Token`，不添加 `Bearer` 前缀。创建使用 `POST /api/v1/dev/instance/pro/create`，字段包括地区列表、每实例 GPU 数量、规格 UUID、镜像 UUID、额外系统盘 GB、CUDA 下限及唯一备注名称。

实例列表使用 `POST /api/v1/dev/instance/pro/list`，逐页查找工具生成的唯一名称；私有镜像使用 `POST /api/v1/dev/instance/pro/image/private/list`。连接测试使用 `POST /api/v1/dev/wallet/balance`，不创建或修改资源。

弹性部署的 GPU 库存接口对应另一种产品，不能直接当作 Pro 库存接口使用。本项目不借用弹性部署创建容器，也不要求企业弹性部署权限。

## 官网接口与价格

普通目录使用 `/api/v1/machine/gpu_type` 和 `/api/v1/region/list`。登录后的普通库存来自 `/api/v1/user/machine/list`，通过 GPU 名称、地区列表、卡数及计费方式筛选。报价来自 `/api/v1/order/price/preview`。

Pro 型号库存来自 `/api/v1/machine_pro/gpu_type`，规格来自 `/api/v1/machine_pro/gpu_spec_config`。网页登录报价使用 `/api/v1/order/price/preview/pro`。Pro 目录的公开库存可能受登录策略影响，存在登录会话时优先使用该会话读取。

普通创建请求包含 `instance_info` 与 `price_info`。数据盘容量以字节传入，`req_gpu_amount` 是每实例卡数。私有镜像写入 `private_image_uuid`，基础镜像写入 `base_image_uuid`。Pro 网页请求使用数值规格 ID、含基础容量的系统盘字节数；官方 API 则使用规格 UUID 和额外扩容 GB，两者不能混用。

官网金额使用元的千分之一。报价明细按产品拆分为实例与磁盘费用；预付费按所选周期折算时价。Pro 官方 API 没有报价预览，保守使用公开规格时价；包含磁盘时根据当前系统盘阶梯价格计算基础容量和额外容量。

预付费先创建订单，再对返回的订单 UUID 支付。创建订单后任何支付异常都视作结果待核对，避免多次生成订单或重复付费。仅有明确实例 ID 才记录为创建结果；这代表平台已经接受并分配实例，不代表镜像已启动完毕或 SSH 已可连接。

## 真实账号联调

以下步骤需真实 AutoDL 登录及用户选择的价格限制；自动化测试用模拟响应验证，不代表已经在真实账号执行：

1. 比较网页与工具的 GPU 名称、地区、当前库存和账号报价。
2. 验证专用浏览器登录、登录持久化以及会话失效后的提示。
3. 使用一个最小目标的按量任务验证镜像、磁盘和创建结果。
4. 检查桌面通知与语音关闭开关，再验证不同计费方式和多卡配置。
5. 如要验证支付路径，先核实预付费周期与账单金额。
6. 模拟断网或超时，检查待核对状态、平台订单及恢复流程，确认不会自动重发。

在平台接口变化时，优先更新适配器与字段测试。不要把缺失价格当作 0，不要用演示数据填补实时目录失败，也不要用重试创建代替结果核对。
