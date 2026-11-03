# CatchGPU

在 Windows 本地运行的 GPU 资源监控工具。读取 AutoDL 型号目录，批量配置监控限制，找到符合条件的资源后创建实例；每条规则达到实例数量目标后停止，全部规则完成后结束任务。

## 本地启动

需要 Python 3.11 或以上版本。网页登录需要本机已安装 Google Chrome 或 Microsoft Edge。首次运行双击 `setup.cmd` 安装依赖；安装完成后双击 `start.cmd`。页面地址为 [http://127.0.0.1:8765](http://127.0.0.1:8765)。保留启动窗口，按 Ctrl+C 停止服务。

也可以从项目根目录运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

## 使用流程

1. 打开“连接与通知”。Pro 按量任务优先使用开发者 Token，获取位置是 AutoDL 控制台 → 账号 → 设置 → 开发者 Token。填写后点击测试连接。
2. 普通实例和 Pro 预付费任务使用两步登录：在“连接与通知”选择本机 Chrome 或 Edge，点击“1. 打开浏览器登录”，手动完成安全认证并进入 AutoDL 控制台；然后回到工具点击“2. 登录完成，连接工具”。工具会刷新控制台并用只读请求校验登录状态，成功后才显示已连接。请保留专用窗口。Pro 公开库存可能受平台登录策略影响，登录后优先读取账号库存。
3. 在“GPU 资源”先选择地区，再从紧凑的型号列表中勾选显卡。括号内为当前地区“空闲/总量”；选中后才读取详细主机、CPU、内存和每卡时价，可切换选中型号并分页。点击“批量设置限制”时会默认带入当前地区，仍可逐行修改。批量表单支持每实例卡数、目标实例数、价格上限、价格口径、镜像、磁盘、计费周期及 CUDA 下限。
4. 每种 GPU 的目标独立计算。例如 RTX 4090 设置“每实例 2 卡、目标 3 实例”，会尝试创建 3 个各含 2 卡的实例；同时选择 A100 会额外完成 A100 对应的目标。多个型号并非互相替代。
5. 可以仅保存任务，也可以保存并开始监控。日志会显示每轮检测和创建结果。所有目标完成后自动停止。
6. Windows 原生桌面通知默认开启，网页语音默认关闭，均可在“连接与通知”修改。通知失败会写入日志，实例进度仍会保留。

首次体验可以切换到“演示环境”。演示数据有明确标记，不访问 AutoDL，不创建真实实例，也不产生费用。

## 收藏与资源同步

点击型号旁的 ☆ 收藏，再点 ★ 取消。收藏按平台分别保存在 SQLite，刷新、服务重启或更换浏览器后仍然保留。“我的收藏”提供快捷点选，“仅看收藏”可缩小型号范围，收藏不影响已有任务或监控数量。

如果网页已经登录但工具尚未连接，可以在资源区点击“同步已登录窗口”。工具会尝试找回自己先前记录的专用窗口；存在多个窗口时，会要求在“连接与通知”中明确选择，避免连接到其他账号。日常浏览器的登录状态不会被自动读取。

资源区会区分已连接、未连接和 Pro API 的公开参考数据；连接状态变化后自动刷新目录。没有取得价格时显示“报价暂不可用”，不会再把缺少价格误显示为未登录。主机报价来自账号价格预览，若使用公开参考价则单独标明。

## 价格与磁盘

- 价格上限由用户填写，没有隐含的默认价格。支持“每张 GPU / 小时”和“每个实例 / 小时”。
- 包日、包周、包月分别按 24、168、720 小时折算，再按周期数量计算每小时价格。上限控制的是折算价格，预付费仍会支付整个所选周期的费用。
- “包含磁盘费用”会将实例的磁盘费用计入对应口径。两张卡的实例若磁盘每小时共 1 元，按每卡口径会为每张卡分摊 0.5 元。
- 普通实例填写额外数据盘容量；Pro 填写额外系统盘容量，包含磁盘费用时也计算 Pro 基础 30 GB 系统盘费用。
- 未取得有效报价，或者勾选包含磁盘但缺少磁盘报价时，不会创建实例。
- Pro 官方开发者 API 尚无创建前报价接口。API 按量任务使用实时公开未折扣价格作为保守上界，可能因此错过实际折后价格符合限制的资源。网页登录模式使用账号价格预览。
- 价格是在发送创建请求前校验；AutoDL 没有公开的原子价格锁定接口。平台最终账单以 AutoDL 为准。

## 运行行为

关闭网页不会停止后台监控。关闭服务、电脑休眠或断网会影响检测；不要依赖休眠期间继续运行。服务重启后任务会暂停，避免在用户未查看状态时恢复创建。

创建前会将唯一请求名称写入 SQLite。请求超时、服务中断或支付结果不明确时，任务进入“需要关注”，不会盲目重发。点击“核对结果”按名称从 AutoDL 实例列表核对；仍未找到时，需在官网检查实例与待支付订单。只有确认均不存在，才能标记“已确认没有实例和未支付订单”，再恢复任务。

“暂停”停止后续检测和创建；已经发给平台的请求会完成结果记录。工具不会自动关机、释放或删除实例。

开发者 Token 只存于当前进程内，不写入任务、日志和网页本地存储。也可用 `AUTODL_TOKEN` 环境变量注入。网页登录资料按浏览器分别保存在 `data/browser/native/`，任务、通知偏好和日志保存在 `data/catchgpu.db`，该目录已忽略提交。备份请先停止服务并关闭工具的专用浏览器窗口，再备份整个数据目录。退出服务时只断开工具连接，不强制关闭你正在操作的浏览器；重启服务后可重新打开并连接尚存的专用窗口。

如果旧版工具的 Chromium 窗口在拼图认证阶段持续失败，而日常浏览器可以通过，请先关闭旧窗口并重启工具，再按以上两步使用本机 Chrome 或 Edge。登录资料使用新目录，不会删除旧资料或读取日常浏览器的账号。是否通过安全验证仍由 AutoDL 决定；工具不会自动处理验证码。

## 项目结构

```text
catchgpu/
  app.py                 HTTP 接口、依赖装配与访问检查
  config.py              运行参数
  models.py              平台无关的数据契约与表单校验
  storage.py             SQLite 持久化和创建意图事务
  scheduler.py           检测、创建、暂停及结果核对
  notifications.py       桌面通知渠道
  locking.py             单进程数据目录锁
  providers/
    base.py              平台能力与错误类型
    transport.py         开发者 API 连接
    autodl_response.py   平台响应与错误分类
    browser_launcher.py 本机浏览器发现、启动与专用窗口复用
    browser_session.py  手动确认后的会话连接与校验
    market_data.py      地区分组与只读市场筛选参数
    autodl.py            AutoDL 普通/Pro 字段及请求映射
    demo.py              独立演示平台
  web/
    index.html           界面结构
    styles.css           响应式样式
    js/api.js            浏览器 HTTP 客户端
    js/rules.js          批量及逐行限制表单
    js/resources.js      地区、紧凑型号、按需详情与收藏
    js/ui.js             安全渲染、图标和提示
    js/app.js            视图、任务操作及状态更新
tests/                   模拟适配器、调度、价格及接口测试
docs/                    架构与平台接入说明
```

前端使用原生 JavaScript 模块，不需要 Node.js 或构建步骤。后端使用 FastAPI、异步 HTTP 和 SQLite；本机浏览器由系统独立启动，登录完成后通过 Playwright 连接。平台请求与通用调度之间通过 `Provider` 接口隔离。

## 开发验证

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

测试不连接真实账号，不执行真实创建和支付。页面验收脚本为 `tests/browser_check.py`，只操作演示平台。先在独立终端启动隔离服务：

```powershell
$env:CATCHGPU_PORT = '8766'
$env:CATCHGPU_DATA_DIR = '.tmp\browser-data'
.\.venv\Scripts\python.exe -m catchgpu
```

页面测试需要额外执行 `.\.venv\Scripts\python.exe -m playwright install chromium` 安装测试浏览器，再从项目根目录运行 `.\.venv\Scripts\python.exe tests/browser_check.py`。可用 `CATCHGPU_TEST_URL` 修改测试地址。截图输出到 `.tmp/browser-artifacts/`。登录界面专项测试为 `tests/browser_login_ui_check.py`；原生浏览器连接检查使用 `python -m tests.native_browser_check`，只创建独立空白窗口，不登录真实账号。

资源界面专项测试为 `python -m tests.market_ui_check`，覆盖地区、收藏、详情和手机布局；`python -m tests.session_market_ui_check` 使用模拟响应验证同步登录后自动刷新账号资源。这两个脚本默认访问隔离服务的 8768 端口，也可通过 `CATCHGPU_TEST_URL` 指定地址。

普通及 Pro 型号目录、地区目录、Pro 规格结构已对官方公开站点做只读核实。已登录账号的普通实例库存与报价也已完成只读联调。自动创建及支付路径以当前官网公开脚本和官方 API 文档为依据；真实账号下的创建、支付与通知效果仍需联调。

## 服务器部署

配置通过环境变量注入：`CATCHGPU_HOST`、`CATCHGPU_PORT`、`CATCHGPU_DATA_DIR`、`CATCHGPU_ACCESS_TOKEN`、`AUTODL_TOKEN`。浏览器安装位置不在常规路径时，可通过 `CATCHGPU_BROWSER_PATH` 指定 Chromium 系浏览器的完整可执行文件路径。无需打开浏览器时使用 `python -m catchgpu`。

监听非本机地址时必须设置 `CATCHGPU_ACCESS_TOKEN`，用户打开网页后输入该工具访问密钥。通过反向代理提供 HTTPS，保持网页与 API 同源。每个数据目录只允许一个服务进程，启动入口固定使用一个工作进程；扩容设计见 [架构说明](docs/architecture.md)。

```powershell
$env:CATCHGPU_HOST = '0.0.0.0'
$env:CATCHGPU_ACCESS_TOKEN = '替换为足够长的随机密钥'
$env:AUTODL_TOKEN = '替换为你的开发者Token'
.\.venv\Scripts\python.exe -m catchgpu
```

也提供了 Dockerfile：

```text
docker build -t catchgpu .
docker run --rm -p 127.0.0.1:8765:8765 -v catchgpu-data:/data -e CATCHGPU_ACCESS_TOKEN -e AUTODL_TOKEN catchgpu
```

服务器的原生通知能力与 Windows 本地不同：桌面通知回退为已授权的浏览器通知，网页需保持打开；语音同样需要网页打开。手动登录模式需要服务器提供图形桌面和可访问的浏览器窗口，纯无界面容器不会凭空提供登录页面。Pro 按量 API 模式可在无图形环境运行，库存可见性取决于平台公开目录策略。

## 扩展与维护

新增平台、通知渠道和替换存储的步骤见 [架构说明](docs/architecture.md)。AutoDL 已核实接口、字段口径及联调清单见 [AutoDL 接入说明](docs/autodl.md)。测试结果和界面截图见 [验证记录](docs/validation.md)。
