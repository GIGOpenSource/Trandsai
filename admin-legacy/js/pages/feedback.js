document.addEventListener('alpine:init', () => {
  Alpine.data('feedbackPage', () => ({
    feedbacks: [],
    loading: true,
    searchQuery: '',
    currentPage: 1,
    pageSize: 10,

    // 回复弹窗
    viewing: false,
    currentThreadId: null,
    currentThreadInfo: '',
    messages: [],
    replyContent: '',

    async init() {
      await this.loadFeedback();
    },

    get filteredFeedbacks() {
      if (!this.searchQuery.trim()) return this.feedbacks;
      const q = this.searchQuery.toLowerCase();
      return this.feedbacks.filter(f =>
        (f.user_name && f.user_name.toLowerCase().includes(q)) ||
        (f.last_message && f.last_message.toLowerCase().includes(q))
      );
    },

    get paginatedFeedbacks() {
      const start = (this.currentPage - 1) * this.pageSize;
      return this.filteredFeedbacks.slice(start, start + this.pageSize);
    },

    get totalPages() {
      return Math.max(1, Math.ceil(this.filteredFeedbacks.length / this.pageSize));
    },

    get pages() {
      const arr = [];
      for (let i = 1; i <= this.totalPages; i++) arr.push(i);
      return arr;
    },

    goToPage(p) {
      if (p >= 1 && p <= this.totalPages) this.currentPage = p;
    },

    async loadFeedback() {
      this.loading = true;
      try {
        const res = await adminFetch(`${API_BASE}/api/admin/feedback`);
        if (!res) return;
        this.feedbacks = await res.json();
      } catch (e) {
        console.error(e);
      } finally {
        this.loading = false;
      }
    },

    async openFeedbackModal(threadId) {
      this.currentThreadId = threadId;
      try {
        const res = await adminFetch(`${API_BASE}/api/admin/feedback/${threadId}/messages`);
        if (!res) return;
        const data = await res.json();
        const thread = data.thread || {};
        this.messages = data.messages || [];
        this.currentThreadInfo = `用户: ${escapeHtml(thread.user_name || '-')} (#${thread.user_id})`;
        this.replyContent = '';
        this.viewing = true;
        // 滚动到底部
        this.$nextTick(() => {
          const box = document.getElementById('feedback-messages');
          if (box) box.scrollTop = box.scrollHeight;
        });
      } catch (e) {
        console.error(e);
        showToast(t('toast.loadFailed'));
      }
    },

    closeFeedbackModal() {
      this.viewing = false;
      this.currentThreadId = null;
      this.messages = [];
      this.replyContent = '';
    },

    async submitReply() {
      if (!this.currentThreadId || !this.replyContent.trim()) {
        showToast(t('toast.enterContent'));
        return;
      }
      try {
        const res = await adminFetch(`${API_BASE}/api/admin/feedback/${this.currentThreadId}/reply`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: this.replyContent.trim() }),
        });
        if (res && res.ok) {
          showToast(t('toast.sent'));
          this.replyContent = '';
          await this.openFeedbackModal(this.currentThreadId);
          await this.loadFeedback();
        } else {
          showToast(t('toast.sendFailed'));
        }
      } catch (e) {
        showToast(t('toast.sendFailed'));
        console.error(e);
      }
    }
  }));
});
