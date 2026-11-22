"""Registro de alertas de evaluaciones; el detalle queda reservado al operador."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import AwareDatetime, Field

from .claude import model_id
from .model import INTRINSIC_FAMILY, Model


def record(guard, kind, title, target_type, target_id, details, status="open", created_at=None):
    key = json.dumps([*guard.scope, kind, target_type, target_id], separators=(",", ":"))
    event_id = hashlib.sha256(key.encode()).hexdigest()[:32]
    guard.db.execute("""
        INSERT INTO activity_events VALUES (?,?,?,?,?,?,?,?,?,?,NULL,NULL)
        ON CONFLICT(org,op,event_id) DO NOTHING
    """, (*guard.scope, event_id, kind, title, created_at or datetime.now(timezone.utc).isoformat(),
          target_type, target_id, status, json.dumps(details, ensure_ascii=False)))
    return event_id


class PastEvent(Model):
    kind: Literal["blocked", "flagged", "allowed"]
    title: str = Field(min_length=1, max_length=300)
    target_type: Literal["source", "review", "message", "delivery"]
    target_id: str = Field(min_length=1, max_length=200)
    created_at: AwareDatetime
    # Only settled notifications: a seeded alert must never look pending or authorize anything.
    status: Literal["resolved", "dismissed", "delivered", "approved"]
    resolution_note: str = Field(default="", max_length=1000)
    details: dict = Field(default_factory=dict)


class PastEvents(Model):
    events: list[PastEvent] = Field(max_length=200)


def seed(guard, items):
    """Past, already reviewed notifications for a demo workspace; restarting never duplicates them."""
    with guard.transaction():
        for item in items:
            key = json.dumps([*guard.scope, item.kind, item.target_type, item.target_id], separators=(",", ":"))
            settled = item.created_at.isoformat() if item.status in {"resolved", "dismissed"} else None
            guard.db.execute("""
                INSERT INTO activity_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(org,op,event_id) DO NOTHING
            """, (*guard.scope, hashlib.sha256(key.encode()).hexdigest()[:32], item.kind, item.title,
                  item.created_at.isoformat(), item.target_type, item.target_id, item.status,
                  json.dumps(item.details, ensure_ascii=False), settled, item.resolution_note or None))


MESSAGE_TITLES = {"allow": "Se entregó una respuesta sin cambios",
                  "repair": "Se reparó una respuesta antes de entregarla",
                  "review": "Se retuvo una respuesta para revisión humana"}


def blocked_disclosure(result):
    return any(attempt.get("blocked") or attempt.get("reason") in {
        "personal_identifier", "personal_identifier_link", "protected_literal", "confidential_node",
        "confidential_inference",
    } for attempt in result.get("attempts", []))


def message_event(guard, item, evaluated, result, screened):
    # Attempts come from the agent's original draft, which shows what was at risk before any rewrite.
    if result["action"] == "allow":
        kind, status = "allowed", "delivered"
    else:
        kind, status = ("blocked" if blocked_disclosure(screened) else "flagged"), "open"
    # Full content for the operator's audit; files are kept as metadata and digest, not bytes.
    return record(guard, kind, MESSAGE_TITLES[result["action"]], "message", f"{item.request_id}:{item.user}", {
        "origin": "message_api", "request_id": item.request_id, "recipient": item.user, "task": item.task,
        "day": item.day.isoformat(), "draft": item.draft, "candidate": evaluated, "delivered_text": result["text"],
        "attachments": [{"name": a.name, "media_type": a.media_type, "bytes": len(a.raw()), "sha256": a.digest()}
                        for a in item.attachments],
        "model": model_id(), "decision": {"action": result["action"], "attempts": screened.get("attempts", [])},
        "logs": [{"component": "rdf_problog · borrador original", "output": screened},
                 {"component": "rdf_problog · decisión final", "output": result}],
        "notice": "Registro completo para auditoría: texto evaluado, transcripción de adjuntos y traza de la decisión.",
    }, status)


def review_event(guard, review_id, revision, user, text, question, analysis, hypotheses, result):
    risk = blocked_disclosure(result)
    clean = result["action"] == "allow" and not any(h.p_if_seen > 0 for h in hypotheses)
    if clean:
        kind, title, status = "allowed", "Se aprobó una respuesta sin riesgo detectado", "approved"
    else:
        kind, status = ("blocked" if risk else "flagged"), "open"
        title = ("Se bloqueó una posible filtración de información" if risk
                 else "Se aprobó una respuesta con señales para revisar" if result["action"] == "allow"
                 else "Una respuesta contiene señales para revisar")
    return record(guard, kind, title, "review", f"{review_id}:{revision}", {
        "origin": "review_api", "review_id": review_id, "revision": revision, "recipient": user,
        "question": question, "candidate": text, "model": model_id(), "decision": result,
        "logs": [
            {"agent": "analyst", "output": analysis.model_dump(mode="json")},
            {"agent": "adversary", "output": [h.model_dump(mode="json") for h in hypotheses]},
            {"component": "rdf_problog", "output": result},
        ],
        "notice": "Registro de salidas estructuradas y decisión; no contiene razonamiento interno del proveedor.",
    }, status)


def source_event(guard, source, result):
    observations = result["internal"] + result["external"]
    hypotheses = [h for event in observations for h in event.hypotheses
                  if h.p_if_seen > 0 and (h.claim[1] == INTRINSIC_FAMILY or h.claim[1] in guard.policy.families)]
    missing = result.get("missing_recipients", [])
    sensitive = bool(hypotheses) or any(event.protected_literals for event in observations)
    unrepresented = result.get("unrepresented", [])
    if not sensitive and observations and not missing and not unrepresented:
        return
    title = ("Se detectó información potencialmente sensible en una fuente" if sensitive
             else "Una fuente tiene afirmaciones sin representar" if unrepresented else "Una fuente necesita revisión")
    details = {"origin": "source_analysis", "source_id": source.origin, "source_text": source.text,
               "model": model_id(), "missing_recipients": missing, "unrepresented_claims": unrepresented,
               "observations": [e.model_dump(mode="json") for e in observations],
               "notice": "Analizar una entrada no implica que se haya bloqueado una salida."}
    if result.get("analysis_error"):
        details["analysis_error"] = result["analysis_error"]
    digest = hashlib.sha256(json.dumps(details, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
    return record(guard, "flagged", title, "source", f"{source.origin}:{digest}", details)


def listing(guard, limit=50):
    rows = guard.db.execute("""
        SELECT event_id,kind,title,created_at,target_type,status,resolved_at
        FROM activity_events WHERE org=? AND op=? AND target_type!='mock' ORDER BY created_at DESC,event_id DESC LIMIT ?
    """, (*guard.scope, limit))
    keys = ("id", "kind", "title", "created_at", "target_type", "status", "resolved_at")
    items = [dict(zip(keys, row)) for row in rows]
    counts = {"total": 0, "blocked": 0, "flagged": 0, "allowed": 0, "open": 0}
    for kind, status, count in guard.db.execute(
        "SELECT kind,status,count(*) FROM activity_events WHERE org=? AND op=? AND target_type!='mock' GROUP BY kind,status", guard.scope):
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


# Illustrative impact mock: fake leaks live only in the Dashboard tab, never in notifications or audits.
SEVERITY_USD = {"critica": 250_000, "alta": 60_000, "media": 12_000, "baja": 2_000}
AREAS = ("Finanzas", "Legal", "RR.HH.", "Comercial", "Operaciones", "TI")
LEAK_TYPES = ("Divulgación directa", "Inferencia por mosaico", "Dato confidencial nombrado", "Identificador personal")


def seed_impact(guard, count=120, days=30):
    import random
    rand, now = random.Random(26), datetime.now(timezone.utc)
    # Reseed on each start so the mock window always ends today.
    guard.db.execute("DELETE FROM activity_events WHERE org=? AND op=? AND target_type='mock'", guard.scope)
    for i in range(count):
        severity = rand.choices(tuple(SEVERITY_USD), weights=(1, 3, 5, 6))[0]
        when = now - timedelta(days=rand.randrange(days))  # whole days keep each leak inside the window
        record(guard, "blocked", "Leak ficticio prevenido", "mock", f"mock_{i}", {
            "mock": True, "severity": severity, "area": rand.choice(AREAS),
            "leak_type": rand.choices(LEAK_TYPES, weights=(2, 4, 3, 2))[0], "loss_usd": SEVERITY_USD[severity],
        }, "approved", created_at=when.isoformat())


def impact(guard, area=None, days=30):
    today = datetime.now(timezone.utc).date()
    daily = {(today - timedelta(days=d)).isoformat(): [0, 0] for d in range(days - 1, -1, -1)}
    severities = {s: [0, 0] for s in SEVERITY_USD}
    types = {t: [0, 0] for t in LEAK_TYPES}
    for created_at, details in guard.db.execute(
            "SELECT created_at,details FROM activity_events WHERE org=? AND op=? AND target_type='mock'", guard.scope):
        item = json.loads(details)
        day = created_at[:10]
        if (area and item["area"] != area) or day not in daily:
            continue
        for bucket in (daily[day], severities[item["severity"]], types[item["leak_type"]]):
            bucket[0] += 1
            bucket[1] += item["loss_usd"]
    leaks, loss = sum(c for c, _ in severities.values()), sum(v for _, v in severities.values())
    pack = lambda table: [{"key": k, "count": c, "loss_usd": v} for k, (c, v) in table.items()]
    return {"mock": True, "areas": list(AREAS), "area": area, "severity_usd": SEVERITY_USD,
            "totals": {"leaks": leaks, "loss_usd": loss, "critical": severities["critica"][0],
                       "avg_loss_usd": round(loss / leaks) if leaks else 0},
            "severities": pack(severities), "types": pack(types), "daily": pack(daily)}
