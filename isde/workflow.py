"""LangGraph orchestration for an external agent's message and bounded rewrites."""
import hashlib
import json
from datetime import date
from typing import Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import Field

from .claude import _request
from .guard import Guard
from .model import Model
from .prompts import REWRITE_PROMPT


class Rewrite(Model):
    text: str = Field(min_length=1, max_length=4000)


def claude_rewrite(draft: str, safe_answer: str, task: str, *, prompt=None) -> str:
    result = _request(
        REWRITE_PROMPT if prompt is None else prompt,
        {"task": task, "draft": draft, "approved_alternative": safe_answer},
        Rewrite, None, None,
    )
    return result.text


class Flow(TypedDict, total=False):
    user: str
    task: str
    day: date
    request_id: str
    fingerprint: str
    candidate: str
    decision: dict
    suggestion: str | None
    rewrites: int
    outcome: dict
    error: str | None


def fingerprint(user: str, task: str, draft: str, day: date, attachments=()) -> str:
    # Attachment hashes join only when present so earlier request identities stay valid.
    payload = json.dumps([user, task, draft, day.isoformat(), *([list(attachments)] if attachments else [])],
                         ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def make_workflow(guard: Guard, rewrite: Callable = claude_rewrite, max_rewrites: int = 2):
    def evaluate(state: Flow):
        exact = guard.run(state["user"], state["task"], state["day"], draft=state["candidate"], strict=True)
        if exact["action"] == "allow":
            return {"decision": exact, "suggestion": None}
        alternatives = guard.run(state["user"], state["task"], state["day"], draft=state["candidate"])
        return {"decision": exact, "suggestion": alternatives.get("text")}

    def route(state: Flow):
        if state["decision"]["action"] == "allow":
            return "deliver"
        if state["suggestion"] and state.get("rewrites", 0) < max_rewrites:
            return "rewrite"
        return "review"

    def rewrite_answer(state: Flow):
        try:
            candidate = rewrite(state["candidate"], state["suggestion"], state["task"])
            if not isinstance(candidate, str) or not candidate.strip() or len(candidate) > 4000:
                raise ValueError("Reformulación inválida")
        except Exception:
            return {"error": "rewrite_unavailable", "rewrites": max_rewrites}
        return {"candidate": candidate, "rewrites": state.get("rewrites", 0) + 1}

    def after_rewrite(state: Flow):
        return "review" if state.get("error") else "evaluate"

    def deliver(state: Flow):
        result = guard.run(state["user"], state["task"], state["day"], state["request_id"],
                           draft=state["candidate"], strict=True, request_fingerprint=state["fingerprint"],
                           rewritten=state.get("rewrites", 0) > 0)
        return {"outcome": result}

    def review(state: Flow):
        # Review is persisted under the same request identity without exposing a candidate.
        with guard.transaction():
            previous = guard.db.execute(
                "SELECT task,draft_hash,decision FROM deliveries WHERE org=? AND op=? AND audience=? AND request_id=?",
                (*guard.scope, state["user"], state["request_id"]),
            ).fetchone()
            if previous:
                if previous[:2] != (state["task"], state["fingerprint"]):
                    raise ValueError("Identificador de entrega reutilizado para otra solicitud")
                return {"outcome": json.loads(previous[2])}
            latest = guard.db.execute(
                "SELECT max(day) FROM deliveries WHERE org=? AND op=? AND audience=?",
                (*guard.scope, state["user"]),
            ).fetchone()[0]
            if latest and state["day"].isoformat() < latest:
                raise ValueError("Una entrega nueva debe respetar el orden del historial")
            result = {"action": "review", "text": None, "attempts": []}
            guard.db.execute("INSERT INTO deliveries VALUES (?,?,?,?,?,?,?,?)",
                             (*guard.scope, state["user"], state["request_id"], state["task"],
                              state["fingerprint"], state["day"].isoformat(), json.dumps(result)))
        return {"outcome": result}

    graph = StateGraph(Flow)
    graph.add_node("evaluate", evaluate)
    graph.add_node("rewrite", rewrite_answer)
    graph.add_node("deliver", deliver)
    graph.add_node("review", review)
    graph.add_edge(START, "evaluate")
    graph.add_conditional_edges("evaluate", route)
    graph.add_conditional_edges("rewrite", after_rewrite)
    graph.add_edge("deliver", END)
    graph.add_edge("review", END)
    return graph.compile()
