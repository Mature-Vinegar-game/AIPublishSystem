# -*- coding: utf-8 -*-
"""追加 BUG-006/007（打包与 start.bat 冷启动问题）并同步统计与交付包文案。"""
import io
from pathlib import Path

ROOT = Path(r"F:\deepseek workarea\出版AI系统")

# ---------- docs/07 ----------
p = ROOT / "docs" / "07_缺陷与回归记录.md"
t = io.open(p, encoding="utf-8").read()

anchor = "| BUG-005 | 2026-11-01 | 部署/启动 | 工程 / 依赖部署 |"
idx = t.find(anchor)
assert idx >= 0
end = t.find("\n", idx)
new_rows = """
| BUG-006 | 2026-11-01 | 冷启动自检 | 工程 / 交付打包 | 源码交付包漏打 `app/__init__.py` 等包初始化文件，解压后启动报 `ImportError: cannot import name 'APP_NAME' from 'app'`（提交包不可运行） | 1. 解压源码 zip 到干净目录 2. `start.bat` 3. 观察启动日志 | 致命 S1 | Lead/开发组 | v1.1（2026-11-01） | 冷启动自检、启动冒烟 | 已关闭 |
| BUG-007 | 2026-11-01 | 冷启动自检 | 工程 / 启动脚本 | `start.bat` 为 LF 行尾，Windows cmd 解析错乱（出现 `'ements.txt' 不是内部命令` 等），无法一键启动 | 1. 双击 `start.bat` 2. 观察多行报错、服务未启动 | 严重 S2 | Lead/开发组 | v1.1（2026-11-01） | 冷启动自检、启动冒烟 | 已关闭 |"""
t = t[:end] + new_rows + t[end:]

t = t.replace("> 说明：以上 5 条为 2026-11-01 开发联调与真实模式验收中**实际出现**的缺陷，均已修复并完成回归；修复证据见 `evidence/` 与 git 提交历史（v0.2/v0.3）。",
              "> 说明：以上 7 条为 2026-11-01 开发联调、真实模式验收与提交包冷启动自检中**实际出现**的缺陷，均已修复并完成回归；修复证据见 `evidence/`（含 `coldstart_20261101.md`）与 git 提交历史（v0.2/v0.3/v1.1）。")

rg_anchor = "| RG-005 | BUG-005 |"
idx = t.find(rg_anchor)
assert idx >= 0
end = t.find("\n", idx)
new_rg = """
| RG-006 | BUG-006 | 冷启动自检、启动冒烟 | v1.1（2026-11-01） | 干净目录 / mock | **PASS**（重新打包后解压启动成功：preflight/seed/uvicorn 均通过，登录与 meta 200） | 未引入（登录/任务/图示页同批复测通过） | Lead/开发组 | 2026-11-01 |
| RG-007 | BUG-007 | 冷启动自检 | v1.1（2026-11-01） | Windows / cmd | **PASS**（start.bat 转 CRLF 后中文与命令解析正常） | 未引入 | Lead/开发组 | 2026-11-01 |"""
t = t[:end] + new_rg + t[end:]

t = t.replace("BUG-001~005 全部已关闭并完成回归", "BUG-001~007 全部已关闭并完成回归")
t = t.replace("| 缺陷总数 | **5** |", "| 缺陷总数 | **7** |")
t = t.replace("| 按严重级 | S1 **1**（已关闭）/ S2 **3**（已关闭）/ S3 **1**（已关闭）/ S4 0 |",
              "| 按严重级 | S1 **2**（已关闭）/ S2 **4**（已关闭）/ S3 **1**（已关闭）/ S4 0 |")
t = t.replace("| 已关闭 / 待回归 / 挂起 | **5** / 0 / 0 |", "| 已关闭 / 待回归 / 挂起 | **7** / 0 / 0 |")
t = t.replace("| 回归执行次数 | **5**（每条缺陷 ≥1 次，全部 PASS）", "| 回归执行次数 | **7**（每条缺陷 ≥1 次，全部 PASS）")
io.open(p, "w", encoding="utf-8").write(t)
print("docs/07 updated")

# ---------- 交付包脚本文案 ----------
ps = ROOT / "scripts" / "_make_delivery.py"
s = io.open(ps, encoding="utf-8").read()
s = s.replace("缺陷 5 条已关闭+回归", "缺陷 7 条已关闭+回归")
s = s.replace("BUG-001~BUG-005 均已修复并完成回归", "BUG-001~BUG-007 均已修复并完成回归")
s = s.replace("BUG-001~005，见 04 材料", "BUG-001~007，见 04 材料")
s = s.replace("缺陷 5 条已关闭", "缺陷 7 条已关闭")
io.open(ps, "w", encoding="utf-8").write(s)
print("delivery script text updated")

# ---------- 04 未解决问题清单源文案（脚本内）----------
s2 = io.open(ps, encoding="utf-8").read()
if "BUG-001~BUG-007 均已修复" not in s2:
    print("warning: delivery script defect text mismatch")
