{% extends "base.html" %}
{% block title %}Ingesta Web · TFM RAG{% endblock %}
{% block content %}
<div class="container py-4">
  <h1 class="h3 mb-3">Ingesta Web (RAG)</h1>

  {% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
      {% for cat, msg in messages %}
      <div class="alert alert-{{cat}}">{{msg}}</div>
      {% endfor %}
    {% endif %}
  {% endwith %}

  <div class="row g-4">
    <!-- Form fuente -->
    <div class="col-lg-5">
      <div class="card shadow-sm">
        <div class="card-header">Configurar fuente</div>
        <div class="card-body">
          <form method="post" action="{{ url_for('ingesta_web.save') }}">
            <input type="hidden" name="id" id="src_id">

            <div class="mb-2">
              <label class="form-label">Nombre</label>
              <input class="form-control" name="name" id="src_name" placeholder="Portal municipal">
            </div>

            <div class="mb-2">
              <label class="form-label">URL base</label>
              <input class="form-control" name="url" id="src_url" placeholder="https://www.onda.es" required>
            </div>

            <div class="row">
              <div class="col-6 mb-2">
                <label class="form-label">Strategy</label>
                <select class="form-select" name="strategy" id="src_strategy">
                  <option value="sitemap" selected>Sitemap</option>
                  <option value="requests">Requests (BFS)</option>
                  <option value="selenium">Selenium (JS)</option>
                </select>
              </div>
              <div class="col-6 mb-2">
                <label class="form-label">Depth (requests)</label>
                <input class="form-control" type="number" name="depth" id="src_depth" min="0" value="{{cfg_defaults.depth}}">
              </div>
            </div>

            <div class="mb-2">
              <label class="form-label">Allowed domains (coma)</label>
              <input class="form-control" name="allowed_domains" id="src_allowed_domains" placeholder="onda.es,www.onda.es">
            </div>

            <div class="mb-2">
              <label class="form-label">Include (1 por línea)</label>
              <textarea class="form-control" rows="3" name="include" id="src_include"></textarea>
            </div>
            <div class="mb-2">
              <label class="form-label">Exclude (1 por línea)</label>
              <textarea class="form-control" rows="3" name="exclude" id="src_exclude"></textarea>
            </div>

            <div class="row">
              <div class="col-6 mb-2">
                <label class="form-label">Robots policy</label>
                <select class="form-select" name="robots_policy" id="src_robots_policy">
                  <option value="strict" selected>strict</option>
                  <option value="ignore">ignore</option>
                  <option value="list">list</option>
                </select>
              </div>
              <div class="col-6 mb-2">
                <label class="form-label">Ignore robots for (coma)</label>
                <input class="form-control" name="ignore_robots_for" id="src_ignore_robots_for" placeholder="onda.es,www.onda.es">
              </div>
            </div>

            <div class="row">
              <div class="col-4 mb-2">
                <label class="form-label">Rate/host (s)</label>
                <input class="form-control" type="number" step="0.1" name="rate_per_host" id="src_rate_per_host" value="{{cfg_defaults.rate_per_host}}">
              </div>
              <div class="col-4 mb-2">
                <label class="form-label">Timeout (s)</label>
                <input class="form-control" type="number" name="timeout" id="src_timeout" value="{{cfg_defaults.timeout}}">
              </div>
              <div class="col-4 mb-2">
                <label class="form-label">Max pages</label>
                <input class="form-control" type="number" name="max_pages" id="src_max_pages" value="{{cfg_defaults.max_pages}}">
              </div>
            </div>

            <div class="form-check form-switch mb-2">
              <input class="form-check-input" type="checkbox" id="force_https" name="force_https" checked>
              <label class="form-check-label" for="force_https">Force HTTPS</label>
            </div>

            <div class="mb-3">
              <label class="form-label">User-Agent</label>
              <input class="form-control" name="user_agent" id="src_user_agent" value="{{cfg_defaults.user_agent}}">
            </div>

            <div class="border rounded p-2 mb-3">
              <div class="form-text mb-2">Opciones Selenium (solo si Strategy = Selenium)</div>
              <div class="row g-2">
                <div class="col-4">
                  <label class="form-label">Driver</label>
                  <select class="form-select" name="driver" id="src_driver">
                    <option value="chrome" selected>chrome</option>
                    <option value="firefox">firefox</option>
                  </select>
                </div>
                <div class="col-4">
                  <label class="form-label">Window size</label>
                  <input class="form-control" name="window_size" id="src_window_size" value="{{cfg_defaults.window_size}}">
                </div>
                <div class="col-4">
                  <label class="form-label">Render wait (ms)</label>
                  <input class="form-control" type="number" name="render_wait_ms" id="src_render_wait_ms" value="{{cfg_defaults.render_wait_ms}}">
                </div>
                <div class="col-6">
                  <label class="form-label">Wait selector (CSS)</label>
                  <input class="form-control" name="wait_selector" id="src_wait_selector" placeholder="#content, .app">
                </div>
                <div class="col-3 form-check mt-4">
                  <input class="form-check-input" type="checkbox" id="no_headless" name="no_headless">
                  <label class="form-check-label" for="no_headless">Mostrar ventana (no headless)</label>
                </div>
                <div class="col-3 form-check mt-4">
                  <input class="form-check-input" type="checkbox" id="scroll" name="scroll">
                  <label class="form-check-label" for="scroll">Hacer scroll</label>
                </div>
              </div>
              <div class="row g-2 mt-2">
                <div class="col-4">
                  <label class="form-label">Scroll steps</label>
                  <input class="form-control" type="number" name="scroll_steps" id="src_scroll_steps" value="{{cfg_defaults.scroll_steps}}">
                </div>
                <div class="col-4">
                  <label class="form-label">Scroll wait (ms)</label>
                  <input class="form-control" type="number" name="scroll_wait_ms" id="src_scroll_wait_ms" value="{{cfg_defaults.scroll_wait_ms}}">
                </div>
              </div>
            </div>

            <div class="d-flex gap-2">
              <button class="btn btn-primary" type="submit">Guardar fuente</button>
              <button type="button" class="btn btn-outline-secondary" onclick="clearForm()">Limpiar</button>
            </div>
          </form>
        </div>
      </div>
    </div>

    <!-- Lista de fuentes y acciones -->
    <div class="col-lg-7">
      <div class="card shadow-sm mb-4">
        <div class="card-header">Fuentes Web</div>
        <div class="table-responsive">
          <table class="table table-sm mb-0">
            <thead>
              <tr><th>ID</th><th>Nombre</th><th>URL</th><th>Strategy</th><th>Robots</th><th class="text-end">Acciones</th></tr>
            </thead>
            <tbody>
            {% for s in sources %}
              <tr>
                <td>{{s.id}}</td>
                <td class="text-truncate" style="max-width:200px">{{s.name or '-'}}</td>
                <td class="text-truncate" style="max-width:360px">{{s.url}}</td>
                <td>{{(s.config or {}).get('strategy','sitemap')}}</td>
                <td>{{(s.config or {}).get('robots_policy','strict')}}</td>
                <td class="text-end text-nowrap">
                  <button
                    class="btn btn-sm btn-outline-secondary"
                    onclick='selectSource({{ s.id }}, {{ {"id": s.id, "name": s.name, "url": s.url, "config": (s.config or {})} | tojson | safe }})'>
                    Editar
                  </button>
                  <form class="d-inline" method="post" action="{{ url_for('ingesta_web.run', source_id=s.id) }}">
                    <button class="btn btn-sm btn-success">Ejecutar</button>
                  </form>
                  <form class="d-inline" method="post" action="{{ url_for('ingesta_web.delete', source_id=s.id) }}"
                        onsubmit="return confirm('¿Eliminar la fuente «{{s.name or s.url}}»? Esta acción no se puede deshacer.');">
                    <button class="btn btn-sm btn-outline-danger">Eliminar</button>
                  </form>
                </td>
              </tr>
            {% else %}
              <tr><td colspan="6" class="text-center text-muted">No hay fuentes aún. Crea la primera a la izquierda.</td></tr>
            {% endfor %}
            </tbody>
          </table>
        </div>
      </div>

      <div class="card shadow-sm">
        <div class="card-header">Ejecuciones recientes</div>
        <div class="table-responsive">
          <table class="table table-sm mb-0">
            <thead>
              <tr>
                <th>ID</th><th>Source</th><th>Estado</th>
                <th class="text-end">Páginas</th><th class="text-end">Chunks</th><th class="text-end">Bytes</th>
                <th>Preview</th><th>Artefactos</th>
              </tr>
            </thead>
            <tbody>
            {% for r in runs %}
              {% set totals = (r.meta or {}).get('summary_totals', {}) %}
              {% set rd = (r.meta or {}).get('run_dir') %}
              <tr>
                <td>{{r.id}}</td>
                <td>{{r.source_id}}</td>
                <td>
                  <span class="badge text-bg-{{ 'success' if r.status=='done' else ('danger' if r.status=='error' else 'secondary') }}">
                    {{r.status}}
                  </span>
                </td>
                <td class="text-end">{{ totals.pages or 0 }}</td>
                <td class="text-end">{{ totals.chunks or 0 }}</td>
                <td class="text-end">{{ totals.bytes or 0 }}</td>
                <td><a class="btn btn-sm btn-outline-primary" href="{{ url_for('ingesta_web.preview', run_id=r.id) }}">Ver salida</a></td>
                <td class="text-nowrap">
                  {% if rd %}
                    <div class="btn-group">
                      <a class="btn btn-sm btn-outline-primary" href="{{ url_for('ingesta_web.artifact', relpath=rd ~ '/stdout.txt') }}">stdout.txt</a>
                      <a class="btn btn-sm btn-outline-secondary" href="{{ url_for('ingesta_web.artifact', relpath=rd ~ '/fetch_index.json') }}">fetch_index.json</a>
                      <a class="btn btn-sm btn-outline-secondary" href="{{ url_for('ingesta_web.artifact', relpath=rd ~ '/sitemap_index.json') }}">sitemap_index.json</a>
                      <a class="btn btn-sm btn-outline-secondary" href="{{ url_for('ingesta_web.artifact', relpath=rd ~ '/sitemap_pages.json') }}">sitemap_pages.json</a>
                    </div>
                  {% else %}
                    <small class="text-muted">Sin path del run</small>
                  {% endif %}
                </td>
              </tr>
            {% else %}
              <tr><td colspan="8" class="text-center text-muted">Sin ejecuciones.</td></tr>
            {% endfor %}
            </tbody>
          </table>
        </div>

        {% if preview %}
          <div class="card-body">
            <pre style="white-space:pre-wrap">{{preview}}</pre>
          </div>
        {% endif %}
      </div>
    </div>
  </div>
</div>

<script>
function clearForm(){
  document.getElementById('src_id').value = '';
  document.getElementById('src_name').value = '';
  document.getElementById('src_url').value = '';
  document.getElementById('src_strategy').value = 'sitemap';
  document.getElementById('src_depth').value = '{{cfg_defaults.depth}}';
  document.getElementById('src_allowed_domains').value = '';
  document.getElementById('src_include').value = '';
  document.getElementById('src_exclude').value = '';
  document.getElementById('src_robots_policy').value = 'strict';
  document.getElementById('src_ignore_robots_for').value = '';
  document.getElementById('src_rate_per_host').value = '{{cfg_defaults.rate_per_host}}';
  document.getElementById('src_timeout').value = '{{cfg_defaults.timeout}}';
  document.getElementById('src_max_pages').value = '{{cfg_defaults.max_pages}}';
  document.getElementById('force_https').checked = true;
  document.getElementById('src_user_agent').value = '{{cfg_defaults.user_agent}}';
  document.getElementById('src_driver').value = 'chrome';
  document.getElementById('src_window_size').value = '{{cfg_defaults.window_size}}';
  document.getElementById('src_render_wait_ms').value = '{{cfg_defaults.render_wait_ms}}';
  document.getElementById('src_wait_selector').value = '';
  document.getElementById('no_headless').checked = false;
  document.getElementById('scroll').checked = false;
  document.getElementById('src_scroll_steps').value = '{{cfg_defaults.scroll_steps}}';
  document.getElementById('src_scroll_wait_ms').value = '{{cfg_defaults.scroll_wait_ms}}';
}

function selectSource(id, srcJson){
  const cfg = Object.assign({}, {{ cfg_defaults|tojson }}, srcJson.config || {});
  document.getElementById('src_id').value = id;
  document.getElementById('src_name').value = srcJson.name || '';
  document.getElementById('src_url').value = srcJson.url || '';
  document.getElementById('src_strategy').value = cfg.strategy || 'sitemap';
  document.getElementById('src_depth').value = cfg.depth ?? 1;
  document.getElementById('src_allowed_domains').value = (cfg.allowed_domains || []).join(',');
  document.getElementById('src_include').value = (cfg.include || []).join('\\n');
  document.getElementById('src_exclude').value = (cfg.exclude || []).join('\\n');
  document.getElementById('src_robots_policy').value = cfg.robots_policy || 'strict';
  document.getElementById('src_ignore_robots_for').value = (cfg.ignore_robots_for || []).join(',');
  document.getElementById('src_rate_per_host').value = cfg.rate_per_host ?? 1.0;
  document.getElementById('src_timeout').value = cfg.timeout ?? 15;
  document.getElementById('src_max_pages').value = cfg.max_pages ?? 100;
  document.getElementById('force_https').checked = !!cfg.force_https;
  document.getElementById('src_user_agent').value = cfg.user_agent || '';

  // Selenium
  document.getElementById('src_driver').value = cfg.driver || 'chrome';
  document.getElementById('src_window_size').value = cfg.window_size || '1366,900';
  document.getElementById('src_render_wait_ms').value = cfg.render_wait_ms ?? 3000;
  document.getElementById('src_wait_selector').value = cfg.wait_selector || '';
  document.getElementById('no_headless').checked = !!cfg.no_headless;
  document.getElementById('scroll').checked = !!cfg.scroll;
  document.getElementById('src_scroll_steps').value = cfg.scroll_steps ?? 4;
  document.getElementById('src_scroll_wait_ms').value = cfg.scroll_wait_ms ?? 500;
}
</script>
{% endblock %}
