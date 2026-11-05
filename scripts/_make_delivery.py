# -*- coding: utf-8 -*-
r"""生成《交付材料/》目录：5 项提交物 + 材料索引 + 表7/表8 模板 + 演示手册。

使用 bundled Python（含 python-docx）运行：
  C:\Users\LC_CC\.dsh\dsh-runtimes\...\python.exe scripts/_make_delivery.py
"""
import hashlib
import io
import os
import shutil
import zipfile
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(r"F:\deepseek workarea\出版AI系统")
DELIVERY = ROOT / "交付材料"
DOCS = ROOT / "docs"
EVID = ROOT / "evidence"
ACCENT = RGBColor(0x1F, 0x4E, 0x79)

EXCLUDE_DIRS = {
    ".git", ".venv", ".tmp", ".tmp2", ".pytest-tmp", ".pytest_cache", ".sandbox", "_acl_recovery",
    "vendor_broken", "vendor", "data", "logs", "evidence", "交付材料", "__pycache__",
}
EXCLUDE_NAMES = {".env", "pytest-cache-files-12gaz4dz"}
EXCLUDE_PREFIXES = ("pytest-cache-files-", "~$")


def safe_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    io.open(path, "w", encoding="utf-8").write(text)


def copy(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def build_source_zip(target: Path):
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith(".")]
            for name in filenames:
                if name in EXCLUDE_NAMES or name.startswith(EXCLUDE_PREFIXES):
                    continue
                if name.startswith("_") and not name.startswith("__") and name.endswith(".py"):
                    continue
                full = Path(dirpath) / name
                rel = full.relative_to(ROOT)
                if rel.parts and rel.parts[0] in EXCLUDE_DIRS:
                    continue
                try:
                    zf.write(full, rel.as_posix())
                except OSError:
                    continue


def set_font(run, size=10.5, bold=False, color=None):
    run.font.name = "宋体"
    run.font.size = Pt(size)
    run.bold = bold
    if color is not None:
        run.font.color.rgb = color
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.append(rFonts)
    rFonts.set(qn("w:eastAsia"), "宋体")


def docx_title(doc, title, subtitle=""):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_font(p.add_run(title), size=18, bold=True, color=ACCENT)
    if subtitle:
        p2 = doc.add_paragraph()
        p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        set_font(p2.add_run(subtitle), size=11, color=RGBColor(0x59, 0x59, 0x59))


def docx_table(doc, header, rows, widths=None):
    table = doc.add_table(rows=1 + len(rows), cols=len(header))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for j, text in enumerate(header):
        cell = table.cell(0, j)
        cell.text = ""
        run = cell.paragraphs[0].add_run(text)
        set_font(run, size=9, bold=True, color=ACCENT)
        tcPr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:fill"), "DEEAF6")
        tcPr.append(shd)
    for i, row in enumerate(rows, start=1):
        for j, text in enumerate(row):
            cell = table.cell(i, j)
            cell.text = ""
            set_font(cell.paragraphs[0].add_run(str(text)), size=9)
    if widths:
        for j, w in enumerate(widths):
            for row in table.rows:
                row.cells[j].width = Cm(w)
    return table


def make_form7(path: Path):
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(1.8)
    docx_title(doc, "小组分工与开题登记表（表7）")
    p = doc.add_paragraph()
    set_font(p.add_run("小组编号：__________    业务方向：印制参数校验与成本测算（印制）"), size=10)
    p = doc.add_paragraph()
    set_font(p.add_run("项目名称：印制参数校验与成本测算 AI 系统    模型及接入：DeepSeek（OpenAI 兼容，deepseek-flash）"), size=10)
    p = doc.add_paragraph()
    set_font(p.add_run("项目仓库或交付位置：https://github.com/Mature-Vinegar-game/AIPublishSystem"), size=10)
    docx_table(
        doc,
        ["姓名", "主要职责", "承担任务与成果", "配合对象"],
        [
            ["成员A（组长）", "需求协调 + AI 功能/Agent 工具链", "Agent 编排、4 工具/3 类、真实调用与记录；需求与三张图", "成员B、成员C、成员E"],
            ["成员B", "后端 / 数据与权限 / 部署", "10 表、24 接口、权限、版本与审核、start.bat/README", "成员A、成员C、成员D"],
            ["成员C", "前端（页面/交互/可演示性）", "7 个页面、AI 轮询、执行记录与结果依据展示", "成员B、成员A、成员E"],
            ["成员D", "数据整理", "纸张/工艺/装订/费率样例数据、规则库、3 类运行记录", "成员A、成员E"],
            ["成员E", "测试交付", "12 条测试用例、缺陷与回归、证据与材料装订、录屏", "成员B、成员C、成员A"],
            ["（5 人组填前 5 行；6 人组可增行）", "", "", ""],
        ],
        widths=[3.0, 4.2, 7.6, 3.4],
    )
    doc.save(str(path))


def make_form8(path: Path):
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(1.8)
    docx_title(doc, "小组成果与教师评分（表8 / 表9）")
    p = doc.add_paragraph()
    set_font(p.add_run("小组编号：__________    项目名称：印制参数校验与成本测算 AI 系统"), size=10)
    docx_table(
        doc,
        ["小组成果", "交付位置", "验证方式与结果"],
        [
            ["可运行程序及源码", "交付材料/01_可运行程序及源码/", "按 README 启动；真实模式运行一次 AI 测算并查看执行记录"],
            ["需求与设计说明（3-5 页 + 三张图）", "交付材料/02_需求与设计说明/", "三张图 HTML 演示；图-程序对照表逐行核查"],
            ["数据与运行记录（3 类脱敏）", "交付材料/03_数据与运行记录/", "正常完成/数据不足/调用失败各 1 条，task_id 可在系统内查"],
            ["测试与交付材料", "交付材料/04_测试与交付材料/", "12 条用例 12 PASS；缺陷 7 条已关闭；见缺陷与回归记录"],
            ["小组分工与开发记录", "交付材料/05_小组分工与开发记录/", "分工表与开发记录、AI 辅助开发说明"],
        ],
        widths=[5.2, 6.4, 6.6],
    )
    p = doc.add_paragraph()
    set_font(p.add_run("主要问题及处理过程："), size=10, bold=True)
    p = doc.add_paragraph()
    set_font(p.add_run("真实模型首轮工具调用超过原限额、故障注入未终止任务、检索空结果结构异常、资料启停缺失、依赖部署目录不可读；均已修复并回归（BUG-001~007，见 04 材料）。"), size=9.5)
    p = doc.add_paragraph()
    set_font(p.add_run("AI 辅助开发的使用及验证："), size=10, bold=True)
    p = doc.add_paragraph()
    set_font(p.add_run("使用 AI 辅助完成需求拆解、代码实现、文档撰写与测试设计；所有 AI 产出均经本地运行验证（pytest 19/19、真实调用 12/12 用例、人工复算 14638.00/4.88），未直接提交其他组成果。"), size=9.5)
    docx_table(
        doc,
        ["业务（30）", "AI（30）", "工程（15）", "测试（15）", "交付（10）", "总分（100）"],
        [["", "", "", "", "", ""]],
        widths=[2.6, 2.6, 2.6, 2.6, 2.6, 2.8],
    )
    p = doc.add_paragraph()
    set_font(p.add_run("评分依据或需补验项目：____________________________________________"), size=9.5)
    p = doc.add_paragraph()
    set_font(p.add_run("评定人：____________    日期：____________"), size=9.5)
    doc.save(str(path))


def main():
    if DELIVERY.exists():
        shutil.rmtree(DELIVERY)
    DELIVERY.mkdir(parents=True, exist_ok=True)

    # 01 源码
    src_dir = DELIVERY / "01_可运行程序及源码"
    src_dir.mkdir(parents=True, exist_ok=True)
    build_source_zip(src_dir / "出版AI系统_源码_20261101.zip")
    copy(ROOT / "README.md", src_dir / "README_运行说明.md")
    copy(ROOT / "requirements.txt", src_dir / "requirements.txt")
    copy(ROOT / ".env.example", src_dir / ".env.example")
    copy(ROOT / "start.bat", src_dir / "start.bat")

    # 02 需求与设计说明
    d02 = DELIVERY / "02_需求与设计说明"
    d02.mkdir(parents=True, exist_ok=True)
    copy(DOCS / "需求与设计说明.docx", d02 / "需求与设计说明.docx")
    copy(DOCS / "三张图演示.html", d02 / "三张图演示.html")
    copy(DOCS / "01_需求与设计说明.md", d02 / "需求与设计说明.md")

    # 03 数据与运行记录
    d03 = DELIVERY / "03_数据与运行记录"
    d03.mkdir(parents=True, exist_ok=True)
    copy(DOCS / "05_数据字典.md", d03 / "数据字典.md")
    copy(DOCS / "11_运行记录_三类脱敏.md", d03 / "运行记录_三类脱敏.md")
    ev_dir = d03 / "原始证据"
    ev_dir.mkdir(parents=True, exist_ok=True)
    for p in sorted(EVID.glob("*.md")) + sorted(EVID.glob("*.json")):
        copy(p, ev_dir / p.name)

    # 04 测试与交付材料
    d04 = DELIVERY / "04_测试与交付材料"
    d04.mkdir(parents=True, exist_ok=True)
    copy(DOCS / "06_测试用例.md", d04 / "测试用例.md")
    copy(DOCS / "07_缺陷与回归记录.md", d04 / "缺陷与回归记录.md")
    copy(DOCS / "09_演示PPT大纲.md", d04 / "演示PPT大纲.md")
    copy(DOCS / "10_录屏脚本.md", d04 / "录屏脚本.md")
    copy(DOCS / "12_验收自检报告.md", d04 / "验收自检报告.md")
    copy(DOCS / "14_现场验收彩排记录.md", d04 / "现场验收彩排记录.md")
    copy(DOCS / "15_UI冒烟测试记录.md", d04 / "UI冒烟测试记录.md")
    copy(DOCS / "17_方案模式说明.md", d04 / "方案模式说明.md")
    copy(DOCS / "18_界面与仪表盘说明.md", d04 / "界面与仪表盘说明.md")
    safe_write(d04 / "未解决问题清单.md", """# 未解决问题清单

> 对应 [原文 L81] 提交物④；与 07 缺陷台账、PPT 风险页口径一致。截至 2026-11-01 22:10。

## 1 未关闭缺陷
当前 **0 条**（BUG-001~BUG-007 均已修复并完成回归，详见 `缺陷与回归记录.md`）。

## 2 待用户/现场补充事项（不影响系统功能）
| # | 事项 | 影响范围 | 处理方式 | 责任人 |
|---|---|---|---|---|
| 1 | 小组真实姓名/学号、组号 | 分工表（表7）、材料封面 | 交付前替换"成员A-E"占位 | 组长 |
| 2 | 教师提交渠道与截止时间 | 提交动作 | 11/2 前向教师确认；11/5 材料冻结备齐 | 组长 |
| 3 | 现场演示录屏（备用） | 演示材料 | 已自动生成备用录屏（104s，含真实 AI 运行与字幕）；可替换为人工旁白版 | 演示人 |
| 4 | 演示 PPT | 演示材料 | 按 `演示PPT大纲.md` 10 页制作（用户要求暂不做） | 组长 |
| 5 | 演示环境（若换电脑） | 启动 | 按 README 安装依赖；提前一天彩排 | 后端 |

## 3 已知限制（演示时如实说明）
- 教学模拟数据（纸张/工艺/装订/费率/书目），不用于真实报价；正式使用需替换当期工价单。
- 成本口径为演示口径：彩页四色整体计价、覆膜按千册计价，实际合同可能按面积/起步价结算。
- SQLite 单 worker 演示场景；若并发多人同时写需换 MySQL（只改 DATABASE_URL，需提前验证）。
- 单次真实 AI 运行约 10-20 秒，受网络与模型服务影响；已准备失败重试与备用录屏方案。
""")

    # 演示运行手册
    safe_write(d04 / "演示运行手册.md", """# 现场演示运行手册（11/6）

> 目标：5 分钟内展示「真实工具调用 + 执行记录 + 结果依据 + 人工确认与版本 + 异常处理」。
> 环境：本机 Windows，`start.bat` 启动；浏览器 127.0.0.1:8000；`LLM_MODE=real`（系统信息页自证）。

## 0 演示前 10 分钟
- [ ] 双击 `start.bat`，确认预检通过、种子数据完成、服务启动
- [ ] 打开 http://127.0.0.1:8000/system ，确认 `llm_mode=real`、`key_configured` 已配置、限额 10/8/30s/3000ms/120s
- [ ] 登录 `operator/123456`；另开无痕窗口登录 `reviewer/123456`
- [ ] 准备备用：本机真实运行录屏、手机热点、备用 API Key（仅本机 .env）

## 1 演示路线（5 分钟基准）
| 时间 | 动作 | 期望证据 |
|---|---|---|
| 0:00-0:20 | 打开 `/system`，展示 real + 模型名 + 限额 | 真实模式自证 |
| 0:20-2:00 | 打开已完成任务 `BT-DEMO-0001` → 看 AI 运行区计数与执行记录（9 次调用/3 类工具） | 工具名/参数/来源 ID/状态/耗时 |
| 2:00-2:40 | 点结果依据（PAPER-001/RATE-001/BIND-001/PROC-001）→ 资料/规则详情 | 依据三跳可回溯 |
| 2:40-3:40 | 新建任务（16开/12印张/3000册/胶版纸80g/胶装/四色/覆膜）→ 运行 AI → 得到 14638.00/4.88 | 真实模型新运行 |
| 3:40-4:20 | 采用 AI 结果 v1 → 人工修改保存 v2 → 提交审核 → 审核员通过 | 版本列表 + 审核记录 |
| 4:20-4:50 | 演示异常：资料库停用 PAPER-001 → 提交旧结果 → 409 BASIS_MISMATCH；恢复启用 | 依据失效拦截 |
| 4:50-5:00 | 执行记录/历史查询；说明数据不足与失败重试场景（可切到预置任务） | task_id 全链路 |

## 2 异常与备用
- 模型网络失败：切到"失败演示"任务（fault=tool_timeout 预置）展示失败态 + 重试；同时说明备用录屏。
- 数据不足：任务参数改为不存在的纸张 → 结果列出 missing_data、无金额输出。
- 越权：reviewer 访问"运行 AI"按钮不可见；直接 curl 接口返回 403（演示包内有请求卡）。
- 如需重置演示数据：`python scripts/reset_demo.py --yes` 后 `python seed/seed_demo.py`（先备份 data/app.db）。

## 3 演示后
- [ ] 材料按 `00_材料索引与提交自检.md` 提交（渠道以教师通知为准）
- [ ] 保留运行日志 `logs/app.log` 与页面截图/录屏

## 4 预置演示任务（2026-11-01 已创建，任务列表可搜索标题）

| 任务 | 任务编号 / id | 状态 | 用途 |
|---|---|---|---|
| 已完成全流程 | `BT-DEMO-0001` / 1 | COMPLETED（v1 AI + v2 MANUAL + 审核通过） | 结果/依据/执行记录/两版本/审核记录 |
| 演示-基线待运行 | `BT20261101-0033` / 34 | PENDING（未运行） | 现场运行真实 AI（预期 14638.00 / 4.88） |
| 演示-数据不足 | `BT20261101-0034` / 35 | PENDING（已预跑 SUCCEEDED 备份） | 缺失依据、不虚构金额 |
| 演示-失败重试 | `BT20261101-0035` / 36 | PENDING（已预跑 FAILED / TOOL_TIMEOUT 备份） | 失败态 + 现场重试成功 |

> 演示前可执行 `python scripts/demo_check.py` 一键自检（21 项：真实模式/Key/限额/演示任务/交付材料）。
> 演示数据恢复：停服后把 `data/demo_snapshot_20261101.db` 复制为 `data/app.db`（同时删除 `-wal/-shm`），再启动即回到 2026-11-01 演示状态。
""")

    rec = ROOT / "evidence" / "recordings" / "备用录屏_系统演示.mp4"
    if rec.exists():
        copy(rec, d04 / "备用录屏_系统演示.mp4")

    ev04 = d04 / "测试执行证据"
    ev04.mkdir(parents=True, exist_ok=True)
    for p in sorted(EVID.glob("test_execution_*.md")) + sorted(EVID.glob("test_execution_*.json")):
        copy(p, ev04 / p.name)

    # 05 分工与开发记录
    d05 = DELIVERY / "05_小组分工与开发记录"
    d05.mkdir(parents=True, exist_ok=True)
    copy(DOCS / "08_分工与开发记录.md", d05 / "分工与开发记录.md")
    make_form7(d05 / "表7_小组分工与开题登记表.docx")
    make_form8(d05 / "表8_小组成果与教师评分.docx")

    # 00 索引
    safe_write(DELIVERY / "00_材料索引与提交自检.md", """# 提交材料索引与自检（v1.1，2026-11-01）

> 对应任务书 [原文 L77-83]「每组提交」5 项材料；提交渠道与截止时间**以教师通知为准**。
> 建议提交方式：整个 `交付材料/` 目录压缩为 `组号_出版AI系统_最终提交_YYYYMMDD.zip`（不含 `.env`、`data/*.db`、`logs/`）。

## 一、5 项提交物对照
| # | 提交物 [原文] | 本目录位置 | 内容与规格 | 对应评分 [表6] | 状态 |
|---|---|---|---|---|---|
| ① | 可运行程序及源码 [L78] | `01_可运行程序及源码/` | 源码 zip + README + requirements + start.bat + .env.example；密钥走环境变量 | 交付 5、业务 15、工程 15 | 就绪 |
| ② | 需求与设计说明 3-5 页 + 三张图 [L79] | `02_需求与设计说明/` | 需求与设计说明.docx（内嵌三图）；三张图演示.html（离线可投屏）；图示与程序一致 | 业务 5、交付 5 | 就绪 |
| ③ | 数据与运行记录 [L80] | `03_数据与运行记录/` | 数据字典；正常完成/数据不足/调用失败 3 类脱敏运行记录；原始证据 JSON/MD | AI 30、测试 5 | 就绪 |
| ④ | 测试与交付材料 [L81] | `04_测试与交付材料/` | 12 条用例（9 类覆盖）12 PASS；缺陷 7 条已关闭+回归；未解决问题清单；演示 PPT 大纲/录屏脚本/演示手册/备用录屏 mp4/方案模式说明/测试证据 | 测试 15、交付 5 | 就绪（PPT/录屏待用户补） |
| ⑤ | 小组分工与开发记录 [L82] | `05_小组分工与开发记录/` | 分工与开发记录 + 表7 开题登记 + 表8 成果与评分；AI 辅助开发说明 | 交付 5 | 就绪（姓名待填） |

## 二、提交前自检（逐项勾选）
- [x] 源码可在本机按 README 冷启动（已实测：干净目录 mock 启动通过；**真实模式冷启动**完成真实 AI 测算 14638.00/4.88；证据 `evidence/coldstart_20261101.md`、`coldstart_real_20261101.md`；BUG-006/007 已修复）
- [x] 真实调用通过（deepseek-flash；9 次工具/3 类；14638.00/4.88）
- [x] 6 项必做功能可运行：角色权限 / 任务 / 资料数据 / AI 结果 / 执行记录 / 人工确认与版本
- [x] ≥10 用例覆盖 9 类场景：12/12 PASS（证据在 04）
- [x] 至少 2 个可查版本、审核退回/通过、越权后端拒绝
- [x] 3 类脱敏运行记录齐（03）
- [x] 三张图 + 图-程序对照；演示 HTML 离线可用
- [x] 验收自检报告（04/验收自检报告.md）+ 一键自检 `python scripts/demo_check.py`（21/21 通过）
- [x] README 含依赖/初始化/运行/模型切换说明
- [x] 密钥不入源码；`.env`/`data/`/`logs/` 未打包
- [ ] 小组真实姓名/学号/组号（待替换）
- [ ] 演示 PPT 与备用录屏（用户确认是否制作）
- [ ] 教师提交渠道与截止确认（11/2 前）

## 三、一键启动（提交包内）
```bat
cd 01_可运行程序及源码
:: 解压 出版AI系统_源码_20261101.zip 到任意目录后：
python -m pip install -r requirements.txt
copy .env.example .env   :: 填写 LLM_API_KEY / LLM_MODEL
start.bat
```
默认地址 <http://127.0.0.1:8000>；演示账号 operator/123456、reviewer/123456。

## 四、联系方式与仓库
- 仓库：https://github.com/Mature-Vinegar-game/AIPublishSystem
- 版本：v1.1（2026-11-01）；Git 提交：见仓库 `git log`
""")

    # 顶层 README
    safe_write(DELIVERY / "README_提交说明.md", """# 出版业务 AI 系统 · 提交材料包

业务方向：**印制参数校验与成本测算**（任务书 7 个可选方向之一）
小组：第 ____ 组（成员A-E，姓名学号待补）
版本：v1.1 · 2026-11-01
仓库：https://github.com/Mature-Vinegar-game/AIPublishSystem

## 目录
- `00_材料索引与提交自检.md` ← 先看这个（5 项材料对照 + 提交前勾选）
- `01_可运行程序及源码/` 源码 zip、README、依赖清单、启动脚本
- `02_需求与设计说明/` 设计说明 Word（内嵌三图）、三张图 HTML 演示版
- `03_数据与运行记录/` 数据字典、3 类脱敏运行记录、原始证据
- `04_测试与交付材料/` 测试用例、缺陷与回归、未解决问题清单、演示手册、录屏脚本、PPT 大纲
- `05_小组分工与开发记录/` 分工记录、表7、表8

## 快速验证（3 分钟）
1. 解压 `01_.../出版AI系统_源码_20261101.zip`；
2. `python -m pip install -r requirements.txt`；
3. 复制 `.env.example` 为 `.env` 并填 `LLM_API_KEY`（模型默认 `deepseek-chat`，本机实测用 `deepseek-flash`）；
4. 双击 `start.bat` → 打开 <http://127.0.0.1:8000>；
5. 登录 operator/123456 → 打开 `BT-DEMO-0001` → 运行 AI 测算 → 查看执行记录/结果依据/版本。

> 注意：本材料包不含 `.env`、数据库、日志与任何真实密钥；演示数据均为教学模拟数据。
""")

    # 文件校验值（SHA256SUMS.txt，供提交前完整性核对）
    sha_lines = []
    for p in sorted(DELIVERY.rglob("*")):
        if p.is_file() and p.name != "SHA256SUMS.txt":
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            sha_lines.append(f"{h}  {p.relative_to(DELIVERY).as_posix()}")
    (DELIVERY / "SHA256SUMS.txt").write_text("\n".join(sha_lines) + "\n", encoding="utf-8")

    # 统计
    total_files = sum(1 for _ in DELIVERY.rglob("*") if _.is_file())
    total_size = sum(p.stat().st_size for p in DELIVERY.rglob("*") if p.is_file())
    print(f"delivery ready: {DELIVERY}")
    print(f"files: {total_files}, size: {total_size/1024/1024:.2f} MB")
    for top in sorted(DELIVERY.iterdir()):
        kind = "DIR " if top.is_dir() else "FILE"
        print(f"  {kind} {top.name}")


if __name__ == "__main__":
    main()
