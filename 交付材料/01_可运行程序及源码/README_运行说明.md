# 印制参数校验与成本测算 AI 系统

基于 **FastAPI + SQLite + Jinja2 + OpenAI 兼容模型接口** 的单体后端系统：业务操作员提交印制参数，AI 通过**真实工具调用**完成"参数校验 → 报价检索 → 成本测算"，输出中文 Markdown 测算说明与结构化明细，并保证每条依据可追溯（资料编号/规则编号/计算记录）；审核员按版本审核通过与退回，版本只增不改、已提交冻结。

> 本仓库按 `BUILD_SPEC.md` 开发，面向 Windows + Python 3.14（源码兼容 Python 3.10+；依赖可用 `pip install -r requirements.txt` 安装，也可离线安装到 `vendor/`），无需 Node/npm、Redis、消息队列、Docker。

---

## 1. 目录结构

```
出版AI系统/
├─ README.md  start.bat  requirements.txt  BUILD_SPEC.md  .env / .env.example
├─ app/
│  ├─ main.py            # FastAPI 入口：页面路由、异常处理、日志、中间件、路由挂载
│  ├─ config.py          # .env 配置（settings）
│  ├─ db.py              # Engine / SessionLocal / get_db / init_db（WAL + busy_timeout + foreign_keys）
│  ├─ models.py          # 10 张表（SQLAlchemy 2.x）
│  ├─ schemas.py         # Pydantic v2 请求模型
│  ├─ auth.py            # PBKDF2 密码 + HMAC-SHA256 签名 Cookie + 角色依赖
│  ├─ api/               # REST 接口：auth/tasks/results/materials/rules/system + ai（S2）
│  ├─ services/          # task_service / version_service / basis_service
│  ├─ agent/             # AI 编排、LLM 客户端、工具注册表、提示词（S2）
│  ├─ templates/         # Jinja2 页面（S3）
│  └─ static/            # style.css / app.js（S3）
├─ seed/                 # paper_quotes.json / process_quotes.json / rules.json / seed_demo.py
├─ scripts/              # preflight.py（预检）/ reset_demo.py（重置演示数据）
├─ tests/                # test_api_smoke.py（API 冒烟）/ test_tools.py（S2 工具单测）
├─ docs/                 # 需求设计、三张图、数据字典、测试与记录（S4）
├─ data/                 # 运行时生成 app.db（不提交）
└─ logs/                 # app.log（含 trace_id，不打印密钥）
```

---

## 2. 角色与演示账号

| 账号 | 密码 | 角色 | 权限 |
|---|---|---|---|
| `operator` | `123456` | 业务操作员（张伟） | 创建/编辑**本人**任务、关联资料、保存结果版本、提交审核、运行 AI 测算 |
| `reviewer` | `123456` | 审核员（李静） | 查看待审/已审任务，审核通过/退回；**不可**创建任务、**不可**运行 AI |

后端逐接口校验"角色 + 对象归属 + 状态"：角色不足 → **403**；对象不属于本人/不在审核范围 → **404**；状态冲突/重复提交/版本冻结 → **409**；超限 → **429**。

---

## 3. 安装

### 方式一：直接安装到当前 Python（本机已安装，推荐）

```bat
cd /d F:\deepseek workarea\出版AI系统
python -m pip install -r requirements.txt
```

`start.bat` 会直接使用当前 `python` 环境；本机依赖已装好，无需再执行。若换电脑/重装环境，执行上面这一行即可。

### 方式二：离线安装到 vendor 目录（无网络时）

```bat
python -m pip install -r requirements.txt --target vendor
set PYTHONPATH=%~dp0vendor
```

> 注意：`start.bat` 默认不设置 `PYTHONPATH`；使用 vendor 离线部署时，请先执行上面的 `set PYTHONPATH`，并在同一命令行窗口内运行 `start.bat`。

---

## 4. 配置（.env）

复制 `.env.example` 为 `.env`（Lead 已创建占位）后按需修改：

| 变量 | 默认 | 说明 |
|---|---|---|
| `APP_ENV` | `demo` | demo 开启故障注入（`tool_timeout`/`model_error`/`invalid_tool_args`），dev 关闭 |
| `DATABASE_URL` | `sqlite:///./data/app.db` | SQLite 相对路径按项目根解析 |
| `SECRET_KEY` | （必改） | Cookie 签名密钥，随机串，不提交 |
| `LLM_MODE` | `real` | `real`=真实模型（验收固定）；`mock`=确定性模拟（开发期） |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | OpenAI 兼容地址 |
| `LLM_MODEL` | `deepseek-chat` | 模型标识 |
| `LLM_API_KEY` | （填写） | 只放在本机 .env，不提交、不截图 |
| `LLM_TIMEOUT_S` | `30` | 单次模型调用超时 |
| `TOOL_TIMEOUT_MS` | `3000` | 单次工具执行超时 |
| `AI_RUN_TIMEOUT_S` | `90` | 单次 AI 运行总时长 |
| `MAX_TOOL_CALLS` | `6` | 单次运行工具总次数 |
| `MAX_LLM_ROUNDS` | `8` | 单次运行模型轮次 |
| `MAX_AI_RUNS` | `10` | 单业务任务 AI 运行次数上限 |

换模型只改 `LLM_BASE_URL / LLM_MODEL / LLM_API_KEY` 三个变量并重启；**不在演示现场切换模式**。`LLM_MODE=real` 且 Key 未配置时 `/api/system/meta` 的 `key_configured=false` 会如实反映。

---

## 5. 初始化与启动

> **三张图 HTML 演示版**：浏览器直接打开 `docs/三张图演示.html`，或系统启动后访问 <http://127.0.0.1:8000/diagrams>（离线单文件，可 Ctrl+P 导出 PDF）。


### 一键启动

```bat
start.bat
```

内部顺序：`chcp 65001` → `python scripts/preflight.py` → `python seed/seed_demo.py` → `uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1`。

> 必须 `--workers 1` 且不要 `--reload`：AI 后台任务是进程内线程。

### 手动分步

```bat
python scripts/preflight.py          :: 环境/依赖/目录/端口/模型配置预检
python seed/seed_demo.py             :: 幂等写入账号、报价、规则、示例任务
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

访问 <http://127.0.0.1:8000>（自动跳转 `/tasks`），接口文档 <http://127.0.0.1:8000/api/docs>。

### 重置演示数据

```bat
python scripts/reset_demo.py --yes
```

删除 `data/app.db` 并重新建表 + 写入 seed 数据（`.env`/`vendor`/`logs` 不受影响）。

### 自测

```bat
python -m pytest tests -q
```

---

## 6. 演示/验收操作路径（建议顺序）

1. `GET /api/system/meta` 或页面"系统信息"：确认 `llm_mode=real`、`key_configured=true`，页脚展示五项限额（工具 10 次 / 模型 8 轮 / 30s / 3000ms / 120s）。
2. 用 `operator/123456` 登录 → 任务列表 → 打开示例任务 `BT-DEMO-0001`。
3. 点击"运行 AI 测算"：后台线程执行，页面每 2s 轮询 `GET /api/ai-tasks/{id}`；结果区显示 `content_md`、结构化 `line_items/total/per_copy`、`basis_refs`（依据三跳）、不确定项/缺失项；执行记录表展示每次工具调用的参数、状态、来源 ID、耗时。
4. "采用 AI 结果"保存为 **v1**（`source=AI`）→ 人工修改再保存 **v2**（`source=MANUAL`）。
5. 提交审核：任务状态 → `PENDING_REVIEW`，版本冻结（再保存返回 409）。
6. 用 `reviewer/123456` 登录 → 审核：通过（`COMPLETED`）或退回（`REJECTED`，必须填意见）。
7. 退回后 operator 可修改保存新版本并重新提交；历史版本始终可查（≥2 版）。
8. 越权自测：operator 调 `POST /api/business-tasks/{id}/review` → 403；reviewer 调 `POST /api/business-tasks` → 403；reviewer 访问非待审任务 → 404。

### 四种典型场景（对应测试用例）

| 场景 | 构造方式 | 预期 |
|---|---|---|
| 正常测算 | 示例任务参数 | AI 成功，`basis_ok=true`，总成本/单册成本与工具记录一致 |
| 参数变化 | 改印数/开本/纸张后重跑 | 工具调用顺序与参数随模型变化，结果随之变化 |
| 数据不足 | 纸张未在报价库 | AI 输出 `missing_data`，`total=null`，不编造价格 |
| 故障与限流 | `POST /ai-tasks` 带 `fault=tool_timeout/model_error/invalid_tool_args`（仅 demo） | 对应错误码落库，执行记录保留，可重试（429 超限） |

---

## 7. 接口清单（24 个，统一 `/api` 前缀）

| # | 方法/路径 | 说明 |
|---|---|---|
| 1 | `POST /api/auth/login` | 登录（JSON/表单），签发 HttpOnly 签名 Cookie（12h） |
| 2 | `GET /api/auth/me` | 当前用户 |
| 3 | `POST /api/business-tasks` | 创建任务（operator） |
| 4 | `GET /api/business-tasks` | 任务列表（operator 本人；reviewer 待审/已审） |
| 5 | `GET /api/business-tasks/{id}` | 任务详情（对象级） |
| 6 | `PUT /api/business-tasks/{id}` | 更新（operator 本人；`expected_revision` 乐观锁） |
| 7 | `POST /api/business-tasks/{id}/materials` | 关联资料 |
| 8 | `POST /api/business-tasks/{id}/results` | 保存结果版本（AI 采用/人工保存） |
| 9 | `GET /api/business-tasks/{id}/versions` | 版本列表 |
| 10 | `POST /api/business-tasks/{id}/submit` | 提交审核（重跑依据 L2 校验） |
| 11 | `POST /api/business-tasks/{id}/review` | 审核通过/退回（reviewer） |
| 12 | `POST /api/business-tasks/{id}/ai-tasks` | 发起 AI 运行（202，operator） |
| 13 | `GET /api/ai-tasks/{id}` | AI 运行状态/计数/错误码 |
| 14 | `GET /api/ai-tasks/{id}/tool-calls` | 工具调用记录（全字段） |
| 15 | `GET /api/ai-tasks/{id}/result` | AI 结果（含依据/不确定项） |
| 16 | `POST /api/ai-tasks/{id}/retry` | 失败重试（新 attempt，受 10 次上限） |
| 17 | `GET /api/materials` | 资料列表 |
| 18 | `POST /api/materials` | 新增资料/报价（operator） |
| 19 | `GET /api/materials/{id}` | 资料详情 + 同编号版本 |
| 20 | `POST /api/rules` | 新增规则（operator） |
| 21 | `GET /api/rules` | 规则列表 |
| 22 | `GET /api/rules/{id}` | 规则详情 |
| 23 | `GET /api/system/meta` | 系统信息（模式/模型/Key 状态/限额） |
| 24 | `PATCH /api/materials/{id}` | 资料启用/停用/标题/来源（operator；停用被引用资料后提交审核触发依据失效 409） |

统一响应：成功 `{"code":0,"message":"ok","data":...,"trace_id":"..."}`；错误同结构且 `code` 为 400/401/403/404/409/429/500。时间统一 ISO8601（带时区）。

---

## 8. 成本公式（demo 唯一口径）

- 用纸量(令) = `ceil(印数 × 印张 ÷ 1000 × 1.05)`；纸张费 = 用纸量 × `price_per_ream`
- 印刷费 = `印数 ÷ 1000 × 印张 × 单色千印张价 × 色数档`（色数 >0 取 4，否则 1）
- 制版费 = 色数档 × 80 元/色
- 装订费 = `印数 ÷ 1000 × 装订单价`
- 工艺费 = `印数 ÷ 1000 × 工艺单价`（无工艺为 0）
- 总成本 = 五项之和；单册成本 = `总成本 ÷ 印数`（四舍五入 2 位）
- 每条 `line_item` 必须带 `formula` 文本，便于人工复算与依据核对。

---

## 9. 业务规则与权限

- 状态机：`PENDING → PENDING_REVIEW → COMPLETED`；`PENDING_REVIEW → REJECTED → PENDING_REVIEW`。
- 版本只增不改：`version_no` 连续递增；`PENDING_REVIEW`/`COMPLETED` 冻结（保存 → 409）。
- AI 运行只写 `ai_results`，**不自动提版**；只有"采用 AI 结果/保存人工结果"才生成业务版本。
- 提交审核时重跑依据 L2 校验：被引用资料/规则被删除或停用 → 409 `BASIS_MISMATCH`。
- 所有写接口校验角色 + 对象归属 + 状态；越权拒绝并记录日志。
- 日志：`logs/app.log`，每行含 `trace_id`，不记录 API Key / Authorization。

---

## 10. 常见问题

| 问题 | 处理 |
|---|---|
| `ModuleNotFoundError: fastapi` | 依赖未安装：`python -m pip install -r requirements.txt`；若用 vendor 离线方式，先在当前窗口 `set PYTHONPATH=%~dp0vendor` 再启动 |
| 端口 8000 被占用 | `python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --workers 1` |
| `database is locked` | 确认单 worker 运行；WAL + busy_timeout=5000 已开启；关闭其它占用 app.db 的进程 |
| AI 一直 `RUNNING` | 看 `logs/app.log` 与 `GET /api/ai-tasks/{id}` 的 `error_code`；重启后启动清理会把残留标记 `STALE_RUNNING` |
| `LLM_MODE=real` 但 AI 失败 | 检查 `.env` 的 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`；`/api/system/meta` 的 `key_configured` 应为 true |
| 依赖安装慢/无网 | 使用离线方式：`python -m pip install -r requirements.txt --target vendor`，或拷贝现成 `vendor/` 后设置 `PYTHONPATH` |
| 忘记密码/数据错乱 | `python scripts/reset_demo.py --yes` 重置演示数据 |
| 提交审核返回 409 | 正常：重复提交/状态不允许；若提示依据失效，请重新运行 AI 或更新被引用资料后重试 |

---

## 11. 安全与提交注意

- `.env`、`data/`、`logs/`、`vendor/`、`__pycache__/`、`.venv/` 已被 `.gitignore` 排除。
- 提交包中**不得**包含 `.env`、`data/*.db`、`logs/`、真实 API Key；提交前用 `grep -R "sk-"` 自检。
- 演示数据均为模拟数据（`seed/*.json` 内注明"模拟数据，仅用于演示"），不用于真实出版报价。
- 服务默认仅监听 `127.0.0.1`，不对外网开放。

---

## 12. 演示与自检（11/6 现场验收）

- **提交前一键**：`python scripts/final_submit.py`（自检 → 重新打包 → 输出提交信息 `提交信息_YYYYMMDD.txt`）
- **演示前一键自检**：`python scripts/demo_check.py`（真实模式 / Key / 限额 / 演示任务 / 交付材料，共 21 项；返回 0 表示全部通过）。
- **预置演示任务**（任务列表搜索标题即可）：
  | 任务 | 编号 / id | 状态 | 用途 |
  |---|---|---|---|
  | 已完成全流程 | `BT-DEMO-0001` / 1 | COMPLETED（v1 AI + v2 MANUAL + 审核通过） | 结果/依据/执行记录/两版本/审核记录 |
  | 演示-基线待运行 | `BT20261101-0033` / 34 | PENDING | 现场运行真实 AI（约 15s，预期 14638.00 / 4.88） |
  | 演示-数据不足 | `BT20261101-0034` / 35 | PENDING（已预跑备份） | 缺失依据、不虚构金额 |
  | 演示-失败重试 | `BT20261101-0035` / 36 | PENDING（已预跑 FAILED 备份） | 失败态 + 现场重试成功 |
- **三张图投屏**：`docs/三张图演示.html` 或 <http://127.0.0.1:8000/diagrams>。
- **演示数据快照**：`data/demo_snapshot_20261101.db`（2026-11-01 演示状态）。恢复方法：停服 → 覆盖为 `data/app.db` 并删除 `-wal/-shm` → 启动。
- **备用录屏**：`交付材料/04_测试与交付材料/备用录屏_系统演示.mp4`（104s，自动操作 + 真实 AI 运行 + 字幕；可替换为人工旁白版）。
- **材料包**：`交付材料/`（5 项提交物 + 材料索引 + 表7/表8 + 演示手册 + 验收自检报告）。

---

## 13. 方案模式（选做增强，print_cost_plan）

- 入口：任务列表右上角 **＋ 方案模式**（或 `/tasks/new-plan`）；
- 适用：纸张/装订/工艺未定、但有预算/偏好的场景；系统从**已启用资料库**枚举组合并真实校验/计算，返回 1–3 个候选方案；
- 组合保护：候选组合先按估算成本排序，最多 24 个进入完整校验/计算，最终最多返回 5 个（默认 3 个）；
- 预算内无可行方案时返回“最接近方案 + 差额”，不编造预算达标；
- 采用某方案后生成版本，提交/审核/权限与标准任务完全一致；
- 标准 `print_cost` 主流程不变；工具 `plan_candidates` 只对方案模式暴露。
- 详细说明与演示任务：`docs/17_方案模式说明.md`。

---

## 14. 界面与仪表盘（2026-11-05）

- 结果页“成本测算说明”已支持 Markdown 排版渲染（标题/列表/表格/加粗），不再直接显示 `##`、`|` 等原始标记；
- 顶部导航新增 **仪表盘** `/dashboard`：任务状态分布、AI 成功率/平均耗时、工具调用 Top、依据来源 Top、最近任务、验收基线；
- 详见 `docs/18_界面与仪表盘说明.md`。
