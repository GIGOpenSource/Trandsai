document.addEventListener('alpine:init', () => {
  Alpine.store('admin', {
    token: localStorage.getItem('admin_token') || '',
    currentPage: 'dashboard',
    isLoggedIn: false,
    lang: localStorage.getItem('admin_lang') || 'zh',

    init() {
      this.isLoggedIn = !!this.token;
      if (this.token) {
        adminToken = this.token;
      }
      setAdminLang(this.lang);
    },

    login(token) {
      this.token = token;
      this.isLoggedIn = true;
      localStorage.setItem('admin_token', token);
      adminToken = token;
    },

    logout() {
      this.token = '';
      this.isLoggedIn = false;
      localStorage.removeItem('admin_token');
      adminToken = '';
    },

    navigate(page) {
      this.currentPage = page;
    },

    setLang(lang) {
      this.lang = lang;
      setAdminLang(lang);
      location.reload();
    }
  });
});
