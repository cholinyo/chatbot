// app/static/js/admin_rag_collections.js
(() => {
    async function start() {
      const sel = document.getElementById('collectionSelect');
      const storeSel = document.getElementById('storeSelect');
      if (!sel) { console.warn('[RAG] No encuentro #collectionSelect'); return; }
  
      const url = document.getElementById('rag-endpoints')?.dataset.collections
               || '/admin/rag/collections'; // sin barra final
  
      try {
        const r = await fetch(url, { credentials: 'include', headers: { 'Accept': 'application/json' } });
        const ct = (r.headers.get('content-type') || '').toLowerCase();
        if (!r.ok || !ct.includes('application/json')) {
          console.warn('[RAG] Respuesta no válida:', r.status, ct);
          sel.innerHTML = '<option value="">Error cargando</option>';
          return;
        }
        const j = await r.json();
        const all = j.collections || [];
        const list = storeSel?.value ? all.filter(c => c.store === storeSel.value) : all;
  
        sel.innerHTML = '';
        if (!list.length) {
          sel.innerHTML = '<option value="">No hay colecciones</option>';
        } else {
          sel.innerHTML = list
            .map(c => `<option value="${c.store}:${c.name}">${c.name} (${c.chunks ?? '?' } chunks)</option>`)
            .join('');
        }
        console.log('[RAG] Colecciones cargadas:', list.length);
      } catch (e) {
        console.error('[RAG] Error fetch colecciones:', e);
        sel.innerHTML = '<option value="">Error</option>';
      }
    }
  
    // refrescar cuando cambias FAISS/Chroma
    document.addEventListener('change', (ev) => {
      if ((ev.target instanceof HTMLElement) && ev.target.id === 'storeSelect') start();
    });
  
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', start, { once: true });
    } else {
      start();
    }
  })();
  