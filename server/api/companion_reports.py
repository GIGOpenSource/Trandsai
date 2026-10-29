"""App 端：智能体对话举报 / 资料（背景图）举报 — 同一接口，进同一管理端列表。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.companion_access import require_login_user
from core.i18n import companion_not_found_message
from core.response import fail, ok
from services.companion_report import list_report_reasons, submit_companion_report

router = APIRouter(tags=["智能体举报"])


class CompanionReportRequest(BaseModel):
    reason_id: Optional[int] = Field(
        default=None,
        description="举报分类 id（来自 GET /api/companions/report-reasons 的 id，推荐传）",
    )
    reason_code: Optional[str] = Field(
        default="",
        description="举报理由 code；未传 reason_id 时必填（兼容旧客户端）",
    )
    remark: Optional[str] = Field(
        default="",
        max_length=2000,
        description="用户补充说明；reason=other 时建议填写",
    )
    content: Optional[str] = Field(
        default="",
        max_length=5000,
        description="被举报对话原文；scene=chat 且未传 message_id 时用于定位消息",
    )
    message_id: Optional[int] = Field(
        default=None,
        description="scene=chat 时必填（推荐）：被举报消息 id（GET /companions/{id}/messages 返回的 id）",
    )
    scene: Optional[str] = Field(
        default="companion",
        description="companion=资料/背景图举报；chat=对话举报",
    )
    source: Optional[str] = Field(
        default=None,
        description="入口页：profile / chat / moments；不传时按 scene 默认",
    )


@router.get("/api/companions/report-reasons", summary="举报理由 / 分类列表")
async def api_companion_report_reasons():
    """返回可选举报分类（id + code + 中英 label + 内部处置说明 + 等级）。"""
    return ok(data={"reasons": list_report_reasons()})


@router.post("/api/companions/{companion_id}/report", summary="举报智能体对话 / 资料背景图")
async def api_report_companion(
    companion_id: str,
    body: CompanionReportRequest,
    user_id: int = Depends(require_login_user),
):
    """
    对话举报与资料（背景图）举报共用本接口，进入同一管理端审核列表：

    - scene=chat：举报一条对话；传 message_id（推荐）或 content；审核通过后该条消息不展示
    - scene=companion：举报资料背景图；审核通过后清空并隐藏背景图（不下架智能体）
    """
    result = submit_companion_report(
        reporter_user_id=int(user_id),
        companion_id=companion_id,
        reason_id=body.reason_id,
        reason_code=body.reason_code or "",
        remark=body.remark or "",
        content=body.content or "",
        message_id=body.message_id,
        scene=body.scene or "companion",
        source=body.source if body.source is not None else "",
    )
    if not result.get("ok"):
        err = result.get("error") or "report_failed"
        code_map = {
            "invalid_reason_code": (400, "invalid_reason_code"),
            "invalid_reason_id": (400, "invalid_reason_id"),
            "invalid_scene": (400, "invalid_scene"),
            "invalid_source": (400, "invalid_source"),
            "invalid_message_id": (400, "invalid_message_id"),
            "message_id_or_content_required": (400, "message_id_or_content_required"),
            "message_not_found": (404, "message_not_found"),
            "message_already_hidden": (400, "message_already_hidden"),
            "remark_too_long": (400, "remark_too_long"),
            "content_too_long": (400, "content_too_long"),
            "remark_or_content_required": (400, "remark_or_content_required"),
            "companion_not_found": (404, companion_not_found_message()),
        }
        status, msg = code_map.get(err, (400, err))
        return fail(message=msg, code=status)

    return ok(
        data={
            "report_id": result.get("report_id"),
            "status": result.get("status"),
            "auto_hidden": result.get("auto_hidden"),
            "duplicate": result.get("duplicate"),
            "reason_id": result.get("reason_id"),
            "reason_code": result.get("reason_code"),
            "scene": result.get("scene"),
            "message_id": result.get("message_id"),
        },
        message="举报已提交，我们会尽快处理",
    )
