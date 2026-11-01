# -*- coding: utf-8 -*-
"""补 PATCH /api/materials/{id} 到文档接口清单，并把 23→24 的残留口径修正。"""
import io
from pathlib import Path

ROOT = Path(r"F:\deepseek workarea\出版AI系统")
edits = {
    "BUILD_SPEC.md": [
        ("## 4. 后端接口（23 个，统一 `/api` 前缀；JSON）", "## 4. 后端接口（24 个，统一 `/api` 前缀；JSON）"),
        (
            "| 23 | GET /system/meta | — | app,version,db,llm_mode,model,key_configured,limits{...},server_time | 登录 |",
            "| 23 | GET /system/meta | — | app,version,db,llm_mode,model,key_configured,limits{...},server_time | 登录 |\n"
            "| 24 | PATCH /materials/{id} | enabled?,title?,source? | 更新后的资料 | operator（停用被引用资料后提交审核 → 409 BASIS_MISMATCH；T06 依据不一致演示） |",
        ),
    ],
    "README.md": [
        ("## 7. 接口清单（23 个，统一 `/api` 前缀）", "## 7. 接口清单（24 个，统一 `/api` 前缀）"),
        (
            "| 23 | `GET /api/system/meta` | 系统信息（模式/模型/Key 状态/限额） |",
            "| 23 | `GET /api/system/meta` | 系统信息（模式/模型/Key 状态/限额） |\n"
            "| 24 | `PATCH /api/materials/{id}` | 资料启用/停用/标题/来源（operator；停用被引用资料后提交审核触发依据失效 409） |",
        ),
    ],
    "docs/01_需求与设计说明.md": [
        (
            "| 资料 | `GET /materials`、`POST /materials`、`GET /materials/{id}` | 3 |",
            "| 资料 | `GET /materials`、`POST /materials`、`GET /materials/{id}`、`PATCH /materials/{id}`（启停/标题/来源） | 4 |",
        ),
    ],
    "docs/02_系统功能图.md": [
        ("## 2 模块 → 接口清单（23 个，按模块归口）", "## 2 模块 → 接口清单（24 个，按模块归口）"),
        (
            "| 5 | 资料库 `materials.html` | `GET /materials`、`POST /materials/create` | F3 资料与规则管理 | `GET/POST /api/materials`、`GET /api/materials/{id}` | `materials`、`task_materials` | ② 填写需求或导入数据 |",
            "| 5 | 资料库 `materials.html` | `GET /materials`、`POST /materials/create` | F3 资料与规则管理 | `GET/POST /api/materials`、`GET /api/materials/{id}`、`PATCH /api/materials/{id}`（启停） | `materials`、`task_materials` | ② 填写需求或导入数据 |",
        ),
        (
            "| F3 资料与规则 | `GET/POST /materials`、`GET /materials/{id}`、`GET/POST /rules`、`GET /rules/{id}` | 6 |",
            "| F3 资料与规则 | `GET/POST /materials`、`GET /materials/{id}`、`PATCH /materials/{id}`、`GET/POST /rules`、`GET /rules/{id}` | 7 |",
        ),
        (
            "| **合计** | 与 `BUILD_SPEC.md` §4 的 23 条逐条一致 | **24** |",
            "| **合计** | 与 `BUILD_SPEC.md` §4 的 24 条逐条一致 | **24** |",
        ),
    ],
    "docs/03_技术调用图.md": [
        ("不占 23 个编号【设计】", "不占 24 个编号【设计】"),
        ("| API 层 | `app/api/{auth,tasks,materials,rules,results,ai,system}.py` | `grep -rn \"@router\" app/api` 计 23 条 | 待核对 |",
         "| API 层 | `app/api/{auth,tasks,materials,rules,results,ai,system}.py` | `grep -rn \"@router\" app/api` 计 24 条 | 已核对（2026-11-01：24 条） |"),
    ],
    "docs/08_分工与开发记录.md": [
        ("（24 接口中的 18 个）", "（24 接口中的 19 个）"),
    ],
}

changed = []
for rel, pairs in edits.items():
    path = ROOT / rel
    text = io.open(path, encoding="utf-8").read()
    original = text
    for old, new in pairs:
        if old not in text:
            raise SystemExit(f"[MISS] {rel}: {old[:60]}")
        text = text.replace(old, new, 1)
    io.open(path, "w", encoding="utf-8").write(text)
    changed.append(rel)

print("updated:", changed)
