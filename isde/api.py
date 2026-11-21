"""Local HTTP demo. The caller is the trusted integration for identity and scope."""
import json
import os
from datetime import date
from threading import RLock

from fastapi import FastAPI, HTTPException
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


def create_app(*, db: str | None = None, rewrite=claude_rewrite, seed=True):
    db = db or os.environ.get("ISDE_API_DB", "isde-api.sqlite")
    samples = EXAMPLES
    policy = Policy.load(os.environ.get("ISDE_POLICY", str(samples / "policy.yaml")))
    case = Case.load(os.environ.get("ISDE_CASE", str(samples / "case.yaml")))
    history = Batch.load(os.environ.get("ISDE_HISTORY", str(samples / "history.yaml"))) if seed else None
    lock = RLock()
    if seed:
        guard = Guard(db, policy, case)
        try:
            guard.observe(history.observations)
        finally:
            guard.close()
    app = FastAPI(title="ISDE demo", version="0.1.0")

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
