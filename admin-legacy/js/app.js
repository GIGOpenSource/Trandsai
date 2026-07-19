document.addEventListener('alpine:init', () => {
  Alpine.data('adminApp', () => ({
    get page() {
      return this.$store.admin.currentPage;
    },
    init() {
      // 根应用初始化，页面切换由 store 管理
    }
  }));
});
