document.addEventListener('alpine:init', () => {
  Alpine.data('usersPage', () => ({
    users: [],
    loading: true,
    searchQuery: '',
    currentPage: 1,
    pageSize: 10,

    async init() {
      await this.loadUsers();
    },

    get filteredUsers() {
      if (!this.searchQuery.trim()) return this.users;
      const q = this.searchQuery.toLowerCase();
      return this.users.filter(u =>
        (u.username && u.username.toLowerCase().includes(q)) ||
        (u.nickname && u.nickname.toLowerCase().includes(q))
      );
    },

    get paginatedUsers() {
      const start = (this.currentPage - 1) * this.pageSize;
      return this.filteredUsers.slice(start, start + this.pageSize);
    },

    get totalPages() {
      return Math.max(1, Math.ceil(this.filteredUsers.length / this.pageSize));
    },

    get pages() {
      const arr = [];
      for (let i = 1; i <= this.totalPages; i++) arr.push(i);
      return arr;
    },

    goToPage(p) {
      if (p >= 1 && p <= this.totalPages) this.currentPage = p;
    },

    async loadUsers() {
      this.loading = true;
      try {
        const res = await adminFetch(`${API_BASE}/api/admin/users`);
        if (!res) return;
        this.users = await res.json();
      } catch (e) {
        console.error(e);
      } finally {
        this.loading = false;
      }
    }
  }));
});
