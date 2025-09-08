// app/static/js/admin_rag_query.js
(() => {
    function addMsg(role, html) {
      const log = document.getElementById('chatMessages');
      const div = document.createElement('div');
      div.className = 'message ' + role;
      div.innerHTML = `
        <div class="message-header">${role === 'user' ? 'Tú' : 'RAG'}</div>
        <div class="message-content">${html}</div>`;
      log.appendChild(div);
      log.scrollTop = log.scrollHeight;
    }
  
    function renderSources(results, elapsed, meta) {
      const items = (results || []).map((r, i) => `
        <div class="source-item">
          <div><span class="source-score">[${i + 1}] sim:</span> ${Number(r.similarity ?? r.score_raw ?? 0).toFixed(3)}</div>
          <div class="source-title">${r.document_title ?? '(sin título)'}</div>
          <div class="source-text">${(r.text || '').slice(0, 500)}${(r.text || '').length > 500 ? '…' : ''}</div>
          <div class="metrics">doc: ${r.document_path ?? '-'} · chunk ${r.chunk_index ?? '-'}</div>
        </div>
      `).join('');
      addMsg('assistant', `
        <b>Fuentes</b> (${results?.length ?? 0})
        <div class="sources">${items}</div>
        <div class="metrics">Tiempo: ${elapsed} ms · Modelo: ${meta?.model ?? '-'} · chunks: ${meta?.n_chunks ?? '-'}</div>
      `);
    }
  
    async function onSend() {
      const queryInput = document.getElementById('queryInput');
      const storeSel = document.getElementById('storeSelect');
      const collSel = document.getElementById('collectionSelect');
      const kSel = document.getElementById('kSelect');
      const btn = document.getElementById('sendBtn');
  
      const q = (queryInput.value || '').trim();
      if (!q) return;
  
      // Determina store y collection (valor "store:name")
      let store = storeSel?.value || 'chroma';
      let collection = '';
      const sel = collSel?.value || '';
      if (sel.includes(':')) { const [s, c] = sel.split(':'); store = s; collection = c; } else { collection = sel; }
  
      addMsg('user', q);
      btn.disabled = true; btn.textContent = 'Consultando…';
  
      try {
        const url = `${document.getElementById('rag-endpoints')?.dataset.query || '/admin/rag/query'}?store=${encodeURIComponent(store)}&collection=${encodeURIComponent(collection)}`;
        const resp = await fetch(url, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: q, k: parseInt(kSel?.value || '5', 10) })
        });
  
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
          addMsg('assistant', `<div class="error">Error: ${data.error || `HTTP ${resp.status}`}</div>`);
          return;
        }
        addMsg('assistant', `Consulta procesada. Resultados: <b>${data.total_results}</b>`);
        renderSources(data.results, data.elapsed_ms, data.model_info);
  
        // Info colección
        const info = document.getElementById('collectionInfo');
        const details = document.getElementById('collectionDetails');
        if (data.model_info && info && details) {
          info.style.display = '';
          details.innerText = `Modelo: ${data.model_info.model} · dim: ${data.model_info.dim ?? '-'} · chunks: ${data.model_info.n_chunks ?? '-'} · store: ${data.model_info.store}`;
        }
      } catch (e) {
        addMsg('assistant', `<div class="error">Error de red: ${e}</div>`);
      } finally {
        btn.disabled = false; btn.textContent = 'Enviar';
        queryInput.value = '';
      }
    }
  
    function onClear() {
      const log = document.getElementById('chatMessages');
      if (log) log.innerHTML = '';
    }
  
    function init() {
      document.getElementById('sendBtn')?.addEventListener('click', onSend);
      document.getElementById('clearChat')?.addEventListener('click', onClear);
      // Enviar con Ctrl+Enter dentro del textarea
      document.getElementById('queryInput')?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); onSend(); }
      });
    }
  
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
      init();
    }
  })();
  