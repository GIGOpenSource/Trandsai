from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from core.auth import verify_token as redis_verify_token
from core.database import FeedbackMessageORM, FeedbackThreadORM, UserORM, get_db

router = APIRouter()


@router.post("/api/feedback/messages")
async def send_feedback_message(data: dict, x_token: Optional[str] = Header(None)):
    """用户发送反馈消息"""
    user_id = redis_verify_token(x_token) if x_token else None
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录或Token已过期")

    content = (data.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="内容不能为空")

    with get_db() as db:
        # 获取用户信息
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")

        # 查找或创建 thread（每个用户唯一）
        thread = db.query(FeedbackThreadORM).filter(
            FeedbackThreadORM.user_id == user_id
        ).first()

        if not thread:
            thread = FeedbackThreadORM(
                user_id=user_id,
                user_name=user.nickname or user.username,
                status="open",
            )
            db.add(thread)
            db.flush()  # 获取 thread.id

        # 插入用户消息
        msg = FeedbackMessageORM(
            thread_id=thread.id,
            sender="user",
            content=content,
        )
        db.add(msg)

        # 如果是该 thread 的第一条用户消息，自动插入系统默认回复
        user_msg_count = db.query(FeedbackMessageORM).filter(
            FeedbackMessageORM.thread_id == thread.id,
            FeedbackMessageORM.sender == "user",
        ).count()

        # 注意：上面刚插入了当前消息，如果 count 为 1 表示这是第一条
        if user_msg_count == 1:
            system_msg = FeedbackMessageORM(
                thread_id=thread.id,
                sender="system",
                content="您的问题已收到，我们会尽快处理",
            )
            db.add(system_msg)
            thread.status = "open"

        thread.updated_at = datetime.now(timezone.utc)

    return {"ok": True}


@router.get("/api/feedback/messages")
async def get_feedback_messages(x_token: Optional[str] = Header(None)):
    """获取当前用户的反馈消息列表"""
    user_id = redis_verify_token(x_token) if x_token else None
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录或Token已过期")

    with get_db() as db:
        thread = db.query(FeedbackThreadORM).filter(
            FeedbackThreadORM.user_id == user_id
        ).first()

        if not thread:
            return {"messages": [], "thread_id": None}

        messages = db.query(FeedbackMessageORM).filter(
            FeedbackMessageORM.thread_id == thread.id
        ).order_by(FeedbackMessageORM.created_at.asc()).all()

        return {
            "thread_id": thread.id,
            "status": thread.status,
            "messages": [
                {
                    "id": m.id,
                    "sender": m.sender,
                    "content": m.content,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m in messages
            ],
        }
