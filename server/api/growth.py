"""用户增长 / 归因上报（独立表，不影响登录、支付、积分）。"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Header, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from core.database import get_db
from core.response import fail, ok
from core.log_util import pipeline_log
from services.growth_attribution import (
    build_attribution_fields,
    link_device_attribution_to_user,
    record_attribution_event,
)

router = APIRouter(tags=["增长归因"])
logger = logging.getLogger(__name__)


class AttributionPayload(BaseModel):
    event_type: str = Field(
        default="install",
        description="install/first_open/register/login/purchase/product",
    )
    device_id: str = Field(default="", max_length=64)
    install_source: str = Field(default="", max_length=32, description="organic/ads/invite/referral/unknown")
    utm_source: str = Field(default="", max_length=128)
    utm_medium: str = Field(default="", max_length=128)
    utm_campaign: str = Field(default="", max_length=128)
    utm_content: str = Field(default="", max_length=128)
    utm_term: str = Field(default="", max_length=128)
    gclid: str = Field(default="", max_length=256, description="Google Click ID，广告点击带来")
    gad_campaignid: str = Field(
        default="",
        max_length=128,
        description="Google Ads campaign id（也可从 play_referrer 解析）",
    )
    invite_code: str = Field(default="", max_length=64)
    referrer_user_id: Optional[int] = None
    ad_id: str = Field(default="", max_length=128, description="IDFA / GAID，需用户授权")
    ad_id_type: str = Field(default="", max_length=16, description="idfa / gaid / none")
    idfv: str = Field(default="", max_length=64)
    android_id: str = Field(default="", max_length=64)
    network: str = Field(default="", max_length=16, description="wifi / cellular / unknown")
    push_enabled: Optional[bool] = None
    login_method: str = Field(default="", max_length=20)
    app_version: str = Field(default="", max_length=32)
    os: str = Field(default="", max_length=16)
    os_version: str = Field(default="", max_length=32)
    device_model: str = Field(default="", max_length=64)
    locale: str = Field(default="", max_length=16)
    timezone: str = Field(default="", max_length=64)
    storefront_country: str = Field(default="", max_length=8)
    ip_country: str = Field(default="", max_length=8)
    attribution_provider: str = Field(
        default="",
        max_length=32,
        description="firebase/play_referrer/invite/zeasads/organic/manual",
    )
    app_env: str = Field(default="", max_length=16, description="prod / test")
    product_event: str = Field(
        default="",
        max_length=64,
        description="event_type=product 时必填，产品行为事件名",
    )
    firebase_event_name: str = Field(
        default="",
        max_length=128,
        description="与 GA4 logEvent 一致的事件名（测试环境可为 test_ 前缀）",
    )
    firebase_event_params: Dict[str, Any] = Field(
        default_factory=dict,
        description="与 GA4 logEvent 参数一致的副本（值建议均为 string）",
    )
    app_instance_id: str = Field(
        default="",
        max_length=64,
        description="Firebase Analytics App Instance ID（建议传，便于与 SDK 会话合并）",
    )
    raw_payload: Dict[str, Any] = Field(
        default_factory=dict,
        description="Firebase / MMP 原始字段，便于排查；可含 gclid/utm 兜底",
    )
    companion_id: str = Field(
        default="",
        max_length=8,
        description="产品埋点关联的智能体 ID（可选；未传/null/无效时忽略，不报错）",
    )

    @field_validator("companion_id", mode="before")
    @classmethod
    def _coerce_companion_id(cls, v: Any) -> str:
        if v is None:
            return ""
        try:
            if isinstance(v, bool):
                return ""
            s = str(v).strip()
            return s[:8] if s else ""
        except Exception:
            return ""

    @model_validator(mode="after")
    def _merge_companion_from_firebase(self) -> "AttributionPayload":
        if (self.companion_id or "").strip():
            return self
        fb = self.firebase_event_params if isinstance(self.firebase_event_params, dict) else {}
        for key in ("companion_id", "companionId"):
            raw = fb.get(key)
            if raw is None or raw == "":
                continue
            try:
                s = str(raw).strip()[:8]
                if s:
                    self.companion_id = s
                    break
            except Exception:
                continue
        return self


@router.post("/api/growth/attribution", summary="上报增长 / 归因 / 产品埋点事件")
async def report_attribution(
    request: Request,
    data: AttributionPayload,
    background_tasks: BackgroundTasks,
    x_device_id: Optional[str] = Header(None, alias="X-Device-Id"),
):
    """
    前端在 **首次打开 App**、**注册/登录成功**、**付费成功**、**产品行为埋点** 时调用。

    - 未登录可上报 `install` / `first_open` / `product`（必须带 `device_id`）
    - 已登录请带 `x-token`，服务端会把 `user_id` 写入并回填同设备的匿名事件
    - `install` 默认每 `device_id` 只记一条（幂等）；`product` 每次都记
    - `event_type=product` 时需带 `product_event`（或 `firebase_event_name`）
    - 若带 `gclid` 且配置了 GA4_MEASUREMENT_ID + GA4_API_SECRET，会异步补报 GA4 Measurement Protocol
    """
    uid = getattr(request.state, "user_id", None)
    req_payload = data.model_dump()
    logger.info(
        "[growth.attribution] body event_type=%s product_event=%s companion_id=%s user_id=%s payload=%s",
        req_payload.get("event_type") or "",
        req_payload.get("product_event") or req_payload.get("firebase_event_name") or "",
        req_payload.get("companion_id") or "",
        uid or "",
        json.dumps(req_payload, ensure_ascii=False, default=str),
    )

    fields = build_attribution_fields(
        request,
        req_payload,
        x_device_id=x_device_id,
        user_id=uid,
    )
    # app_instance_id 一并带给 GA4 MP（不落库也可）
    if data.app_instance_id:
        fields["app_instance_id"] = (data.app_instance_id or "").strip()[:64]
    if not fields.get("device_id"):
        return fail(message="device_id required", code=400)
    if fields.get("event_type") == "product" and not fields.get("product_event"):
        return fail(message="product_event required", code=400)

    with get_db() as db:
        linked = 0
        if uid:
            linked = link_device_attribution_to_user(db, fields["device_id"], int(uid))
            fields["user_id"] = int(uid)
        row = record_attribution_event(db, fields)
        db.commit()
        pipeline_log(
            "[growth.attribution] persisted recorded=%s linked=%s event_type=%s "
            "product_event=%s user_id=%s companion_id=%s locale=%s app_env=%s app_key=%s",
            bool(row),
            linked,
            fields.get("event_type"),
            fields.get("product_event") or "",
            fields.get("user_id"),
            fields.get("companion_id") or "",
            fields.get("locale") or "",
            fields.get("app_env") or "",
            fields.get("app_key") or "",
        )
        # msg_strategy 触发日志在 record_attribution_event 内输出（含 device 反查 user_id）

    if fields.get("gclid"):
        from services.ga4_measurement import report_gclid_to_ga4_safe

        background_tasks.add_task(report_gclid_to_ga4_safe, dict(fields))

    return ok(
        data={
            "recorded": bool(row),
            "linked": linked,
            "event_type": fields.get("event_type"),
            "product_event": fields.get("product_event") or "",
            "app_key": fields.get("app_key") or "trandsai",
            "ga4_queued": bool(fields.get("gclid")),
        },
        message="ok",
    )
