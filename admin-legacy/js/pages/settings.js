document.addEventListener('alpine:init', () => {
  Alpine.data('settingsPage', () => ({
    // 系统配置
    cfg: {
      model_provider: 'anthropic',
      anthropic_ready: false,
      deepseek_ready: false,
      openai_ready: false,
      admin_password_set: false,
    },
    inputs: {
      anthropic: '', deepseek: '', openai: '', password: ''
    },

    // Agent 配置
    agentCfg: {
      model_provider: '',
      temperature: 0.93,
      max_tokens: 2048,
      system_prompt_zh: '',
      system_prompt_en: '',
      system_prompt_ja: '',
      system_prompt_ko: '',
      system_prompt_pt: '',
      system_prompt_es: '',
      system_prompt_id: '',
    },

    // Embedding 状态
    embState: 'checking',
    embMsg: '--',
    embProgress: 0,
    embPolling: false,
    embTimer: null,

    loading: true,

    async init() {
      await this.loadSettings();
      await this.loadAgentConfig();
      await this.loadEmbeddingStatus();
    },

    get providerMap() {
      return { anthropic: 'Anthropic', deepseek: 'DeepSeek', openai: 'OpenAI' };
    },

    async loadSettings() {
      try {
        const res = await adminFetch(`${API_BASE}/api/admin/config`);
        if (!res) return;
        const data = await res.json();
        this.cfg = {
          model_provider: data.model_provider || 'anthropic',
          anthropic_ready: data.anthropic_ready || false,
          deepseek_ready: data.deepseek_ready || false,
          openai_ready: data.openai_ready || false,
          admin_password_set: data.admin_password_set || false,
        };
      } catch (e) {
        console.error(e);
      }
    },

    async loadAgentConfig() {
      try {
        const res = await adminFetch(`${API_BASE}/api/admin/agent-config`);
        if (!res) return;
        const ac = await res.json();
        this.agentCfg = {
          model_provider: ac.model_provider || '',
          temperature: ac.temperature ?? 0.93,
          max_tokens: ac.max_tokens ?? 2048,
          system_prompt_zh: ac.system_prompt_zh || '',
          system_prompt_en: ac.system_prompt_en || '',
          system_prompt_ja: ac.system_prompt_ja || '',
          system_prompt_ko: ac.system_prompt_ko || '',
          system_prompt_pt: ac.system_prompt_pt || '',
          system_prompt_es: ac.system_prompt_es || '',
          system_prompt_id: ac.system_prompt_id || '',
        };
      } catch (e) {
        console.error(e);
      } finally {
        this.loading = false;
      }
    },

    async loadEmbeddingStatus() {
      try {
        const res = await adminFetch(`${API_BASE}/api/admin/embedding-status`);
        if (!res) return;
        const st = await res.json();
        this.embState = st.state || 'checking';
        this.embMsg = st.message || '--';
        this.embProgress = st.progress || 0;

        if (st.state === 'downloading' || st.state === 'idle' || st.state === 'checking') {
          if (!this.embTimer) {
            this.embTimer = setInterval(() => this.loadEmbeddingStatus(), 1500);
          }
        } else {
          if (this.embTimer) {
            clearInterval(this.embTimer);
            this.embTimer = null;
          }
        }
      } catch (e) {
        console.error(e);
      }
    },

    async saveSettings() {
      const body = {
        model_provider: this.cfg.model_provider,
      };
      if (this.inputs.anthropic) body.anthropic_key = this.inputs.anthropic;
      if (this.inputs.deepseek) body.deepseek_key = this.inputs.deepseek;
      if (this.inputs.openai) body.openai_key = this.inputs.openai;
      if (this.inputs.password) body.admin_password = this.inputs.password;

      try {
        const res = await adminFetch(`${API_BASE}/api/admin/config`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (res && res.ok) {
          showToast(t('toast.saved'));
          this.inputs = { anthropic: '', deepseek: '', openai: '', password: '' };
          if (this.inputs.password) {
            this.$store.admin.logout();
            location.reload();
          } else {
            await this.loadSettings();
          }
        } else {
          showToast(t('toast.saveFailed'));
        }
      } catch (e) {
        showToast(t('toast.saveFailed'));
        console.error(e);
      }
    },

    async testConnection() {
      showToast(t('toast.testing'));
      try {
        const res = await adminFetch(`${API_BASE}/api/admin/config/test`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ provider: this.cfg.model_provider }),
        });
        if (!res) return;
        const data = await res.json();
        if (data.ok) {
          showToast(t('toast.testSuccess') + ': ' + data.response);
        } else {
          showToast(t('toast.testFailed') + ': ' + data.error);
        }
      } catch (e) {
        showToast(t('toast.testError'));
        console.error(e);
      }
    },

    async saveAgentConfig() {
      const body = {
        model_provider: this.agentCfg.model_provider,
        temperature: parseFloat(this.agentCfg.temperature),
        max_tokens: parseInt(this.agentCfg.max_tokens, 10),
        system_prompt_zh: this.agentCfg.system_prompt_zh,
        system_prompt_en: this.agentCfg.system_prompt_en,
        system_prompt_ja: this.agentCfg.system_prompt_ja,
        system_prompt_ko: this.agentCfg.system_prompt_ko,
        system_prompt_pt: this.agentCfg.system_prompt_pt,
        system_prompt_es: this.agentCfg.system_prompt_es,
        system_prompt_id: this.agentCfg.system_prompt_id,
      };
      try {
        const res = await adminFetch(`${API_BASE}/api/admin/agent-config`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (res && res.ok) {
          showToast(t('toast.saved'));
        } else {
          showToast(t('toast.saveFailed'));
        }
      } catch (e) {
        showToast(t('toast.saveFailed'));
        console.error(e);
      }
    }
  }));
});
