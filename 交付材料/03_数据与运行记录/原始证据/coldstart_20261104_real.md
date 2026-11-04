# 11/4 最终真实模式冷启动复检（提交包）

- 日期：2026-11-04 10:45（Asia/Shanghai）
- 对象：最终交付包 `01_可运行程序及源码/出版AI系统_源码_20261101.zip`
- 方法：解压到全新干净目录 `_coldstart_1104_final`，复制本机真实 `.env`（`LLM_MODE=real`、`LLM_MODEL=deepseek-flash`）→ `start.bat`。

## 结果：全部通过

| 检查项 | 结果 |
|---|---|
| 服务启动（preflight/seed/uvicorn :8000） | 通过 |
| operator 登录 API | 200，`code=0` |
| `/api/system/meta` | 200，`llm_mode=real`、`key_configured=true`、`model=deepseek-flash` |
| 真实 AI 运行（BT-DEMO-0001） | 202 → **SUCCEEDED**，约 12.2s |
| 模型轮次 / 工具调用 / 类型 | 4 轮 / **8 次** / retrieval、validation、calculation（3 类） |
| 业务结果 | **14638.00 元、单册 4.88 元**；`basis_ok=true` |
| 限额 | 10/8/30s/3000ms/120s（meta 返回正确） |

## 结论

- 当前提交包在 11/4 环境下（干净目录 + 真实 Key + deepseek-flash）**可直接启动并完成真实测算**；
- 与 11/1 的验证结果一致（14638.00 / 4.88），说明材料与程序在 2.5 天后仍可复现；
- 11/5 提交前若更换演示机，按同一流程重跑本检查即可。
