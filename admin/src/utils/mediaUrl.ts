/**
 * 开发时管理端在 5174、API 在 8000；后端返回的 /data/images/... 应指向 API 根，否则 <img> 会请求 5174 而 404 或错目录。
 * 生产与 API 同域时 VITE_API_BASE_URL 未设置，使用相对路径即可。
 */
const API_PREFIX = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? '';

export function resolveAdminMediaUrl(url: string | null | undefined): string {
  if (url == null) return '';
  const s = String(url).trim();
  if (!s) return '';
  if (s.startsWith('http://') || s.startsWith('https://') || s.startsWith('data:') || s.startsWith('blob:')) {
    return s;
  }
  if (s.startsWith('/')) {
    return API_PREFIX ? `${API_PREFIX}${s}` : s;
  }
  return API_PREFIX ? `${API_PREFIX}/${s}` : `/${s}`;
}
