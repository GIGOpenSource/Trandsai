from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from core.auth import verify_token as redis_verify_token
from core.database import UserORM, get_db
from core.i18n import (
    auth_required_message,
    empty_content_message,
    user_not_found_message,
)
from services.feedback import (
    clear_thread_messages,
    empty_inbox_payload,
    get_or_create_user_thread,
    get_user_thread,
    inbox_payload,
    list_messages,
    mark_user_read,
    send_user_message,
    serialize_message,
)

router = APIRouter(tags=["反馈"])


class SendFeedbackRequest(BaseModel):
    """发送反馈消息请求体"""
    content: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="反馈内容",
        examples=["应用闪退了，希望能修复"]
    )


def _require_user(request: Request, x_token: Optional[str]) -> int:
    user_id = redis_verify_token(x_token) if x_token else None
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail=auth_required_message(request.headers.get("accept-language")),
        )
    return int(user_id)


@router.get("/api/feedback/inbox", summary="Messages 页 Feedback 入口")
async def get_feedback_inbox(
    request: Request,
    x_token: Optional[str] = Header(None, alias="x-token"),
):
    """聊天列表置顶入口。始终可展示；has_chat=false 时 last_message_at 为空，不要用 updated_at。"""
    user_id = _require_user(request, x_token)
    al = request.headers.get("accept-language")
    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=user_not_found_message(al))
        thread = get_user_thread(db, user_id)
        return inbox_payload(db, thread, lang=al)


@router.post("/api/feedback/messages", summary="发送反馈消息")
async def send_feedback_message(
        request: Request,
        data: SendFeedbackRequest,
        x_token: Optional[str] = Header(None, alias="x-token", description="用户Token")
):
    """用户发送反馈。无自动回复，等运营人工回复。"""
    user_id = _require_user(request, x_token)
    al = request.headers.get("accept-language")
    content = data.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail=empty_content_message(al))

    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=user_not_found_message(al))
        thread = get_or_create_user_thread(db, user)
        msg = send_user_message(db, thread, content)
        return {
            "ok": True,
            "thread_id": thread.id,
            "has_chat": True,
            "unread_count": 0,
            "messages": [serialize_message(msg, lang=al)],
        }


@router.get("/api/feedback/messages", summary="获取反馈消息列表")
async def get_feedback_messages(
    request: Request,
    x_token: Optional[str] = Header(None),
    since_id: Optional[int] = Query(None, description="只返回 id 大于该值的新消息（轮询）"),
    mark_read: bool = Query(True, description="进入会话时清官方未读，轮询时可 false"),
):
    """进入 Feedback 聊天：默认清未读。轮询请带 since_id，并把 mark_read=false。无记录不创建会话。"""
    user_id = _require_user(request, x_token)
    al = request.headers.get("accept-language")
    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=user_not_found_message(al))
        thread = get_user_thread(db, user_id)
        if not thread:
            empty = empty_inbox_payload(al)
            return {
                "thread_id": None,
                "pinned": True,
                "has_chat": False,
                "status": "open",
                "unread_count": 0,
                "messages": [],
                "entry_name": empty["entry_name"],
            }
        if mark_read:
            mark_user_read(thread)
        payload = inbox_payload(db, thread, lang=al)
        return {
            "thread_id": thread.id,
            "has_chat": payload["has_chat"],
            "status": thread.status,
            "unread_count": 0 if mark_read else payload["unread_count"],
            "messages": list_messages(db, thread, lang=al, since_id=since_id),
        }


@router.post("/api/feedback/clear-messages", summary="清空客服聊天记录")
@router.delete("/api/feedback/messages", summary="清空客服聊天记录", include_in_schema=False)
async def clear_feedback_messages(
    request: Request,
    x_token: Optional[str] = Header(None, alias="x-token"),
):
    """删除当前用户全部客服消息。列表仍置顶；has_chat=false，不要展示最近聊天时间。"""
    user_id = _require_user(request, x_token)
    al = request.headers.get("accept-language")
    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=user_not_found_message(al))
        thread = get_user_thread(db, user_id)
        deleted = clear_thread_messages(db, thread) if thread else 0
        return {
            "ok": True,
            "pinned": True,
            "has_chat": False,
            "deleted": deleted,
            "unread_count": 0,
            "last_message_at": None,
        }


@router.post("/api/feedback/read", summary="清零 Feedback 官方未读")
async def mark_feedback_read(
    request: Request,
    x_token: Optional[str] = Header(None, alias="x-token"),
):
    user_id = _require_user(request, x_token)
    al = request.headers.get("accept-language")
    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=user_not_found_message(al))
        thread = get_user_thread(db, user_id)
        if thread:
            mark_user_read(thread)
        return {"ok": True, "unread_count": 0}
