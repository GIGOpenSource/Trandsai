document.addEventListener('alpine:init', () => {
  Alpine.data('knowledgePage', () => ({
    items: [],
    loading: true,
    searchQuery: '',
    searchResults: [],
    searching: false,
    currentPage: 1,
    pageSize: 10,

    async init() {
      await this.loadKnowledge();
    },

    get filteredItems() {
      if (!this.searchQuery.trim()) return this.items;
      const q = this.searchQuery.toLowerCase();
      return this.items.filter(item =>
        (item.title && item.title.toLowerCase().includes(q)) ||
        (item.content && item.content.toLowerCase().includes(q))
      );
    },

    get paginatedItems() {
      const start = (this.currentPage - 1) * this.pageSize;
      return this.filteredItems.slice(start, start + this.pageSize);
    },

    get totalPages() {
      return Math.max(1, Math.ceil(this.filteredItems.length / this.pageSize));
    },

    get pages() {
      const arr = [];
      for (let i = 1; i <= this.totalPages; i++) arr.push(i);
      return arr;
    },

    goToPage(p) {
      if (p >= 1 && p <= this.totalPages) this.currentPage = p;
    },

    async loadKnowledge() {
      this.loading = true;
      try {
        const res = await adminFetch(`${API_BASE}/api/admin/knowledge`);
        if (!res) return;
        this.items = await res.json();
      } catch (e) {
        console.error(e);
      } finally {
        this.loading = false;
      }
    },

    async searchKnowledge() {
      const query = this.searchQuery.trim();
      if (!query) return;
      this.searching = true;
      this.searchResults = [];
      try {
        const res = await adminFetch(`${API_BASE}/api/admin/knowledge/search`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query, top_k: 10 }),
        });
        if (!res) return;
        const data = await res.json();
        this.searchResults = data.results || [];
      } catch (e) {
        console.error(e);
      } finally {
        this.searching = false;
      }
    },

    async deleteKnowledge(id) {
      if (!confirm(t('confirm.deleteMoment'))) return;
      try {
        await adminFetch(`${API_BASE}/api/admin/knowledge/${id}`, { method: 'DELETE' });
        await this.loadKnowledge();
      } catch (e) {
        console.error(e);
      }
    }
  }));
});
