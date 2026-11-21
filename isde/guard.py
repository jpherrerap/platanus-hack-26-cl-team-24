"""Local inbox is the delivery boundary. Decision details are operator-only."""
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import date

from rdflib import RDF

from .graphs import E, P, R, V, internal_view, make_schema, observer_view, probabilities, rdf_fact, role_graph, user_grants
from .model import Case, Observation, Policy


class Guard:
    def __init__(self, db: str, policy: Policy, case: Case):
        self.policy = policy
        self.case = case
        if any(a.role not in policy.roles for a in case.assignments):
            raise ValueError("Rol del directorio desconocido")
        self.schema = make_schema(policy)
        self.scope = (case.org, case.operation)
        try:
            self.db = sqlite3.connect(db, isolation_level=None, timeout=15)
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS evidence (
                    org TEXT, op TEXT, audience TEXT, origin TEXT, fact TEXT, day TEXT,
                    grade TEXT, cap REAL, quote TEXT, PRIMARY KEY(org, op, audience, origin, fact, day, grade));
                CREATE TABLE IF NOT EXISTS deliveries (
                    org TEXT, op TEXT, audience TEXT, request_id TEXT, task TEXT, draft_hash TEXT, day TEXT,
                    decision TEXT, PRIMARY KEY(org, op, audience, request_id));
            """)
        except sqlite3.Error as exc:
            raise ValueError(f"No se puede abrir la base de datos {db}") from exc

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def close(self):
        self.db.close()

    def _record(self, user, origin, facts, at, grade, cap=1., quote=None):
        self.db.executemany("""
            INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(org,op,audience,origin,fact,day,grade)
            DO UPDATE SET cap=max(cap,excluded.cap), quote=coalesce(excluded.quote,quote)
        """, [(*self.scope, user, origin, json.dumps(f), at.isoformat(), grade, cap, quote) for f in facts])

    def observe(self, observations: list[Observation]):
        with self.transaction():
            for event in observations:
                permitted = self.policy.vocabulary.keys() | self.policy.families.keys()
                if any(predicate not in permitted for _, predicate, _ in event.facts):
                    raise ValueError("Predicado fuera del vocabulario")
                for _, predicate, value in event.facts:
                    if predicate in self.policy.families and value not in self.policy.families[predicate].statuses:
                        raise ValueError("Estado fuera de la familia protegida")
                if event.grade == "possible_access":
                    roles = role_graph(self.schema, self.case, event.at)
                    readable, _ = user_grants(self.policy, roles, event.user)
                    if event.resource not in readable:
                        continue
                self._record(event.user or "", "src:" + event.origin, event.facts, event.at,
                             event.grade or "internal", 1. if event.confidence is None else event.confidence,
                             event.quote)

    def records(self, user, at):
        rows = self.db.execute("""
            SELECT origin,fact,grade,cap FROM evidence
            WHERE org=? AND op=? AND audience=? AND day<=?
        """, (*self.scope, user, at.isoformat()))
        return [(origin, tuple(json.loads(fact)), min(cap, self.policy.evidence.get(grade, 1.)))
                for origin, fact, grade, cap in rows]

    def model_context(self, user, at):
        """Export only the ontology this model is allowed to see as of the source date."""
        if user is None:
            private = internal_view(self.schema, (f for _, f, _ in self.records("", at)))
            triples = sorted((str(s).removeprefix(str(E)), str(p).removeprefix(str(P)),
                              str(o).removeprefix(str(E))) for s, p, o in private
                             if str(p).startswith(str(P)))
            return {"kind": "internal", "facts": triples}
        access = role_graph(self.schema, self.case, at)
        roles = sorted({name for assignment in access.subjects(V.user, E[user])
                        for name in self.policy.roles if (assignment, RDF.type, R[name]) in access})
        hints = [{"fact": fact, "p_known": weight} for _, fact, weight in self.records(user, at)]
        return {"kind": "observer", "roles": roles, "possible_knowledge": hints}

    def _decide(self, user, task_id, at, draft):
        if task_id not in self.case.tasks:
            raise ValueError(f"Tarea desconocida: {task_id}")
        task = self.case.tasks[task_id]
        private = internal_view(self.schema, (f for _, f, _ in self.records("", at)))
        records = self.records(user, at)
        baseline = probabilities(self.policy, observer_view(records))
        _, allowed = user_grants(self.policy, role_graph(self.schema, self.case, at), user)
        attempts = []
        options = list(task.options)
        if draft is not None:
            def render(option):
                return " ".join(self.case.clauses[key].text for key in option.clauses)

            # Agents and shells add stray spaces or newlines; compare on normalized whitespace.
            wanted = " ".join(draft.split())
            match = next((option for option in options if " ".join(render(option).split()) == wanted), None)
            if match is None:
                attempts.append({"reason": "unmapped_draft"})
                # For an unrecognized draft, try the shortest approved answer that completes the task.
                options.sort(key=lambda option: len(render(option)))
            else:
                options.remove(match)
                options.insert(0, match)
        for option in options:
            clauses = [self.case.clauses[k] for k in option.clauses]
            if set(task.requires) - {s for clause in clauses for s in clause.provides}:
                attempts.append({"reason": "incomplete_task"})
                continue
            facts = {f for clause in clauses for f in clause.facts}
            ambiguous = any(predicate in self.policy.families and
                            len(set(private.objects(E[subject], P[predicate]))) > 1
                            for subject, predicate, _ in facts)
            if ambiguous or any(rdf_fact(f) not in private for f in facts):
                attempts.append({"reason": "unverified_content"})
                continue
            # A repeated disclosure of the same facts shares one exposure origin.
            exposed = []
            for clause in clauses:
                payload = json.dumps(sorted(clause.facts), separators=(",", ":"))
                origin = "out:" + hashlib.sha256(payload.encode()).hexdigest()
                exposed.extend((origin, fact, self.policy.evidence["delivered"]) for fact in clause.facts)
            possible = records + exposed
            after = probabilities(self.policy, observer_view(possible))
            blocked, changes = [], []
            for goal, posterior in sorted(after.items()):
                subject, family, status = goal
                if family in allowed:
                    continue
                prior = baseline.get(goal, 0.)
                delta = posterior - prior
                rule = self.policy.families[family]
                direct = goal in facts
                entry = dict(subject=subject, family=family, status=status, before=prior,
                             after=posterior, increase=delta, direct=direct,
                             internally_supported=rdf_fact(goal) in private)
                if delta > 1e-9 or direct:
                    changes.append(entry)
                if direct or (delta > 1e-9 and (posterior >= rule.absolute or delta >= rule.increase)):
                    blocked.append(entry)
            attempts.append({"changes": changes, "blocked": blocked})
            if not blocked:
                text = " ".join(c.text for c in clauses)
                decision = dict(action="allow" if len(attempts) == 1 else "repair", text=text, attempts=attempts)
                return decision, exposed
        return dict(action="review", text=None, attempts=attempts), None

    def run(self, user: str, task: str, at: date, request_id: str | None = None, *, draft: str | None = None):
        if not user or not task or (request_id is not None and not request_id.strip()):
            raise ValueError("Destinatario, tarea o identificador vacío")
        # A typo would otherwise start a fresh, empty history for a recipient that does not exist.
        if user not in {a.user for a in self.case.assignments if (a.org, a.operation) == self.scope}:
            raise ValueError(f"Destinatario fuera del directorio: {user}")
        draft_hash = hashlib.sha256(draft.encode()).hexdigest() if draft is not None else None
        with self.transaction():
            if request_id is not None:
                previous = self.db.execute("""
                    SELECT task,draft_hash,decision FROM deliveries
                    WHERE org=? AND op=? AND audience=? AND request_id=?
                """, (*self.scope, user, request_id)).fetchone()
                if previous:
                    if previous[:2] != (task, draft_hash):
                        raise ValueError("Identificador de entrega reutilizado para otra solicitud")
                    return json.loads(previous[2])
                latest = self.db.execute("SELECT max(day) FROM deliveries WHERE org=? AND op=? AND audience=?",
                                         (*self.scope, user)).fetchone()[0]
                if latest and at.isoformat() < latest:
                    raise ValueError("Una entrega nueva debe respetar el orden del historial")
            result, exposure = self._decide(user, task, at, draft)
            # A review is recorded too, so a retry returns it and the id cannot be reused for another request.
            if request_id is not None:
                for origin, fact, _ in exposure or []:
                    self._record(user, origin, [fact], at, "delivered")
                self.db.execute("INSERT INTO deliveries VALUES (?,?,?,?,?,?,?,?)",
                                (*self.scope, user, request_id, task, draft_hash, at.isoformat(), json.dumps(result)))
            return result

    def inbox(self, user):
        rows = self.db.execute("""
            SELECT request_id,decision FROM deliveries
            WHERE org=? AND op=? AND audience=? ORDER BY rowid
        """, (*self.scope, user))
        # Reviewed requests are recorded without text; the recipient never sees them.
        return [{"id": key, "text": text} for key, raw in rows if (text := json.loads(raw)["text"]) is not None]
