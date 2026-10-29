"""数据统计优化：日活 / 留存 / 事件 / 漏斗 / 营收（标准化 ok/paginated）。"""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from typing import Any, List, Optional

from fastapi import Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from api.admin_common import admin_auth_required, router
from core.database import get_db
from core.response import ok, paginated
from services.admin_data_stats import (
    compute_dau_rows,
    compute_event_rows,
    compute_funnel_rows,
    compute_overview,
    compute_retention_rows,
    compute_revenue_rows,
    filter_options,
    normalize_user_type,
    resolve_date_range,
    slice_page,
)


def _parse_user_type(user_type: Optional[str]) -> str:
    try:
        return normalize_user_type(user_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="invalid_user_type: use all | new | old (全部/新用户/老用户)",
        )


def _write_sheet(ws, headers: List[str], rows: List[List[Any]]) -> None:
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    thin = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
        cell.border = thin
    for r_idx, row in enumerate(rows, 2):
        for c_idx, val in enumerate(row, 1):
            cell = ws.cell(row=r_idx, column=c_idx, value=val)
            cell.border = thin


def _export_xlsx(
    sheets: List[tuple],
    filename: str,
) -> StreamingResponse:
    """sheets: [(sheet_name, headers, rows), ...]"""
    from urllib.parse import quote

    from openpyxl import Workbook

    wb = Workbook()
    first = True
    for name, headers, rows in sheets:
        if first:
            ws = wb.active
            first = False
        else:
            ws = wb.create_sheet()
        ws.title = (name or "data")[:31]
        _write_sheet(ws, headers, rows or [])
    if first:
        wb.active.title = "data"
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    ascii_name = "data_stats.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename)}'
            )
        },
    )


def _sheet_dau(rows: List[dict]) -> tuple:
    headers = ["日期", "应用", "打开设备数", "登录UID数", "日活用户数", "当日新增用户数"]
    data = [
        [
            r["date"],
            r.get("app_key") or "all",
            r["open_devices"],
            r["login_uids"],
            r.get("active_users", 0),
            r["new_users"],
        ]
        for r in rows
    ]
    return "日活统计", headers, data


def _sheet_retention(rows: List[dict]) -> tuple:
    headers = [
        "新增日", "新增用户",
        "D0人数", "D0%",
        "D1人数", "D1%", "D2人数", "D2%", "D3人数", "D3%",
        "D4人数", "D4%", "D5人数", "D5%", "D6人数", "D6%",
        "D7人数", "D7%", "D30人数", "D30%",
    ]
    data = [
        [
            r["cohort_date"], r["new_users"],
            r["d0_count"], r["d0_rate"],
            r["d1_count"], r["d1_rate"],
            r["d2_count"], r["d2_rate"],
            r["d3_count"], r["d3_rate"],
            r["d4_count"], r["d4_rate"],
            r["d5_count"], r["d5_rate"],
            r["d6_count"], r["d6_rate"],
            r["d7_count"], r["d7_rate"],
            r["d30_count"], r["d30_rate"],
        ]
        for r in rows
    ]
    return "留存统计", headers, data


def _sheet_events(rows: List[dict]) -> tuple:
    headers = ["日期", "中文事件名", "英文事件名", "PV", "UV"]
    data = [
        [r["date"], r["event_name_zh"], r["event_name_en"], r["pv"], r["uv"]]
        for r in rows
    ]
    return "事件统计", headers, data


def _sheet_funnel(rows: List[dict]) -> tuple:
    headers = [
        "日期（UTC）",
        "登录用户数",
        "发消息用户数",
        "3轮",
        "5轮",
        "免费用完",
        "付费弹窗",
        "付费点击",
        "充值成功",
        "调研失败打点",
    ]
    data = [
        [
            r["date"],
            r.get("login_users_display"),
            r.get("message_users_display"),
            r.get("turn3_display"),
            r.get("turn5_display"),
            r.get("free_exhausted_display"),
            r.get("pay_popup_display"),
            r.get("pay_click_display"),
            r.get("recharge_display"),
            r.get("survey_fail_display"),
        ]
        for r in rows
    ]
    return "漏斗统计", headers, data


def _sheet_revenue(rows: List[dict]) -> tuple:
    headers = [
        "日期", "当日总充值",
        "全部付费率%", "全部付费人数", "全部用户数", "全部ARPPU", "全部ARPU",
        "新用户付费率%", "新用户付费人数", "新用户数", "新用户ARPPU", "新用户ARPU",
        "老用户付费率%", "老用户付费人数", "老用户数", "老用户ARPPU", "老用户ARPU",
    ]
    data = [
        [
            r["date"], r["total_revenue"],
            r["all_pay_rate"], r["all_paying_users"], r["all_users"], r["all_arppu"], r["all_arpu"],
            r["new_pay_rate"], r["new_paying_users"], r["new_users"], r["new_arppu"], r["new_arpu"],
            r["old_pay_rate"], r["old_paying_users"], r["old_users"], r["old_arppu"], r["old_arpu"],
        ]
        for r in rows
    ]
    return "营收统计", headers, data


_APP_KEY_DESC = "应用马甲：不传/空=trandsai（含历史未打标）；kaya=只看 kaya"


def _collect_all_sheets(
    db,
    start_d,
    end_d,
    *,
    user_type: str,
    event: Optional[str],
    app_key: Optional[str] = None,
) -> List[tuple]:
    return [
        _sheet_dau(compute_dau_rows(db, start_d, end_d, app_key=app_key)),
        _sheet_retention(compute_retention_rows(db, start_d, end_d, app_key=app_key)),
        _sheet_events(
            compute_event_rows(
                db, start_d, end_d, user_type=user_type, event_keyword=event, app_key=app_key
            )
        ),
        _sheet_funnel(compute_funnel_rows(db, start_d, end_d, user_type=user_type, app_key=app_key)),
        _sheet_revenue(compute_revenue_rows(db, start_d, end_d, app_key=app_key)),
    ]


def _export_name(module: str, start: str, end: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"数据统计_{module}_{start}_{end}_{ts}.xlsx"


@router.get("/api/admin/stats/overview", summary="数据统计-顶部卡片")
async def admin_stats_overview(_token: str = Depends(admin_auth_required)):
    with get_db() as db:
        return ok(data=compute_overview(db))


@router.get("/api/admin/stats/filter-options", summary="数据统计-筛选区选项")
async def admin_stats_filter_options(_token: str = Depends(admin_auth_required)):
    """返回用户类型、事件、应用下拉；日期由前端 DatePicker 传 start_date/end_date。"""
    return ok(data=filter_options())


@router.get("/api/admin/stats/dau", summary="数据统计-日活")
async def admin_stats_dau(
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    app_key: Optional[str] = Query(None, description=_APP_KEY_DESC),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=200),
    _token: str = Depends(admin_auth_required),
):
    start_d, end_d = resolve_date_range(start_date, end_date)
    with get_db() as db:
        all_rows = compute_dau_rows(db, start_d, end_d, app_key=app_key)
    items, total, page, page_size = slice_page(all_rows, page, page_size)
    return paginated(items=items, total=total, page=page, page_size=page_size)


@router.get("/api/admin/stats/retention", summary="数据统计-留存")
async def admin_stats_retention(
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    app_key: Optional[str] = Query(None, description=_APP_KEY_DESC),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=200),
    _token: str = Depends(admin_auth_required),
):
    start_d, end_d = resolve_date_range(start_date, end_date)
    with get_db() as db:
        all_rows = compute_retention_rows(db, start_d, end_d, app_key=app_key)
    items, total, page, page_size = slice_page(all_rows, page, page_size)
    return paginated(items=items, total=total, page=page, page_size=page_size)


@router.get("/api/admin/stats/events", summary="数据统计-事件")
async def admin_stats_events(
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    user_type: str = Query("all", description="all|new|old 或 全部|新用户|老用户"),
    event: Optional[str] = Query(None, description="事件英文名精确 / 中文名 / 关键词"),
    app_key: Optional[str] = Query(None, description=_APP_KEY_DESC),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=200),
    _token: str = Depends(admin_auth_required),
):
    start_d, end_d = resolve_date_range(start_date, end_date)
    ut = _parse_user_type(user_type)
    with get_db() as db:
        all_rows = compute_event_rows(
            db, start_d, end_d, user_type=ut, event_keyword=event, app_key=app_key
        )
    items, total, page, page_size = slice_page(all_rows, page, page_size)
    return paginated(items=items, total=total, page=page, page_size=page_size)


@router.get("/api/admin/stats/funnel", summary="数据统计-漏斗")
async def admin_stats_funnel(
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    user_type: str = Query("all", description="all|new|old 或 全部|新用户|老用户"),
    app_key: Optional[str] = Query(None, description=_APP_KEY_DESC),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=200),
    _token: str = Depends(admin_auth_required),
):
    start_d, end_d = resolve_date_range(start_date, end_date)
    ut = _parse_user_type(user_type)
    with get_db() as db:
        all_rows = compute_funnel_rows(
            db, start_d, end_d, user_type=ut, app_key=app_key
        )
    items, total, page, page_size = slice_page(all_rows, page, page_size)
    return paginated(items=items, total=total, page=page, page_size=page_size)


@router.get("/api/admin/stats/revenue", summary="数据统计-营收")
async def admin_stats_revenue(
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    app_key: Optional[str] = Query(None, description=_APP_KEY_DESC),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=200),
    _token: str = Depends(admin_auth_required),
):
    start_d, end_d = resolve_date_range(start_date, end_date)
    with get_db() as db:
        all_rows = compute_revenue_rows(db, start_d, end_d, app_key=app_key)
    items, total, page, page_size = slice_page(all_rows, page, page_size)
    return paginated(items=items, total=total, page=page, page_size=page_size)


@router.get("/api/admin/stats/export", summary="数据统计-导出 Excel（单模块）")
async def admin_stats_export(
    module: str = Query(..., description="dau|retention|events|funnel|revenue|all"),
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    app_key: Optional[str] = Query(None, description=_APP_KEY_DESC),
    user_type: str = Query("all", description="events/funnel: all|new|old"),
    event: Optional[str] = Query(None, description="events: 事件筛选"),
    page: Optional[int] = Query(None, ge=1, description="导出第几页；与 page_size 一起传则按页导出，都不传则全量"),
    page_size: Optional[int] = Query(None, ge=1, le=200, description="每页条数；与 page 一起传则按页导出，都不传则全量"),
    _token: str = Depends(admin_auth_required),
):
    mod = (module or "").strip().lower()
    start_d, end_d = resolve_date_range(start_date, end_date)
    s, e = start_d.strftime("%Y-%m-%d"), end_d.strftime("%Y-%m-%d")
    ut = _parse_user_type(user_type) if mod in ("events", "funnel", "all") else "all"

    def _page_rows(all_rows: List[Any]) -> List[Any]:
        if page is None and page_size is None:
            return all_rows
        items, _total, _p, _ps = slice_page(all_rows, page or 1, page_size or 10)
        return items

    with get_db() as db:
        if mod in ("all", "combined"):
            sheets = _collect_all_sheets(
                db, start_d, end_d, user_type=ut, event=event, app_key=app_key
            )
            return _export_xlsx(sheets, _export_name("全部", s, e))
        if mod == "dau":
            sheet = _sheet_dau(
                _page_rows(compute_dau_rows(db, start_d, end_d, app_key=app_key))
            )
            return _export_xlsx([sheet], _export_name("日活", s, e))
        if mod == "retention":
            sheet = _sheet_retention(
                _page_rows(compute_retention_rows(db, start_d, end_d, app_key=app_key))
            )
            return _export_xlsx([sheet], _export_name("留存", s, e))
        if mod == "events":
            sheet = _sheet_events(
                _page_rows(
                    compute_event_rows(
                        db, start_d, end_d, user_type=ut, event_keyword=event, app_key=app_key
                    )
                )
            )
            return _export_xlsx([sheet], _export_name("事件", s, e))
        if mod == "funnel":
            sheet = _sheet_funnel(
                _page_rows(
                    compute_funnel_rows(db, start_d, end_d, user_type=ut, app_key=app_key)
                )
            )
            return _export_xlsx([sheet], _export_name("漏斗", s, e))
        if mod == "revenue":
            sheet = _sheet_revenue(
                _page_rows(compute_revenue_rows(db, start_d, end_d, app_key=app_key))
            )
            return _export_xlsx([sheet], _export_name("营收", s, e))

    raise HTTPException(status_code=400, detail="invalid_module")


@router.get("/api/admin/stats/export-all", summary="数据统计-五类合一 Excel")
async def admin_stats_export_all(
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    app_key: Optional[str] = Query(None, description=_APP_KEY_DESC),
    user_type: str = Query("all", description="事件/漏斗：all|new|old，默认全部"),
    event: Optional[str] = Query(None, description="仅事件 sheet 可用的事件筛选"),
    _token: str = Depends(admin_auth_required),
):
    """按日期把日活/留存/事件/漏斗/营收打进同一个 xlsx，五个 sheet。全量，不分页。"""
    start_d, end_d = resolve_date_range(start_date, end_date)
    s, e = start_d.strftime("%Y-%m-%d"), end_d.strftime("%Y-%m-%d")
    ut = _parse_user_type(user_type)
    with get_db() as db:
        sheets = _collect_all_sheets(
            db, start_d, end_d, user_type=ut, event=event, app_key=app_key
        )
    return _export_xlsx(sheets, _export_name("全部", s, e))
