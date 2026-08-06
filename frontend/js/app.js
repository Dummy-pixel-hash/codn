// codn Dashboard — Main Application Logic

(function () {
  'use strict';

  // ── State ────────────────────────────────────────────────
  const state = {
    currentSection: 'dashboard',
    pipelineStatus: {
      llama: 'online',
      comfy: 'idle',
      tunnel: 'online'
    },
    generation: {
      inProgress: false,
      currentPhase: 0,
      phases: ['art-prompt', 'art-gen', 'text-overlay', 'upload']
    }
  };

  // ── DOM References ───────────────────────────────────────
  const dom = {
    navLinks: document.querySelectorAll('.nav-link'),
    sections: document.querySelectorAll('.section'),
    generateBtn: document.getElementById('generate-btn'),
    cancelGen: document.getElementById('cancel-gen'),
    progressTracker: document.getElementById('progress-tracker'),
    phaseSteps: document.querySelectorAll('.phase-step'),
    quoteSearch: document.getElementById('quote-search'),
    categoryFilter: document.getElementById('category-filter'),
    quotesGrid: document.getElementById('quotes-grid'),
    statusFilter: document.getElementById('status-filter'),
    historyGrid: document.getElementById('history-grid'),
    addQuoteBtn: document.getElementById('add-quote-btn'),
    modal: document.getElementById('add-quote-modal'),
    closeModal: document.getElementById('close-modal'),
    cancelAddQuote: document.getElementById('cancel-add-quote'),
    saveAddQuote: document.getElementById('save-add-quote'),
    tunnelUrl: document.getElementById('tunnel-url'),
    themeInput: document.getElementById('theme-input'),
    uploadToggle: document.getElementById('upload-toggle')
  };

  // ── Navigation ───────────────────────────────────────────
  function switchSection(sectionId) {
    state.currentSection = sectionId;

    // Update nav links
    dom.navLinks.forEach(link => {
      const isActive = link.dataset.section === sectionId;
      link.classList.toggle('active', isActive);
    });

    // Update sections
    document.querySelectorAll('.section').forEach(section => {
      section.classList.toggle('active', section.id === sectionId);
    });

    // Load data when navigating to sections that need it
    if (sectionId === 'quotes') loadQuotes();
    if (sectionId === 'history') loadHistory();
  }

  dom.navLinks.forEach(link => {
    link.addEventListener('click', () => {
      switchSection(link.dataset.section);
    });
  });

  // ── Pipeline Status Monitoring ───────────────────────────
  function updateStatusIndicator(type, status) {
    const indicator = document.querySelector(`[data-status="${type}"]`);
    if (!indicator) return;

    indicator.className = `status-indicator ${status}`;
    state.pipelineStatus[type] = status;
  }

  async function refreshPipelineStatus() {
    try {
      const resp = await fetch('/config');
      if (!resp.ok) return;
      const data = await resp.json();

      // Update llama indicator
      updateStatusIndicator('llama', data.llama_running ? 'online' : 'error');

      // Update comfy indicator
      const comfyStatus = data.comfy_running ? 'processing' : 'idle';
      updateStatusIndicator('comfy', comfyStatus);

      // Update tunnel URL dynamically (ngrok live tunnel or static domain)
      if (dom.tunnelUrl) {
        try {
          const tunnelsResp = await fetch('http://127.0.0.1:4040/api/tunnels', { signal: AbortSignal.timeout(3000) });
          if (tunnelsResp.ok) {
            const tunnels = await tunnelsResp.json();
            const tunnel = tunnels.tunnels?.find(t => t.public_url?.startsWith('https://'));
            if (tunnel) {
              dom.tunnelUrl.textContent = tunnel.public_url;
            }
          }
        } catch {
          // Tunnel API unavailable — keep placeholder or last known value
        }
      }

      // Update connection status text
      if (data.instagram_account) {
        document.querySelector('.status-text').textContent = 'Connected';
      }

      // Update prompts counter in the dashboard
      const counterEl = document.getElementById('prompts-counter');
      if (counterEl && data.prompts_generated != null) {
        counterEl.textContent = data.prompts_generated;
      }
    } catch (err) {
      console.error('Failed to refresh pipeline status:', err);
    }
  }

  // Poll every 5 seconds
  setInterval(refreshPipelineStatus, 5000);

  // Initial load
  refreshPipelineStatus();

  // ── Generation Control ───────────────────────────────────
  function resetPhaseSteps() {
    dom.phaseSteps.forEach(step => {
      step.className = 'phase-step';
      const statusEl = step.querySelector('.phase-status');
      if (statusEl) statusEl.textContent = 'waiting';
    });
  }

  function updatePhaseStep(index, status) {
    const step = dom.phaseSteps[index];
    if (!step) return;

    const statusEl = step.querySelector('.phase-status');

    // Reset all classes first
    step.className = 'phase-step';

    if (status === 'active') {
      step.classList.add('active');
      statusEl.textContent = 'running...';
    } else if (status === 'completed') {
      step.classList.add('completed');
      statusEl.textContent = '✓ Done';
    } else if (status === 'failed') {
      step.classList.add('failed');
      statusEl.textContent = '✗ Failed';
    } else if (status === 'waiting') {
      step.classList.add('waiting');
      statusEl.textContent = 'waiting';
    }
  }

  async function startGeneration() {
    if (state.generation.inProgress) return;

    const category = document.getElementById('theme-select').value;
    const theme = dom.themeInput ? dom.themeInput.value.trim() : '';

    state.generation.inProgress = true;
    state.generation.currentPhase = 0;

    // AbortController for cancel button
    const controller = new AbortController();
    state.generation.abortController = controller;

    // Show progress tracker
    dom.progressTracker.classList.remove('hidden');
    dom.generateBtn.disabled = true;
    dom.generateBtn.querySelector('.btn-icon').style.display = 'none';

    // Reset phase steps
    resetPhaseSteps();

    const body = {
      category: category || undefined,
      upload: dom.uploadToggle ? dom.uploadToggle.checked : true
    };
    if (theme) {
      body.theme = theme;
    }

    try {
      // Phase 1: Art prompt (llama-server)
      updatePhaseStep(0, 'active');
      state.generation.currentPhase = 0;
      const resp = await fetch('/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal
      });

      // Handle non-200 responses gracefully (e.g. 500 from llama/ComfyUI failure)
      let data;
      try {
        data = await resp.json();
      } catch {
        data = { detail: 'Unexpected server response' };
      }

      if (!resp.ok) {
        const errorMsg = data.detail || 'Server error';
        updatePhaseStep(0, 'failed');
        const statusEl = dom.phaseSteps[0].querySelector('.phase-status');
        if (statusEl) statusEl.textContent = errorMsg.substring(0, 40);
        finishGeneration(false);
        return;
      }

      // All phases ran server-side in one call — update them in quick sequence
      updatePhaseStep(0, 'completed');
      await new Promise(r => setTimeout(r, 150));
      updatePhaseStep(1, 'active');
      await new Promise(r => setTimeout(r, 150));
      updatePhaseStep(1, 'completed');
      updatePhaseStep(2, 'active');
      await new Promise(r => setTimeout(r, 150));
      updatePhaseStep(2, 'completed');

      // Phase 4: Instagram upload — check the actual field the backend uses
      updatePhaseStep(3, 'active');
      if (data.upload_result) {
        updatePhaseStep(3, 'completed');
        finishGeneration(true);
      } else {
        updatePhaseStep(3, 'failed');
        finishGeneration(false);
      }
    } catch (err) {
      // AbortError from cancel button — don't spam the console
      if (err.name === 'AbortError') return;

      console.error('Generation failed:', err);
      for (let i = 0; i < 4; i++) {
        updatePhaseStep(i, i === 0 ? 'failed' : 'waiting');
      }
      finishGeneration(false);
    }
  }

  function cancelGeneration() {
    // Abort the in-flight fetch so the server stops processing
    if (state.generation.abortController) {
      state.generation.abortController.abort();
    }

    state.generation.inProgress = false;

    // Mark current phase as failed
    const currentPhaseIndex = Math.min(state.generation.currentPhase, dom.phaseSteps.length - 1);
    updatePhaseStep(currentPhaseIndex, 'failed');

    finishGeneration(false);
  }

  function finishGeneration(success) {
    state.generation.inProgress = false;
    dom.generateBtn.disabled = false;
    dom.generateBtn.querySelector('.btn-icon').style.display = '';

    // Hide progress after delay
    setTimeout(() => {
      dom.progressTracker.classList.add('hidden');
      resetPhaseSteps();
    }, 3000);
  }

  dom.generateBtn.addEventListener('click', startGeneration);
  dom.cancelGen.addEventListener('click', cancelGeneration);

  // ── Quote Search & Filter ────────────────────────────────
  let cachedQuotes = [];

  async function loadQuotes() {
    try {
      const resp = await fetch('/quotes');
      if (!resp.ok) return;
      const data = await resp.json();
      cachedQuotes = data.quotes || [];
      renderQuotes(cachedQuotes);
    } catch (err) {
      console.error('Failed to load quotes:', err);
    }
  }

  function renderQuotes(quotes) {
    dom.quotesGrid.innerHTML = '';
    if (!quotes || quotes.length === 0) {
      dom.quotesGrid.innerHTML = '<p class="empty-state">No quotes generated yet. Start a generation to build your library.</p>';
      return;
    }
    quotes.forEach(q => {
      const card = document.createElement('div');
      card.className = 'quote-card';
      card.innerHTML = `
        <div class="quote-preview">
          <div class="quote-text">"${q.quote_text || ''}" — ${q.author || ''}</div>
        </div>
        <div class="quote-meta">
          <span class="quote-category ${q.category || ''}">${q.category || 'general'}</span>
          <span class="quote-source">${q.author || 'Unknown'}</span>
        </div>
      `;
      dom.quotesGrid.appendChild(card);
    });
  }

  function filterQuotes() {
    const searchTerm = dom.quoteSearch.value.toLowerCase();
    const category = dom.categoryFilter.value;

    const cards = dom.quotesGrid.querySelectorAll('.quote-card');

    cards.forEach(card => {
      const text = card.querySelector('.quote-text').textContent.toLowerCase();
      const quoteCategory = card.querySelector('.quote-category')?.textContent.toLowerCase() || '';

      const matchesSearch = !searchTerm || text.includes(searchTerm);
      const matchesCategory = !category || quoteCategory === category.toLowerCase();

      card.style.display = matchesSearch && matchesCategory ? '' : 'none';
    });
  }

  dom.quoteSearch.addEventListener('input', filterQuotes);
  dom.categoryFilter.addEventListener('change', filterQuotes);

  // ── History Load & Render ────────────────────────────────
  async function loadHistory() {
    try {
      const resp = await fetch('/history');
      if (!resp.ok) return;
      const data = await resp.json();
      const generations = data.generations || [];
      renderHistory(generations);
    } catch (err) {
      console.error('Failed to load history:', err);
    }
  }

  function renderHistory(generations) {
    dom.historyGrid.innerHTML = '';
    if (!generations || generations.length === 0) {
      dom.historyGrid.innerHTML = '<p class="empty-state">No generation history yet. Start a generation to see results here.</p>';
      return;
    }
    generations.forEach(g => {
      const card = document.createElement('div');
      const statusClass = g.status === 'success' || g.status === 'upload_failed' ? 'success' : 'error';
      const badgeText = g.status === 'success' ? '✓ Success' : g.status === 'upload_failed' ? '✗ Upload failed' : '✗ Failed';

      card.className = `history-card ${statusClass}`;

      const thumbContent = g.image_filename
        ? `<img src="/media/${g.image_filename}" alt="Generated quote art" onerror="this.style.display='none'">`
        : '';

      card.innerHTML = `
        <div class="history-thumb">${thumbContent}</div>
        <div class="history-info">
          <span class="history-badge ${statusClass}">${badgeText}</span>
          <span class="history-date">${g.created_at || ''}</span>
          <span class="history-quote">${g.quote_text ? '"' + g.quote_text + '"' : '—'} — ${g.category || 'general'}</span>
        </div>
        <div class="history-actions">
          ${g.image_filename ? `<a href="/media/${g.image_filename}" target="_blank" class="btn btn-sm btn-ghost">View</a>` : ''}
        </div>
      `;
      dom.historyGrid.appendChild(card);
    });
  }

  // ── Add Quote Modal ──────────────────────────────────────
  function openModal() {
    dom.modal.classList.remove('hidden');
  }

  function closeModal() {
    dom.modal.classList.add('hidden');
  }

  dom.addQuoteBtn.addEventListener('click', openModal);
  dom.closeModal.addEventListener('click', closeModal);
  dom.cancelAddQuote.addEventListener('click', closeModal);

  // Close modal on outside click
  dom.modal.addEventListener('click', (e) => {
    if (e.target === dom.modal) closeModal();
  });

  dom.saveAddQuote.addEventListener('click', () => {
    const text = document.getElementById('new-quote-text').value.trim();
    const source = document.getElementById('new-quote-source').value.trim();
    const category = document.getElementById('new-quote-category').value;

    if (!text || !source) {
      alert('Please fill in all fields');
      return;
    }

    // Create new quote card
    const card = document.createElement('div');
    card.className = 'quote-card';
    card.innerHTML = `
      <div class="quote-preview">
        <div class="quote-text">"${text}" — ${source}</div>
      </div>
      <div class="quote-meta">
        <span class="quote-category ${category}">${category}</span>
        <span class="quote-source">${source}</span>
      </div>
    `;

    dom.quotesGrid.insertBefore(card, dom.quotesGrid.firstChild);

    // Reset form and close modal
    document.getElementById('new-quote-text').value = '';
    document.getElementById('new-quote-source').value = '';
    closeModal();
  });

  // ── Settings Save/Reset ──────────────────────────────────
  const defaultSettings = {
    'llama-port': '8001',
    'comfy-port': '8189',
    'model-path': '/home/Darsh/models/Bonsai-27B-Q1_0.gguf',
    'tunnel-engine': 'ngrok',
    'ngrok-token': '2a8f3b1c4d5e6f7g8h9i0j',
    'ig-access-token': 'IGQWR1a2b3c4d5e6f7g8h9i0j',
    'ig-page-id': '17841405793180680',
    'ig-user-id': '17841405793180680'
  };

  document.querySelector('.settings-actions .btn-primary').addEventListener('click', () => {
    // Collect all settings
    const settings = {};
    Object.keys(defaultSettings).forEach(id => {
      const el = document.getElementById(id);
      if (el) settings[id] = el.value;
    });

    // Save to localStorage (dashboard settings are local only)
    localStorage.setItem('codn-settings', JSON.stringify(settings));
    alert('Settings saved locally.');
  });

  document.querySelector('.settings-actions .btn-ghost').addEventListener('click', () => {
    Object.keys(defaultSettings).forEach(id => {
      const el = document.getElementById(id);
      if (el) el.value = defaultSettings[id];
    });
  });

  // ── Initialize ───────────────────────────────────────────
  // Load saved settings on startup
  const savedSettings = localStorage.getItem('codn-settings');
  if (savedSettings) {
    try {
      const settings = JSON.parse(savedSettings);
      Object.keys(settings).forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = settings[id];
      });
    } catch (e) {
      // Ignore parse errors
    }
  }

  // Start with dashboard section active
  switchSection('dashboard');

})();
