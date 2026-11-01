# 最终提交包冷启动验证（2026-11-01 23:15）

> 对象：当前最终交付包 `01_可运行程序及源码/出版AI系统_源码_20261101.zip`（含 favicon 修复、demo_check、备用录屏说明等最新改动）。
> 方法：解压到全新干净目录 `_coldstart_final_20261101`，`copy .env.example .env` 并设 `LLM_MODE=mock`（真实模式此前已在干净目录验证过），双击 `start.bat`。

## 结果：7/7 通过

| # | 检查项 | 结果 |
|---|---|---|
| 1 | `start.bat` 预检/种子/服务启动 | 通过（preflight + seed + uvicorn :8000） |
| 2 | `GET /login` | 200，页面含"登录" |
| 3 | operator 登录 API | 200，`code=0` |
| 4 | `/api/system/meta` | 200，`llm_mode=mock`（自检模式） |
| 5 | 限额 | `max_tool_calls=10`、`ai_run_timeout_s=120` |
| 6 | `/api/business-tasks` | 200 |
| 7 | `/diagrams` 三张图 / `/favicon.ico` | 200 / 204（控制台无 404） |

## 结论

- 最终提交包在干净目录按 README 可启动，页面/API/三张图/静态资源均正常；
- 真实模式（deepseek-flash）已在本提交包的早前版本上完成冷启动真实调用（见 `coldstart_real_20261101.md`），本次改动（favicon 路由、自检脚本、文档）不涉及模型链路；
- 11/5 提交前将按同一流程在演示机做最后一次 real 模式冷启动与彩排。
