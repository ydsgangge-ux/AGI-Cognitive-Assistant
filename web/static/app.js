/* ── AI Web App ─────────────────── */

const socket = io();
let currentChatId = null;
let chatHistory = {};
let isTyping = false;
let sidebarOpen = true;

socket.on('connect', () => {
  console.log('Connected');
  const sid = sessionStorage.getItem('sessionId');
  if (sid) socket.emit('restore_session', { session_id: sid });
});

socket.on('auth_result', d => {
  if (!d || !d.ok) { showLoginErr((d && d.error) || '认证失败'); return; }
  sessionStorage.setItem('sessionId', socket.id);
  if (d.name) document.getElementById('userName').textContent = d.name;
  document.getElementById('loginPage').style.display = 'none';
  document.getElementById('appPage').style.display = 'flex';
  loadChatList();
});

socket.on('chat:list', list => { renderChatList(list || []); });

socket.on('chat:loaded', d => {
  currentChatId = d.chat_id;
  document.getElementById('welcomeScreen')?.remove();
  document.getElementById('chatMessages').innerHTML = '';
  document.getElementById('chatTitle').textContent = d.title || 'AI';
  if (d.messages) d.messages.forEach(m => appendMessage(m));
  scrollToBottom();
  setActiveChat(d.chat_id);
});

socket.on('chat:reply', d => {
  hideTyping();
  if (d.message) appendMessage(d.message);
  scrollToBottom();
  if (!currentChatId && d.chat_id) currentChatId = d.chat_id;
  if (d.message) updateChatTitleFromMsg(d.message.content);
});

socket.on('chat:created', d => {
  if (!currentChatId && d.chat_id) currentChatId = d.chat_id;
  loadChatList();
});

socket.on('chat:typing', () => { showTyping(); });

socket.on('error', e => {
  hideTyping();
  const msg = (e && e.message) || e || '未知错误';
  appendMessage({ role: 'assistant', content: '**错误：** ' + msg });
  scrollToBottom();
});

socket.on('timed:reminder', d => {
  showReminderToast(d.message || '⏰ 定时提醒');
  loadTimedTasks();
});

socket.on('timed_task_list', list => { renderTimedList(list || []); });

function doLogin() {
  const p = document.getElementById('loginPass').value.trim();
  if (!p) return;
  const btn = document.getElementById('loginBtn');
  btn.disabled = true;
  btn.textContent = '登录中…';
  socket.emit('login', { passphrase: p }, r => {
    btn.disabled = false;
    btn.textContent = '登录';
    if (!r || !r.ok) showLoginErr((r && r.error) || '登录失败，请检查网络连接');
  });
  setTimeout(() => {
    if (btn.disabled) {
      btn.disabled = false;
      btn.textContent = '登录';
      showLoginErr('登录超时，请检查服务是否正常运行');
    }
  }, 10000);
}

function showLoginErr(msg) {
  const el = document.getElementById('loginErr');
  el.textContent = msg;
  setTimeout(() => el.textContent = '', 4000);
}

function doLogout() {
  socket.emit('logout');
  sessionStorage.clear();
  location.reload();
}

function newChat() {
  currentChatId = null;
  document.getElementById('chatMessages').innerHTML = `
    <div class="welcome-screen" id="welcomeScreen">
      <h2>AI</h2>
      <p>How can I help you today?</p>
    </div>`;
  document.getElementById('chatTitle').textContent = 'AI';
  clearActiveChat();
  document.getElementById('chatInput').focus();
}

function sendMessage() {
  const input = document.getElementById('chatInput');
  const text = input.value.trim();
  if (!text || isTyping) return;

  input.value = '';
  autoResize(input);

  document.getElementById('welcomeScreen')?.remove();

  appendMessage({ role: 'user', content: text });
  scrollToBottom();

  socket.emit('chat:message', { chat_id: currentChatId || '', message: text });
  showTyping();
}

function handleKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 160) + 'px';
}

function appendMessage(m) {
  const area = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = 'message-row ' + (m.role === 'user' ? 'user' : 'assistant');

  let html = '<div class="message-text">';
  html += formatContent(m.content || '');
  html += '</div>';

  if (m.timestamp) {
    html += `<div class="msg-time">${formatTime(m.timestamp)}</div>`;
  }

  div.innerHTML = html;
  area.appendChild(div);

  if (m.role === 'user') saveChatToHistory(m.content);
}

function formatContent(text) {
  if (!text) return '';
  let s = text
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/`([^`\n]+)`/g, '<code>$1</code>');
  s = s.replace(/```(\w*)\n?([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
  s = s.replace(/\n/g, '<br>');
  return s;
}

function formatTime(ts) {
  try { return new Date(ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }); }
  catch { return ''; }
}

function scrollToBottom() {
  const el = document.getElementById('chatMessages');
  requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; });
}

function showTyping() {
  if (isTyping) return;
  isTyping = true;
  const area = document.getElementById('chatMessages');
  const row = document.createElement('div');
  row.className = 'typing-row';
  row.id = 'typingIndicator';
  row.innerHTML = `<div class="typing-dots"><span></span><span></span><span></span></div>`;
  area.appendChild(row);
  scrollToBottom();
}

function hideTyping() {
  isTyping = false;
  const el = document.getElementById('typingIndicator');
  if (el) el.remove();
}

function toggleSidebar() {
  const sb = document.getElementById('sidebar');
  sidebarOpen = !sidebarOpen;
  sb.classList.toggle('collapsed', !sidebarOpen);
}

let toolListCache = [];

socket.on('tool_list', list => {
  toolListCache = list || [];
  renderToolbox(list);
});

function toggleToolbox() {
  const dd = document.getElementById('toolboxDropdown');
  const btn = document.getElementById('toolboxBtn');
  const isOpen = dd.style.display !== 'none';
  dd.style.display = isOpen ? 'none' : 'block';
  btn.classList.toggle('active', !isOpen);
  if (!isOpen && toolListCache.length === 0) {
    socket.emit('get_tool_list');
  }
  if (!isOpen) {
    const si = document.getElementById('toolSearchInput');
    if (si) { si.value = ''; si.focus(); }
    renderToolbox(toolListCache);
  }
}

function renderToolbox(list) {
  const container = document.getElementById('toolboxList');
  if (!container) return;
  if (!list || list.length === 0) {
    container.innerHTML = '<div style="padding:10px;text-align:center;color:#999;font-size:.82rem">暂无可用工具</div>';
    return;
  }
  container.innerHTML = '';
  list.forEach(t => {
    const riskColors = { high: '#f53f3f', medium: '#ff7d00', low: '#999' };
    const riskColor = riskColors[t.risk] || '#999';
    const item = document.createElement('div');
    item.className = 'toolbox-item';
    item.dataset.name = t.name;
    item.onclick = () => useTool(t.name, t.params);
    item.innerHTML = `
      <div class="toolbox-item-info">
        <span class="toolbox-item-name">${escapeHtml(t.name)}</span>
        <span class="toolbox-item-desc">${escapeHtml(t.description || '')}</span>
      </div>
      <span class="toolbox-item-risk" style="color:${riskColor}">${t.risk}</span>`;
    container.appendChild(item);
  });
}

function filterTools(keyword) {
  const kw = (keyword || '').toLowerCase();
  if (!kw) { renderToolbox(toolListCache); return; }
  const filtered = toolListCache.filter(t =>
    t.name.toLowerCase().includes(kw) || (t.description || '').toLowerCase().includes(kw)
  );
  renderToolbox(filtered);
}

function useTool(name, params) {
  const input = document.getElementById('chatInput');
  const paramHint = (params && params.length > 0) ? params.join(', ') : '';
  input.value = `使用 ${name} 工具` + (paramHint ? `（参数：${paramHint}）` : '') + '：';
  input.focus();
  autoResize(input);
  toggleToolbox();
}

document.addEventListener('click', e => {
  const wrapper = document.querySelector('.toolbox-wrapper');
  if (wrapper && !wrapper.contains(e.target)) {
    const dd = document.getElementById('toolboxDropdown');
    const btn = document.getElementById('toolboxBtn');
    if (dd) dd.style.display = 'none';
    if (btn) btn.classList.remove('active');
  }
});

/* ── Chat List ── */

function renderChatList(list) {
  const starredEl = document.getElementById('chatListStarred');
  const recentEl = document.getElementById('chatListRecent');
  starredEl.innerHTML = '';
  recentEl.innerHTML = '';

  const sorted = [...list].sort((a, b) =>
    new Date(b.updated_at || b.created_at || 0) - new Date(a.updated_at || a.created_at || 0));

  sorted.forEach(c => {
    const item = document.createElement('div');
    item.className = 'chat-item' + (c.chat_id === currentChatId || c.id === currentChatId ? ' active' : '');
    item.dataset.id = c.chat_id || c.id;

    const title = c.title || '新对话';
    const msgs = c.messages || [];
    const msgCount = msgs.length;

    item.innerHTML = `
      <span class="chat-item-title" onclick="loadChat('${c.chat_id || c.id}')">${escapeHtml(title)}</span>
      ${msgCount > 1 ? `<span class="chat-item-count">${msgCount}</span>` : ''}
      <button class="chat-item-delete" onclick="deleteChat('${c.chat_id || c.id}',event)" title="删除">&times;</button>`;

    (c.starred ? starredEl : recentEl).appendChild(item);
  });
}

function escapeHtml(s) {
  const d = document.createElement('div'); d.textContent = s; return d.innerHTML;
}

function loadChat(id) {
  socket.emit('load_chat', { chat_id: id });
}

function deleteChat(id, e) {
  e.stopPropagation();
  if (!confirm('确定删除此对话？')) return;
  socket.emit('delete_chat', { chat_id: id });
  if (id === currentChatId) newChat();
  setTimeout(() => loadChatList(), 200);
}

function loadChatList() { socket.emit('get_chat_list'); }

function setActiveChat(id) {
  document.querySelectorAll('.chat-item').forEach(el => el.classList.toggle('active', el.dataset.id === id));
}
function clearActiveChat() { document.querySelectorAll('.chat-item').forEach(el => el.classList.remove('active')); }

function saveChatToHistory(text) {
  if (!currentChatId) return;
  if (!chatHistory[currentChatId]) chatHistory[currentChatId] = [];
  chatHistory[currentChatId].push({ role: 'user', content: text, ts: Date.now() });
}

function updateChatTitleFromMsg(content) {
  if (!content) return;
  const title = content.replace(/\*\*/g, '').replace(/<[^>]+>/g, '').substring(0, 40);
  if (title.length > 3) {
    document.getElementById('chatTitle').textContent = title;
    socket.emit('rename_chat', { chat_id: currentChatId, title: title });
  }
}

/* ── Timed Tasks ── */

function showTimedTasks() {
  document.getElementById('timedPanel').style.display = 'flex';
  loadTimedTasks();
}

function hideTimedTasks() {
  document.getElementById('timedPanel').style.display = 'none';
}

function closeTimedTasks(e) {
  if (e.target === e.currentTarget) hideTimedTasks();
}

function loadTimedTasks() { socket.emit('list_timed_tasks'); }

function renderTimedList(list) {
  const body = document.getElementById('timedBody');
  if (!list || list.length === 0) {
    body.innerHTML = '<p class="timed-hint">暂无定时任务。通过对话设置提醒，如"30分钟后提醒我喝水"</p>';
    return;
  }
  body.innerHTML = '';
  list.forEach(t => {
    const item = document.createElement('div');
    item.className = 'timed-item';
    const statusMap = { pending: '⏳ 等待中', done: '✅ 已完成', cancelled: '❌ 已取消', expired: '⚠️ 已过期' };
    const statusLabel = statusMap[t.status] || t.status;
    item.innerHTML = `
      <div class="timed-item-content">
        <strong>${escapeHtml(t.message || '提醒')}</strong>
        <span style="color:#999;font-size:.78rem;margin-left:6px">[${statusLabel}]</span>
        ${t.repeat_interval ? '<span class="timed-item-repeat">重复</span>' : ''}
      </div>
      <div class="timed-item-time">
        触发时间：${new Date((t.trigger_time || 0) * 1000).toLocaleString('zh-CN')}
        ${t.repeat_interval ? '<br>重复间隔：' + t.repeat_interval : ''}
      </div>`;
    body.appendChild(item);
  });
}

/* ── Image Viewer ── */

function closeImageViewer() {
  document.getElementById('imageViewer').style.display = 'none';
}

document.addEventListener('click', e => {
  if (e.target.classList.contains('message-image')) {
    document.getElementById('imageViewerImg').src = e.target.src;
    document.getElementById('imageViewer').style.display = 'flex';
  }
});

/* ── Reminder Toast ── */

function showReminderToast(msg) {
  const existing = document.querySelector('.reminder-toast');
  if (existing) existing.remove();

  const toast = document.createElement('div');
  toast.className = 'reminder-toast';
  toast.innerHTML = `<div class="reminder-toast-title">⏰ 提醒时间到</div><div class="reminder-toast-msg">${escapeHtml(msg)}</div>`;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 8000);
}

/* ── Init ── */
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('chatInput').focus();
});
