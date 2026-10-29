from datetime import datetime, timezone
from io import BytesIO
from typing import Optional

from fastapi import Body, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func

from api.admin_common import _get_error_msg, admin_auth_required, get_admin_lang, router
from core.database import ButtonClickORM, CompanionORM, PageViewORM, SystemNotificationORM, get_db, serialize_datetime
from core.i18n import _
from services.analytics_stats import compute_dau_series, compute_retention_cohorts

# ===== Analytics =====

def _parse_date(date_str: Optional[str]):
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _companion_avatar_url(companion_id: Optional[str], avatar_url: Optional[str]) -> str:
    cleaned = (avatar_url or "").strip()
    if cleaned and cleaned != "__GENERATING__":
        return cleaned
    if companion_id:
        return f"https://api.dicebear.com/7.x/avataaars/svg?seed={companion_id}"
    return ""


def _load_companion_avatars(db, companion_ids: list) -> dict:
    ids = [c for c in {str(x).strip() for x in companion_ids if x} if c]
    if not ids:
        return {}
    rows = db.query(CompanionORM.id, CompanionORM.avatar_url).filter(CompanionORM.id.in_(ids)).all()
    return {r.id: _companion_avatar_url(r.id, r.avatar_url) for r in rows}


def _public_notification_item(r, avatars: dict) -> dict:
    cid = getattr(r, "companion_id", None)
    return {
        "id": r.id,
        "title": r.title,
        "content": r.content,
        "language": r.language,
        "user_id": getattr(r, "user_id", None),
        "companion_id": cid,
        "moment_id": getattr(r, "moment_id", None),
        "avatar_url": avatars.get(cid) or _companion_avatar_url(cid, ""),
        "created_at": serialize_datetime(r.created_at),
    }


@router.get("/api/admin/analytics",summary="获取分析数据")
async def admin_analytics(
    page_views: int = 1,
    button_clicks: int = 1,
    include_dau: int = 0,
    include_retention: int = 0,
    start_date: str = None,
    end_date: str = None,
    language: Optional[str] = None,
    use_user_id: int = 0,  # 默认使用 device_id 统计 UV（客户端埋点未传 user_id）
    _token: str = Depends(admin_auth_required),
):
    start_dt = _parse_date(start_date)
    end_dt = _parse_date(end_date)
    if end_dt:
        end_dt = end_dt.replace(hour=23, minute=59, second=59)

    result = {}

    if page_views:
        with get_db() as db:
            # 根据 use_user_id 参数决定使用 user_id 还是 device_id 统计 UV
            if use_user_id:
                uv_field = func.count(func.distinct(PageViewORM.user_id))
            else:
                uv_field = func.count(func.distinct(PageViewORM.device_id))

            query = db.query(
                PageViewORM.page_path,
                PageViewORM.page_name,
                PageViewORM.language,
                func.count(PageViewORM.id).label("pv_count"),
                uv_field.label("uv_count"),
            )
            if start_dt:
                query = query.filter(PageViewORM.created_at >= start_dt)
            if end_dt:
                query = query.filter(PageViewORM.created_at <= end_dt)
            if language:
                query = query.filter(PageViewORM.language == language)
            rows = query.group_by(PageViewORM.page_path, PageViewORM.page_name, PageViewORM.language).order_by(func.count(PageViewORM.id).desc()).all()
            result["page_views"] = [
                {
                    "page_path": r.page_path,
                    "page_name": r.page_name,
                    "language": r.language,
                    "pv_count": r.pv_count,
                    "uv_count": r.uv_count,
                }
                for r in rows
            ]
            # 汇总
            total_pv = sum(r.pv_count for r in rows)
            # 重新查总UV
            if use_user_id:
                uv_query = db.query(func.count(func.distinct(PageViewORM.user_id)))
            else:
                uv_query = db.query(func.count(func.distinct(PageViewORM.device_id)))
            if start_dt:
                uv_query = uv_query.filter(PageViewORM.created_at >= start_dt)
            if end_dt:
                uv_query = uv_query.filter(PageViewORM.created_at <= end_dt)
            if language:
                uv_query = uv_query.filter(PageViewORM.language == language)
            total_uv = uv_query.scalar() or 0
            result["page_summary"] = {"total_pv": total_pv, "total_uv": total_uv}

    if button_clicks:
        with get_db() as db:
            # 根据 use_user_id 参数决定使用 user_id 还是 device_id 统计 UV
            if use_user_id:
                uv_field = func.count(func.distinct(ButtonClickORM.user_id))
            else:
                uv_field = func.count(func.distinct(ButtonClickORM.device_id))

            query = db.query(
                ButtonClickORM.button_id,
                ButtonClickORM.button_name,
                ButtonClickORM.page_path,
                ButtonClickORM.language,
                func.count(ButtonClickORM.id).label("click_count"),
                uv_field.label("uv_count"),
            )
            if start_dt:
                query = query.filter(ButtonClickORM.created_at >= start_dt)
            if end_dt:
                query = query.filter(ButtonClickORM.created_at <= end_dt)
            if language:
                query = query.filter(ButtonClickORM.language == language)
            rows = query.group_by(ButtonClickORM.button_id, ButtonClickORM.button_name, ButtonClickORM.page_path, ButtonClickORM.language).order_by(func.count(ButtonClickORM.id).desc()).all()
            result["button_clicks"] = [
                {
                    "button_id": r.button_id,
                    "button_name": r.button_name,
                    "page_path": r.page_path,
                    "language": r.language,
                    "click_count": r.click_count,
                    "uv_count": r.uv_count,
                }
                for r in rows
            ]
            total_click = sum(r.click_count for r in rows)
            if use_user_id:
                uv_query = db.query(func.count(func.distinct(ButtonClickORM.user_id)))
            else:
                uv_query = db.query(func.count(func.distinct(ButtonClickORM.device_id)))
            if start_dt:
                uv_query = uv_query.filter(ButtonClickORM.created_at >= start_dt)
            if end_dt:
                uv_query = uv_query.filter(ButtonClickORM.created_at <= end_dt)
            if language:
                uv_query = uv_query.filter(ButtonClickORM.language == language)
            total_uv = uv_query.scalar() or 0
            result["button_summary"] = {"total_clicks": total_click, "total_uv": total_uv}

    if include_dau:
        with get_db() as db:
            result["dau_series"] = compute_dau_series(db, start_dt, end_dt, language)

    if include_retention:
        with get_db() as db:
            result["retention"] = compute_retention_cohorts(db, start_dt, end_dt, language)

    return result


@router.get("/api/admin/analytics/export", summary="导出分析数据")
async def admin_analytics_export(
    type: str = "all",
    start_date: str = None,
    end_date: str = None,
    language: Optional[str] = None,
    use_user_id: int = 1,  # 新增：是否使用 user_id 统计 UV
    _token: str = Depends(admin_auth_required),
):
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

    start_dt = _parse_date(start_date)
    end_dt = _parse_date(end_date)
    if end_dt:
        end_dt = end_dt.replace(hour=23, minute=59, second=59)

    wb = Workbook()

    # 样式
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    center_align = Alignment(horizontal="center", vertical="center")
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )

    def _style_header(ws, headers):
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center_align
            cell.border = thin_border

    if type in ("all", "page_views"):
        ws_pv = wb.active
        ws_pv.title = "页面访问统计"
        headers_pv = ["页面路径", "页面名称", "语言", "访问次数(PV)", "访问人数(UV)1"]
        _style_header(ws_pv, headers_pv)

        with get_db() as db:
            # 根据 use_user_id 参数决定使用 user_id 还是 device_id 统计 UV
            if use_user_id:
                uv_field = func.count(func.distinct(PageViewORM.user_id))
            else:
                uv_field = func.count(func.distinct(PageViewORM.device_id))

            query = db.query(
                PageViewORM.page_path,
                PageViewORM.page_name,
                PageViewORM.language,
                func.count(PageViewORM.id).label("pv_count"),
                uv_field.label("uv_count"),
            )
            if start_dt:
                query = query.filter(PageViewORM.created_at >= start_dt)
            if end_dt:
                query = query.filter(PageViewORM.created_at <= end_dt)
            if language:
                query = query.filter(PageViewORM.language == language)
            rows = query.group_by(PageViewORM.page_path, PageViewORM.page_name, PageViewORM.language).order_by(func.count(PageViewORM.id).desc()).all()
            for idx, r in enumerate(rows, 2):
                ws_pv.cell(row=idx, column=1, value=r.page_path).border = thin_border
                ws_pv.cell(row=idx, column=2, value=r.page_name).border = thin_border
                ws_pv.cell(row=idx, column=3, value=r.language).border = thin_border
                ws_pv.cell(row=idx, column=4, value=r.pv_count).border = thin_border
                ws_pv.cell(row=idx, column=5, value=r.uv_count).border = thin_border

        for col in range(1, 6):
            ws_pv.column_dimensions[chr(64 + col)].width = 24

    if type in ("all", "button_clicks"):
        ws_bc = wb.create_sheet(title="按钮点击统计") if type == "all" else wb.active
        if type == "button_clicks":
            ws_bc.title = "按钮点击统计"
        headers_bc = ["按钮ID", "按钮名称", "所属页面", "语言", "点击次数", "点击人数(UV)"]
        _style_header(ws_bc, headers_bc)

        with get_db() as db:
            # 根据 use_user_id 参数决定使用 user_id 还是 device_id 统计 UV
            if use_user_id:
                uv_field = func.count(func.distinct(ButtonClickORM.user_id))
            else:
                uv_field = func.count(func.distinct(ButtonClickORM.device_id))

            query = db.query(
                ButtonClickORM.button_id,
                ButtonClickORM.button_name,
                ButtonClickORM.page_path,
                ButtonClickORM.language,
                func.count(ButtonClickORM.id).label("click_count"),
                uv_field.label("uv_count"),
            )
            if start_dt:
                query = query.filter(ButtonClickORM.created_at >= start_dt)
            if end_dt:
                query = query.filter(ButtonClickORM.created_at <= end_dt)
            if language:
                query = query.filter(ButtonClickORM.language == language)
            rows = query.group_by(ButtonClickORM.button_id, ButtonClickORM.button_name, ButtonClickORM.page_path, ButtonClickORM.language).order_by(func.count(ButtonClickORM.id).desc()).all()
            for idx, r in enumerate(rows, 2):
                ws_bc.cell(row=idx, column=1, value=r.button_id).border = thin_border
                ws_bc.cell(row=idx, column=2, value=r.button_name).border = thin_border
                ws_bc.cell(row=idx, column=3, value=r.page_path).border = thin_border
                ws_bc.cell(row=idx, column=4, value=r.language).border = thin_border
                ws_bc.cell(row=idx, column=5, value=r.click_count).border = thin_border
                ws_bc.cell(row=idx, column=6, value=r.uv_count).border = thin_border

        for col in range(1, 7):
            ws_bc.column_dimensions[chr(64 + col)].width = 24

    if type in ("all", "dau"):
        if type == "all":
            ws_dau = wb.create_sheet(title="日活DAU")
        else:
            ws_dau = wb.active
            ws_dau.title = "日活DAU"
        headers_dau = ["日期", "活跃设备数(DAU)"]
        _style_header(ws_dau, headers_dau)
        with get_db() as db:
            series = compute_dau_series(db, start_dt, end_dt, language)
        for idx, row in enumerate(series, 2):
            ws_dau.cell(row=idx, column=1, value=row["date"]).border = thin_border
            ws_dau.cell(row=idx, column=2, value=row["dau"]).border = thin_border

    if type in ("all", "retention"):
        if type == "all":
            ws_ret = wb.create_sheet(title="留存统计")
        else:
            ws_ret = wb.active
            ws_ret.title = "留存统计"
        offs = [1, 3, 7, 30]
        headers_ret = ["cohort日期", "新增用户数"]
        for o in offs:
            headers_ret.append(f"D{o}留存%")
            headers_ret.append(f"D{o}人数")
        _style_header(ws_ret, headers_ret)
        with get_db() as db:
            ret_payload = compute_retention_cohorts(db, start_dt, end_dt, language)
        for idx, row in enumerate(ret_payload["cohorts"], 2):
            ws_ret.cell(row=idx, column=1, value=row["cohort_date"]).border = thin_border
            ws_ret.cell(row=idx, column=2, value=row["new_users"]).border = thin_border
            col = 3
            for o in offs:
                key = f"d{o}"
                rp = row.get("retention_pct") or {}
                rc = row.get("retention_counts") or {}
                ws_ret.cell(row=idx, column=col, value=rp.get(key)).border = thin_border
                ws_ret.cell(row=idx, column=col + 1, value=rc.get(key)).border = thin_border
                col += 2

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"analytics_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ===== 系统通知管理 =====

@router.get("/api/admin/notifications", summary="获取通知列表")
async def admin_list_notifications(_token: str = Depends(admin_auth_required)):
    with get_db() as db:
        rows = db.query(SystemNotificationORM).order_by(SystemNotificationORM.created_at.desc()).all()
        return [
            {
                "id": r.id,
                "title": r.title,
                "content": r.content,
                "language": r.language,
                "user_id": getattr(r, "user_id", None),
                "companion_id": getattr(r, "companion_id", None),
                "moment_id": getattr(r, "moment_id", None),
                "created_at": serialize_datetime(r.created_at),
                "updated_at": serialize_datetime(r.updated_at),
            }
            for r in rows
        ]


@router.post("/api/admin/notifications", summary="创建通知")
async def admin_create_notification(data: dict, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    title = data.get("title", "").strip()
    content = data.get("content", "").strip()
    language = data.get("language", "zh").strip()
    if not title or not content:
        raise HTTPException(status_code=400, detail=_get_error_msg("title_content_required", lang))
    supported_langs = {"zh", "en", "ja", "ko", "pt", "es", "id"}
    if language not in supported_langs:
        raise HTTPException(status_code=400, detail=_get_error_msg("unsupported_language", lang) + f": {language}")
    with get_db() as db:
        notif = SystemNotificationORM(title=title, content=content, language=language)
        db.add(notif)
        db.flush()
        return {
            "id": notif.id,
            "title": notif.title,
            "content": notif.content,
            "language": notif.language,
            "created_at": serialize_datetime(notif.created_at),
        }


@router.delete("/api/admin/notifications/{notification_id}", summary="删除通知")
async def admin_delete_notification(notification_id: int, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    with get_db() as db:
        notif = db.query(SystemNotificationORM).filter(SystemNotificationORM.id == notification_id).first()
        if not notif:
            raise HTTPException(status_code=404, detail=_get_error_msg("notification_not_found", lang))
        db.delete(notif)
        return {"ok": True}


# ===== 批量操作API =====
@router.post("/api/admin/notifications/batch-delete", summary="批量删除通知")
async def admin_batch_delete_notifications(data: dict, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    """批量删除系统通知"""
    ids = data.get("ids", [])
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail=_("ids 必须是非空整数列表"))
    with get_db() as db:
        # 确保都是int
        int_ids = [int(i) for i in ids if str(i).isdigit()]
        if not int_ids:
            raise HTTPException(status_code=400, detail=_("无效的ID列表"))
        deleted = db.query(SystemNotificationORM).filter(
            SystemNotificationORM.id.in_(int_ids)
        ).delete(synchronize_session=False)
        db.commit()
    return {"ok": True, "deleted": deleted, "total": len(int_ids)}

# ===== 内容审核：帖子审核 & 资料审核 =====

@router.get("/api/admin/audit/posts", summary="帖子审核列表")
async def admin_list_post_reviews(
    status: str = Query("pending"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _token: str = Depends(admin_auth_required),
):
    from services.posts import list_posts_for_admin

    allowed = {"pending", "approved", "rejected", "all"}
    st = (status or "pending").strip().lower()
    if st not in allowed:
        st = "pending"
    items, total = list_posts_for_admin(status=None if st == "all" else st, limit=limit, offset=offset)
    return {"items": items, "total": total}


@router.post("/api/admin/audit/posts/{post_id}/approve", summary="通过帖子审核")
async def admin_approve_post(
    post_id: int,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.posts import review_post

    result = review_post(post_id, approve=True, reviewer="admin")
    if not result.get("ok"):
        raise HTTPException(
            status_code=404 if result.get("error") == "post_not_found" else 400,
            detail=_get_error_msg(result.get("error", "server_error"), lang),
        )
    return result


@router.post("/api/admin/audit/posts/{post_id}/reject", summary="驳回帖子审核")
async def admin_reject_post(
    post_id: int,
    data: Optional[dict] = Body(None),
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.audit_reject_reasons import parse_reject_body
    from services.posts import review_post

    body = data or {}
    reason_id, remark, err = parse_reject_body(
        reason_id=body.get("reason_id"),
        remark=body.get("remark"),
    )
    if err:
        raise HTTPException(status_code=400, detail=_get_error_msg(err, lang))
    result = review_post(
        post_id,
        approve=False,
        reason=remark,
        reviewer="admin",
        reason_id=reason_id,
    )
    if not result.get("ok"):
        raise HTTPException(
            status_code=404 if result.get("error") == "post_not_found" else 400,
            detail=_get_error_msg(result.get("error", "server_error"), lang),
        )
    return result


@router.get("/api/admin/audit/profiles", summary="资料审核列表")
async def admin_list_profile_reviews(
    status: str = Query("pending"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _token: str = Depends(admin_auth_required),
):
    from services.profile_review import list_profile_reviews

    allowed = {"pending", "approved", "rejected", "all"}
    st = (status or "pending").strip().lower()
    if st not in allowed:
        st = "pending"
    items, total = list_profile_reviews(status=None if st == "all" else st, limit=limit, offset=offset)
    return {"items": items, "total": total}


@router.post("/api/admin/audit/profiles/{review_id}/approve", summary="通过资料审核")
async def admin_approve_profile(
    review_id: int,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.profile_review import approve_profile_review

    result = approve_profile_review(review_id, reviewer="admin")
    if not result.get("ok"):
        raise HTTPException(
            status_code=404 if result.get("error") in ("review_not_found", "user_not_found") else 400,
            detail=_get_error_msg(result.get("error", "server_error"), lang),
        )
    return result


@router.post("/api/admin/audit/profiles/{review_id}/reject", summary="驳回资料审核")
async def admin_reject_profile(
    review_id: int,
    data: Optional[dict] = Body(None),
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.audit_reject_reasons import parse_reject_body
    from services.profile_review import reject_profile_review

    body = data or {}
    reason_id, remark, err = parse_reject_body(
        reason_id=body.get("reason_id"),
        remark=body.get("remark"),
    )
    if err:
        raise HTTPException(status_code=400, detail=_get_error_msg(err, lang))
    result = reject_profile_review(
        review_id,
        reason=remark,
        reviewer="admin",
        reason_id=reason_id,
    )
    if not result.get("ok"):
        raise HTTPException(
            status_code=404 if result.get("error") == "review_not_found" else 400,
            detail=_get_error_msg(result.get("error", "server_error"), lang),
        )
    return result


# ===== 智能体举报审核 =====


@router.get("/api/admin/audit/companion-reports", summary="智能体举报列表（对话+资料背景图）")
async def admin_list_companion_reports(
    status: str = Query("pending", description="pending / approved / rejected / all"),
    scene: Optional[str] = Query(
        None,
        description="可选筛选：chat=对话举报 / companion=资料背景图 / all 或不传=全部",
    ),
    companion_id: Optional[str] = Query(None),
    reporter_user_id: Optional[int] = Query(None, ge=1),
    reason_code: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    _token: str = Depends(admin_auth_required),
):
    from core.response import paginated
    from services.companion_report import list_companion_reports

    st = (status or "pending").strip().lower()
    if st not in {"pending", "approved", "rejected", "all"}:
        st = "pending"
    scn = (scene or "").strip().lower() or None
    if scn and scn not in {"chat", "companion", "all"}:
        scn = None
    items, total = list_companion_reports(
        status=None if st == "all" else st,
        scene=scn,
        companion_id=companion_id,
        reporter_user_id=reporter_user_id,
        reason_code=reason_code,
        page=page,
        page_size=page_size,
    )
    return paginated(items=items, total=total, page=page, page_size=page_size)


@router.get("/api/admin/audit/companion-reports/{report_id}", summary="智能体举报详情")
async def admin_get_companion_report(
    report_id: int,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from core.response import ok as resp_ok
    from services.companion_report import get_companion_report

    row = get_companion_report(report_id)
    if not row:
        raise HTTPException(status_code=404, detail=_get_error_msg("not_found", lang))
    return resp_ok(data=row)


@router.post(
    "/api/admin/audit/companion-reports/{report_id}/approve",
    summary="举报审核通过（对话→隐藏消息；资料→清空背景图）",
)
async def admin_approve_companion_report(
    report_id: int,
    data: Optional[dict] = Body(None),
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.companion_report import approve_companion_report
    from services.im_admin import resolve_admin_actor

    actor = resolve_admin_actor(_token)
    reviewer = str(getattr(actor, "username", None) or getattr(actor, "id", None) or "admin")
    note = (data or {}).get("review_note") or (data or {}).get("note") or ""
    result = approve_companion_report(report_id, reviewer=reviewer, review_note=note)
    if not result.get("ok"):
        err = result.get("error") or "server_error"
        status = 404 if "not_found" in err else 400
        raise HTTPException(status_code=status, detail=_get_error_msg(err, lang))
    return result


@router.post("/api/admin/audit/companion-reports/{report_id}/reject", summary="举报审核驳回（举报不成立）")
async def admin_reject_companion_report(
    report_id: int,
    data: Optional[dict] = Body(None),
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.companion_report import reject_companion_report
    from services.im_admin import resolve_admin_actor

    actor = resolve_admin_actor(_token)
    reviewer = str(getattr(actor, "username", None) or getattr(actor, "id", None) or "admin")
    note = (data or {}).get("review_note") or (data or {}).get("note") or ""
    result = reject_companion_report(report_id, reviewer=reviewer, review_note=note)
    if not result.get("ok"):
        err = result.get("error") or "server_error"
        status = 404 if "not_found" in err else 400
        raise HTTPException(status_code=status, detail=_get_error_msg(err, lang))
    return result


# ===== 智能体资料审核（背景图） =====


@router.get("/api/admin/audit/companions", summary="智能体资料审核列表")
async def admin_list_companion_profile_reviews(
    status: str = Query("pending"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _token: str = Depends(admin_auth_required),
):
    from services.companion_profile_review import list_companion_profile_reviews

    allowed = {"pending", "approved", "rejected", "all"}
    st = (status or "pending").strip().lower()
    if st not in allowed:
        st = "pending"
    items, total = list_companion_profile_reviews(
        status=None if st == "all" else st, limit=limit, offset=offset
    )
    return {"items": items, "total": total}


@router.post("/api/admin/audit/companions/{review_id}/approve", summary="通过智能体资料审核（展示背景图）")
async def admin_approve_companion_profile(
    review_id: int,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.companion_profile_review import approve_companion_profile_review

    result = approve_companion_profile_review(review_id, reviewer="admin")
    if not result.get("ok"):
        raise HTTPException(
            status_code=404 if result.get("error") in ("review_not_found", "companion_not_found") else 400,
            detail=_get_error_msg(result.get("error", "server_error"), lang),
        )
    return result


@router.post("/api/admin/audit/companions/{review_id}/reject", summary="驳回智能体资料审核（不展示背景图）")
async def admin_reject_companion_profile(
    review_id: int,
    data: Optional[dict] = Body(None),
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.audit_reject_reasons import parse_reject_body
    from services.companion_profile_review import reject_companion_profile_review

    body = data or {}
    reason_id, remark, err = parse_reject_body(
        reason_id=body.get("reason_id"),
        remark=body.get("remark"),
    )
    if err:
        raise HTTPException(status_code=400, detail=_get_error_msg(err, lang))
    result = reject_companion_profile_review(
        review_id,
        reason=remark,
        reviewer="admin",
        reason_id=reason_id,
    )
    if not result.get("ok"):
        raise HTTPException(
            status_code=404 if result.get("error") == "review_not_found" else 400,
            detail=_get_error_msg(result.get("error", "server_error"), lang),
        )
    return result


# ===== 用户资料举报审核 =====


@router.get("/api/admin/audit/user-reports", summary="用户资料举报列表")
async def admin_list_user_reports(
    status: str = Query("pending", description="pending / approved / rejected / all"),
    reported_user_id: Optional[int] = Query(None, ge=1),
    reporter_user_id: Optional[int] = Query(None, ge=1),
    reason_code: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    _token: str = Depends(admin_auth_required),
):
    from core.response import paginated
    from services.ugc_report import list_user_reports

    st = (status or "pending").strip().lower()
    if st not in {"pending", "approved", "rejected", "all"}:
        st = "pending"
    items, total = list_user_reports(
        status=None if st == "all" else st,
        reported_user_id=reported_user_id,
        reporter_user_id=reporter_user_id,
        reason_code=reason_code,
        page=page,
        page_size=page_size,
    )
    return paginated(items=items, total=total, page=page, page_size=page_size)


@router.get("/api/admin/audit/user-reports/{report_id}", summary="用户资料举报详情")
async def admin_get_user_report(
    report_id: int,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from core.response import ok as resp_ok
    from services.ugc_report import get_user_report

    row = get_user_report(report_id)
    if not row:
        raise HTTPException(status_code=404, detail=_get_error_msg("not_found", lang))
    return resp_ok(data=row)


@router.post("/api/admin/audit/user-reports/{report_id}/approve", summary="用户举报通过（封禁用户）")
async def admin_approve_user_report(
    report_id: int,
    data: Optional[dict] = Body(None),
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.im_admin import resolve_admin_actor
    from services.ugc_report import approve_user_report

    actor = resolve_admin_actor(_token)
    reviewer = str(getattr(actor, "username", None) or getattr(actor, "id", None) or "admin")
    note = (data or {}).get("review_note") or (data or {}).get("note") or ""
    result = approve_user_report(report_id, reviewer=reviewer, review_note=note)
    if not result.get("ok"):
        err = result.get("error") or "server_error"
        status = 404 if "not_found" in err else 400
        raise HTTPException(status_code=status, detail=_get_error_msg(err, lang))
    return result


@router.post("/api/admin/audit/user-reports/{report_id}/reject", summary="用户举报驳回")
async def admin_reject_user_report(
    report_id: int,
    data: Optional[dict] = Body(None),
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.im_admin import resolve_admin_actor
    from services.ugc_report import reject_user_report

    actor = resolve_admin_actor(_token)
    reviewer = str(getattr(actor, "username", None) or getattr(actor, "id", None) or "admin")
    note = (data or {}).get("review_note") or (data or {}).get("note") or ""
    result = reject_user_report(report_id, reviewer=reviewer, review_note=note)
    if not result.get("ok"):
        err = result.get("error") or "server_error"
        status = 404 if "not_found" in err else 400
        raise HTTPException(status_code=status, detail=_get_error_msg(err, lang))
    return result


# ===== 帖子举报审核 =====


@router.get("/api/admin/audit/post-reports", summary="帖子举报列表")
async def admin_list_post_reports(
    status: str = Query("pending", description="pending / approved / rejected / all"),
    post_id: Optional[int] = Query(None, ge=1),
    reporter_user_id: Optional[int] = Query(None, ge=1),
    reason_code: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    _token: str = Depends(admin_auth_required),
):
    from core.response import paginated
    from services.ugc_report import list_post_reports

    st = (status or "pending").strip().lower()
    if st not in {"pending", "approved", "rejected", "all"}:
        st = "pending"
    items, total = list_post_reports(
        status=None if st == "all" else st,
        post_id=post_id,
        reporter_user_id=reporter_user_id,
        reason_code=reason_code,
        page=page,
        page_size=page_size,
    )
    return paginated(items=items, total=total, page=page, page_size=page_size)


@router.get("/api/admin/audit/post-reports/{report_id}", summary="帖子举报详情")
async def admin_get_post_report(
    report_id: int,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from core.response import ok as resp_ok
    from services.ugc_report import get_post_report

    row = get_post_report(report_id)
    if not row:
        raise HTTPException(status_code=404, detail=_get_error_msg("not_found", lang))
    return resp_ok(data=row)


@router.post("/api/admin/audit/post-reports/{report_id}/approve", summary="帖子举报通过（下架帖子）")
async def admin_approve_post_report(
    report_id: int,
    data: Optional[dict] = Body(None),
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.im_admin import resolve_admin_actor
    from services.ugc_report import approve_post_report

    actor = resolve_admin_actor(_token)
    reviewer = str(getattr(actor, "username", None) or getattr(actor, "id", None) or "admin")
    note = (data or {}).get("review_note") or (data or {}).get("note") or ""
    result = approve_post_report(report_id, reviewer=reviewer, review_note=note)
    if not result.get("ok"):
        err = result.get("error") or "server_error"
        status = 404 if "not_found" in err else 400
        raise HTTPException(status_code=status, detail=_get_error_msg(err, lang))
    return result


@router.post("/api/admin/audit/post-reports/{report_id}/reject", summary="帖子举报驳回")
async def admin_reject_post_report(
    report_id: int,
    data: Optional[dict] = Body(None),
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.im_admin import resolve_admin_actor
    from services.ugc_report import reject_post_report

    actor = resolve_admin_actor(_token)
    reviewer = str(getattr(actor, "username", None) or getattr(actor, "id", None) or "admin")
    note = (data or {}).get("review_note") or (data or {}).get("note") or ""
    result = reject_post_report(report_id, reviewer=reviewer, review_note=note)
    if not result.get("ok"):
        err = result.get("error") or "server_error"
        status = 404 if "not_found" in err else 400
        raise HTTPException(status_code=status, detail=_get_error_msg(err, lang))
    return result


# ===== 公开 API：获取系统通知（供前端使用） =====
# 历史原因：用户侧读通知挂在本 admin 模块；鉴权走用户 x-token，勿误加成 admin_auth。

@router.get("/api/notifications", summary="获取系统通知", tags=["反馈"])
async def public_list_notifications(
    request: Request,
    language: str = "zh",
    limit: int = 20,
    x_token: Optional[str] = Header(None, alias="x-token"),
):
    """返回当前语言的广播通知 + 当前登录用户的定向通知（审核 / 私信 / 评论回复）。"""
    supported_langs = {"zh", "en", "ja", "ko", "pt", "es", "id"}
    if language not in supported_langs:
        language = "zh"

    user_id = None
    if x_token:
        try:
            from core.auth import verify_token as redis_verify_token

            user_id = redis_verify_token(x_token)
        except Exception:
            user_id = None
    if not user_id:
        user_id = getattr(getattr(request, "state", None), "user_id", None)
    if not user_id:
        auth = request.headers.get("authorization") or ""
        bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if bearer:
            try:
                from core.auth import verify_token as redis_verify_token

                user_id = redis_verify_token(bearer)
            except Exception:
                user_id = None

    from sqlalchemy import or_, and_

    with get_db() as db:
        # 广播：user_id 为空且语言匹配
        broadcast = and_(
            SystemNotificationORM.language == language,
            SystemNotificationORM.user_id.is_(None),
        )
        if user_id:
            # 审核类定向通知仍按语言
            directed_lang = and_(
                SystemNotificationORM.language == language,
                SystemNotificationORM.user_id == user_id,
            )
            # 私信 / 评论回复带 companion_id：不因 language 参数漏掉
            directed_companion = and_(
                SystemNotificationORM.user_id == user_id,
                SystemNotificationORM.companion_id.isnot(None),
            )
            cond = or_(broadcast, directed_lang, directed_companion)
        else:
            cond = broadcast
        rows = (
            db.query(SystemNotificationORM)
            .filter(cond)
            .order_by(SystemNotificationORM.created_at.desc())
            .limit(limit * 3 if user_id else limit)  # 预取，便于过滤创建者软删关联通知
            .all()
        )

        # 创建者软删后：自己侧不展示关联该智能体的通知；其他人不受影响
        if user_id:
            from core.state import get_companion_manager

            owner_keys = get_companion_manager()._owner_keys(user_id, db=db)
            deleted_mine = {
                r.id
                for r in db.query(CompanionORM.id)
                .filter(
                    CompanionORM.is_deleted.is_(True),
                    CompanionORM.created_by.in_(list(owner_keys)),
                )
                .all()
            }
            filtered = []
            for r in rows:
                cid = getattr(r, "companion_id", None)
                if cid and cid in deleted_mine:
                    continue
                filtered.append(r)
                if len(filtered) >= limit:
                    break
            rows = filtered
        else:
            rows = rows[:limit]

        avatars = _load_companion_avatars(
            db, [getattr(r, "companion_id", None) for r in rows]
        )
        return {
            "notifications": [_public_notification_item(r, avatars) for r in rows]
        }


@router.get("/api/admin/outreach/funnel", summary="运营触达漏斗看板", tags=["运营触达"])
async def admin_outreach_funnel(
    days: int = 7,
    _token: str = Depends(admin_auth_required),
):
    """O-24：按 job_type/status 聚合 + 回复率 + Moment 路径转化。"""
    from services.outreach_phase3 import get_outreach_funnel

    return get_outreach_funnel(days=days)


@router.post("/api/admin/outreach/phase3-scan", summary="手动触发 Phase3 扫描", tags=["运营触达"])
async def admin_outreach_phase3_scan(_token: str = Depends(admin_auth_required)):
    from services.outreach_phase3 import run_phase3_periodic_scans

    return run_phase3_periodic_scans()
