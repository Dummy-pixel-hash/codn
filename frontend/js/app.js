// codn Dashboard — hardened frontend logic (v1.1.0)
// Frontend-only. Never sends local settings/secrets to the server.

(function () {
  'use strict';

  // ── Constants ──────────────────────────────────────────
  var SETTINGS_KEY = 'codn-settings-v1';
  var QUOTES_PER_PAGE = 9;
  var HISTORY_PER_PAGE = 9;
  var FETCH_TIMEOUT_MS = 15000;
  var GENERATE_TIMEOUT_MS = 15 * 60 * 1000; // GPU pipeline can take minutes
  var POLL_INTERVAL_MS = 3000;
  var _authWarned = {}; // rate-limit auth toasts; declared early (used by fetch wrapper)

  var DEFAULT_SETTINGS = {
    // NOTE: 'api-token' is intentionally absent — session-only, never persisted.
    'llama-port': '',
    'comfy-port': '',
    'model-path': '',
    'tunnel-engine': 'ngrok',
    'ngrok-token': '',
    'ig-access-token': '',
    'ig-page-id': '',
    'ig-user-id': ''
  };

  // ── State ──────────────────────────────────────────────
  var state = {
    currentSection: 'dashboard',
    generation: { inProgress: false, abort: null, startedAt: 0, lastResult: null },
    quotes: { all: [], page: 1 },
    history: { all: [], page: 1 }
  };

  // ── Helpers ────────────────────────────────────────────
  function $(id) { return document.getElementById(id); }

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  // Media URLs carry ?v=<updated_at digits> so in-place edits (same
  // filename, new pixels) never show stale cached images.
  function mediaUrl(file, ts) {
    var u = '/media/' + encodeURIComponent(file);
    if (ts) {
      var digits = String(ts).replace(/[^0-9]/g, '');
      if (digits) u += '?v=' + digits;
    }
    return u;
  }

  // Composed empty state: title + hint + one real action (never a dead link).
  function emptyStateWithAction(title, hint, actionLabel, onAction) {
    var box = el('div', 'empty-state-block');
    box.appendChild(el('p', 'empty-title', title));
    box.appendChild(el('p', 'empty-hint', hint));
    var btn = el('button', 'btn btn-sm btn-primary', actionLabel);
    btn.type = 'button';
    btn.addEventListener('click', onAction);
    box.appendChild(btn);
    return box;
  }

  function fetchWithTimeout(url, opts, timeoutMs) {
    opts = opts || {};
    var ms = timeoutMs || FETCH_TIMEOUT_MS;
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, ms);
    var userSignal = opts.signal;
    if (userSignal) {
      if (userSignal.aborted) ctrl.abort();
      else userSignal.addEventListener('abort', function () { ctrl.abort(); }, { once: true });
    }
    opts.signal = ctrl.signal;
    // Attach session API token (Settings → Access). Never hardcoded.
    try {
      opts.headers = Object.assign({}, opts.headers || {});
      var tok = getApiToken();
      if (tok && !opts.headers.Authorization) {
        opts.headers.Authorization = 'Bearer ' + tok;
      }
    } catch (e) {}
    var quiet = !!opts.quiet;
    delete opts.quiet;
    return fetch(url, opts).then(function (resp) {
      if (!quiet && (resp.status === 401 || resp.status === 403) && !_authWarned[resp.status]) {
        _authWarned[resp.status] = true;
        toast('API token rejected (' + resp.status + ') — paste the server API_TOKEN into Settings → Access.', 'error');
        setTimeout(function () { _authWarned[resp.status] = false; }, 30000);
      }
      if (!quiet && resp.status === 503 && !_authWarned[503]) {
        try {
          resp.clone().json().then(function (d) {
            if (d && /API token not configured/i.test(d.detail || '')) {
              _authWarned[503] = true;
              toast('Server has no API_TOKEN — set it in .env and restart the app.', 'error');
              setTimeout(function () { _authWarned[503] = false; }, 30000);
            }
          }).catch(function () {});
        } catch (e) {}
      }
      return resp;
    }).finally(function () { clearTimeout(timer); });
  }

  // ── API token (session only — never localStorage/persisted) ──
  // Reads the live Settings input first so paste-then-generate works
  // without Save; falls back to the session value restored on load.
  function getApiToken() {
    try {
      var field = document.getElementById('api-token');
      if (field && field.value.trim()) return field.value.trim();
      return sessionStorage.getItem('codn-token') || '';
    } catch (e) { return ''; }
  }
  function setApiToken(t) {
    try {
      if (t) sessionStorage.setItem('codn-token', t);
      else sessionStorage.removeItem('codn-token');
    } catch (e) {}
  }

  function safeJson(resp) {
    return resp.text().then(function (t) {
      try { return t ? JSON.parse(t) : {}; }
      catch (e) { return { _raw: t }; }
    });
  }

  // ── Authed API ─────────────────────────────────────────
  // (Auth injection + 401/403/503 hints live in fetchWithTimeout;
  // token getters live above. apiErrorHint formats call-site errors.)
  function apiErrorHint(status, data) {
    var detail = (data && (data.detail || data.error)) || '';
    if (status === 503 && /API token not configured/i.test(detail)) {
      toast('Server has no API_TOKEN — generate one, set it in .env, and restart the app.', 'error');
    }
    return detail || ('Server error ' + status);
  }

  // ── Scroll reveals (IntersectionObserver, transform+opacity only) ──
  document.documentElement.classList.add('js');
  var revealIO = ('IntersectionObserver' in window) ? new IntersectionObserver(function (entries) {
    entries.forEach(function (en) {
      if (en.isIntersecting) {
        en.target.classList.add('in');
        revealIO.unobserve(en.target);
      }
    });
  }, { threshold: 0.08, rootMargin: '0px 0px -4% 0px' }) : null;

  function watchReveals(scope) {
    var nodes = (scope || document).querySelectorAll('.reveal:not(.in):not([data-watched])');
    if (!revealIO) {
      // No observer support: show everything immediately, never trap content.
      Array.prototype.forEach.call(nodes, function (n) { n.classList.add('in'); });
      return;
    }
    var nodes = (scope || document).querySelectorAll('.reveal:not(.in):not([data-watched])');
    Array.prototype.forEach.call(nodes, function (n, i) {
      n.setAttribute('data-watched', '1');
      n.style.setProperty('--rd', Math.min(i * 70, 350) + 'ms');
      revealIO.observe(n);
    });
  }

  // Cursor spotlight coordinates (CSS vars only — no layout work, skipped
  // entirely under reduced-motion).
  if (!window.matchMedia || !matchMedia('(prefers-reduced-motion: reduce)').matches) {
    document.addEventListener('mousemove', function (e) {
      var t = e.target && e.target.closest ? e.target.closest('.spot') : null;
      if (!t) return;
      var r = t.getBoundingClientRect();
      t.style.setProperty('--mx', (e.clientX - r.left) + 'px');
      t.style.setProperty('--my', (e.clientY - r.top) + 'px');
    }, { passive: true });
  }

  // ── Toasts ─────────────────────────────────────────────
  var toastBox = null;
  function toast(msg, kind) {
    if (!toastBox) toastBox = $('toasts');
    if (!toastBox) return;
    var t = el('div', 'toast toast-' + (kind || 'info'));
    t.textContent = msg;
    var close = el('button', 'toast-close', '×');
    close.type = 'button';
    close.setAttribute('aria-label', 'Dismiss');
    close.addEventListener('click', function () { t.remove(); });
    t.appendChild(close);
    toastBox.appendChild(t);
    setTimeout(function () {
      t.classList.add('toast-out');
      setTimeout(function () { t.remove(); }, 300);
    }, 5200);
    while (toastBox.children.length > 4) toastBox.firstChild.remove();
  }

  // ── Navigation ─────────────────────────────────────────
  var navLinks = Array.prototype.slice.call(document.querySelectorAll('.nav-link'));
  function switchSection(id, pushHash) {
    state.currentSection = id;
    navLinks.forEach(function (link) {
      var active = link.dataset.section === id;
      link.classList.toggle('active', active);
      if (active) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
    Array.prototype.forEach.call(document.querySelectorAll('.section'), function (s) {
      s.classList.toggle('active', s.id === id);
    });
    if (pushHash !== false) {
      try { history.replaceState(null, '', '#' + id); } catch (e) {}
    }
    if (id === 'dashboard') loadActivity();
    if (id === 'quotes') { if (!state.quotes.all.length) loadQuotes(); else renderQuotes(); }
    if (id === 'history') { if (!state.history.all.length) loadHistory(); else renderHistory(); }
  }
  navLinks.forEach(function (link) {
    link.addEventListener('click', function () { switchSection(link.dataset.section); });
  });

  // ── Status ─────────────────────────────────────────────
  function setIndicator(type, status) {
    var n = document.querySelector('[data-status="' + type + '"]');
    if (n) n.className = 'status-indicator ' + status;
  }

  function refreshPipelineStatus() {
    fetchWithTimeout('/config', {}, 8000).then(function (resp) {
      if (!resp.ok) throw new Error('config ' + resp.status);
      return resp.json();
    }).then(function (data) {
      var llamaOn = !!data.llama_running;
      var comfyOn = !!data.comfy_running;
      setIndicator('llama', llamaOn ? 'online' : 'error');
      setIndicator('comfy', comfyOn ? 'online' : 'idle');
      var ls = $('llama-state-text'); if (ls) ls.textContent = llamaOn ? 'running' : 'stopped';
      var cs = $('comfy-state-text'); if (cs) cs.textContent = comfyOn ? 'running' : 'idle';
      var badge = $('backend-badge'); if (badge) badge.textContent = '● local';
      var igLine = $('ig-account-line');
      if (igLine) igLine.textContent = 'instagram: ' + (data.instagram_account || 'not linked');
      var igSrv = $('ig-server-account');
      if (igSrv) igSrv.textContent = data.instagram_account || 'not linked (server .env)';
      var side = $('sidebar-status');
      if (side) side.textContent = data.instagram_account ? 'Connected' : 'Local only';
      var dot = $('sidebar-dot');
      if (dot) dot.className = 'status-dot ' + (data.instagram_account ? 'online' : 'idle');
      var counter = $('prompts-counter');
      if (counter && data.prompts_generated != null) counter.textContent = data.prompts_generated + ' prompts';
      resolveTunnel();
    }).catch(function (err) {
      if (err && err.name === 'AbortError') return;
      setIndicator('llama', 'error');
      setIndicator('comfy', 'error');
      setIndicator('tunnel', 'error');
      var badge = $('backend-badge'); if (badge) badge.textContent = '● offline';
    });
  }

  // Prefer backend proxy; never call the tunnel daemon port directly (CORS/prod break).
  function resolveTunnel() {
    var urlEl = $('tunnel-url');
    function setTunnel(text, ok) {
      if (urlEl) urlEl.textContent = text;
      setIndicator('tunnel', ok ? 'online' : 'idle');
    }
    fetchWithTimeout('/api/tunnel', {}, 5000).then(function (r) {
      if (!r.ok) throw new Error('no proxy');
      return r.json();
    }).then(function (d) {
      if (d && d.url) setTunnel(d.url + (d.engine ? ' · ' + d.engine : ''), true);
      else throw new Error('empty');
    }).catch(function () {
      fetchWithTimeout('/api/pipeline-status', {}, 5000).then(function (r) {
        if (!r.ok) throw new Error('no status api');
        return r.json();
      }).then(function (d) {
        if (d && (d.tunnel_url || d.public_url)) setTunnel(d.tunnel_url || d.public_url, true);
        else setTunnel('tunnel: check start-all.sh', false);
      }).catch(function () {
        setTunnel('tunnel: check start-all.sh', false);
      });
    });
  }

  // ── Generation ─────────────────────────────────────────
  var generateBtn = $('generate-btn');
  var generateBtnText = $('generate-btn-text');
  var progressTracker = $('progress-tracker');
  var phaseSteps = Array.prototype.slice.call(document.querySelectorAll('.phase-step'));

  function resetPhases() {
    phaseSteps.forEach(function (s) {
      s.className = 'phase-step';
      var st = s.querySelector('.phase-status');
      if (st) st.textContent = 'waiting';
    });
  }
  function setPhase(i, cls, text) {
    var s = phaseSteps[i];
    if (!s) return;
    s.className = 'phase-step ' + cls;
    var st = s.querySelector('.phase-status');
    if (st) st.textContent = text;
  }

  function startGeneration() {
    if (state.generation.inProgress) return;
    var theme = $('theme-input') ? $('theme-input').value.trim() : '';

    state.generation.inProgress = true;
    state.generation.abort = new AbortController();
    state.generation.startedAt = Date.now();
    hideResult();

    progressTracker.classList.remove('hidden');
    generateBtn.disabled = true;
    if (generateBtnText) generateBtnText.textContent = 'Generating… (may take minutes)';
    resetPhases();
    setPhase(0, 'active', 'request sent…');

    // Free-form vibe only — the model interprets it (and derives a tag).
    var body = { upload: false };
    if (theme) body.theme = theme.slice(0, 200);

    fetchWithTimeout('/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: state.generation.abort.signal
    }, FETCH_TIMEOUT_MS).then(function (resp) {
      return safeJson(resp).then(function (data) { return { ok: resp.ok, status: resp.status, data: data }; });
    }).then(function (res) {
      if (!res.ok) {
        var msg = apiErrorHint(res.status, res.data);
        setPhase(0, 'failed', String(msg).slice(0, 42));
        toast('Generation failed: ' + msg, 'error');
        finishGeneration(false);
        return;
      }
      var data = res.data || {};
      // Async backend: 202 + job_id → poll /jobs/{id} for live phases.
      if (data.job_id && !data.image && !data.image_url) {
        state.generation.jobId = data.job_id;
        setPhase(0, 'active', 'queued…');
        pollJob(data.job_id);
        return;
      }
      // Legacy blocking backend fallback (200 with image payload).
      handleGeneratePayload(data);
    }).catch(function (err) {
      if (err && err.name === 'AbortError') {
        // Local cancel path (cancelGeneration) already updated the UI.
        if (state.generation.inProgress) {
          setPhase(0, 'failed', 'cancelled');
          finishGeneration(false);
        }
        return;
      }
      setPhase(0, 'failed', 'network error');
      toast('Generation failed: ' + (err.message || 'network error'), 'error');
      finishGeneration(false);
    });
  }

  // Live phase rendering from GET /jobs/{id} payloads.
  var JOB_PHASES = ['art-prompt', 'art-gen', 'text-overlay', 'upload'];

  function renderJobPhase(job) {
    if (job.status === 'queued') {
      resetPhases();
      setPhase(0, 'active', 'queued' + (job.position != null ? ' #' + (job.position + 1) : '') + '…');
      return;
    }
    var idx = JOB_PHASES.indexOf(job.phase);
    if (idx === -1) return;
    for (var i = 0; i < JOB_PHASES.length; i++) {
      if (i < idx) setPhase(i, 'completed', '✓ Done');
      else if (i === idx) setPhase(i, 'active', 'running…');
    }
  }

  function handleGeneratePayload(data) {
    // Shared by the legacy blocking response and job completion.
    setPhase(0, 'completed', '✓ Done');
    setPhase(1, 'completed', '✓ Done');
    setPhase(2, 'completed', '✓ Done');
    var uploaded = !!data.upload_result;
    var status = data.status || (data.image_url || data.image ? 'generated' : 'failed');
    if (status === 'generated') {
      // Normal UI path: generated for review — upload is a manual next step.
      setPhase(3, 'completed', '✓ Ready to review');
      showResult(data);
      toast('Image ready — review below, then Upload, Edit or Download.', 'success');
      finishGeneration(true);
    } else if (status === 'success') {
      // API-triggered runs with upload:true still report through here.
      setPhase(3, uploaded ? 'completed' : 'failed', uploaded ? '✓ Done' : 'upload failed');
      showResult(data);
      toast(uploaded ? 'Generation complete.' : 'Image generated (upload failed).',
        uploaded ? 'success' : 'warn');
      finishGeneration(true);
    } else if (status === 'upload_failed') {
      setPhase(3, 'failed', 'upload failed');
      showResult(data);
      toast('Image generated but Instagram upload failed.', 'warn');
      finishGeneration(true);
    } else {
      setPhase(3, 'failed', String(status).slice(0, 32));
      toast('Generation returned: ' + status, 'error');
      finishGeneration(false);
    }
    var postedit = $('postedit-toggle');
    if (data.overlay && postedit && postedit.checked) {
      var img = data.image_url || data.image || '';
      if (img) openEditOverlay(data.overlay, img, data.source_filename);
    }
  }

  function pollJob(jobId) {
    var deadline = Date.now() + GENERATE_TIMEOUT_MS;
    var eta = $('progress-eta');
    function tick() {
      if (!state.generation.inProgress) return;
      fetchWithTimeout('/jobs/' + encodeURIComponent(jobId), { quiet: true }, FETCH_TIMEOUT_MS).then(function (resp) {
        return safeJson(resp).then(function (data) { return { ok: resp.ok, status: resp.status, data: data }; });
      }).then(function (res) {
        if (!state.generation.inProgress) return;
        if (!res.ok) {
          if (res.status === 404) {
            setPhase(0, 'failed', 'job lost');
            toast('Job not found — server may have restarted.', 'error');
            finishGeneration(false);
            return;
          }
          if (res.status === 401 || res.status === 403 || res.status === 503) {
            setPhase(0, 'failed', 'auth error');
            finishGeneration(false);
            return;
          }
          if (Date.now() > deadline) {
            setPhase(0, 'failed', 'poll timeout');
            toast('Lost contact with job: ' + apiErrorHint(res.status, res.data), 'error');
            finishGeneration(false);
            return;
          }
          state.generation.pollTimer = setTimeout(tick, POLL_INTERVAL_MS);
          return;
        }
        var job = res.data || {};
        state.generation.jobId = job.job_id || jobId;
        if (job.status === 'queued' || job.status === 'running') {
          renderJobPhase(job);
          if (eta) eta.textContent = '· job ' + (job.job_id || jobId) +
            (job.status === 'queued' && job.position != null ? ' · queue #' + (job.position + 1) : '') +
            ' · keep this tab open';
          if (Date.now() > deadline) {
            setPhase(0, 'failed', 'timed out');
            toast('Generation timed out waiting for the server.', 'error');
            finishGeneration(false);
            return;
          }
          state.generation.pollTimer = setTimeout(tick, POLL_INTERVAL_MS);
          return;
        }
        if (job.status === 'done') {
          handleGeneratePayload(job.result || {});
          return;
        }
        if (job.status === 'cancelled') {
          resetPhases();
          setPhase(0, 'failed', 'cancelled');
          toast('Generation cancelled on server.', 'warn');
          finishGeneration(false);
          return;
        }
        var errMsg = job.error || 'job failed';
        resetPhases();
        setPhase(0, 'failed', String(errMsg).slice(0, 42));
        toast('Generation failed: ' + errMsg, 'error');
        finishGeneration(false);
      }).catch(function (err) {
        if (!state.generation.inProgress) return;
        if (err && err.name === 'AbortError') return;
        if (Date.now() > deadline) {
          setPhase(0, 'failed', 'poll timeout');
          toast('Lost contact with job: ' + (err.message || 'network error'), 'error');
          finishGeneration(false);
          return;
        }
        state.generation.pollTimer = setTimeout(tick, POLL_INTERVAL_MS);
      });
    }
    tick();
  }

  function finishGeneration() {
    state.generation.inProgress = false;
    state.generation.abort = null;
    state.generation.jobId = null;
    if (state.generation.pollTimer) {
      clearTimeout(state.generation.pollTimer);
      state.generation.pollTimer = null;
    }
    generateBtn.disabled = false;
    if (generateBtnText) generateBtnText.textContent = 'Start Generation';
    var eta = $('progress-eta');
    if (eta) eta.textContent = '· server is working, keep this tab open';
    loadActivity();
    loadHistory(true);
    loadQuotes(true);
    setTimeout(function () {
      if (!state.generation.inProgress) {
        progressTracker.classList.add('hidden');
        resetPhases();
      }
    }, 6000);
  }

  function cancelGeneration() {
    // Real server-side cancel when a job is active; local-only otherwise.
    var jobId = state.generation.jobId;
    if (state.generation.pollTimer) {
      clearTimeout(state.generation.pollTimer);
      state.generation.pollTimer = null;
    }
    if (state.generation.abort) {
      try { state.generation.abort.abort(); } catch (e) {}
    }
    state.generation.inProgress = false;
    generateBtn.disabled = false;
    if (generateBtnText) generateBtnText.textContent = 'Start Generation';
    if (jobId) {
      setPhase(0, 'failed', 'cancelling…');
      fetchWithTimeout('/jobs/' + encodeURIComponent(jobId), { method: 'DELETE' }, FETCH_TIMEOUT_MS).then(function () {
        loadActivity();
        loadHistory(true);
      }).catch(function () {});
      toast('Cancel requested on server.', 'info');
    }
  }

  if (generateBtn) generateBtn.addEventListener('click', startGeneration);
  var cancelBtn = $('cancel-gen');
  if (cancelBtn) cancelBtn.addEventListener('click', cancelGeneration);

  // ── Result card ────────────────────────────────────────
  function hideResult() {
    var c = $('result-card');
    if (c) c.classList.add('hidden');
  }
  function showResult(data) {
    var card = $('result-card');
    if (!card) return;
    state.generation.lastResult = data;
    var rawUrl = data.image_url || '';
    var file = rawUrl ? rawUrl.split('/').pop() : (data.image ? String(data.image).split('/').pop() : '');
    var fullUrl = rawUrl || (file ? '/media/' + encodeURIComponent(file) : '');
    var img = $('result-img');
    if (img && fullUrl) { img.src = fullUrl; img.alt = 'Generated quote art'; }
    var badge = $('result-badge');
    if (badge) {
      var ok = data.status === 'success' || data.status === 'generated' || !!data.upload_result;
      badge.className = 'history-badge ' + (ok ? 'success' : 'error');
      badge.textContent = data.status === 'upload_failed' ? '✗ Upload failed' : ok ? '✓ Done' : '✗ ' + (data.status || 'Failed');
    }
    var t = $('result-time');
    if (t) t.textContent = new Date().toLocaleString();
    var q = $('result-quote');
    if (q) {
      var ov = data.overlay || {};
      q.textContent = ov.quote ? '“' + ov.quote + '”' + (ov.author ? ' — ' + ov.author : '') : '';
    }
    var p = $('result-prompt');
    if (p) p.textContent = data.prompt ? 'Prompt: ' + data.prompt : '';
    var view = $('result-view');
    if (view && fullUrl) view.href = fullUrl;
    var dl = $('result-download');
    if (dl && fullUrl) {
      dl.href = fullUrl;
      dl.setAttribute('download', file || 'codn-quote.jpg');
    }
    card.classList.remove('hidden');
    card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
  var copyBtn = $('result-copy');
  if (copyBtn) copyBtn.addEventListener('click', function () {
    var d = state.generation.lastResult;
    var txt = d && d.prompt ? d.prompt : '';
    if (!txt) { toast('Nothing to copy yet.', 'warn'); return; }
    if (navigator.clipboard) navigator.clipboard.writeText(txt).then(
      function () { toast('Prompt copied.', 'success'); },
      function () { toast('Copy failed.', 'error'); });
  });
  var editBtn = $('result-edit');
  if (editBtn) editBtn.addEventListener('click', function () {
    var d = state.generation.lastResult;
    if (d && d.overlay) openEditOverlay(d.overlay, d.image_url || d.image || '', d.source_filename);
    else toast('No overlay data for this result.', 'warn');
  });
  var uploadBtn = $('result-upload');
  if (uploadBtn) uploadBtn.addEventListener('click', function () {
    var d = state.generation.lastResult;
    if (!d) { toast('Nothing to upload yet.', 'warn'); return; }
    var file = (d.image_url || '').split('/').pop() ||
      (d.image ? String(d.image).split('/').pop() : '');
    if (!file) { toast('No image to upload.', 'warn'); return; }
    var ov = d.overlay || {};
    openUploadModal(file, ov.quote || '', ov.author || '');
  });

  // ── Upload to Instagram (manual review-first step) ───────
  var uploadState = null; // { filename }
  function openUploadModal(filename, quote, author, bustTs) {
    uploadState = { filename: filename };
    var img = $('upload-img');
    if (img) img.src = mediaUrl(filename, bustTs);
    var cap = $('upload-caption');
    if (cap) {
      var prefill = quote ? ('\u201C' + quote + '\u201D' + (author ? ' \u2014 ' + author : '')) : '';
      cap.value = prefill;
    }
    var reel = $('upload-is-reel');
    if (reel) reel.checked = false;
    openModal($('upload-modal'), 'upload-caption');
  }
  function closeUploadModal() {
    uploadState = null;
    closeModal($('upload-modal'));
  }
  var upClose = $('close-upload-modal'), upCancel = $('cancel-upload');
  if (upClose) upClose.addEventListener('click', closeUploadModal);
  if (upCancel) upCancel.addEventListener('click', closeUploadModal);
  var upModal = $('upload-modal');
  if (upModal) upModal.addEventListener('click', function (e) { if (e.target === upModal) closeUploadModal(); });
  var upConfirm = $('confirm-upload');
  if (upConfirm) upConfirm.addEventListener('click', function () {
    if (!uploadState) return;
    var caption = $('upload-caption') ? $('upload-caption').value.trim() : '';
    var isReel = $('upload-is-reel') ? $('upload-is-reel').checked : false;
    if (!caption) { toast('Caption is empty.', 'warn'); return; }
    upConfirm.disabled = true;
    fetchWithTimeout('/upload', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_path: uploadState.filename, caption: caption, is_reel: isReel })
    }, FETCH_TIMEOUT_MS).then(function (r) {
      return safeJson(r).then(function (d) { return { ok: r.ok, status: r.status, d: d }; });
    }).then(function (res) {
      var posted = res.ok && res.d && (res.d.status === 'uploaded' || (res.d.result && res.d.status !== 'failed'));
      if (posted) {
        closeUploadModal();
        toast('Posted to Instagram.', 'success');
        loadActivity();
        loadHistory(true);
      } else {
        toast('Upload failed: ' + ((res.d && (res.d.detail || res.d.status)) || res.status), 'error');
      }
    }).catch(function (e) {
      toast('Upload failed: ' + (e.message || 'network'), 'error');
    }).finally(function () {
      upConfirm.disabled = false;
    });
  });

  // ── Activity ───────────────────────────────────────────
  function relTime(dateStr) {
    if (!dateStr) return '';
    var then = new Date(String(dateStr).replace(' ', 'T') + 'Z');
    if (isNaN(then.getTime())) return String(dateStr);
    var s = Math.floor((Date.now() - then.getTime()) / 1000);
    if (s < 60) return 'just now';
    var m = Math.floor(s / 60);
    if (m < 60) return m + ' min ago';
    var h = Math.floor(m / 60);
    if (h < 24) return h + 'h ago';
    return Math.floor(h / 24) + 'd ago';
  }

  function loadActivity() {
    var list = $('activity-list');
    fetchWithTimeout('/history', {}, 8000).then(function (r) {
      if (!r.ok) throw new Error('history ' + r.status);
      return r.json();
    }).then(function (d) {
      renderActivity((d.generations || []).slice(0, 5));
    }).catch(function () {
      if (list && !list.children.length) {
        list.textContent = '';
        list.appendChild(el('p', 'activity-empty', 'Could not reach backend. Is it running on :4000?'));
      }
    });
  }

  function renderActivity(items) {
    var list = $('activity-list');
    if (!list) return;
    list.textContent = '';
    if (!items.length) {
      list.appendChild(el('p', 'activity-empty', 'No activity yet. Start a generation to see it here.'));
      return;
    }
    items.forEach(function (g) {
      var ok = g.status === 'success' || g.status === 'generated';
      var warn = g.status === 'upload_failed';
      var item = el('div', 'activity-item reveal');
      var badge = el('span', 'activity-badge ' + (ok ? 'success' : warn ? 'warn' : 'error'),
        ok ? '✓ Success' : warn ? '! Upload failed' : '✗ Failed');
      item.appendChild(badge);
      item.appendChild(el('span', 'activity-time', relTime(g.created_at)));
      var desc = (g.quote_text ? '“' + g.quote_text + '”' : '—') + ' — ' + (g.category || 'general');
      if (g.error_message) desc += ' — ' + String(g.error_message).slice(0, 60);
      item.appendChild(el('span', 'activity-desc', desc));
      list.appendChild(item);
    });
    watchReveals(list);
  }
  var refreshAct = $('refresh-activity');
  if (refreshAct) refreshAct.addEventListener('click', loadActivity);

  // ── Quotes ─────────────────────────────────────────────
  function loadQuotes(quiet) {
    var grid = $('quotes-grid');
    if (!quiet && grid && !state.quotes.all.length) {
      grid.textContent = '';
      for (var i = 0; i < 3; i++) {
        var sk = el('div', 'quote-card skeleton-card');
        sk.appendChild(el('div', 'skeleton'));
        grid.appendChild(sk);
      }
    }
    var url = '/quotes';
    fetchWithTimeout(url, {}, 8000).then(function (r) {
      if (!r.ok) throw new Error('quotes ' + r.status);
      return r.json();
    }).then(function (d) {
      state.quotes.all = d.quotes || [];
      state.quotes.page = 1;
      renderQuotes();
    }).catch(function (e) {
      if (!quiet) toast('Could not load quotes: ' + (e.message || 'network'), 'error');
    });
  }

  function filteredQuotes() {
    var term = $('quote-search') ? $('quote-search').value.trim().toLowerCase() : '';
    var cat = $('category-filter') ? $('category-filter').value.toLowerCase() : '';
    return state.quotes.all.filter(function (q) {
      var hay = ((q.quote_text || '') + ' ' + (q.author || '')).toLowerCase();
      var okS = !term || hay.indexOf(term) !== -1;
      var okC = !cat || String(q.category || 'general').toLowerCase().indexOf(cat) !== -1;
      return okS && okC;
    });
  }

  function categoryClass(c) {
    c = String(c || 'general').toLowerCase();
    return ['philosophy', 'anime', 'literature', 'literary', 'general'].indexOf(c) !== -1 ? c : 'general';
  }

  function renderQuotes() {
    var grid = $('quotes-grid');
    if (!grid) return;
    grid.textContent = '';
    var list = filteredQuotes();
    var pages = Math.max(1, Math.ceil(list.length / QUOTES_PER_PAGE));
    state.quotes.page = Math.min(Math.max(1, state.quotes.page), pages);
    var start = (state.quotes.page - 1) * QUOTES_PER_PAGE;
    var slice = list.slice(start, start + QUOTES_PER_PAGE);
    if (!slice.length) {
      if (state.quotes.all.length) {
        grid.appendChild(el('p', 'empty-state', 'No quotes match this filter.'));
      } else {
        grid.appendChild(emptyStateWithAction(
          'Your library is empty.',
          'Generate your first quote-art and it will land here.',
          'Start a generation',
          function () { switchSection('dashboard'); }
        ));
      }
    }
    slice.forEach(function (q) {
      var card = el('div', 'quote-card reveal spot');
      var prev = el('div', 'quote-preview');
      prev.appendChild(el('div', 'quote-text', '“' + (q.quote_text || '') + '”'));
      card.appendChild(prev);
      var meta = el('div', 'quote-meta');
      meta.appendChild(el('span', 'quote-category ' + categoryClass(q.category), q.category || 'general'));
      meta.appendChild(el('span', 'quote-source', q.author || 'Unknown'));
      card.appendChild(meta);
      if (q.created_at) card.appendChild(el('div', 'quote-date', relTime(q.created_at)));
      grid.appendChild(card);
    });
    var info = $('quotes-page-info');
    if (info) info.textContent = list.length ? ('Page ' + state.quotes.page + ' of ' + pages + ' · ' + list.length + ' quotes') : 'No quotes yet';
    var pv = $('quotes-prev'), nx = $('quotes-next');
    if (pv) pv.disabled = state.quotes.page <= 1;
    if (nx) nx.disabled = state.quotes.page >= pages;
    watchReveals(grid);
  }

  var qs = $('quote-search');
  if (qs) qs.addEventListener('input', function () { state.quotes.page = 1; renderQuotes(); });
  var cf = $('category-filter');
  if (cf) cf.addEventListener('input', function () { state.quotes.page = 1; renderQuotes(); });
  var qp = $('quotes-prev'), qn = $('quotes-next');
  if (qp) qp.addEventListener('click', function () { state.quotes.page--; renderQuotes(); });
  if (qn) qn.addEventListener('click', function () { state.quotes.page++; renderQuotes(); });

  // ── History ────────────────────────────────────────────
  function loadHistory(quiet) {
    var grid = $('history-grid');
    var status = $('status-filter') ? $('status-filter').value : '';
    var cat = $('history-category-filter') ? $('history-category-filter').value : '';
    var params = [];
    if (status) params.push('status=' + encodeURIComponent(status));
    var url = '/history' + (params.length ? '?' + params.join('&') : '');
    fetchWithTimeout(url, {}, 8000).then(function (r) {
      if (!r.ok) throw new Error('history ' + r.status);
      return r.json();
    }).then(function (d) {
      var gens = d.generations || [];
      // Tag filter is client-side substring (tags are model-derived, open set).
      if (cat) gens = gens.filter(function (g) { return String(g.category || '').toLowerCase().indexOf(cat.toLowerCase()) !== -1; });
      if (status) gens = gens.filter(function (g) { return String(g.status || '') === status; });
      state.history.all = gens;
      state.history.page = 1;
      renderHistory();
    }).catch(function (e) {
      if (!quiet) {
        if (grid && !state.history.all.length) {
          grid.textContent = '';
          grid.appendChild(el('p', 'empty-state', 'Could not load history: ' + (e.message || 'network')));
        } else toast('Could not refresh history.', 'error');
      }
    });
  }

  function statusBadge(g) {
    if (g.status === 'success' || g.status === 'generated') return { cls: 'success', txt: '✓ Success' };
    if (g.status === 'upload_failed') return { cls: 'warn', txt: '! Upload failed' };
    return { cls: 'error', txt: '✗ Failed' };
  }

  function renderHistory() {
    var grid = $('history-grid');
    if (!grid) return;
    grid.textContent = '';
    var list = state.history.all;
    var pages = Math.max(1, Math.ceil(list.length / HISTORY_PER_PAGE));
    state.history.page = Math.min(Math.max(1, state.history.page), pages);
    var slice = list.slice((state.history.page - 1) * HISTORY_PER_PAGE, state.history.page * HISTORY_PER_PAGE);
    if (!slice.length) {
      grid.appendChild(emptyStateWithAction(
        'Nothing here yet.',
        'Runs of the pipeline will appear in this journal.',
        'Start a generation',
        function () { switchSection('dashboard'); }
      ));
    }
    slice.forEach(function (g) {
      var b = statusBadge(g);
      var card = el('div', 'history-card reveal spot');
      var thumb = el('div', 'history-thumb');
      if (g.image_filename) {
        var img = document.createElement('img');
        img.src = mediaUrl(g.image_filename, g.updated_at);
        img.alt = 'Generated quote art';
        img.loading = 'lazy';
        img.onerror = function () { this.style.display = 'none'; };
        thumb.appendChild(img);
      } else {
        thumb.appendChild(el('span', 'thumb-fallback', 'no image'));
      }
      card.appendChild(thumb);
      var info = el('div', 'history-info');
      info.appendChild(el('span', 'history-badge ' + b.cls, b.txt));
      info.appendChild(el('span', 'history-date', g.created_at || ''));
      var qtxt = g.quote_text ? '“' + g.quote_text + '”' : '—';
      info.appendChild(el('span', 'history-quote', qtxt + ' — ' + (g.category || 'general')));
      if (g.error_message) info.appendChild(el('span', 'history-error', String(g.error_message).slice(0, 120)));
      card.appendChild(info);
      var actions = el('div', 'history-actions');
      if (g.image_filename) {
        var a = el('a', 'btn btn-sm btn-ghost', 'View');
        a.href = mediaUrl(g.image_filename, g.updated_at);
        a.target = '_blank';
        a.rel = 'noopener';
        actions.appendChild(a);
        var dl = el('a', 'btn btn-sm btn-ghost', 'Download');
        dl.href = mediaUrl(g.image_filename, g.updated_at);
        dl.setAttribute('download', g.image_filename);
        actions.appendChild(dl);
        if (g.quote_text || g.author) {
          var eb = el('button', 'btn btn-sm btn-ghost', 'Edit text');
          eb.type = 'button';
          (function (rec) {
            eb.addEventListener('click', function () {
              // Stored overlay + clean source restore the exact generated
              // composition; legacy rows without them fall back gracefully.
              openEditOverlay(rec.overlay || { quote: rec.quote_text || '', author: rec.author || '', font_size: 'medium', text_color: '#FFFFFF', position: 'bottom-center' }, mediaUrl(rec.image_filename, rec.updated_at), rec.source_filename, rec.updated_at);
            });
          })(g);
          actions.appendChild(eb);
        }
        var ub = el('button', 'btn btn-sm btn-primary', 'Upload');
        ub.type = 'button';
        (function (rec) {
          ub.addEventListener('click', function () {
            openUploadModal(rec.image_filename, rec.quote_text || '', rec.author || '', rec.updated_at);
          });
        })(g);
        actions.appendChild(ub);
      }
      card.appendChild(actions);
      grid.appendChild(card);
    });
    var info2 = $('history-page-info');
    if (info2) info2.textContent = list.length ? ('Page ' + state.history.page + ' of ' + pages + ' · ' + list.length + ' runs') : '';
    var hp = $('history-prev'), hn = $('history-next');
    if (hp) hp.disabled = state.history.page <= 1;
    if (hn) hn.disabled = state.history.page >= pages;
    watchReveals(grid);
  }

  var sf = $('status-filter'), hcf = $('history-category-filter');
  if (sf) sf.addEventListener('change', function () { state.history.page = 1; loadHistory(true); });
  if (hcf) hcf.addEventListener('input', function () { state.history.page = 1; loadHistory(true); });
  var rh = $('refresh-history');
  if (rh) rh.addEventListener('click', function () { loadHistory(); });
  var hp2 = $('history-prev'), hn2 = $('history-next');
  if (hp2) hp2.addEventListener('click', function () { state.history.page--; renderHistory(); });
  if (hn2) hn2.addEventListener('click', function () { state.history.page++; renderHistory(); });

  var exportBtn = $('export-btn');
  if (exportBtn) exportBtn.addEventListener('click', function () {
    var rows = state.history.all;
    if (!rows.length) { toast('Nothing to export yet.', 'warn'); return; }
    var head = ['id', 'status', 'category', 'theme', 'quote_text', 'author', 'image_filename', 'error_message', 'created_at'];
    function csvCell(v) {
      v = v == null ? '' : String(v);
      return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
    }
    var lines = [head.join(',')].concat(rows.map(function (g) {
      return head.map(function (k) { return csvCell(g[k]); }).join(',');
    }));
    var blob = new Blob([lines.join('\n')], { type: 'text/csv' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'codn-history.csv';
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
    toast('Exported ' + rows.length + ' rows.', 'success');
  });

  // ── Modals (a11y) ──────────────────────────────────────
  var lastFocus = null;
  function openModal(m, focusId) {
    lastFocus = document.activeElement;
    m.classList.remove('hidden');
    document.body.classList.add('modal-open');
    var f = focusId ? $(focusId) : m.querySelector('textarea,input,select,button');
    if (f) setTimeout(function () { f.focus(); }, 30);
  }
  function closeModal(m) {
    m.classList.add('hidden');
    document.body.classList.remove('modal-open');
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    [$('add-quote-modal'), $('edit-overlay-modal')].forEach(function (m) {
      if (m && !m.classList.contains('hidden')) {
        if (m.id === 'edit-overlay-modal') closeEditOverlay();
        else closeModal(m);
      }
    });
  });

  // Add quote
  var addModal = $('add-quote-modal');
  var addBtn = $('add-quote-btn');
  if (addBtn) addBtn.addEventListener('click', function () { openModal(addModal, 'new-quote-text'); });
  var closeM = $('close-modal'), cancelAQ = $('cancel-add-quote');
  if (closeM) closeM.addEventListener('click', function () { closeModal(addModal); });
  if (cancelAQ) cancelAQ.addEventListener('click', function () { closeModal(addModal); });
  if (addModal) addModal.addEventListener('click', function (e) { if (e.target === addModal) closeModal(addModal); });

  var saveAQ = $('save-add-quote');
  if (saveAQ) saveAQ.addEventListener('click', function () {
    var text = $('new-quote-text').value.trim();
    var source = $('new-quote-source').value.trim();
    var category = $('new-quote-category') ? $('new-quote-category').value.trim() : '';
    if (!text || !source) { toast('Please fill in quote text and author.', 'warn'); return; }
    saveAQ.disabled = true;
    fetchWithTimeout('/api/quotes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ quote_text: text, author: source, category: category })
    }, 8000).then(function (r) {
      if (!r.ok) throw new Error('no api');
      return r.json();
    }).then(function () {
      toast('Quote saved to server.', 'success');
      $('new-quote-text').value = '';
      $('new-quote-source').value = '';
      closeModal(addModal);
      loadQuotes(true);
    }).catch(function () {
      // Fallback: session-only card so the button never feels dead
      // (e.g. offline or unreachable backend).
      state.quotes.all.unshift({ quote_text: text, author: source, category: category, created_at: new Date().toISOString().slice(0, 19).replace('T', ' ') + ' (local)' });
      state.quotes.page = 1;
      if (state.currentSection !== 'quotes') switchSection('quotes');
      else renderQuotes();
      $('new-quote-text').value = '';
      $('new-quote-source').value = '';
      closeModal(addModal);
      toast('Could not reach the server — kept in this session only.', 'warn');
    }).finally(function () { saveAQ.disabled = false; });
  });

  // ── Post-edit overlay ──────────────────────────────────
  var editModal = $('edit-overlay-modal');
  var overlayState = null;

  // Font/style keys the modal preserves verbatim — only the 5 visible
  // controls change. Sent back so edits never reset the typography.
  var OVERLAY_PASSTHROUGH = ['font_style', 'font', 'font_weight', 'alignment',
    'x_offset', 'y_offset', 'background_overlay', 'text_effect', 'glow_color',
    'text_gradient_from', 'text_gradient_to', 'letter_spacing'];
  var POSITION_ALIASES = { top: 'top-center', bottom: 'bottom-center' };

  // imagePath: composition to display/update. sourceFilename: clean base
  // art to render from (single composition — edits never stack text).
  // bustTs: row updated_at for cache-busting the initial preview.
  function openEditOverlay(overlayData, imagePath, sourceFilename, bustTs) {
    var overlay = overlayData || {};
    var quote = overlay.quote || overlay.quote_text || '';
    var author = overlay.author || '';
    var fontSize = overlay.font_size || 'medium';
    var color = overlay.text_color || '#FFFFFF';
    var position = POSITION_ALIASES[overlay.position] || overlay.position || 'bottom-center';
    var filename = String(imagePath || '').split('/').pop().split('?')[0];
    if (!filename) { toast('No image to edit.', 'warn'); return; }
    var source = String(sourceFilename || '').split('/').pop().split('?')[0] || filename;
    overlayState = { filename: filename, source: source, timer: null, base: overlay };
    $('edit-quote-text').value = quote;
    $('edit-author').value = author;
    // Fall back to medium when the stored size isn't offered (forward-compat).
    var sizeSel = $('edit-font-size');
    var sizeOk = false;
    if (sizeSel) {
      for (var i = 0; i < sizeSel.options.length; i++) {
        if (sizeSel.options[i].value === fontSize) { sizeOk = true; break; }
      }
      sizeSel.value = sizeOk ? fontSize : 'medium';
    }
    var cc = $('edit-text-color');
    cc.value = /^#[0-9a-fA-F]{6}$/.test(color) ? color : '#FFFFFF';
    var prev = $('preview-img');
    prev.src = mediaUrl(filename, bustTs);
    prev.alt = 'Preview of edited overlay';
    syncPosGrid(position);
    var load = $('edit-loading');
    if (load) { load.textContent = 'Loading preview…'; load.hidden = true; }
    openModal(editModal, 'edit-quote-text');
  }

  function syncPosGrid(position) {
    Array.prototype.forEach.call(document.querySelectorAll('.pos-cell'), function (c) {
      var on = c.dataset.anchor === position;
      c.classList.toggle('active', on);
      c.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    var grid = document.querySelector('.position-grid');
    if (grid) grid.dataset.position = position;
  }

  function closeEditOverlay() {
    if (overlayState && overlayState.timer) clearTimeout(overlayState.timer);
    overlayState = null;
    closeModal(editModal);
  }

  function collectOverlayParams() {
    if (!overlayState) return null;
    var active = document.querySelector('.pos-cell.active');
    var params = {
      filename: overlayState.filename,
      source_filename: overlayState.source,
      quote_text: $('edit-quote-text').value.trim(),
      author: $('edit-author').value.trim(),
      font_size: $('edit-font-size').value,
      text_color: $('edit-text-color').value,
      position: active ? active.dataset.anchor : 'bottom-center'
    };
    // Preserve the generated typography — the modal doesn't expose these,
    // so they round-trip untouched instead of resetting to defaults.
    var base = overlayState.base || {};
    OVERLAY_PASSTHROUGH.forEach(function (k) {
      if (base[k] !== undefined && base[k] !== null && base[k] !== '') params[k] = base[k];
    });
    return params;
  }

  function requestPreview() {
    if (!overlayState || editModal.classList.contains('hidden')) return;
    var params = collectOverlayParams();
    if (!params) return;
    var load = $('edit-loading');
    if (load) { load.hidden = false; }
    fetchWithTimeout('/preview-overlay', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params)
    }, FETCH_TIMEOUT_MS).then(function (r) {
      return safeJson(r).then(function (d) { return { ok: r.ok, d: d }; });
    }).then(function (res) {
      if (res.ok && res.d.image_url) $('preview-img').src = res.d.image_url;
    }).catch(function () {}).finally(function () {
      if (load) load.hidden = true;
    });
  }

  function schedulePreview() {
    if (!overlayState) return;
    if (overlayState.timer) clearTimeout(overlayState.timer);
    overlayState.timer = setTimeout(requestPreview, 500);
  }

  var ce = $('close-edit-modal'), ca = $('cancel-edit-overlay');
  if (ce) ce.addEventListener('click', closeEditOverlay);
  if (ca) ca.addEventListener('click', closeEditOverlay);
  if (editModal) editModal.addEventListener('click', function (e) { if (e.target === editModal) closeEditOverlay(); });
  ['edit-quote-text', 'edit-author', 'edit-font-size', 'edit-text-color'].forEach(function (id) {
    var n = $(id);
    if (!n) return;
    n.addEventListener('input', schedulePreview);
    n.addEventListener('change', schedulePreview);
  });
  Array.prototype.forEach.call(document.querySelectorAll('.pos-cell'), function (cell) {
    cell.addEventListener('click', function () {
      syncPosGrid(cell.dataset.anchor);
      schedulePreview();
    });
  });

  var applyBtn = $('apply-edit-overlay');
  if (applyBtn) applyBtn.addEventListener('click', function () {
    if (!overlayState) return;
    var params = collectOverlayParams();
    if (!params.quote_text) { toast('Quote text is empty.', 'warn'); return; }
    applyBtn.disabled = true;
    var load = $('edit-loading');
    if (load) { load.textContent = 'Saving…'; load.hidden = false; }
    fetchWithTimeout('/apply-overlay', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params)
    }, FETCH_TIMEOUT_MS).then(function (r) {
      return safeJson(r).then(function (d) { return { ok: r.ok, d: d }; });
    }).then(function (res) {
      if (res.ok) {
        // In-place update: refresh the result card if it shows this file,
        // using the cache-busted URL the server returned.
        var newUrl = (res.d && res.d.image_url) || '';
        var last = state.generation.lastResult;
        if (newUrl && last) {
          var lastFile = ((last.image_url || '').split('/').pop() || '').split('?')[0];
          var newFile = newUrl.split('/').pop().split('?')[0];
          if (lastFile && lastFile === newFile) {
            var rimg = $('result-img');
            if (rimg) rimg.src = newUrl;
            var rview = $('result-view');
            if (rview) rview.href = newUrl;
            var rdl = $('result-download');
            if (rdl) { rdl.href = newUrl; rdl.setAttribute('download', newFile); }
          }
        }
        closeEditOverlay();
        toast('Overlay applied.', 'success');
        loadActivity();
        loadHistory(true);
      } else {
        toast('Failed to apply edits: ' + ((res.d && res.d.detail) || 'unknown error'), 'error');
      }
    }).catch(function (e) {
      toast('Failed to apply edits: ' + (e.message || 'network'), 'error');
    }).finally(function () {
      applyBtn.disabled = false;
      if (load) { load.textContent = 'Loading preview…'; load.hidden = true; }
    });
  });

  // ── Settings (local-only) ──────────────────────────────
  function loadSettings() {
    var saved = null;
    try { saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || 'null'); } catch (e) {}
    var merged = Object.assign({}, DEFAULT_SETTINGS, saved || {});
    Object.keys(DEFAULT_SETTINGS).forEach(function (id) {
      var n = $(id);
      if (n) n.value = merged[id] != null ? merged[id] : '';
    });
    try {
      var pe = localStorage.getItem('codn-postedit');
      if (pe != null && $('postedit-toggle')) $('postedit-toggle').checked = pe !== '0';
    } catch (e) {}
    // API token is session-only; reflect it without persisting.
    var at = $('api-token');
    if (at) at.value = getApiToken();
    // Model path shows the server's live value (source of truth).
    var mp = $('model-path');
    if (mp) fetchWithTimeout('/api/text-model', {}, 8000).then(function (r) {
      if (!r.ok) return null;
      return r.json();
    }).then(function (d) {
      if (d && d.path && mp) {
        mp.value = d.path;
        mp.title = d.exists ? 'Model file found on server' : 'WARNING: file not found on server';
      }
    }).catch(function () {});
  }
  var saveS = $('save-settings');
  if (saveS) saveS.addEventListener('click', function () {
    var out = {};
    Object.keys(DEFAULT_SETTINGS).forEach(function (id) {
      var n = $(id);
      if (n) out[id] = n.value;
    });
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(out));
      if ($('postedit-toggle')) localStorage.setItem('codn-postedit', $('postedit-toggle').checked ? '1' : '0');
    } catch (e) {}
    // API token goes to session storage only, never localStorage.
    var tokEl = $('api-token');
    if (tokEl) {
      setApiToken(tokEl.value.trim());
      tokEl.value = getApiToken();
    }
    // Model path is applied to the server live (takes effect next job).
    var mpEl = $('model-path');
    var mpVal = mpEl ? mpEl.value.trim() : '';
    if (!mpVal) {
      toast('Settings saved in this browser only.', 'success');
      return;
    }
    fetchWithTimeout('/api/text-model', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: mpVal })
    }, 8000).then(function (r) {
      return safeJson(r).then(function (d) { return { ok: r.ok, status: r.status, d: d }; });
    }).then(function (res) {
      if (res.ok) {
        toast('Settings saved; text model switched' + (res.d.persisted === false ? ' (live only — .env not writable)' : '') + '.', 'success');
      } else {
        toast('Settings saved locally, but model switch failed: ' + ((res.d && res.d.detail) || res.status), 'error');
      }
    }).catch(function (e) {
      toast('Settings saved locally, but model switch failed: ' + (e.message || 'network'), 'error');
    });
  });
  var resetS = $('reset-settings');
  if (resetS) resetS.addEventListener('click', function () {
    Object.keys(DEFAULT_SETTINGS).forEach(function (id) {
      var n = $(id);
      if (n) n.value = DEFAULT_SETTINGS[id];
    });
    try { localStorage.removeItem(SETTINGS_KEY); } catch (e) {}
    var tokReset = $('api-token');
    if (tokReset) tokReset.value = '';
    setApiToken('');
    toast('Settings reset to defaults.', 'info');
  });
  var peT = $('postedit-toggle');
  if (peT) peT.addEventListener('change', function () {
    try { localStorage.setItem('codn-postedit', peT.checked ? '1' : '0'); } catch (e) {}
  });

  // ── First-run setup ──────────────────────────────────
  // Dismissible checklist banner + 4-step wizard. Probes the live server
  // (individual endpoints today; GET /api/setup/status when the backend
  // ships it). Never blocks the app. Token stays session-only.
  var SETUP_KEY = 'codn-setup-v1';
  var SETUP_DISMISS_KEY = 'codn-setup-dismissed';
  var SETUP_ORDER = ['access', 'model', 'instagram', 'tunnel', 'comfy'];
  var SETUP_LABELS = { access: 'API access', model: 'Model', instagram: 'Instagram', tunnel: 'Tunnel', comfy: 'ComfyUI' };
  var setupChecks = {};
  var setupStep = 0;
  SETUP_ORDER.forEach(function (k) { setupChecks[k] = { pass: false, detail: 'not checked yet' }; });

  function setCheck(name, pass, detail) {
    setupChecks[name] = { pass: !!pass, detail: detail || '' };
  }

  function probeStep(url, opts) {
    var o = Object.assign({ quiet: true }, opts || {});
    return fetchWithTimeout(url, o, FETCH_TIMEOUT_MS).then(function (resp) {
      return safeJson(resp).then(function (data) { return { ok: resp.ok, status: resp.status, data: data }; });
    }).catch(function () { return { ok: false, status: 0, data: {} }; });
  }

  function probeIndividual() {
    var configData = null;
    return probeStep('/config').then(function (r) {
      configData = r.ok ? r.data : null;
      setCheck('access', !!configData, configData ? 'token accepted' : 'token missing or rejected');
      setCheck('instagram', !!(configData && configData.instagram_account),
        configData ? (configData.instagram_account ? '@' + configData.instagram_account : 'not linked') : 'needs API access first');
      return probeStep('/api/text-model');
    }).then(function (r) {
      if (r.ok) setCheck('model', !!r.data.exists, r.data.path || '');
      else setCheck('model', false, configData ? 'check failed' : 'needs API access first');
      return probeStep('/api/tunnel');
    }).then(function (r) {
      var url = (r.ok && r.data && r.data.url) || '';
      setCheck('tunnel', !!url, url || 'no public URL yet');
      return probeStep('/comfy/status');
    }).then(function (r) {
      if (!r.ok) {
        setCheck('comfy', false, configData ? 'check failed' : 'needs API access first');
      } else {
        var missing = (r.data && r.data.missing) || [];
        setCheck('comfy', !!r.data.connected && missing.length === 0,
          !r.data.connected ? String((r.data && r.data.error) || 'unreachable').slice(0, 60)
            : (missing.length ? 'missing nodes: ' + missing.join(', ') : ((r.data.nodes_count || '?') + ' nodes')));
      }
      renderSetupBanner();
      renderSetupRail();
      return setupChecks;
    });
  }

  function probeSetup() {
    return probeStep('/api/setup/status').then(function (res) {
      if (res.ok && res.data && res.data.checks && typeof res.data.checks === 'object') {
        var complete = true;
        SETUP_ORDER.forEach(function (k) {
          var c = res.data.checks[k];
          if (!c || typeof c.pass === 'undefined') complete = false;
          else setCheck(k, c.pass, c.detail || '');
        });
        if (complete) {
          renderSetupBanner();
          renderSetupRail();
          return setupChecks;
        }
      }
      return probeIndividual();
    });
  }

  function setupPassCount() {
    return SETUP_ORDER.filter(function (k) { return setupChecks[k].pass; }).length;
  }

  function setupDismissed() {
    try { return sessionStorage.getItem(SETUP_DISMISS_KEY) === '1'; } catch (e) { return false; }
  }

  function renderSetupBanner() {
    var banner = $('setup-banner');
    if (!banner) return;
    var n = setupPassCount();
    var count = $('setup-count');
    if (count) count.textContent = n + '/' + SETUP_ORDER.length;
    if (n === SETUP_ORDER.length) {
      banner.classList.add('hidden');
      try { localStorage.setItem(SETUP_KEY, JSON.stringify({ done: true, at: Date.now() })); } catch (e) {}
      return;
    }
    var missing = SETUP_ORDER.filter(function (k) { return !setupChecks[k].pass; })
      .map(function (k) { return SETUP_LABELS[k]; });
    var summary = $('setup-summary');
    if (summary) summary.textContent = 'Needs: ' + missing.join(', ');
    banner.classList.toggle('hidden', setupDismissed());
  }

  function railDoneFor(step) {
    if (step === 0) return setupChecks.access.pass;
    if (step === 1) return setupChecks.model.pass;
    if (step === 2) return setupChecks.instagram.pass;
    return setupChecks.tunnel.pass && setupChecks.comfy.pass;
  }

  function renderSetupRail() {
    Array.prototype.forEach.call(document.querySelectorAll('#setup-rail li'), function (li) {
      var i = parseInt(li.dataset.step, 10);
      li.classList.toggle('active', i === setupStep);
      li.classList.toggle('done', railDoneFor(i));
    });
    Array.prototype.forEach.call(document.querySelectorAll('.setup-step'), function (s) {
      s.classList.toggle('active', parseInt(s.dataset.step, 10) === setupStep);
    });
    var back = $('setup-back'), next = $('setup-next');
    if (back) back.disabled = setupStep === 0;
    if (next) next.textContent = setupStep === 3 ? 'Finish' : 'Next →';
  }

  function showSetupStep(i) {
    setupStep = Math.max(0, Math.min(3, i));
    renderSetupRail();
    if (setupStep === 3) renderSetupChecks();
  }

  function firstFailingStep() {
    if (!setupChecks.access.pass) return 0;
    if (!setupChecks.model.pass) return 1;
    if (!setupChecks.instagram.pass) return 2;
    return 3;
  }

  function openSetup(atStep) {
    var m = $('setup-modal');
    if (!m) return;
    try {
      var tok = getApiToken();
      var field = $('setup-token');
      if (tok && field && !field.value) field.value = tok;
    } catch (e) {}
    probeSetup().then(function () {
      showSetupStep(typeof atStep === 'number' ? atStep : firstFailingStep());
      renderSetupChecks();
      openModal(m, 'setup-token');
    });
  }

  function closeSetup(dismiss) {
    var m = $('setup-modal');
    if (m) closeModal(m);
    if (dismiss) {
      try { sessionStorage.setItem(SETUP_DISMISS_KEY, '1'); } catch (e) {}
      renderSetupBanner();
    }
  }

  function setSetupResult(id, cls, text) {
    var n = $(id);
    if (!n) return;
    n.className = 'setup-result ' + cls;
    n.textContent = text;
  }

  function renderSetupChecks() {
    var box = $('setup-checks');
    if (!box) return;
    box.textContent = '';
    [['tunnel', 'Public tunnel'], ['comfy', 'ComfyUI']].forEach(function (pair) {
      var c = setupChecks[pair[0]];
      var row = el('div', 'setup-check ' + (c.pass ? 'ok' : 'fail'));
      row.appendChild(el('span', 'check-dot'));
      row.appendChild(el('span', 'check-name', pair[1]));
      row.appendChild(el('span', 'check-detail', c.detail || ''));
      box.appendChild(row);
    });
  }

  // Step 1: verify pasted token against /config.
  function setupVerifyToken() {
    var v = $('setup-token') ? $('setup-token').value.trim() : '';
    if (!v) { setSetupResult('setup-token-result', 'fail', 'Paste a token first.'); return; }
    var apiField = $('api-token');
    if (apiField) apiField.value = v;
    setApiToken(v);
    setSetupResult('setup-token-result', 'busy', 'Verifying…');
    probeStep('/config').then(function (r) {
      if (r.ok) {
        setSetupResult('setup-token-result', 'ok', 'Token accepted.');
        toast('API access confirmed.', 'success');
      } else {
        setSetupResult('setup-token-result', 'fail',
          r.status === 503 ? 'Server has no API_TOKEN in .env.' : 'Rejected (' + (r.status || 'network') + ') — check the token.');
      }
      probeSetup();
    });
  }

  // Step 2: check current model / save a new path.
  function setupCheckModel() {
    setSetupResult('setup-model-result', 'busy', 'Checking…');
    probeStep('/api/text-model').then(function (r) {
      if (!r.ok) {
        setSetupResult('setup-model-result', 'fail', 'Needs API access first (step 1).');
        return;
      }
      var input = $('setup-model');
      if (input && r.data.path) input.value = r.data.path;
      setSetupResult('setup-model-result', r.data.exists ? 'ok' : 'fail',
        (r.data.exists ? 'Found: ' : 'Missing: ') + (r.data.path || 'unknown path'));
    });
  }

  function setupSaveModel() {
    var p = $('setup-model') ? $('setup-model').value.trim() : '';
    if (!p) { setSetupResult('setup-model-result', 'fail', 'Enter a .gguf path first.'); return; }
    setSetupResult('setup-model-result', 'busy', 'Saving…');
    fetchWithTimeout('/api/text-model', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: p })
    }, FETCH_TIMEOUT_MS).then(function (resp) {
      return safeJson(resp).then(function (data) { return { ok: resp.ok, status: resp.status, data: data }; });
    }).then(function (res) {
      if (res.ok) {
        setSetupResult('setup-model-result', 'ok',
          'Saved' + (res.data.persisted === false ? ' (live only — .env write failed)' : ' + persisted to .env'));
        toast('Model updated.', 'success');
      } else {
        setSetupResult('setup-model-result', 'fail', apiErrorHint(res.status, res.data));
      }
      probeSetup();
    }).catch(function (e) {
      setSetupResult('setup-model-result', 'fail', 'Network error: ' + (e.message || e));
    });
  }

  // Step 3: verify + save Instagram (new endpoint when available, else guide).
  function setupSaveIG() {
    var tok = $('setup-ig-token') ? $('setup-ig-token').value.trim() : '';
    var bid = $('setup-ig-id') ? $('setup-ig-id').value.trim() : '';
    if (!tok || !bid) { setSetupResult('setup-ig-result', 'fail', 'Both token and business ID are required.'); return; }
    setSetupResult('setup-ig-result', 'busy', 'Verifying with Instagram…');
    fetchWithTimeout('/api/setup/instagram', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ access_token: tok, business_id: bid })
    }, FETCH_TIMEOUT_MS).then(function (resp) {
      return safeJson(resp).then(function (data) { return { ok: resp.ok, status: resp.status, data: data }; });
    }).then(function (res) {
      if (res.ok) {
        setSetupResult('setup-ig-result', 'ok', 'Linked as @' + ((res.data && res.data.username) || 'unknown'));
        var t = $('setup-ig-token');
        if (t) t.value = '';
        toast('Instagram connected.', 'success');
      } else if (res.status === 404) {
        setSetupResult('setup-ig-result', 'warn',
          'This server stores IG creds in .env only: set INSTAGRAM_ACCESS_TOKEN + INSTAGRAM_BUSINESS_ID, restart, then re-check below.');
      } else {
        setSetupResult('setup-ig-result', 'fail', apiErrorHint(res.status, res.data));
      }
      probeSetup();
    }).catch(function (e) {
      setSetupResult('setup-ig-result', 'fail', 'Network error: ' + (e.message || e));
    });
  }

  function setupRecheckIG() {
    setSetupResult('setup-ig-result', 'busy', 'Re-checking link…');
    probeSetup().then(function () {
      var c = setupChecks.instagram;
      setSetupResult('setup-ig-result', c.pass ? 'ok' : 'fail',
        c.pass ? ('Linked (' + c.detail + ')') : ('Not linked: ' + c.detail));
    });
  }

  // Step 4: trial generation (no upload), wizard-local polling.
  function setupTrialRun() {
    if (state.generation.inProgress) {
      setSetupResult('setup-trial-result', 'warn', 'A generation is already running — wait for it to finish.');
      return;
    }
    setSetupResult('setup-trial-result', 'busy', 'Queueing trial…');
    fetchWithTimeout('/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ upload: false, category: 'general' })
    }, FETCH_TIMEOUT_MS).then(function (resp) {
      return safeJson(resp).then(function (data) { return { ok: resp.ok, status: resp.status, data: data }; });
    }).then(function (res) {
      if (!res.ok || !(res.data && res.data.job_id)) {
        setSetupResult('setup-trial-result', 'fail',
          res.ok ? 'Unexpected server response.' : apiErrorHint(res.status, res.data));
        return;
      }
      setupPollTrial(res.data.job_id, Date.now() + GENERATE_TIMEOUT_MS);
    }).catch(function (e) {
      setSetupResult('setup-trial-result', 'fail', 'Network error: ' + (e.message || e));
    });
  }

  function setupPollTrial(jobId, deadline) {
    var modal = $('setup-modal');
    if (!modal || modal.classList.contains('hidden')) return;
    setSetupResult('setup-trial-result', 'busy', 'Trial queued…');
    function tick() {
      var m = $('setup-modal');
      if (!m || m.classList.contains('hidden')) return;
      probeStep('/jobs/' + encodeURIComponent(jobId)).then(function (res) {
        if (!res.ok) {
          if (Date.now() > deadline) setSetupResult('setup-trial-result', 'fail', 'Trial timed out.');
          else setTimeout(tick, POLL_INTERVAL_MS);
          return;
        }
        var job = res.data || {};
        if (job.status === 'queued' || job.status === 'running') {
          setSetupResult('setup-trial-result', 'busy',
            'Trial ' + job.status + (job.phase ? ' · ' + job.phase : '') + '… (job ' + (job.job_id || jobId) + ')');
          if (Date.now() > deadline) setSetupResult('setup-trial-result', 'fail', 'Trial timed out.');
          else setTimeout(tick, POLL_INTERVAL_MS);
          return;
        }
        if (job.status === 'done') {
          var q = job.result && job.result.overlay ? job.result.overlay.quote : '';
          setSetupResult('setup-trial-result', 'ok',
            'Trial passed — pipeline works end to end.' + (q ? ' “' + String(q).slice(0, 80) + '”' : ''));
          toast('Trial generation passed.', 'success');
          loadActivity();
          loadHistory(true);
          return;
        }
        setSetupResult('setup-trial-result', job.status === 'cancelled' ? 'warn' : 'fail',
          job.status === 'cancelled' ? 'Trial was cancelled.' : ('Trial failed: ' + (job.error || 'unknown error')));
      });
    }
    tick();
  }

  function wireSetup() {
    var b;
    b = $('setup-open'); if (b) b.addEventListener('click', function () { openSetup(); });
    b = $('setup-dismiss'); if (b) b.addEventListener('click', function () {
      try { sessionStorage.setItem(SETUP_DISMISS_KEY, '1'); } catch (e) {}
      renderSetupBanner();
    });
    b = $('rerun-setup'); if (b) b.addEventListener('click', function () { openSetup(0); });
    b = $('setup-close'); if (b) b.addEventListener('click', function () { closeSetup(false); });
    b = $('setup-skip'); if (b) b.addEventListener('click', function () { closeSetup(true); });
    b = $('setup-back'); if (b) b.addEventListener('click', function () { showSetupStep(setupStep - 1); });
    b = $('setup-next'); if (b) b.addEventListener('click', function () {
      if (setupStep === 3) closeSetup(setupPassCount() === SETUP_ORDER.length);
      else showSetupStep(setupStep + 1);
    });
    var modal = $('setup-modal');
    if (modal) modal.addEventListener('click', function (e) { if (e.target === modal) closeSetup(false); });
    Array.prototype.forEach.call(document.querySelectorAll('#setup-rail li'), function (li) {
      li.addEventListener('click', function () { showSetupStep(parseInt(li.dataset.step, 10)); });
    });
    b = $('setup-verify-token'); if (b) b.addEventListener('click', setupVerifyToken);
    b = $('setup-check-model'); if (b) b.addEventListener('click', setupCheckModel);
    b = $('setup-save-model'); if (b) b.addEventListener('click', setupSaveModel);
    b = $('setup-save-ig'); if (b) b.addEventListener('click', setupSaveIG);
    b = $('setup-refresh-checks'); if (b) b.addEventListener('click', function () {
      renderSetupChecks();
      probeSetup().then(renderSetupChecks);
    });
    b = $('setup-trial'); if (b) b.addEventListener('click', setupTrialRun);
    var igNote = $('setup-ig-note');
    if (igNote) {
      var recheck = document.createElement('button');
      recheck.type = 'button';
      recheck.className = 'btn btn-sm btn-ghost';
      recheck.textContent = 'Re-check link';
      recheck.addEventListener('click', setupRecheckIG);
      igNote.appendChild(document.createTextNode(' '));
      igNote.appendChild(recheck);
    }
  }

  // ── Theme (earth light/dark, persisted, system-aware default) ──
  var THEME_KEY = 'codn-theme';
  function applyTheme(t) {
    var theme = t === 'light' ? 'light' : 'dark';
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem(THEME_KEY, theme); } catch (e) {}
    var icon = $('theme-icon'), btn = $('theme-toggle'), meta = $('meta-theme');
    if (icon) icon.textContent = theme === 'light' ? '☀' : '☾';
    if (btn) btn.setAttribute('aria-label', theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode');
    if (meta) meta.setAttribute('content', theme === 'light' ? '#f6f2e9' : '#0b0f0c');
  }
  function wireTheme() {
    applyTheme(document.documentElement.dataset.theme || 'dark');
    var btn = $('theme-toggle');
    if (btn) btn.addEventListener('click', function () {
      applyTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light');
    });
  }

  // ── Init ───────────────────────────────────────────────
  loadSettings();
  var initial = (location.hash || '#dashboard').replace('#', '');
  if (!$(initial)) initial = 'dashboard';
  switchSection(initial, false);
  refreshPipelineStatus();
  setInterval(refreshPipelineStatus, 15000);
  wireSetup();
  probeSetup();
  wireTheme();
  watchReveals(document);
  // Expose for debugging / future job API wiring
  window.codn = { switchSection: switchSection, refresh: refreshPipelineStatus, toast: toast };

})();
