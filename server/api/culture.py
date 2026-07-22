from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from services.culture_data import get_cities, get_random_names

router = APIRouter(tags=["文化数据"])


@router.get("/api/culture/names", summary="获取随机姓名列表")
async def api_culture_names(
    lang: str = Query("zh", description="语言代码"),
    gender: str = Query("female", description="性别: male / female"),
    count: int = Query(5, ge=1, le=20, description="返回数量"),
):
    """获取符合当地文化的随机姓名列表"""
    names = get_random_names(lang, gender, count=count)
    return {"names": names, "lang": lang, "gender": gender}


@router.get("/api/culture/cities", summary="获取城市列表")
async def api_culture_cities(
    lang: str = Query("zh", description="语言代码"),
):
    """获取对应语言的典型城市列表"""
    cities = get_cities(lang)
    return {"cities": cities, "lang": lang}
