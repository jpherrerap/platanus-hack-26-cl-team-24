"""Local inbox is the delivery boundary. Decision details are operator-only."""
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import date

from rdflib import RDF

from .graphs import E, P, R, V, internal_view, make_schema, observer_view, probabilities, rdf_fact, role_graph, user_grants
from .model import Case, INTRINSIC_FAMILY, Observation, ObserverEstimate, Policy, RuleProposal, intrinsic_family
from .privacy import RutProtection


class Guard:
    def __init__(self, db: str, policy: Policy, case: Case, *, prompts=None):
        self.policy = policy
        self.case = case
        self.prompts = prompts or {}
        if any(a.role not in policy.roles for a in case.assignments):
            raise ValueError("Rol del directorio desconocido")
        self.schema = make_schema(policy)
        self.scope = (case.org, case.operation)
        try:
            self.db = sqlite3.connect(db, isolation_level=None, timeout=15)
        except sqlite3.Error as exc:
            raise ValueError("No se puede abrir la base de datos") from exc
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS evidence (
                org TEXT, op TEXT, audience TEXT, origin TEXT, fact TEXT, day TEXT,
                grade TEXT, cap REAL, quote TEXT, PRIMARY KEY(org, op, audience, origin, fact, day, grade));
            CREATE TABLE IF NOT EXISTS interpretations (
                org TEXT, op TEXT, audience TEXT, origin TEXT, claim TEXT, day TEXT, grade TEXT,
                cap REAL, p_if_seen REAL, quote TEXT, assessor TEXT,
                PRIMARY KEY(org, op, audience, origin, claim, day, grade, quote, assessor));
            CREATE TABLE IF NOT EXISTS proposed_rules (
                org TEXT, op TEXT, origin TEXT, day TEXT, rule TEXT, quote TEXT,
                confidence REAL, assessor TEXT,
                PRIMARY KEY(org, op, origin, day, rule, quote, assessor));
            CREATE TABLE IF NOT EXISTS proposed_vocabulary (
                org TEXT, op TEXT, predicate TEXT, description TEXT, day TEXT,
                origin TEXT, quote TEXT, confidence REAL, assessor TEXT,
                PRIMARY KEY(org, op, predicate));
            CREATE TABLE IF NOT EXISTS source_imports (
                org TEXT, op TEXT, source_id TEXT, digest TEXT,
                PRIMARY KEY(org, op, source_id));
            CREATE TABLE IF NOT EXISTS dashboard_config (
                org TEXT, op TEXT, policy TEXT, prompts TEXT, revision INTEGER,
                PRIMARY KEY(org, op));
            CREATE TABLE IF NOT EXISTS activity_events (
                org TEXT, op TEXT, event_id TEXT, kind TEXT, title TEXT, created_at TEXT,
                target_type TEXT, target_id TEXT, status TEXT, details TEXT, resolved_at TEXT, resolution_note TEXT,
                PRIMARY KEY(org, op,event_id));
            CREATE TABLE IF NOT EXISTS semantic_reviews (
                org TEXT, op TEXT, review_id TEXT, user TEXT, task TEXT, day TEXT, question TEXT,
                original_hash TEXT, revision INTEGER, text TEXT, analysis TEXT,
                hypotheses TEXT, snapshot TEXT, status TEXT, trace TEXT,
                PRIMARY KEY(org, op, review_id));
            CREATE TABLE IF NOT EXISTS semantic_revisions (
                org TEXT, op TEXT, review_id TEXT, revision_id TEXT, digest TEXT, response TEXT,
                PRIMARY KEY(org, op, review_id, revision_id));
            CREATE TABLE IF NOT EXISTS deliveries (
                org TEXT, op TEXT, audience TEXT, request_id TEXT, task TEXT, draft_hash TEXT, day TEXT,
                decision TEXT, PRIMARY KEY(org, op, audience, request_id));
        """)

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

    def _record_hypothesis(self, user, origin, at, grade, cap, hypothesis, assessor):
        self.db.execute("""
            INSERT INTO interpretations VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(org,op,audience,origin,claim,day,grade,quote,assessor)
            DO UPDATE SET p_if_seen=excluded.p_if_seen, cap=excluded.cap
        """, (*self.scope, user, origin, json.dumps(hypothesis.claim), at.isoformat(),
              grade, cap, hypothesis.p_if_seen, hypothesis.quote, assessor))

    def hypotheses(self, user, at):
        rows = self.db.execute("""
            SELECT origin,claim,day,grade,cap,p_if_seen,quote,assessor FROM interpretations
            WHERE org=? AND op=? AND audience=? AND day<=? ORDER BY rowid
        """, (*self.scope, user, at.isoformat()))
        return [ObserverEstimate(origin=origin, claim=json.loads(claim), at=day, quote=quote,
                                 assessor=assessor, p_if_seen=conditional,
                                 p_seen=min(cap, self.policy.evidence[grade]))
                for origin, claim, day, grade, cap, conditional, quote, assessor in rows]

    def source_has_content(self, source_id):
        origin = "src:" + source_id
        for table in ("evidence", "interpretations", "proposed_rules", "proposed_vocabulary"):
            if self.db.execute(f"SELECT 1 FROM {table} WHERE org=? AND op=? AND origin=? LIMIT 1",
                               (*self.scope, origin)).fetchone():
                return True
        return False

    def proposed_vocabulary(self, at):
        rows = self.db.execute("""
            SELECT predicate,description,day,origin,quote,confidence,assessor FROM proposed_vocabulary
            WHERE org=? AND op=? AND day<=? ORDER BY predicate
        """, (*self.scope, at.isoformat()))
        return [dict(predicate=predicate, description=description, at=day, origin=origin, quote=quote,
                     confidence=confidence, assessor=assessor)
                for predicate, description, day, origin, quote, confidence, assessor in rows]

    def policy_at(self, at):
        vocabulary = dict(self.policy.vocabulary)
        for item in self.proposed_vocabulary(at):
            if item["predicate"] not in vocabulary and item["predicate"] not in self.policy.families:
                vocabulary[item["predicate"]] = item["description"]
        return self.policy.model_copy(update={"vocabulary": vocabulary})

    def recipient_policy(self, user, at):
        # Never expose descriptions learned solely from internal sources to the recipient model.
        vocabulary = dict(self.policy.vocabulary)
        visible = {fact[1] for _, fact, _ in self.records(user, at)}
        visible |= {h.claim[1] for h in self.hypotheses(user, at)}
        learned = {item["predicate"] for item in self.proposed_vocabulary(at)}
        for name in visible & learned:
            if name not in vocabulary and name not in self.policy.families:
                vocabulary[name] = "Relación visible en el contexto de este destinatario."
        return self.policy.model_copy(update={"vocabulary": vocabulary})

    def observe(self, observations: list[Observation], *, source_id=None, source_digest=None):
        with self.transaction():
            previous = None
            if source_id is not None:
                previous = self.db.execute(
                    "SELECT digest FROM source_imports WHERE org=? AND op=? AND source_id=?",
                    (*self.scope, source_id)).fetchone()
                if previous:
                    if previous[0] != source_digest:
                        raise ValueError("Identificador de fuente usado con otro contenido")
                    if self.source_has_content(source_id) or not observations:
                        return False
                if not observations:
                    return False
            for event in sorted(observations, key=lambda event: not bool(event.vocabulary)):
                for proposal in event.vocabulary:
                    proposal.validate_source(event.quote)
                    if proposal.predicate in self.policy.vocabulary or proposal.predicate in self.policy.families:
                        raise ValueError("El concepto propuesto ya está definido en la política")
                    existing = self.db.execute("""
                        SELECT description,day FROM proposed_vocabulary WHERE org=? AND op=? AND predicate=?
                    """, (*self.scope, proposal.predicate)).fetchone()
                    if existing and (existing[0] != proposal.description or existing[1] > event.at.isoformat()):
                        raise ValueError("El identificador del concepto ya está en uso")
                    if not existing:
                        self.db.execute("INSERT INTO proposed_vocabulary VALUES (?,?,?,?,?,?,?,?,?)",
                                        (*self.scope, proposal.predicate, proposal.description, event.at.isoformat(),
                                         "src:" + event.origin, proposal.quote, proposal.confidence, event.assessor))
                effective = self.policy_at(event.at)
                permitted = effective.vocabulary.keys() | effective.families.keys()
                if any(predicate not in permitted for _, predicate, _ in event.facts):
                    raise ValueError("Predicado fuera del vocabulario")
                for _, predicate, value in event.facts:
                    if predicate in effective.families and not effective.families[predicate].accepts(value):
                        raise ValueError("Estado fuera de la familia protegida")
                if event.grade == "possible_access":
                    roles = role_graph(self.schema, self.case, event.at)
                    readable, _ = user_grants(self.policy, roles, event.user)
                    if event.resource not in readable:
                        continue
                for hypothesis in event.hypotheses:
                    hypothesis.validate_source(effective, event.quote)
                    self._record_hypothesis(event.user, "src:" + event.origin, event.at, event.grade,
                                            1. if event.confidence is None else event.confidence,
                                            hypothesis, event.assessor)
                for proposal in event.rules:
                    proposal.validate_source(effective, event.quote)
                    self.db.execute("""
                        INSERT INTO proposed_rules VALUES (?,?,?,?,?,?,?,?)
                        ON CONFLICT(org,op,origin,day,rule,quote,assessor)
                        DO UPDATE SET confidence=excluded.confidence
                    """, (*self.scope, "src:" + event.origin, event.at.isoformat(),
                          json.dumps(proposal.rule.model_dump(), sort_keys=True), proposal.quote,
                          proposal.confidence, event.assessor))
                self._record(event.user or "", "src:" + event.origin, event.facts, event.at,
                             event.grade or "internal", 1. if event.confidence is None else event.confidence,
                             event.quote)
            if source_id is not None and not previous:
                self.db.execute("INSERT INTO source_imports VALUES (?,?,?,?)",
                                (*self.scope, source_id, source_digest))
            return True

    def records(self, user, at):
        rows = self.db.execute("""
            SELECT origin,fact,grade,cap FROM evidence
            WHERE org=? AND op=? AND audience=? AND day<=?
        """, (*self.scope, user, at.isoformat()))
        return [(origin, tuple(json.loads(fact)), min(cap, self.policy.evidence.get(grade, 1.)))
                for origin, fact, grade, cap in rows]

    def proposed_rules(self, at):
        rows = self.db.execute("""
            SELECT origin,day,rule,quote,confidence,assessor FROM proposed_rules
            WHERE org=? AND op=? AND day<=? ORDER BY origin,day,rule,quote,assessor
        """, (*self.scope, at.isoformat()))
        result = []
        for origin, day, rule, quote, confidence, assessor in rows:
            proposal = RuleProposal.model_validate({"rule": json.loads(rule), "quote": quote,
                                                    "confidence": confidence})
            proposal.rule.validate_policy(self.policy_at(at))
            result.append({"origin": origin, "at": day, **proposal.model_dump(mode="json"),
                           "assessor": assessor})
        return result

    def risk_policy(self, at):
        proposals = self.proposed_rules(at)
        effective = self.policy_at(at)
        rules = list(effective.rules)
        seen = {json.dumps(rule.model_dump(), sort_keys=True) for rule in rules}
        for item in proposals:
            key = json.dumps(item["rule"], sort_keys=True)
            if key not in seen:
                rules.append(RuleProposal.model_validate({k: item[k] for k in ("rule", "quote", "confidence")}).rule)
                seen.add(key)
        families = {**effective.families, INTRINSIC_FAMILY: intrinsic_family()}
        return effective.model_copy(update={"rules": rules, "families": families})

    def model_context(self, user, at):
        """Export only the ontology this model is allowed to see as of the source date."""
        if user is None:
            facts = [f for _, f, _ in self.records("", at)]
            private = internal_view(self.schema, facts, self.policy_at(at))
            triples = sorted((str(s).removeprefix(str(E)), str(p).removeprefix(str(P)),
                              str(o).removeprefix(str(E))) for s, p, o in private
                             if str(p).startswith(str(P)))
            proposals = self.proposed_rules(at)
            potential = internal_view(self.schema, facts, self.risk_policy(at)) if proposals else private
            inferred = sorted((str(s).removeprefix(str(E)), str(p).removeprefix(str(P)),
                               str(o).removeprefix(str(E))) for s, p, o in potential
                              if str(p).startswith(str(P)) and (s, p, o) not in private)
            row = self.db.execute("SELECT revision FROM dashboard_config WHERE org=? AND op=?", self.scope).fetchone()
            return {"kind": "internal", "facts": triples, "proposed_rules": proposals,
                    "proposed_vocabulary": self.proposed_vocabulary(at),
                    "possible_inferences": inferred, "config_revision": row[0] if row else 0}
        access = role_graph(self.schema, self.case, at)
        roles = sorted({name for assignment in access.subjects(V.user, E[user])
                        for name in self.policy.roles if (assignment, RDF.type, R[name]) in access})
        hints = [{"fact": fact, "p_known": weight} for _, fact, weight in self.records(user, at)]
        estimates = [h.model_dump(mode="json") for h in self.hypotheses(user, at)]
        return {"kind": "observer", "roles": roles, "possible_knowledge": hints, "hypotheses": estimates}

    def render(self, option):
        return " ".join(self.case.clauses[key].text for key in option.clauses)

    def _decide(self, user, task_id, at, draft, assessments, assessor, *, strict=False):
        if task_id not in self.case.tasks:
            raise ValueError(f"Tarea desconocida: {task_id}")
        task = self.case.tasks[task_id]
        private_facts = [fact for _, fact, _ in self.records("", at)]
        private = internal_view(self.schema, private_facts, self.policy_at(at))
        records = self.records(user, at)
        rut_protection = RutProtection(private_facts, records)
        historical = self.hypotheses(user, at)
        risk_policy = self.risk_policy(at)
        baseline = probabilities(risk_policy, observer_view(records, historical))
        _, allowed = user_grants(self.policy, role_graph(self.schema, self.case, at), user)
        attempts = []
        options = list(task.options)
        if draft is not None:
            wanted = " ".join(draft.split())
            match = next((o for o in options if " ".join(self.render(o).split()) == wanted), None)
            if strict and match is None:
                return {"action": "review", "text": None, "attempts": [{"reason": "unmapped_draft"}]}, None
            if strict:
                options = [match]
            elif match is None:
                attempts.append({"reason": "unmapped_draft"})
                options.sort(key=lambda option: len(self.render(option)))
            else:
                options.remove(match)
                options.insert(0, match)
        for option in options:
            clauses = [self.case.clauses[k] for k in option.clauses]
            if set(task.requires) - {s for clause in clauses for s in clause.provides}:
                attempts.append({"reason": "incomplete_task"})
                continue
            facts = {f for clause in clauses for f in clause.facts}
            text = " ".join(c.text for c in clauses)
            privacy_reason = rut_protection.reason(text, facts)
            if privacy_reason:
                attempts.append({"reason": privacy_reason})
                continue
            ambiguous = any(predicate in self.policy.families and
                            len(set(private.objects(E[subject], P[predicate]))) > 1
                            for subject, predicate, _ in facts)
            unverified = ambiguous or any(rdf_fact(f) not in private for f in facts)
            # A repeated disclosure of the same facts shares one exposure origin.
            exposed = []
            clause_origins = {}
            for clause in clauses:
                payload = json.dumps(sorted(clause.facts), separators=(",", ":"))
                origin = "out:" + hashlib.sha256((payload if clause.facts else clause.text).encode()).hexdigest()
                clause_origins[clause.text] = origin
                exposed.extend((origin, fact, self.policy.evidence["delivered"]) for fact in clause.facts)
            possible = records + exposed
            projected = []
            for hypothesis in assessments.get(text, []):
                origin = next((source for clause_text, source in clause_origins.items()
                               if hypothesis.quote in clause_text), None)
                if origin is None:
                    origin = "out-text:" + hashlib.sha256(text.encode()).hexdigest()
                projected.append(ObserverEstimate(**hypothesis.model_dump(), origin=origin,
                                                  at=at, assessor=assessor, p_seen=self.policy.evidence["delivered"]))
            after = probabilities(risk_policy, observer_view(possible, historical + projected))
            blocked, changes = [], []
            for goal, posterior in sorted(after.items()):
                subject, family, status = goal
                if family in allowed:
                    continue
                prior = baseline.get(goal, 0.)
                delta = posterior - prior
                rule = risk_policy.families[family]
                direct = goal in facts
                entry = dict(subject=subject, family=family, status=status, before=prior,
                             after=posterior, increase=delta, direct=direct,
                             internally_supported=rdf_fact(goal) in private)
                intrinsic_risk = family == INTRINSIC_FAMILY and any(
                    h.claim == goal and h.p_seen * h.p_if_seen >= rule.increase for h in projected)
                if delta > 1e-9 or direct or intrinsic_risk:
                    changes.append(entry)
                if direct or intrinsic_risk or (delta > 1e-9 and (posterior >= rule.absolute or delta >= rule.increase)):
                    blocked.append(entry)
            attempt = {"changes": changes, "blocked": blocked}
            if projected:
                attempt["hypotheses"] = [h.model_dump(mode="json") for h in projected]
            attempts.append(attempt)
            if not blocked and unverified:
                attempt["reason"] = "unverified_content"
                continue
            if not blocked:
                decision = dict(action="allow" if len(attempts) == 1 else "repair", text=text, attempts=attempts)
                return decision, (exposed, projected)
        return dict(action="review", text=None, attempts=attempts), None

    def _previous(self, user, request_id, task, draft_hash):
        if request_id is None:
            return None
        previous = self.db.execute("""
            SELECT task,draft_hash,decision FROM deliveries
            WHERE org=? AND op=? AND audience=? AND request_id=?
        """, (*self.scope, user, request_id)).fetchone()
        if previous:
            if previous[:2] != (task, draft_hash):
                raise ValueError("Identificador de entrega reutilizado para otra solicitud")
            return json.loads(previous[2])

    def run(self, user: str, task: str, at: date, request_id: str | None = None, *, draft: str | None = None,
            assess=False, client=None, model=None, strict=False, request_fingerprint=None, rewritten=False):
        if not user or not task or (request_id is not None and not request_id.strip()):
            raise ValueError("Destinatario, tarea o identificador vacío")
        if user not in {a.user for a in self.case.assignments if (a.org, a.operation) == self.scope}:
            raise ValueError(f"Destinatario fuera del directorio: {user}")
        draft_hash = request_fingerprint or (hashlib.sha256(draft.encode()).hexdigest() if draft is not None else None)
        assessments, context, internal_context, assessor = {}, None, None, None
        if assess or self.policy.assess_drafts:
            from .claude import assess_hypotheses, model_id
            with self.transaction():
                previous = self._previous(user, request_id, task, draft_hash)
                if previous:
                    return previous
                context = self.model_context(user, at)
                internal_context = self.model_context(None, at)
            assessor = model_id(model)
            for option in self.case.tasks[task].options:
                text = " ".join(self.case.clauses[key].text for key in option.clauses)
                if text in assessments:
                    continue
                try:
                    policy = self.recipient_policy(user, at)
                    assessments[text] = assess_hypotheses(policy, text, context, client=client, model=assessor,
                                                          prompt=self.prompts.get("adversary"))
                except Exception:
                    # A provider outage or malformed assessment leaves the inbox untouched.
                    return {"action": "review", "text": None, "attempts": [{"reason": "assessment_unavailable"}]}
        with self.transaction():
            previous = self._previous(user, request_id, task, draft_hash)
            if previous:
                return previous
            if context is not None and (context != self.model_context(user, at)
                                        or internal_context != self.model_context(None, at)):
                return {"action": "review", "text": None, "attempts": [{"reason": "observer_context_changed"}]}
            if request_id is not None:
                latest = self.db.execute("SELECT max(day) FROM deliveries WHERE org=? AND op=? AND audience=?",
                                         (*self.scope, user)).fetchone()[0]
                if latest and at.isoformat() < latest:
                    raise ValueError("Una entrega nueva debe respetar el orden del historial")
            result, exposure = self._decide(user, task, at, draft, assessments, assessor, strict=strict)
            if rewritten and result["action"] == "allow":
                result["action"] = "repair"
            if request_id is not None:
                if exposure is not None:
                    exposed, projections = exposure
                    for h in projections:
                        self._record_hypothesis(user, h.origin, at, "delivered", 1., h, assessor)
                    for origin, fact, _ in exposed:
                        self._record(user, origin, [fact], at, "delivered")
                self.db.execute("INSERT INTO deliveries VALUES (?,?,?,?,?,?,?,?)",
                                (*self.scope, user, request_id, task, draft_hash, at.isoformat(), json.dumps(result)))
            return result

    def inbox(self, user):
        rows = self.db.execute("""
            SELECT request_id,decision FROM deliveries
            WHERE org=? AND op=? AND audience=? ORDER BY rowid
        """, (*self.scope, user))
        return [{"id": key, "text": text} for key, raw in rows if (text := json.loads(raw)["text"]) is not None]
