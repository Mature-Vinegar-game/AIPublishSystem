# -*- coding: utf-8 -*-
"""修复：任务详情“编辑参数”表单的装订/工艺/开本下拉是硬编码，导致新增装订（锁线胶装/线圈装）无法选择。"""
from pathlib import Path

ROOT = Path(r"F:\deepseek workarea\出版AI系统")


def patch_text(path: Path, old: str, new: str):
    text = path.read_text(encoding="utf-8")
    if new in text:
        print("skip:", path.name)
        return
    if old not in text:
        raise SystemExit("anchor not found in " + str(path))
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("patched:", path.name)


# 1) main.py：详情页上下文带上资料库驱动下拉选项
main = ROOT / "app" / "main.py"
text = main.read_text(encoding="utf-8")
anchor = "def _task_new_context(user: Optional[dict], db: Session, **extra) -> dict:"
helper = '''def _form_options(db: Session) -> dict:
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


def _task_new_context(user: Optional[dict], db: Session, **extra) -> dict:'''
if helper not in text:
    text = text.replace(anchor, helper, 1)
    main.write_text(text, encoding="utf-8")
    print("patched main.py: _form_options")
else:
    print("skip main.py helper")

text = main.read_text(encoding="utf-8")
old_ctx = """    versions=version_service.list_versions(db, task.id),
    reviews=task_service.reviews_for_task(db, task.id),
    **_permissions(user, task),
    )"""
new_ctx = """    versions=version_service.list_versions(db, task.id),
    reviews=task_service.reviews_for_task(db, task.id),
    **_permissions(user, task),
    **_form_options(db),
    )"""
if "**_form_options(db)," not in text:
    if old_ctx not in text:
        raise SystemExit("detail context anchor not found")
    text = text.replace(old_ctx, new_ctx, 1)
    main.write_text(text, encoding="utf-8")
    print("patched main.py: detail context options")
else:
    print("skip main.py detail context")

# 2) task_detail.html：动态选项 + 兜底
tpl = ROOT / "app" / "templates" / "task_detail.html"
t = tpl.read_text(encoding="utf-8")
if "{% set binding_opts = binding_options" not in t:
    anchor2 = "{% set sm = business_status.get(tstatus, ('未知', 'badge-gray')) %}"
    opts = """{% set binding_opts = binding_options if binding_options is defined and binding_options else ['胶装', '骑马订', '精装'] %}
{% set process_opts = (['无'] + process_options) if process_options is defined and process_options else ['无', '覆膜', 'UV', '烫金'] %}
{% set format_opts = formats if formats is defined and formats else ['16开', '32开', '大32开'] %}
"""
    t = t.replace(anchor2, opts + anchor2, 1)
    tpl.write_text(t, encoding="utf-8")
    print("patched task_detail.html: option vars")

t = tpl.read_text(encoding="utf-8")
repls = [
    ("{% for opt in ['16开', '32开', '大32开'] %}", "{% for opt in format_opts %}"),
    ("{% for opt in ['胶装', '骑马订', '精装'] %}", "{% for opt in binding_opts %}"),
    ("{% for opt in ['无', '覆膜', 'UV', '烫金'] %}", "{% for opt in process_opts %}"),
]
for old, new in repls:
    if old in t:
        t = t.replace(old, new, 1)
        print("patched loop:", new[:40])
tpl.write_text(t, encoding="utf-8")
print("done")
