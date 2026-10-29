"""
@ai-readonly — DO NOT MODIFY THIS FILE WITHOUT EXPLICIT USER REQUEST.

统一接口响应工具

用法:
    from core.response import ok, fail, paginated

    # 成功（无数据）
    return ok()

    # 成功（带数据）
    return ok(data={"name": "test"})

    # 成功（带消息）
    return ok(data={"id": 1}, message="创建成功")

    # 失败
    return fail(message="参数错误", code=400)

    # 分页
    return paginated(items=list, total=100, page=1, page_size=20)
"""

from math import ceil
from typing import Any, Dict, List, Optional

from fastapi.responses import JSONResponse


def ok(
    data: Any = None,
    message: str = "success",
    code: int = 200,
) -> JSONResponse:
    """
    成功响应（非分页）

    返回格式:
    {
        "code": 200,
        "message": "success",
        "results": {...}
    }
    """
    return JSONResponse(
        status_code=200,
        content={
            "code": code,
            "message": message,
            "results": data if data is not None else {},
        },
    )


def fail(
    message: str = "error",
    code: int = 400,
    data: Any = None,
) -> JSONResponse:
    """
    失败响应

    返回格式:
    {
        "code": 400,
        "message": "error",
        "results": {}
    }
    """
    return JSONResponse(
        content={
            "code": code,
            "message": message,
            "results": data if data is not None else {},
        },
    )


def paginated(
    items: List[Any],
    total: int,
    page: int = 1,
    page_size: int = 20,
    message: str = "success",
    code: int = 200,
) -> JSONResponse:
    """
    分页响应

    返回格式:
    {
        "code": 200,
        "message": "success",
        "results": [...],
        "pagination": {
            "page": 1,
            "page_size": 20,
            "total": 100,
            "total_pages": 5
        }
    }
    """
    total_pages = ceil(total / page_size) if page_size > 0 else 0
    return JSONResponse(
        status_code=200,
        content={
            "code": code,
            "message": message,
            "results": items,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
            },
        },
    )
