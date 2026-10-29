"""管理端：接口访问日志列表 / 下载 / 删除 / 开关。"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from api.admin_common import admin_auth_required, router
from core import api_file_log
from core.response import ok


class ApiLogEnabledBody(BaseModel):
    enabled: bool = Field(..., description="是否写入接口日志")


@router.get("/api/admin/logs", summary="接口日志文件列表")
async def admin_list_api_logs(_token: str = Depends(admin_auth_required)):
    return ok(data=api_file_log.settings())


@router.put("/api/admin/logs/enabled", summary="开关接口日志写入")
async def admin_set_api_log_enabled(
    body: ApiLogEnabledBody,
    _token: str = Depends(admin_auth_required),
):
    if body.enabled and api_file_log.env_forced_off():
        raise HTTPException(
            status_code=400,
            detail="API_LOG_ENABLED=0，环境变量强制关闭，无法打开",
        )
    api_file_log.set_enabled(body.enabled)
    return ok(data=api_file_log.settings())


@router.get("/api/admin/logs/file", summary="下载接口日志文件")
async def admin_download_api_log(
    name: str = Query("api.log", description="文件名，默认当前 api.log"),
    _token: str = Depends(admin_auth_required),
):
    try:
        path = api_file_log.resolve_name(name)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid log file name")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="log file not found")
    return FileResponse(
        path=str(path),
        filename=path.name,
        media_type="text/plain; charset=utf-8",
    )


@router.delete("/api/admin/logs/file", summary="删除一个接口日志文件")
async def admin_delete_api_log(
    name: str = Query(..., description="文件名，例如 api.log"),
    _token: str = Depends(admin_auth_required),
):
    try:
        deleted = api_file_log.delete_file(name)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid log file name")
    if not deleted:
        raise HTTPException(status_code=404, detail="log file not found")
    return ok(data=api_file_log.settings())


@router.delete("/api/admin/logs", summary="删除全部接口日志文件")
async def admin_delete_all_api_logs(_token: str = Depends(admin_auth_required)):
    deleted = api_file_log.delete_all()
    data = api_file_log.settings()
    data["deleted"] = deleted
    return ok(data=data)
