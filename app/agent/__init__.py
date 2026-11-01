"""AI/Agent 子系统（S2）。

模块：
- ``prompts``      系统提示词、任务上下文、护栏重提示；
- ``tools``        4 个业务工具（检索/规则/校验/计算）、OpenAI function schema、参数校验；
- ``llm``          OpenAI 兼容 chat/completions 客户端 + 确定性 mock 脚本；
- ``orchestrator`` Agent 循环、限额、完成判定、依据校验、故障注入、残留 RUNNING 清理。

对外主要入口见 ``orchestrator.start_ai_task`` / ``run_ai_task`` / ``cleanup_stale_running``。
"""

__all__ = ["prompts", "tools", "llm", "orchestrator"]
