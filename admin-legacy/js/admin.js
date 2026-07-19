const API_BASE = window.location.origin;

let adminToken = localStorage.getItem('admin_token') || '';
let currentPage = 'dashboard';

// ===== 登录 =====
function initLogin() {
  document.getElementById('login-btn')?.addEventListener('click', async () => {
    const pwd = document.getElementById('login-pwd').value;
    const errEl = document.getElementById('login-error');
    try {
      const res = await fetch(`${API_BASE}/api/admin/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: pwd }),
      });
      const data = await res.json();
      if (data.token) {
        adminToken = data.token;
        localStorage.setItem('admin_token', adminToken);
        location.reload();
      } else {
        errEl.textContent = '密码错误';
      }
    } catch (e) {
      errEl.textContent = '请求失败';
    }
  });
}

// ===== 带认证的 fetch =====
async function adminFetch(url, opts = {}) {
  opts.headers = opts.headers || {};
  opts.headers['Authorization'] = `Bearer ${adminToken}`;
  const res = await fetch(url, opts);
  if (res.status === 401) {
    localStorage.removeItem('admin_token');
    location.reload();
    return null;
  }
  return res;
}

// ===== 导航切换 =====
function showPage(page) {
  currentPage = page;
  document.querySelectorAll('.admin-page').forEach(el => el.classList.add('hidden'));
  document.getElementById(`page-${page}`)?.classList.remove('hidden');
  document.querySelectorAll('.admin-nav-item').forEach(el => el.classList.remove('active'));
  document.querySelector(`[data-page="${page}"]`)?.classList.add('active');

  if (page === 'dashboard') loadDashboard();
  if (page === 'companions') loadCompanions();
  if (page === 'moments') loadMoments();
  if (page === 'users') loadUsers();
  if (page === 'feedback') loadFeedback();
  if (page === 'knowledge') loadKnowledge();
  if (page === 'settings') loadSettings();
  else stopEmbeddingPoll();
}

// ===== Dashboard =====
async function loadDashboard() {
  try {
    const res = await adminFetch(`${API_BASE}/api/admin/stats`);
    if (!res) return;
    const stats = await res.json();
    document.getElementById('stat-companions').textContent = stats.companion_count || 0;
    document.getElementById('stat-turns').textContent = stats.total_turns || 0;
    document.getElementById('stat-avg-affection').textContent = stats.avg_affection || 0;
  } catch (e) {
    console.error(e);
  }
}

// ===== 智能体管理 =====
async function loadCompanions() {
  try {
    const res = await adminFetch(`${API_BASE}/api/admin/companions`);
    if (!res) return;
    const list = await res.json();
    const tbody = document.getElementById('companions-table-body');
    tbody.innerHTML = list.map(c => `
      <tr>
        <td>${c.profile.id}</td>
        <td>${escapeHtml(c.profile.name)}</td>
        <td>${c.profile.gender === '男' ? '♂' : '♀'}</td>
        <td>${c.profile.age}</td>
        <td>${escapeHtml(c.profile.city)}</td>
        <td>${c.state.affection}</td>
        <td>${new Date(c.profile.created_at).toLocaleString()}</td>
        <td>
          <button class="btn-sm btn-warning" onclick='openEditModal(${JSON.stringify(c).replace(/'/g, "&#39;")})'>编辑</button>
          <button class="btn-sm btn-danger" onclick="deleteCompanion('${c.profile.id}')">删除</button>
        </td>
      </tr>
    `).join('');
  } catch (e) {
    console.error(e);
  }
}

function openEditModal(c) {
  document.getElementById('edit-companion-id').value = c.profile.id;
  document.getElementById('edit-name').value = c.profile.name;
  document.getElementById('edit-gender').value = c.profile.gender;
  document.getElementById('edit-age').value = c.profile.age;
  document.getElementById('edit-city').value = c.profile.city;
  document.getElementById('edit-personality').value = c.profile.personality;
  document.getElementById('edit-background').value = c.profile.background;
  document.getElementById('edit-speech_style').value = c.profile.speech_style;
  document.getElementById('edit-hobbies').value = c.profile.hobbies || '';
  document.getElementById('edit-values').value = c.profile.values || '';
  document.getElementById('edit-fears').value = c.profile.fears || '';
  document.getElementById('edit-love_view').value = c.profile.love_view || '';
  document.getElementById('edit-daily_routine').value = c.profile.daily_routine || '';
  document.getElementById('edit-favorite_things').value = c.profile.favorite_things || '';
  // 重置 prompt 区域
  document.getElementById('edit-prompt-zh').value = '';
  document.getElementById('edit-prompt-en').value = '';
  document.getElementById('edit-prompt-ja').value = '';
  document.getElementById('edit-prompt-ko').value = '';
  document.getElementById('companion-prompt-section').classList.add('hidden');
  document.getElementById('prompt-toggle-icon').textContent = '▶';
  // 加载智能体级 Agent Config
  loadCompanionAgentConfig(c.profile.id);
  document.getElementById('companion-edit-modal').classList.add('show');
}

function toggleCompanionPrompts() {
  const section = document.getElementById('companion-prompt-section');
  const icon = document.getElementById('prompt-toggle-icon');
  if (section.classList.contains('hidden')) {
    section.classList.remove('hidden');
    icon.textContent = '▼';
  } else {
    section.classList.add('hidden');
    icon.textContent = '▶';
  }
}

async function loadCompanionAgentConfig(companionId) {
  try {
    const res = await adminFetch(`${API_BASE}/api/admin/companions/${companionId}/agent-config`);
    if (!res) return;
    const cfg = await res.json();
    document.getElementById('edit-prompt-zh').value = cfg.system_prompt_zh || '';
    document.getElementById('edit-prompt-en').value = cfg.system_prompt_en || '';
    document.getElementById('edit-prompt-ja').value = cfg.system_prompt_ja || '';
    document.getElementById('edit-prompt-ko').value = cfg.system_prompt_ko || '';
  } catch (e) {
    console.error(e);
  }
}

function closeEditModal() {
  document.getElementById('companion-edit-modal').classList.remove('show');
}

async function saveCompanionEdit() {
  const id = document.getElementById('edit-companion-id').value;
  const body = {
    name: document.getElementById('edit-name').value.trim(),
    gender: document.getElementById('edit-gender').value,
    age: parseInt(document.getElementById('edit-age').value, 10),
    city: document.getElementById('edit-city').value.trim(),
    personality: document.getElementById('edit-personality').value.trim(),
    background: document.getElementById('edit-background').value.trim(),
    speech_style: document.getElementById('edit-speech_style').value.trim(),
    hobbies: document.getElementById('edit-hobbies').value.trim(),
    values: document.getElementById('edit-values').value.trim(),
    fears: document.getElementById('edit-fears').value.trim(),
    love_view: document.getElementById('edit-love_view').value.trim(),
    daily_routine: document.getElementById('edit-daily_routine').value.trim(),
    favorite_things: document.getElementById('edit-favorite_things').value.trim(),
  };
  try {
    const res = await adminFetch(`${API_BASE}/api/admin/companions/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (res && res.ok) {
      // 同时保存智能体级 Agent Config
      const promptBody = {
        system_prompt_zh: document.getElementById('edit-prompt-zh').value.trim(),
        system_prompt_en: document.getElementById('edit-prompt-en').value.trim(),
        system_prompt_ja: document.getElementById('edit-prompt-ja').value.trim(),
        system_prompt_ko: document.getElementById('edit-prompt-ko').value.trim(),
      };
      await adminFetch(`${API_BASE}/api/admin/companions/${id}/agent-config`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(promptBody),
      });
      showToast('保存成功');
      closeEditModal();
      loadCompanions();
    } else {
      showToast('保存失败');
    }
  } catch (e) {
    showToast('保存失败');
    console.error(e);
  }
}

async function deleteCompanion(id) {
  if (!confirm('确定删除此智能体？所有数据将被清除。')) return;
  await adminFetch(`${API_BASE}/api/admin/companions/${id}`, { method: 'DELETE' });
  loadCompanions();
}

// ===== 内容库 =====

async function loadKnowledge() {
  try {
    const res = await adminFetch(`${API_BASE}/api/admin/knowledge`);
    if (!res) return;
    const list = await res.json();
    renderKnowledgeList(list);
  } catch (e) {
    console.error(e);
  }
}

function renderKnowledgeList(list) {
  document.getElementById('kb-list').innerHTML = list.map(item => `
    <div class="kb-item">
      <div class="title">${escapeHtml(item.title)}</div>
      <div class="meta">${item.category} · ${item.language} · ${new Date(item.created_at).toLocaleString()}</div>
      <div class="content">${escapeHtml(item.content.slice(0, 200))}${item.content.length > 200 ? '...' : ''}</div>
      <div class="actions">
        <button class="btn-sm btn-danger" onclick="deleteKnowledge('${item.id}')">删除</button>
      </div>
    </div>
  `).join('');
}

async function searchKnowledge() {
  const query = document.getElementById('kb-search-input').value.trim();
  if (!query) return;
  const box = document.getElementById('kb-search-results');
  box.innerHTML = '<p style="color:#888;font-size:13px;">搜索中...</p>';
  try {
    const res = await adminFetch(`${API_BASE}/api/admin/knowledge/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, top_k: 10 }),
    });
    if (!res) return;
    const data = await res.json();
    const results = data.results || [];
    if (!results.length) {
      box.innerHTML = '<p style="color:#888;font-size:13px;">本地知识库中无匹配结果</p>';
      return;
    }
    box.innerHTML = results.map(r => `
      <div class="kb-item">
        <div class="title">${escapeHtml(r.title || '无标题')}</div>
        <div class="meta">${r.category || 'other'} · 相似度: ${(1 - (r.distance || 0)).toFixed(2)}</div>
        <div class="content">${escapeHtml((r.content || '').slice(0, 300))}${(r.content || '').length > 300 ? '...' : ''}</div>
      </div>
    `).join('');
  } catch (e) {
    console.error(e);
    box.innerHTML = '<p style="color:#e94560;font-size:13px;">搜索失败</p>';
  }
}

async function deleteKnowledge(id) {
  if (!confirm('确定删除此条目？')) return;
  await adminFetch(`${API_BASE}/api/admin/knowledge/${id}`, { method: 'DELETE' });
  loadKnowledge();
}

// ===== 朋友圈管理 =====
async function loadMoments() {
  try {
    const res = await adminFetch(`${API_BASE}/api/admin/moments?limit=50`);
    if (!res) return;
    const data = await res.json();
    const tbody = document.getElementById('moments-table-body');
    const moments = data.moments || [];
    if (!moments.length) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#888;">暂无朋友圈</td></tr>';
      return;
    }
    tbody.innerHTML = moments.map(m => `
      <tr>
        <td>${m.id}</td>
        <td>${escapeHtml(m.companion_id)}</td>
        <td>${escapeHtml(m.caption)}</td>
        <td>${m.likes_count}</td>
        <td>${m.comments_count}</td>
        <td>${new Date(m.created_at).toLocaleString()}</td>
        <td>
          <button class="btn-sm btn-danger" onclick="deleteMoment(${m.id})">删除</button>
        </td>
      </tr>
    `).join('');
  } catch (e) {
    console.error(e);
  }
}

async function deleteMoment(id) {
  if (!confirm('确定删除此朋友圈？')) return;
  await adminFetch(`${API_BASE}/api/admin/moments/${id}`, { method: 'DELETE' });
  loadMoments();
}

// ===== 用户管理 =====
async function loadUsers() {
  try {
    const res = await adminFetch(`${API_BASE}/api/admin/users`);
    if (!res) return;
    const users = await res.json();
    const tbody = document.getElementById('users-table-body');
    if (!users.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#888;">暂无注册用户</td></tr>';
      return;
    }
    tbody.innerHTML = users.map(u => `
      <tr>
        <td>${u.id}</td>
        <td>${escapeHtml(u.username)}</td>
        <td>${escapeHtml(u.nickname || '-')}</td>
        <td>${u.gender || '-'}</td>
        <td>${new Date(u.created_at).toLocaleString()}</td>
      </tr>
    `).join('');
  } catch (e) {
    console.error(e);
  }
}

// ===== Embedding 状态 =====
let _embeddingPollTimer = null;

async function loadEmbeddingStatus() {
  try {
    const res = await adminFetch(`${API_BASE}/api/admin/embedding-status`);
    if (!res) return;
    const st = await res.json();
    const badge = document.getElementById('emb-badge');
    const msg = document.getElementById('emb-msg');
    const progressWrap = document.getElementById('emb-progress-wrap');
    const progressFill = document.getElementById('emb-progress-fill');
    const progressText = document.getElementById('emb-progress-text');

    badge.className = 'embedding-status-badge';
    if (st.state === 'ready') {
      badge.textContent = '已就绪';
      badge.classList.add('ready');
      progressWrap.classList.add('hidden');
    } else if (st.state === 'downloading') {
      badge.textContent = '下载中';
      badge.classList.add('downloading');
      progressWrap.classList.remove('hidden');
      progressFill.style.width = st.progress + '%';
      progressText.textContent = st.progress + '%';
    } else if (st.state === 'error') {
      badge.textContent = '错误';
      badge.classList.add('error');
      progressWrap.classList.add('hidden');
    } else {
      badge.textContent = '检查中';
      badge.classList.add('checking');
      progressWrap.classList.add('hidden');
    }
    msg.textContent = st.message || '--';

    // 如果还在下载或检查中，继续轮询
    if (st.state === 'downloading' || st.state === 'idle' || st.state === 'checking') {
      if (!_embeddingPollTimer) {
        _embeddingPollTimer = setInterval(loadEmbeddingStatus, 1500);
      }
    } else {
      if (_embeddingPollTimer) {
        clearInterval(_embeddingPollTimer);
        _embeddingPollTimer = null;
      }
    }
  } catch (e) {
    console.error(e);
  }
}

function stopEmbeddingPoll() {
  if (_embeddingPollTimer) {
    clearInterval(_embeddingPollTimer);
    _embeddingPollTimer = null;
  }
}

// ===== 设置 =====
async function loadSettings() {
  loadEmbeddingStatus();
  try {
    const res = await adminFetch(`${API_BASE}/api/admin/config`);
    if (!res) return;
    const cfg = await res.json();

    // 设置 provider
    document.getElementById('cfg-model-provider').value = cfg.model_provider || 'anthropic';

    // 更新概览 - 模型提供商
    const providerMap = { anthropic: 'Anthropic', deepseek: 'DeepSeek', openai: 'OpenAI' };
    const overviewProvider = document.getElementById('overview-provider');
    if (overviewProvider) {
      overviewProvider.textContent = providerMap[cfg.model_provider] || cfg.model_provider || '--';
    }

    // 更新各个 key 的状态（概览 + 输入框旁标签）
    const keyMap = {
      anthropic: ['overview-anthropic', 'status-anthropic', 'cfg-input-anthropic'],
      deepseek:  ['overview-deepseek',  'status-deepseek',  'cfg-input-deepseek'],
      openai:    ['overview-openai',    'status-openai',    'cfg-input-openai'],
    };

    for (const [key, [ovId, stId, inpId]] of Object.entries(keyMap)) {
      const isReady = cfg[`${key}_ready`];
      const ovEl = document.getElementById(ovId);
      const stEl = document.getElementById(stId);
      const inpEl = document.getElementById(inpId);

      if (ovEl) {
        ovEl.textContent = isReady ? '已配置' : '未配置';
        ovEl.className = isReady ? 'overview-status ready' : 'overview-status';
      }
      if (stEl) {
        stEl.textContent = isReady ? '已配置' : '未配置';
        stEl.className = isReady ? 'config-status ready' : 'config-status';
      }
      if (inpEl) {
        inpEl.placeholder = isReady ? '已配置，留空表示不修改' : '请输入 API Key';
      }
    }

    // 管理员密码状态
    const pwdReady = cfg.admin_password_set;
    const pwdOv = document.getElementById('overview-password');
    const pwdSt = document.getElementById('status-password');
    if (pwdOv) {
      pwdOv.textContent = pwdReady ? '已配置' : '未配置';
      pwdOv.className = pwdReady ? 'overview-status ready' : 'overview-status';
    }
    if (pwdSt) {
      pwdSt.textContent = pwdReady ? '已配置' : '未配置';
      pwdSt.className = pwdReady ? 'config-status ready' : 'config-status';
    }
  } catch (e) {
    console.error(e);
  }

  try {
    const res2 = await adminFetch(`${API_BASE}/api/admin/agent-config`);
    if (!res2) return;
    const ac = await res2.json();
    document.getElementById('agent-model-provider').value = ac.model_provider || '';
    document.getElementById('agent-temperature').value = ac.temperature ?? 0.93;
    document.getElementById('agent-max-tokens').value = ac.max_tokens ?? 2048;
    document.getElementById('agent-prompt-zh').value = ac.system_prompt_zh || '';
    document.getElementById('agent-prompt-en').value = ac.system_prompt_en || '';
    document.getElementById('agent-prompt-ja').value = ac.system_prompt_ja || '';
    document.getElementById('agent-prompt-ko').value = ac.system_prompt_ko || '';
  } catch (e) {
    console.error(e);
  }
}

async function saveSettings() {
  const body = {};
  const anthropic = document.getElementById('cfg-input-anthropic').value.trim();
  const deepseek = document.getElementById('cfg-input-deepseek').value.trim();
  const openai = document.getElementById('cfg-input-openai').value.trim();
  const password = document.getElementById('cfg-input-password').value.trim();
  const provider = document.getElementById('cfg-model-provider').value;

  body.model_provider = provider;
  if (anthropic) body.anthropic_key = anthropic;
  if (deepseek) body.deepseek_key = deepseek;
  if (openai) body.openai_key = openai;
  if (password) body.admin_password = password;

  try {
    const res = await adminFetch(`${API_BASE}/api/admin/config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (res && res.ok) {
      showToast('配置已保存');
      // 清空敏感输入框
      document.getElementById('cfg-input-anthropic').value = '';
      document.getElementById('cfg-input-deepseek').value = '';
      document.getElementById('cfg-input-openai').value = '';
      document.getElementById('cfg-input-password').value = '';
      // 如果改了密码，后端会清空 token，刷新页面回到登录页
      if (password) {
        localStorage.removeItem('admin_token');
        location.reload();
      }
    } else {
      showToast('保存失败');
    }
  } catch (e) {
    showToast('保存失败');
    console.error(e);
  }
}

async function testConnection() {
  const provider = document.getElementById('cfg-model-provider').value;
  showToast(`正在测试 ${provider} ...`);
  try {
    const res = await adminFetch(`${API_BASE}/api/admin/config/test`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider }),
    });
    if (!res) return;
    const data = await res.json();
    if (data.ok) {
      showToast('连通成功: ' + data.response);
    } else {
      showToast('连通失败: ' + data.error);
    }
  } catch (e) {
    showToast('连通测试异常');
    console.error(e);
  }
}

async function saveAgentConfig() {
  const body = {
    model_provider: document.getElementById('agent-model-provider').value,
    temperature: parseFloat(document.getElementById('agent-temperature').value),
    max_tokens: parseInt(document.getElementById('agent-max-tokens').value, 10),
    system_prompt_zh: document.getElementById('agent-prompt-zh').value.trim(),
    system_prompt_en: document.getElementById('agent-prompt-en').value.trim(),
    system_prompt_ja: document.getElementById('agent-prompt-ja').value.trim(),
    system_prompt_ko: document.getElementById('agent-prompt-ko').value.trim(),
  };
  try {
    const res = await adminFetch(`${API_BASE}/api/admin/agent-config`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (res && res.ok) {
      showToast('Agent 配置已保存，立即生效');
    } else {
      showToast('保存失败');
    }
  } catch (e) {
    showToast('保存失败');
    console.error(e);
  }
}

// ===== 工具 =====
function escapeHtml(str) {
  const map = {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'};
  return str.replace(/[&<>"']/g, m => map[m]);
}

function showToast(text) {
  let el = document.getElementById('toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'toast';
    el.style.cssText = 'position:fixed;top:60px;left:50%;transform:translateX(-50%);background:rgba(0,0,0,0.7);color:#fff;padding:8px 16px;border-radius:20px;font-size:12px;z-index:999;opacity:0;transition:opacity 0.3s;';
    document.body.appendChild(el);
  }
  el.textContent = text;
  el.style.opacity = '1';
  setTimeout(() => el.style.opacity = '0', 2000);
}

// ===== 初始化 =====
if (!adminToken) {
  document.getElementById('login-page')?.classList.remove('hidden');
  document.getElementById('admin-page')?.classList.add('hidden');
  initLogin();
} else {
  document.getElementById('login-page')?.classList.add('hidden');
  document.getElementById('admin-page')?.classList.remove('hidden');
  document.querySelectorAll('.admin-nav-item').forEach(el => {
    el.addEventListener('click', () => showPage(el.dataset.page));
  });
  showPage('dashboard');
}

// ===== 意见反馈管理 =====
let _currentFeedbackThreadId = null;

async function loadFeedback() {
  try {
    const res = await adminFetch(`${API_BASE}/api/admin/feedback`);
    if (!res) return;
    const list = await res.json();
    const tbody = document.getElementById('feedback-table-body');
    if (!list.length) {
      tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#888;">暂无反馈</td></tr>';
      return;
    }
    tbody.innerHTML = list.map(f => {
      const statusText = f.status === 'replied' ? '已回复' : '待处理';
      const statusClass = f.status === 'replied' ? 'ready' : '';
      return `
      <tr>
        <td>${f.id}</td>
        <td>${escapeHtml(f.user_name || '-')} <span style="color:#888;font-size:12px;">(#${f.user_id})</span></td>
        <td>${escapeHtml(f.last_message || '')} <span style="color:#888;font-size:12px;">[${f.last_message_sender || ''}]</span></td>
        <td><span class="overview-status ${statusClass}">${statusText}</span></td>
        <td>${f.updated_at ? new Date(f.updated_at).toLocaleString() : '-'}</td>
        <td>
          <button class="btn-sm btn-primary" onclick="openFeedbackModal(${f.id})">查看/回复</button>
        </td>
      </tr>
    `}).join('');
  } catch (e) {
    console.error(e);
  }
}

async function openFeedbackModal(threadId) {
  _currentFeedbackThreadId = threadId;
  try {
    const res = await adminFetch(`${API_BASE}/api/admin/feedback/${threadId}/messages`);
    if (!res) return;
    const data = await res.json();
    const thread = data.thread || {};
    const messages = data.messages || [];

    document.getElementById('feedback-thread-info').textContent =
      `用户: ${escapeHtml(thread.user_name || '-')} (#${thread.user_id})`;

    const msgBox = document.getElementById('feedback-messages');
    if (!messages.length) {
      msgBox.innerHTML = '<p style="color:#888;text-align:center;">暂无消息</p>';
    } else {
      msgBox.innerHTML = messages.map(m => {
        const isUser = m.sender === 'user';
        const isAdmin = m.sender === 'admin';
        const label = isUser ? '用户' : (isAdmin ? '管理员' : '系统');
        const align = isUser ? 'right' : 'left';
        const bg = isUser ? '#e94560' : (isAdmin ? '#4a69bd' : '#555');
        return `
          <div style="margin-bottom:10px;text-align:${align};">
            <div style="font-size:11px;color:#888;margin-bottom:2px;">${label} · ${new Date(m.created_at).toLocaleString()}</div>
            <div style="display:inline-block;background:${bg};color:#fff;padding:8px 12px;border-radius:12px;text-align:left;max-width:80%;word-break:break-word;">
              ${escapeHtml(m.content)}
            </div>
          </div>
        `;
      }).join('');
      // 滚动到底部
      msgBox.scrollTop = msgBox.scrollHeight;
    }

    document.getElementById('feedback-reply-input').value = '';
    document.getElementById('feedback-modal').classList.add('show');
  } catch (e) {
    console.error(e);
    showToast('加载失败');
  }
}

function closeFeedbackModal() {
  document.getElementById('feedback-modal').classList.remove('show');
  _currentFeedbackThreadId = null;
}

async function submitFeedbackReply() {
  if (!_currentFeedbackThreadId) return;
  const content = document.getElementById('feedback-reply-input').value.trim();
  if (!content) {
    showToast('请输入回复内容');
    return;
  }
  try {
    const res = await adminFetch(`${API_BASE}/api/admin/feedback/${_currentFeedbackThreadId}/reply`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    });
    if (res && res.ok) {
      showToast('回复已发送');
      document.getElementById('feedback-reply-input').value = '';
      // 刷新弹窗内消息和列表
      await openFeedbackModal(_currentFeedbackThreadId);
      loadFeedback();
    } else {
      showToast('发送失败');
    }
  } catch (e) {
    showToast('发送失败');
    console.error(e);
  }
}

// 点击弹窗外部关闭
document.getElementById('companion-edit-modal')?.addEventListener('click', (e) => {
  if (e.target.id === 'companion-edit-modal') closeEditModal();
});
document.getElementById('feedback-modal')?.addEventListener('click', (e) => {
  if (e.target.id === 'feedback-modal') closeFeedbackModal();
});
