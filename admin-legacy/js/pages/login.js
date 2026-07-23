document.addEventListener('alpine:init', () => {
  Alpine.data('loginPage', () => ({
    password: '',
    error: '',
    loading: false,

    async doLogin() {
      this.error = '';
      if (!this.password) {
        this.error = t('login.error.empty');
        return;
      }
      this.loading = true;
      try {
        const res = await fetch(`${API_BASE}/api/admin/login`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password: this.password }),
        });
        const data = await res.json();
        if (data.token) {
          this.$store.admin.login(data.token);
          location.reload();
        } else {
          this.error = t('login.error.wrong');
        }
      } catch (e) {
        this.error = t('login.error.failed');
      } finally {
        this.loading = false;
      }
    }
  }));
});
