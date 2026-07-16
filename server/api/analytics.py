from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from core.database import ButtonClickORM, PageViewORM, get_db

router = APIRouter()


class PageViewPayload(BaseModel):
    page_path: str
    page_name: str
    device_id: str
    user_id: Optional[int] = None  # 新增：用户ID
    language: str = ""


class ButtonClickPayload(BaseModel):
    button_id: str
    button_name: str
    page_path: str
    device_id: str
    user_id: Optional[int] = None  # 新增：用户ID
    language: str = ""


@router.post("/api/analytics/page-view")
async def track_page_view(data: PageViewPayload):
    with get_db() as db:
        db.add(PageViewORM(
            page_path=data.page_path,
            page_name=data.page_name,
            user_id=data.user_id,  # 保存 user_id
            device_id=data.device_id,  # 保留 device_id
            language=data.language,
        ))
        db.commit()
    return {"ok": True}


@router.post("/api/analytics/button-click")
async def track_button_click(data: ButtonClickPayload):
    with get_db() as db:
        db.add(ButtonClickORM(
            button_id=data.button_id,
            button_name=data.button_name,
            page_path=data.page_path,
            user_id=data.user_id,  # 保存 user_id
            device_id=data.device_id,  # 保留 device_id
            language=data.language,
        ))
        db.commit()
    return {"ok": True}
