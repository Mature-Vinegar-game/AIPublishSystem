# 冷启动自检记录（README 可启动性验证）

- 日期：2026-11-01 22:20（Asia/Shanghai）
- 目的：验证提交包 `01_可运行程序及源码/出版AI系统_源码_20261101.zip` 解压后**仅按 README 步骤**即可启动（对应 [原文 L83] 11/5 前按 README 检查演示环境）。
- 环境：Windows 11 + 系统 Python 3.14.6；依赖已按 `requirements.txt` 安装（用户级 site-packages）；**干净目录** `F:\deepseek workarea\_coldstart_20261101`；包内不含 `.env`、`data/`、`logs/`。
- 步骤：
  1. 解压源码 zip 到干净目录；
  2. `copy .env.example .env`，将 `LLM_MODE` 改为 `mock`（避免把真实 Key 放入自检包；真实模式另行验证）；
  3. 双击 `start.bat`（内部：preflight → seed_demo → uvicorn :8000 --workers 1）。

## 自检结果

| # | 检查项 | 结果 | 证据 |
|---|---|---|---|
| 1 | `start.bat` 中文输出与命令解析 | 通过（修复 CRLF 后） | 控制台 preflight/seed 输出正常 |
| 2 | 环境预检 | 通过（依赖 9/9、`.env` 存在、data/logs 可写、DB 可连、端口空闲） | preflight 输出 |
| 3 | 演示数据初始化 | 通过（2 账号、17 资料、15 规则、1 示例任务） | seed 输出 |
| 4 | 服务启动 | 通过（uvicorn 单 worker :8000） | 端口监听 |
| 5 | `GET /login` | 200，含"登录" | HTTP |
| 6 | 登录 API（operator/123456） | 200，`code=0` | HTTP |
| 7 | `GET /api/system/meta` | 200，`llm_mode=mock`，限额 10/8/30/3000/120/10 正确 | JSON |
| 8 | `GET /api/business-tasks` | 200，示例任务 1 条 | JSON |
| 9 | `GET /diagrams`（三张图） | 200，HTML 正常 | HTTP |
| 10 | `GET /system`（未登录） | 302/登录页（符合权限设计） | HTTP |

## 本轮发现并修复的问题

| 缺陷 | 现象 | 修复 | 状态 |
|---|---|---|---|
| BUG-006 | 源码交付包漏打 `app/__init__.py` 等包初始化文件，解压后启动报 `ImportError: cannot import name 'APP_NAME' from 'app'` | 打包脚本排除规则修正（仅排除下划线开头的临时脚本，不再排除 `__init__.py`）；重新打包后冷启动通过 | 已关闭 |
| BUG-007 | `start.bat` 为 LF 行尾，Windows cmd 解析错乱（出现 `'ements.txt' 不是内部命令` 等），无法一键启动 | 统一转为 CRLF 行尾（UTF-8）；重新冷启动通过 | 已关闭 |

## 结论

- 提交包在干净目录按 README 可启动（mock 模式）；
- 真实模式已在开发机 `.env`（`LLM_MODE=real`、`deepseek-flash`）下验证：9 次工具调用/3 类工具/总成本 14638.00 元（见 `real_run_20261101_213054.md`）；
- 11/5 提交前将按同一流程在演示机再做一次 real 模式冷启动与彩排。
