# -*- coding: utf-8 -*-
"""生成《需求与设计说明.docx》（3-5 页正文 + 三张图），使用 bundled python-docx。"""
import io
import os
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(r"F:\deepseek workarea\出版AI系统")
SHOT = ROOT / "evidence" / "screenshots"
OUT = ROOT / "docs" / "需求与设计说明.docx"

ACCENT = RGBColor(0x1F, 0x4E, 0x79)
INK = RGBColor(0x22, 0x22, 0x22)


def set_run(run, name="宋体", size=10.5, bold=False, color=INK, mono=False):
    run.font.name = "Consolas" if mono else name
    run.font.size = Pt(size)
    run.bold = bold
    run.font.color.rgb = color
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.append(rFonts)
    rFonts.set(qn("w:eastAsia"), "微软雅黑" if not mono else "Consolas")
    rFonts.set(qn("w:ascii"), "Consolas" if mono else name)
    rFonts.set(qn("w:hAnsi"), "Consolas" if mono else name)


def para(doc, text="", size=10.5, bold=False, color=INK, align=None, space_after=4, indent=None, mono=False):
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.line_spacing = 1.3
    if indent is not None:
        p.paragraph_format.first_line_indent = Cm(indent)
    if text:
        run = p.add_run(text)
        set_run(run, size=size, bold=bold, color=color, mono=mono)
    return p


def heading(doc, text, level=1):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10 if level == 1 else 6)
    p.paragraph_format.space_after = Pt(4)
    run = p.add_run(text)
    if level == 1:
        set_run(run, size=14, bold=True, color=ACCENT)
        pPr = p._p.get_or_add_pPr()
        pBdr = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), "6")
        bottom.set(qn("w:space"), "2")
        bottom.set(qn("w:color"), "1F4E79")
        pBdr.append(bottom)
        pPr.append(pBdr)
    else:
        set_run(run, size=11.5, bold=True, color=RGBColor(0x2E, 0x74, 0xB5))
    return p


def bullet(doc, text, size=10.5):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.6)
    p.paragraph_format.first_line_indent = Cm(-0.4)
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.line_spacing = 1.25
    run = p.add_run("• " + text)
    set_run(run, size=size)
    return p


def add_table(doc, header, rows, widths=None):
    table = doc.add_table(rows=1 + len(rows), cols=len(header))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for j, text in enumerate(header):
        cell = table.cell(0, j)
        cell.text = ""
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(text)
        set_run(run, size=9, bold=True, color=ACCENT)
        tcPr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:fill"), "DEEAF6")
        tcPr.append(shd)
    for i, row in enumerate(rows, start=1):
        for j, text in enumerate(row):
            cell = table.cell(i, j)
            cell.text = ""
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(1)
            run = p.add_run(str(text))
            set_run(run, size=9)
    if widths:
        for j, w in enumerate(widths):
            for row in table.rows:
                row.cells[j].width = Cm(w)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return table


def add_image(doc, path, caption, width_cm=16.2):
    if not path.exists():
        para(doc, f"（图片缺失：{path.name}）", size=9, color=RGBColor(0xB4, 0x23, 0x18))
        return
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(2)
    p.add_run().add_picture(str(path), width=Cm(width_cm))
    cap = doc.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap.paragraph_format.space_after = Pt(6)
    run = cap.add_run(caption)
    set_run(run, size=9, color=RGBColor(0x59, 0x59, 0x59))


def footer_page_number(section):
    footer = section.footer
    p = footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("第 ")
    set_run(run, size=8, color=RGBColor(0x59, 0x59, 0x59))
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    p._p.append(fld)
    run2 = p.add_run(" 页 / 共 ")
    set_run(run2, size=8, color=RGBColor(0x59, 0x59, 0x59))
    fld2 = OxmlElement("w:fldSimple")
    fld2.set(qn("w:instr"), "NUMPAGES")
    p._p.append(fld2)
    run3 = p.add_run(" 页")
    set_run(run3, size=8, color=RGBColor(0x59, 0x59, 0x59))
    p2 = footer.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p2.add_run("印制参数校验与成本测算 AI 系统 · 需求与设计说明")
    set_run(r, size=8, color=RGBColor(0x8A, 0x8A, 0x8A))


def main():
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21.0), Cm(29.7)
    section.left_margin = section.right_margin = Cm(2.2)
    section.top_margin = Cm(2.0)
    section.bottom_margin = Cm(1.8)
    normal = doc.styles["Normal"]
    normal.font.name = "宋体"
    normal.font.size = Pt(10.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    footer_page_number(section)

    # 标题区
    para(doc, "印制参数校验与成本测算 AI 系统", size=22, bold=True, color=ACCENT,
         align=WD_ALIGN_PARAGRAPH.CENTER, space_after=2)
    para(doc, "需求与设计说明（v1.3 · 最终版）", size=13, bold=True, color=RGBColor(0x2E, 0x74, 0xB5),
         align=WD_ALIGN_PARAGRAPH.CENTER, space_after=10)
    add_table(
        doc,
        ["项目信息", "内容"],
        [
            ["业务方向", "印制参数校验与成本测算（任务书 7 个可选方向之一：印制）"],
            ["小组信息", "第 ____ 组：娄长春（Agent 编排）、赵飞翔（前端页面）、佘轩辰（后端）；学号以表7为准"],
            ["模型及接入", "DeepSeek API（OpenAI 兼容协议），模型 deepseek-flash，密钥经环境变量注入"],
            ["技术栈", "Python 3.14 + FastAPI + SQLAlchemy + SQLite(WAL) + Jinja2 服务端模板；单进程单 worker，无 Node/无前端构建"],
            ["版本与日期", "v1.3（2026-11-05 最终版）；对应任务书 2026 年 10 月版"],
            ["图示", "三张图 HTML 演示版：docs/三张图演示.html；系统运行后可访问 /diagrams"],
        ],
        widths=[3.6, 13.6],
    )

    heading(doc, "1 业务场景与目标")
    para(doc, "出版社在印制环节需要根据图书规格（开本、印张、印数、纸张、装订、工艺、色数）同时完成两件事：一是校验参数是否符合印制规则，二是依据纸张/工艺/装订/费率报价测算印制成本。传统做法依赖人工翻报价单、套公式，易漏项、难追溯、难复核。", indent=0.74)
    para(doc, "本系统把该业务做成一个可运行的 AI 应用：操作人员创建印制成本测算任务并填写参数；系统调用业务工具检索资料库中的报价与规则、校验参数、按统一公式计算成本；模型基于本次真实工具结果生成可追溯的测算说明与依据；结果经人工修改/确认、提交审核、版本留痕后可随时按任务 ID 查询历史。", indent=0.74)
    para(doc, "对应任务书要求：业务主流程 7 环节 [原文 L23]；表 2 六项必做功能；不要求覆盖出版全流程、不做真实订单/设备对接 [原文 L3]。", size=9.5, color=RGBColor(0x59, 0x59, 0x59))

    heading(doc, "2 角色与权限")
    add_table(
        doc,
        ["角色（按场景命名）", "账号", "允许操作", "明确不允许"],
        [
            ["业务操作人员（印制专员）", "operator", "创建/编辑本人任务、关联资料、运行 AI 测算、保存人工版本、提交审核、查询历史", "审核任务；操作他人任务"],
            ["审核 / 确认人员（成本审核员）", "reviewer", "查看待审/已审任务，审核通过或退回（退回必须写意见）", "创建任务、运行 AI（后端 403）"],
        ],
        widths=[4.6, 2.4, 6.6, 4.2],
    )
    para(doc, "权限校验全部在后端逐接口执行：角色不足 → 403；对象不属于本人/不在审核范围 → 404；状态冲突/重复提交/版本冻结 → 409；超限 → 429。前端隐藏按钮不构成权限控制。", size=9.5)

    heading(doc, "3 业务流程（7 环节）")
    add_table(
        doc,
        ["#", "环节 [原文 L23]", "系统实现"],
        [
            ["1", "创建任务", "任务列表 → 新建任务；填写目标与关键参数（必填校验）"],
            ["2", "填写需求 / 导入数据", "表单录入参数；资料库/规则库提供样例数据（含 ID、来源、版本），任务可关联资料"],
            ["3", "调用工具处理", "Agent 由模型自主选择工具与参数：检索报价/规则 → 参数校验 → 成本计算；后端校验并真实执行"],
            ["4", "查看结果和依据", "结果页展示测算说明、成本明细与逐条依据；依据三跳可回溯到资料/规则/执行记录"],
            ["5", "人工修改或确认", "可编辑结果并保存人工版本；AI 重跑只新增结果、不覆盖人工版本"],
            ["6", "审核", "提交审核 → 审核员通过（COMPLETED）或退回（REJECTED，需意见）→ 可修改后重新提交"],
            ["7", "查询历史记录", "按 task_id 查询任务、AI 运行、全部工具调用记录、结果、全部版本与审核记录"],
        ],
        widths=[1.0, 4.2, 12.0],
    )

    heading(doc, "4 成本口径与算例")
    para(doc, "系统内置示例成本口径（可在资料库/规则库调整，均由带来源的数据驱动）：", size=10.5)
    for line in [
        "用纸量（令）= ceil(印数 × 印张 ÷ 1000 × 1.05)；纸张费 = 用纸量 × 纸张单价（元/令）",
        "印刷费 = 印数 ÷ 1000 × 印张 × 单色千印张价 × 色数档（彩色页数 > 0 取 4，否则取 1）",
        "制版费 = 色数档 × 80 元/色；装订费 = 印数 ÷ 1000 × 装订单价；工艺费 = 印数 ÷ 1000 × 工艺单价",
        "总成本 = 纸张费 + 印刷费 + 制版费 + 装订费 + 工艺费；单册成本 = 总成本 ÷ 印数（四舍五入 2 位）",
    ]:
        bullet(doc, line)
    para(doc, "算例（示例任务 BT-DEMO-0001）：16 开 / 12 印张 / 3000 册 / 胶版纸 80g（265 元/令）/ 胶装 / 四色 / 覆膜 → 纸张费 10070.00 + 印刷费 3168.00 + 制版费 320.00 + 装订费 540.00 + 工艺费 540.00 = 总成本 14638.00 元，单册 4.88 元。该结果与真实调用（模型 deepseek-flash）的 AI 运行结果一致，且明细 line_items 均带可复算公式。", size=10)

    heading(doc, "5 系统结构（与程序一致）")
    add_table(
        doc,
        ["层", "内容", "程序对应"],
        [
            ["数据层", "SQLite 单文件，10 张表：users、business_tasks、materials、task_materials、rules、ai_tasks、tool_call_records、ai_results、result_versions、review_records", "app/models.py、app/db.py"],
            ["接口层", "25 个 REST 接口（统一 /api）：认证 2、任务 9、AI 5、资料 4、规则 4、系统 1（含规则启停 PATCH）", "app/api/*.py"],
            ["Agent 层", "单 Agent 循环：模型自主选工具与参数，后端校验执行并回填；工具 4 个 / 3 类（方案模式另含 plan_candidates）；完成判定（≥2 类工具成功且依据非空）；调用限额与故障注入", "app/agent/{orchestrator,llm,tools,prompts}.py"],
            ["页面层", "Jinja2 服务端渲染 11 个页面（任务/方案模式/资料/规则/校验试算/效果评测/仪表盘/系统信息等）；原生 JS；无 CDN、无构建", "app/templates、app/static"],
        ],
        widths=[2.4, 10.4, 4.4],
    )
    para(doc, "工具清单：search_quotes（检索纸张/工艺/装订/费率）、get_rules（读取规则）、validate_spec（参数校验）、calculate_cost（成本计算）；方案模式另有 plan_candidates（候选方案枚举，最多 24 个候选）。每个工具返回统一信封（ok/data/source.ids/warnings/error_code），每次调用落 tool_call_records，同一 business_task_id 贯通。", size=9.5)
    para(doc, "限额（[原文 L49]，页面页脚与系统信息页展示）：工具 ≤10 次、模型 ≤8 轮、模型 30s、工具 3000ms、单次运行 ≤120s、单任务运行 ≤10 次。验收固定真实模型（real），mock 仅开发期并标注。", size=9.5)

    para(doc, "最终版增强（选做，不影响标准主流程）：方案模式（部分参数留白 + 预算生成 1–3 个方案）、运行仪表盘、校验试算、效果评测、版本对比、按审核意见重跑、工具执行详情、导出/打印、任务检索增强。以上功能均不改变 10 张表、标准任务状态机与审核流程。", size=9.5)

    heading(doc, "6 验收范围与明确不做")
    bullet(doc, "验收范围：完成所选业务（印制成本测算）的主要流程；6 项必做功能全部可运行；真实工具调用与执行记录可展示；人工确认与至少 2 个可查版本；≥10 条测试用例覆盖 9 类场景；结果依据可追溯。")
    bullet(doc, "明确不做 [原文 L36-37]：模型训练/微服务/多 Agent/OCR/复杂排版/真实设备与订单渠道对接；选做功能不另加分 [原文 L39]；模拟数据仅用于教学演示，不当真实业务数据 [原文 L61]。")

    heading(doc, "7 三张图")
    para(doc, "三张图按任务书要求 [原文 L79] 绘制，并与程序实际实现一致；HTML 演示版可离线打开（docs/三张图演示.html），系统运行后亦可访问 /diagrams。", size=10)
    add_image(doc, SHOT / "diagram_1.png", "图 1  系统功能图：角色 → 页面 → 功能模块 → 10 张表 / 25 个接口（基线架构；最终版增强见文字说明）")
    add_image(doc, SHOT / "diagram_2.png", "图 2  技术调用图：浏览器 → FastAPI 单进程（页面/API/服务/Agent/工具）→ 模型 → SQLite")
    add_image(doc, SHOT / "diagram_3.png", "图 3  数据处理与工具执行图：同一 business_task_id 贯穿数据链路与依据三跳")

    heading(doc, "8 数据、测试与部署摘要")
    bullet(doc, "数据：教学模拟数据；资料库 30 条（18 纸张 / 6 工艺 / 5 装订 / 1 费率）、规则库 22 条（20 spec + 1 data + 1 calc）；材料保留 material_no/source/version_no/enabled；3 类脱敏运行记录见 docs/11 与 evidence/。")
    bullet(doc, "测试：核心 TC01–TC12 共 12 条（覆盖 9 类场景）+ 最终版补充 TC13–TC18；核心 12/12 PASS；pytest 23/23、full_check 38/38；缺陷 8 条均已修复回归（docs/07）。")
    bullet(doc, "部署：Windows + Python 3.14；pip install -r requirements.txt（或离线 vendor）+ start.bat；单进程 :8000、单 worker；密钥仅存 .env，不入源码/仓库；无需 Node/Redis/Docker。")
    para(doc, "文档与程序一致性声明：本文所述页面、接口、表、限额与提交目录中的实际代码一致；三张图为 HTML/Markdown 双版本，节点均可在程序中点开核查。", size=9.5, color=RGBColor(0x59, 0x59, 0x59))

    doc.save(str(OUT))
    print("saved:", OUT, OUT.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
