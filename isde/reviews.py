"""Durable preview, revision and atomic local delivery for semantic evaluations."""
import hashlib
import json
from datetime import date
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .model import Hypothesis
from .semantic import FREE_TASKS, DraftAssessment, inspect_graph, score_candidate
from .claude import model_id
from .media import with_attachments
from . import events


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def review(guard, model, review_id, user, task, day, text, question, *, revision=False, revision_id=None, attachments=()):
    if user not in {a.user for a in guard.case.assignments if (a.org, a.operation) == guard.scope}:
        raise ValueError("Destinatario fuera del directorio")
    if not revision and task not in FREE_TASKS and task not in guard.case.tasks:
        raise ValueError("Tarea desconocida")
    if not revision and task in FREE_TASKS and not question.strip():
        raise ValueError("La tarea libre requiere una pregunta")
    # Attachment hashes join only when present so earlier review identities stay valid.
    digests = [[a.digest() for a in attachments]] if attachments else []
    original_hash = hashlib.sha256(encode([user, task, day.isoformat(), text, question, *digests]).encode()).hexdigest()
    with guard.transaction():
        previous = guard.db.execute(
            "SELECT user,task,day,original_hash,revision,status,question FROM semantic_reviews WHERE org=? AND op=? AND review_id=?",
            (*guard.scope, review_id)).fetchone()
        if revision:
            if not previous or previous[0] != user:
                raise ValueError("Revisión desconocida")
            replay = guard.db.execute(
                "SELECT digest,response FROM semantic_revisions WHERE org=? AND op=? AND review_id=? AND revision_id=?",
                (*guard.scope, review_id, revision_id)).fetchone()
            if replay:
                if replay[0] != hashlib.sha256(text.encode()).hexdigest():
                    raise ValueError("Identificador de revisión usado para otro texto")
                return json.loads(replay[1])
            if previous[5] not in {"revise", "review"} or previous[4] >= 2:
                raise ValueError("Revisión finalizada o límite de intentos alcanzado")
            task, day, question = previous[1], date.fromisoformat(previous[2]), previous[6]
            original_hash = previous[3]
        elif previous:
            if previous[3] != original_hash:
                raise ValueError("Identificador usado para otra solicitud")
            return {"review_id": review_id, "status": previous[5], "revision": previous[4],
                    "feedback": "Reformula para responder la tarea sin divulgar información restringida."
                                if previous[5] == "revise" else None}
        expected = previous

    # Transcribe only after the replay checks so a repeated request costs nothing.
    text = with_attachments(text, attachments) if attachments else text
    if text is None:
        return {"review_id": review_id, "status": "rejected", "revision": 0,
                "feedback": "Se rechazó el audio porque no se pudo transcribir. Quítalo para evaluar el resto."}
    state = inspect_graph(guard, model).invoke({"user": user, "task": task, "question": question, "day": day, "text": text})
    status = "approved" if state["result"]["action"] == "allow" else "revise"
    snapshot = encode({"observer": state["context"], "internal": state["internal"]})
    analysis = encode(state["analysis"].model_dump(mode="json"))
    hypotheses = encode([h.model_dump(mode="json") for h in state["hypotheses"]])
    with guard.transaction():
        current = guard.db.execute(
            "SELECT user,task,day,original_hash,revision,status,question FROM semantic_reviews WHERE org=? AND op=? AND review_id=?",
            (*guard.scope, review_id)).fetchone()
        if current != expected:
            raise ValueError("La revisión cambió durante el análisis")
        actual = encode({"observer": guard.model_context(user, day), "internal": guard.model_context(None, day)})
        if actual != snapshot:
            raise ValueError("El contexto cambió durante el análisis")
        number = previous[4] + 1 if revision else 0
        response = {"review_id": review_id, "status": status, "revision": number,
                    "feedback": "Reformula para responder la tarea sin divulgar información restringida."
                                if status == "revise" else None}
        guard.db.execute("""
            INSERT INTO semantic_reviews VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(org,op,review_id) DO UPDATE SET revision=excluded.revision, text=excluded.text,
                analysis=excluded.analysis, hypotheses=excluded.hypotheses,
                snapshot=excluded.snapshot,status=excluded.status,trace=excluded.trace
        """, (*guard.scope, review_id, user, task, day.isoformat(), question, original_hash, number, text,
              analysis, hypotheses, snapshot, status, encode(state["result"])))
        if revision:
            guard.db.execute("INSERT INTO semantic_revisions VALUES (?,?,?,?,?,?)",
                             (*guard.scope, review_id, revision_id, hashlib.sha256(text.encode()).hexdigest(),
                              encode(response)))
        events.review_event(guard, review_id, number, user, text, question,
                            state["analysis"], state["hypotheses"], state["result"])
    return response


def deliver(guard, review_id, user):
    with guard.transaction():
        row = guard.db.execute("""
            SELECT user,task,day,revision,text,analysis,hypotheses,snapshot,status
            FROM semantic_reviews WHERE org=? AND op=? AND review_id=?
        """, (*guard.scope, review_id)).fetchone()
        if not row or row[0] != user:
            raise ValueError("Revisión desconocida")
        if row[8] == "delivered":
            stored = guard.db.execute("""
                SELECT decision FROM deliveries WHERE org=? AND op=? AND audience=? AND request_id=?
            """, (*guard.scope, user, "semantic:" + review_id)).fetchone()
            return json.loads(stored[0])
        if row[8] != "approved":
            raise ValueError("La revisión no está aprobada")
        day = date.fromisoformat(row[2])
        latest = guard.db.execute("SELECT max(day) FROM deliveries WHERE org=? AND op=? AND audience=?",
                                  (*guard.scope, user)).fetchone()[0]
        if latest and day.isoformat() < latest:
            raise ValueError("Una entrega nueva debe respetar el orden del historial")
        context = {"observer": guard.model_context(user, day), "internal": guard.model_context(None, day)}
        if encode(context) != row[7]:
            guard.db.execute("UPDATE semantic_reviews SET status='review' WHERE org=? AND op=? AND review_id=?",
                             (*guard.scope, review_id))
            events.record(guard, "flagged", "Una entrega necesita reevaluación", "delivery", review_id,
                          {"reason": "context_changed", "review_id": review_id, "recipient": user,
                           "notice": "No se entregó la respuesta porque cambió el contexto."})
            return {"action": "review", "text": None, "reason": "context_changed"}
        state = {"user": user, "task": row[1], "day": day, "text": row[4],
                 "analysis": DraftAssessment.model_validate_json(row[5]),
                 "hypotheses": [Hypothesis.model_validate(h) for h in json.loads(row[6])]}
        decision, exposure = score_candidate(guard, state)
        if decision["action"] != "allow" or exposure is None:
            guard.db.execute("UPDATE semantic_reviews SET status='review' WHERE org=? AND op=? AND review_id=?",
                             (*guard.scope, review_id))
            return {"action": "review", "text": None, "reason": "reassessment_required"}
        facts, hypotheses = exposure
        for origin, fact, _ in facts:
            guard._record(user, origin, [fact], day, "delivered")
        for hypothesis in hypotheses:
            guard._record_hypothesis(user, hypothesis.origin, day, "delivered", 1., hypothesis, model_id())
        result = {"action": "repair" if row[3] else "allow", "text": decision["text"]}
        guard.db.execute("INSERT INTO deliveries VALUES (?,?,?,?,?,?,?,?)",
                         (*guard.scope, user, "semantic:" + review_id, row[1],
                          hashlib.sha256(row[4].encode()).hexdigest(), row[2], json.dumps(result)))
        guard.db.execute("UPDATE semantic_reviews SET status='delivered' WHERE org=? AND op=? AND review_id=?",
                         (*guard.scope, review_id))
        return result


class DeliveryState(TypedDict, total=False):
    review_id: str
    user: str
    outcome: dict


def delivery_graph(guard):
    def validate(state):
        if not state["review_id"] or not state["user"]:
            raise ValueError("Solicitud de entrega inválida")
        return {}

    def confirm(state):
        return {"outcome": deliver(guard, state["review_id"], state["user"])}

    graph = StateGraph(DeliveryState)
    graph.add_node("validate", validate)
    graph.add_node("confirm", confirm)
    graph.add_edge(START, "validate")
    graph.add_edge("validate", "confirm")
    graph.add_edge("confirm", END)
    return graph.compile()
