import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_cos_client = None

# 上传去重缓存：避免同一文件重复上传（key=cos_key, value=url）
_upload_cache: dict[str, str] = {}

# 支持的图片 MIME 类型
_MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}


def _get_cos_client():
    """懒加载 COS 客户端"""
    global _cos_client
    if _cos_client is not None:
        return _cos_client

    secret_id = os.environ.get("COS_SECRET_ID", "")
    secret_key = os.environ.get("COS_SECRET_KEY", "")
    if not secret_id or not secret_key:
        logger.warning("COS 未配置 (缺少 COS_SECRET_ID 或 COS_SECRET_KEY)，将使用本地存储")
        return None

    try:
        from qcloud_cos import CosConfig, CosS3Client
        region = os.environ.get("COS_REGION", "ap-beijing")
        config = CosConfig(Region=region, SecretId=secret_id, SecretKey=secret_key)
        _cos_client = CosS3Client(config)
        logger.info("COS 客户端初始化成功 (region=%s, bucket=%s)", region, os.environ.get("COS_BUCKET", ""))
        return _cos_client
    except ImportError:
        logger.error("COS SDK 未安装，请运行: pip install cos-python-sdk-v5")
        return None
    except Exception as e:
        logger.warning("COS 客户端初始化失败: %s", e)
        return None


def get_cos_domain() -> str:
    """获取 COS 访问域名（返回 URL 前缀，不含协议）"""
    return os.environ.get("COS_DOMAIN", "")


def is_cos_enabled() -> bool:
    """检查 COS 是否已配置"""
    return _get_cos_client() is not None


def _get_mime_type(file_path: str) -> str:
    """根据文件扩展名获取 MIME 类型"""
    ext = Path(file_path).suffix.lower()
    return _MIME_MAP.get(ext, "application/octet-stream")


def _build_cos_url(cos_key: str) -> str:
    """根据配置构建 COS 公开访问 URL"""
    domain = get_cos_domain()
    if domain:
        return f"https://{domain}/{cos_key}"
    bucket = os.environ.get("COS_BUCKET", "")
    region = os.environ.get("COS_REGION", "ap-beijing")
    return f"https://{bucket}.cos.{region}.myqcloud.com/{cos_key}"


def upload_file_to_cos(local_path: str, cos_key: str) -> str | None:
    """上传本地文件到 COS，返回公开访问 URL。

    带去重缓存：同一 cos_key 不会重复上传。
    """
    # 去重：已上传过则直接返回缓存的 URL
    if cos_key in _upload_cache:
        return _upload_cache[cos_key]

    client = _get_cos_client()
    if not client:
        return None

    bucket = os.environ.get("COS_BUCKET", "")
    if not bucket:
        logger.warning("COS_BUCKET 未配置，无法上传文件")
        return None

    # 检查本地文件是否存在
    if not os.path.isfile(local_path):
        logger.warning("COS 上传失败：本地文件不存在 %s", local_path)
        return None

    file_size = os.path.getsize(local_path)
    content_type = _get_mime_type(local_path)

    try:
        with open(local_path, "rb") as f:
            client.put_object(
                Bucket=bucket,
                Body=f,
                Key=cos_key,
                EnableMD5=False,
                ContentType=content_type,
            )

        url = _build_cos_url(cos_key)
        logger.info("COS 上传成功: %s (%s, %.1fKB)", cos_key, content_type, file_size / 1024)

        # 缓存成功结果
        _upload_cache[cos_key] = url
        return url
    except ImportError:
        logger.error("COS SDK 未安装，跳过上传: %s", local_path)
        return None
    except Exception as e:
        logger.error("COS 上传失败 [%s]: %s", local_path, e)
        return None


def upload_bytes_to_cos(data: bytes, cos_key: str) -> str | None:
    """上传字节数据到 COS，返回公开访问 URL。"""
    # 去重
    if cos_key in _upload_cache:
        return _upload_cache[cos_key]

    client = _get_cos_client()
    if not client:
        return None

    bucket = os.environ.get("COS_BUCKET", "")
    if not bucket:
        return None

    try:
        client.put_object(
            Bucket=bucket,
            Body=data,
            Key=cos_key,
        )

        url = _build_cos_url(cos_key)
        logger.info("COS 上传成功: %s (%.1fKB)", cos_key, len(data) / 1024)

        _upload_cache[cos_key] = url
        return url
    except ImportError:
        logger.error("COS SDK 未安装，跳过上传: %s", cos_key)
        return None
    except Exception as e:
        logger.error("COS 上传失败 [%s]: %s", cos_key, e)
        return None


def delete_cos_object(cos_key: str) -> bool:
    """删除 COS 上的对象"""
    client = _get_cos_client()
    if not client:
        return False

    bucket = os.environ.get("COS_BUCKET", "")
    try:
        client.delete_object(Bucket=bucket, Key=cos_key)
        # 清除上传缓存
        _upload_cache.pop(cos_key, None)
        logger.info("COS 删除成功: %s", cos_key)
        return True
    except Exception as e:
        logger.warning("COS 删除失败 [%s]: %s", cos_key, e)
        return False
