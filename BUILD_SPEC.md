# BUILD_SPEC：印制参数校验与成本测算 AI 系统（开发规格，v1.0）

> 供 4 路开发智能体并行使用。**权威依据**：《出版业务AI系统_要求与实施清单（2026-11-01）》第7-13章；本文件把架构落成可编码规格。
> 项目根：`F:\deepseek workarea\出版AI系统` ｜ 基准日 2026-11-01 ｜ 目标：11/5 前可交付、11/6 现场验收。
> 运行环境：Windows + 系统 Python 3.14（`F:\Python_env\Python314\python.exe`，`python` 已在 PATH）+ `vendor\`（pip --target 安装的依赖，`PYTHONPATH=vendor`）。
> 交付约束：单体后端、SQLite、Jinja2 服务端模板、OpenAI 兼容模型接口；不引入 Node/Redis/队列/微服务/OCR/多 Agent。

## 0. 角色与文件所有权（严禁越界写他人文件）

| 智能体 | 只允许写 | 依赖 |
|---|---|---|
| S1 后端 | `app/main.py, config.py, db.py, models.py, schemas.py, auth.py, app/api/{auth,tasks,materials,rules,results,system}.py, app/services/*, seed/*, scripts/*, tests/test_api_smoke.py, requirements.txt, start.bat, README.md, .gitkeep` | 本 spec |
| S2 Agent | `app/agent/{__init__,llm,tools,orchestrator,prompts}.py, app/api/ai.py, tests/test_tools.py` | 本 spec + `app/models.py`（S1 产出） |
| S3 前端 | `app/templates/*.html, app/static/style.css, app/static/app.js` | 本 spec |
| S4 文档 | `docs/01_需求与设计说明.md, docs/02_系统功能图.md, docs/03_技术调用图.md, docs/04_数据处理与工具执行图.md, docs/05_数据字典.md, docs/06_测试用例.md, docs/07_缺陷与回归记录.md, docs/08_分工与开发记录.md, docs/09_演示PPT大纲.md, docs/10_录屏脚本.md` | 本 spec |
| Lead | `BUILD_SPEC.md, .env, .env.example, .gitignore`；集成、运行、修 bug、Git、最终材料 | — |

公共约定：Python 3.14；UTF-8；4 空格；中文注释/文案；所有接口返回 JSON 包 `{code,message,data,trace_id}`（`code=0` 成功）。S1-S3 不要互相等待：按本 spec 的契约并行写，最终由 Lead 集成。写完后自检：Python 文件 `python -m py_compile`；模板无 Jinja 语法低级错误。

## 1. 目录结构

```
出版AI系统/
├─ README.md  start.bat  requirements.txt  .env / .env.example  .gitignore  BUILD_SPEC.md
├─ app/
│  ├─ __init__.py  main.py  config.py  db.py  models.py  schemas.py  auth.py
│  ├─ api/ __init__.py auth.py tasks.py materials.py rules.py results.py ai.py system.py
│  ├─ services/ __init__.py task_service.py version_service.py basis_service.py
│  ├─ agent/ __init__.py llm.py tools.py orchestrator.py prompts.py
│  ├─ templates/ base.html login.html tasks.html task_new.html task_detail.html materials.html rules.html
│  └─ static/ style.css app.js
├─ seed/ seed_demo.py  paper_quotes.json  process_quotes.json  rules.json
├─ scripts/ preflight.py  reset_demo.py
├─ tests/ test_api_smoke.py  test_tools.py
├─ docs/（S4）
└─ data/（运行时生成 app.db；不提交）
```

## 2. 环境变量（`.env`，Lead 已创建占位）

`APP_ENV=demo|dev`、`DATABASE_URL=sqlite:///./data/app.db`、`SECRET_KEY`、`LLM_MODE=real|mock`、`LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY`、`LLM_TIMEOUT_S=30`、`TOOL_TIMEOUT_MS=3000`、`AI_RUN_TIMEOUT_S=90`、`MAX_TOOL_CALLS=6`、`MAX_LLM_ROUNDS=8`、`MAX_AI_RUNS=10`。

## 3. 数据模型（SQLite，SQLAlchemy 2.x，10 张表；时间用 ISO8601 字符串）

1. `users(id PK, username UNIQUE, password_hash, salt, role_code TEXT, display_name, created_at)`；role_code ∈ `operator|reviewer`。
2. `business_tasks(id PK, task_no UNIQUE, business_type TEXT default 'print_cost', title, goal, params_json TEXT, status TEXT, revision INTEGER default 1, created_by INT, created_at, updated_at)`；status ∈ `PENDING|PENDING_REVIEW|REJECTED|COMPLETED`。
3. `materials(id PK, material_no UNIQUE, type TEXT, title, source, version_no INT default 1, content_json TEXT, enabled INT default 1, created_at)`；type ∈ `paper|process|binding|rate|book`（资料/报价类）。
4. `task_materials(id PK, task_id, material_id, link_role, created_at)`。
5. `rules(id PK, rule_code UNIQUE, name, business_type, rule_type, content_json, severity, source, enabled INT, created_at)`。
6. `ai_tasks(id PK, business_task_id, status TEXT, model_mode, model_name, input_snapshot_json, counters_json, error_code, error_message, started_at, finished_at, created_by, created_at)`；status ∈ `PENDING|RUNNING|SUCCEEDED|FAILED`。
7. `tool_call_records(id PK, ai_task_id, business_task_id, seq INT, tool_name, tool_type TEXT, args_json, result_summary, source_ids_json, status TEXT, error_code, latency_ms INT, created_at)`；tool_type ∈ `retrieval|validation|calculation`。
8. `ai_results(id PK, ai_task_id, business_task_id, content_md, structured_json, basis_refs_json, basis_ok INT, uncertain_items_json, missing_data_json, created_at)`。
9. `result_versions(id PK, business_task_id, version_no INT, source TEXT, content_md, structured_json, basis_refs_json, change_note, created_by, created_at, UNIQUE(task_id,version_no))`；source ∈ `AI|MANUAL`。
10. `review_records(id PK, business_task_id, version_id, action TEXT, opinion, reviewer_id, created_at)`；action ∈ `APPROVE|REJECT`。

## 4. 后端接口（23 个，统一 `/api` 前缀；JSON）

| # | 方法/路径 | 入参 | 出参 data | 权限 |
|---|---|---|---|---|
| 1 | POST /auth/login | username,password | token,user{id,username,role_code,role_name,display_name} | 匿名 |
| 2 | GET /auth/me | — | user | 登录 |
| 3 | POST /business-tasks | business_type,title,goal,params{material_ids?} | id,task_no,status,revision | operator |
| 4 | GET /business-tasks | status?,keyword?,page? | items[],total | 登录（operator 仅本人；reviewer 仅待审/已审） |
| 5 | GET /business-tasks/{id} | — | task+params+linked_materials+latest_ai+latest_result+versions_count | 登录（对象级） |
| 6 | PUT /business-tasks/{id} | 字段,expected_revision | task | operator 本人；PENDING/REJECTED |
| 7 | POST /business-tasks/{id}/materials | material_id,link_role | linked[] | operator 本人 |
| 8 | POST /business-tasks/{id}/results | content_md?,structured?,change_note? | version_no,version_id | operator 本人；PENDING/REJECTED |
| 9 | GET /business-tasks/{id}/versions | — | versions[]（含 content_md/structured/source/created_by/created_at） | 登录（对象级） |
| 10 | POST /business-tasks/{id}/submit | version_no,comment? | status=PENDING_REVIEW | operator 本人；PENDING/REJECTED |
| 11 | POST /business-tasks/{id}/review | version_no,action=APPROVE/REJECT,opinion | status | reviewer；PENDING_REVIEW |
| 12 | POST /business-tasks/{id}/ai-tasks | note?,fault?∈{tool_timeout,model_error,invalid_tool_args}（仅 demo） | 202 {ai_task_id,status=PENDING} | operator 本人；PENDING/REJECTED |
| 13 | GET /ai-tasks/{id} | — | status,counters,error_code,model_mode,result_id? | 登录（对象级） |
| 14 | GET /ai-tasks/{id}/tool-calls | — | records[]（全字段） | 登录（对象级） |
| 15 | GET /ai-tasks/{id}/result | — | content_md,structured,basis_refs,basis_ok,uncertain_items,missing_data | 登录（对象级） |
| 16 | POST /ai-tasks/{id}/retry | — | 202 {ai_task_id}（新 attempt；仅 FAILED） | operator 本人 |
| 17 | GET /materials | type?,keyword? | items[] | 登录 |
| 18 | POST /materials | type,title,source,content（dict） | id,material_no | operator |
| 19 | GET /materials/{id} | — | 资料详情+同 material_no 的版本 | 登录 |
| 20 | POST /rules | rule_code,name,business_type,rule_type,content,severity,source | id | operator |
| 21 | GET /rules | business_type?,keyword? | items[] | 登录 |
| 22 | GET /rules/{id} | — | 详情 | 登录 |
| 23 | GET /system/meta | — | app,version,db,llm_mode,model,key_configured,limits{...},server_time | 登录 |

错误码：400 参数/状态非法；401 未登录；403 角色不足；404 对象不属于本人/不在审核范围；409 状态冲突/重复提交/版本冻结；429 超限；500 内部错误（统一异常处理器）。所有写接口后端校验"角色+对象归属+状态"。

**状态与版本规则**：业务 PENDING→PENDING_REVIEW→COMPLETED；PENDING_REVIEW→REJECTED→PENDING（重新提交）；result_versions 只增不改，已提交/已通过版本冻结（改 →409）；AI 重跑只新增 ai_results，不自动提版；人工"采用/保存结果"才生成 MANUAL/AI 版本；至少 2 版可查。

**认证**：登录成功签发 HMAC-SHA256 签名 cookie（`uid|role|exp|sig`，exp 12h，SECRET_KEY 签名）；`HTTPException`/重定向；密码 `pbkdf2_hmac('sha256', pwd, salt, 100000)` 十六进制存储。

## 5. 工具契约（S2 实现，S1 的 API 调用其注册表；工具自己读写 DB）

统一返回信封：`{ok: bool, data: any, source: {ids: [str]}, warnings: [str], error_code?: str, error_message?: str}`。
工具超时/异常由执行器捕获 → 记录 FAILED + 错误码，返回 `ok=false`。所有工具结果必须带 `source.ids`（资料 material_no / rule_code），供 basis 校验与"依据三跳"。

| 工具 | 类型 | 入参 JSON Schema | 行为与返回 data |
|---|---|---|---|
| `search_quotes` | retrieval | `{keyword?:str(≤50), category?:str∈paper/process/binding/rate}` | 在 materials 中按 title/content LIKE 检索；返回 `{items:[{material_no,title,type,source,version_no,content}]}`；无结果 `ok=false,error_code=DATA_NOT_FOUND` |
| `get_rules` | retrieval | `{business_type?:str}` | 查 rules（默认 print_cost）；返回 `{rules:[{rule_code,name,rule_type,severity,content}]}` |
| `validate_spec` | validation | `{params:object}` | 按规则校验任务参数：format∈{16开,32开,大32开}；print_sheet>0 且 ≤50；qty≥500 且整数；paper_type 非空且能在 materials(type=paper) 找到；binding∈{胶装,骑马订,精装}；color_pages≥0；process 可空。返回 `{valid,issues:[{field,message,rule_code?}],normalized:{...}}`；规则来自 rules 表（rule_type=spec） |
| `calculate_cost` | calculation | `{params:object}` | 从 materials 读纸张/工艺/装订单价 + rate；公式见 §6；缺任何必需单价 → `ok=false,error_code=DATA_NOT_FOUND,data={missing:[...]}`；成功返回 `{line_items:[{name,formula,amount}],total,per_copy,currency:'CNY',missing:[],warnings:[]}` |

**成本公式（demo 用，写入设计说明与计算工具，保持唯一口径）**：
- 用纸量(令) = ceil(印数 × 印张 ÷ 1000 × 1.05)；纸张单价取 materials(type=paper, content.price_per_ream) → 纸张费 = 用纸量 × 单价。
- 印刷费 = 印数/1000 × 印张 × 单色千印张价(type=rate, content.price_per_1000_sheets_per_color) × 色数(color_pages>0?4:1, 简化: 单色/四色两档)。
- 制版费 = 色数档 × 80 元/色（demo 参数，来源 rate 资料）。
- 装订费 = 印数/1000 × 装订单价(type=binding, content.price_per_1000)。
- 工艺费 = 印数/1000 × 工艺单价(type=process, content.price_per_1000)（若无工艺=0）。
- 总成本 = 纸张费+印刷费+制版费+装订费+工艺费；单册成本 = total/印数（四舍五入 2 位）。
- 每项 line_item 必须带 `formula` 文本（如 `1000×3.5÷1000×1.05×85=312.38`），供依据核对与人工复算。

**种子数据（S1）**：`seed/paper_quotes.json`（≥8 种纸，字段 material_no/title/source/version_no/content{price_per_ream,grammage,format_fit[]}）、`process_quotes.json`（覆膜/UV/烫金 3 项，price_per_1000）、`rules.json`（≥10 条：印数下限、开本白名单、印张上限、装订方式与印张匹配、纸张-开本适配、必填项、色数范围等）。`seed_demo.py` 幂等写入（已存在则跳过），并创建 2 个账号：`operator/123456`（业务操作员 张伟）、`reviewer/123456`（审核员 李静）；再建 1 条示例任务（PENDING，关联 1 条纸张报价+1 条规则）供演示。

## 6. Agent 实现（S2）

- LLM 客户端：`httpx` 直连 `${LLM_BASE_URL}/chat/completions`，`Authorization: Bearer ${LLM_API_KEY}`；请求带 `tools`（4 个函数的 JSON Schema）、`tool_choice="auto"`、`temperature=0.2`；解析 `choices[0].message.tool_calls`（`id/function.name/function.arguments`）；**每个 tool_call 必须回填一条 `role=tool` 消息并带 `tool_call_id`**；`LLM_MODE=mock` 时返回确定性模拟响应（用于无 key 开发，页面/记录标注 mock）。
- `POST /ai-tasks`：写 `ai_tasks(PENDING)`+快照 → 后台线程执行（`threading.Thread(daemon=True)`；SQLite 每线程新 Session，`check_same_thread=False`）→ 202。`run_ai_task` 外层 `try/finally` 写终态；启动时把残留 RUNNING 清理为 FAILED(STALE_RUNNING)。
- 循环（≤MAX_LLM_ROUNDS 轮，工具总调用 ≤MAX_TOOL_CALLS，总时长 ≤AI_RUN_TIMEOUT_S）：
  1. 构造 messages：system prompt（角色/必须先用工具取证/数据不足不得虚构/最终输出 JSON/不得编造价格）+ user（任务目标、参数、关联资料摘要、规则摘要）。
  2. 调模型；若有 tool_calls：逐个校验参数（pydantic/JSON Schema；非法 → 记 FAILED TOOL_ARGS_INVALID，回填 tool 消息）→ 执行（超时按 TOOL_TIMEOUT_MS，记 latency/状态/来源 ID）→ 回填 tool 消息；重复同参调用（tool_name+args hash 第二次）→ 回填"已调用过"提示。
  3. 若模型给出文本 final：剥离 ```json 围栏后解析 JSON（允许前后说明文本，取第一个完整 JSON 对象）。解析失败 → 回填"只输出 JSON"重试 ≤2。
  4. **完成判定护栏**：成功调用需覆盖 ≥2 类工具（retrieval+validation+calculation 中）且每类 ≥1 次成功；`basis_refs` 非空；否则回填"必须调用工具获取依据"重试 ≤1；仍不满足 → FAILED NO_TOOL_CALL。
  5. 依据校验：`basis_refs[]` 的 id 必须属于 本次工具 source.ids ∪ 任务关联资料 material_no ∪ rule_code ∪ 参数中业务数据；L1 不命中 → `basis_ok=false` + uncertain_items 追记（不 FAILED）；L2 id 在库不存在 → 软标记"依据已变更"；**数据不足仍输出 total → 重试 1 次，仍犯 → FAILED DATA_FABRICATED**。
  6. 成功：写 `ai_results`（content_md/structured/basis_refs/basis_ok/uncertain/missing）→ ai_tasks SUCCEEDED。失败：FAILED + error_code/error_message。
- 故障注入（仅 `APP_ENV=demo`，请求级参数）：`tool_timeout`（首次工具强制 sleep > TOOL_TIMEOUT_MS → TOOL_TIMEOUT）、`model_error`（直接 FAILED MODEL_ERROR）、`invalid_tool_args`（首次工具强制非法参数 → TOOL_ARGS_INVALID）；不依赖改环境变量、不重启进程。
- 最终 JSON 期望：`{content_md:str, structured:{line_items:[...],total:num|null,per_copy:num|null,missing:[...],uncertain:[...]}, basis_refs:[{type:'material'|'rule'|'calc', id:str, label:str}], uncertain_items:[str], missing_data:[str]}`；`content_md` 用中文 Markdown 写成本测算说明与依据。
- 记录：`tool_call_records` 每调用一条（seq/tool_name/tool_type/args_json/result_summary≤200字/source_ids_json/status/error_code/latency_ms）；`ai_tasks.counters_json` 记 rounds/tool_calls/distinct_types/model_calls；页脚展示限额（6/8/30s/3000ms/90s）。
- `POST /ai-tasks/{id}/retry`：仅 FAILED 可重试；新建 ai_task（同 business_task_id）并启动线程；受 MAX_AI_RUNS=10 限制（超 →429）。
- 真实/模拟：`/api/system/meta` 返回 `llm_mode` 与 `key_configured`；页面顶部/结果页标注 model_mode；**验收固定 real**。

## 7. 前端（S3：Jinja2 + 原生 JS，无构建）

- `base.html`：顶部导航（任务列表 / 资料库 / 规则库 / 系统信息 / 当前用户+角色 / 退出）+ 内容块 + 页脚（限额展示：工具 6 次、模型 8 轮、模型 30s、工具 3000ms、总 90s；LLM_MODE）。
- `login.html`：用户名/密码表单（POST /login，服务端表单）；演示账号提示（operator/123456、reviewer/123456）。
- `tasks.html`：任务表格（task_no/title/status/创建人/更新时间/版本数/AI 状态）+ "新建任务"按钮；按状态筛选（服务端 query）。
- `task_new.html`：表单字段：title*、goal*、book_title、format（16开/32开/大32开）、print_sheet*、qty*、paper_type*、binding*（胶装/骑马订/精装）、color_pages（0-4）、process（无/覆膜/UV/烫金）、note；提交 POST /tasks/create；JS 做必填与数值范围前端提示（后端仍校验）。
- `task_detail.html`：①任务信息与参数（可编辑）；②关联资料；③AI 运行区：按钮"运行 AI 测算"（POST /tasks/{id}/ai-run 前端聚合 → 调 API 12）、状态徽标（PENDING/RUNNING/SUCCEEDED/FAILED）、轮询 GET /api/ai-tasks/{id}（2s，90s 超时提示）、失败显示错误码+重试按钮；④结果区：content_md 渲染（Markdown 简版：换行/标题/列表/表格用 CSS pre-wrap 即可）、结构化明细表（line_items/formula/amount）、依据列表（basis_refs 可点击 → GET /materials/{id} 或 /rules/{id} 详情页/弹层）、不确定项/缺失项；⑤执行记录表（tool_call_records 全字段）；⑥版本区：版本列表（v1/v2…来源/操作人/时间/意见）+ "保存人工版本"表单；⑦审核区（reviewer 才显示：通过/退回 + 意见）；⑧操作：提交审核、退回后重新提交、历史查询。
- `materials.html`：列表 + 新增表单（type/title/source/content JSON 文本域）+ 详情查看；`rules.html` 类似。
- `style.css`：简洁专业（深蓝主色、卡片、表格斑马纹、状态徽标颜色：PENDING 灰/RUNNING 蓝/SUCCEEDED 绿/FAILED 红）。
- `app.js`：AI 轮询与表单增强；不使用前端框架。
- 所有页面服务端渲染（FastAPI Jinja2Templates），未登录重定向 /login；operator/reviewer 菜单与操作按角色显示（后端仍校验）。

**HTML 路由（S1 提供）**：GET /login、POST /login、POST /logout、GET /（302 /tasks）、GET /tasks、GET /tasks/new、POST /tasks/create、GET /tasks/{id}、POST /tasks/{id}/ai-run（前端按钮 → 调 API 12 的薄封装，可 302 回详情页）、POST /tasks/{id}/result/save、POST /tasks/{id}/submit、POST /tasks/{id}/review、GET /materials、POST /materials/create、GET /rules、POST /rules/create、GET /system。模板上下文由 S1 按上述字段传；S3 只消费这些字段名。

## 8. 运行与验收

- 启动：`start.bat`（chcp 65001；`set PYTHONPATH=%~dp0vendor`；`python scripts/preflight.py`；`python seed/seed_demo.py`；`python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1`）。开发期用 `python -m uvicorn ... --reload` 可选。
- `scripts/preflight.py`：检查 Python 版本、vendor/依赖可 import、data 目录可写、端口、`.env` 是否存在、`LLM_MODE` 与 key 是否配置（real 且 key 空 → 警告）。
- `README.md`：项目说明、角色与演示账号、安装（requirements/vendor 两种）、初始化、启动、模型切换（LLM_* 环境变量）、目录结构、验收操作路径、常见问题。
- 自测：`python -m pytest tests -q`（工具单元 + API 冒烟）。

## 9. 质量与交付要求

- 所有功能可运行、无 500；异常路径返回明确错误码；日志写 `logs/app.log`（含 task_id/trace_id，不打印 key）。
- 密钥只在 `.env`；`.gitignore` 排除 `.env`、`data/`、`logs/`、`vendor/`、`__pycache__/`、`.venv/`。
- 不实现：OCR、PDF 解析、复杂排版、微服务、多 Agent、消息队列、前端构建、Docker。
- 证据与材料（S4）：需求与设计说明 3-5 页正文 + 3 张图（mermaid 源+说明）、数据字典、12 条测试用例（覆盖 9 类+业务专项）、缺陷回归模板、分工表（成员A-E 占位）、AI 辅助开发说明、演示 PPT 大纲、录屏脚本。
- 完成后各智能体自检并汇报：文件清单、关键实现点、已知问题。
