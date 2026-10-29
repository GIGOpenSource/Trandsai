"""
App Store Connect API 服务

功能：
- 生成 App Store Connect API JWT Token
- 获取应用内购买商品的价格档位
- 同步各地区价格到数据库

配置环境变量：
- APP_STORE_KEY_ID: App Store Connect API Key ID
- APP_STORE_ISSUER_ID: Issuer ID (UUID)
- APP_STORE_PRIVATE_KEY_PATH: .p8 私钥文件路径（或直接设置 APP_STORE_PRIVATE_KEY 内容）
"""

import json
import logging
import os
import time
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# ===== 配置 =====
APP_STORE_KEY_ID = os.getenv("APP_STORE_KEY_ID", "")
APP_STORE_ISSUER_ID = os.getenv("APP_STORE_ISSUER_ID", "")
APP_STORE_PRIVATE_KEY_PATH = os.getenv("APP_STORE_PRIVATE_KEY_PATH", "")
APP_STORE_PRIVATE_KEY = os.getenv("APP_STORE_PRIVATE_KEY", "")

# API 基础 URL
APP_STORE_API_BASE = "https://api.appstoreconnect.apple.com/v1"

# Token 缓存
_jwt_token: Optional[str] = None
_jwt_token_expires_at: float = 0


def _load_private_key() -> Optional[str]:
    """加载私钥内容"""
    # 优先从环境变量读取
    if APP_STORE_PRIVATE_KEY:
        return APP_STORE_PRIVATE_KEY

    # 从文件读取
    if APP_STORE_PRIVATE_KEY_PATH and os.path.exists(APP_STORE_PRIVATE_KEY_PATH):
        try:
            with open(APP_STORE_PRIVATE_KEY_PATH, "r") as f:
                return f.read()
        except Exception as e:
            logger.error("[appstore] 读取私钥文件失败: %s", e)
            return None

    return None


def _generate_jwt_token() -> Optional[str]:
    """生成 App Store Connect API JWT Token"""
    global _jwt_token, _jwt_token_expires_at

    # 检查缓存
    if _jwt_token and time.time() < _jwt_token_expires_at:
        return _jwt_token

    if not APP_STORE_KEY_ID or not APP_STORE_ISSUER_ID:
        logger.error("[appstore] APP_STORE_KEY_ID 或 APP_STORE_ISSUER_ID 未配置")
        return None

    private_key = _load_private_key()
    if not private_key:
        logger.error("[appstore] 私钥未配置")
        return None

    try:
        import jwt

        now = int(time.time())
        payload = {
            "iss": APP_STORE_ISSUER_ID,
            "iat": now,
            "exp": now + 1200,  # 20 分钟过期
            "aud": "appstoreconnect-v1",
        }

        _jwt_token = jwt.encode(
            payload,
            private_key,
            algorithm="ES256",
            headers={"kid": APP_STORE_KEY_ID},
        )
        _jwt_token_expires_at = now + 1100  # 提前 100 秒刷新

        return _jwt_token

    except ImportError:
        logger.error("[appstore] 需要安装 PyJWT: pip install PyJWT[crypto]")
        return None
    except Exception as e:
        logger.error("[appstore] 生成 JWT 失败: %s", e)
        return None


def _api_request(endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
    """发送 App Store Connect API 请求"""
    token = _generate_jwt_token()
    if not token:
        return None

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    url = f"{APP_STORE_API_BASE}/{endpoint}"

    try:
        resp = requests.get(url, headers=headers, params=params or {}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        logger.error("[appstore] API 请求失败 (%s): %s", url, e)
        return None


def get_in_app_purchase_prices(iap_id: str) -> List[Dict]:
    """
    获取应用内购买商品的价格档位

    参数:
        iap_id: In-App Purchase ID（如 "com.app.vip.yearly"）

    返回:
        [
            {
                "territory": "CN",
                "currency": "CNY",
                "price": 598.0,
                "price_tier": 50,
            },
            ...
        ]
    """
    # 首先需要获取 IAP 的内部 ID
    # 通过搜索 product_id 获取
    result = _api_request(
        "inAppPurchases",
        params={
            "filter[productId]": iap_id,
            "limit": 1,
        }
    )

    if not result or not result.get("data"):
        logger.warning("[appstore] 未找到 IAP: %s", iap_id)
        return []

    iap_internal_id = result["data"][0]["id"]

    # 获取价格档位
    prices_data = _api_request(
        f"inAppPurchases/{iap_internal_id}/pricePoints",
        params={
            "limit": 200,
            "fields[inAppPurchasePricePoints]": "customerPrice,equalizations,proceeds,territory",
            "include": "territory",
        }
    )

    if not prices_data:
        return []

    # 解析价格数据
    prices = []
    territories_map = {}

    # 构建 territory 映射
    if "included" in prices_data:
        for item in prices_data["included"]:
            if item["type"] == "territories":
                territories_map[item["id"]] = {
                    "currency": item["attributes"].get("currency", ""),
                }

    for price_point in prices_data.get("data", []):
        attrs = price_point.get("attributes", {})
        territory_rel = price_point.get("relationships", {}).get("territory", {}).get("data", {})

        territory_code = territory_rel.get("id", "")
        currency = territories_map.get(territory_code, {}).get("currency", "")

        # customerPrice 是客户支付的价格（字符串，如 "598.00"）
        customer_price = attrs.get("customerPrice", "0")

        prices.append({
            "territory": territory_code,
            "currency": currency,
            "price": float(customer_price) if customer_price else 0,
        })

    return prices


def get_subscription_prices(subscription_id: str) -> List[Dict]:
    """
    获取订阅商品的价格计划

    参数:
        subscription_id: 订阅 ID（如 "com.app.vip.yearly"）

    返回各地区价格列表
    """
    # 搜索订阅
    result = _api_request(
        "subscriptions",
        params={
            "filter[productId]": subscription_id,
            "limit": 1,
        }
    )

    if not result or not result.get("data"):
        logger.warning("[appstore] 未找到订阅: %s", subscription_id)
        return []

    sub_internal_id = result["data"][0]["id"]

    # 获取订阅价格
    prices_data = _api_request(
        f"subscriptions/{sub_internal_id}/subscriptionPrices",
        params={
            "limit": 200,
            "include": "territory",
        }
    )

    if not prices_data:
        return []

    prices = []
    territories_map = {}

    if "included" in prices_data:
        for item in prices_data["included"]:
            if item["type"] == "territories":
                territories_map[item["id"]] = {
                    "currency": item["attributes"].get("currency", ""),
                }

    for price_entry in prices_data.get("data", []):
        attrs = price_entry.get("attributes", {})
        territory_rel = price_entry.get("relationships", {}).get("territory", {}).get("data", {})

        territory_code = territory_rel.get("id", "")
        currency = territories_map.get(territory_code, {}).get("currency", "")
        price = attrs.get("customerPrice", "0")

        prices.append({
            "territory": territory_code,
            "currency": currency,
            "price": float(price) if price else 0,
        })

    return prices


def sync_product_prices(product_id: int, apple_product_id: str) -> Dict:
    """
    同步单个商品的各地区价格到数据库

    参数:
        product_id: 数据库中的商品 ID
        apple_product_id: Apple 商品 ID（如 "com.app.vip.yearly"）

    返回:
        {"ok": True, "synced": 45, "prices": [...]}
    """
    from core.database import ProductPriceORM, get_db

    # 获取价格
    prices = get_in_app_purchase_prices(apple_product_id)

    if not prices:
        # 尝试作为订阅获取
        prices = get_subscription_prices(apple_product_id)

    if not prices:
        return {"ok": False, "reason": "no_prices_found", "synced": 0}

    # 写入数据库
    synced = 0
    with get_db() as db:
        # 删除旧价格
        db.query(ProductPriceORM).filter(
            ProductPriceORM.product_id == product_id
        ).delete()

        # 插入新价格
        for price_info in prices:
            price_record = ProductPriceORM(
                product_id=product_id,
                territory=price_info["territory"],
                currency=price_info["currency"],
                price=price_info["price"],
            )
            db.add(price_record)
            synced += 1

        db.commit()

    logger.info("[appstore] 同步价格完成: product_id=%d, 数量=%d", product_id, synced)

    return {
        "ok": True,
        "synced": synced,
        "prices": prices,
    }


def get_local_price(product_id: int, territory: str) -> Optional[Dict]:
    """
    获取商品在指定地区的本地价格

    参数:
        product_id: 数据库中的商品 ID
        territory: 地区代码（如 "CN", "US", "JP"）

    返回:
        {"price": 598.0, "currency": "CNY"} 或 None
    """
    from core.database import ProductPriceORM, get_db

    with get_db() as db:
        # 先查指定地区
        price_record = db.query(ProductPriceORM).filter(
            ProductPriceORM.product_id == product_id,
            ProductPriceORM.territory == territory.upper(),
        ).first()

        if price_record:
            return {
                "price": price_record.price,
                "currency": price_record.currency,
            }

        # 回退到 US 价格
        price_record = db.query(ProductPriceORM).filter(
            ProductPriceORM.product_id == product_id,
            ProductPriceORM.territory == "US",
        ).first()

        if price_record:
            return {
                "price": price_record.price,
                "currency": price_record.currency,
            }

    return None
