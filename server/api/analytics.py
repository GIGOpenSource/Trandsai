from typing import List, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from core.database import ButtonClickORM, PageViewORM, get_db

router = APIRouter(tags=["数据分析"])


class PageViewPayload(BaseModel):
    page_path: str
    page_name: str
    device_id: str
    user_id: Optional[int] = None  # 忽略客户端伪造；以鉴权态为准
    language: str = ""


class ButtonClickPayload(BaseModel):
    button_id: str
    button_name: str
    page_path: str
    device_id: str
    user_id: Optional[int] = None  # 忽略客户端伪造；以鉴权态为准
    language: str = ""


class AnalyticsBatchEvent(BaseModel):
    type: str
    page_path: str = ""
    page_name: str = ""
    device_id: str = ""
    button_id: str = ""
    button_name: str = ""
    user_id: Optional[int] = None
    language: str = ""


class AnalyticsBatchPayload(BaseModel):
    events: List[AnalyticsBatchEvent] = Field(default_factory=list)


@router.post("/api/analytics/page-view", summary="记录页面浏览")
async def track_page_view(data: PageViewPayload, request: Request):
    uid = getattr(request.state, "user_id", None)
    with get_db() as db:
        db.add(PageViewORM(
            page_path=data.page_path,
            page_name=data.page_name,
            user_id=uid,
            device_id=data.device_id,
            language=data.language,
        ))
        db.commit()
    return {"ok": True}


@router.post("/api/analytics/button-click", summary="记录按钮点击")
async def track_button_click(data: ButtonClickPayload, request: Request):
    uid = getattr(request.state, "user_id", None)
    with get_db() as db:
        db.add(ButtonClickORM(
            button_id=data.button_id,
            button_name=data.button_name,
            page_path=data.page_path,
            user_id=uid,
            device_id=data.device_id,
            language=data.language,
        ))
        db.commit()
    return {"ok": True}


@router.post("/api/analytics/batch", summary="批量记录页面浏览与按钮点击")
async def track_analytics_batch(data: AnalyticsBatchPayload, request: Request):
    uid = getattr(request.state, "user_id", None)
    events = list(data.events or [])[:50]
    accepted = 0
    with get_db() as db:
        for ev in events:
            etype = (ev.type or "").strip()
            if etype == "page_view":
                if not ev.page_path or not ev.device_id:
                    continue
                db.add(PageViewORM(
                    page_path=ev.page_path,
                    page_name=ev.page_name,
                    user_id=uid,
                    device_id=ev.device_id,
                    language=ev.language,
                ))
                accepted += 1
            elif etype == "button_click":
                if not ev.button_id or not ev.device_id:
                    continue
                db.add(ButtonClickORM(
                    button_id=ev.button_id,
                    button_name=ev.button_name,
                    page_path=ev.page_path,
                    user_id=uid,
                    device_id=ev.device_id,
                    language=ev.language,
                ))
                accepted += 1
        db.commit()
    return {"ok": True, "accepted": accepted}
