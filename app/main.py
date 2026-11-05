"""FastAPI 应用入口：中间件 / 全局异常 / 日志 / 页面路由 / API 路由挂载。

页面路由（S3 模板按这些上下文渲染）：
    GET  /                      → 302 /tasks
    GET  /login                 → login.html（error=None）
    POST /login                 → 表单登录，成功下发 Cookie 后 302 /tasks
    POST /logout                → 清 Cookie 302 /login
    GET  /tasks                 → tasks.html
    GET  /tasks/new             → task_new.html
    POST /tasks/create          → 创建任务 → 302 /tasks/{id}
    GET  /tasks/{id}            → task_detail.html
    POST /tasks/{id}/ai-run     → 薄封装调用 API #12（S2）→ 302 回详情
    POST /tasks/{id}/result/save→ 保存人工版本 → 302
    POST /tasks/{id}/submit     → 提交审核 → 302
    POST /tasks/{id}/review     → 审核（reviewer）→ 302
    GET  /materials             → materials.html
    POST /materials/create      → 新增资料 → 302
    GET  /rules                 → rules.html
    POST /rules/create          → 新增规则 → 302
    GET  /system                → system.html（缺模板时用内置页面兜底）
"""

from __future__ import annotations

import html as html_lib
import inspect
import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import APP_NAME, APP_VERSION
from app.api import ai, auth as auth_api, materials as materials_api, results as results_api
from app.api import rules as rules_api, system as system_api, tasks as tasks_api
from app.auth import (
    authenticate,
    clear_auth_cookie,
    create_token,
    get_current_user,
    set_auth_cookie,
)
from app.config import BASE_DIR, settings, trace_id_ctx
from app.db import get_db, init_db
from app.models import (
    AI_SUCCEEDED,
    MATERIAL_TYPE_LABELS,
    ROLE_OPERATOR,
    ROLE_REVIEWER,
    TASK_EDITABLE_STATUSES,
    TASK_PENDING_REVIEW,
    TASK_STATUS_LABELS,
    AiResult,
    AiTask,
    BusinessTask,
    Material,
    ResultVersion,
    ReviewRecord,
    Rule,
    ToolCallRecord,
)
from app.schemas import MaterialCreateRequest, RuleCreateRequest
from app.services import basis_service, task_service, version_service

# ============================ 日志 ============================
# Windows 控制台默认编码可能不是 UTF-8，避免中文日志/打印报 UnicodeEncodeError
try:  # pragma: no cover - 平台相关
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "app.log"


class TraceIdFilter(logging.Filter):
    """把当前请求 trace_id 注入日志记录（无请求时为 -）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = trace_id_ctx.get() or "-"
        return True


def setup_logging() -> logging.Logger:
    """应用日志：控制台 + logs/app.log，含 trace_id；绝不打印密钥。"""
    app_logger = logging.getLogger("app")
    if app_logger.handlers:
        return app_logger
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    app_logger.setLevel(logging.INFO)
    app_logger.propagate = False
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(trace_id)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.addFilter(TraceIdFilter())
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(TraceIdFilter())
    app_logger.addHandler(file_handler)
    app_logger.addHandler(console_handler)
    return app_logger


setup_logging()
logger = logging.getLogger(__name__)

# ============================ 目录 ============================
APP_DIR = BASE_DIR / "app"
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["app_version"] = APP_VERSION
templates.env.globals["app_name"] = APP_NAME
# 兼容不同 Starlette 版本的 TemplateResponse 签名
_TEMPLATE_NEW_SIGNATURE = "request" in inspect.signature(Jinja2Templates.TemplateResponse).parameters


# ============================ 生命周期 ============================
@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动：建表 → 清理残留 RUNNING AI 任务（S2 orchestrator，未就绪时跳过）。"""
    init_db()
    logger.info(
        "应用启动 app_env=%s llm_mode=%s model=%s db=%s",
        settings.app_env,
        settings.llm_mode,
        settings.llm_model,
        settings.resolved_database_url(),
    )
    try:
        from app.agent.orchestrator import cleanup_stale_running

        cleanup_stale_running()
        logger.info("已清理残留 RUNNING 状态的 AI 任务")
    except Exception as exc:  # pragma: no cover - S2 未就绪时不应影响启动
        logger.warning("跳过残留 AI 任务清理：%s", exc)
    yield
    logger.info("应用停止")


app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description="印制参数校验与成本测算：业务任务、资料/规则、AI 工具调用记录、结果版本与审核。",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)


# ============================ 中间件：trace_id + 访问日志 ============================
class TraceMiddleware:
    """纯 ASGI 中间件：注入 trace_id、回写 X-Trace-Id、记录访问日志。

    用纯 ASGI 而非 BaseHTTPMiddleware，兼容不同 Starlette 版本且不缓冲流式响应。
    """

    def __init__(self, app):  # noqa: ANN001
        self.app = app

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        trace_id = (headers.get(b"x-trace-id") or b"").decode("latin-1") or uuid.uuid4().hex[:16]
        scope["trace_id"] = trace_id
        state = scope.get("state")
        if isinstance(state, dict):
            state["trace_id"] = trace_id
        token = trace_id_ctx.set(trace_id)
        started = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message):  # noqa: ANN001
            if message.get("type") == "http.response.start":
                status_holder["status"] = message.get("status", 500)
                response_headers = list(message.get("headers") or [])
                response_headers.append((b"x-trace-id", trace_id.encode("latin-1")))
                message["headers"] = response_headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            path = scope.get("path", "")
            if not path.startswith("/static"):
                logger.info(
                    "%s %s -> %s %.1fms",
                    scope.get("method", ""),
                    path,
                    status_holder["status"],
                    elapsed_ms,
                )
            trace_id_ctx.reset(token)


app.add_middleware(TraceMiddleware)


# ============================ 统一异常处理 ============================
DEFAULT_MESSAGES = {
    400: "参数或状态非法",
    401: "未登录或登录已过期",
    403: "当前角色无此操作权限",
    404: "对象不存在或不在你的权限范围内",
    409: "状态冲突",
    429: "请求超出限额",
    500: "服务器内部错误",
}


def _trace_of(request: Request) -> str:
    return request.scope.get("trace_id") or trace_id_ctx.get() or "-"


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    detail = exc.detail
    status_code = exc.status_code
    code = status_code
    message = DEFAULT_MESSAGES.get(status_code, str(detail) if isinstance(detail, str) else "请求失败")
    data = None
    if isinstance(detail, dict):
        code = int(detail.get("code") or status_code)
        message = str(detail.get("message") or message)
        data = detail.get("data")
    elif isinstance(detail, str) and detail:
        message = detail
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "data": data, "trace_id": _trace_of(request)},
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    fields = []
    for error in exc.errors():
        location = ".".join(
            str(part) for part in error.get("loc", ()) if part not in ("body", "query", "path")
        )
        fields.append({"field": location or "body", "message": error.get("msg", "参数非法")})
    preview = "；".join(f"{item['field']}: {item['message']}" for item in fields[:5])
    return JSONResponse(
        status_code=400,
        content={
            "code": 400,
            "message": f"参数校验失败：{preview}" if preview else "参数校验失败",
            "data": {"fields": fields},
            "trace_id": _trace_of(request),
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("未处理异常 %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "code": 500,
            "message": "服务器内部错误，请将 trace_id 提供给管理员查看日志",
            "data": None,
            "trace_id": _trace_of(request),
        },
    )


# ============================ 静态资源 / 路由挂载 ============================
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
else:  # pragma: no cover - S3 尚未创建静态目录时
    logger.warning("静态目录不存在，跳过 /static 挂载：%s", STATIC_DIR)

app.include_router(auth_api.router)
app.include_router(tasks_api.router)
app.include_router(results_api.router)
app.include_router(materials_api.router)
app.include_router(rules_api.router)
app.include_router(system_api.router)


def _mount_agent_router() -> None:
    """挂载 S2 的 AI 接口路由；自动兼容其是否自带 /api 前缀。"""
    router = getattr(ai, "router", None) or getattr(ai, "api_router", None)
    if router is None:
        logger.warning("app.api.ai 未暴露 router/api_router，AI 接口未挂载")
        return
    paths = [str(getattr(route, "path", "")) for route in getattr(router, "routes", [])]
    needs_prefix = bool(paths) and not all(path.startswith("/api") for path in paths)
    app.include_router(router, prefix="/api" if needs_prefix else "")
    logger.info("已挂载 S2 AI 接口路由（前缀：%s）", "/api" if needs_prefix else "自带")


_mount_agent_router()


# ============================ 页面公共工具 ============================
def _page_context(user: Optional[dict], active_nav: Optional[str], **extra) -> dict:
    context = {
        "user": user,
        "llm_mode": settings.llm_mode,
        "limits": settings.limits,
        "active_nav": active_nav,
        "app_version": APP_VERSION,
        "app_env": settings.app_env,
    }
    context.update(extra)
    return context


def _render(request: Request, template_name: str, context: dict, status_code: int = 200):
    context = dict(context)
    context.setdefault("request", request)
    # Starlette 新版 TemplateResponse 签名为 (request, name, context...)，旧版为 (name, context...)
    if _TEMPLATE_NEW_SIGNATURE:
        return templates.TemplateResponse(
            request=request,
            name=template_name,
            context=context,
            status_code=status_code,
        )
    return templates.TemplateResponse(template_name, context, status_code=status_code)


def _message_page(title: str, message: str, status_code: int = 400, back_url: str = "/tasks"):
    """模板缺失/权限拒绝时的内置兜底页面（不依赖 S3 模板）。"""
    safe_title = html_lib.escape(str(title))
    safe_message = html_lib.escape(str(message))
    safe_back = html_lib.escape(str(back_url), quote=True)
    return HTMLResponse(
        status_code=status_code,
        content=f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>{safe_title}</title>
<style>body{{font-family:"Microsoft YaHei",Arial,sans-serif;background:#f5f7fb;color:#1f2d3d;margin:0}}
.box{{max-width:720px;margin:80px auto;background:#fff;border-radius:8px;padding:32px 40px;
box-shadow:0 2px 12px rgba(20,40,80,.08)}}h1{{font-size:20px;color:#123a6b}}
a{{color:#1b5fa8}}</style></head><body><div class="box">
<h1>{safe_title}</h1><p>{safe_message}</p><p><a href="{safe_back}">返回任务列表</a> ｜ <a href="/login">重新登录</a></p>
</div></body></html>""",
    )


def _redirect(url: str, error: Optional[str] = None, **params) -> RedirectResponse:
    query_parts = []
    if error:
        query_parts.append(f"error={quote(str(error))}")
    for key, value in params.items():
        if value is not None and value != "":
            query_parts.append(f"{quote(str(key))}={quote(str(value))}")
    if query_parts:
        url = f"{url}{'&' if '?' in url else '?'}{'&'.join(query_parts)}"
    return RedirectResponse(url=url, status_code=302)


def _login_redirect() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=302)


def _to_int(value, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _to_float(value, default: float = 0.0) -> float:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
        return default
    return number


def _number_or_int(value: float):
    """整数值返回 int（如 12.0 → 12），否则保留 float（如 3.5）。"""
    return int(value) if float(value).is_integer() else value


def _form_value(form, key: str, default: str = "") -> str:
    value = form.get(key) if hasattr(form, "get") else None
    if value is None:
        return default
    return str(value).strip()


def _permissions(user: Optional[dict], task) -> dict:
    role = (user or {}).get("role_code")
    return {
        "can_edit": role == ROLE_OPERATOR
        and int(task.created_by or 0) == int((user or {}).get("id") or 0)
        and task.status in TASK_EDITABLE_STATUSES,
        "can_review": role == ROLE_REVIEWER and task.status == TASK_PENDING_REVIEW,
    }


# ============================ 登录 / 退出 ============================
@app.get("/login", include_in_schema=False)
def login_page(request: Request, error: Optional[str] = Query(default=None)):
    return _render(
        request,
        "login.html",
        {
            "error": error,
            "user": get_current_user(request),
            "llm_mode": settings.llm_mode,
            "limits": settings.limits,
            "active_nav": None,
            "app_version": APP_VERSION,
        },
    )


@app.post("/login", include_in_schema=False)
async def login_submit(request: Request, db: Session = Depends(get_db)):
    try:
        form = await request.form()
    except Exception:
        form = {}
    username = _form_value(form, "username")
    password = _form_value(form, "password")
    user = authenticate(db, username, password) if username and password else None
    if user is None:
        logger.info("页面登录失败 username=%s", username)
        return _render(
            request,
            "login.html",
            _page_context(None, None, error="用户名或密码错误，请重试"),
            status_code=401,
        )
    token = create_token(user.id, user.role_code)
    response = RedirectResponse(url="/tasks", status_code=302)
    set_auth_cookie(response, token)
    logger.info("页面登录成功 user_id=%s username=%s role=%s", user.id, user.username, user.role_code)
    return response


@app.post("/logout", include_in_schema=False)
def logout(request: Request):
    response = RedirectResponse(url="/login", status_code=302)
    clear_auth_cookie(response)
    return response


@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse(url="/tasks", status_code=302)


# ============================ 任务列表 ============================
def _dashboard_context(user: Optional[dict], db: Session, days: int = 7) -> dict:
    """运行仪表盘：任务/AI/工具/版本/审核统计（只读）。"""
    from collections import Counter

    from datetime import datetime as _dt, timedelta as _td

    all_tasks = db.query(BusinessTask).all()
    all_ai = db.query(AiTask).all()
    all_tool = db.query(ToolCallRecord).all()
    all_versions = db.query(ResultVersion).all()
    all_reviews = db.query(ReviewRecord).all()

    days = int(days) if days is not None else 7
    if days <= 0:
        range_label = "全部时间"
        trend_days = 14
        cutoff_date = None
    else:
        range_label = f"近 {days} 天"
        trend_days = days
        cutoff_date = (_dt.now() - _td(days=days)).date().isoformat()

    def _in_range(row) -> bool:
        if cutoff_date is None:
            return True
        return str(getattr(row, "created_at", "") or "")[:10] >= cutoff_date

    tasks = [row for row in all_tasks if _in_range(row)]
    ai_tasks = [row for row in all_ai if _in_range(row)]
    tool_calls = [row for row in all_tool if _in_range(row)]
    versions = [row for row in all_versions if _in_range(row)]
    reviews = [row for row in all_reviews if _in_range(row)]

    task_status_order = ["PENDING", "PENDING_REVIEW", "REJECTED", "COMPLETED"]
    task_counts = Counter(str(getattr(t, "status", "") or "") for t in tasks)
    task_total = len(tasks) or 1
    task_status_items = []
    for key in task_status_order:
        count = int(task_counts.get(key, 0))
        label = TASK_STATUS_LABELS.get(key, key)
        task_status_items.append(
            {"key": key, "label": label, "count": count, "pct": round(count * 100.0 / task_total, 1)}
        )

    ai_counts = Counter(str(getattr(a, "status", "") or "") for a in ai_tasks)
    ai_total = len(ai_tasks) or 1
    ai_status_items = []
    for key, label in (("SUCCEEDED", "完成"), ("FAILED", "失败"), ("RUNNING", "运行中"), ("PENDING", "待执行")):
        count = int(ai_counts.get(key, 0))
        ai_status_items.append({"key": key, "label": label, "count": count, "pct": round(count * 100.0 / ai_total, 1)})
    success_rate = round(ai_counts.get("SUCCEEDED", 0) * 100.0 / ai_total, 1) if ai_tasks else 0.0

    elapsed_values = []
    for ai in ai_tasks:
        counters = basis_service.parse_json(getattr(ai, "counters_json", None), {})
        if isinstance(counters, dict) and isinstance(counters.get("elapsed_ms"), (int, float)):
            elapsed_values.append(float(counters["elapsed_ms"]))
    avg_elapsed = round(sum(elapsed_values) / len(elapsed_values) / 1000.0, 1) if elapsed_values else 0.0

    tool_counts = Counter(str(getattr(c, "tool_name", "") or "") for c in tool_calls)
    tool_items = [
        {"name": name, "count": count}
        for name, count in tool_counts.most_common(10)
    ]

    source_counter: Counter = Counter()
    for call in tool_calls:
        ids = basis_service.parse_json(getattr(call, "source_ids_json", None), [])
        if isinstance(ids, list):
            for sid in ids:
                sid = str(sid or "").strip()
                if sid:
                    source_counter[sid] += 1
    source_items = [{"id": sid, "count": count} for sid, count in source_counter.most_common(8)]

    plan_tasks = [t for t in all_tasks if str(getattr(t, "business_type", "")) == "print_cost_plan"]
    recent = sorted(tasks, key=lambda t: str(getattr(t, "created_at", "") or ""), reverse=True)[:8]
    recent_items = [
        {
            "id": t.id,
            "task_no": t.task_no,
            "title": t.title,
            "status": t.status,
            "status_label": TASK_STATUS_LABELS.get(t.status, t.status),
            "business_type": t.business_type,
            "created_at": t.created_at,
        }
        for t in recent
    ]

    # 失败原因分布
    failure_counts = Counter(
        str(getattr(ai, "error_code", "") or "UNKNOWN")
        for ai in ai_tasks
        if str(getattr(ai, "status", "")) == "FAILED"
    )
    failure_items = [{"code": code, "count": count} for code, count in failure_counts.most_common(8)]

    # 每日趋势（任务创建 / AI 运行）
    today = _dt.now().date()
    dates = [today - _td(days=i) for i in range(trend_days - 1, -1, -1)]
    task_day_counts = Counter(str(getattr(t, "created_at", "") or "")[:10] for t in all_tasks)
    ai_day_counts = Counter(str(getattr(a, "created_at", "") or "")[:10] for a in all_ai)
    trend_items = []
    max_count = 1
    for d in dates:
        key = d.isoformat()
        tcount = int(task_day_counts.get(key, 0))
        acount = int(ai_day_counts.get(key, 0))
        max_count = max(max_count, tcount, acount)
        trend_items.append({"date": key[5:], "tasks": tcount, "ai": acount})
    for item in trend_items:
        item["task_pct"] = round(item["tasks"] * 100.0 / max_count, 1)
        item["ai_pct"] = round(item["ai"] * 100.0 / max_count, 1)

    baseline = db.query(AiResult).filter(AiResult.business_task_id == 1).order_by(AiResult.id.desc()).first()
    baseline_data = {}
    if baseline is not None:
        structured = basis_service.parse_json(baseline.structured_json, {})
        baseline_data = {
            "total": structured.get("total"),
            "per_copy": structured.get("per_copy"),
            "created_at": baseline.created_at,
        }

    return _page_context(
        user,
        "dashboard",
        dashboard={
            "task_total": len(tasks),
            "ai_total": len(ai_tasks),
            "tool_total": len(tool_calls),
            "version_total": len(versions),
            "review_total": len(reviews),
            "task_status_items": task_status_items,
            "ai_status_items": ai_status_items,
            "success_rate": success_rate,
            "avg_elapsed": avg_elapsed,
            "tool_items": tool_items,
            "source_items": source_items,
            "plan_total": len(plan_tasks),
            "plan_pending": sum(1 for t in plan_tasks if t.status == "PENDING"),
            "recent_items": recent_items,
            "baseline": baseline_data,
            "range_label": range_label,
            "days": days,
            "trend_items": trend_items,
            "failure_items": failure_items,
        },
    )


BENCHMARKS = [
    {"name": "教辅重印基线", "params": {"format": "16开", "print_sheet": 12, "qty": 3000, "paper_type": "胶版纸80g", "binding": "胶装", "color_pages": 4, "process": "覆膜"}, "expected_total": 14638.00, "expected_per": 4.88},
    {"name": "输入变化（14印张/5000册）", "params": {"format": "16开", "print_sheet": 14, "qty": 5000, "paper_type": "胶版纸80g", "binding": "胶装", "color_pages": 4, "process": "覆膜"}, "expected_total": 27890.00, "expected_per": 5.58},
    {"name": "线圈培训手册（单色/无工艺）", "params": {"format": "32开", "print_sheet": 6, "qty": 2000, "paper_type": "双胶纸80g", "binding": "线圈装", "color_pages": 0, "process": ""}, "expected_total": 4384.00, "expected_per": 2.19},
    {"name": "铜版纸精装画册", "params": {"format": "16开", "print_sheet": 8, "qty": 2000, "paper_type": "铜版纸157g", "binding": "精装", "color_pages": 4, "process": "覆膜"}, "expected_total": 11888.00, "expected_per": 5.94},
]


@app.get("/evaluation", include_in_schema=False)
def evaluation_page(request: Request, db: Session = Depends(get_db)):
    """确定性效果评测：固定 benchmark 参数 → calculate_cost/validate_spec → 与预期对比。"""
    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    from app.agent.tools import calculate_cost, validate_spec

    cases = []
    passed = 0
    for item in BENCHMARKS:
        params = dict(item["params"])
        calc = calculate_cost(params)
        val = validate_spec(params)
        data = calc.get("data") or {}
        actual_total = data.get("total")
        actual_per = data.get("per_copy")
        valid = bool((val.get("data") or {}).get("valid"))
        ok = (
            valid
            and actual_total is not None
            and abs(float(actual_total) - float(item["expected_total"])) < 0.011
            and actual_per is not None
            and abs(float(actual_per) - float(item["expected_per"])) < 0.011
        )
        if ok:
            passed += 1
        cases.append(
            {
                "name": item["name"],
                "params": params,
                "expected_total": item["expected_total"],
                "expected_per": item["expected_per"],
                "actual_total": actual_total,
                "actual_per": actual_per,
                "valid": valid,
                "ok": ok,
                "source_ids": (calc.get("source") or {}).get("ids") or [],
                "error_code": calc.get("error_code"),
            }
        )
    context = _page_context(
        user,
        "evaluation",
        cases=cases,
        passed=passed,
        total=len(cases),
    )
    return _render(request, "evaluation.html", context)


@app.get("/dashboard", include_in_schema=False)
def dashboard_page(request: Request, days: int = Query(default=7, ge=0, le=365), db: Session = Depends(get_db)):
    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    return _render(request, "dashboard.html", _dashboard_context(user, db, days=days))


@app.get("/tasks", include_in_schema=False)
def tasks_page(
    request: Request,
    status: Optional[str] = Query(default=None),
    status_filter: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    sort: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    selected_status = status_filter or status or "ALL"
    items, total, page, _page_size = task_service.list_tasks(
        db, user, status=selected_status, keyword=keyword, page=page, page_size=20, sort=sort
    )
    context = _page_context(
        user,
        "tasks",
        tasks=items,
        status_filter=selected_status,
        status_options=[{"value": key, "label": value} for key, value in TASK_STATUS_LABELS.items()],
        total=total,
        page=page,
        keyword=keyword or "",
        sort=(sort or "updated_desc"),
    )
    return _render(request, "tasks.html", context)


# ============================ 新建任务 ============================
def _form_options(db: Session) -> dict:
    """任务表单下拉选项统一从资料库读取（新增资料后无需改模板）。"""
    papers = (
        db.query(Material)
        .filter(Material.type == "paper", Material.enabled == 1)
        .order_by(Material.material_no.asc())
        .all()
    )
    processes = (
        db.query(Material)
        .filter(Material.type == "process", Material.enabled == 1)
        .order_by(Material.material_no.asc())
        .all()
    )
    bindings = (
        db.query(Material)
        .filter(Material.type == "binding", Material.enabled == 1)
        .order_by(Material.material_no.asc())
        .all()
    )
    return {
        "paper_types": [paper.title for paper in papers],
        "process_options": [process.title for process in processes],
        "binding_options": [binding.title for binding in bindings],
        "formats": ["16开", "32开", "大32开"],
    }


def _task_new_context(user: Optional[dict], db: Session, **extra) -> dict:
    papers = (
        db.query(Material)
        .filter(Material.type == "paper", Material.enabled == 1)
        .order_by(Material.material_no.asc())
        .all()
    )
    processes = (
        db.query(Material)
        .filter(Material.type == "process", Material.enabled == 1)
        .order_by(Material.material_no.asc())
        .all()
    )
    bindings = (
        db.query(Material)
        .filter(Material.type == "binding", Material.enabled == 1)
        .order_by(Material.material_no.asc())
        .all()
    )
    context = _page_context(
        user,
        "tasks",
        paper_types=[paper.title for paper in papers],
        process_options=[process.title for process in processes],
        binding_options=[binding.title for binding in bindings],
        formats=["16开", "32开", "大32开"],
    )
    context.update(extra)
    return context


@app.get("/tasks/new", include_in_schema=False)
def task_new_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    if user.get("role_code") != ROLE_OPERATOR:
        return _message_page("无权限", "审核员不能创建业务任务，请使用业务操作员账号。", 403)
    return _render(request, "task_new.html", _task_new_context(user, db))


@app.get("/tasks/new-plan", include_in_schema=False)
def task_new_plan_page(request: Request, db: Session = Depends(get_db)):
    """方案模式：允许纸张/装订/工艺留白 + 预算，由 plan_candidates 枚举方案。"""
    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    if user.get("role_code") != ROLE_OPERATOR:
        return _message_page("无权限", "审核员不能创建业务任务，请使用业务操作员账号。", 403)
    return _render(request, "task_new_plan.html", _task_new_context(user, db, plan_mode=True))


@app.post("/tasks/create-plan", include_in_schema=False)
async def task_create_plan_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    if user.get("role_code") != ROLE_OPERATOR:
        return _message_page("无权限", "审核员不能创建业务任务，请使用业务操作员账号。", 403)
    try:
        form = await request.form()
    except Exception:
        form = {}

    title = _form_value(form, "title")
    goal = _form_value(form, "goal")
    book_title = _form_value(form, "book_title")
    book_format = _form_value(form, "format", "16开") or "16开"
    print_sheet = _to_float(form.get("print_sheet"), 0)
    qty = _to_int(form.get("qty"), 0)
    color_pages = _to_int(form.get("color_pages"), 4)
    budget_total = _to_float(form.get("budget_total"), 0)
    paper_type = _form_value(form, "paper_type")
    binding = _form_value(form, "binding")
    process = _form_value(form, "process")
    note = _form_value(form, "note")

    errors: list[str] = []
    if not title:
        errors.append("任务标题不能为空")
    if not goal:
        errors.append("测算目标不能为空")
    if book_format not in ("16开", "32开", "大32开"):
        errors.append("开本无效")
    if print_sheet <= 0 or print_sheet > 50:
        errors.append("印张必须大于 0 且不超过 50")
    if qty < 500:
        errors.append("印数必须不低于 500 册")
    if color_pages < 0 or color_pages > 4:
        errors.append("色数档位必须在 0-4 之间")
    if budget_total < 0:
        errors.append("预算不能为负数")
    if errors:
        context = _task_new_context(user, db, plan_mode=True, error="；".join(errors), form_data=dict(form))
        return _render(request, "task_new_plan.html", context, status_code=400)

    params: dict = {
        "book_title": book_title,
        "format": book_format,
        "print_sheet": print_sheet,
        "qty": qty,
        "color_pages": color_pages,
    }
    if paper_type:
        params["paper_type"] = paper_type
    if binding:
        params["binding"] = binding
    if process and process not in ("", "无"):
        params["process"] = process
    if note:
        params["note"] = note
    if budget_total > 0:
        params["budget_total"] = budget_total

    try:
        task = task_service.create_task(
            db,
            user,
            title=title,
            goal=goal,
            business_type="print_cost_plan",
            params=params,
        )
    except StarletteHTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        context = _task_new_context(
            user, db, plan_mode=True,
            error=str(detail.get("message") or "创建任务失败"),
            form_data=dict(form),
        )
        return _render(request, "task_new_plan.html", context, status_code=exc.status_code)
    logger.info("创建方案任务 task_id=%s user=%s", task.id, user.get("username"))
    return _redirect(f"/tasks/{task.id}")


@app.post("/tasks/create", include_in_schema=False)
async def task_create_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    if user.get("role_code") != ROLE_OPERATOR:
        return _message_page("无权限", "审核员不能创建业务任务，请使用业务操作员账号。", 403)

    try:
        form = await request.form()
    except Exception:
        form = {}

    title = _form_value(form, "title")
    goal = _form_value(form, "goal")
    book_title = _form_value(form, "book_title")
    book_format = _form_value(form, "format", "16开") or "16开"
    paper_type = _form_value(form, "paper_type")
    binding = _form_value(form, "binding")
    process = _form_value(form, "process")
    note = _form_value(form, "note")
    print_sheet = _number_or_int(_to_float(form.get("print_sheet") if hasattr(form, "get") else 0, 0.0))
    qty = _to_int(form.get("qty") if hasattr(form, "get") else 0, 0)
    color_pages = _to_int(form.get("color_pages") if hasattr(form, "get") else 0, 0)
    if process in ("", "无"):
        process = ""

    form_data = {
        "title": title,
        "goal": goal,
        "book_title": book_title,
        "format": book_format,
        "print_sheet": print_sheet,
        "qty": qty,
        "paper_type": paper_type,
        "binding": binding,
        "color_pages": color_pages,
        "process": process,
        "note": note,
    }

    def _error(message: str):
        return _render(
            request,
            "task_new.html",
            _task_new_context(user, db, error=message, form_data=form_data),
            status_code=400,
        )

    if not title:
        return _error("任务标题不能为空")
    if not goal:
        return _error("任务目标不能为空")
    if book_format not in ("16开", "32开", "大32开"):
        return _error("开本只能是 16开 / 32开 / 大32开")
    if not isinstance(print_sheet, (int, float)) or print_sheet <= 0:
        return _error("印张必须为正数（支持 0.5 步进）")
    if print_sheet > 50:
        return _error("印张不能超过 50")
    if qty <= 0:
        return _error("印数必须为正整数")
    if not paper_type:
        return _error("请选择纸张类型")
    if not binding:
        return _error("请选择装订方式")
    if color_pages < 0 or color_pages > 4:
        return _error("色数必须在 0-4 之间")

    params = {
        "book_title": book_title,
        "format": book_format,
        "print_sheet": print_sheet,
        "qty": qty,
        "paper_type": paper_type,
        "binding": binding,
        "color_pages": color_pages,
        "process": process,
        "note": note,
    }

    # 自动关联匹配的纸张/工艺/装订报价，供 AI 工具取证
    material_ids = []
    for material_type, title_value in (
        ("paper", paper_type),
        ("process", process),
        ("binding", binding),
    ):
        if not title_value:
            continue
        material = (
            db.query(Material)
            .filter(
                Material.type == material_type,
                Material.title == title_value,
                Material.enabled == 1,
            )
            .first()
        )
        if material is not None:
            material_ids.append(material.id)

    try:
        task = task_service.create_task(
            db,
            user,
            title=title,
            goal=goal,
            params=params,
            material_ids=material_ids,
        )
    except StarletteHTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return _error(str(detail.get("message") or "创建任务失败"))
    return RedirectResponse(url=f"/tasks/{task.id}", status_code=302)


# ============================ 任务详情 ============================
@app.get("/tasks/{task_id}/versions/compare", include_in_schema=False)
def version_compare_page(
    task_id: int,
    request: Request,
    left: Optional[int] = Query(default=None),
    right: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
):
    """版本对比：金额/明细/依据 + 正文差异（只读）。"""
    import difflib

    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    try:
        task = task_service.get_visible_task(db, task_id, user)
    except StarletteHTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return _message_page("无法查看该任务", str(detail.get("message") or "任务不存在或不在你的权限范围内"), exc.status_code)

    versions = version_service.list_versions(db, task.id, newest_first=False)
    if len(versions) < 2:
        return _message_page("暂无可对比版本", "当前任务至少需要两个版本才能对比。", 400, f"/tasks/{task.id}")
    by_no = {int(v.get("version_no") or 0): v for v in versions}
    left_no = left if left in by_no else versions[0]["version_no"]
    right_no = right if right in by_no else versions[-1]["version_no"]
    lv = by_no[left_no]
    rv = by_no[right_no]

    left_struct = lv.get("structured") or {}
    right_struct = rv.get("structured") or {}
    left_items = {str(x.get("name")): x.get("amount") for x in (left_struct.get("line_items") or [])}
    right_items = {str(x.get("name")): x.get("amount") for x in (right_struct.get("line_items") or [])}
    names = list(dict.fromkeys(list(left_items.keys()) + list(right_items.keys())))
    item_rows = []
    for name in names:
        lval = left_items.get(name)
        rval = right_items.get(name)
        delta = None
        if isinstance(lval, (int, float)) and isinstance(rval, (int, float)):
            delta = round(float(rval) - float(lval), 2)
        item_rows.append({"name": name, "left": lval, "right": rval, "delta": delta})

    left_basis = {(str(b.get("type")), str(b.get("id"))) for b in (lv.get("basis_refs") or []) if isinstance(b, dict)}
    right_basis = {(str(b.get("type")), str(b.get("id"))) for b in (rv.get("basis_refs") or []) if isinstance(b, dict)}
    basis_added = sorted(right_basis - left_basis)
    basis_removed = sorted(left_basis - right_basis)

    diff_lines = []
    for line in difflib.unified_diff(
        (lv.get("content_md") or "").splitlines(),
        (rv.get("content_md") or "").splitlines(),
        fromfile="v" + str(left_no),
        tofile="v" + str(right_no),
        lineterm="",
    ):
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("@@"):
            kind = "meta"
        elif line.startswith("+"):
            kind = "add"
        elif line.startswith("-"):
            kind = "del"
        else:
            kind = "same"
        diff_lines.append({"kind": kind, "text": line})

    context = _page_context(
        user,
        "tasks",
        task_info=task_service.serialize_task(db, task),
        versions=versions,
        left=lv,
        right=rv,
        item_rows=item_rows,
        changed_amounts={
            "total": (left_struct.get("total"), right_struct.get("total")),
            "per_copy": (left_struct.get("per_copy"), right_struct.get("per_copy")),
        },
        basis_added=basis_added,
        basis_removed=basis_removed,
        diff_lines=diff_lines,
    )
    return _render(request, "version_compare.html", context)


@app.get("/tasks/{task_id}", include_in_schema=False)
def task_detail_page(task_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    try:
        task = task_service.get_visible_task(db, task_id, user)
    except StarletteHTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return _message_page(
            "无法查看该任务",
            str(detail.get("message") or "任务不存在或不在你的权限范围内"),
            exc.status_code,
        )

    latest_ai = task_service.get_latest_ai_task(db, task.id)
    ai_task = task_service.ai_task_to_dict(db, latest_ai)
    ai_result = task_service.ai_result_to_dict(db, task_service.get_latest_ai_result(db, task.id))
    tool_calls = task_service.tool_calls_for_task(
        db, task.id, ai_task_id=latest_ai.id if latest_ai else None
    )
    context = _page_context(
        user,
        "tasks",
        task=task_service.serialize_task(db, task, detail=True),
        params=task_service.parse_params(task),
        linked_materials=task_service.linked_materials(db, task.id),
        ai_task=ai_task,
        ai_result=ai_result,
        tool_calls=tool_calls,
        versions=version_service.list_versions(db, task.id),
        reviews=task_service.reviews_for_task(db, task.id),
        last_reject_opinion=next(
            (
                str(r.get("opinion") or "")
                for r in reversed(task_service.reviews_for_task(db, task.id))
                if str(r.get("action") or "").upper() == "REJECT"
            ),
            "",
        ),
        **_permissions(user, task),
        **_form_options(db),
    )
    return _render(request, "task_detail.html", context)


# ============================ 详情页操作 ============================
async def _read_form(request: Request):
    try:
        return await request.form()
    except Exception:
        return {}


def _error_message(exc: StarletteHTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict):
        return str(detail.get("message") or "操作失败")
    return str(detail or "操作失败")


@app.post("/tasks/{task_id}/ai-run", include_in_schema=False)
async def task_ai_run_page(task_id: int, request: Request, db: Session = Depends(get_db)):
    """页面按钮薄封装：内部调用 S2 的 API #12（POST /api/business-tasks/{id}/ai-tasks）。"""
    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    if user.get("role_code") != ROLE_OPERATOR:
        return _message_page("无权限", "审核员不能运行 AI 测算。", 403, back_url=f"/tasks/{task_id}")
    try:
        task = task_service.get_visible_task(db, task_id, user)
    except StarletteHTTPException as exc:
        return _message_page("无权限", _error_message(exc), exc.status_code)

    if task.status not in TASK_EDITABLE_STATUSES:
        return _redirect(f"/tasks/{task.id}", f"任务当前状态为「{task_service.status_label(task.status)}」，不能运行 AI")

    form = await _read_form(request)
    payload = {"note": _form_value(form, "note")}
    fault = _form_value(form, "fault")
    if fault:
        payload["fault"] = fault

    headers = {}
    cookie_header = request.headers.get("cookie")
    if cookie_header:
        headers["cookie"] = cookie_header
    authorization = request.headers.get("authorization")
    if authorization:
        headers["authorization"] = authorization

    try:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://internal.local") as client:
            response = await client.post(
                f"/api/business-tasks/{task.id}/ai-tasks",
                json=payload,
                headers=headers,
                timeout=30.0,
            )
    except Exception:
        logger.exception("页面启动 AI 任务失败 task_id=%s", task.id)
        return _redirect(f"/tasks/{task.id}", "启动 AI 测算失败，请查看日志")

    if response.status_code in (200, 201, 202):
        try:
            body = response.json()
        except Exception:
            body = {}
        data = body.get("data") if isinstance(body, dict) else {}
        if not isinstance(data, dict):
            data = body if isinstance(body, dict) else {}
        ai_task_id = data.get("ai_task_id") or data.get("id")
        return _redirect(f"/tasks/{task.id}", ai_task_id=ai_task_id)

    try:
        body = response.json()
        message = body.get("message") or f"启动 AI 测算失败（HTTP {response.status_code}）"
    except Exception:
        message = f"启动 AI 测算失败（HTTP {response.status_code}）"
    return _redirect(f"/tasks/{task.id}", message)


@app.post("/tasks/{task_id}/result/save", include_in_schema=False)
async def task_result_save_page(task_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    if user.get("role_code") != ROLE_OPERATOR:
        return _message_page("无权限", "审核员不能保存结果版本。", 403, back_url=f"/tasks/{task_id}")
    try:
        task = task_service.get_visible_task(db, task_id, user)
    except StarletteHTTPException as exc:
        return _message_page("无权限", _error_message(exc), exc.status_code)

    form = await _read_form(request)
    content_md = _form_value(form, "content_md")
    change_note = _form_value(form, "change_note")
    ai_task_id = _to_int(form.get("ai_task_id") if hasattr(form, "get") else None, 0)

    source = "MANUAL"
    structured = {}
    basis_refs = None
    try:
        if ai_task_id:
            ai_task = (
                db.query(AiTask)
                .filter(AiTask.id == ai_task_id, AiTask.business_task_id == task.id)
                .first()
            )
            if ai_task is None or ai_task.status != AI_SUCCEEDED:
                raise HTTPException(
                    status_code=400,
                    detail={"code": 400, "message": "AI 运行尚未成功完成，不能采用其结果"},
                )
            ai_result = db.query(AiResult).filter(AiResult.ai_task_id == ai_task.id).first()
            if ai_result is None:
                raise HTTPException(
                    status_code=400, detail={"code": 400, "message": "该 AI 运行没有可用结果"}
                )
            source = "AI"
            content_md = content_md or (ai_result.content_md or "")
            structured = basis_service.parse_json(ai_result.structured_json, {})
            basis_refs = basis_service.basis_refs_of(ai_result)
        else:
            structured_raw = _form_value(form, "structured_json")
            if structured_raw:
                parsed = basis_service.parse_json(structured_raw, None)
                if isinstance(parsed, dict):
                    structured = parsed

        if not content_md.strip() and not structured:
            raise HTTPException(
                status_code=400,
                detail={"code": 400, "message": "请填写结果内容，或选择采用 AI 结果"},
            )
        version = version_service.create_version(
            db,
            task,
            user_id=int(user.get("id") or 0),
            content_md=content_md,
            structured=structured,
            basis_refs=basis_refs,
            source=source,
            change_note=change_note,
        )
    except StarletteHTTPException as exc:
        return _redirect(f"/tasks/{task.id}", _error_message(exc))
    return RedirectResponse(url=f"/tasks/{task.id}#versions", status_code=302)


@app.post("/tasks/{task_id}/submit", include_in_schema=False)
async def task_submit_page(task_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    if user.get("role_code") != ROLE_OPERATOR:
        return _message_page("无权限", "审核员不能提交任务。", 403, back_url=f"/tasks/{task_id}")
    try:
        task = task_service.get_visible_task(db, task_id, user)
    except StarletteHTTPException as exc:
        return _message_page("无权限", _error_message(exc), exc.status_code)

    form = await _read_form(request)
    version_no = _to_int(form.get("version_no") if hasattr(form, "get") else None, 0)
    comment = _form_value(form, "comment")
    try:
        task_service.submit_task(
            db, task, version_no=version_no or None, comment=comment or None
        )
    except StarletteHTTPException as exc:
        return _redirect(f"/tasks/{task.id}", _error_message(exc))
    return _redirect(f"/tasks/{task.id}", submitted=1)


@app.post("/tasks/{task_id}/review", include_in_schema=False)
async def task_review_page(task_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    if user.get("role_code") != ROLE_REVIEWER:
        return _message_page("无权限", "只有审核员可以审核任务。", 403, back_url=f"/tasks/{task_id}")
    try:
        task = task_service.get_visible_task(db, task_id, user)
    except StarletteHTTPException as exc:
        return _message_page("无权限", _error_message(exc), exc.status_code)

    form = await _read_form(request)
    action = _form_value(form, "action", "APPROVE").upper() or "APPROVE"
    opinion = _form_value(form, "opinion")
    version_no = _to_int(form.get("version_no") if hasattr(form, "get") else None, 0)
    try:
        task_service.review_task(
            db,
            task,
            reviewer=user,
            version_no=version_no or None,
            action=action,
            opinion=opinion,
        )
    except StarletteHTTPException as exc:
        return _redirect(f"/tasks/{task.id}", _error_message(exc))
    return _redirect(f"/tasks/{task.id}", reviewed=1)


# ============================ 资料库 ============================
def _materials_context(user: Optional[dict], db: Session, type_filter: Optional[str] = None) -> dict:
    query = db.query(Material)
    selected_type = (type_filter or "").strip().lower()
    if selected_type in ("", "all", "全部", "全部类型"):
        selected_type = ""
    if selected_type:
        query = query.filter(Material.type == selected_type)
    rows = (
        query.order_by(Material.type.asc(), Material.material_no.asc(), Material.version_no.desc())
        .limit(500)
        .all()
    )
    materials = [
        {
            "id": row.id,
            "material_no": row.material_no,
            "type": row.type,
            "type_label": MATERIAL_TYPE_LABELS.get(row.type, row.type),
            "title": row.title,
            "source": row.source,
            "version_no": row.version_no,
            "enabled": bool(int(row.enabled or 0)),
            "content": basis_service.parse_json(row.content_json, {}),
            "created_at": row.created_at,
        }
        for row in rows
    ]
    return _page_context(
        user,
        "materials",
        materials=materials,
        type_filter=selected_type or "ALL",
        type_options=[{"value": value, "label": label} for value, label in MATERIAL_TYPE_LABELS.items()],
    )


@app.get("/materials", include_in_schema=False)
def materials_page(
    request: Request,
    type: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    return _render(request, "materials.html", _materials_context(user, db, type))


@app.post("/materials/create", include_in_schema=False)
async def material_create_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    if user.get("role_code") != ROLE_OPERATOR:
        return _message_page("无权限", "只有业务操作员可以新增资料。", 403, back_url="/materials")

    form = await _read_form(request)
    payload = {
        "type": _form_value(form, "type", "paper") or "paper",
        "title": _form_value(form, "title"),
        "source": _form_value(form, "source", "手工录入") or "手工录入",
        "content": _form_value(form, "content", "{}") or "{}",
        "material_no": _form_value(form, "material_no") or None,
    }
    try:
        request_model = MaterialCreateRequest(**payload)
        materials_api.create_material(payload=request_model, user=user, db=db)
    except StarletteHTTPException as exc:
        return _redirect("/materials", _error_message(exc))
    except Exception as exc:  # 参数校验失败（pydantic）
        return _redirect("/materials", f"资料参数不合法：{exc}")
    return _redirect("/materials", created=1)


# ============================ 规则库 ============================
def _rules_context(user: Optional[dict], db: Session) -> dict:
    rows = db.query(Rule).order_by(Rule.id.asc()).limit(500).all()
    rules = [
        {
            "id": row.id,
            "rule_code": row.rule_code,
            "name": row.name,
            "business_type": row.business_type,
            "rule_type": row.rule_type,
            "severity": row.severity,
            "source": row.source,
            "enabled": bool(int(row.enabled or 0)),
            "content": basis_service.parse_json(row.content_json, {}),
            "created_at": row.created_at,
        }
        for row in rows
    ]
    return _page_context(user, "rules", rules=rules)


@app.get("/validate", include_in_schema=False)
def validate_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    context = _page_context(
        user,
        "validate",
        **_form_options(db),
        form_data={},
        validation=None,
    )
    return _render(request, "validate.html", context)


@app.post("/validate", include_in_schema=False)
async def validate_submit(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    try:
        form = await request.form()
    except Exception:
        form = {}
    params: dict = {}
    fmt = _form_value(form, "format")
    if fmt:
        params["format"] = fmt
    sheet = _form_value(form, "print_sheet")
    if sheet:
        params["print_sheet"] = _to_float(sheet, 0)
    qty = _form_value(form, "qty")
    if qty:
        params["qty"] = _to_int(qty, 0)
    paper = _form_value(form, "paper_type")
    if paper:
        params["paper_type"] = paper
    binding = _form_value(form, "binding")
    if binding:
        params["binding"] = binding
    color = _form_value(form, "color_pages")
    if color:
        params["color_pages"] = _to_int(color, 0)
    process = _form_value(form, "process")
    if process:
        params["process"] = process

    from app.agent.tools import validate_spec

    result = validate_spec(params) if params else {"ok": False, "error_code": "TOOL_ARGS_INVALID", "data": {"valid": False, "issues": [{"field": "params", "message": "请至少填写一个参数", "rule_code": ""}]}}
    data = result.get("data") or {}
    context = _page_context(
        user,
        "validate",
        **_form_options(db),
        form_data=dict(form),
        validation={
            "ok": bool(result.get("ok")),
            "valid": bool(data.get("valid")),
            "issues": data.get("issues") or [],
            "normalized": data.get("normalized") or {},
            "error_code": result.get("error_code"),
            "error_message": result.get("error_message"),
            "params": params,
        },
    )
    return _render(request, "validate.html", context)


@app.get("/rules", include_in_schema=False)
def rules_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    return _render(request, "rules.html", _rules_context(user, db))


@app.post("/rules/create", include_in_schema=False)
async def rule_create_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    if user.get("role_code") != ROLE_OPERATOR:
        return _message_page("无权限", "只有业务操作员可以新增规则。", 403, back_url="/rules")

    form = await _read_form(request)
    payload = {
        "rule_code": _form_value(form, "rule_code"),
        "name": _form_value(form, "name"),
        "business_type": _form_value(form, "business_type", "print_cost") or "print_cost",
        "rule_type": _form_value(form, "rule_type", "spec") or "spec",
        "severity": _form_value(form, "severity", "error") or "error",
        "source": _form_value(form, "source"),
        "content": _form_value(form, "content", "{}") or "{}",
    }
    try:
        request_model = RuleCreateRequest(**payload)
        rules_api.create_rule(payload=request_model, user=user, db=db)
    except StarletteHTTPException as exc:
        return _redirect("/rules", _error_message(exc))
    except Exception as exc:
        return _redirect("/rules", f"规则参数不合法：{exc}")
    return _redirect("/rules", created=1)


# ============================ 系统信息 ============================
def _system_fallback_html(meta: dict) -> str:
    rows = "".join(
        f"<tr><th>{html_lib.escape(str(key))}</th><td>{html_lib.escape(str(value))}</td></tr>"
        for key, value in meta.items()
        if key != "limits"
    )
    limits = "".join(
        f"<span class='tag'>{html_lib.escape(str(key))} = {html_lib.escape(str(value))}</span>"
        for key, value in (meta.get("limits") or {}).items()
    )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>系统信息</title><style>body{{font-family:"Microsoft YaHei",Arial,sans-serif;background:#f5f7fb;color:#1f2d3d}}
.box{{max-width:860px;margin:40px auto;background:#fff;border-radius:8px;padding:28px 36px;box-shadow:0 2px 12px rgba(20,40,80,.08)}}
h1{{font-size:20px;color:#123a6b}}table{{border-collapse:collapse;width:100%}}th,td{{border-bottom:1px solid #eef2f7;padding:9px 10px;text-align:left;font-size:14px}}
th{{width:220px;color:#5b6b7c;font-weight:500}}.tag{{display:inline-block;background:#eef4fc;color:#1b5fa8;border-radius:4px;padding:4px 10px;margin:4px 8px 0 0;font-size:13px}}
a{{color:#1b5fa8}}</style></head><body><div class="box">
<h1>系统信息（内置兜底页：app/templates/system.html 未提供时显示）</h1>
<table>{rows}</table><p style="margin-top:16px">限额：{limits}</p>
<p><a href="/tasks">返回任务列表</a> ｜ <a href="/api/docs">API 文档</a></p></div></body></html>"""


@app.get("/system", include_in_schema=False)
def system_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    try:
        meta = system_api.system_meta(user=user, db=db)["data"]
    except Exception:
        logger.exception("读取系统信息失败")
        meta = {
            "app": APP_NAME,
            "version": APP_VERSION,
            "llm_mode": settings.llm_mode,
            "model": settings.llm_model,
            "limits": settings.limits,
        }
    try:
        return _render(request, "system.html", _page_context(user, "system", meta=meta))
    except Exception:
        return HTMLResponse(_system_fallback_html(meta))


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """避免浏览器默认请求 favicon 产生 404（不影响功能，保持控制台干净）。"""
    return HTMLResponse(content="", status_code=204)


DIAGRAMS_FILE = BASE_DIR / "docs" / "三张图演示.html"


@app.get("/diagrams", include_in_schema=False)
def diagrams_page():
    """三张图 HTML 演示版（离线单文件；教师现场投屏用）。"""
    try:
        content = DIAGRAMS_FILE.read_text(encoding="utf-8")
    except OSError:
        raise HTTPException(status_code=404, detail="三张图演示文件不存在：docs/三张图演示.html")
    return HTMLResponse(content)


__all__ = ["app"]
