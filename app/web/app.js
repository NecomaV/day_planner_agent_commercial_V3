'use strict';

const apiKeyInput = document.getElementById('apiKey');
const serviceKeyInput = document.getElementById('serviceKey');
const loadWeekButton = document.getElementById('loadWeek');
const prevWeekButton = document.getElementById('prevWeek');
const nextWeekButton = document.getElementById('nextWeek');
const weekLabel = document.getElementById('weekLabel');
const weekGrid = document.getElementById('weekGrid');
const backlogList = document.getElementById('backlogList');
const backlogSection = document.getElementById('backlogSection');
const statusEl = document.getElementById('status');
const themeToggle = document.getElementById('themeToggle');
const clearSelectionButton = document.getElementById('clearSelection');
const selectedTaskInfo = document.getElementById('selectedTaskInfo');
const markDoneButton = document.getElementById('markDone');
const deleteTaskButton = document.getElementById('deleteTask');
const unscheduleTaskButton = document.getElementById('unscheduleTask');
const moveDateInput = document.getElementById('moveDate');
const moveTimeInput = document.getElementById('moveTime');
const moveMinutesInput = document.getElementById('moveMinutes');
const moveTaskButton = document.getElementById('moveTask');
const searchInput = document.getElementById('searchInput');
const kindFilter = document.getElementById('kindFilter');
const showDoneCheckbox = document.getElementById('showDone');
const showBacklogCheckbox = document.getElementById('showBacklog');
const resetFiltersButton = document.getElementById('resetFilters');

const DATE_FMT = new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: 'short' });
const DAY_FMT = new Intl.DateTimeFormat('ru-RU', { weekday: 'short', day: '2-digit', month: 'short' });
const TIME_FMT = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' });

let weekStart = startOfWeek(new Date());
const taskIndex = new Map();
let selectedTaskId = null;
let selectedTaskElement = null;
let currentWeekDays = [];
let cachedDayTasks = [];
let cachedBacklog = [];

function setStatus(message, isError = false) {
  statusEl.textContent = message || '';
  statusEl.classList.toggle('error', isError);
}

function applyTheme(theme) {
  const finalTheme = theme === 'light' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', finalTheme);
  if (themeToggle) {
    themeToggle.textContent = `Тема: ${finalTheme === 'light' ? 'светлая' : 'темная'}`;
  }
  try {
    localStorage.setItem('dpCabinetTheme', finalTheme);
  } catch (err) {
    // ignore storage errors
  }
}

function loadTheme() {
  try {
    const stored = localStorage.getItem('dpCabinetTheme');
    if (stored) {
      applyTheme(stored);
      return;
    }
  } catch (err) {
    // ignore storage errors
  }
  applyTheme('dark');
}

function getFilterState() {
  return {
    query: (searchInput.value || '').trim().toLowerCase(),
    kind: kindFilter.value || 'all',
    showDone: Boolean(showDoneCheckbox.checked),
    showBacklog: Boolean(showBacklogCheckbox.checked),
  };
}

function taskMatchesFilters(task, filters) {
  if (!filters.showDone && task.is_done) {
    return false;
  }
  const taskKind = task.kind || 'other';
  if (filters.kind !== 'all' && taskKind !== filters.kind) {
    return false;
  }
  if (filters.query) {
    const haystack = `${task.title || ''} ${task.notes || ''}`.toLowerCase();
    if (!haystack.includes(filters.query)) {
      return false;
    }
  }
  return true;
}

function applyFilters() {
  if (!currentWeekDays.length) {
    return;
  }
  const filters = getFilterState();
  const filteredDays = cachedDayTasks.map((tasks) => tasks.filter((task) => taskMatchesFilters(task, filters)));
  const filteredBacklog = cachedBacklog.filter((task) => taskMatchesFilters(task, filters));
  taskIndex.clear();
  renderWeek(currentWeekDays, filteredDays);
  if (filters.showBacklog) {
    backlogSection.style.display = '';
    renderBacklog(filteredBacklog);
  } else {
    backlogSection.style.display = 'none';
  }
  restoreSelection();
}

function clearSelection() {
  if (selectedTaskElement) {
    selectedTaskElement.classList.remove('selected');
  }
  selectedTaskElement = null;
  selectedTaskId = null;
  selectedTaskInfo.textContent = 'Выберите задачу в календаре или бэклоге.';
  setActionButtonsEnabled(false);
  markDoneButton.textContent = 'Готово';
}

function setActionButtonsEnabled(enabled, task = null) {
  markDoneButton.disabled = !enabled;
  deleteTaskButton.disabled = !enabled;
  moveTaskButton.disabled = !enabled;
  if (!enabled) {
    unscheduleTaskButton.disabled = true;
    return;
  }
  unscheduleTaskButton.disabled = !task || !task.planned_start;
}

function setSelectedTask(task, element) {
  if (!task) {
    clearSelection();
    return;
  }
  if (selectedTaskElement) {
    selectedTaskElement.classList.remove('selected');
  }
  selectedTaskElement = element;
  selectedTaskId = task.id;
  if (selectedTaskElement) {
    selectedTaskElement.classList.add('selected');
  }
  updateSelectedTaskInfo(task);
  setActionButtonsEnabled(true, task);
  fillMoveInputs(task);
  markDoneButton.textContent = task.is_done ? 'Вернуть' : 'Готово';
}

function updateSelectedTaskInfo(task) {
  const parts = [`id=${task.id}`, task.title];
  if (task.planned_start) {
    const start = new Date(task.planned_start);
    const label = `${DATE_FMT.format(start)} ${TIME_FMT.format(start)}`;
    parts.push(`запланировано: ${label}`);
  } else if (task.due_at) {
    const due = new Date(task.due_at);
    parts.push(`дедлайн: ${DATE_FMT.format(due)}`);
  } else {
    parts.push('без времени');
  }
  if (task.is_done) {
    parts.push('статус: выполнено');
  }
  selectedTaskInfo.textContent = parts.join('\n');
}

function fillMoveInputs(task) {
  const now = new Date();
  let baseDate = now;
  let baseTime = { hour: 9, minute: 0 };
  if (task.planned_start) {
    const start = new Date(task.planned_start);
    baseDate = start;
    baseTime = { hour: start.getHours(), minute: start.getMinutes() };
  }
  moveDateInput.value = formatDateInput(baseDate);
  moveTimeInput.value = formatTimeInput(baseTime.hour, baseTime.minute);
  const duration = getTaskDurationMinutes(task);
  moveMinutesInput.value = duration ? String(duration) : '';
}

function formatDateInput(dateObj) {
  const year = dateObj.getFullYear();
  const month = String(dateObj.getMonth() + 1).padStart(2, '0');
  const day = String(dateObj.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function formatTimeInput(hour, minute) {
  return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`;
}

function getTaskDurationMinutes(task) {
  if (task.planned_start && task.planned_end) {
    const start = new Date(task.planned_start);
    const end = new Date(task.planned_end);
    const diff = Math.round((end - start) / 60000);
    if (diff > 0) return diff;
  }
  return task.estimate_minutes || 30;
}

function parseLocalDateTime(dateValue, timeValue) {
  if (!dateValue || !timeValue) return null;
  const [year, month, day] = dateValue.split('-').map(Number);
  const [hour, minute] = timeValue.split(':').map(Number);
  if (!year || !month || !day) return null;
  if (Number.isNaN(hour) || Number.isNaN(minute)) return null;
  return new Date(year, month - 1, day, hour, minute, 0, 0);
}

function toLocalIsoString(dateObj) {
  return [
    `${dateObj.getFullYear()}-${String(dateObj.getMonth() + 1).padStart(2, '0')}-${String(
      dateObj.getDate(),
    ).padStart(2, '0')}`,
    `${String(dateObj.getHours()).padStart(2, '0')}:${String(dateObj.getMinutes()).padStart(2, '0')}:00`,
  ].join('T');
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
  const userToken = apiKeyInput.value.trim();
  if (!userToken) {
    throw new Error('Введите API токен.');
  }
  const headers = {
    Authorization: `Bearer ${userToken}`,
  };
  const serviceKey = serviceKeyInput.value.trim();
  if (serviceKey) {
    headers['X-API-Key'] = serviceKey;
  }
  return headers;
}


async function fetchJson(url, headers) {
  const response = await fetch(url, { headers });
  if (!response.ok) {
    let detail = response.statusText || 'Запрос не выполнен';
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

function registerTask(task) {
  taskIndex.set(String(task.id), task);
}

function handleDragStart(event, taskId) {
  event.dataTransfer.effectAllowed = 'move';
  event.dataTransfer.setData('text/plain', String(taskId));
  try {
    event.dataTransfer.setData('text/task-id', String(taskId));
  } catch (err) {
    // ignore
  }
}

async function handleDropOnDay(event, dateStr) {
  event.preventDefault();
  const taskId = event.dataTransfer.getData('text/task-id') || event.dataTransfer.getData('text/plain');
  if (!taskId) return;
  const task = taskIndex.get(String(taskId));
  if (!task) return;

  const [year, month, day] = dateStr.split('-').map(Number);
  if (!year || !month || !day) return;

  let hour = 9;
  let minute = 0;
  if (task.planned_start) {
    const current = new Date(task.planned_start);
    hour = current.getHours();
    minute = current.getMinutes();
  }
  const start = new Date(year, month - 1, day, hour, minute, 0, 0);
  const duration = getTaskDurationMinutes(task);
  const end = new Date(start.getTime() + duration * 60000);

  await runAction('Перемещаю задачу', async () => {
    await patchTask(task.id, {
      planned_start: toLocalIsoString(start),
      planned_end: toLocalIsoString(end),
      schedule_source: 'manual',
    });
  });
}

async function handleDropOnSlot(event, dateStr, timeStr) {
  event.preventDefault();
  const taskId = event.dataTransfer.getData('text/task-id') || event.dataTransfer.getData('text/plain');
  if (!taskId) return;
  const task = taskIndex.get(String(taskId));
  if (!task) return;
  const start = parseLocalDateTime(dateStr, timeStr);
  if (!start) return;
  const duration = getTaskDurationMinutes(task);
  const end = new Date(start.getTime() + duration * 60000);
  await runAction('Перемещаю задачу', async () => {
    await patchTask(task.id, {
      planned_start: toLocalIsoString(start),
      planned_end: toLocalIsoString(end),
      schedule_source: 'manual',
    });
  });
}

async function handleDropOnBacklog(event) {
  event.preventDefault();
  const taskId = event.dataTransfer.getData('text/task-id') || event.dataTransfer.getData('text/plain');
  if (!taskId) return;
  const task = taskIndex.get(String(taskId));
  if (!task || !task.planned_start) return;
  await runAction('Перемещаю в бэклог', async () => {
    await patchTask(task.id, { planned_start: null, planned_end: null, schedule_source: 'manual' });
  });
}

function createTitleElement(task, item) {
  const title = document.createElement('div');
  title.className = 'task-title';
  title.textContent = task.title;
  title.addEventListener('dblclick', () => startTitleEdit(task, item, title));
  return title;
}

function createPriorityElement(task) {
  const pill = document.createElement('button');
  pill.className = 'priority-pill';
  pill.type = 'button';
  pill.dataset.priority = String(task.priority || 2);
  pill.textContent = `P${task.priority || 2}`;
  pill.title = 'Нажмите, чтобы сменить приоритет';
  pill.addEventListener('click', async (event) => {
    event.stopPropagation();
    const current = task.priority || 2;
    const next = current >= 3 ? 1 : current + 1;
    await runAction('Обновляю приоритет', async () => {
      await patchTask(task.id, { priority: next });
    });
  });
  return pill;
}

function createNotesElement(task, item) {
  const notes = document.createElement('div');
  notes.className = 'task-notes';
  notes.textContent = task.notes ? `Заметка: ${task.notes}` : 'Добавить заметку';
  if (!task.notes) {
    notes.classList.add('empty');
  }
  notes.addEventListener('dblclick', () => startNotesEdit(task, item, notes));
  return notes;
}

function createTimeElement(task, item) {
  const timeEl = document.createElement('time');
  timeEl.textContent = formatTimeRange(task);
  timeEl.addEventListener('dblclick', () => startTimeEdit(task, item, timeEl));
  return timeEl;
}

function renderTask(task, dayDateStr = null) {
  const item = document.createElement('div');
  item.className = 'task';
  if (task.is_done) {
    item.classList.add('done');
  }
  item.dataset.taskId = String(task.id);
  if (dayDateStr) {
    item.dataset.day = dayDateStr;
  }
  item.addEventListener('click', () => setSelectedTask(task, item));
  item.setAttribute('draggable', 'true');
  item.addEventListener('dragstart', (event) => handleDragStart(event, task.id));
  const timeLabel = formatTimeRange(task);
  if (timeLabel) {
    item.appendChild(createTimeElement(task, item));
  }
  item.appendChild(createTitleElement(task, item));
  const meta = document.createElement('div');
  meta.className = 'task-meta';
  meta.appendChild(createPriorityElement(task));
  meta.appendChild(createNotesElement(task, item));
  item.appendChild(meta);
  if (task.location_label) {
    const loc = document.createElement('div');
    loc.className = 'task-location';
    loc.textContent = `Локация: ${task.location_label}`;
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

function startTitleEdit(task, item, titleEl) {
  if (item.classList.contains('editing')) return;
  item.classList.add('editing');
  const input = document.createElement('input');
  input.type = 'text';
  input.value = task.title;
  input.className = 'task-edit-title';
  titleEl.replaceWith(input);
  input.focus();
  input.select();

  const finalize = async (save) => {
    const newTitle = input.value.trim();
    input.replaceWith(createTitleElement(task, item));
    item.classList.remove('editing');
    if (!save || !newTitle || newTitle === task.title) {
      return;
    }
    await runAction('Сохраняю название', async () => {
      await patchTask(task.id, { title: newTitle });
    });
  };

  input.addEventListener('blur', () => finalize(true));
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      input.blur();
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      finalize(false);
    }
  });
}

function startTimeEdit(task, item, timeEl) {
  if (item.classList.contains('editing')) return;
  const baseDate = task.planned_start ? new Date(task.planned_start) : null;
  const fallbackDay = item.dataset.day ? new Date(item.dataset.day) : null;
  const day = baseDate || fallbackDay;
  if (!day) {
    setStatus('Не удалось определить дату задачи.', true);
    return;
  }
  const current = baseDate || day;
  const input = document.createElement('input');
  input.type = 'time';
  input.value = formatTimeInput(current.getHours(), current.getMinutes());
  input.className = 'task-edit-time';
  item.classList.add('editing');
  timeEl.replaceWith(input);
  input.focus();

  const finalize = async (save) => {
    input.replaceWith(createTimeElement(task, item));
    item.classList.remove('editing');
    if (!save) return;
    const value = input.value;
    if (!value) return;
    const start = parseLocalDateTime(formatDateInput(day), value);
    if (!start) return;
    const duration = getTaskDurationMinutes(task);
    const end = new Date(start.getTime() + duration * 60000);
    await runAction('Сохраняю время', async () => {
      await patchTask(task.id, {
        planned_start: toLocalIsoString(start),
        planned_end: toLocalIsoString(end),
        schedule_source: 'manual',
      });
    });
  };

  input.addEventListener('blur', () => finalize(true));
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      input.blur();
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      finalize(false);
    }
  });
}

function startNotesEdit(task, item, notesEl) {
  if (item.classList.contains('editing')) return;
  item.classList.add('editing');
  const textarea = document.createElement('textarea');
  textarea.className = 'task-edit-notes';
  textarea.rows = 2;
  textarea.value = task.notes || '';
  notesEl.replaceWith(textarea);
  textarea.focus();

  const finalize = async (save) => {
    textarea.replaceWith(createNotesElement(task, item));
    item.classList.remove('editing');
    if (!save) return;
    const value = textarea.value.trim();
    const nextNotes = value.length ? value : null;
    if ((task.notes || null) === nextNotes) {
      return;
    }
    await runAction('Сохраняю заметку', async () => {
      await patchTask(task.id, { notes: nextNotes });
    });
  };

  textarea.addEventListener('blur', () => finalize(true));
  textarea.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      finalize(false);
    }
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      textarea.blur();
    }
  });
}

function buildTimeSlots() {
  const slots = [];
  for (let hour = 0; hour < 24; hour += 1) {
    slots.push({ hour, minute: 0 });
    slots.push({ hour, minute: 30 });
  }
  return slots;
}

function slotKeyFromDate(dateObj) {
  const hour = dateObj.getHours();
  const minute = dateObj.getMinutes() < 30 ? 0 : 30;
  return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`;
}

function renderWeek(days, dayTasks) {
  weekGrid.innerHTML = '';
  const slots = buildTimeSlots();
  days.forEach((day, idx) => {
    const card = document.createElement('div');
    card.className = 'day-card';
    const dayStr = isoDate(day);
    card.dataset.date = dayStr;
    card.addEventListener('dragover', (event) => {
      event.preventDefault();
      card.classList.add('drag-over');
    });
    card.addEventListener('dragleave', () => card.classList.remove('drag-over'));
    card.addEventListener('drop', async (event) => {
      card.classList.remove('drag-over');
      await handleDropOnDay(event, dayStr);
    });
    const heading = document.createElement('h3');
    heading.textContent = DAY_FMT.format(day);
    card.appendChild(heading);
    const tasks = dayTasks[idx] || [];
    const timeline = document.createElement('div');
    timeline.className = 'day-timeline';
    const buckets = new Map();
    tasks.forEach((task) => {
      registerTask(task);
      if (!task.planned_start) {
        return;
      }
      const start = new Date(task.planned_start);
      const key = slotKeyFromDate(start);
      const list = buckets.get(key) || [];
      list.push(task);
      buckets.set(key, list);
    });

    slots.forEach((slot) => {
      const timeStr = formatTimeInput(slot.hour, slot.minute);
      const row = document.createElement('div');
      row.className = 'time-slot';
      const label = document.createElement('div');
      label.className = 'time-label';
      label.textContent = timeStr;
      const drop = document.createElement('div');
      drop.className = 'time-drop';
      drop.addEventListener('dragover', (event) => {
        event.preventDefault();
        drop.classList.add('drag-over');
      });
      drop.addEventListener('dragleave', () => drop.classList.remove('drag-over'));
      drop.addEventListener('drop', async (event) => {
        drop.classList.remove('drag-over');
        await handleDropOnSlot(event, dayStr, timeStr);
      });
      const slotTasks = buckets.get(timeStr) || [];
      slotTasks.forEach((task) => {
        drop.appendChild(renderTask(task, dayStr));
      });
      row.appendChild(label);
      row.appendChild(drop);
      timeline.appendChild(row);
    });

    if (!tasks.length) {
      const empty = document.createElement('div');
      empty.className = 'backlog-meta';
      empty.textContent = 'Нет запланированных задач.';
      timeline.appendChild(empty);
    }

    card.appendChild(timeline);
    weekGrid.appendChild(card);
  });
}

function renderBacklog(backlog) {
  backlogList.innerHTML = '';
  if (!backlog.length) {
    backlogList.textContent = 'Бэклог пуст.';
    return;
  }
  backlog.forEach((task) => {
    registerTask(task);
    const item = document.createElement('div');
    item.className = 'backlog-item';
    item.dataset.taskId = String(task.id);
    item.addEventListener('click', () => setSelectedTask(task, item));
    item.setAttribute('draggable', 'true');
    item.addEventListener('dragstart', (event) => handleDragStart(event, task.id));
    const title = createTitleElement(task, item);
    item.appendChild(title);
    const meta = document.createElement('div');
    meta.className = 'task-meta';
    meta.appendChild(createPriorityElement(task));
    meta.appendChild(createNotesElement(task, item));
    item.appendChild(meta);
    const metaParts = [];
    if (task.due_at) {
      const due = new Date(task.due_at);
      metaParts.push(`Срок ${DATE_FMT.format(due)}`);
    }
    if (task.estimate_minutes) {
      metaParts.push(`${task.estimate_minutes} мин`);
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

function restoreSelection() {
  if (!selectedTaskId) {
    setActionButtonsEnabled(false);
    return;
  }
  const task = taskIndex.get(String(selectedTaskId));
  if (!task) {
    clearSelection();
    return;
  }
  const element = document.querySelector(`[data-task-id="${selectedTaskId}"]`);
  if (!element) {
    clearSelection();
    return;
  }
  setSelectedTask(task, element);
}

function saveAuth() {
  try {
    const payload = {
      apiKey: apiKeyInput.value.trim(),
      serviceKey: serviceKeyInput.value.trim(),
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
    if (payload.apiKey) apiKeyInput.value = payload.apiKey;
    if (payload.serviceKey) serviceKeyInput.value = payload.serviceKey;
  } catch (err) {
    // ignore storage errors
  }
}


async function patchTask(taskId, payload) {
  const headers = buildHeaders();
  headers['Content-Type'] = 'application/json';
  const response = await fetch(`/tasks/${taskId}`, {
    method: 'PATCH',
    headers,
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    let detail = response.statusText || 'Запрос не выполнен';
    try {
      const body = await response.json();
      if (body && body.detail) detail = body.detail;
    } catch (err) {
      // ignore
    }
    throw new Error(`${detail} (${response.status})`);
  }
  return response.json();
}

async function deleteTask(taskId) {
  const headers = buildHeaders();
  const response = await fetch(`/tasks/${taskId}`, {
    method: 'DELETE',
    headers,
  });
  if (!response.ok) {
    let detail = response.statusText || 'Запрос не выполнен';
    try {
      const body = await response.json();
      if (body && body.detail) detail = body.detail;
    } catch (err) {
      // ignore
    }
    throw new Error(`${detail} (${response.status})`);
  }
  return response.json();
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
  setStatus('Загружаю...');

  const days = weekDays(weekStart);
  weekLabel.textContent = formatWeekLabel(days);
  weekGrid.innerHTML = '';
  backlogList.textContent = 'Загружаю...';

  try {
    const dayRequests = days.map((day) => fetchJson(`/tasks/day?date=${isoDate(day)}`, headers));
    const backlogRequest = fetchJson('/tasks/backlog', headers);
    const [dayTasks, backlog] = await Promise.all([Promise.all(dayRequests), backlogRequest]);
    currentWeekDays = days;
    cachedDayTasks = dayTasks;
    cachedBacklog = backlog;
    applyFilters();
    const total = dayTasks.reduce((sum, list) => sum + list.length, 0);
    setStatus(`Загружено задач: ${total}.`);
  } catch (err) {
    setStatus(err.message || 'Не удалось загрузить данные.', true);
    backlogList.textContent = 'Не удалось загрузить бэклог.';
  }
}

function getSelectedTask() {
  if (!selectedTaskId) return null;
  return taskIndex.get(String(selectedTaskId)) || null;
}

async function runAction(label, action) {
  setStatus(`${label}...`);
  try {
    await action();
    await loadWeek();
    setStatus(`${label} готово.`);
  } catch (err) {
    setStatus(err.message || 'Ошибка действия.', true);
  }
}

async function handleMarkDone() {
  const task = getSelectedTask();
  if (!task) {
    setStatus('Сначала выберите задачу.', true);
    return;
  }
  const nextDone = !task.is_done;
  const label = nextDone ? 'Отмечаю выполненной' : 'Возвращаю в работу';
  await runAction(label, async () => {
    await patchTask(task.id, { is_done: nextDone });
  });
}

async function handleDelete() {
  const task = getSelectedTask();
  if (!task) {
    setStatus('Сначала выберите задачу.', true);
    return;
  }
  if (!window.confirm(`Удалить задачу "${task.title}"?`)) {
    return;
  }
  await runAction('Удаляю задачу', async () => {
    await deleteTask(task.id);
    clearSelection();
  });
}

async function handleUnschedule() {
  const task = getSelectedTask();
  if (!task) {
    setStatus('Сначала выберите задачу.', true);
    return;
  }
  await runAction('Перемещаю в бэклог', async () => {
    await patchTask(task.id, { planned_start: null, planned_end: null, schedule_source: 'manual' });
  });
}

async function handleMove() {
  const task = getSelectedTask();
  if (!task) {
    setStatus('Сначала выберите задачу.', true);
    return;
  }
  const dateValue = moveDateInput.value;
  const timeValue = moveTimeInput.value;
  if (!dateValue || !timeValue) {
    setStatus('Укажите дату и время.', true);
    return;
  }
  const start = parseLocalDateTime(dateValue, timeValue);
  if (!start) {
    setStatus('Неверная дата или время.', true);
    return;
  }
  const minutes = parseInt(moveMinutesInput.value, 10) || getTaskDurationMinutes(task);
  const end = new Date(start.getTime() + minutes * 60000);
  await runAction('Переношу задачу', async () => {
    await patchTask(task.id, {
      planned_start: toLocalIsoString(start),
      planned_end: toLocalIsoString(end),
      schedule_source: 'manual',
    });
  });
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
loadTheme();

if (themeToggle) {
  themeToggle.addEventListener('click', () => {
    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    applyTheme(current === 'dark' ? 'light' : 'dark');
  });
}

clearSelectionButton.addEventListener('click', () => clearSelection());
markDoneButton.addEventListener('click', handleMarkDone);
deleteTaskButton.addEventListener('click', handleDelete);
unscheduleTaskButton.addEventListener('click', handleUnschedule);
moveTaskButton.addEventListener('click', handleMove);

searchInput.addEventListener('input', () => applyFilters());
kindFilter.addEventListener('change', () => applyFilters());
showDoneCheckbox.addEventListener('change', () => applyFilters());
showBacklogCheckbox.addEventListener('change', () => applyFilters());
resetFiltersButton.addEventListener('click', () => {
  searchInput.value = '';
  kindFilter.value = 'all';
  showDoneCheckbox.checked = false;
  showBacklogCheckbox.checked = true;
  applyFilters();
});

backlogList.addEventListener('dragover', (event) => {
  event.preventDefault();
  backlogSection.classList.add('drag-over');
});
backlogList.addEventListener('dragleave', () => {
  backlogSection.classList.remove('drag-over');
});
backlogList.addEventListener('drop', async (event) => {
  backlogSection.classList.remove('drag-over');
  await handleDropOnBacklog(event);
});

setActionButtonsEnabled(false);

document.addEventListener('keydown', (event) => {
  if (!selectedTaskId) return;
  const target = event.target;
  if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) {
    return;
  }
  if (event.key === 'Delete' || event.key === 'Backspace') {
    event.preventDefault();
    handleDelete();
  }
  if (event.key === 'Enter') {
    event.preventDefault();
    handleMarkDone();
  }
  if (event.key === 'Escape') {
    event.preventDefault();
    clearSelection();
  }
});
