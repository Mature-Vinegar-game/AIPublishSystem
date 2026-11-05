# 印制参数校验与成本测算 AI 系统

>按本文从零开始，可以在 5–10 分钟内把系统跑起来，并走完“创建任务 → AI 测算 → 查看依据 → 人工确认 → 审核”的完整流程。
>
> 业务方向：出版印制环节的**参数校验与成本测算**。系统使用 FastAPI + SQLite + Jinja2 服务端页面 + DeepSeek（OpenAI 兼容接口），AI 通过**真实工具调用**查资料库/规则库、做校验和计算；结果与依据可追溯，人工确认与审核流程完整。

---

## 1. 5 分钟快速开始（TL;DR）

> 以下命令在**项目根目录**执行（即包含 `app/`、`seed/`、`requirements.txt` 的目录）。
> 本项目不需要 Node.js、Redis、Docker；Python 3.10+ 即可（已在 Python 3.14 验证）。

```bash
# 1) 安装依赖
python -m pip install -r requirements.txt

# 2) 准备配置文件（不要提交 .env）
# Windows:
copy .env.example .env
# macOS / Linux:
# cp .env.example .env
```

编辑 `.env`，至少填写：

```dotenv
SECRET_KEY=换成一串随机字符
LLM_MODE=real
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat          # 按你的账号实际模型名填写
LLM_API_KEY=你的 API Key
```

```bash
# 3) 初始化演示数据（幂等，可重复执行）
python seed/seed_demo.py

# 4) 启动（二选一）
# Windows 一键：
start.bat
# 或手动（Windows / macOS / Linux 通用）：
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

浏览器打开 **<http://127.0.0.1:8000>**，使用演示账号登录：

| 账号 | 密码 | 角色 |
|---|---|---|
| `operator` | `123456` | 业务操作员（张伟） |
| `reviewer` | `123456` | 审核员（李静） |

- 先打开「系统信息」页，确认 `模型模式 = real`、`Key 已配置`；
- 打开示例任务 `BT-DEMO-0001`，点「运行 AI 测算」，即可看到真实工具调用与结果；
- 启动后还可访问：接口文档 `/api/docs`、三张图 `/diagrams`、仪表盘 `/dashboard`、效果评测 `/evaluation`。

> `start.bat` 会依次执行：环境预检 → 演示数据初始化 → 启动 uvicorn（单进程、单 worker）。**必须单 worker**：AI 任务在进程内后台线程执行。

---

## 2. 这个系统做什么

任务书要求的核心流程：**创建任务 → 填写需求或导入数据 → 调用工具处理 → 查看结果和依据 → 人工修改或确认 → 审核 → 查询历史记录**。

系统在这个流程上提供：

| 能力 | 说明 |
|---|---|
| 两类角色 + 后端权限 | operator 负责建任务/运行 AI/保存结果；reviewer 负责通过/退回；角色、对象归属、状态均在后端校验 |
| 业务任务管理 | 标题、目标、开本、印张、印数、纸张、装订、色数、工艺；保存/修改/列表/检索 |
| 资料与数据管理 | 纸张、工艺、装订、费率报价；资料带编号、来源、版本、启用状态；页面可新增、可停用/启用 |
| 规则与数据管理 | 参数校验规则、成本口径规则；页面可新增/启停；校验试算页可直接看规则命中 |
| AI 与真实工具调用 | 模型自主选择工具和参数：`search_quotes` / `get_rules` / `validate_spec` / `calculate_cost`；后端真实执行并落库 |
| 结果与依据 | 成本说明、结构化明细、总成本/单册成本、逐条依据（资料/规则/计算记录）可点击查看 |
| Agent 执行记录 | 每次工具调用的工具名、参数、结果摘要、来源 ID、状态、错误码、耗时；点击结果摘要可看完整详情 |
| 人工确认与版本 | 采用 AI 结果或人工保存为新版本；版本只增不改；提交后冻结，至少保留两个可查版本 |
| 审核 | reviewer 通过/退回（退回必填意见）；退回后可修改/重跑/重新提交 |
| 方案模式（增强） | 纸张/装订/工艺可留白 + 预算，系统枚举资料库组合并真实计算，返回 1–3 个可行方案 |
| 查询与展示 | 任务检索/状态筛选/排序/关键词高亮、仪表盘、版本对比、效果评测、导出 Markdown、打印结果、三张图演示 |

---

## 3. 环境要求与安装

### 3.1 环境要求

| 项 | 要求 |
|---|---|
| 操作系统 | Windows / macOS / Linux（演示机为 Windows） |
| Python | 3.10+，推荐 3.11–3.14（本项目在 3.14 验证） |
| 网络 | 真实模型模式需要能访问 `LLM_BASE_URL`；无网络时可用 `LLM_MODE=mock` 开发 |
| 其他 | 不需要 Node/npm、Redis、消息队列、Docker |

### 3.2 创建虚拟环境（推荐）

```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
```

### 3.3 安装依赖

```bash
python -m pip install -r requirements.txt
```

无网络的离线安装可选：

```bash
python -m pip install -r requirements.txt --target vendor
# Windows 设置 PYTHONPATH 后运行
set PYTHONPATH=%CD%\vendor
# macOS / Linux:
# export PYTHONPATH="$PWD/vendor"
```

### 3.4 配置 `.env`

`.env.example` 是模板；复制为 `.env` 后填写。**`.env` 不要提交、不要截图、不要打包。**

| 变量 | 说明 | 示例/默认 |
|---|---|---|
| `APP_ENV` | 运行环境；`demo` 允许故障注入演示 | `demo` |
| `DATABASE_URL` | SQLite 地址（相对项目根） | `sqlite:///./data/app.db` |
| `SECRET_KEY` | Cookie 签名密钥，随机串，必改 | `replace-with-random-string` |
| `LLM_MODE` | `real` 真实模型（验收固定）；`mock` 确定性模拟 | `real` |
| `LLM_BASE_URL` | OpenAI 兼容接口地址 | `https://api.deepseek.com/v1` |
| `LLM_MODEL` | 模型标识，按账号实际名称填写 | `deepseek-chat`（示例） |
| `LLM_API_KEY` | 模型 Key，只放本机 `.env` | `sk-...` |
| `LLM_TIMEOUT_S` | 单次模型调用超时 | `30` |
| `TOOL_TIMEOUT_MS` | 单次工具执行超时 | `3000` |
| `AI_RUN_TIMEOUT_S` | 单次 AI 运行总时长 | `120` |
| `MAX_TOOL_CALLS` | 单次运行工具调用上限 | `10` |
| `MAX_LLM_ROUNDS` | 单次运行模型轮次上限 | `8` |
| `MAX_AI_RUNS` | 单任务 AI 运行次数上限 | `10` |

> 换模型只改 `LLM_BASE_URL / LLM_MODEL / LLM_API_KEY` 三个变量并重启。
> 真实模式且未配置 Key 时，系统信息页会如实显示 `key_configured=false`，AI 运行会失败——这不是系统故障。

### 3.5 初始化演示数据

```bash
python seed/seed_demo.py
```

会幂等写入：2 个账号、**30 条资料**（18 纸张 / 6 工艺 / 5 装订 / 1 费率）、**22 条规则**、示例任务 `BT-DEMO-0001`。

### 3.6 启动

```bash
# Windows
start.bat

# 或手动（所有平台一致；必须 --workers 1，不要 --reload）
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

打开 <http://127.0.0.1:8000>。

### 3.7 启动后自检

```bash
# 演示前环境/数据/材料自检（21 项）
python scripts/demo_check.py

# 全功能 HTTP 自检（38 项：页面/数据/权限/试算/评测/方案模式/版本等）
python scripts/full_check.py

# 单元/接口测试
python -m pytest tests -q
```

正常结果分别为 **21/21、38/38、23 passed**。自检失败时会列出具体项。

### 3.8 重置演示数据

```bash
python scripts/reset_demo.py --yes
```

会删除 `data/app.db` 并重新建表 + 写入 seed 数据；`.env`、日志不受影响。

---

## 4. 第一次使用：推荐演示路线（约 5 分钟）

1. 登录 `operator/123456`；
2. 打开「系统信息」：确认 `real / 模型名 / Key 已配置 / 限额`；
3. 任务列表搜索 `BT-DEMO-0001` → 打开：
   - 结果区看总成本/单册/成本明细/依据引用；
   - ⑤ 工具执行记录：点“结果摘要”可看完整参数与返回；
   - ⑥ 结果版本：打开“版本对比”；
4. 新建任务：16开 / 12印张 / 3000册 / 胶版纸80g / 胶装 / 四色 / 覆膜 → 运行 AI；
   预期：**14638.00 元 / 单册 4.88 元**；
5. 采用 AI 结果 → 保存人工版本 v2 → 提交审核；
6. 退出，用 `reviewer/123456` 登录 → 打开任务 → 通过或退回（退回写意见）；
7. 可选：
   - 「方案模式」新建任务：纸张/装订/工艺留白，填预算（如 15000），运行 AI 得到多个方案；
   - 「校验试算」：不运行 AI，直接看规则命中；
   - 「效果评测」：4 个 benchmark 与预期值自动比对；
   - 「仪表盘」：任务/AI/工具/依据/趋势统计。

---

## 5. 页面与功能入口

| 页面 | 路径 | 用途 |
|---|---|---|
| 登录 | `/login` | 两种角色登录 |
| 任务列表 | `/tasks` | 检索、状态筛选、排序、关键词高亮、分页 |
| 新建任务 | `/tasks/new` | 标准参数任务 |
| 方案模式 | `/tasks/new-plan` | 参数可留白 + 预算，生成候选方案 |
| 任务详情 | `/tasks/{id}` | AI 运行、结果/依据、执行记录、版本、审核 |
| 校验试算 | `/validate` | 直接调用 `validate_spec`，不运行模型 |
| 效果评测 | `/evaluation` | 固定 benchmark 与预期结果比对 |
| 仪表盘 | `/dashboard` | 任务/AI/工具/依据统计、趋势、失败原因 |
| 资料库 | `/materials` | 新增/查看/启停资料与报价 |
| 规则库 | `/rules` | 新增/查看/启停规则 |
| 系统信息 | `/system` | 真实模式、模型、Key、限额自证 |
| 三张图 | `/diagrams` | 系统功能图/技术调用图/数据处理与工具执行图 |

结果区支持：Markdown 排版、导出 Markdown、打印结果；任务有 ≥2 个版本时可进入**版本对比**；退回任务可**按审核意见重新运行**。

---

## 6. 成本口径与业务规则

### 6.1 成本公式（演示口径）

- 用纸量（令）= `ceil(印数 × 印张 ÷ 1000 × 1.05)`
- 纸张费 = 用纸量 × 纸张单价（元/令）
- 印刷费 = `印数 ÷ 1000 × 印张 × 单色千印张价 × 色数档`
- 制版费 = `色数档 × 80 元/色`
- 装订费 = `印数 ÷ 1000 × 装订单价`
- 工艺费 = `印数 ÷ 1000 × 工艺单价`（无工艺为 0）
- 总成本 = 五项之和；单册成本 = 总成本 ÷ 印数（保留 2 位）

色数档：`色数 > 0` 取 4，否则取 1。

### 6.2 状态与版本

```
业务任务：PENDING → PENDING_REVIEW → COMPLETED
                     ↘ REJECTED → 修改/重跑 → 再提交
AI 任务：PENDING → RUNNING → SUCCEEDED / FAILED（可重试）
```

- 版本只增不改；提交审核后对应版本冻结（再保存返回 409）；
- AI 运行只新增结果，不自动生成版本；采用/人工保存才生成版本；
- 提交时校验依据有效性：引用的资料/规则被停用/删除 → 409 `BASIS_MISMATCH`。

### 6.3 权限

- 后端逐接口校验角色 + 对象归属 + 状态；
- 角色不足 → 403；对象不属于本人/不在审核范围 → 404；状态冲突/重复提交 → 409；超过 AI 运行上限 → 429。

---

## 7. 资料库、规则库与方案模式

- 演示数据：**30 条资料**、**22 条规则**，均标注“模拟数据，仅用于演示”；
- 资料/规则均可在页面新增，并可停用/启用；停用被引用的资料后提交会触发 409；
- 方案模式（`print_cost_plan`）：
  - 开本/印张/印数/色数必填；纸张/装订/工艺可留白；预算可选；
  - 服务端先从资料库枚举组合，按估算成本排序，只对最多 **24 个候选**做完整规则校验和成本计算；
  - 返回 **1–3 个**方案（最多 5），预算内无方案时给“最接近方案 + 差额”，不会编造；
  - 选择方案后生成版本 → 提交/审核与标准任务完全一致；
  - 标准 `print_cost` 主流程不受影响。

---

## 8. 项目结构（相对路径）

```
.
├─ app/
│  ├─ main.py              # FastAPI 入口：页面路由、仪表盘/试算/评测/对比页、异常处理、日志
│  ├─ config.py            # .env 配置
│  ├─ db.py                # SQLite engine/session/建表
│  ├─ models.py            # 10 张表
│  ├─ schemas.py           # Pydantic 请求模型
│  ├─ auth.py              # 密码哈希、Cookie 会话、角色依赖
│  ├─ api/                 # auth/tasks/results/materials/rules/system/ai
│  ├─ services/            # 任务/版本/依据服务
│  ├─ agent/               # Agent 编排、LLM 客户端、工具注册、提示词
│  ├─ templates/           # Jinja2 页面
│  └─ static/              # style.css / app.js
├─ seed/                   # 纸张/工艺/装订/费率/规则 + seed_demo.py
├─ scripts/                # preflight / reset_demo / demo_check / full_check / final_submit 等
├─ tests/                  # API 冒烟、工具单测、方案模式单测
├─ docs/                   # 需求设计、三张图、数据字典、测试/缺陷/分工/验收记录
├─ data/                   # 运行时生成 app.db（不提交）
├─ logs/                   # 运行时日志 app.log（不提交）
├─ requirements.txt
├─ start.bat               # Windows 一键启动
├─ .env.example            # 配置模板（无真实 Key）
└─ README.md
```

---

## 9. 测试与验收

| 项 | 命令 | 当前结果 |
|---|---|---|
| 单元/接口测试 | `python -m pytest tests -q` | 23 passed |
| 全功能 HTTP 自测 | `python scripts/full_check.py` | 38/38 通过 |
| 演示前自检 | `python scripts/demo_check.py` | 21/21 通过 |
| 手工测试用例 | `docs/06_测试用例.md` | TC01–TC12（9 类场景覆盖） |
| 验收自检 | `docs/12_验收自检报告.md` | 逐项对应任务书 |
| 全功能记录 | `docs/19_全功能自测记录.md` | 页面/浏览器逐项结果 |

真实模型运行参考：`BT-DEMO-0001` 约 15–20 秒，9 次工具调用 / 3 类工具，总成本 14638.00 元、单册 4.88 元；所有金额与依据由 `calculate_cost` 和资料库真实产生。

---

## 10. 常见问题

| 问题 | 处理 |
|---|---|
| `ModuleNotFoundError: fastapi` | 依赖未安装：在当前 Python 环境执行 `python -m pip install -r requirements.txt`；离线安装时确认设置了 `PYTHONPATH` |
| 端口 8000 被占用 | 换个端口：`python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --workers 1` |
| `database is locked` | 确认只跑了单 worker；关闭其它占用 `data/app.db` 的进程 |
| AI 一直 RUNNING | 看 `logs/app.log` 与 `/api/ai-tasks/{id}` 的 `error_code`；重启后启动清理会标记 `STALE_RUNNING` |
| 真实模式 AI 失败 | 检查 `.env` 的 `LLM_API_KEY / LLM_BASE_URL / LLM_MODEL`；系统信息页 `key_configured` 应为 true |
| 提交审核 409 | 正常保护：重复提交/状态不允许；若提示依据失效，请重新运行 AI 或恢复被停用资料 |
| 页面还是旧样式/旧脚本 | 浏览器缓存：`Ctrl + F5` 强制刷新（或换无痕窗口） |
| 依赖安装慢/无网 | 离线安装到 `vendor/` 后设置 `PYTHONPATH`；或先在可联网机器下载依赖包 |
| 重置演示数据 | `python scripts/reset_demo.py --yes` |

---

## 11. 安全与提交注意

- `.env`、`data/`、`logs/`、`vendor/`、`__pycache__/`、`.venv/` 不入库；
- 源码与提交包中**不得**出现真实 API Key；提交前可检查 `sk-` 长字符串；
- 演示数据均为模拟数据，不可用于真实出版报价；正式使用需替换当期真实工价单；
- 服务默认仅监听 `127.0.0.1`，不对外网开放；
- 方案模式、仪表盘、校验试算、效果评测、版本对比均为**只读或附加功能**，不改变标准任务主流程。

---

## 12. 文档索引

| 文档 | 内容 |
|---|---|
| `docs/01_需求与设计说明.md` | 需求、角色、流程、验收范围 |
| `docs/02~04` + `docs/三张图演示.html` | 三张图（系统功能图/技术调用图/数据处理与工具执行图） |
| `docs/05_数据字典.md` | 10 张表字段与约束 |
| `docs/06_测试用例.md` | TC01–TC12（9 类场景） |
| `docs/07_缺陷与回归记录.md` | 缺陷台账与回归 |
| `docs/08_分工与开发记录.md` | 小组分工与开发记录 |
| `docs/11_运行记录_三类脱敏.md` | 正常/数据不足/失败 3 类记录 |
| `docs/12_验收自检报告.md` | 对照任务书逐项自检 |
| `docs/17_方案模式说明.md` | 方案模式设计与演示任务 |
| `docs/18_界面与仪表盘说明.md` | 结果排版、仪表盘、校验试算、版本对比等 |
| `docs/19_全功能自测记录.md` | 38 项 HTTP + 浏览器逐项自测 |

如果准备现场演示，另见 `交付材料/04_测试与交付材料/` 中的演示运行手册、备用录屏与验收材料。
