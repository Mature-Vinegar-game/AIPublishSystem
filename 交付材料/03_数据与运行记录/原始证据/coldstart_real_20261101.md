# 真实模式冷启动复检记录（提交包实测）

- 日期：2026-11-01 22:35（Asia/Shanghai）
- 目的：验证**提交包源码 zip** 在干净目录解压后，使用真实模型（`LLM_MODE=real`）可启动并完成一次真实 AI 测算（对应 [原文 L63] 验收须真实调用、[L83] 11/5 前 README 冷启动）。
- 环境：Windows 11 + Python 3.14.6；依赖已按 requirements 安装；干净目录 `F:\deepseek workarea\_coldstart_real_20261101`；包内不含 `.env`/`data/`/`logs/`，测试时复制本机 `.env`（含真实 Key，不进入提交包）。
- 步骤：解压交付包源码 zip → 复制 `.env`（`LLM_MODE=real`、`LLM_MODEL=deepseek-flash`）→ `start.bat`（preflight → seed → uvicorn :8000）。

## 实测结果

| # | 检查项 | 结果 |
|---|---|---|
| 1 | `GET /login` | 200 |
| 2 | operator 登录 | 200，`code=0` |
| 3 | `/api/system/meta` | 200；`llm_mode=real`、`key_configured=true`、`model=deepseek-flash` |
| 4 | 示例任务 `BT-DEMO-0001` | 存在（id=1） |
| 5 | 真实 AI 运行 | 202 创建，状态 **SUCCEEDED**；耗时约 **15.6 s** |
| 6 | 模型/工具计数 | 模型轮次 6；工具调用 **8 次**；工具类别 **retrieval / validation / calculation（3 类）** |
| 7 | 业务结果 | 总成本 **14638.00 元**、单册 **4.88 元**；`basis_ok=true`；`missing_data=[]` |
| 8 | 三张图 `/diagrams` | 200 |

## 结论

- **提交包在干净目录 + 真实模型下可启动、可完成真实 AI 测算**，且结果与开发机验证一致（14638.00 / 4.88）。
- 该测试与 `coldstart_20261101.md`（mock 模式冷启动）共同覆盖"提交包可运行性"与"真实调用"两项验收要求。
- 本记录只包含脱敏信息（不含 Key、Authorization）；真实 Key 仅在测试机 `.env` 中，未进入源码包/交付包/Git 仓库。
