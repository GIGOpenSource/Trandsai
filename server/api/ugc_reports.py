"""App 端：用户资料举报 / 帖子举报。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.companion_access import require_login_user
from core.response import fail, ok
from services.ugc_report import (
    list_ugc_report_reasons,
    submit_post_report,
    submit_user_report,
)

router = APIRouter(tags=["UGC举报"])


class UgcReportRequest(BaseModel):
    reason_id: Optional[int] = Field(
        default=None,
        description="举报分类 id（来自 GET /api/report-reasons）",
    )
    reason_code: Optional[str] = Field(
        default="",
        description="举报理由 code；未传 reason_id 时必填",
    )
    remark: Optional[str] = Field(default="", max_length=2000)
    content: Optional[str] = Field(default="", max_length=5000)
    source: Optional[str] = Field(
        default=None,
        description="入口：用户举报 profile/chat/moments；帖子举报 feed/detail/profile/moments",
    )


def _map_report_error(err: str):
    code_map = {
        "invalid_reason_code": (400, "invalid_reason_code"),
        "invalid_reason_id": (400, "invalid_reason_id"),
        "invalid_source": (400, "invalid_source"),
        "remark_too_long": (400, "remark_too_long"),
        "content_too_long": (400, "content_too_long"),
        "remark_or_content_required": (400, "remark_or_content_required"),
        "cannot_report_self": (400, "cannot_report_self"),
        "cannot_report_own_post": (400, "cannot_report_own_post"),
        "user_not_found": (404, "user_not_found"),
        "post_not_found": (404, "post_not_found"),
    }
    return code_map.get(err, (400, err or "report_failed"))


@router.get("/api/report-reasons", summary="通用举报理由列表")
async def api_report_reasons():
    """用户 / 帖子 / 智能体举报共用分类（与 /api/companions/report-reasons 一致）。"""
    return ok(data={"reasons": list_ugc_report_reasons()})


@router.post("/api/users/{reported_user_id}/report", summary="举报用户资料")
async def api_report_user(
    reported_user_id: int,
    body: UgcReportRequest,
    user_id: int = Depends(require_login_user),
):
    result = submit_user_report(
        reporter_user_id=int(user_id),
        reported_user_id=int(reported_user_id),
        reason_id=body.reason_id,
        reason_code=body.reason_code or "",
        remark=body.remark or "",
        content=body.content or "",
        source=body.source if body.source is not None else "profile",
    )
    if not result.get("ok"):
        status, msg = _map_report_error(result.get("error") or "report_failed")
        return fail(message=msg, code=status)
    return ok(
        data={
            "report_id": result.get("report_id"),
            "status": result.get("status"),
            "auto_hidden": result.get("auto_hidden"),
            "duplicate": result.get("duplicate"),
            "reason_id": result.get("reason_id"),
            "reason_code": result.get("reason_code"),
        },
        message="举报已提交，我们会尽快处理",
    )


@router.post("/api/posts/{post_id}/report", summary="举报帖子")
async def api_report_post(
    post_id: int,
    body: UgcReportRequest,
    user_id: int = Depends(require_login_user),
):
    result = submit_post_report(
        reporter_user_id=int(user_id),
        post_id=int(post_id),
        reason_id=body.reason_id,
        reason_code=body.reason_code or "",
        remark=body.remark or "",
        content=body.content or "",
        source=body.source if body.source is not None else "feed",
    )
    if not result.get("ok"):
        status, msg = _map_report_error(result.get("error") or "report_failed")
        return fail(message=msg, code=status)
    return ok(
        data={
            "report_id": result.get("report_id"),
            "status": result.get("status"),
            "auto_hidden": result.get("auto_hidden"),
            "duplicate": result.get("duplicate"),
            "reason_id": result.get("reason_id"),
            "reason_code": result.get("reason_code"),
        },
        message="举报已提交，我们会尽快处理",
    )
