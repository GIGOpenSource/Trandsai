document.addEventListener('alpine:init', () => {
  Alpine.data('dashboardPage', () => ({
    stats: { companion_count: 0, total_turns: 0, avg_affection: 0 },
    loading: true,

    async init() {
      await this.loadStats();
    },

    async loadStats() {
      this.loading = true;
      try {
        const res = await adminFetch(`${API_BASE}/api/admin/stats`);
        if (!res) return;
        this.stats = await res.json();
      } catch (e) {
        console.error(e);
      } finally {
        this.loading = false;
      }
    }
  }));
});
