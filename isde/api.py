"""Local HTTP demo. The caller is the trusted integration for identity and scope."""
import hashlib
import json
import os
import secrets
from datetime import date
from threading import RLock

from fastapi import FastAPI, Header, HTTPException
from .semantic import ClaudeAnalysis, SourceInput, source_graph
from . import reviews
from pydantic import BaseModel, ConfigDict, Field

from .cli import EXAMPLES
from .guard import Guard
from .model import Batch, Case, Policy
from .workflow import claude_rewrite, fingerprint, make_workflow


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: str = Field(min_length=1, max_length=96)
    task: str = Field(min_length=1, max_length=96)
    draft: str = Field(min_length=1, max_length=4000)
    request_id: str = Field(min_length=1, max_length=128)
    day: date


class NewReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_id: str = Field(min_length=1, max_length=96, pattern=r"^[a-zA-Z0-9_-]+$")
    user: str = Field(min_length=1, max_length=96)
    task: str = Field(min_length=1, max_length=96)
    question: str = Field(min_length=1, max_length=4000)
    text: str = Field(min_length=1, max_length=4000)
    day: date


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


def create_app(*, db: str | None = None, rewrite=claude_rewrite, seed=True, model=None):
    db = db or os.environ.get("MOSAIC_API_DB", os.environ.get("ISDE_API_DB", "mosaic-api.sqlite"))
    samples = EXAMPLES
    policy = Policy.load(os.environ.get("MOSAIC_POLICY", os.environ.get("ISDE_POLICY", str(samples / "policy.yaml"))))
    case = Case.load(os.environ.get("MOSAIC_CASE", os.environ.get("ISDE_CASE", str(samples / "case.yaml"))))
    history = Batch.load(os.environ.get("MOSAIC_HISTORY", os.environ.get("ISDE_HISTORY", str(samples / "history.yaml")))) if seed else None
    lock = RLock()
    operator_token = os.environ.get("MOSAIC_OPERATOR_TOKEN")
    model = model or ClaudeAnalysis()
    if seed:
        guard = Guard(db, policy, case)
        try:
            guard.observe(history.observations)
        finally:
            guard.close()
    app = FastAPI(title="mosAIc demo", version="0.1.0")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/v1/messages")
    def message(item: Message):
        if not item.draft.strip() or not item.request_id.strip():
            raise HTTPException(status_code=422, detail="Borrador o identificador vacío")
        with lock:
            guard = Guard(db, policy, case)
            try:
                if item.user not in {a.user for a in case.assignments if (a.org, a.operation) == guard.scope}:
                    raise ValueError("Destinatario fuera del directorio")
                if item.task not in case.tasks:
                    raise ValueError("Tarea desconocida")
                token = fingerprint(item.user, item.task, item.draft, item.day)
                previous = guard.db.execute(
                    "SELECT task,draft_hash,decision FROM deliveries WHERE org=? AND op=? AND audience=? AND request_id=?",
                    (*guard.scope, item.user, item.request_id),
                ).fetchone()
                if previous:
                    if previous[:2] != (item.task, token):
                        raise HTTPException(status_code=409, detail="Identificador usado para otra solicitud")
                    stored = json.loads(previous[2])
                    return {"request_id": item.request_id, "action": stored["action"], "text": stored["text"]}
                flow = make_workflow(guard, rewrite).invoke({
                    "user": item.user, "task": item.task, "day": item.day, "request_id": item.request_id,
                    "fingerprint": token, "candidate": item.draft, "rewrites": 0,
                })
                outcome = flow["outcome"]
                return {"request_id": item.request_id, "action": outcome["action"], "text": outcome["text"]}
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            finally:
                guard.close()

    @app.post("/v1/observations")
    def observations(batch: Batch):
        with lock:
            guard = Guard(db, policy, case)
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
    def sources(source: IncomingSource):
        with lock:
            guard = Guard(db, policy, case)
            try:
                valid = {a.user for a in case.assignments if (a.org, a.operation) == guard.scope}
                if set(source.recipients) - valid:
                    raise ValueError("Destinatario fuera del directorio")
                data = SourceInput.model_validate(source.model_dump(exclude={"accept"}))
                digest = hashlib.sha256(data.model_dump_json().encode()).hexdigest()
                if source.accept:
                    previous = guard.db.execute(
                        "SELECT digest FROM source_imports WHERE org=? AND op=? AND source_id=?",
                        (*guard.scope, data.origin)).fetchone()
                    if previous:
                        if previous[0] != digest:
                            raise ValueError("Identificador de fuente usado con otro contenido")
                        return {"accepted": False, "proposals": 0}
                result = source_graph(guard, model).invoke(
                    {"source": data, "accept": source.accept, "digest": digest})
                events = result["internal"] + result["external"]
                if source.accept:
                    return {"accepted": result["accepted"], "proposals": len(events)}
                return {"accepted": False, "observations": [e.model_dump(mode="json") for e in events]}
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except Exception as exc:
                raise HTTPException(status_code=503, detail="No se pudo analizar la fuente") from exc
            finally:
                guard.close()

    @app.post("/v1/reviews")
    def new_review(item: NewReview):
        with lock:
            guard = Guard(db, policy, case)
            try:
                return reviews.review(guard, model, item.review_id, item.user, item.task,
                                      item.day, item.text, item.question)
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
            guard = Guard(db, policy, case)
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
            guard = Guard(db, policy, case)
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
            guard = Guard(db, policy, case)
            try:
                return reviews.delivery_graph(guard).invoke({"review_id": review_id, "user": item.user})["outcome"]
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            finally:
                guard.close()

    @app.get("/v1/inbox/{user}")
    def inbox(user: str):
        with lock:
            guard = Guard(db, policy, case)
            try:
                if user not in {a.user for a in case.assignments if (a.org, a.operation) == guard.scope}:
                    raise HTTPException(status_code=404, detail="Destinatario desconocido")
                return {"messages": guard.inbox(user)}
            finally:
                guard.close()

    return app
