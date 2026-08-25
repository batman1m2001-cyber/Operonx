"""Render a Project IR to a self-contained HTML page.

One file, no network: a page that needs a CDN is useless the moment you open
it on a box without internet, which is where debugging usually happens.

What the view has to get right, taken from what previous attempts got wrong:

* **Every node is drawn.** An orphan is placed, not skipped. A diagram that
  quietly omits things is worse than no diagram.
* **No edge comes from nowhere.** Edges are only drawn between nodes that
  exist; ``START``/``END`` are boundaries and are rendered as such rather
  than invented as nodes.
* **The inspector shows real data.** Each input names where its value comes
  from — the producing op and output, a ``SCRATCH`` key, or a literal —
  because "what feeds this?" is the question the graph is opened to answer.
* **Soft edges say who softened them.** An edge the compiler auto-softened
  reads differently from one the author wrote ``~`` on.
"""

from __future__ import annotations

import html
import json
from typing import Any, Dict, List

from operonx_studio.layout import NODE_H, NODE_W, Layout, layout_graph

__all__ = ["render_project", "render_html"]

_EDGE_STYLE = {
    "condition": ("var(--edge-cond)", "6 4"),
    "lookback": ("var(--edge-back)", "2 5"),
}


def _edge_path(layout: Layout, src: str, dst: str, back: bool) -> str:
    """A cubic from the source's right edge to the target's left edge.

    A return path is bowed under the row instead of cutting back through the
    nodes between, so a loop reads as a loop.
    """
    nodes = layout.by_id
    a, b = nodes.get(src), nodes.get(dst)
    if a is None or b is None:
        return ""
    x1, y1 = a.x + NODE_W, a.y + NODE_H / 2
    x2, y2 = b.x, b.y + NODE_H / 2
    if back:
        drop = max(y1, y2) + NODE_H
        return f"M {x1} {y1} C {x1 + 60} {drop}, {x2 - 60} {drop}, {x2} {y2}"
    span = max(48.0, (x2 - x1) / 2)
    return f"M {x1} {y1} C {x1 + span} {y1}, {x2 - span} {y2}, {x2} {y2}"


def _binding_text(binding: Dict[str, Any]) -> str:
    kind = binding.get("kind")
    if kind == "ref":
        src = binding.get("from") or "?"
        out = binding.get("output") or "?"
        extra = binding.get("transforms") or 0
        tail = f"  ·  {extra} transform(s)" if extra else ""
        return f"{src.rsplit('.', 1)[-1]}.{out}{tail}"
    if kind == "scratch":
        return f"SCRATCH[{binding.get('key')!r}]"
    if kind == "literal":
        return json.dumps(binding.get("value"))[:120]
    if kind == "unset":
        return "—"
    return binding.get("repr", "opaque")[:120]


def _graph_payload(graph: Dict[str, Any]) -> Dict[str, Any]:
    """Everything the page needs for one graph, layout included."""
    placed = layout_graph(graph)
    loops = graph.get("loops") or {}
    nodes = []
    for node in placed.nodes:
        raw = node.meta
        source = raw.get("source") or {}
        anchor = source.get("wired_at") or source.get("defined_at") or {}
        nodes.append(
            {
                "id": node.id,
                "name": node.name,
                "kind": node.kind,
                "x": node.x,
                "y": node.y,
                "bound": raw.get("bound"),
                "resource": raw.get("resource"),
                "channel": raw.get("channel"),
                "start": raw.get("start"),
                "end": raw.get("end"),
                "outputs": raw.get("outputs") or [],
                "nested": bool(raw.get("graph")),
                "loop": loops.get(node.id),
                "source": f"{anchor.get('file')}:{anchor.get('line')}" if anchor else None,
                "inputs": [
                    {
                        "name": i["name"],
                        "kind": (i.get("binding") or {}).get("kind"),
                        "text": _binding_text(i.get("binding") or {}),
                        "required": i.get("required"),
                    }
                    for i in (raw.get("inputs") or [])
                ],
            }
        )
    edges = []
    for e in placed.edges:
        colour, dash = _EDGE_STYLE.get(e.type, ("var(--edge)", ""))
        if e.soft:
            dash = dash or "5 5"
            colour = "var(--edge-soft)"
        if e.back:
            colour = "var(--edge-back)"
        edges.append(
            {
                "src": e.src,
                "dst": e.dst,
                "d": _edge_path(placed, e.src, e.dst, e.back),
                "colour": colour,
                "dash": dash,
                "label": f"{e.type}·{e.origin}"
                if e.origin != "authored" or e.type != "normal"
                else "",
                "origin": e.origin,
                "type": e.type,
            }
        )
    return {
        "name": graph.get("name"),
        "entry": graph.get("entry"),
        "entries": graph.get("entries") or [],
        "exits": graph.get("exits") or [],
        "width": placed.width,
        "height": placed.height,
        "nodes": nodes,
        "edges": edges,
        "rewritten": graph.get("rewritten_from") or {},
    }


def render_project(ir: Dict[str, Any], env_status: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Reduce a Project IR to exactly what the page renders.

    ``env_status`` is machine state supplied by the caller, never taken from
    the IR — see ``envstatus``. Pure without it, so tests need no environment.
    """
    return {
        "project": ir.get("project"),
        "description": ir.get("description") or "",
        "resources": ir.get("resources") or {},
        "dependencies": ir.get("dependencies") or {},
        "env_status": env_status or {},
        "graphs": [_graph_payload(g) for g in (ir.get("graphs") or [])],
    }


def _embed_json(payload: Dict[str, Any]) -> str:
    """Serialise for embedding inside a ``<script>`` block.

    JSON does not escape ``<``, so an op named ``</script>`` would close the
    block and everything after it would be parsed as markup. Escaping the
    three characters that can start a tag keeps the payload inert while
    staying valid JSON — ``\\u003c`` parses back to ``<``.
    """
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render_html(
    ir: Dict[str, Any],
    title: str | None = None,
    env_status: Dict[str, Any] | None = None,
) -> str:
    payload = render_project(ir, env_status)
    name = title or payload["project"] or "operonx project"
    return _TEMPLATE.replace("__TITLE__", html.escape(str(name))).replace(
        "__DATA__", _embed_json(payload)
    )


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ · operonx studio</title>
<style>
:root {
  --bg:#f6f7f9; --panel:#ffffff; --ink:#12151a; --muted:#5b6472; --line:#dfe3e9;
  --node:#ffffff; --node-line:#c8cfd9; --accent:#3b6ef5; --accent-soft:#e8efff;
  --edge:#8b95a5; --edge-soft:#b6a45e; --edge-cond:#8a5cf0; --edge-back:#d2694a;
  --chip:#eef1f5; --start:#2a9d5c; --end:#c2453a;
}
:root:not([data-theme="light"]) { }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg:#12151a; --panel:#181c23; --ink:#e6e9ee; --muted:#98a2b3; --line:#272d37;
    --node:#1d222b; --node-line:#333b47; --accent:#6b95ff; --accent-soft:#1e2940;
    --edge:#6d7787; --edge-soft:#b09a4e; --edge-cond:#a07df5; --edge-back:#e08163;
    --chip:#232935; --start:#3fbf78; --end:#e0685c;
  }
}
:root[data-theme="dark"] {
  --bg:#12151a; --panel:#181c23; --ink:#e6e9ee; --muted:#98a2b3; --line:#272d37;
  --node:#1d222b; --node-line:#333b47; --accent:#6b95ff; --accent-soft:#1e2940;
  --edge:#6d7787; --edge-soft:#b09a4e; --edge-cond:#a07df5; --edge-back:#e08163;
  --chip:#232935; --start:#3fbf78; --end:#e0685c;
}
* { box-sizing:border-box; }
body {
  margin:0; background:var(--bg); color:var(--ink);
  font:14px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
}
header {
  display:flex; align-items:center; gap:16px; flex-wrap:wrap;
  padding:12px 18px; border-bottom:1px solid var(--line); background:var(--panel);
}
h1 { font-size:15px; margin:0; font-weight:640; letter-spacing:-.01em; }
.sub { color:var(--muted); font-size:12.5px; }
select, button {
  font:inherit; color:var(--ink); background:var(--panel);
  border:1px solid var(--line); border-radius:7px; padding:5px 9px; cursor:pointer;
}
main { display:flex; height:calc(100vh - 53px); }
#canvas { flex:1; overflow:auto; position:relative; }
#panel { flex:1; overflow:auto; padding:22px 26px; }
#panel h2 { font-size:14px; margin:22px 0 10px; letter-spacing:-.01em; }
#panel h2:first-child { margin-top:0; }
table { border-collapse:collapse; width:100%; max-width:780px; font-size:12.5px; }
th { text-align:left; font-weight:600; color:var(--muted); font-size:11px;
  text-transform:uppercase; letter-spacing:.06em; padding:6px 10px 6px 0;
  border-bottom:1px solid var(--line); }
td { padding:7px 10px 7px 0; border-bottom:1px solid var(--line);
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:11.5px;
  vertical-align:top; }
.pill { display:inline-block; border-radius:999px; padding:1px 9px; font-size:10.5px;
  font-weight:600; font-family:inherit; }
.ok { background:rgba(42,157,92,.16); color:var(--start); }
.miss { background:rgba(194,69,58,.16); color:var(--end); }
.note { color:var(--muted); font-size:12px; max-width:640px; margin:6px 0 0; }
#tabs { display:flex; gap:2px; }
.tab { border:none; background:transparent; color:var(--muted); padding:5px 11px;
  border-radius:7px; font-size:12.5px; }
.tab.on { background:var(--accent-soft); color:var(--accent); font-weight:600; }
aside {
  width:340px; flex:none; border-left:1px solid var(--line); background:var(--panel);
  overflow:auto; padding:16px;
}
aside h2 { font-size:13px; margin:0 0 2px; letter-spacing:-.01em; }
aside .kind { color:var(--muted); font-size:12px; margin-bottom:14px; }
.section { font-size:11px; text-transform:uppercase; letter-spacing:.07em;
  color:var(--muted); margin:16px 0 7px; font-weight:600; }
.row { display:flex; gap:8px; padding:5px 0; border-top:1px solid var(--line); font-size:12.5px; }
.row .k { color:var(--muted); flex:none; min-width:96px; }
.row .v { word-break:break-word; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:11.5px; }
.chip { display:inline-block; background:var(--chip); border-radius:5px;
  padding:1px 6px; font-size:11px; color:var(--muted); margin-right:5px; }
.empty { color:var(--muted); font-size:12.5px; font-style:italic; }
.edit { margin-top:8px; display:flex; gap:6px; align-items:center; }
.edit input { flex:1; min-width:0; font:inherit; font-size:12px; color:var(--ink);
  background:var(--bg); border:1px solid var(--line); border-radius:6px; padding:4px 7px; }
.edit button, .danger { font-size:11.5px; padding:4px 9px; }
.danger { color:var(--end); border-color:var(--end); }
.diff { margin-top:10px; border:1px solid var(--line); border-radius:8px;
  background:var(--bg); padding:10px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-size:11px; white-space:pre-wrap; overflow-x:auto; max-height:240px; }
.diff .add { color:var(--start); }
.diff .del { color:var(--end); }
.confirm { display:flex; gap:6px; margin-top:9px; }
.err { color:var(--end); font-size:12px; margin-top:8px; }
.node { cursor:pointer; }
.node rect { fill:var(--node); stroke:var(--node-line); stroke-width:1.4; rx:9; }
.node.sel rect { stroke:var(--accent); stroke-width:2.4; fill:var(--accent-soft); }
.node .nm { font-size:13px; font-weight:600; fill:var(--ink); }
.node .kd { font-size:11px; fill:var(--muted); }
.node .bd { font-size:10.5px; fill:var(--muted); }
.pin { font-size:10px; font-weight:700; }
.legend { display:flex; gap:14px; flex-wrap:wrap; font-size:11.5px; color:var(--muted); }
.legend i { display:inline-block; width:22px; height:0; border-top:2px solid var(--edge);
  vertical-align:middle; margin-right:5px; }
.legend .soft { border-top-style:dashed; border-color:var(--edge-soft); }
.legend .cond { border-top-style:dashed; border-color:var(--edge-cond); }
.legend .back { border-color:var(--edge-back); }
.banner { padding:9px 18px; background:var(--accent-soft); border-bottom:1px solid var(--line);
  font-size:12.5px; }
</style>
</head>
<body>
<header>
  <h1 id="proj"></h1>
  <nav id="tabs">
    <button class="tab on" data-tab="graph">graph</button>
    <button class="tab" data-tab="resources">resources</button>
    <button class="tab" data-tab="env">env</button>
    <button class="tab" data-tab="deps">deps</button>
  </nav>
  <select id="pick"></select>
  <span class="sub" id="stats"></span>
  <span style="flex:1"></span>
  <div class="legend">
    <span><i></i>edge</span>
    <span><i class="soft"></i>soft</span>
    <span><i class="cond"></i>condition</span>
    <span><i class="back"></i>loop back</span>
  </div>
  <button id="theme">theme</button>
</header>
<div class="banner" id="banner" hidden></div>
<main>
  <div id="canvas"></div>
  <div id="panel" hidden></div>
  <aside id="side"></aside>
</main>
<script id="ir" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('ir').textContent);
const NODE_W = 210, NODE_H = 76;
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let current = 0, selected = null;

$('#proj').textContent = DATA.project || 'operonx project';
DATA.graphs.forEach((g, i) => {
  const o = document.createElement('option');
  o.value = i; o.textContent = g.name;
  $('#pick').appendChild(o);
});
$('#pick').onchange = e => { current = +e.target.value; selected = null; draw(); };
let tab = 'graph';
document.querySelectorAll('.tab').forEach(b => {
  b.onclick = () => {
    tab = b.dataset.tab;
    document.querySelectorAll('.tab').forEach(x => x.classList.toggle('on', x === b));
    $('#pick').style.display = tab === 'graph' ? '' : 'none';
    $('#side').style.display = tab === 'graph' ? '' : 'none';
    $('#canvas').hidden = tab !== 'graph';
    $('#panel').hidden = tab === 'graph';
    if (tab === 'graph') draw(); else panel();
  };
});

function rows(pairs) {
  return pairs.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v}</td></tr>`).join('');
}

function panel() {
  const res = DATA.resources || {};
  const env = res.env || {required: [], optional: {}};
  const st = DATA.env_status || {};
  const deps = DATA.dependencies || {};
  let html = '';

  if (tab === 'resources') {
    const keys = res.keys || [];
    const groups = {};
    for (const k of keys) {
      const [cat] = k.includes(':') ? k.split(':') : ['other'];
      (groups[cat] = groups[cat] || []).push(k);
    }
    html = `<h2>declared resources</h2>
      <p class="note">Keys an op may reference by name. Values live in the resource
      files and are never read into this page.</p>`;
    html += Object.keys(groups).length
      ? Object.entries(groups).map(([cat, ks]) =>
          `<h2>${esc(cat)}</h2><table><tbody>${
            ks.map(k => `<tr><td>${esc(k)}</td></tr>`).join('')}</tbody></table>`).join('')
      : '<p class="note">This project declares no resources.</p>';
  }

  if (tab === 'env') {
    const required = env.required || [];
    const optional = env.optional || {};
    const cell = name => {
      const s = st[name];
      if (!s) return '<span class="pill miss">unknown</span>';
      if (!s.set) return '<span class="pill miss">missing</span>';
      const where = [s.in_environment && 'environment', s.in_dotenv && '.env']
        .filter(Boolean).join(' + ');
      return `<span class="pill ok">set</span> <span class="chip">${esc(where)}</span>`;
    };
    html = `<h2>environment contract</h2>
      <p class="note">Derived from <code>${'${VAR}'}</code> references in the resource
      files — there is no second list to keep in step. Presence is checked on this
      machine; <strong>no value is ever read or shown</strong>.</p>`;
    html += required.length
      ? `<h2>required</h2><table><thead><tr><th>variable</th><th>status</th></tr></thead>
         <tbody>${rows(required.map(n => [n, cell(n)]))}</tbody></table>`
      : '<p class="note">No required variables.</p>';
    const opt = Object.entries(optional);
    if (opt.length) {
      html += `<h2>optional</h2><table><thead><tr><th>variable</th><th>default</th><th>status</th></tr></thead>
        <tbody>${opt.map(([n, d]) =>
          `<tr><td>${esc(n)}</td><td>${esc(d)}</td><td>${cell(n)}</td></tr>`).join('')}</tbody></table>`;
    }
    const missing = required.filter(n => !(st[n] || {}).set);
    if (missing.length) {
      html += `<p class="note"><strong>${missing.length} required variable(s) unset here.</strong>
        This project will not run on this machine until they are provided.</p>`;
    }
  }

  if (tab === 'deps') {
    if (!deps.declared) {
      html = '<h2>dependencies</h2><p class="note">No <code>pyproject.toml</code> found.</p>';
    } else {
      html = `<h2>dependencies</h2>
        <p class="note">Declared in <code>pyproject.toml</code>. These are declarations,
        not what happens to be installed.</p>
        <table><tbody>${rows([
          ['name', esc(deps.name || '—')],
          ['requires-python', esc(deps.requires_python || '—')],
        ])}</tbody></table>
        <h2>requires</h2><table><tbody>${
          (deps.dependencies || []).map(d => `<tr><td>${esc(d)}</td></tr>`).join('')
          || '<tr><td>—</td></tr>'}</tbody></table>`;
      const extras = Object.entries(deps.extras || {});
      if (extras.length) {
        html += `<h2>extras</h2>${extras.map(([k, v]) =>
          `<h2>[${esc(k)}]</h2><table><tbody>${
            v.map(d => `<tr><td>${esc(d)}</td></tr>`).join('')}</tbody></table>`).join('')}`;
      }
    }
  }
  $('#panel').innerHTML = html;
}

$('#theme').onclick = () => {
  const dark = document.documentElement.getAttribute('data-theme') === 'dark';
  document.documentElement.setAttribute('data-theme', dark ? 'light' : 'dark');
};

function draw() {
  const g = DATA.graphs[current];
  $('#stats').textContent = `${g.nodes.length} nodes · ${g.edges.length} edges · ${g.entry || ''}`;
  const rw = Object.keys(g.rewritten || {});
  const banner = $('#banner');
  if (rw.length) {
    banner.hidden = false;
    banner.textContent = `${rw.length} cycle(s) rewritten to hidden loop(s): ${rw.join(', ')}. ` +
      `Back-edges are deleted from the built graph — shown here from the rewrite record.`;
  } else banner.hidden = true;

  const parts = [`<svg width="${g.width}" height="${g.height}" xmlns="http://www.w3.org/2000/svg">`,
    `<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
      orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="var(--edge)"/></marker></defs>`];

  for (const e of g.edges) {
    if (!e.d) continue;
    parts.push(`<path d="${e.d}" fill="none" stroke="${e.colour}" stroke-width="1.7"
      ${e.dash ? `stroke-dasharray="${e.dash}"` : ''} marker-end="url(#a)"/>`);
  }
  for (const n of g.nodes) {
    const sel = n.id === selected ? ' sel' : '';
    parts.push(`<g class="node${sel}" data-id="${esc(n.id)}" transform="translate(${n.x},${n.y})">
      <rect width="${NODE_W}" height="${NODE_H}"/>
      <text class="nm" x="14" y="26">${esc(n.name)}</text>
      <text class="kd" x="14" y="45">${esc(n.kind)}${n.resource ? ' · ' + esc(n.resource) : ''}${n.channel ? ' · #' + esc(n.channel) : ''}</text>
      <text class="bd" x="14" y="63">${esc(n.bound || '')}${n.nested ? ' · subgraph' : ''}${n.loop ? ' · loop(' + esc(n.loop.mode) + ')' : ''}</text>
      ${n.start ? `<text class="pin" x="${NODE_W - 12}" y="20" text-anchor="end" fill="var(--start)">START</text>` : ''}
      ${n.end ? `<text class="pin" x="${NODE_W - 12}" y="${NODE_H - 10}" text-anchor="end" fill="var(--end)">END</text>` : ''}
    </g>`);
  }
  parts.push('</svg>');
  $('#canvas').innerHTML = parts.join('');
  $('#canvas').querySelectorAll('.node').forEach(el => {
    el.onclick = () => { selected = el.dataset.id; draw(); };
  });
  inspect();
}

function inspect() {
  const g = DATA.graphs[current];
  const n = g.nodes.find(x => x.id === selected);
  if (!n) {
    const env = (DATA.resources && DATA.resources.env) || {required: [], optional: {}};
    const keys = (DATA.resources && DATA.resources.keys) || [];
    $('#side').innerHTML = `<h2>${esc(g.name)}</h2>
      <div class="kind">${esc(g.entry || '')}</div>
      <div class="section">boundary</div>
      <div class="row"><span class="k">entries</span><span class="v">${g.entries.join(', ') || '—'}</span></div>
      <div class="row"><span class="k">exits</span><span class="v">${g.exits.join(', ') || '—'}</span></div>
      <div class="section">resources</div>
      ${keys.length ? keys.map(k => `<div class="row"><span class="v">${esc(k)}</span></div>`).join('')
                    : '<div class="empty">none declared</div>'}
      <div class="section">env required</div>
      ${env.required.length ? env.required.map(k => `<div class="row"><span class="v">${esc(k)}</span></div>`).join('')
                            : '<div class="empty">none</div>'}
      ${Object.keys(env.optional || {}).length ? '<div class="section">env optional</div>' +
        Object.entries(env.optional).map(([k, v]) =>
          `<div class="row"><span class="k">${esc(k)}</span><span class="v">${esc(v)}</span></div>`).join('') : ''}
      <div class="section">select a node</div>
      <div class="empty">Click any node to see what feeds it.</div>`;
    return;
  }
  const ins = n.inputs.length ? n.inputs.map(i =>
    `<div class="row"><span class="k">${esc(i.name)}${i.required ? '' : '?'}</span>
     <span class="v"><span class="chip">${esc(i.kind)}</span>${esc(i.text)}</span></div>`).join('')
    : '<div class="empty">no declared inputs</div>';
  const outs = n.outputs.length ? n.outputs.map(o =>
    `<div class="row"><span class="v">${esc(o)}</span></div>`).join('')
    : '<div class="empty">none</div>';
  $('#side').innerHTML = `<h2>${esc(n.name)}</h2>
    <div class="kind">${esc(n.kind)} · ${esc(n.bound || '')}</div>
    <div class="row"><span class="k">id</span><span class="v">${esc(n.id)}</span></div>
    ${n.source ? `<div class="row"><span class="k">source</span><span class="v">${esc(n.source)}</span></div>` : ''}
    ${n.resource ? `<div class="row"><span class="k">resource</span><span class="v">${esc(n.resource)}</span></div>` : ''}
    ${n.channel ? `<div class="row"><span class="k">channel</span><span class="v">${esc(n.channel)}</span></div>` : ''}
    ${n.loop ? `<div class="row"><span class="k">loop</span><span class="v">${esc(n.loop.mode)}${
        n.loop.until === null ? ' · exit lives in an if_() inside the body' : ' · until ' + esc(n.loop.until)}</span></div>` : ''}
    <div class="section">inputs — where each value comes from</div>${ins}
    <div class="section">outputs</div>${outs}
    ${editControls(g, n)}`;
  wireEditing(g, n);
}

// Edit affordances exist only when a daemon is serving the page. A file
// someone shared must not show buttons that call an API that is not there.
function editable() { return window.__OPERONX_EDITABLE__ === true; }

function editControls(g, n) {
  if (!editable()) return '';
  const resourceRow = typeof n.resource === 'string'
    ? `<div class="edit"><input id="ed-res" value="${esc(n.resource)}" aria-label="resource">
       <button data-act="set_resource">set resource</button></div>`
    : '';
  return `<div class="section">edit</div>
    <div class="edit"><input id="ed-name" value="${esc(n.name)}" aria-label="new name">
      <button data-act="rename">rename</button></div>
    ${resourceRow}
    <div class="edit"><button class="danger" data-act="delete">delete node</button></div>
    <div id="ed-out"></div>`;
}

function renderDiff(text) {
  return text.split('\n').map(line => {
    const cls = line.startsWith('+') && !line.startsWith('+++') ? 'add'
              : line.startsWith('-') && !line.startsWith('---') ? 'del' : '';
    return cls ? `<span class="${cls}">${esc(line)}</span>` : esc(line);
  }).join('\n');
}

function wireEditing(g, n) {
  if (!editable()) return;
  const out = $('#ed-out');
  const args = act => {
    if (act === 'rename') return {old: n.name, new: ($('#ed-name') || {}).value};
    if (act === 'set_resource') return {op_name: n.name, resource: ($('#ed-res') || {}).value};
    return {name: n.name};
  };
  const post = (act, apply) => fetch('api/edit', {
      method: 'POST', headers: {'content-type': 'application/json'},
      body: JSON.stringify(Object.assign({graph: g.name, action: act, dry_run: !apply}, args(act)))
    }).then(r => r.json().then(d => ({ok: r.ok, d})));

  document.querySelectorAll('#side .edit button').forEach(btn => {
    btn.onclick = () => {
      const act = btn.dataset.act;
      out.innerHTML = '<div class="empty">checking…</div>';
      post(act, false).then(({ok, d}) => {
        if (!ok) { out.innerHTML = `<div class="err">${esc(d.error)}</div>`; return; }
        if (!d.changed) { out.innerHTML = '<div class="empty">no change</div>'; return; }
        out.innerHTML = `<div class="diff">${renderDiff(d.diff)}</div>
          <div class="confirm"><button id="ed-go">apply to ${esc(d.file)}</button>
          <button id="ed-no">cancel</button></div>`;
        $('#ed-no').onclick = () => { out.innerHTML = ''; };
        $('#ed-go').onclick = () => {
          out.innerHTML = '<div class="empty">applying…</div>';
          post(act, true).then(({ok, d}) => {
            // On success the daemon's watcher sees the write and the page
            // reloads itself; no manual refresh to keep in step.
            out.innerHTML = ok
              ? '<div class="empty">applied — reloading</div>'
              : `<div class="err">${esc(d.error)}</div>`;
          });
        };
      });
    };
  });
}
draw();
</script>
</body>
</html>
"""
