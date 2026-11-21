"""Local visual demo: one page over an in-memory Guard with preview semantics (nothing is delivered)."""
import json
import secrets
from datetime import date
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

from .guard import Guard
from .model import Batch

DAY = date(2026, 1, 4)
MAX_DRAFT = 2000

PAGE = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ISDE · demo</title>
<style nonce="{nonce}">
:root{{--bg:#fff;--fg:#1a1a1a;--mute:#555;--line:#ccc;--card:#f6f6f4;--ok:#1d6b35;--warn:#8a5300;--bad:#a1221b;--bar:#2b5ea7}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151515;--fg:#eee;--mute:#bbb;--line:#444;--card:#202020;--ok:#6fd08c;--warn:#f0b35a;--bad:#ff8a80;--bar:#7fb0ff}}}}
body{{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:16px/1.45 system-ui,sans-serif}}
main{{max-width:1100px;margin:auto}} h1{{font-size:1.4rem;margin:0 0 4px}} h2{{font-size:1.05rem;margin:0 0 8px}} h3{{font-size:.95rem;margin:8px 0 4px}}
p.lead{{color:var(--mute);margin:0 0 16px}}
form{{display:flex;flex-wrap:wrap;gap:12px;align-items:end;margin-bottom:16px}}
label{{display:flex;flex-direction:column;gap:4px;font-weight:600}} textarea{{width:100%;min-height:4.5em}}
select,textarea,button{{font:inherit;color:inherit;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:6px 8px}}
button{{cursor:pointer;font-weight:600}} :focus-visible{{outline:3px solid var(--bar);outline-offset:2px}}
.wide{{flex:1 1 100%}} .grid{{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}}
section{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px}}
.tag{{font-size:.8rem;color:var(--mute);text-transform:uppercase;letter-spacing:.04em}}
.allow{{color:var(--ok)}} .repair{{color:var(--warn)}} .review{{color:var(--bad)}} .decision{{font-size:1.3rem;font-weight:700}}
.bar{{height:10px;background:var(--line);border-radius:5px;overflow:hidden;margin:2px 0 6px}} .bar span{{display:block;height:100%;background:var(--bar)}}
.blocked .bar span{{background:var(--bad)}} ul{{margin:0;padding-left:1.2em}} blockquote{{margin:0;font-size:1.1rem}}
</style></head><body><main>
<h1>ISDE · ¿qué puede deducir el destinatario?</h1>
<p class="lead">Caso ficticio en memoria al {day}. Vista previa: no se entrega nada ni se guarda historial.</p>
<form id="f">
<label>Destinatario<select id="user">{users}</select></label>
<label>Tarea<select id="task">{tasks}</select></label>
<label class="wide">Borrador del agente (vacío = respuesta del catálogo)<textarea id="draft" maxlength="{max_draft}"></textarea></label>
<button type="submit">Evaluar</button>
</form>
<p id="error" class="review" role="alert"></p>
<div class="grid" aria-live="polite">
<section><span class="tag">Operador · traza privada</span><h2>Borrador evaluado</h2><blockquote id="o-draft"></blockquote></section>
<section><span class="tag">Operador · traza privada</span><h2>Probabilidad de deducir la conclusión protegida</h2><div id="o-bars"></div></section>
<section><span class="tag">Operador · traza privada</span><h2>Decisión</h2><p id="o-action" class="decision"></p></section>
<section><span class="tag">Destinatario · solo ve su bandeja</span><h2>Texto que llegaría a la bandeja</h2><blockquote id="r-text"></blockquote></section>
<section><span class="tag">Lo que se cree que sabe el destinatario (sin grafo interno)</span><h2>Contexto del observador</h2><div id="o-ctx"></div></section>
</div></main>
<script nonce="{nonce}">
const $ = id => document.getElementById(id);
const pct = x => (100 * x).toFixed(1) + " %";
const NAMES = {{allow: "Permitir", repair: "Reparar", review: "Revisión humana"}};
const REASONS = {{unmapped_draft: "El borrador no está en el catálogo", incomplete_task: "No completa la tarea",
  unverified_content: "Contenido sin respaldo interno"}};
function el(tag, text, cls) {{ const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; }}
function bar(x) {{ const b = el("div", undefined, "bar"), s = el("span"); s.style.width = pct(x).replace(" ", ""); b.setAttribute("aria-hidden", "true"); b.append(s); return b; }}
async function evaluate(keepDraft) {{
  const q = new URLSearchParams({{user: $("user").value, task: $("task").value}});
  if (keepDraft && $("draft").value.trim()) q.set("draft", $("draft").value);
  const r = await fetch("/api/preview?" + q), data = await r.json();
  $("error").textContent = r.ok ? "" : data.error;
  if (!r.ok) return;
  if (!keepDraft) $("draft").value = data.draft;
  $("o-draft").textContent = data.draft;
  const a = $("o-action"); a.textContent = NAMES[data.action]; a.className = "decision " + data.action;
  $("r-text").textContent = data.text ?? "(nada: queda retenido para revisión)";
  const bars = $("o-bars"); bars.replaceChildren();
  data.attempts.forEach((t, i) => {{
    bars.append(el("h3", "Alternativa " + (i + 1)));
    if (t.reason) {{ bars.append(el("p", REASONS[t.reason] || t.reason)); return; }}
    if (!t.changes.length) bars.append(el("p", "Sin aumento de la probabilidad."));
    const blocked = new Set(t.blocked.map(c => c.subject + c.family + c.status));
    t.changes.forEach(c => {{
      const box = el("div", undefined, blocked.has(c.subject + c.family + c.status) ? "blocked" : "");
      box.append(el("p", `${{c.subject}} · ${{c.family}} = ${{c.status}}: ${{pct(c.before)}} → ${{pct(c.after)}}` +
        (c.direct ? " (revelación directa, bloqueada)" : blocked.has(c.subject + c.family + c.status) ? " (bloqueada)" : " (bajo el umbral)")),
        el("span", "antes", "tag"), bar(c.before), el("span", "después", "tag"), bar(c.after));
      bars.append(box);
    }});
  }});
  const ctx = $("o-ctx"); ctx.replaceChildren(el("p", "Roles: " + (data.observer.roles.join(", ") || "ninguno")));
  const list = el("ul");
  data.observer.possible_knowledge.forEach(h => list.append(el("li", h.fact.join(" · ") + " (p = " + h.p_known.toFixed(2) + ")")));
  if (!list.children.length) list.append(el("li", "Sin pistas registradas."));
  ctx.append(list);
}}
for (const [k, v] of new URLSearchParams(location.search)) if (["user", "task"].includes(k)) $(k).value = v;
$("f").addEventListener("submit", e => {{ e.preventDefault(); evaluate(true); }});
$("user").addEventListener("change", () => evaluate(false));
$("task").addEventListener("change", () => evaluate(false));
evaluate(false);
</script></body></html>"""


def respond(guard: Guard, path: str):
    """Return (status, content_type, body, nonce) so tests can exercise routing without sockets."""
    url = urlsplit(path)
    if url.path == "/":
        nonce = secrets.token_urlsafe(16)
        users = dict.fromkeys(a.user for a in guard.case.assignments)
        page = PAGE.format(nonce=nonce, day=DAY.isoformat(), max_draft=MAX_DRAFT,
                           users="".join(f'<option value="{escape(u)}">{escape(u)}</option>' for u in users),
                           tasks="".join(f'<option value="{escape(t)}">{escape(t)}</option>' for t in guard.case.tasks))
        return 200, "text/html; charset=utf-8", page, nonce
    if url.path != "/api/preview":
        return 404, "application/json", {"error": "Ruta desconocida"}, None
    query = parse_qs(url.query)
    user, task = query.get("user", [""])[0], query.get("task", [""])[0]
    draft = query.get("draft", [""])[0] or None
    if user not in {a.user for a in guard.case.assignments}:
        return 400, "application/json", {"error": "Destinatario desconocido"}, None
    if task not in guard.case.tasks:
        return 400, "application/json", {"error": "Tarea desconocida"}, None
    if draft is not None and len(draft) > MAX_DRAFT:
        return 400, "application/json", {"error": f"El borrador supera {MAX_DRAFT} caracteres"}, None
    try:
        result = guard.run(user, task, DAY, draft=draft)
    except ValueError as exc:
        return 400, "application/json", {"error": str(exc)}, None
    shown = draft or " ".join(guard.case.clauses[k].text for k in guard.case.tasks[task].options[0].clauses)
    return 200, "application/json", {"user": user, "task": task, "draft": shown, **result,
                                     "observer": guard.model_context(user, DAY)}, None


def serve(policy, case, history, port=8000):
    guard = Guard(":memory:", policy, case)
    guard.observe(Batch.load(history).observations)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status, kind, body, nonce = respond(guard, self.path)
            data = (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", f"default-src 'none'; connect-src 'self'; base-uri 'none'; "
                             f"form-action 'none'; frame-ancestors 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'")
            self.end_headers()
            self.wfile.write(data)

    # shortcut: single-threaded server on loopback without a Host check, fine for a local pitch; add one before exposing it.
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"ISDE demo en http://127.0.0.1:{port}/ (Ctrl+C para salir)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        guard.close()
