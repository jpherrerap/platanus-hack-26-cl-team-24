"""Registro de alertas de evaluaciones; el detalle queda reservado al operador."""
import hashlib
import json
from datetime import datetime, timezone

from .claude import model_id
from .model import INTRINSIC_FAMILY


def record(guard, kind, title, target_type, target_id, details):
    key = json.dumps([*guard.scope, kind, target_type, target_id], separators=(",", ":"))
    event_id = hashlib.sha256(key.encode()).hexdigest()[:32]
    guard.db.execute("""
        INSERT INTO activity_events VALUES (?,?,?,?,?,?,?,?,?,?,NULL,NULL)
        ON CONFLICT(org,op,event_id) DO NOTHING
    """, (*guard.scope, event_id, kind, title, datetime.now(timezone.utc).isoformat(),
          target_type, target_id, "open", json.dumps(details, ensure_ascii=False)))
    return event_id


def review_event(guard, review_id, revision, user, text, question, analysis, hypotheses, result):
    risk = any(attempt.get("blocked") or attempt.get("reason") in {"personal_identifier", "personal_identifier_link"}
               for attempt in result.get("attempts", []))
    if result["action"] == "allow" and not any(h.p_if_seen > 0 for h in hypotheses):
        return
    kind = "blocked" if risk else "flagged"
    title = "Se bloqueó una posible filtración de información" if risk else "Una respuesta contiene señales para revisar"
    return record(guard, kind, title, "review", f"{review_id}:{revision}", {
        "origin": "review_api", "review_id": review_id, "revision": revision, "recipient": user,
        "question": question, "candidate": text, "model": model_id(), "decision": result,
        "logs": [
            {"agent": "analyst", "output": analysis.model_dump(mode="json")},
            {"agent": "adversary", "output": [h.model_dump(mode="json") for h in hypotheses]},
            {"component": "rdf_problog", "output": result},
        ],
        "notice": "Registro de salidas estructuradas y decisión; no contiene razonamiento interno del proveedor.",
    })


def source_event(guard, source, result):
    observations = result["internal"] + result["external"]
    hypotheses = [h for event in observations for h in event.hypotheses
                  if h.p_if_seen > 0 and (h.claim[1] == INTRINSIC_FAMILY or h.claim[1] in guard.policy.families)]
    missing = result.get("missing_recipients", [])
    if not hypotheses and observations and not missing:
        return
    title = "Se detectó información potencialmente sensible en una fuente" if hypotheses else "Una fuente necesita revisión"
    details = {"origin": "source_analysis", "source_id": source.origin, "source_text": source.text,
               "model": model_id(), "missing_recipients": missing,
               "observations": [e.model_dump(mode="json") for e in observations],
               "notice": "Analizar una entrada no implica que se haya bloqueado una salida."}
    if result.get("analysis_error"):
        details["analysis_error"] = result["analysis_error"]
    digest = hashlib.sha256(json.dumps(details, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
    return record(guard, "flagged", title, "source", f"{source.origin}:{digest}", details)


def listing(guard, limit=50):
    rows = guard.db.execute("""
        SELECT event_id,kind,title,created_at,target_type,status,resolved_at
        FROM activity_events WHERE org=? AND op=? ORDER BY created_at DESC,event_id DESC LIMIT ?
    """, (*guard.scope, limit))
    keys = ("id", "kind", "title", "created_at", "target_type", "status", "resolved_at")
    items = [dict(zip(keys, row)) for row in rows]
    counts = {"total": 0, "blocked": 0, "flagged": 0, "open": 0}
    for kind, status, count in guard.db.execute(
        "SELECT kind,status,count(*) FROM activity_events WHERE org=? AND op=? GROUP BY kind,status", guard.scope):
        counts["total"] += count
        counts[kind] += count
        counts["open"] += count if status == "open" else 0
    return {"events": items, "counts": counts, "details_require_operator": True}


def detail(guard, event_id):
    row = guard.db.execute("""
        SELECT kind,title,created_at,target_type,target_id,status,details,resolved_at,resolution_note
        FROM activity_events WHERE org=? AND op=? AND event_id=?
    """, (*guard.scope, event_id)).fetchone()
    if row is None:
        raise ValueError("Notificación desconocida")
    keys = ("kind", "title", "created_at", "target_type", "target_id", "status", "details", "resolved_at", "resolution_note")
    result = {"id": event_id, **dict(zip(keys, row))}
    result["details"] = json.loads(result["details"])
    return result


def resolve(guard, event_id, status, note):
    previous = detail(guard, event_id)
    if previous["status"] != "open":
        if previous["status"] != status or previous["resolution_note"] != note:
            raise ValueError("El elemento ya fue revisado")
        return previous
    guard.db.execute("""
        UPDATE activity_events SET status=?,resolved_at=?,resolution_note=?
        WHERE org=? AND op=? AND event_id=?
    """, (status, datetime.now(timezone.utc).isoformat(), note, *guard.scope, event_id))
    return detail(guard, event_id)
