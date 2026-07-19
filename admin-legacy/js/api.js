const API_BASE = window.location.origin;
let adminToken = localStorage.getItem('admin_token') || '';

async function adminFetch(url, opts = {}) {
  opts.headers = opts.headers || {};
  opts.headers['Authorization'] = `Bearer ${adminToken}`;
  const res = await fetch(url, opts);
  if (res.status === 401) {
    localStorage.removeItem('admin_token');
    adminToken = '';
    location.reload();
    return null;
  }
  return res;
}

function escapeHtml(str) {
  if (!str) return '';
  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return str.replace(/[&<>"']/g, m => map[m]);
}

function showToast(text) {
  let el = document.getElementById('toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'toast';
    el.style.cssText = 'position:fixed;top:60px;left:50%;transform:translateX(-50%);background:rgba(0,0,0,0.8);color:#fff;padding:10px 20px;border-radius:20px;font-size:13px;z-index:9999;opacity:0;transition:opacity 0.3s;pointer-events:none;';
    document.body.appendChild(el);
  }
  el.textContent = text;
  el.style.opacity = '1';
  setTimeout(() => el.style.opacity = '0', 2500);
}

function formatDate(iso) {
  if (!iso) return '-';
  const locale = (typeof currentAdminLang !== 'undefined' && currentAdminLang === 'en') ? 'en-US' : 'zh-CN';
  return new Date(iso).toLocaleString(locale);
}

// 通用的客户端分页/过滤工具
function usePagination(items, pageSize = 10) {
  return {
    currentPage: 1,
    pageSize,
    searchQuery: '',

    get filteredItems() {
      if (!this.searchQuery.trim()) return items;
      const q = this.searchQuery.toLowerCase();
      return items.filter(item => JSON.stringify(item).toLowerCase().includes(q));
    },

    get paginatedItems() {
      const start = (this.currentPage - 1) * this.pageSize;
      return this.filteredItems.slice(start, start + this.pageSize);
    },

    get totalPages() {
      return Math.max(1, Math.ceil(this.filteredItems.length / this.pageSize));
    },

    get pages() {
      const pages = [];
      for (let i = 1; i <= this.totalPages; i++) pages.push(i);
      return pages;
    },

    goToPage(p) {
      if (p >= 1 && p <= this.totalPages) this.currentPage = p;
    },

    prevPage() { this.goToPage(this.currentPage - 1); },
    nextPage() { this.goToPage(this.currentPage + 1); },
  };
}
