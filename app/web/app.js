'use strict';

const userIdInput = document.getElementById('userId');
const apiKeyInput = document.getElementById('apiKey');
const loadWeekButton = document.getElementById('loadWeek');
const prevWeekButton = document.getElementById('prevWeek');
const nextWeekButton = document.getElementById('nextWeek');
const weekLabel = document.getElementById('weekLabel');
const weekGrid = document.getElementById('weekGrid');
const backlogList = document.getElementById('backlogList');
const statusEl = document.getElementById('status');

const DATE_FMT = new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: 'short' });
const DAY_FMT = new Intl.DateTimeFormat('ru-RU', { weekday: 'short', day: '2-digit', month: 'short' });
const TIME_FMT = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' });

let weekStart = startOfWeek(new Date());

function setStatus(message, isError = false) {
  statusEl.textContent = message || '';
  statusEl.classList.toggle('error', isError);
}

function startOfWeek(date) {
  const d = new Date(date);
  d.setHours(0, 0, 0, 0);
  const day = d.getDay();
  const diff = (day + 6) % 7;
  d.setDate(d.getDate() - diff);
  return d;
}

function addDays(date, offset) {
  const d = new Date(date);
  d.setDate(d.getDate() + offset);
  return d;
}

function isoDate(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function weekDays(startDate) {
  const days = [];
  for (let i = 0; i < 7; i += 1) {
    days.push(addDays(startDate, i));
  }
  return days;
}

function formatWeekLabel(days) {
  if (!days.length) return '';
  return `${DATE_FMT.format(days[0])} - ${DATE_FMT.format(days[days.length - 1])}`;
}

function buildHeaders() {
  const userId = userIdInput.value.trim();
  if (!userId) {
    throw new Error('User ID is required.');
  }
  const headers = {
    'X-User-Id': userId,
  };
  const apiKey = apiKeyInput.value.trim();
  if (apiKey) {
    headers['X-API-Key'] = apiKey;
  }
  return headers;
}

async function fetchJson(url, headers) {
  const response = await fetch(url, { headers });
  if (!response.ok) {
    let detail = response.statusText || 'Request failed';
    try {
      const body = await response.json();
      if (body && body.detail) {
        detail = body.detail;
      }
    } catch (err) {
      // ignore parse errors
    }
    throw new Error(`${detail} (${response.status})`);
  }
  return response.json();
}

function renderTask(task) {
  const item = document.createElement('div');
  item.className = 'task';
  if (task.is_done) {
    item.classList.add('done');
  }
  const timeLabel = formatTimeRange(task);
  if (timeLabel) {
    const timeEl = document.createElement('time');
    timeEl.textContent = timeLabel;
    item.appendChild(timeEl);
  }
  const title = document.createElement('div');
  title.textContent = task.title;
  item.appendChild(title);
  if (task.location_label) {
    const loc = document.createElement('div');
    loc.className = 'task-location';
    loc.textContent = `Location: ${task.location_label}`;
    item.appendChild(loc);
  }
  return item;
}

function formatTimeRange(task) {
  if (!task.planned_start) return '';
  const start = new Date(task.planned_start);
  const startLabel = TIME_FMT.format(start);
  if (task.planned_end) {
    const end = new Date(task.planned_end);
    const endLabel = TIME_FMT.format(end);
    return `${startLabel}-${endLabel}`;
  }
  return startLabel;
}

function renderWeek(days, dayTasks) {
  weekGrid.innerHTML = '';
  days.forEach((day, idx) => {
    const card = document.createElement('div');
    card.className = 'day-card';
    const heading = document.createElement('h3');
    heading.textContent = DAY_FMT.format(day);
    card.appendChild(heading);
    const tasks = dayTasks[idx] || [];
    if (!tasks.length) {
      const empty = document.createElement('div');
      empty.className = 'backlog-meta';
      empty.textContent = 'No scheduled tasks.';
      card.appendChild(empty);
    } else {
      tasks.forEach((task) => card.appendChild(renderTask(task)));
    }
    weekGrid.appendChild(card);
  });
}

function renderBacklog(backlog) {
  backlogList.innerHTML = '';
  if (!backlog.length) {
    backlogList.textContent = 'Backlog is empty.';
    return;
  }
  backlog.forEach((task) => {
    const item = document.createElement('div');
    item.className = 'backlog-item';
    const title = document.createElement('div');
    title.textContent = task.title;
    item.appendChild(title);
    const metaParts = [];
    if (task.due_at) {
      const due = new Date(task.due_at);
      metaParts.push(`Due ${DATE_FMT.format(due)}`);
    }
    if (task.estimate_minutes) {
      metaParts.push(`${task.estimate_minutes} min`);
    }
    if (metaParts.length) {
      const meta = document.createElement('div');
      meta.className = 'backlog-meta';
      meta.textContent = metaParts.join(' | ');
      item.appendChild(meta);
    }
    backlogList.appendChild(item);
  });
}

function saveAuth() {
  try {
    const payload = {
      userId: userIdInput.value.trim(),
      apiKey: apiKeyInput.value.trim(),
    };
    localStorage.setItem('dpCabinetAuth', JSON.stringify(payload));
  } catch (err) {
    // ignore storage errors
  }
}

function loadAuth() {
  try {
    const raw = localStorage.getItem('dpCabinetAuth');
    if (!raw) return;
    const payload = JSON.parse(raw);
    if (payload.userId) userIdInput.value = payload.userId;
    if (payload.apiKey) apiKeyInput.value = payload.apiKey;
  } catch (err) {
    // ignore storage errors
  }
}

async function loadWeek() {
  let headers;
  try {
    headers = buildHeaders();
  } catch (err) {
    setStatus(err.message, true);
    return;
  }

  saveAuth();
  setStatus('Loading...');

  const days = weekDays(weekStart);
  weekLabel.textContent = formatWeekLabel(days);
  weekGrid.innerHTML = '';
  backlogList.textContent = 'Loading...';

  try {
    const dayRequests = days.map((day) => fetchJson(`/tasks/day?date=${isoDate(day)}`, headers));
    const backlogRequest = fetchJson('/tasks/backlog', headers);
    const [dayTasks, backlog] = await Promise.all([Promise.all(dayRequests), backlogRequest]);
    renderWeek(days, dayTasks);
    renderBacklog(backlog);
    const total = dayTasks.reduce((sum, list) => sum + list.length, 0);
    setStatus(`Loaded ${total} tasks.`);
  } catch (err) {
    setStatus(err.message || 'Failed to load data.', true);
    backlogList.textContent = 'Failed to load backlog.';
  }
}

loadWeekButton.addEventListener('click', loadWeek);
prevWeekButton.addEventListener('click', () => {
  weekStart = addDays(weekStart, -7);
  loadWeek();
});
nextWeekButton.addEventListener('click', () => {
  weekStart = addDays(weekStart, 7);
  loadWeek();
});

loadAuth();
