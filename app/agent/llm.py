"""LLM 客户端（BUILD_SPEC §6）。

- 真实模式：``httpx`` 直连 ``{LLM_BASE_URL}/chat/completions``，Bearer key，
  ``tools``（4 个业务工具 schema）、``tool_choice="auto"``、``temperature=0.2``、
  ``timeout=LLM_TIMEOUT_S``，解析 ``choices[0].message.tool_calls``。
- mock 模式：不联网，按固定脚本返回确定性的 tool_calls（4 个工具各 1 次），
  第二轮根据工具结果生成最终 JSON。用于无 key 开发/断网排练；
  ``ai_tasks.model_mode`` 会标记为 ``mock``。

真实模式网络/HTTP/解析失败统一抛 :class:`LLMError`，编排器记 FAILED MODEL_ERROR。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.agent.tools import get_tool_schemas
from app.config import settings


logger = logging.getLogger("app.agent.llm")


def _setting(name: str, default: Any = None) -> Any:
    """兼容 settings 属性大小写两种写法。"""
    if hasattr(settings, name):
        return getattr(settings, name)
    upper = name.upper()
    if hasattr(settings, upper):
        return getattr(settings, upper)
    return default


class LLMError(RuntimeError):
    """模型调用失败（网络/HTTP/超时/响应解析）。"""

    def __init__(self, message: str, code: str = "MODEL_ERROR") -> None:
        super().__init__(message)
        self.code = code


def _parse_arguments(raw: Any) -> tuple[dict[str, Any], str | None]:
    """解析 tool_call.arguments；失败时返回 (空 dict, 错误描述)。"""
    if raw is None or raw == "":
        return {}, None
    if isinstance(raw, dict):
        return raw, None
    if not isinstance(raw, str):
        return {}, f"工具参数类型异常：{type(raw).__name__}"
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        return {}, f"工具参数不是合法 JSON：{exc}"
    if not isinstance(parsed, dict):
        return {}, "工具参数必须是 JSON 对象"
    return parsed, None


def _content_to_text(content: Any) -> str | None:
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") in ("text", "output_text"):
                parts.append(str(part.get("text") or ""))
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts) if parts else None
    return str(content)


class LLMClient:
    """OpenAI 兼容 chat/completions 客户端 + mock 脚本模式。"""

    def __init__(
        self,
        *,
        mode: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        raw_mode = (mode or _setting("llm_mode", "mock") or "mock").strip().lower()
        self.mode = "mock" if raw_mode == "mock" else "real"
        self.base_url = str(base_url if base_url is not None else _setting("llm_base_url", "") or "").rstrip("/")
        self.model = str(model if model is not None else _setting("llm_model", "deepseek-chat") or "deepseek-chat")
        self.api_key = str(api_key if api_key is not None else _setting("llm_api_key", "") or "")
        timeout_value = timeout_s if timeout_s is not None else _setting("llm_timeout_s", 30)
        try:
            self.timeout_s = float(timeout_value)
        except (TypeError, ValueError):
            self.timeout_s = 30.0
        if self.timeout_s <= 0:
            self.timeout_s = 30.0

    # ------------------------------------------------------------------
    # 统一入口
    # ------------------------------------------------------------------
    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        """返回 ``{"content": str|None, "tool_calls": [{"id","name","arguments",...}]}``。"""
        if self.mode == "mock":
            return self._mock_chat(messages)
        return self._real_chat(
            messages,
            tools=tools if tools is not None else get_tool_schemas(),
            tool_choice=tool_choice,
            temperature=temperature,
        )

    # ------------------------------------------------------------------
    # 真实模型
    # ------------------------------------------------------------------
    def _real_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        tool_choice: str,
        temperature: float,
    ) -> dict[str, Any]:
        if not self.base_url:
            raise LLMError("LLM_BASE_URL 未配置，无法调用真实模型")
        if not self.api_key or self.api_key.startswith("sk-PLEASE-REPLACE"):
            raise LLMError("LLM_API_KEY 未配置或仍为占位值，无法调用真实模型")

        url = self.base_url
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "tool_choice": tool_choice,
            "tools": tools,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(
                timeout=httpx.Timeout(self.timeout_s, connect=min(10.0, self.timeout_s)),
                follow_redirects=True,
            ) as client:
                response = client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise LLMError(f"模型调用超时（{self.timeout_s:g}s）：{exc}") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"模型网络请求失败：{exc}") from exc

        if response.status_code >= 400:
            body = (response.text or "")[:500]
            raise LLMError(f"模型接口返回 HTTP {response.status_code}：{body}")

        try:
            body = response.json()
        except ValueError as exc:
            raise LLMError(f"模型响应不是合法 JSON：{exc}") from exc
        if not isinstance(body, dict):
            raise LLMError("模型响应格式异常（顶层不是对象）")

        choices = body.get("choices") or []
        if not choices or not isinstance(choices, list):
            raise LLMError(f"模型响应缺少 choices：{str(body)[:300]}")
        message = choices[0].get("message") or {}
        if not isinstance(message, dict):
            raise LLMError("模型响应 choices[0].message 格式异常")

        content = _content_to_text(message.get("content"))
        tool_calls: list[dict[str, Any]] = []
        for index, item in enumerate(message.get("tool_calls") or []):
            if not isinstance(item, dict):
                continue
            function = item.get("function") or {}
            if not isinstance(function, dict):
                continue
            name = str(function.get("name") or "").strip()
            arguments, arguments_error = _parse_arguments(function.get("arguments"))
            tool_calls.append(
                {
                    "id": str(item.get("id") or f"call_{index + 1}"),
                    "name": name,
                    "arguments": arguments,
                    "raw_arguments": function.get("arguments"),
                    "arguments_error": arguments_error,
                }
            )

        logger.info(
            "LLM 调用完成 model=%s finish_reason=%s tool_calls=%d",
            self.model,
            choices[0].get("finish_reason"),
            len(tool_calls),
        )
        return {
            "content": content,
            "tool_calls": tool_calls,
            "model": body.get("model") or self.model,
            "usage": body.get("usage"),
            "finish_reason": choices[0].get("finish_reason"),
        }

    # ------------------------------------------------------------------
    # Mock 模式：确定性脚本
    # ------------------------------------------------------------------
    def _mock_chat(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        params = _extract_task_params(messages)
        has_tool_round = any(
            isinstance(msg.get("tool_calls"), list) and msg.get("tool_calls")
            for msg in messages
            if isinstance(msg, dict) and msg.get("role") == "assistant"
        )
        if not has_tool_round:
            tool_calls = [
                {
                    "id": "call_mock_search_1",
                    "name": "search_quotes",
                    "arguments": {"category": "paper"},
                    "raw_arguments": json.dumps({"category": "paper"}, ensure_ascii=False),
                    "arguments_error": None,
                },
                {
                    "id": "call_mock_rules_1",
                    "name": "get_rules",
                    "arguments": {"business_type": "print_cost"},
                    "raw_arguments": json.dumps({"business_type": "print_cost"}, ensure_ascii=False),
                    "arguments_error": None,
                },
                {
                    "id": "call_mock_validate_1",
                    "name": "validate_spec",
                    "arguments": {"params": params},
                    "raw_arguments": json.dumps({"params": params}, ensure_ascii=False),
                    "arguments_error": None,
                },
                {
                    "id": "call_mock_calc_1",
                    "name": "calculate_cost",
                    "arguments": {"params": params},
                    "raw_arguments": json.dumps({"params": params}, ensure_ascii=False),
                    "arguments_error": None,
                },
            ]
            return {
                "content": None,
                "tool_calls": tool_calls,
                "model": "mock-script",
                "usage": None,
                "finish_reason": "tool_calls",
            }

        final = _build_mock_final(messages)
        return {
            "content": json.dumps(final, ensure_ascii=False),
            "tool_calls": [],
            "model": "mock-script",
            "usage": None,
            "finish_reason": "stop",
        }


# ---------------------------------------------------------------------------
# Mock 辅助：从 messages 里还原任务参数 / 工具结果
# ---------------------------------------------------------------------------


def _extract_first_json_object(text: str, start: int = 0) -> Any:
    """从 text[start:] 中截取第一个完整 JSON 对象（处理字符串与转义）。"""
    begin = text.find("{", start)
    if begin < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(begin, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[begin : index + 1])
                except (TypeError, ValueError):
                    return None
    return None


def _extract_task_params(messages: list[dict[str, Any]]) -> dict[str, Any]:
    marker = "【任务参数 JSON】"
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        text = message.get("content")
        if not isinstance(text, str):
            continue
        index = text.find(marker)
        if index < 0:
            continue
        parsed = _extract_first_json_object(text, index + len(marker))
        if isinstance(parsed, dict):
            return parsed
    return {}


def _iter_tool_payloads(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        content = message.get("content")
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except (TypeError, ValueError):
                continue
            if isinstance(parsed, dict):
                payloads.append(parsed)
    return payloads


def _find_payload(payloads: list[dict[str, Any]], *data_keys: str) -> dict[str, Any] | None:
    for payload in reversed(payloads):
        data = payload.get("data")
        if isinstance(data, dict) and any(key in data for key in data_keys):
            return payload
    return None


def _build_mock_final(messages: list[dict[str, Any]]) -> dict[str, Any]:
    payloads = _iter_tool_payloads(messages)
    search_payload = _find_payload(payloads, "items")
    rules_payload = _find_payload(payloads, "rules")
    validate_payload = _find_payload(payloads, "valid")
    calc_payload = _find_payload(payloads, "line_items", "currency", "missing")

    basis_refs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def add_ref(ref_type: str, ref_id: Any, label: Any) -> None:
        ref_id = str(ref_id or "").strip()
        if not ref_id or ref_id in seen_ids:
            return
        seen_ids.add(ref_id)
        basis_refs.append({"type": ref_type, "id": ref_id, "label": str(label or ref_id)})

    calc_ok = bool(calc_payload and calc_payload.get("ok"))
    if calc_ok:
        # 计算成功时，依据以实际参与计算的单价资料为主，避免罗列无关报价
        for source_id in calc_payload.get("source_ids") or []:
            add_ref("material", source_id, "成本计算使用单价")
    elif search_payload:
        for item in (search_payload.get("data") or {}).get("items") or []:
            add_ref("material", item.get("material_no"), item.get("title") or "报价资料")
    if rules_payload:
        for rule in (rules_payload.get("data") or {}).get("rules") or []:
            if rule.get("rule_type") not in (None, "spec"):
                continue
            add_ref("rule", rule.get("rule_code"), rule.get("name") or "业务规则")

    calc_data = (calc_payload or {}).get("data") or {}
    line_items = calc_data.get("line_items") or []
    total = calc_data.get("total") if calc_ok else None
    per_copy = calc_data.get("per_copy") if calc_ok else None
    missing: list[str] = []
    if not calc_ok:
        if isinstance(calc_data.get("missing"), list):
            missing.extend(str(item) for item in calc_data["missing"])
        if not missing:
            missing.append(
                str(
                    (calc_payload or {}).get("error_message")
                    or "calculate_cost 未返回可用结果，缺少必需单价"
                )
            )

    uncertain: list[str] = []
    if validate_payload and not (validate_payload.get("data") or {}).get("valid"):
        for issue in (validate_payload.get("data") or {}).get("issues") or []:
            message = issue.get("message") or issue.get("field")
            if message:
                uncertain.append(f"参数校验：{message}")

    if calc_ok:
        content_lines = [
            "## 印制成本测算结果",
            "",
            f"- 总成本：**{float(total):.2f} 元**",
            f"- 单册成本：**{float(per_copy):.2f} 元**",
            "",
            "### 成本明细",
            "",
            "| 费用项 | 计算公式 | 金额（元） |",
            "|---|---|---:|",
        ]
        for item in line_items:
            content_lines.append(
                f"| {item.get('name', '')} | {item.get('formula', '')} | {float(item.get('amount') or 0):.2f} |"
            )
        content_lines.extend(["", "### 依据", ""])
        for ref in basis_refs:
            content_lines.append(f"- {ref['type']}：{ref['id']}（{ref['label']}）")
        if uncertain:
            content_lines.extend(["", "### 不确定项", ""] + [f"- {item}" for item in uncertain])
    else:
        content_lines = [
            "## 印制成本测算结果",
            "",
            "当前资料不足，无法给出总成本与单册成本。",
            "",
            "### 缺少的资料/参数",
            "",
        ]
        content_lines.extend([f"- {item}" for item in missing] or ["- 未取得完整报价资料"])
        content_lines.extend(["", "### 已获取的依据", ""])
        for ref in basis_refs:
            content_lines.append(f"- {ref['type']}：{ref['id']}（{ref['label']}）")

    content_lines.extend(
        [
            "",
            "> 说明：本次为开发期模拟响应（mock），非真实模型调用。",
        ]
    )

    return {
        "content_md": "\n".join(content_lines),
        "structured": {
            "line_items": line_items if calc_ok else [],
            "total": total,
            "per_copy": per_copy,
            "missing": missing,
            "uncertain": uncertain,
        },
        "basis_refs": basis_refs,
        "uncertain_items": uncertain,
        "missing_data": missing,
    }
