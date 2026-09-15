'use strict';

const $ = (id) => document.getElementById(id);
const els = {
  layout: $('layout'),
  main: $('main'),
  greeting: $('greeting'),
  messages: $('messages'),
  form: $('composer'),
  input: $('input'),
  send: $('send'),
  newChat: $('newChat'),
  modelButton: $('modelButton'),
  modelLabel: $('modelLabel'),
  modelMenu: $('modelMenu'),
  sidebar: $('sidebar'),
  sidebarToggle: $('sidebarToggle'),
  sidebarNewChat: $('sidebarNewChat'),
  historyList: $('historyList'),
};

let models = [];
let currentModel = localStorage.getItem('gemini.model') || '';
let history = [];
let busy = false;

/* ---------- 대화 히스토리 (localStorage) ---------- */
const SESSIONS_KEY = 'gemini.sessions';
const ACTIVE_KEY = 'gemini.activeSessionId';

function loadSessions() {
  try {
    const raw = JSON.parse(localStorage.getItem(SESSIONS_KEY));
    return Array.isArray(raw) ? raw : [];
  } catch {
    return [];
  }
}

function saveSessions() {
  try {
    localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions));
  } catch {
    /* localStorage 를 사용할 수 없으면 히스토리는 저장하지 않는다 */
  }
}

let sessions = loadSessions();
let activeId = localStorage.getItem(ACTIVE_KEY) || null;

function renderHistory() {
  els.historyList.innerHTML = '';
  for (const s of sessions) {
    const li = document.createElement('li');
    li.className = `history-item${s.id === activeId ? ' active' : ''}`;
    li.dataset.id = s.id;

    const title = document.createElement('span');
    title.className = 'history-title';
    title.textContent = s.title || '새 채팅';
    li.appendChild(title);

    const del = document.createElement('button');
    del.className = 'history-delete';
    del.type = 'button';
    del.title = '삭제';
    del.innerHTML = '<span class="material-symbols-outlined">close</span>';
    del.addEventListener('click', (e) => {
      e.stopPropagation();
      deleteSession(s.id);
    });
    li.appendChild(del);

    li.addEventListener('click', () => openSession(s.id));
    els.historyList.appendChild(li);
  }
}

function closeSidebarMobile() {
  els.layout.classList.remove('sidebar-open');
}

function startNewChat() {
  history = [];
  activeId = null;
  localStorage.removeItem(ACTIVE_KEY);
  els.messages.innerHTML = '';
  els.greeting.hidden = false;
  renderHistory();
  closeSidebarMobile();
  els.input.focus();
}

function openSession(id) {
  const session = sessions.find((s) => s.id === id);
  if (!session) {
    localStorage.removeItem(ACTIVE_KEY);
    activeId = null;
    return;
  }
  activeId = id;
  localStorage.setItem(ACTIVE_KEY, id);
  history = session.messages.map((m) => ({ ...m }));

  els.messages.innerHTML = '';
  els.greeting.hidden = history.length > 0;
  for (const m of history) {
    addTurn(m.role, m.text, m.role === 'model' ? { tag: m.tag || currentModel } : {});
  }
  renderHistory();
  closeSidebarMobile();
  scrollToBottom();
}

function deleteSession(id) {
  sessions = sessions.filter((s) => s.id !== id);
  saveSessions();
  if (id === activeId) startNewChat();
  else renderHistory();
}

function ensureSession(firstText) {
  if (activeId) {
    const existing = sessions.find((s) => s.id === activeId);
    if (existing) return existing;
  }
  const session = {
    id: crypto.randomUUID ? crypto.randomUUID() : String(Date.now()),
    title: firstText.length > 40 ? `${firstText.slice(0, 40)}…` : firstText,
    createdAt: Date.now(),
    messages: [],
  };
  sessions.unshift(session);
  activeId = session.id;
  localStorage.setItem(ACTIVE_KEY, session.id);
  saveSessions();
  renderHistory();
  return session;
}

function persistActiveSession() {
  if (!activeId) return;
  const session = sessions.find((s) => s.id === activeId);
  if (!session) return;
  session.messages = history.map((m) => ({ ...m }));
  saveSessions();
}

els.sidebarNewChat.addEventListener('click', startNewChat);
els.sidebarToggle.addEventListener('click', () => els.layout.classList.toggle('sidebar-open'));

/* ---------- 모델 선택 ---------- */
async function loadModels() {
  try {
    const res = await fetch('/api/models');
    const data = await res.json();
    models = data.models || [];
  } catch {
    models = [];
  }
  if (!models.length) {
    els.modelLabel.textContent = '모델 없음';
    return;
  }
  if (!models.some((m) => m.id === currentModel)) currentModel = models[0].id;
  renderModelMenu();
  setModel(currentModel);
}

function renderModelMenu() {
  els.modelMenu.innerHTML = '';
  for (const m of models) {
    const li = document.createElement('li');
    li.setAttribute('role', 'option');
    li.dataset.id = m.id;
    li.innerHTML = `<span>${m.label}</span><span class="model-id">${m.id}</span>`;
    li.addEventListener('click', () => {
      setModel(m.id);
      toggleMenu(false);
    });
    els.modelMenu.appendChild(li);
  }
}

function setModel(id) {
  currentModel = id;
  localStorage.setItem('gemini.model', id);
  const m = models.find((x) => x.id === id);
  els.modelLabel.textContent = m ? m.label : id;
  for (const li of els.modelMenu.children) {
    li.setAttribute('aria-selected', String(li.dataset.id === id));
  }
}

function toggleMenu(open) {
  const next = open ?? els.modelMenu.hidden;
  els.modelMenu.hidden = !next;
  els.modelButton.setAttribute('aria-expanded', String(next));
}

els.modelButton.addEventListener('click', (e) => {
  e.stopPropagation();
  toggleMenu();
});
document.addEventListener('click', () => toggleMenu(false));

/* ---------- 입력창 동작 ---------- */
function autoGrow() {
  els.input.style.height = 'auto';
  els.input.style.height = `${els.input.scrollHeight}px`;
  els.send.disabled = busy || !els.input.value.trim();
}
els.input.addEventListener('input', autoGrow);
els.input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    els.form.requestSubmit();
  }
});

/* ---------- 메시지 렌더링 ---------- */
function addTurn(role, text, { tag } = {}) {
  els.greeting.hidden = true;
  const turn = document.createElement('div');
  turn.className = `turn ${role}`;
  if (tag) {
    const label = document.createElement('div');
    label.className = 'model-tag';
    label.textContent = tag;
    turn.appendChild(label);
  }
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.textContent = text;
  turn.appendChild(bubble);
  els.messages.appendChild(turn);
  scrollToBottom();
  return bubble;
}

function scrollToBottom() {
  els.main.scrollTop = els.main.scrollHeight;
}

function addSources(bubble, sources) {
  const box = document.createElement('div');
  box.className = 'sources';
  const label = document.createElement('div');
  label.className = 'sources-label';
  label.textContent = '출처';
  box.appendChild(label);
  const list = document.createElement('ul');
  for (const s of sources) {
    const li = document.createElement('li');
    const a = document.createElement('a');
    a.href = s.uri;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.textContent = s.title || s.uri;
    li.appendChild(a);
    list.appendChild(li);
  }
  box.appendChild(list);
  bubble.after(box);
  scrollToBottom();
}

/* ---------- 전송 + 스트리밍 수신 ---------- */
els.form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = els.input.value.trim();
  if (!text || busy) return;

  busy = true;
  els.input.value = '';
  autoGrow();
  ensureSession(text);
  addTurn('user', text);
  history.push({ role: 'user', text });

  const bubble = addTurn('model', '', { tag: els.modelLabel.textContent });
  bubble.classList.add('blink');
  let answer = '';

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ model: currentModel, messages: history }),
    });

    if (!res.ok || !res.body) {
      const err = await res.json().catch(() => ({ error: `요청 실패 (${res.status})` }));
      throw new Error(err.error || '요청 실패');
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      for (const line of lines) {
        if (!line.startsWith('data:')) continue;
        const payload = JSON.parse(line.slice(5));
        if (payload.error) throw new Error(payload.error);
        if (payload.text) {
          answer += payload.text;
          bubble.textContent = answer;
          scrollToBottom();
        }
        if (payload.done && payload.sources?.length) {
          addSources(bubble, payload.sources);
        }
      }
    }
    history.push({ role: 'model', text: answer, tag: els.modelLabel.textContent });
  } catch (err) {
    bubble.classList.add('error');
    bubble.textContent = `오류: ${err.message}`;
  } finally {
    bubble.classList.remove('blink');
    busy = false;
    autoGrow();
    els.input.focus();
    persistActiveSession();
  }
});

/* ---------- 새 채팅 ---------- */
els.newChat.addEventListener('click', startNewChat);

renderHistory();
if (activeId) openSession(activeId);
loadModels();
autoGrow();
