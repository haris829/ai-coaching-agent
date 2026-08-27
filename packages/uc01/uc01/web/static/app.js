/* UC-01 reference client.
 *
 * Design rule: this file renders what the API says and nothing else. It contains no
 * authorization logic and no availability rules of its own — every disabled mode, every
 * reason string and every notice comes from the server, and the server re-validates the
 * submission. Disabling a control here is a usability affordance, never a security
 * control.
 */

'use strict';

const API = '/api/v1';

const state = {
  token: 'dev-alice',
  scenarios: { naric: '', courses: '', cases: '', profile: '' },
  bootstrap: null,
  selectedMode: null,
  continueWithoutCalibration: false,
};

const el = (id) => document.getElementById(id);

/* ------------------------------------------------------------------ *
 * HTTP
 * ------------------------------------------------------------------ */

function headers() {
  const out = { Authorization: `Bearer ${state.token}` };
  const scenarioParts = Object.entries(state.scenarios)
    .filter(([, value]) => value)
    .map(([key, value]) => `${key}=${value}`);
  if (scenarioParts.length) out['X-Dev-Scenarios'] = scenarioParts.join(',');
  return out;
}

async function apiGet(path) {
  const response = await fetch(`${API}${path}`, { headers: headers() });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw { status: response.status, body };
  return body;
}

async function apiPost(path, payload) {
  const response = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: { ...headers(), 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw { status: response.status, body };
  return body;
}

/* ------------------------------------------------------------------ *
 * Rendering helpers
 * ------------------------------------------------------------------ */

const MODE_LABELS = {
  'free-form': 'Free-form',
  'course-linked': 'Course-linked',
  'case-linked': 'Case-linked',
};

const MODE_DESCRIPTIONS = {
  'free-form': 'Ask about anything. Always available.',
  'course-linked': 'Coach me through a specific lesson in one of my courses.',
  'case-linked': 'Work on one of my case files.',
};

function renderNotices(target, notices) {
  target.replaceChildren();
  (notices || []).forEach((notice) => {
    const item = document.createElement('li');
    item.dataset.severity = notice.severity || 'info';
    const code = document.createElement('span');
    code.className = 'notice-code';
    code.textContent = notice.code;
    const text = document.createElement('span');
    text.textContent = notice.message;
    item.append(code, text);
    target.append(item);
  });
}

function renderModes(modes) {
  const list = el('mode-list');
  list.replaceChildren();

  modes.forEach((mode) => {
    const label = document.createElement('label');
    label.className = 'mode';
    label.dataset.available = String(mode.available);
    label.dataset.mode = mode.mode;

    const input = document.createElement('input');
    input.type = 'radio';
    input.name = 'mode';
    input.value = mode.mode;
    input.disabled = !mode.available;
    input.checked = mode.mode === state.selectedMode;
    input.setAttribute('aria-describedby', `mode-reason-${mode.mode}`);
    input.addEventListener('change', () => {
      state.selectedMode = mode.mode;
      syncPickers();
    });

    const body = document.createElement('span');
    const title = document.createElement('span');
    title.className = 'mode__title';
    title.textContent = MODE_LABELS[mode.mode] || mode.mode;
    const reason = document.createElement('span');
    reason.className = 'mode__reason';
    reason.id = `mode-reason-${mode.mode}`;
    // The reason text for a disabled mode is always the server's wording.
    reason.textContent = mode.available
      ? MODE_DESCRIPTIONS[mode.mode] || ''
      : mode.reason || 'Not available right now.';
    body.append(title, reason);

    const status = document.createElement('span');
    status.className = 'mode__status';
    status.textContent = mode.available ? 'Available' : 'Disabled';

    label.append(input, body, status);
    list.append(label);
  });
}

function renderCourses(courses) {
  const select = el('course-select');
  select.replaceChildren();
  courses.forEach((course) => {
    const option = document.createElement('option');
    option.value = course.course_id;
    option.textContent = course.title;
    select.append(option);
  });
  // The change listener is registered once, at the bottom of this file.
  renderLessons();
}

function currentCourse() {
  const courses = state.bootstrap ? state.bootstrap.courses : [];
  return courses.find((course) => course.course_id === el('course-select').value) || null;
}

function renderLessons() {
  const select = el('lesson-select');
  const course = currentCourse();
  select.replaceChildren();
  const lessons = course ? course.lessons : [];
  lessons.forEach((lesson) => {
    const option = document.createElement('option');
    option.value = lesson.lesson_id;
    option.textContent = lesson.title;
    select.append(option);
  });
  const hint = el('course-hint');
  if (course && lessons.length === 0) {
    hint.textContent = 'This course has no lessons yet, so it cannot be used for a session.';
    select.disabled = true;
  } else {
    hint.textContent = '';
    select.disabled = false;
  }
}

function renderCases(caseFiles) {
  const select = el('case-select');
  select.replaceChildren();
  caseFiles.forEach((caseFile) => {
    const option = document.createElement('option');
    option.value = caseFile.case_id;
    option.textContent = caseFile.matter_reference
      ? `${caseFile.title} (${caseFile.matter_reference})`
      : caseFile.title;
    select.append(option);
  });
}

function renderNaric(naric) {
  const block = el('naric-block');
  const summary = el('naric-summary');
  const notice = el('naric-notice');
  const continueLabel = el('naric-continue-label');

  summary.textContent = naric.is_fallback
    ? `Explanations will use Level ${naric.level} by default.`
    : `Explanations are calibrated to your NARIC Level ${naric.level}.`;
  notice.textContent = naric.notice || '';
  notice.hidden = !naric.notice;

  // NARIC problems never disable the session; they only add this option.
  continueLabel.hidden = !naric.offer_continue_without_calibration;
  el('naric-continue').checked = state.continueWithoutCalibration;
  block.hidden = false;
}

function syncPickers() {
  const mode = state.selectedMode;
  el('course-picker').hidden = mode !== 'course-linked';
  el('case-picker').hidden = mode !== 'case-linked';
  el('start-session').disabled = !mode;
}

/* ------------------------------------------------------------------ *
 * Bootstrap
 * ------------------------------------------------------------------ */

async function loadBootstrap() {
  el('status').hidden = false;
  el('status').textContent = 'Loading your coaching session…';
  el('setup').setAttribute('aria-busy', 'true');
  el('load-error').hidden = true;
  el('session-view').hidden = true;
  el('open-error').hidden = true;

  try {
    const data = await apiGet(
      `/session-bootstrap?continue_without_calibration=${state.continueWithoutCalibration}`
    );
    state.bootstrap = data;

    el('mock-badge').hidden = !(data.integrations && data.integrations.using_mock_adapters);
    if (data.integrations && data.integrations.warning) {
      el('mock-badge').title = data.integrations.warning;
    }

    // Default to the first available mode (free-form is always available).
    const firstAvailable = data.modes.find((mode) => mode.available);
    if (!state.selectedMode || !data.modes.some((m) => m.mode === state.selectedMode && m.available)) {
      state.selectedMode = firstAvailable ? firstAvailable.mode : null;
    }

    el('greeting-preview').textContent = data.greeting_preview.text;
    renderNotices(el('notices'), data.notices);
    renderModes(data.modes);
    renderCourses(data.courses);
    renderCases(data.case_files);
    renderNaric(data.naric);
    syncPickers();

    el('setup').hidden = false;
    el('status').hidden = true;
  } catch (error) {
    // Even a total bootstrap failure keeps the interface usable: retry is offered.
    el('status').hidden = true;
    el('load-error').hidden = false;
    el('load-error-text').textContent = safeMessage(
      error,
      'We could not load your coaching session. Please try again.'
    );
  } finally {
    el('setup').removeAttribute('aria-busy');
  }
}

function safeMessage(error, fallback) {
  if (error && error.body && error.body.error && error.body.error.message) {
    return error.body.error.message;
  }
  return fallback;
}

/* ------------------------------------------------------------------ *
 * Opening a session
 * ------------------------------------------------------------------ */

async function openSession(event) {
  event.preventDefault();
  const button = el('start-session');
  button.disabled = true;
  el('submit-status').textContent = 'Opening your session…';
  el('open-error').hidden = true;

  const payload = {
    mode: state.selectedMode,
    continue_without_calibration: el('naric-continue').checked,
    on_dependency_failure: el('fallback-free-form').checked ? 'fallback_free_form' : 'fail',
  };
  if (state.selectedMode === 'course-linked') {
    payload.course_id = el('course-select').value || null;
    payload.lesson_id = el('lesson-select').value || null;
  }
  if (state.selectedMode === 'case-linked') {
    payload.case_id = el('case-select').value || null;
  }

  try {
    const data = await apiPost('/sessions', payload);
    showSession(data);
  } catch (error) {
    el('open-error').hidden = false;
    el('open-error-text').textContent = safeMessage(
      error,
      'We could not open that session. Please try again.'
    );
    const recovery = error && error.body ? error.body.recovery : null;
    el('open-error-recovery').textContent = recovery && recovery.available_modes
      ? `You can still start: ${recovery.available_modes
          .map((mode) => MODE_LABELS[mode] || mode)
          .join(', ')}.`
      : '';
    // Refresh availability so the picker reflects the server's current view.
    await loadBootstrap();
    el('open-error').hidden = false;
  } finally {
    button.disabled = false;
    el('submit-status').textContent = '';
  }
}

function showSession(data) {
  el('setup').hidden = true;
  el('open-error').hidden = true;
  el('session-greeting').textContent = data.greeting.text;
  renderNotices(el('session-notices'), data.notices);

  const facts = el('session-facts');
  facts.replaceChildren();
  const linked = data.session.linked_resource;
  const rows = [
    ['Session id', data.session.session_id],
    ['Mode', data.session.session_type],
    ['Status', data.session.status],
    ['Explanation level', String(data.session.explanation_level)],
    ['Level source', data.session.naric_level_source],
    ['Linked resource', linked ? `${linked.label}${linked.secondary_label ? ' / ' + linked.secondary_label : ''}` : '—'],
    ['Degraded dependencies', data.session.degraded_dependencies.join(', ') || 'none'],
  ];
  if (data.session.downgraded_from) rows.push(['Downgraded from', data.session.downgraded_from]);
  rows.forEach(([key, value]) => {
    const dt = document.createElement('dt');
    dt.textContent = key;
    const dd = document.createElement('dd');
    dd.textContent = value;
    facts.append(dt, dd);
  });

  el('session-view').hidden = false;
  el('session-view').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* ------------------------------------------------------------------ *
 * Developer panel
 * ------------------------------------------------------------------ */

async function initDevPanel() {
  let context;
  try {
    context = await apiGet('/dev/context');
  } catch (error) {
    return; // dev mode off: the panel simply does not appear
  }
  const panel = el('dev-panel');
  panel.hidden = false;

  const userSelect = el('dev-user');
  context.users.forEach((user) => {
    const option = document.createElement('option');
    option.value = user.token;
    option.textContent = user.label;
    userSelect.append(option);
  });
  userSelect.value = state.token;

  Object.entries(context.scenario_options).forEach(([key, options]) => {
    const select = el(`dev-${key}`);
    if (!select) return;
    const inherit = document.createElement('option');
    inherit.value = '';
    inherit.textContent = `(server default: ${context.scenarios[key]})`;
    select.append(inherit);
    options.forEach((value) => {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = value;
      select.append(option);
    });
  });

  if (!context.scenario_header_enabled) {
    el('dev-apply').disabled = true;
    el('dev-apply').title = 'Scenario header is disabled by configuration.';
  }

  el('dev-apply').addEventListener('click', () => {
    state.token = userSelect.value;
    state.scenarios = {
      naric: el('dev-naric').value,
      courses: el('dev-courses').value,
      cases: el('dev-cases').value,
      profile: el('dev-profile').value,
    };
    state.selectedMode = null;
    loadBootstrap();
  });
}

/* ------------------------------------------------------------------ *
 * Wiring
 * ------------------------------------------------------------------ */

el('open-form').addEventListener('submit', openSession);
el('retry-bootstrap').addEventListener('click', loadBootstrap);
el('open-error-dismiss').addEventListener('click', () => {
  el('open-error').hidden = true;
});
el('new-session').addEventListener('click', () => {
  el('session-view').hidden = true;
  loadBootstrap();
});
el('course-select').addEventListener('change', renderLessons);
el('naric-continue').addEventListener('change', (event) => {
  state.continueWithoutCalibration = event.target.checked;
  loadBootstrap();
});

initDevPanel().finally(loadBootstrap);
