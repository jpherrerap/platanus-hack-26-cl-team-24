"""Local HTTP demo. The caller is the trusted integration for identity and scope."""
import html
import json
import os
import secrets
from datetime import date
from threading import RLock
from urllib.parse import urlsplit
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from .semantic import ClaudeAnalysis, RecordedReviews, SourceInput, source_graph
from . import events as activity_events, reviews
from pydantic import BaseModel, ConfigDict, Field

from .cli import EXAMPLES, load_env
from .guard import Guard
from .media import Attachment, with_attachments
from .model import Batch, Case, Family, INTRINSIC_FAMILY, Policy
from .prompts import DEFAULT_PROMPTS
from .workflow import claude_rewrite, fingerprint, make_workflow


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: str = Field(min_length=1, max_length=96)
    task: str = Field(min_length=1, max_length=96)
    draft: str = Field(min_length=1, max_length=4000)
    request_id: str = Field(min_length=1, max_length=128)
    day: date
    attachments: list[Attachment] = Field(default_factory=list, max_length=4)


class NewReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_id: str = Field(min_length=1, max_length=96, pattern=r"^[a-zA-Z0-9_-]+$")
    user: str = Field(min_length=1, max_length=96)
    task: str = Field(min_length=1, max_length=96)
    question: str = Field(min_length=1, max_length=4000)
    text: str = Field(min_length=1, max_length=4000)
    day: date
    attachments: list[Attachment] = Field(default_factory=list, max_length=4)


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: str = Field(min_length=1, max_length=96, pattern=r"^[a-zA-Z0-9_-]+$")
    user: str = Field(min_length=1, max_length=96)
    text: str = Field(min_length=1, max_length=4000)


class Recipient(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user: str = Field(min_length=1, max_length=96)


class IncomingSource(SourceInput):
    accept: bool = False


class PromptUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=16000)


class VocabularyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=1, max_length=2000)


class SecretUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=1, max_length=2000)
    statuses: list[str] = Field(default_factory=list, max_length=32)
    any_value: bool = False
    absolute: float = Field(ge=0, le=1, allow_inf_nan=False)
    increase: float = Field(ge=0, le=1, allow_inf_nan=False)
    allowed_roles: list[str] = Field(default_factory=list, max_length=100)


class EventResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["resolved", "dismissed"]
    note: str = Field(default="", max_length=1000)


def create_app(*, db: str | None = None, rewrite=claude_rewrite, seed=True, model=None):
    # Only the real server (uvicorn --factory, no arguments) reads .env; tests pass db and must not pick up real keys.
    if db is None:
        load_env()
    db = db or os.environ.get("MOSAIC_API_DB", os.environ.get("ISDE_API_DB", "mosaic-api.sqlite"))
    samples = EXAMPLES
    policy = Policy.load(os.environ.get("MOSAIC_POLICY", os.environ.get("ISDE_POLICY", str(samples / "mvp-policy.yaml"))))
    case = Case.load(os.environ.get("MOSAIC_CASE", os.environ.get("ISDE_CASE", str(samples / "mvp-case.yaml"))))
    history = Batch.load(os.environ.get("MOSAIC_HISTORY", os.environ.get("ISDE_HISTORY", str(samples / "mvp-history.yaml")))) if seed else None
    lock = RLock()
    operator_token = os.environ.get("MOSAIC_OPERATOR_TOKEN")
    prompts = dict(DEFAULT_PROMPTS)
    initializer = Guard(db, policy, case)
    try:
        row = initializer.db.execute("SELECT policy,prompts FROM dashboard_config WHERE org=? AND op=?",
                                     initializer.scope).fetchone()
        if row:
            policy = Policy.model_validate_json(row[0])
            saved = json.loads(row[1])
            prompts.update({key: value for key, value in saved.items() if key in prompts})
    finally:
        initializer.close()
    recorded = os.environ.get("MOSAIC_RECORDED", str(samples / "demo-recorded.yaml"))
    model = model or ClaudeAnalysis(prompts, RecordedReviews.load(recorded).reviews if recorded else ())
    staged_sources = {}
    if seed:
        guard = Guard(db, policy, case, prompts=prompts)
        try:
            guard.observe(history.observations)
            # Optional demo notifications; without the variable the feed starts empty, as before.
            if os.environ.get("MOSAIC_EVENTS"):
                activity_events.seed(guard, activity_events.PastEvents.load(os.environ["MOSAIC_EVENTS"]).events)
            activity_events.seed_impact(guard)
        finally:
            guard.close()
    app = FastAPI(title="mosAIc demo", version="0.1.0")

    def open_guard():
        return Guard(db, policy, case, prompts=prompts)

    def persist_config(guard, new_policy, new_prompts):
        guard.db.execute("""
            INSERT INTO dashboard_config VALUES (?,?,?,?,1)
            ON CONFLICT(org,op) DO UPDATE SET policy=excluded.policy, prompts=excluded.prompts,
                                               revision=dashboard_config.revision+1
        """, (*guard.scope, new_policy.model_dump_json(), json.dumps(new_prompts, ensure_ascii=False)))

    def require_operator(token):
        if not operator_token or not secrets.compare_digest(operator_token.encode(), token.encode()):
            raise HTTPException(status_code=403, detail="Acceso de operador requerido")

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        from .dashboard import PAGE
        page = PAGE
        # Prefill only for a loopback client under a local Host: a DNS-rebinding page must not read the token.
        local = request.client and request.client.host in {"127.0.0.1", "::1"} and \
            urlsplit("//" + request.headers.get("host", "")).hostname in {"127.0.0.1", "localhost", "::1"}
        if operator_token and local:
            page = page.replace('placeholder="Introduce tu token">',
                                f'value="{html.escape(operator_token)}" readonly>', 1).replace(
                "Se guarda solo en este navegador.", "Cargado desde MOSAIC_OPERATOR_TOKEN.", 1)
        return HTMLResponse(page, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                            "Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; "
                                                       "style-src 'unsafe-inline'; object-src 'none'; "
                                                       "base-uri 'none'; frame-ancestors 'none'"})

    @app.get("/v1/demo/events")
    def activity(response: Response, limit: int = Query(default=50, ge=1, le=100)):
        response.headers["Cache-Control"] = "no-store"
        with lock:
            guard = open_guard()
            try:
                return activity_events.listing(guard, limit)
            finally:
                guard.close()

    @app.get("/v1/demo/impact")
    def impact(response: Response, area: str | None = Query(default=None, max_length=40)):
        response.headers["Cache-Control"] = "no-store"
        with lock:
            guard = open_guard()
            try:
                return activity_events.impact(guard, area or None)
            finally:
                guard.close()

    @app.get("/v1/demo/events/{event_id}")
    def event_detail(event_id: str, response: Response, x_mosaic_operator_token: str = Header(default="")):
        require_operator(x_mosaic_operator_token)
        response.headers["Cache-Control"] = "no-store"
        with lock:
            guard = open_guard()
            try:
                return activity_events.detail(guard, event_id)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            finally:
                guard.close()

    @app.post("/v1/demo/events/{event_id}/resolution")
    def event_resolution(event_id: str, item: EventResolution, response: Response, x_mosaic_operator_token: str = Header(default="")):
        require_operator(x_mosaic_operator_token)
        response.headers["Cache-Control"] = "no-store"
        with lock:
            guard = open_guard()
            try:
                with guard.transaction():
                    return activity_events.resolve(guard, event_id, item.status, item.note)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            finally:
                guard.close()

    @app.get("/v1/demo/config")
    def configuration():
        with lock:
            guard = open_guard()
            try:
                return {"users": sorted({a.user for a in case.assignments if (a.org, a.operation) == (case.org, case.operation)}),
                        "roles": sorted(policy.roles), "prompts": dict(prompts), "default_prompts": dict(DEFAULT_PROMPTS),
                        "families": policy.model_dump()["families"],
                        "grants": {name: role.receive for name, role in policy.roles.items()},
                        "vocabulary": guard.policy_at(date.max).vocabulary,
                        "names": case.names, "scope": {"org": case.org, "operation": case.operation}, "day": date.today().isoformat()}
            finally:
                guard.close()

    @app.put("/v1/demo/prompts/{agent}")
    def update_prompt(agent: str, item: PromptUpdate):
        if agent not in DEFAULT_PROMPTS or not item.prompt.strip():
            raise HTTPException(status_code=400, detail="Agente o prompt inválido")
        with lock:
            guard = open_guard()
            try:
                changed = {**prompts, agent: item.prompt}
                with guard.transaction():
                    persist_config(guard, policy, changed)
                prompts.update(changed)
                return {"agent": agent, "saved": True}
            finally:
                guard.close()

    @app.put("/v1/demo/secrets/{name}")
    def update_secret(name: str, item: SecretUpdate):
        nonlocal policy
        with lock:
            guard = open_guard()
            try:
                if set(item.allowed_roles) - policy.roles.keys():
                    raise ValueError("Rol desconocido")
                if name in policy.families:
                    existing = policy.families[name]
                    if existing.any_value and not item.any_value:
                        raise ValueError("No se puede reducir una familia que protege cualquier valor")
                    if not item.any_value and set(existing.statuses) - set(item.statuses):
                        raise ValueError("No se pueden eliminar estados protegidos existentes desde el panel")
                data = policy.model_dump(mode="json")
                data["families"][name] = Family(description=item.description, statuses=item.statuses,
                                                any_value=item.any_value,
                                                absolute=item.absolute, increase=item.increase).model_dump(mode="json")
                for role, entry in data["roles"].items():
                    entry["receive"] = [family for family in entry["receive"] if family != name]
                    if role in item.allowed_roles:
                        entry["receive"].append(name)
                updated = Policy.model_validate(data)
                for table, column in (("evidence", "fact"), ("interpretations", "claim")):
                    for (encoded,) in guard.db.execute(f"SELECT {column} FROM {table} WHERE org=? AND op=?", guard.scope):
                        _, predicate, value = json.loads(encoded)
                        if predicate == name and not updated.families[name].accepts(value):
                            raise ValueError("Un estado existente quedaría fuera de la familia protegida")
                vocab = {**guard.policy_at(date.max).vocabulary, **updated.vocabulary}
                effective = updated.model_copy(update={"vocabulary": vocab})
                for proposal in guard.proposed_rules(date.max):
                    from .model import Rule
                    Rule.model_validate(proposal["rule"]).validate_policy(effective)
                with guard.transaction():
                    persist_config(guard, updated, prompts)
                policy = updated
                return {"secret": name, "saved": True}
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            finally:
                guard.close()

    @app.put("/v1/demo/vocabulary/{name}")
    def update_vocabulary(name: str, item: VocabularyUpdate):
        nonlocal policy
        with lock:
            guard = open_guard()
            try:
                if name in policy.families:
                    raise ValueError("La relación ya es una familia protegida")
                data = policy.model_dump(mode="json")
                data["vocabulary"][name] = item.description
                updated = Policy.model_validate(data)
                with guard.transaction():
                    persist_config(guard, updated, prompts)
                policy = updated
                return {"predicate": name, "saved": True}
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            finally:
                guard.close()

    @app.post("/v1/demo/sources/preview")
    def preview_source(source: SourceInput, x_mosaic_operator_token: str = Header(default="")):
        if any(literal.family != INTRINSIC_FAMILY for literal in source.protected_literals):
            require_operator(x_mosaic_operator_token)
        with lock:
            guard = open_guard()
            try:
                valid = {a.user for a in case.assignments if (a.org, a.operation) == guard.scope}
                if set(source.recipients) - valid:
                    raise ValueError("Destinatario fuera del directorio")
                digest = source.digest()
                context = {"internal": guard.model_context(None, source.at),
                           "recipients": {u: guard.model_context(u, source.at) for u in source.recipients}}
                try:
                    result = source_graph(guard, model).invoke({"source": source, "accept": False, "digest": digest})
                except ValueError as exc:
                    activity_events.source_event(guard, source, {"internal": [], "external": [], "analysis_error": str(exc)})
                    raise
                events = result["internal"] + result["external"]
                current = {"internal": guard.model_context(None, source.at),
                           "recipients": {u: guard.model_context(u, source.at) for u in source.recipients}}
                if current != context:
                    raise ValueError("El contexto cambió durante el análisis")
                activity_events.source_event(guard, source, result)
                if result["missing_recipients"]:
                    return {"preview_id": None, "observations": [e.model_dump(mode="json") for e in events],
                            "message": "No se extrajeron pistas para: " + ", ".join(result["missing_recipients"]) +
                                       ". No se guardó la fuente; vuelve a analizarla o modifica los destinatarios."}
                if not events:
                    return {"preview_id": None, "observations": [], "message": "No se extrajeron hechos ni reglas; no se guardó la fuente."}
                token = secrets.token_hex(16)
                if len(staged_sources) >= 32:
                    staged_sources.pop(next(iter(staged_sources)))
                staged_sources[token] = (source, digest, context, events)
                return {"preview_id": token, "observations": [e.model_dump(mode="json") for e in events]}
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except Exception as exc:
                raise HTTPException(status_code=503, detail="No se pudo analizar la fuente") from exc
            finally:
                guard.close()

    @app.post("/v1/demo/sources/{preview_id}/accept")
    def accept_source(preview_id: str):
        with lock:
            if preview_id not in staged_sources:
                raise HTTPException(status_code=404, detail="Propuesta no disponible; vuelve a analizar la fuente")
            source, digest, context, events = staged_sources[preview_id]
            guard = open_guard()
            try:
                with guard.transaction():
                    previous = guard.db.execute("SELECT digest FROM source_imports WHERE org=? AND op=? AND source_id=?",
                                                (*guard.scope, source.origin)).fetchone()
                    if previous:
                        if previous[0] != digest:
                            raise ValueError("Identificador de fuente usado con otro contenido")
                        if guard.source_has_content(source.origin):
                            return {"accepted": False, "proposals": 0}
                    current = {"internal": guard.model_context(None, source.at),
                               "recipients": {u: guard.model_context(u, source.at) for u in source.recipients}}
                    if current != context:
                        raise ValueError("El contexto cambió; vuelve a analizar la fuente")
                accepted = guard.observe(events, source_id=source.origin, source_digest=digest)
                return {"accepted": accepted, "proposals": len(events)}
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            finally:
                guard.close()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/v1/messages")
    def message(item: Message):
        if not item.draft.strip() or not item.request_id.strip():
            raise HTTPException(status_code=422, detail="Borrador o identificador vacío")
        with lock:
            guard = open_guard()
            try:
                if item.user not in {a.user for a in case.assignments if (a.org, a.operation) == guard.scope}:
                    raise ValueError("Destinatario fuera del directorio")
                if item.task not in case.tasks:
                    raise ValueError("Tarea desconocida")
                token = fingerprint(item.user, item.task, item.draft, item.day, [a.digest() for a in item.attachments])
                previous = guard.db.execute(
                    "SELECT task,draft_hash,decision FROM deliveries WHERE org=? AND op=? AND audience=? AND request_id=?",
                    (*guard.scope, item.user, item.request_id),
                ).fetchone()
                if previous:
                    if previous[:2] != (item.task, token):
                        raise HTTPException(status_code=409, detail="Identificador usado para otra solicitud")
                    stored = json.loads(previous[2])
                    return {"request_id": item.request_id, "action": stored["action"], "text": stored["text"]}
                active_rewrite = (lambda draft, safe, task: claude_rewrite(draft, safe, task, prompt=prompts["rewriter"])) if rewrite is claude_rewrite else rewrite
                # A file cannot be rewritten, so a risky attachment goes to review instead.
                evaluated = with_attachments(item.draft, item.attachments)
                flow = make_workflow(guard, active_rewrite, max_rewrites=0 if item.attachments else 2).invoke({
                    "user": item.user, "task": item.task, "day": item.day, "request_id": item.request_id,
                    "fingerprint": token, "candidate": evaluated, "rewrites": 0,
                })
                outcome = flow["outcome"]
                activity_events.message_event(guard, item, evaluated, outcome, flow.get("screened", outcome))
                return {"request_id": item.request_id, "action": outcome["action"], "text": outcome["text"]}
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            finally:
                guard.close()

    @app.post("/v1/observations")
    def observations(batch: Batch, x_mosaic_operator_token: str = Header(default="")):
        if any(literal.family != INTRINSIC_FAMILY for event in batch.observations
               for literal in event.protected_literals):
            require_operator(x_mosaic_operator_token)
        with lock:
            guard = open_guard()
            try:
                valid = {a.user for a in case.assignments if (a.org, a.operation) == guard.scope}
                if any(event.user is not None and event.user not in valid for event in batch.observations):
                    raise ValueError("Destinatario fuera del directorio")
                guard.observe(batch.observations)
                return {"accepted": len(batch.observations)}
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            finally:
                guard.close()

    @app.post("/v1/sources")
    def sources(source: IncomingSource, x_mosaic_operator_token: str = Header(default="")):
        if any(literal.family != INTRINSIC_FAMILY for literal in source.protected_literals):
            require_operator(x_mosaic_operator_token)
        with lock:
            guard = open_guard()
            try:
                valid = {a.user for a in case.assignments if (a.org, a.operation) == guard.scope}
                if set(source.recipients) - valid:
                    raise ValueError("Destinatario fuera del directorio")
                data = SourceInput.model_validate(source.model_dump(exclude={"accept"}))
                digest = data.digest()
                if source.accept:
                    previous = guard.db.execute(
                        "SELECT digest FROM source_imports WHERE org=? AND op=? AND source_id=?",
                        (*guard.scope, data.origin)).fetchone()
                    if previous:
                        if previous[0] != digest:
                            raise ValueError("Identificador de fuente usado con otro contenido")
                        if guard.source_has_content(data.origin):
                            return {"accepted": False, "proposals": 0}
                try:
                    result = source_graph(guard, model).invoke(
                        {"source": data, "accept": source.accept, "digest": digest})
                except ValueError as exc:
                    activity_events.source_event(guard, data, {"internal": [], "external": [], "analysis_error": str(exc)})
                    raise
                observations = result["internal"] + result["external"]
                activity_events.source_event(guard, data, result)
                if source.accept:
                    return {"accepted": result["accepted"], "proposals": len(observations)}
                return {"accepted": False, "observations": [e.model_dump(mode="json") for e in observations],
                        "missing_recipients": result["missing_recipients"]}
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except Exception as exc:
                raise HTTPException(status_code=503, detail="No se pudo analizar la fuente") from exc
            finally:
                guard.close()

    @app.post("/v1/reviews")
    def new_review(item: NewReview):
        with lock:
            guard = open_guard()
            try:
                return reviews.review(guard, model, item.review_id, item.user, item.task,
                                      item.day, item.text, item.question, attachments=item.attachments)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except Exception as exc:
                raise HTTPException(status_code=503, detail="No se pudo evaluar el borrador") from exc
            finally:
                guard.close()

    @app.get("/v1/reviews/{review_id}")
    def review_trace(review_id: str, user: str, x_mosaic_operator_token: str = Header(default="")):
        if not operator_token or not secrets.compare_digest(operator_token.encode(), x_mosaic_operator_token.encode()):
            raise HTTPException(status_code=403, detail="Acceso de operador requerido")
        with lock:
            guard = open_guard()
            try:
                row = guard.db.execute(
                    "SELECT revision,status,trace FROM semantic_reviews WHERE org=? AND op=? AND review_id=? AND user=?",
                    (*guard.scope, review_id, user)).fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail="Revisión desconocida")
                return {"review_id": review_id, "revision": row[0], "status": row[1], "trace": json.loads(row[2])}
            finally:
                guard.close()

    @app.post("/v1/reviews/{review_id}/revisions")
    def revision(review_id: str, item: Revision):
        with lock:
            guard = open_guard()
            try:
                return reviews.review(guard, model, review_id, item.user, "", date.today(),
                                      item.text, "", revision=True, revision_id=item.revision_id)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except Exception as exc:
                raise HTTPException(status_code=503, detail="No se pudo evaluar la reformulación") from exc
            finally:
                guard.close()

    @app.post("/v1/reviews/{review_id}/deliver")
    def deliver(review_id: str, item: Recipient):
        with lock:
            guard = open_guard()
            try:
                return reviews.delivery_graph(guard).invoke({"review_id": review_id, "user": item.user})["outcome"]
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            finally:
                guard.close()

    @app.get("/v1/graph")
    def relation_graph(response: Response, user: str = Query(default=""), day: date | None = None,
                       x_mosaic_operator_token: str = Header(default="")):
        # Operator-only: the internal graph and what a recipient is believed to know are both private traces.
        require_operator(x_mosaic_operator_token)
        response.headers["Cache-Control"] = "no-store"
        with lock:
            guard = open_guard()
            try:
                if user and user not in {a.user for a in case.assignments if (a.org, a.operation) == guard.scope}:
                    raise HTTPException(status_code=404, detail="Destinatario desconocido")
                return guard.graph(user, day or date.today())
            finally:
                guard.close()

    @app.get("/v1/inbox/{user}")
    def inbox(user: str):
        with lock:
            guard = open_guard()
            try:
                if user not in {a.user for a in case.assignments if (a.org, a.operation) == guard.scope}:
                    raise HTTPException(status_code=404, detail="Destinatario desconocido")
                return {"messages": guard.inbox(user)}
            finally:
                guard.close()

    return app
