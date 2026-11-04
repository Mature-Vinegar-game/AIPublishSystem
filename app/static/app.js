/* ============================================================
   印制参数校验与成本测算 AI 系统 · 前端交互（原生 JS，无第三方依赖）
   - 提供：fetch 封装、toast、弹层、表单校验、AI 状态轮询
   - 所有请求均带 credentials: 'same-origin'
   - 页面卸载时自动清理轮询定时器
   ============================================================ */
(function () {
  'use strict';

  var POLL_INTERVAL_MS = 2000;
  var AI_STATUS_TEXT = { PENDING: '待执行', RUNNING: '运行中', SUCCEEDED: '完成', FAILED: '失败' };
  var AI_STATUS_CLASS = { PENDING: 'badge-gray', RUNNING: 'badge-blue', SUCCEEDED: 'badge-green', FAILED: 'badge-red' };
  var TOOL_TYPE_TEXT = { retrieval: '检索', validation: '校验', calculation: '计算' };
  var TOOL_STATUS_TEXT = { SUCCESS: '成功', OK: '成功', FAILED: '失败', ERROR: '失败', TIMEOUT: '超时' };
  var BASIS_TYPE_TEXT = {
    material: { label: '资料', cls: 'badge-blue' },
    rule: { label: '规则', cls: 'badge-amber' },
    calc: { label: '计算', cls: 'badge-gray' }
  };

  /* ---------------- 基础工具 ---------------- */

  function el(id) { return document.getElementById(id); }
  function qs(sel, root) { return (root || document).querySelector(sel); }
  function qsa(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function str(value) {
    if (value === null || value === undefined) { return ''; }
    return String(value);
  }

  function setText(id, value) {
    var node = el(id);
    if (node) { node.textContent = (value === null || value === undefined || value === '') ? '—' : str(value); }
  }

  function truncateText(value, max) {
    var s = str(value);
    if (!s) { return ''; }
    return s.length > max ? s.slice(0, max) + '…' : s;
  }

  function summarizeJson(value, max) {
    if (value === null || value === undefined || value === '') { return '—'; }
    var s;
    if (typeof value === 'string') {
      s = value;
    } else {
      try { s = JSON.stringify(value); } catch (e) { s = str(value); }
    }
    return truncateText(s || '—', max);
  }

  function prettyJson(value) {
    if (value === null || value === undefined) { return '（空）'; }
    if (typeof value === 'string') {
      try { return JSON.stringify(JSON.parse(value), null, 2); } catch (e) { return value; }
    }
    try { return JSON.stringify(value, null, 2); } catch (e) { return str(value); }
  }

  function formatMoney(value) {
    if (value === null || value === undefined || value === '') { return '—'; }
    var n = Number(value);
    if (!isFinite(n)) { return str(value); }
    return n.toFixed(2);
  }

  function asArray(data, keys) {
    if (Array.isArray(data)) { return data; }
    if (data && typeof data === 'object') {
      var names = keys || ['items', 'records', 'versions', 'list', 'tool_calls'];
      for (var i = 0; i < names.length; i++) {
        if (Array.isArray(data[names[i]])) { return data[names[i]]; }
      }
    }
    return [];
  }

  function normalizeCounters(counters) {
    if (typeof counters === 'string') {
      try { counters = JSON.parse(counters); } catch (e) { counters = {}; }
    }
    return counters && typeof counters === 'object' ? counters : {};
  }

  function setBusy(button, busy, busyText) {
    if (!button) { return; }
    if (busy) {
      if (!button.dataset.idleText) { button.dataset.idleText = button.textContent; }
      button.disabled = true;
      button.classList.add('is-loading');
      if (busyText) { button.textContent = busyText; }
    } else {
      button.disabled = false;
      button.classList.remove('is-loading');
      if (button.dataset.idleText) { button.textContent = button.dataset.idleText; }
    }
  }

  /* ---------------- toast ---------------- */

  var TOAST_ICONS = { success: '✓', error: '!', warn: '!', info: 'i' };

  function getToastRoot() {
    var root = el('toast-root');
    if (!root) {
      root = document.createElement('div');
      root.id = 'toast-root';
      root.className = 'toast-root';
      document.body.appendChild(root);
    }
    return root;
  }

  function removeToast(node) {
    if (!node || !node.parentNode) { return; }
    node.style.transition = 'opacity .16s ease, transform .16s ease';
    node.style.opacity = '0';
    node.style.transform = 'translateY(-6px)';
    window.setTimeout(function () {
      if (node.parentNode) { node.parentNode.removeChild(node); }
    }, 170);
  }

  function toast(message, type, timeout) {
    var kind = type || 'info';
    if (!message) { return; }
    var node = document.createElement('div');
    node.className = 'toast toast-' + kind;
    node.setAttribute('role', kind === 'error' ? 'alert' : 'status');

    var icon = document.createElement('span');
    icon.className = 'toast-icon';
    icon.textContent = TOAST_ICONS[kind] || 'i';

    var body = document.createElement('span');
    body.className = 'toast-text';
    body.textContent = str(message);

    var close = document.createElement('button');
    close.type = 'button';
    close.className = 'toast-close';
    close.setAttribute('aria-label', '关闭提示');
    close.textContent = '×';
    close.addEventListener('click', function () { removeToast(node); });

    node.appendChild(icon);
    node.appendChild(body);
    node.appendChild(close);
    getToastRoot().appendChild(node);
    window.setTimeout(function () { removeToast(node); }, timeout || (kind === 'error' ? 6500 : 4000));
  }

  function flash(message, type) {
    try { sessionStorage.setItem('printcost_flash', JSON.stringify({ message: message, type: type || 'success' })); } catch (e) { /* ignore */ }
  }

  function consumeFlash() {
    try {
      var raw = sessionStorage.getItem('printcost_flash');
      if (!raw) { return; }
      sessionStorage.removeItem('printcost_flash');
      var item = JSON.parse(raw);
      if (item && item.message) { window.setTimeout(function () { toast(item.message, item.type); }, 120); }
    } catch (e) { /* ignore */ }
  }

  /* ---------------- 请求封装 ---------------- */

  function pickMessage(payload) {
    if (!payload) { return ''; }
    if (typeof payload === 'string') { return payload; }
    if (typeof payload.detail === 'string') { return payload.detail; }
    if (Array.isArray(payload.detail)) {
      return payload.detail.map(function (d) {
        return (d && (d.msg || d.message)) || JSON.stringify(d);
      }).join('；');
    }
    if (payload.detail && typeof payload.detail === 'object') {
      return payload.detail.message || payload.detail.msg || JSON.stringify(payload.detail);
    }
    if (typeof payload.message === 'string') { return payload.message; }
    return '';
  }

  async function apiFetch(url, options) {
    var opts = options || {};
    var init = {
      method: opts.method || 'GET',
      credentials: 'same-origin',
      headers: Object.assign({ 'Accept': 'application/json' }, opts.headers || {})
    };
    if (opts.body !== undefined && opts.body !== null) {
      if (typeof opts.body === 'string') {
        init.body = opts.body;
      } else {
        init.headers['Content-Type'] = 'application/json';
        init.body = JSON.stringify(opts.body);
      }
    }

    var res;
    try {
      res = await fetch(url, init);
    } catch (e) {
      var netErr = new Error('网络请求失败，请确认服务已启动');
      netErr.cause = e;
      throw netErr;
    }

    var raw = '';
    var payload = null;
    try { raw = await res.text(); } catch (e) { raw = ''; }
    if (raw) {
      try { payload = JSON.parse(raw); } catch (e) { payload = null; }
    }

    if (!res.ok) {
      var message = pickMessage(payload) || (res.status === 401 ? '登录已过期，请重新登录' : ('请求失败（HTTP ' + res.status + '）'));
      var err = new Error(message);
      err.status = res.status;
      err.payload = payload;
      throw err;
    }

    if (payload && typeof payload === 'object' && !Array.isArray(payload) &&
        Object.prototype.hasOwnProperty.call(payload, 'code')) {
      if (payload.code !== 0 && payload.code !== '0') {
        throw new Error(payload.message || '业务处理失败');
      }
      if (Object.prototype.hasOwnProperty.call(payload, 'data')) { return payload.data; }
    }
    if (payload === null && raw) { return raw; }
    return payload;
  }

  /* ---------------- 弹层 ---------------- */

  var modalNode = null;
  var modalLastFocus = null;

  function closeModal() {
    if (!modalNode) { return; }
    modalNode.hidden = true;
    document.body.style.overflow = '';
    if (modalLastFocus && typeof modalLastFocus.focus === 'function') {
      try { modalLastFocus.focus(); } catch (e) { /* ignore */ }
    }
  }

  function ensureModal() {
    if (modalNode) { return modalNode; }
    modalNode = document.createElement('div');
    modalNode.className = 'modal-overlay';
    modalNode.id = 'app-modal';
    modalNode.hidden = true;

    var dialog = document.createElement('div');
    dialog.className = 'modal';
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    dialog.setAttribute('aria-labelledby', 'app-modal-title');

    var head = document.createElement('div');
    head.className = 'modal-head';
    var titleWrap = document.createElement('div');
    var title = document.createElement('h3');
    title.className = 'modal-title';
    title.id = 'app-modal-title';
    var sub = document.createElement('p');
    sub.className = 'modal-sub';
    sub.id = 'app-modal-sub';
    titleWrap.appendChild(title);
    titleWrap.appendChild(sub);
    var closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'modal-close';
    closeBtn.id = 'app-modal-close';
    closeBtn.setAttribute('aria-label', '关闭');
    closeBtn.textContent = '×';
    closeBtn.addEventListener('click', closeModal);
    head.appendChild(titleWrap);
    head.appendChild(closeBtn);

    var body = document.createElement('div');
    body.className = 'modal-body';
    body.id = 'app-modal-body';

    var foot = document.createElement('div');
    foot.className = 'modal-foot';
    var footBtn = document.createElement('button');
    footBtn.type = 'button';
    footBtn.className = 'btn btn-ghost';
    footBtn.textContent = '关闭';
    footBtn.addEventListener('click', closeModal);
    foot.appendChild(footBtn);

    dialog.appendChild(head);
    dialog.appendChild(body);
    dialog.appendChild(foot);
    modalNode.appendChild(dialog);

    modalNode.addEventListener('click', function (event) {
      if (event.target === modalNode) { closeModal(); }
    });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && modalNode && !modalNode.hidden) { closeModal(); }
    });

    document.body.appendChild(modalNode);
    return modalNode;
  }

  function showModal(titleText, content, subtitle) {
    ensureModal();
    modalLastFocus = document.activeElement;
    el('app-modal-title').textContent = str(titleText);
    var sub = el('app-modal-sub');
    if (subtitle) {
      sub.textContent = str(subtitle);
      sub.hidden = false;
    } else {
      sub.textContent = '';
      sub.hidden = true;
    }
    var body = el('app-modal-body');
    body.textContent = '';
    if (content instanceof Node) {
      body.appendChild(content);
    } else {
      var pre = document.createElement('pre');
      pre.textContent = str(content);
      body.appendChild(pre);
    }
    modalNode.hidden = false;
    document.body.style.overflow = 'hidden';
    el('app-modal-close').focus();
  }

  async function openBasisDetail(type, id, label, targetId) {
    if (!id) {
      toast('该依据缺少编号，无法查看详情', 'warn');
      return;
    }
    if (type === 'calc') {
      showModal('计算依据 · ' + (label || id),
        '该依据由本次 AI 工具计算生成，不是资料库记录。\n\n依据编号：' + id +
        (label ? '\n依据说明：' + label : ''),
        '结果 → 依据 → 计算过程');
      return;
    }

    var base = type === 'rule' ? '/api/rules' : '/api/materials';
    var codeKey = type === 'rule' ? 'rule_code' : 'material_no';
    var detail = null;

    var directId = targetId || (/^\d+$/.test(str(id)) ? id : '');
    if (directId) {
      try { detail = await apiFetch(base + '/' + encodeURIComponent(directId)); } catch (e) { detail = null; }
    }
    if (!detail) {
      try {
        var list = await apiFetch(base);
        var items = asArray(list, ['items', 'materials', 'rules', 'list']);
        var match = null;
        for (var i = 0; i < items.length; i++) {
          var it = items[i] || {};
          if (str(it[codeKey]) === str(id) || str(it.id) === str(id)) { match = it; break; }
        }
        if (match) {
          if (match.id !== undefined && match.id !== null && str(match.id) !== str(id)) {
            try { detail = await apiFetch(base + '/' + encodeURIComponent(match.id)); } catch (e) { detail = match; }
          } else {
            detail = match;
          }
        }
      } catch (e) {
        detail = null;
      }
    }

    if (!detail) {
      toast('未找到' + (type === 'rule' ? '规则' : '资料') + '「' + id + '」，依据可能已变更', 'warn', 6000);
      return;
    }

    var frag = document.createElement('div');
    var tip = document.createElement('p');
    tip.className = 'hint';
    tip.textContent = '接口：GET ' + base + '/{id}　｜　依据编号：' + id + '　｜　结果 → 依据 → 数据';
    var pre = document.createElement('pre');
    pre.textContent = prettyJson(detail);
    frag.appendChild(tip);
    frag.appendChild(pre);
    showModal((type === 'rule' ? '规则详情' : '资料详情') + ' · ' + (label || id), frag);
  }

  /* ---------------- 全局初始化 ---------------- */

  function initDemoAccounts() {
    qsa('[data-demo-user]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var form = qs('form[action="/login"]');
        if (!form) { return; }
        var user = qs('input[name="username"]', form);
        var pass = qs('input[name="password"]', form);
        if (user) { user.value = btn.getAttribute('data-demo-user') || ''; }
        if (pass) { pass.value = btn.getAttribute('data-demo-password') || ''; }
        if (pass) { pass.focus(); }
        toast('已填入演示账号，点击“登录”进入系统', 'info', 2600);
      });
    });
  }

  function initAutoSubmit() {
    qsa('[data-auto-submit]').forEach(function (control) {
      control.addEventListener('change', function () {
        if (control.form) { control.form.submit(); }
      });
    });
  }

  function initTableFilter() {
    qsa('[data-table-filter]').forEach(function (input) {
      var selector = input.getAttribute('data-table-filter');
      var table = selector ? qs(selector) : null;
      if (!table) { return; }
      var rows = qsa('tbody tr', table);
      input.addEventListener('input', function () {
        var keyword = str(input.value).trim().toLowerCase();
        rows.forEach(function (row) {
          row.hidden = !!keyword && str(row.textContent).toLowerCase().indexOf(keyword) === -1;
        });
      });
    });
  }

  function findFieldError(control) {
    var field = control ? control.closest('.field') : null;
    return field ? qs('.field-error', field) : null;
  }

  function setFieldError(control, message) {
    if (!control) { return; }
    var field = control.closest('.field');
    if (field) { field.classList.toggle('has-error', !!message); }
    var box = findFieldError(control);
    if (box) { box.textContent = message || ''; }
  }

  function validateJsonTextarea(textarea) {
    var value = str(textarea.value).trim();
    var message = '';
    if (!value) {
      message = '请填写 JSON 内容';
    } else {
      try { JSON.parse(value); } catch (e) { message = 'JSON 格式错误：' + e.message; }
    }
    setFieldError(textarea, message);
    return !message;
  }

  function initJsonForms() {
    qsa('textarea[data-json]').forEach(function (textarea) {
      textarea.addEventListener('blur', function () { validateJsonTextarea(textarea); });
      textarea.addEventListener('input', function () {
        var field = textarea.closest('.field');
        if (field && !field.classList.contains('has-error')) { return; }
        validateJsonTextarea(textarea);
      });
    });

    qsa('form.js-json-form').forEach(function (form) {
      form.addEventListener('submit', function (event) {
        var invalid = null;
        qsa('textarea[data-json]', form).forEach(function (textarea) {
          if (!validateJsonTextarea(textarea) && !invalid) { invalid = textarea; }
        });
        if (invalid) {
          event.preventDefault();
          invalid.focus();
          toast('请先修正 JSON 格式错误', 'error');
          return;
        }
        var submit = form.querySelector('button[type="submit"]');
        if (submit) { setBusy(submit, true, '提交中…'); }
      });
    });
  }

  function initJsonExamples() {
    var materialSamples = {
      paper: { price_per_ream: 85, grammage: 157, format_fit: ['16开', '32开'] },
      process: { price_per_1000: 120, unit: '元/千册' },
      binding: { price_per_1000: 300, unit: '元/千册' },
      rate: { price_per_1000_sheets_per_color: 35, unit: '元/千印张/色' },
      book: { note: '示例图书资料', pages: 224 }
    };
    var typeSelect = el('material-type');
    var content = el('material-content');
    var exampleBtn = el('btn-material-example');
    if (typeSelect && content) {
      var updatePlaceholder = function () {
        var sample = materialSamples[typeSelect.value] || materialSamples.paper;
        content.placeholder = JSON.stringify(sample);
      };
      typeSelect.addEventListener('change', updatePlaceholder);
      updatePlaceholder();
    }
    if (exampleBtn && content) {
      exampleBtn.addEventListener('click', function () {
        var sample = materialSamples[typeSelect ? typeSelect.value : 'paper'] || materialSamples.paper;
        content.value = JSON.stringify(sample, null, 2);
        validateJsonTextarea(content);
        content.focus();
      });
    }

    var ruleBtn = el('btn-rule-example');
    var ruleContent = el('rule-content');
    if (ruleBtn && ruleContent) {
      ruleBtn.addEventListener('click', function () {
        ruleContent.value = JSON.stringify({
          field: 'qty',
          min: 500,
          message: '印数不得低于 500 册',
          severity: 'error'
        }, null, 2);
        validateJsonTextarea(ruleContent);
        ruleContent.focus();
      });
    }
  }

  /* ---------------- 新建任务表单校验 ---------------- */

  function initTaskCreateForm() {
    var form = el('task-create-form');
    if (!form) { return; }

    var rules = [
      { id: 'title', message: '请填写任务标题', test: function (v) { return str(v).trim().length > 0; } },
      { id: 'goal', message: '请填写测算目标', test: function (v) { return str(v).trim().length > 0; } },
      { id: 'format', message: '请选择开本', test: function (v) { return ['16开', '32开', '大32开'].indexOf(str(v)) !== -1; } },
      {
        id: 'print_sheet', message: '印张须大于 0 且不超过 50', test: function (v) {
          var n = Number(v);
          return str(v).trim() !== '' && isFinite(n) && n > 0 && n <= 50;
        }
      },
      {
        id: 'qty', message: '印数须为不小于 500 的整数', test: function (v) {
          var n = Number(v);
          return str(v).trim() !== '' && isFinite(n) && Math.floor(n) === n && n >= 500;
        }
      },
      { id: 'paper_type', message: '请填写纸张名称（如：铜版纸157g）', test: function (v) { return str(v).trim().length > 0; } },
      { id: 'binding', message: '请选择装订方式', test: function (v) { return ['胶装', '骑马订', '精装'].indexOf(str(v)) !== -1; } }
    ];

    function applyRule(rule) {
      var control = el(rule.id);
      if (!control) { return true; }
      var ok = rule.test(control.value);
      setFieldError(control, ok ? '' : rule.message);
      return ok;
    }

    rules.forEach(function (rule) {
      var control = el(rule.id);
      if (!control) { return; }
      var evt = control.tagName === 'SELECT' ? 'change' : 'input';
      control.addEventListener(evt, function () { applyRule(rule); });
    });

    form.addEventListener('submit', function (event) {
      var firstInvalid = null;
      rules.forEach(function (rule) {
        if (!applyRule(rule) && !firstInvalid) { firstInvalid = el(rule.id); }
      });
      if (firstInvalid) {
        event.preventDefault();
        firstInvalid.focus();
        toast('请检查表单中标红的字段', 'error');
        return;
      }
      var submit = form.querySelector('button[type="submit"]');
      if (submit) { setBusy(submit, true, '正在创建…'); }
    });
  }

  function initLoginForm() {
    var form = qs('form[action="/login"]');
    if (!form) { return; }
    form.addEventListener('submit', function () {
      var submit = form.querySelector('button[type="submit"]');
      if (submit) { setBusy(submit, true, '登录中…'); }
    });
  }

  /* ---------------- AI 轮询与结果渲染 ---------------- */

  function initTaskDetail() {
    var root = el('task-detail');
    if (!root) { return; }

    var taskId = str(root.getAttribute('data-task-id'));
    var revision = Number(root.getAttribute('data-revision') || 1);
    var canEdit = root.getAttribute('data-can-edit') === '1';
    var currentAiId = str(root.getAttribute('data-ai-id'));
    var currentAiStatus = str(root.getAttribute('data-ai-status'));
    var resultSectionNode = el('section-result');
    var lastResultAiTaskId = resultSectionNode ? str(resultSectionNode.getAttribute('data-result-ai-id')) : '';
    var timeoutMs = Number(root.getAttribute('data-ai-timeout-ms') || 120000);
    if (!isFinite(timeoutMs) || timeoutMs < 10000) { timeoutMs = 120000; }

    var pollTimer = null;
    var pollStart = 0;
    var pollCount = 0;
    var polling = false;
    var pollFailures = 0;

    function aiUrl(suffix) {
      return '/api/ai-tasks/' + encodeURIComponent(currentAiId) + (suffix || '');
    }

    function showProgress() {
      var node = el('ai-progress');
      if (node) { node.hidden = false; }
    }

    function hideProgress() {
      var node = el('ai-progress');
      if (node) { node.hidden = true; }
    }

    function showTimeout() {
      var node = el('ai-timeout-note');
      if (node) { node.hidden = false; }
    }

    function hideTimeout() {
      var node = el('ai-timeout-note');
      if (node) { node.hidden = true; }
    }

    function updateProgressText() {
      var node = el('ai-progress-text');
      if (!node) { return; }
      var elapsed = pollStart ? Math.round((Date.now() - pollStart) / 1000) : 0;
      node.textContent = 'AI 正在执行：读取资料 → 校验参数 → 计算成本 → 生成依据…（第 ' + pollCount + ' 次查询，已等待 ' + elapsed + 's）';
    }

    function showAiError(code, message) {
      var box = el('ai-error-box');
      if (box) { box.hidden = false; }
      setText('ai-error-code', code || 'AI_RUN_FAILED');
      setText('ai-error-message', message || 'AI 测算失败，请查看执行记录或重试。');
    }

    function hideAiError() {
      var box = el('ai-error-box');
      if (box) { box.hidden = true; }
      setText('ai-error-code', '');
      setText('ai-error-message', '');
    }

    function updateCounters(counters) {
      var c = normalizeCounters(counters);
      var distinct = c.distinct_types;
      if (Array.isArray(distinct)) { distinct = distinct.length; }
      if (distinct === undefined || distinct === null) {
        distinct = c.distinct_type_count !== undefined && c.distinct_type_count !== null ? c.distinct_type_count : 0;
      }
      var modelCalls = c.model_calls !== undefined && c.model_calls !== null ? c.model_calls : c.llm_calls;
      setText('ai-counter-rounds', c.rounds !== undefined && c.rounds !== null ? c.rounds : 0);
      setText('ai-counter-tools', c.tool_calls !== undefined && c.tool_calls !== null ? c.tool_calls : 0);
      setText('ai-counter-types', distinct);
      setText('ai-counter-model-calls', modelCalls !== undefined && modelCalls !== null ? modelCalls : 0);
    }

    function updateAiStatus(data) {
      if (!data) { return; }
      var status = str(data.status) || currentAiStatus;
      if (status) { currentAiStatus = status; }
      var badge = el('ai-status-badge');
      if (badge) {
        badge.textContent = AI_STATUS_TEXT[status] || status || '—';
        badge.className = 'badge badge-lg ' + (AI_STATUS_CLASS[status] || 'badge-gray');
      }

      if (data.model_mode) {
        var mode = el('ai-model-mode');
        if (mode) {
          mode.textContent = str(data.model_mode).toUpperCase();
          mode.className = 'mode-tag ' + (data.model_mode === 'mock' ? 'mode-mock' : 'mode-real');
        }
      }
      if (data.model_name) { setText('ai-model-name', data.model_name); }
      if (data.counters) { updateCounters(data.counters); }
      if (data.finished_at) { setText('ai-finished-at', data.finished_at); }
      if (data.started_at) { setText('ai-started-at', data.started_at); }
      if (data.error_code) {
        showAiError(data.error_code, data.error_message);
      } else if (status !== 'FAILED') {
        hideAiError();
      }

      var never = el('ai-never-run');
      if (never) { never.hidden = true; }
      var refresh = el('btn-ai-refresh');
      if (refresh) { refresh.hidden = false; }
    }

    function setRunIdle() {
      var btn = el('btn-ai-run');
      if (!btn) { return; }
      btn.disabled = false;
      btn.classList.remove('is-loading');
      btn.textContent = '运行 AI 测算';
    }

    function setRunRunning() {
      var btn = el('btn-ai-run');
      if (!btn) { return; }
      btn.disabled = true;
      btn.classList.remove('is-loading');
      btn.textContent = '运行中…';
    }

    function stopPolling() {
      if (pollTimer) {
        window.clearInterval(pollTimer);
        pollTimer = null;
      }
      polling = false;
    }

    function startPolling(resetTimer) {
      if (!currentAiId) { return; }
      stopPolling();
      if (resetTimer !== false) {
        pollStart = Date.now();
        pollCount = 0;
        pollFailures = 0;
      }
      showProgress();
      hideTimeout();
      updateProgressText();
      setRunRunning();
      var refresh = el('btn-ai-refresh');
      if (refresh) { refresh.hidden = false; }
      pollTimer = window.setInterval(pollOnce, POLL_INTERVAL_MS);
      pollOnce();
    }

    async function pollOnce() {
      if (polling || !currentAiId) { return; }
      polling = true;
      try {
        var data = await apiFetch(aiUrl(''));
        pollFailures = 0;
        pollCount += 1;
        updateProgressText();
        if (data && data.ai_task) { data = data.ai_task; }
        updateAiStatus(data);
        var status = str(data && data.status) || currentAiStatus;

        if (status === 'SUCCEEDED') {
          stopPolling();
          hideProgress();
          hideTimeout();
          setRunIdle();
          var retry = el('btn-ai-retry');
          if (retry) { retry.hidden = true; }
          await loadToolCalls(true);
          await loadResult(true);
          toast('AI 测算完成，结果与执行记录已更新', 'success');
        } else if (status === 'FAILED') {
          stopPolling();
          hideProgress();
          hideTimeout();
          setRunIdle();
          showAiError(data && data.error_code, data && data.error_message);
          var retryBtn = el('btn-ai-retry');
          if (retryBtn && canEdit) { retryBtn.hidden = false; }
          await loadToolCalls(true);
          toast('AI 测算失败：' + str((data && data.error_code) || '未知错误'), 'error', 7000);
        } else {
          await loadToolCalls(true);
          if (Date.now() - pollStart > timeoutMs) {
            stopPolling();
            hideProgress();
            showTimeout();
            toast('轮询超时：已停止自动刷新，可点击“刷新状态”继续查询', 'warn', 6000);
          }
        }
      } catch (e) {
        pollFailures += 1;
        if (pollFailures === 1 || pollFailures % 5 === 0) {
          toast('AI 状态查询失败：' + e.message, 'error');
        }
        if (Date.now() - pollStart > timeoutMs) {
          stopPolling();
          hideProgress();
          showTimeout();
        }
      } finally {
        polling = false;
      }
    }

    async function refreshAiOnce() {
      if (!currentAiId) {
        toast('尚无 AI 运行记录，请先运行 AI 测算', 'info');
        return;
      }
      var btn = el('btn-ai-refresh');
      if (btn) { setBusy(btn, true, '查询中…'); }
      try {
        var data = await apiFetch(aiUrl(''));
        if (data && data.ai_task) { data = data.ai_task; }
        updateAiStatus(data);
        var status = str(data && data.status);
        if (status === 'SUCCEEDED') {
          stopPolling();
          hideProgress();
          hideTimeout();
          setRunIdle();
          await loadToolCalls(true);
          await loadResult(true);
          toast('状态：已完成，结果已刷新', 'success');
        } else if (status === 'FAILED') {
          stopPolling();
          hideProgress();
          hideTimeout();
          setRunIdle();
          var retryBtn = el('btn-ai-retry');
          if (retryBtn && canEdit) { retryBtn.hidden = false; }
          await loadToolCalls(true);
          toast('状态：失败（' + str(data.error_code || '未知错误') + '）', 'error');
        } else {
          toast('状态：' + (AI_STATUS_TEXT[status] || status || '未知') + '，继续轮询', 'info');
          startPolling(true);
        }
      } catch (e) {
        toast('状态查询失败：' + e.message, 'error');
      } finally {
        if (btn) { setBusy(btn, false); }
      }
    }

    function resetForNewRun() {
      hideAiError();
      hideTimeout();
      var retry = el('btn-ai-retry');
      if (retry) { retry.hidden = true; }
      setText('ai-task-id-label', currentAiId);
      setText('ai-finished-at', '—');
      var never = el('ai-never-run');
      if (never) { never.hidden = true; }
      var refresh = el('btn-ai-refresh');
      if (refresh) { refresh.hidden = false; }
      var resultRefresh = el('btn-result-refresh');
      if (resultRefresh) { resultRefresh.hidden = false; }
      var adopt = el('btn-adopt-ai');
      if (adopt) { adopt.hidden = true; }

      var empty = el('result-empty');
      if (empty) { empty.hidden = false; }
      var body = el('result-body');
      if (body) { body.hidden = true; }
      lastResultAiTaskId = '';
      var aiTaskInput = el('version-ai-task-id');
      if (aiTaskInput) { aiTaskInput.value = ''; }
      if (resultSectionNode) { resultSectionNode.setAttribute('data-result-ai-id', ''); }
      var md = el('result-content-md');
      if (md) { md.textContent = ''; }
      var basisStatus = el('result-basis-status');
      if (basisStatus) {
        basisStatus.textContent = '依据未校验';
        basisStatus.className = 'badge badge-gray';
      }
      renderToolCalls([]);
      updateCounters({ rounds: 0, tool_calls: 0, distinct_types: 0, model_calls: 0 });
      var badge = el('ai-status-badge');
      if (badge) {
        badge.textContent = '待执行';
        badge.className = 'badge badge-lg badge-gray';
      }
      var mode = el('ai-model-mode');
      if (mode) { mode.textContent = '—'; mode.className = 'mode-tag mode-unknown'; }
      setText('ai-model-name', '');
    }

    async function runAi() {
      if (!taskId) { toast('缺少任务 ID，无法运行 AI', 'error'); return; }
      var btn = el('btn-ai-run');
      if (btn && btn.disabled) { return; }
      var faultSelect = el('ai-fault');
      var fault = faultSelect ? faultSelect.value : '';
      var body = {};
      if (fault) { body.fault = fault; }
      if (btn) { btn.disabled = true; btn.classList.add('is-loading'); btn.textContent = '提交中…'; }
      try {
        var data = await apiFetch('/api/business-tasks/' + encodeURIComponent(taskId) + '/ai-tasks', {
          method: 'POST',
          body: body
        });
        var newId = data && (data.ai_task_id || data.id);
        if (!newId) { throw new Error('接口未返回 AI 任务 ID'); }
        currentAiId = str(newId);
        currentAiStatus = 'PENDING';
        resetForNewRun();
        setRunRunning();
        startPolling(true);
        toast('AI 测算已启动，正在每 2 秒轮询状态…', 'info', 3500);
      } catch (e) {
        if (btn) { btn.disabled = false; btn.classList.remove('is-loading'); btn.textContent = '运行 AI 测算'; }
        toast('启动失败：' + e.message, 'error', 7000);
      }
    }

    async function retryAi() {
      if (!currentAiId) { toast('没有可重试的 AI 任务', 'warn'); return; }
      var btn = el('btn-ai-retry');
      if (btn) { setBusy(btn, true, '重试中…'); }
      try {
        var data = await apiFetch(aiUrl('/retry'), { method: 'POST' });
        var newId = data && (data.ai_task_id || data.id);
        if (!newId) { throw new Error('接口未返回新的 AI 任务 ID'); }
        currentAiId = str(newId);
        currentAiStatus = 'PENDING';
        resetForNewRun();
        setRunRunning();
        startPolling(true);
        toast('已发起重试，正在轮询新的 AI 任务…', 'info');
      } catch (e) {
        toast('重试失败：' + e.message, 'error', 7000);
      } finally {
        if (btn) { setBusy(btn, false); }
      }
    }

    /* ---- 结果渲染 ---- */

    function parseMaybeJson(value, fallback) {
      if (typeof value !== 'string') { return value === undefined || value === null ? fallback : value; }
      try { return JSON.parse(value); } catch (e) { return fallback; }
    }

    function toggleEmpty(id, hasData) {
      var node = el(id);
      if (node) { node.hidden = !!hasData; }
    }

    function renderBasisRefs(refs) {
      var ul = el('result-basis-refs');
      if (!ul) { return; }
      ul.textContent = '';
      (refs || []).forEach(function (ref) {
        var meta = BASIS_TYPE_TEXT[ref.type] || { label: ref.type || '依据', cls: 'badge-gray' };
        var li = document.createElement('li');
        var badge = document.createElement('span');
        badge.className = 'badge ' + meta.cls;
        badge.textContent = meta.label;
        li.appendChild(badge);

        if (ref.type === 'material' || ref.type === 'rule') {
          var btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'link-btn mono';
          btn.textContent = ref.id || '—';
          btn.title = '点击查看详情';
          btn.setAttribute('data-basis-type', ref.type);
          btn.setAttribute('data-basis-id', ref.id || '');
          if (ref.target_id !== undefined && ref.target_id !== null) {
            btn.setAttribute('data-basis-target', ref.target_id);
          }
          btn.setAttribute('data-basis-label', ref.label || ref.id || '');
          li.appendChild(btn);
        } else {
          var code = document.createElement('span');
          code.className = 'mono';
          code.textContent = ref.id || '—';
          li.appendChild(code);
        }
        if (ref.label) {
          var label = document.createElement('span');
          label.className = 'basis-label';
          label.textContent = ref.label;
          li.appendChild(label);
        }
        ul.appendChild(li);
      });
      toggleEmpty('result-basis-empty', (refs || []).length > 0);
    }

    function renderLineItems(items) {
      var tbody = el('result-line-items');
      if (!tbody) { return; }
      tbody.textContent = '';
      (items || []).forEach(function (item) {
        var tr = document.createElement('tr');
        var name = document.createElement('td');
        name.textContent = item.name || '—';
        var formula = document.createElement('td');
        formula.className = 'formula';
        formula.textContent = item.formula || '—';
        var amount = document.createElement('td');
        amount.className = 'text-right mono';
        amount.textContent = formatMoney(item.amount);
        tr.appendChild(name);
        tr.appendChild(formula);
        tr.appendChild(amount);
        tbody.appendChild(tr);
      });
      toggleEmpty('result-line-items-empty', (items || []).length > 0);
    }

    function renderList(id, items) {
      var list = el(id);
      if (!list) { return; }
      list.textContent = '';
      (items || []).forEach(function (item) {
        var li = document.createElement('li');
        li.textContent = typeof item === 'string' ? item : prettyJson(item);
        list.appendChild(li);
      });
    }

    function renderResult(rawData) {
      if (!rawData) { return; }
      var r = rawData.result || rawData;
      lastResultAiTaskId = r.ai_task_id ? str(r.ai_task_id) : (currentAiId || '');
      var structured = parseMaybeJson(r.structured, {}) || {};
      var lineItems = Array.isArray(structured.line_items) ? structured.line_items : [];
      var basisRefs = parseMaybeJson(r.basis_refs, []) || [];
      if (!Array.isArray(basisRefs)) { basisRefs = []; }
      var uncertain = Array.isArray(r.uncertain_items) ? r.uncertain_items : [];
      var structuredUncertain = Array.isArray(structured.uncertain) ? structured.uncertain : [];
      if (!uncertain.length) { uncertain = structuredUncertain; }
      var missing = Array.isArray(r.missing_data) ? r.missing_data : [];
      var structuredMissing = Array.isArray(structured.missing) ? structured.missing : [];
      if (!missing.length) { missing = structuredMissing; }

      var md = el('result-content-md');
      if (md) { md.textContent = str(r.content_md); }
      setText('result-total', formatMoney(structured.total));
      setText('result-per-copy', formatMoney(structured.per_copy));
      renderLineItems(lineItems);
      renderBasisRefs(basisRefs);
      renderList('result-uncertain-list', uncertain);
      renderList('result-missing-list', missing);
      var uncertainWrap = el('result-uncertain-wrap');
      if (uncertainWrap) { uncertainWrap.hidden = !uncertain.length; }
      var missingWrap = el('result-missing-wrap');
      if (missingWrap) { missingWrap.hidden = !missing.length; }

      var basisStatus = el('result-basis-status');
      if (basisStatus) {
        var bok = r.basis_ok;
        var ok = bok === true || bok === 1 || bok === '1' || bok === 'true' || bok === 'True';
        var unknown = bok === null || bok === undefined || bok === '';
        if (unknown) {
          basisStatus.textContent = '依据未校验';
          basisStatus.className = 'badge badge-gray';
        } else if (ok) {
          basisStatus.textContent = '依据校验通过';
          basisStatus.className = 'badge badge-green';
        } else {
          basisStatus.textContent = '依据存在不确定';
          basisStatus.className = 'badge badge-amber';
        }
      }

      var empty = el('result-empty');
      if (empty) { empty.hidden = true; }
      var body = el('result-body');
      if (body) { body.hidden = false; }
      var section = el('section-result');
      if (section) {
        section.setAttribute('data-loaded', '1');
        section.setAttribute('data-result-ai-id', lastResultAiTaskId || currentAiId || '');
      }
      var adopt = el('btn-adopt-ai');
      if (adopt && canEdit) { adopt.hidden = false; }
      var refresh = el('btn-result-refresh');
      if (refresh) { refresh.hidden = false; }
    }

    async function loadResult(silent) {
      if (!currentAiId) {
        if (!silent) { toast('尚无 AI 运行记录', 'info'); }
        return false;
      }
      try {
        var data = await apiFetch(aiUrl('/result'));
        if (!data) {
          if (!silent) { toast('该 AI 任务暂无结果', 'info'); }
          return false;
        }
        renderResult(data);
        return true;
      } catch (e) {
        if (!silent) { toast('结果加载失败：' + e.message, 'error'); }
        return false;
      }
    }

    function renderToolCalls(records) {
      var tbody = el('tool-calls-body');
      if (!tbody) { return; }
      tbody.textContent = '';
      (records || []).forEach(function (record, index) {
        var tr = document.createElement('tr');
        var seq = document.createElement('td');
        seq.className = 'mono';
        seq.textContent = str(record.seq || index + 1);

        var toolName = document.createElement('td');
        toolName.className = 'mono nowrap';
        toolName.textContent = record.tool_name || '—';

        var toolType = document.createElement('td');
        toolType.className = 'nowrap';
        toolType.textContent = TOOL_TYPE_TEXT[record.tool_type] || record.tool_type || '—';

        var args = document.createElement('td');
        args.className = 'summary-cell';
        var argsSpan = document.createElement('span');
        argsSpan.className = 'truncate';
        var argsValue = (record.args !== undefined && record.args !== null) ? record.args : record.args_json;
        argsSpan.textContent = summarizeJson(argsValue, 120);
        argsSpan.title = summarizeJson(argsValue, 500);
        args.appendChild(argsSpan);

        var resultSummary = document.createElement('td');
        resultSummary.className = 'summary-cell';
        var resultSpan = document.createElement('span');
        resultSpan.className = 'truncate';
        resultSpan.textContent = truncateText(record.result_summary || '—', 60);
        resultSpan.title = str(record.result_summary);
        resultSummary.appendChild(resultSpan);

        var sources = document.createElement('td');
        sources.className = 'summary-cell';
        var sourceValue = (Array.isArray(record.source_ids) && record.source_ids.length) ? record.source_ids : record.source_ids_json;
        if (typeof sourceValue === 'string') {
          try {
            var parsedSource = JSON.parse(sourceValue);
            if (parsedSource !== null && parsedSource !== undefined) { sourceValue = parsedSource; }
          } catch (e) { /* 保留原始字符串 */ }
        }
        var sourceText = '';
        if (Array.isArray(sourceValue)) { sourceText = sourceValue.join(', '); }
        else if (sourceValue && typeof sourceValue === 'object') { sourceText = JSON.stringify(sourceValue); }
        else { sourceText = str(sourceValue); }
        if (sourceText) {
          var srcSpan = document.createElement('span');
          srcSpan.className = 'mono';
          srcSpan.textContent = sourceText;
          srcSpan.title = sourceText;
          sources.appendChild(srcSpan);
        } else {
          var emptySrc = document.createElement('span');
          emptySrc.className = 'muted';
          emptySrc.textContent = '—';
          sources.appendChild(emptySrc);
        }

        var status = document.createElement('td');
        var statusKey = str(record.status);
        var statusCls = ['SUCCESS', 'OK', '完成'].indexOf(statusKey) !== -1 ? 'badge-green'
          : (['FAILED', 'ERROR', 'TIMEOUT'].indexOf(statusKey) !== -1 ? 'badge-red' : 'badge-gray');
        var statusBadge = document.createElement('span');
        statusBadge.className = 'badge ' + statusCls;
        statusBadge.textContent = TOOL_STATUS_TEXT[statusKey] || statusKey || '—';
        status.appendChild(statusBadge);

        var errorCode = document.createElement('td');
        errorCode.className = 'mono';
        errorCode.textContent = record.error_code || '—';

        var latency = document.createElement('td');
        latency.className = 'text-right mono';
        latency.textContent = (record.latency_ms === null || record.latency_ms === undefined) ? '—' : (str(record.latency_ms) + ' ms');

        tr.appendChild(seq);
        tr.appendChild(toolName);
        tr.appendChild(toolType);
        tr.appendChild(args);
        tr.appendChild(resultSummary);
        tr.appendChild(sources);
        tr.appendChild(status);
        tr.appendChild(errorCode);
        tr.appendChild(latency);
        tbody.appendChild(tr);
      });

      setText('tool-calls-count', (records || []).length);
      var countNode = el('tool-calls-count');
      if (countNode) { countNode.textContent = str((records || []).length); }
      toggleEmpty('tool-calls-empty', (records || []).length > 0);
    }

    async function loadToolCalls(silent) {
      if (!currentAiId) { return; }
      try {
        var data = await apiFetch(aiUrl('/tool-calls'));
        var records = asArray(data, ['records', 'items', 'tool_calls']);
        renderToolCalls(records);
      } catch (e) {
        if (!silent) { toast('执行记录加载失败：' + e.message, 'error'); }
      }
    }

    /* ---- 参数编辑 ---- */

    function openParamsEditor() {
      var panel = el('params-edit-form');
      if (panel) {
        panel.hidden = false;
        panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
        var title = el('edit-title');
        if (title) { title.focus(); }
      }
    }

    function closeParamsEditor() {
      var panel = el('params-edit-form');
      if (panel) { panel.hidden = true; }
    }

    function readNumber(id) {
      var node = el(id);
      if (!node) { return undefined; }
      var value = str(node.value).trim();
      if (!value) { return undefined; }
      var n = Number(value);
      return isFinite(n) ? n : value;
    }

    function initParamsEditor() {
      var toggle = el('btn-params-edit');
      var form = el('params-edit-form');
      var cancel = el('btn-params-cancel');
      if (toggle) { toggle.addEventListener('click', openParamsEditor); }
      if (cancel) { cancel.addEventListener('click', closeParamsEditor); }
      if (!form) { return; }

      form.addEventListener('submit', async function (event) {
        event.preventDefault();
        if (!form.reportValidity()) { return; }
        var title = el('edit-title');
        var goal = el('edit-goal');
        if (title && !str(title.value).trim()) {
          setFieldError(title, '任务标题不能为空');
          title.focus();
          return;
        }
        if (goal && !str(goal.value).trim()) {
          setFieldError(goal, '测算目标不能为空');
          goal.focus();
          return;
        }
        var params = {
          format: el('edit-format') ? el('edit-format').value : undefined,
          print_sheet: readNumber('edit-print_sheet'),
          qty: readNumber('edit-qty'),
          paper_type: el('edit-paper_type') ? el('edit-paper_type').value.trim() : undefined,
          binding: el('edit-binding') ? el('edit-binding').value : undefined,
          color_pages: readNumber('edit-color_pages'),
          process: el('edit-process') ? el('edit-process').value : undefined,
          note: el('edit-note') ? el('edit-note').value.trim() : undefined
        };
        var body = {
          expected_revision: revision,
          title: title ? title.value.trim() : undefined,
          goal: goal ? goal.value.trim() : undefined,
          params: params
        };
        var btn = el('btn-params-save');
        if (btn) { setBusy(btn, true, '保存中…'); }
        try {
          await apiFetch('/api/business-tasks/' + encodeURIComponent(taskId), { method: 'PUT', body: body });
          flash('任务参数已保存', 'success');
          window.setTimeout(function () { location.reload(); }, 350);
        } catch (e) {
          toast('保存失败：' + e.message, 'error', 7000);
          if (btn) { setBusy(btn, false); }
        }
      });
    }

    /* ---- 关联资料 ---- */

    async function loadMaterialOptions() {
      var select = el('add-material-id');
      if (!select) { return; }
      try {
        var data = await apiFetch('/api/materials');
        var items = asArray(data, ['items', 'materials', 'list']);
        select.textContent = '';
        if (!items.length) {
          var emptyOpt = document.createElement('option');
          emptyOpt.value = '';
          emptyOpt.textContent = '资料库暂无数据，请先新增资料';
          select.appendChild(emptyOpt);
          select.disabled = true;
          return;
        }
        var placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.textContent = '请选择资料…';
        select.appendChild(placeholder);
        items.forEach(function (item) {
          var id = item.id !== undefined && item.id !== null ? item.id : item.material_id;
          if (id === undefined || id === null) { return; }
          var option = document.createElement('option');
          option.value = str(id);
          var parts = [item.material_no || ('ID ' + id), item.title || ''];
          if (item.source) { parts.push('· ' + item.source); }
          option.textContent = parts.join(' ').trim();
          select.appendChild(option);
        });
      } catch (e) {
        select.textContent = '';
        var failOpt = document.createElement('option');
        failOpt.value = '';
        failOpt.textContent = '资料加载失败，请刷新页面';
        select.appendChild(failOpt);
        select.disabled = true;
      }
    }

    function initAddMaterial() {
      var form = el('add-material-form');
      if (!form) { return; }
      form.addEventListener('submit', async function (event) {
        event.preventDefault();
        var select = el('add-material-id');
        var materialId = select ? select.value : '';
        if (!materialId) { toast('请选择要关联的资料', 'warn'); if (select) { select.focus(); } return; }
        var roleSelect = el('add-material-role');
        var btn = form.querySelector('button[type="submit"]');
        if (btn) { setBusy(btn, true, '添加中…'); }
        try {
          await apiFetch('/api/business-tasks/' + encodeURIComponent(taskId) + '/materials', {
            method: 'POST',
            body: { material_id: Number(materialId), link_role: roleSelect ? roleSelect.value : 'reference' }
          });
          flash('资料已关联到任务', 'success');
          window.setTimeout(function () { location.reload(); }, 350);
        } catch (e) {
          toast('添加失败：' + e.message, 'error', 7000);
          if (btn) { setBusy(btn, false); }
        }
      });
    }

    /* ---- 版本与提交 ---- */

    function initVersionActions() {
      var saveForm = el('version-save-form');
      if (saveForm) {
        saveForm.addEventListener('submit', async function (event) {
          event.preventDefault();
          if (!saveForm.reportValidity()) { return; }
          var content = el('version-content');
          if (!content || !str(content.value).trim()) {
            if (content) { content.focus(); }
            toast('版本内容不能为空', 'warn');
            return;
          }
          var note = el('version-note');
          var aiTaskInput = el('version-ai-task-id');
          var body = {
            content_md: content.value,
            change_note: note ? str(note.value).trim() : ''
          };
          if (aiTaskInput && str(aiTaskInput.value)) {
            body.ai_task_id = Number(aiTaskInput.value);
          }
          var btn = el('btn-save-version');
          if (btn) { setBusy(btn, true, '保存中…'); }
          try {
            await apiFetch('/api/business-tasks/' + encodeURIComponent(taskId) + '/results', {
              method: 'POST',
              body: body
            });
            flash('人工版本已保存', 'success');
            window.setTimeout(function () { location.reload(); }, 350);
          } catch (e) {
            toast('保存失败：' + e.message, 'error', 7000);
            if (btn) { setBusy(btn, false); }
          }
        });
      }

      var adopt = el('btn-adopt-ai');
      if (adopt) {
        adopt.addEventListener('click', async function () {
          var content = el('version-content');
          if (!content) { return; }
          var md = el('result-content-md');
          if ((!md || !str(md.textContent).trim()) && currentAiId) {
            var loaded = await loadResult(true);
            md = el('result-content-md');
            if (!loaded || !md) { toast('暂无可用结果，请先运行 AI 测算', 'warn'); return; }
          }
          content.value = md ? str(md.textContent) : '';
          var note = el('version-note');
          if (note && !str(note.value).trim()) { note.value = '采用 AI 测算结果'; }
          var aiTaskInput = el('version-ai-task-id');
          if (aiTaskInput) { aiTaskInput.value = lastResultAiTaskId || currentAiId || ''; }
          content.focus();
          content.scrollIntoView({ behavior: 'smooth', block: 'center' });
          toast('已填入 AI 结果，可修改后保存为新版本', 'info');
        });
      }

      var submitForm = el('submit-review-form');
      if (submitForm) {
        submitForm.addEventListener('submit', async function (event) {
          event.preventDefault();
          var versionSelect = el('submit-version-no');
          if (!versionSelect || !versionSelect.value) { toast('请选择要提交的版本', 'warn'); return; }
          var comment = el('submit-comment');
          var btn = el('btn-submit-review');
          if (btn) { setBusy(btn, true, '提交中…'); }
          try {
            await apiFetch('/api/business-tasks/' + encodeURIComponent(taskId) + '/submit', {
              method: 'POST',
              body: {
                version_no: Number(versionSelect.value),
                comment: comment ? str(comment.value).trim() : ''
              }
            });
            flash('已提交审核', 'success');
            window.setTimeout(function () { location.reload(); }, 350);
          } catch (e) {
            toast('提交失败：' + e.message, 'error', 7000);
            if (btn) { setBusy(btn, false); }
          }
        });
      }
    }

    function initReviewActions() {
      var reviewForm = el('review-form');
      if (reviewForm) {
        reviewForm.addEventListener('submit', function (event) { event.preventDefault(); });
      }
      var buttons = qsa('[data-review-action]');
      if (!buttons.length) { return; }
      buttons.forEach(function (btn) {
        btn.addEventListener('click', async function () {
          var action = btn.getAttribute('data-review-action');
          var opinionNode = el('review-opinion');
          var opinion = opinionNode ? str(opinionNode.value).trim() : '';
          if (action === 'REJECT' && !opinion) {
            toast('退回时必须填写审核意见', 'warn');
            if (opinionNode) { opinionNode.focus(); }
            return;
          }
          var versionSelect = el('review-version-no');
          if (!versionSelect || !versionSelect.value) { toast('请选择审核版本', 'warn'); return; }
          var confirmed = window.confirm(action === 'APPROVE' ? '确认通过该版本并完成任务？' : '确认退回该版本？');
          if (!confirmed) { return; }
          buttons.forEach(function (b) { setBusy(b, true); });
          if (btn) { btn.textContent = action === 'APPROVE' ? '通过中…' : '退回中…'; }
          try {
            await apiFetch('/api/business-tasks/' + encodeURIComponent(taskId) + '/review', {
              method: 'POST',
              body: {
                version_no: Number(versionSelect.value),
                action: action,
                opinion: opinion
              }
            });
            flash(action === 'APPROVE' ? '审核已通过' : '已退回任务', 'success');
            window.setTimeout(function () { location.reload(); }, 350);
          } catch (e) {
            toast('审核失败：' + e.message, 'error', 7000);
            buttons.forEach(function (b) { setBusy(b, false); });
            if (btn) { btn.textContent = action === 'APPROVE' ? '通过' : '退回'; }
          }
        });
      });
    }

    /* ---- 绑定 ---- */

    var runBtn = el('btn-ai-run');
    if (runBtn) { runBtn.addEventListener('click', runAi); }
    var retryBtn = el('btn-ai-retry');
    if (retryBtn) { retryBtn.addEventListener('click', retryAi); }
    var refreshBtn = el('btn-ai-refresh');
    if (refreshBtn) { refreshBtn.addEventListener('click', refreshAiOnce); }
    var resultRefresh = el('btn-result-refresh');
    if (resultRefresh) {
      resultRefresh.addEventListener('click', async function () {
        if (!currentAiId) { toast('尚无 AI 运行记录', 'info'); return; }
        setBusy(resultRefresh, true, '刷新中…');
        await loadResult(false);
        await loadToolCalls(true);
        setBusy(resultRefresh, false);
      });
    }

    initParamsEditor();
    initAddMaterial();
    initVersionActions();
    initReviewActions();
    loadMaterialOptions();

    // 页面加载时补齐服务端未传入的数据，或恢复运行中任务的轮询
    if (currentAiId) {
      var resultSection = el('section-result');
      var resultLoaded = resultSection && resultSection.getAttribute('data-loaded') === '1';
      var tbody = el('tool-calls-body');
      if (!resultLoaded) {
        loadResult(true);
      }
      if (tbody && !tbody.children.length) {
        loadToolCalls(true);
      }
      if (currentAiStatus === 'PENDING' || currentAiStatus === 'RUNNING') {
        startPolling(true);
      }
    }

    window.addEventListener('beforeunload', stopPolling);
    window.addEventListener('pagehide', stopPolling);
  }

  /* ---------------- 资料启停（T06 演示：停用被引用资料 → 提交审核依据失效） ---------------- */

  function initRuleToggle() {
    var buttons = document.querySelectorAll('[data-toggle-rule]');
    Array.prototype.forEach.call(buttons, function (btn) {
      btn.addEventListener('click', async function () {
        var id = btn.getAttribute('data-toggle-rule');
        var enabled = btn.getAttribute('data-rule-enabled') === '1';
        var next = !enabled;
        if (!next && !window.confirm('停用后该规则不再参与后续 AI 校验（历史结果与执行记录仍保留规则 ID）。确认停用？')) {
          return;
        }
        btn.disabled = true;
        try {
          await apiFetch('/api/rules/' + encodeURIComponent(id), { method: 'PATCH', body: { enabled: next } });
          toast('规则已' + (next ? '启用' : '停用'), 'success');
          window.setTimeout(function () { location.reload(); }, 600);
        } catch (e) {
          toast(e.message || '操作失败', 'error');
          btn.disabled = false;
        }
      });
    });
  }

  function initMaterialToggle() {
    var buttons = document.querySelectorAll('[data-toggle-material]');
    Array.prototype.forEach.call(buttons, function (btn) {
      btn.addEventListener('click', async function () {
        var id = btn.getAttribute('data-toggle-material');
        var enabled = btn.getAttribute('data-material-enabled') === '1';
        var next = !enabled;
        if (!next && !window.confirm('停用后，引用该资料的 AI 结果在提交审核时会被判定"依据失效"（409 BASIS_MISMATCH）。确认停用？')) {
          return;
        }
        btn.disabled = true;
        try {
          await apiFetch('/api/materials/' + encodeURIComponent(id), { method: 'PATCH', body: { enabled: next } });
          toast('资料已' + (next ? '启用' : '停用'), 'success');
          window.setTimeout(function () { location.reload(); }, 600);
        } catch (e) {
          toast(e.message || '操作失败', 'error');
          btn.disabled = false;
        }
      });
    });
  }

  /* ---------------- 启动 ---------------- */

  function initGlobal() {
    consumeFlash();
    initDemoAccounts();
    initAutoSubmit();
    initTableFilter();
    initJsonForms();
    initJsonExamples();
    initTaskCreateForm();
    initLoginForm();
    initMaterialToggle();
    initRuleToggle();

    document.addEventListener('click', function (event) {
      var origin = event.target;
      if (!origin || typeof origin.closest !== 'function') { return; }
      var target = origin.closest('[data-basis-type]');
      if (!target) { return; }
      var type = target.getAttribute('data-basis-type');
      var id = target.getAttribute('data-basis-id');
      var label = target.getAttribute('data-basis-label');
      var targetId = target.getAttribute('data-basis-target');
      openBasisDetail(type, id, label, targetId);
    });

    var pageRefresh = el('btn-page-refresh');
    if (pageRefresh) {
      pageRefresh.addEventListener('click', function () { location.reload(); });
    }

    initTaskDetail();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initGlobal);
  } else {
    initGlobal();
  }
})();
