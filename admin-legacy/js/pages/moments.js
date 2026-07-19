document.addEventListener('alpine:init', () => {
  Alpine.data('momentsPage', () => ({
    moments: [],
    loading: true,
    searchQuery: '',
    currentPage: 1,
    pageSize: 10,

    // 图片预览
    previewImage: null,
    previewCaption: '',

    async init() {
      await this.loadMoments();
    },

    get filteredMoments() {
      if (!this.searchQuery.trim()) return this.moments;
      const q = this.searchQuery.toLowerCase();
      return this.moments.filter(m =>
        (m.caption && m.caption.toLowerCase().includes(q)) ||
        (m.companion_id && m.companion_id.toLowerCase().includes(q))
      );
    },

    get paginatedMoments() {
      const start = (this.currentPage - 1) * this.pageSize;
      return this.filteredMoments.slice(start, start + this.pageSize);
    },

    get totalPages() {
      return Math.max(1, Math.ceil(this.filteredMoments.length / this.pageSize));
    },

    get pages() {
      const arr = [];
      for (let i = 1; i <= this.totalPages; i++) arr.push(i);
      return arr;
    },

    goToPage(p) {
      if (p >= 1 && p <= this.totalPages) this.currentPage = p;
    },

    async loadMoments() {
      this.loading = true;
      try {
        const res = await adminFetch(`${API_BASE}/api/admin/moments?limit=200`);
        if (!res) return;
        const data = await res.json();
        this.moments = data.moments || [];
      } catch (e) {
        console.error(e);
      } finally {
        this.loading = false;
      }
    },

    async deleteMoment(id) {
      if (!confirm(t('confirm.deleteMoment'))) return;
      try {
        await adminFetch(`${API_BASE}/api/admin/moments/${id}`, { method: 'DELETE' });
        await this.loadMoments();
      } catch (e) {
        console.error(e);
      }
    },

    openPreview(url, caption) {
      this.previewImage = url;
      this.previewCaption = caption || '';
    },

    closePreview() {
      this.previewImage = null;
      this.previewCaption = '';
    },

    async regenerateImage(id) {
      showToast(t('toast.regenerating'));
      try {
        const res = await adminFetch(`${API_BASE}/api/admin/moments/${id}/regenerate-image`, {
          method: 'POST'
        });
        if (res && res.ok) {
          showToast(t('toast.regenerateSuccess'));
          await this.loadMoments();
        } else {
          showToast(t('toast.regenerateFailed'));
        }
      } catch (e) {
        showToast(t('toast.regenerateFailed'));
        console.error(e);
      }
    },

    async clearAllMoments() {
      if (!confirm(t('confirm.clearAllMoments'))) return;
      try {
        const res = await adminFetch(`${API_BASE}/api/admin/moments`, { method: 'DELETE' });
        if (res && res.ok) {
          const data = await res.json();
          showToast(t('toast.clearSuccess') + ': ' + (data.deleted || 0));
          await this.loadMoments();
        } else {
          showToast(t('toast.clearFailed'));
        }
      } catch (e) {
        showToast(t('toast.clearFailed'));
        console.error(e);
      }
    },

    async regenerateAllMoments() {
      if (!confirm(t('confirm.regenerateAll'))) return;
      showToast(t('toast.regenerating'));
      try {
        const res = await adminFetch(`${API_BASE}/api/admin/moments/regenerate`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ moments_per_companion: 3 })
        });
        if (res && res.ok) {
          const data = await res.json();
          showToast(t('toast.regenerateSuccess') + ': ' + (data.created || 0));
          await this.loadMoments();
        } else {
          showToast(t('toast.regenerateFailed'));
        }
      } catch (e) {
        showToast(t('toast.regenerateFailed'));
        console.error(e);
      }
    }
  }));
});
