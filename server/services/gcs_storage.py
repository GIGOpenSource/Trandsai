"""Google Cloud Storage 后端实现。

通过 cos_storage.py 路由调用，对外接口保持一致。
"""
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_gcs_client = None

# MIME 类型映射
_MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}

# 上传去重缓存
_upload_cache: dict[str, str] = {}


def _get_gcs_client():
    """懒加载 GCS 客户端"""
    global _gcs_client
    if _gcs_client is not None:
        return _gcs_client

    try:
        from google.cloud import storage
    except ImportError:
        logger.error("GCS SDK 未安装，请运行: pip install google-cloud-storage")
        return None

    run_mode = os.environ.get("RUN_MODE", "2").strip()
    if run_mode == "1":
        creds_path = os.environ.get("GCS_CREDENTIALS_PROD", "").strip()
    else:
        creds_path = os.environ.get("GCS_CREDENTIALS_LOCAL", "").strip()
    logger.info("[GCS-DEBUG] RUN_MODE=%s, creds_path='%s', isfile=%s",
                run_mode, creds_path, os.path.isfile(creds_path) if creds_path else "N/A")
    if not creds_path:
        logger.warning("[GCS-DEBUG] GCS_CREDENTIALS 路径未配置 (RUN_MODE=%s)", run_mode)
    elif not os.path.isfile(creds_path):
        logger.warning("[GCS-DEBUG] 文件不存在: %s", creds_path)
    try:
        if creds_path and os.path.isfile(creds_path):
            _gcs_client = storage.Client.from_service_account_json(creds_path)
        else:
            # 使用 Application Default Credentials (环境变量 GOOGLE_APPLICATION_CREDENTIALS)
            logger.warning("[GCS-DEBUG] 回退到 ADC (Application Default Credentials)")
            _gcs_client = storage.Client()
        bucket_name = os.environ.get("GCS_BUCKET", "")
        logger.info("GCS 客户端初始化成功 (bucket=%s, creds=%s)",
                     bucket_name, creds_path or "ADC")
        return _gcs_client
    except Exception as e:
        logger.error("GCS 客户端初始化失败: %s", e)
        return None


def _get_bucket():
    """获取 GCS Bucket 对象"""
    client = _get_gcs_client()
    if not client:
        return None
    bucket_name = os.environ.get("GCS_BUCKET", "")
    if not bucket_name:
        logger.warning("GCS_BUCKET 未配置")
        return None
    return client.bucket(bucket_name)


def _get_mime_type(file_path: str) -> str:
    ext = Path(file_path).suffix.lower()
    return _MIME_MAP.get(ext, "application/octet-stream")


def _build_public_url(gcs_key: str) -> str:
    """构建公开访问 URL"""
    custom_domain = os.environ.get("GCS_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if custom_domain:
        return f"{custom_domain}/{gcs_key}"
    bucket_name = os.environ.get("GCS_BUCKET", "")
    return f"https://storage.googleapis.com/{bucket_name}/{gcs_key}"


def is_enabled() -> bool:
    """检查 GCS 是否可用"""
    return _get_gcs_client() is not None


def upload_file(local_path: str, gcs_key: str) -> str | None:
    """上传本地文件到 GCS，返回公开访问 URL。"""
    if gcs_key in _upload_cache:
        return _upload_cache[gcs_key]

    bucket = _get_bucket()
    if not bucket:
        return None

    if not os.path.isfile(local_path):
        logger.warning("GCS 上传失败：本地文件不存在 %s", local_path)
        return None

    file_size = os.path.getsize(local_path)
    content_type = _get_mime_type(local_path)

    try:
        blob = bucket.blob(gcs_key)
        blob.upload_from_filename(local_path, content_type=content_type)
        # Uniform Bucket-Level Access 模式下，公开读取由桶级 IAM 控制（allUsers → Storage Object Viewer）
        # 不调用 make_public()，它会触发 legacy ACL 报错

        url = _build_public_url(gcs_key)
        logger.info("GCS 上传成功: %s (%s, %.1fKB)", gcs_key, content_type, file_size / 1024)
        _upload_cache[gcs_key] = url
        return url
    except Exception as e:
        logger.error("GCS 上传失败 [%s]: %s", local_path, e)
        return None


def upload_bytes(data: bytes, gcs_key: str) -> str | None:
    """上传字节数据到 GCS，返回公开访问 URL。"""
    if gcs_key in _upload_cache:
        return _upload_cache[gcs_key]

    bucket = _get_bucket()
    if not bucket:
        return None

    content_type = _get_mime_type(gcs_key)

    try:
        blob = bucket.blob(gcs_key)
        blob.upload_from_string(data, content_type=content_type)

        url = _build_public_url(gcs_key)
        logger.info("GCS 上传成功: %s (%.1fKB)", gcs_key, len(data) / 1024)
        _upload_cache[gcs_key] = url
        return url
    except Exception as e:
        logger.error("GCS 上传失败 [%s]: %s", gcs_key, e)
        return None


def delete_object(gcs_key: str) -> bool:
    """删除 GCS 上的对象"""
    bucket = _get_bucket()
    if not bucket:
        return False
    try:
        blob = bucket.blob(gcs_key)
        blob.delete()
        _upload_cache.pop(gcs_key, None)
        logger.info("GCS 删除成功: %s", gcs_key)
        return True
    except Exception as e:
        logger.warning("GCS 删除失败 [%s]: %s", gcs_key, e)
        return False
