"""语言路由与单语提示词包。一次请求只加载一份 pack。"""

from services.prompts.resolve import SUPPORTED_LANGS, get_pack, pack_lang, resolve_lang

__all__ = ["SUPPORTED_LANGS", "get_pack", "pack_lang", "resolve_lang"]
