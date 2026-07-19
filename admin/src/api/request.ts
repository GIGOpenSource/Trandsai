import { message } from 'antd';

const API_BASE = '';

let adminToken = localStorage.getItem('admin_token') || '';

export function getToken() {
  return adminToken;
}

export function setToken(token: string) {
  adminToken = token;
  localStorage.setItem('admin_token', token);
}

export function clearToken() {
  adminToken = '';
  localStorage.removeItem('admin_token');
}

function getLanguageHeader(): string {
  const saved = localStorage.getItem('admin_lang') || 'zh';
  const map: Record<string, string> = {
    zh: 'zh-CN',
    en: 'en-US',
  };
  return map[saved] || saved;
}

export async function adminFetch(url: string, opts: RequestInit = {}) {
  opts.headers = opts.headers || {};
  (opts.headers as Record<string, string>)['Authorization'] = `Bearer ${adminToken}`;
  (opts.headers as Record<string, string>)['Accept-Language'] = getLanguageHeader();
  const res = await fetch(`${API_BASE}${url}`, opts);
  if (res.status === 401) {
    clearToken();
    window.location.hash = '#/login';
    return null;
  }
  return res;
}

export async function adminFetchJson<T = unknown>(url: string, opts: RequestInit = {}): Promise<T | null> {
  const res = await adminFetch(url, opts);
  if (!res) return null;
  if (!res.ok) {
    let errorMsg = `HTTP ${res.status}`;
    try {
      // 支持后端 {error: "..."} 或 {detail: "..."} 格式，解决不一致问题
      const errorData = await res.json();
      errorMsg = errorData.error || errorData.detail || JSON.stringify(errorData) || errorMsg;
    } catch {
      try {
        const text = await res.text();
        errorMsg = text || errorMsg;
      } catch {
        // keep default fallback error message
      }
    }
    throw new Error(errorMsg);
  }
  return res.json() as Promise<T>;
}

export function showSuccess(text: string) {
  message.success(text);
}

export function showError(text: string) {
  message.error(text);
}

export function formatDate(iso: string | null | undefined, locale: string = 'zh-CN') {
  if (!iso) return '-';
  return new Date(iso).toLocaleString(locale === 'zh' ? 'zh-CN' : 'en-US');
}
