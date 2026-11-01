"""资料/报价库接口（BUILD_SPEC §4 #17-#19）。"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api import api_error, success
from app.auth import require_operator, require_user
from app.db import get_db, now_iso
from app.models import MATERIAL_TYPE_LABELS, MATERIAL_TYPES, Material, TaskMaterial
from app.schemas import MaterialCreateRequest
from app.services import basis_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/materials", tags=["资料库"])

TYPE_PREFIX = {"paper": "PAPER", "process": "PROC", "binding": "BIND", "rate": "RATE", "book": "BOOK"}


def _material_to_dict(material: Material, with_content: bool = True) -> dict:
    data = {
        "id": material.id,
        "material_no": material.material_no,
        "type": material.type,
        "type_label": MATERIAL_TYPE_LABELS.get(material.type, material.type),
        "title": material.title,
        "source": material.source,
        "version_no": material.version_no,
        "enabled": bool(int(material.enabled or 0)),
        "created_at": material.created_at,
    }
    if with_content:
        data["content"] = basis_service.parse_json(material.content_json, {})
    return data


def generate_material_no(db: Session, material_type: str) -> str:
    prefix = TYPE_PREFIX.get(material_type, "MAT")
    total = db.query(Material).filter(Material.material_no.like(f"{prefix}-%")).count()
    seq = total + 1
    while True:
        candidate = f"{prefix}-{seq:04d}"
        exists = db.query(Material.id).filter(Material.material_no == candidate).first()
        if not exists:
            return candidate
        seq += 1


@router.get("", summary="资料列表（登录）")
def list_materials(
    type: Optional[str] = Query(default=None, description="paper/process/binding/rate/book"),
    keyword: Optional[str] = Query(default=None, description="资料编号/标题/来源模糊搜索"),
    include_disabled: bool = Query(default=False),
    user: dict = Depends(require_user),
    db: Session = Depends(get_db),
):
    query = db.query(Material)
    type = (type or "").strip().lower()
    if type in ("all", "全部", "全部类型"):
        type = ""
    if type:
        if type not in MATERIAL_TYPES:
            api_error(400, f"type 非法，只能是 {', '.join(MATERIAL_TYPES)}")
        query = query.filter(Material.type == type)
    if not include_disabled:
        query = query.filter(Material.enabled == 1)
    keyword = (keyword or "").strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            or_(
                Material.material_no.like(like),
                Material.title.like(like),
                Material.source.like(like),
            )
        )
    rows = (
        query.order_by(Material.type.asc(), Material.material_no.asc(), Material.version_no.desc())
        .limit(500)
        .all()
    )
    return success({"items": [_material_to_dict(row) for row in rows], "total": len(rows)})


@router.post("", summary="新增资料/报价（operator）")
def create_material(
    payload: MaterialCreateRequest,
    user: dict = Depends(require_operator),
    db: Session = Depends(get_db),
):
    material_type = payload.type
    if material_type not in MATERIAL_TYPES:
        api_error(400, f"type 非法，只能是 {', '.join(MATERIAL_TYPES)}")
    content: Any = payload.content
    if isinstance(content, str):
        parsed = basis_service.parse_json(content, None)
        if not isinstance(parsed, (dict, list)):
            api_error(400, "content 必须是 JSON 对象（如 {\"price_per_ream\": 265}）")
        content = parsed
    if not isinstance(content, (dict, list)):
        api_error(400, "content 必须是 JSON 对象")

    material_no = (payload.material_no or "").strip()
    existing_rows = []
    if material_no:
        existing_rows = (
            db.query(Material)
            .filter(Material.material_no == material_no)
            .order_by(Material.version_no.desc())
            .all()
        )
    else:
        material_no = generate_material_no(db, material_type)

    if existing_rows:
        version_no = int(existing_rows[0].version_no or 0) + 1
    else:
        version_no = int(payload.version_no or 1)

    material = Material(
        material_no=material_no,
        type=material_type,
        title=payload.title.strip(),
        source=(payload.source or "手工录入").strip(),
        version_no=version_no,
        content_json=basis_service.dump_json(content, "{}"),
        enabled=1,
        created_at=now_iso(),
    )
    db.add(material)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        api_error(409, f"资料 {material_no} 版本 v{version_no} 已存在，请重试")
    db.refresh(material)
    logger.info(
        "新增资料 material_no=%s type=%s version=%s user=%s",
        material.material_no,
        material.type,
        material.version_no,
        user.get("username"),
    )
    return success(
        {
            "id": material.id,
            "material_no": material.material_no,
            "type": material.type,
            "version_no": material.version_no,
        }
    )


@router.get("/{material_id}", summary="资料详情 + 同编号版本列表（登录）")
def get_material(
    material_id: int,
    user: dict = Depends(require_user),
    db: Session = Depends(get_db),
):
    material = db.query(Material).filter(Material.id == int(material_id)).first()
    if material is None:
        api_error(404, "资料不存在")
    version_rows = (
        db.query(Material)
        .filter(Material.material_no == material.material_no)
        .order_by(Material.version_no.desc())
        .all()
    )
    linked_tasks = (
        db.query(TaskMaterial).filter(TaskMaterial.material_id == material.id).count()
    )
    data = _material_to_dict(material)
    data["versions"] = [_material_to_dict(row) for row in version_rows]
    data["linked_task_count"] = linked_tasks
    return success(data)
