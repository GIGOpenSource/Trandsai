document.addEventListener('alpine:init', () => {
  Alpine.data('companionsPage', () => ({
    companions: [],
    loading: true,
    searchQuery: '',
    currentPage: 1,
    pageSize: 10,

    // 编辑弹窗
    editing: false,
    editId: '',
    editForm: {
      name: '', gender: '女', age: 24, city: '',
      personality: '', background: '', speech_style: '',
      hobbies: '', values: '', fears: '', love_view: '',
      daily_routine: '', favorite_things: ''
    },
    editPrompts: { zh: '', en: '', ja: '', ko: '', pt: '', es: '', id: '' },
    showPrompts: false,

    async init() {
      await this.loadCompanions();
    },

    get filteredCompanions() {
      if (!this.searchQuery.trim()) return this.companions;
      const q = this.searchQuery.toLowerCase();
      return this.companions.filter(c =>
        (c.profile.name && c.profile.name.toLowerCase().includes(q)) ||
        (c.profile.city && c.profile.city.toLowerCase().includes(q))
      );
    },

    get paginatedCompanions() {
      const start = (this.currentPage - 1) * this.pageSize;
      return this.filteredCompanions.slice(start, start + this.pageSize);
    },

    get totalPages() {
      return Math.max(1, Math.ceil(this.filteredCompanions.length / this.pageSize));
    },

    get pages() {
      const arr = [];
      for (let i = 1; i <= this.totalPages; i++) arr.push(i);
      return arr;
    },

    goToPage(p) {
      if (p >= 1 && p <= this.totalPages) this.currentPage = p;
    },

    async loadCompanions() {
      this.loading = true;
      try {
        const res = await adminFetch(`${API_BASE}/api/admin/companions`);
        if (!res) return;
        this.companions = await res.json();
      } catch (e) {
        console.error(e);
      } finally {
        this.loading = false;
      }
    },

    openEditModal(c) {
      this.editId = c.profile.id;
      this.editForm = {
        name: c.profile.name || '',
        gender: c.profile.gender || '女',
        age: c.profile.age || 24,
        city: c.profile.city || '',
        personality: c.profile.personality || '',
        background: c.profile.background || '',
        speech_style: c.profile.speech_style || '',
        hobbies: c.profile.hobbies || '',
        values: c.profile.values || '',
        fears: c.profile.fears || '',
        love_view: c.profile.love_view || '',
        daily_routine: c.profile.daily_routine || '',
        favorite_things: c.profile.favorite_things || ''
      };
      this.editPrompts = { zh: '', en: '', ja: '', ko: '', pt: '', es: '', id: '' };
      this.showPrompts = false;
      this.loadCompanionAgentConfig(c.profile.id);
      this.editing = true;
    },

    closeEditModal() {
      this.editing = false;
      this.editId = '';
    },

    async loadCompanionAgentConfig(companionId) {
      try {
        const res = await adminFetch(`${API_BASE}/api/admin/companions/${companionId}/agent-config`);
        if (!res) return;
        const cfg = await res.json();
        this.editPrompts = {
          zh: cfg.system_prompt_zh || '',
          en: cfg.system_prompt_en || '',
          ja: cfg.system_prompt_ja || '',
          ko: cfg.system_prompt_ko || '',
          pt: cfg.system_prompt_pt || '',
          es: cfg.system_prompt_es || '',
          id: cfg.system_prompt_id || ''
        };
      } catch (e) {
        console.error(e);
      }
    },

    async saveCompanionEdit() {
      const body = { ...this.editForm };
      try {
        const res = await adminFetch(`${API_BASE}/api/admin/companions/${this.editId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (res && res.ok) {
          const promptBody = {
            system_prompt_zh: this.editPrompts.zh,
            system_prompt_en: this.editPrompts.en,
            system_prompt_ja: this.editPrompts.ja,
            system_prompt_ko: this.editPrompts.ko,
            system_prompt_pt: this.editPrompts.pt,
            system_prompt_es: this.editPrompts.es,
            system_prompt_id: this.editPrompts.id,
          };
          await adminFetch(`${API_BASE}/api/admin/companions/${this.editId}/agent-config`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(promptBody),
          });
          showToast(t('toast.saved'));
          this.closeEditModal();
          await this.loadCompanions();
        } else {
          showToast(t('toast.saveFailed'));
        }
      } catch (e) {
        showToast(t('toast.saveFailed'));
        console.error(e);
      }
    },

    async deleteCompanion(id) {
      if (!confirm(t('confirm.deleteCompanion'))) return;
      try {
        await adminFetch(`${API_BASE}/api/admin/companions/${id}`, { method: 'DELETE' });
        await this.loadCompanions();
      } catch (e) {
        console.error(e);
      }
    }
  }));
});
