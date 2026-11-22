"""Local inbox is the delivery boundary. Decision details are operator-only."""
import hashlib
import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from contextlib import contextmanager
from datetime import date

from rdflib import RDF

from .graphs import (E, P, R, V, describe, explain, internal_view, make_schema, observer_view, path_goals, plain,
                     probabilities, rdf_fact, role_graph, signature, user_grants, value_nodes)
from .model import (ACTOR_NODES, CONFIDENTIAL, Case, INTRINSIC_FAMILY, Observation, ObserverEstimate, Policy, RuleProposal,
                    intrinsic_family, stricter)
from .privacy import RutProtection


def fold_text(text):
    # Some labels arrive as identifiers (ensayo_clinico); underscores read as spaces.
    return " ".join(unicodedata.normalize("NFKD", text.replace("_", " ")).encode("ascii", "ignore").decode().split()).casefold()


def mentions(label, text):
    """Whole-word, accent- and case-insensitive match; labels under three characters would match anything."""
    label = fold_text(label or "")
    return len(label) >= 3 and re.search(r"(?<!\w)" + re.escape(label) + r"(?!\w)", fold_text(text)) is not None


class Guard:
    def __init__(self, db: str, policy: Policy, case: Case, *, prompts=None):
        self.policy = policy
        self.case = case
        self.prompts = prompts or {}
        if unknown := sorted({a.role for a in case.assignments} - set(policy.roles)):
            raise ValueError(f"Rol del directorio desconocido: {', '.join(unknown)}. La política solo define: "
                             f"{', '.join(policy.roles)}. Revisa que MOSAIC_POLICY y MOSAIC_CASE sean del mismo ejemplo")
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
            CREATE TABLE IF NOT EXISTS protected_literals (
                org TEXT, op TEXT, origin TEXT, day TEXT, family TEXT, value TEXT, quote TEXT,
                PRIMARY KEY(org, op, origin, day, family, value));
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
            CREATE TABLE IF NOT EXISTS kg_nodes (
                org TEXT, op TEXT, id TEXT, type TEXT, label TEXT, confidentiality TEXT, PRIMARY KEY(org, op, id));
            CREATE TABLE IF NOT EXISTS kg_aliases (
                org TEXT, op TEXT, alias TEXT, id TEXT, PRIMARY KEY(org, op, alias));
            CREATE TABLE IF NOT EXISTS path_ratings (
                org TEXT, op TEXT, signature TEXT, relation TEXT, confidence REAL, assessor TEXT,
                PRIMARY KEY(org, op, signature));
        """)
        if "confidentiality" not in {row[1] for row in self.db.execute("PRAGMA table_info(kg_nodes)")}:
            self.db.execute("ALTER TABLE kg_nodes ADD COLUMN confidentiality TEXT")

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
        for table in ("evidence", "interpretations", "proposed_rules", "proposed_vocabulary", "protected_literals"):
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
        for name in visible:
            if name not in vocabulary and name not in self.policy.families and name != INTRINSIC_FAMILY:
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
            resolve = self.resolver()
            # A node named by a protected value is that secret, whatever level the extraction gave it.
            secrets = {literal.value for event in observations for literal in event.protected_literals}
            secrets |= {value for (value,) in self.db.execute(
                "SELECT DISTINCT value FROM protected_literals WHERE org=? AND op=?", self.scope)}
            for event in observations:
                for entity in event.entities:
                    # The node must name the whole secret as words; "Banco" is not "Banco Estado 1234", "de" is no secret.
                    if any(len(value.strip()) >= 4 and mentions(value, entity.label) for value in secrets):
                        entity = entity.model_copy(update={"confidentiality": "restringido"})
                    self._register(entity, resolve)
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
                for literal in event.protected_literals:
                    if literal.family != INTRINSIC_FAMILY and literal.family not in effective.families:
                        raise ValueError("Familia desconocida para el valor protegido")
                    self.db.execute("INSERT OR IGNORE INTO protected_literals VALUES (?,?,?,?,?,?,?)",
                                    (*self.scope, "src:" + event.origin, event.at.isoformat(),
                                     literal.family, literal.value, literal.quote))
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
                    hypothesis = hypothesis.model_copy(update={"claim": self._canonical(hypothesis.claim, resolve, effective)})
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
                self._record(event.user or "", "src:" + event.origin,
                             [self._canonical(fact, resolve, effective) for fact in event.facts], event.at,
                             event.grade or "internal", 1. if event.confidence is None else event.confidence,
                             event.quote)
            if source_id is not None and not previous:
                self.db.execute("INSERT INTO source_imports VALUES (?,?,?,?)",
                                (*self.scope, source_id, source_digest))
            return True

    def resolver(self):
        """Map a mention to its node: declared alias, exact id, or the only id equal up to underscores."""
        aliases = dict(self.db.execute("SELECT alias,id FROM kg_aliases WHERE org=? AND op=?", self.scope))
        known = {row[0] for row in self.db.execute("SELECT id FROM kg_nodes WHERE org=? AND op=?", self.scope)}
        for table, column in (("evidence", "fact"), ("interpretations", "claim")):
            for (raw,) in self.db.execute(f"SELECT DISTINCT {column} FROM {table} WHERE org=? AND op=?", self.scope):
                subject, _, value = json.loads(raw)
                known.update((subject, value))
        keys = defaultdict(set)
        for name in known:
            keys[name.replace("_", "")].add(name)

        def resolve(name):
            # shortcut: underscore-insensitive matching can join ids like a_bc and ab_c; declare aliases if it bites.
            if name in aliases:
                return aliases[name]
            if name not in known and len(keys[name.replace("_", "")]) == 1:
                return next(iter(keys[name.replace("_", "")]))
            known.add(name)
            keys[name.replace("_", "")].add(name)
            return name
        resolve.aliases = aliases
        resolve.known = known
        return resolve

    def _canonical(self, fact, resolve, policy):
        subject, predicate, value = fact
        # Family states are a closed vocabulary, never entity mentions.
        if predicate in policy.families or predicate == INTRINSIC_FAMILY:
            return resolve(subject), predicate, value
        return resolve(subject), predicate, resolve(value)

    def _register(self, entity, resolve):
        node = resolve(entity.id)
        previous = self.db.execute("SELECT confidentiality FROM kg_nodes WHERE org=? AND op=? AND id=?",
                                   (*self.scope, node)).fetchone()
        # A later source can raise a node's confidentiality but never lower it.
        level = stricter(previous and previous[0], entity.confidentiality)
        self.db.execute("""
            INSERT INTO kg_nodes VALUES (?,?,?,?,?,?)
            ON CONFLICT(org,op,id) DO UPDATE SET type=CASE WHEN type='entidad' THEN excluded.type ELSE type END,
                confidentiality=excluded.confidentiality
        """, (*self.scope, node, entity.type, entity.label, level))
        for alias in entity.aliases:
            # An alias that already names another node is ambiguous: keep both nodes instead of merging them.
            if alias == node or alias in resolve.aliases or alias in resolve.known:
                continue
            self.db.execute("INSERT INTO kg_aliases VALUES (?,?,?,?)", (*self.scope, alias, node))
            resolve.aliases[alias] = node

    def labels(self):
        return dict(self.db.execute("SELECT id,label FROM kg_nodes WHERE org=? AND op=?", self.scope))

    def confidential_nodes(self):
        """Names (ids and aliases) of confidential data nodes, mapped to the node and the label a draft could quote."""
        # A confidential datum, codename or event blocks by itself; a confidential person or organization stays an
        # intrinsic-risk judgment, or every reply that names them would be held.
        nodes = {node: label for node, kind, label, level in self.db.execute(
            "SELECT id,type,label,confidentiality FROM kg_nodes WHERE org=? AND op=?", self.scope)
            if level in CONFIDENTIAL and kind not in ACTOR_NODES}
        names = {node: (node, label) for node, label in nodes.items()}
        return names | {alias: names[node] for alias, node in self.db.execute(
            "SELECT alias,id FROM kg_aliases WHERE org=? AND op=?", self.scope) if node in nodes}

    def secret_briefs(self, at):
        """Each group of linked confidential data nodes, with its names and the internal sentences about it."""
        names = self.confidential_nodes()
        nodes = {node for node, _ in names.values()}
        if not nodes:
            return []
        facts = [fact for _, fact, _ in self.records("", at)]
        group = {node: node for node in nodes}

        def root(node):
            while group[node] != node:
                node = group[node]
            return node
        for subject, _, value in facts:
            if subject in nodes and value in nodes:
                # The smallest id names the group, so the same graph always yields the same secret ids.
                first, second = sorted((root(subject), root(value)))
                group[second] = first
        labels = self.labels()
        briefs = {}
        for node in sorted(nodes):
            brief = briefs.setdefault(root(node), {"id": root(node), "nombres": [], "hechos": []})
            brief["nombres"].append(labels.get(node, node))
        for fact in facts:
            touched = {root(term) for term in (fact[0], fact[2]) if term in nodes}
            for key in touched:
                briefs[key]["hechos"] += describe([fact], labels)
        return [{**brief, "hechos": sorted(set(brief["hechos"]))} for brief in briefs.values()]

    def path_ratings(self):
        return dict(self.db.execute("SELECT signature,confidence FROM path_ratings WHERE org=? AND op=?", self.scope))

    def restricted_literals(self, user, at):
        # A caller-supplied historical date cannot bypass current restrictions.
        _, historical = user_grants(self.policy, role_graph(self.schema, self.case, at), user)
        _, current = user_grants(self.policy, role_graph(self.schema, self.case, date.today()), user)
        allowed = historical & current & self.policy.families.keys()
        return {value for family, value in self.db.execute(
            "SELECT DISTINCT family,value FROM protected_literals WHERE org=? AND op=?", self.scope
        ) if family not in allowed}

    def secrets(self, at):
        private = internal_view(self.schema, [f for _, f, _ in self.records("", at)], self.policy_at(at))
        return sorted(f for f in plain(private) if f[1] in self.policy.families)

    def unrated_paths(self, user, at, extra=()):
        """Paths toward internal secrets that neither a rule nor a stored reading explains yet."""
        policy = self.risk_policy(at)
        facts = {f for _, f, _ in self.records(user, at)} | {h.claim for h in self.hypotheses(user, at)} | set(extra)
        rated = self.path_ratings()
        return [(goal, path, signature(goal, path)) for goal, paths in path_goals(policy, facts, self.secrets(at)).items()
                for path, backed in paths if not backed and signature(goal, path) not in rated]

    def rate_paths(self, readings, assessor):
        self.db.executemany("""
            INSERT INTO path_ratings VALUES (?,?,?,?,?,?)
            ON CONFLICT(org,op,signature) DO UPDATE SET relation=excluded.relation,
                confidence=excluded.confidence, assessor=excluded.assessor
        """, [(*self.scope, sig, relation, confidence, assessor) for sig, relation, confidence in readings])

    def graph(self, user, at):
        """Nodes and labelled edges seen by one audience; '' is the internal graph."""
        rows = self.db.execute("""
            SELECT origin,fact,grade,cap,quote FROM evidence WHERE org=? AND op=? AND audience=? AND day<=?
            ORDER BY rowid
        """, (*self.scope, user, at.isoformat())).fetchall()
        edges = []
        for origin, raw, grade, cap, quote in rows:
            subject, predicate, value = json.loads(raw)
            edges.append({"source": subject, "target": value, "predicate": predicate,
                          "relation": predicate.replace("_", " "), "origin": origin, "quote": quote,
                          "weight": min(cap, self.policy.evidence.get(grade, 1.))})
        facts = {(e["source"], e["predicate"], e["target"]) for e in edges}
        values = value_nodes(self.policy_at(at), facts)
        stored = {node: (kind, label, level) for node, kind, label, level in self.db.execute(
            "SELECT id,type,label,confidentiality FROM kg_nodes WHERE org=? AND op=?", self.scope)}
        names = sorted({term for e in edges for term in (e["source"], e["target"])})
        nodes = [{"id": name, "type": "valor" if name in values else stored.get(name, ("entidad",))[0],
                  "label": stored.get(name, (None, name))[1], "confidentiality": stored.get(name, (None, None, None))[2]}
                 for name in names]
        return {"nodes": nodes, "edges": edges}

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
            entities = [{"id": node, "type": kind, "label": label, "confidentiality": level,
                         "aliases": [a for (a,) in self.db.execute(
                             "SELECT alias FROM kg_aliases WHERE org=? AND op=? AND id=? ORDER BY alias",
                             (*self.scope, node))]}
                        for node, kind, label, level in self.db.execute(
                            "SELECT id,type,label,confidentiality FROM kg_nodes WHERE org=? AND op=? ORDER BY id",
                            self.scope).fetchall()]
            return {"kind": "internal", "facts": triples, "entities": entities, "proposed_rules": proposals,
                    "proposed_vocabulary": self.proposed_vocabulary(at),
                    "possible_inferences": inferred, "config_revision": row[0] if row else 0}
        access = role_graph(self.schema, self.case, at)
        roles = sorted({name for assignment in access.subjects(V.user, E[user])
                        for name in self.policy.roles if (assignment, RDF.type, R[name]) in access})
        hints = [{"fact": fact, "p_known": weight} for _, fact, weight in self.records(user, at)]
        estimates = [h.model_dump(mode="json") for h in self.hypotheses(user, at)]
        view = {"kind": "observer", "roles": roles, "possible_knowledge": hints, "hypotheses": estimates}
        messages = [
            {"at": day, "text": text}
            for day, text in self.db.execute("""
                SELECT day,json_extract(decision, '$.text') FROM deliveries
                WHERE org=? AND op=? AND audience=? AND day<=?
                    AND json_extract(decision, '$.action') IN ('allow', 'repair')
                    AND json_extract(decision, '$.text') IS NOT NULL
                ORDER BY rowid
            """, (*self.scope, user, at.isoformat()))
        ]
        if messages:
            view["delivered_messages"] = messages
        return view

    def render(self, option):
        return " ".join(self.case.clauses[key].text for key in option.clauses)

    def _decide(self, user, task_id, at, draft, assessments, assessor, *, strict=False,
                verify_all_facts=True, disclosed=()):
        if task_id not in self.case.tasks:
            raise ValueError(f"Tarea desconocida: {task_id}")
        task = self.case.tasks[task_id]
        private_facts = [fact for _, fact, _ in self.records("", at)]
        private = internal_view(self.schema, private_facts, self.policy_at(at))
        records = self.records(user, at)
        rut_protection = RutProtection(private_facts, records)
        historical = self.hypotheses(user, at)
        risk_policy = self.risk_policy(at)
        secrets = sorted(f for f in plain(private) if f[1] in self.policy.families)
        ratings = self.path_ratings()
        labels = self.labels()
        baseline = probabilities(risk_policy, observer_view(records, historical), secrets, ratings)
        _, allowed = user_grants(self.policy, role_graph(self.schema, self.case, at), user)
        restricted_literals = self.restricted_literals(user, at)
        confidential = self.confidential_nodes()
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
            if any(value in text for value in restricted_literals):
                attempts.append({"reason": "protected_literal"})
                continue
            # A confidential datum is restricted for everyone, like an intrinsic secret: no role receives it.
            named = {confidential[term][0] for fact in facts for term in (fact[0], fact[2]) if term in confidential}
            # The text check covers a draft whose extracted facts missed the node.
            named |= {node for node, label in confidential.values() if mentions(label, text)}
            if named:
                attempts.append({"reason": "confidential_node", "nodes": sorted(named)})
                continue
            # The secret judge read this exact text; its finding holds the draft like a named secret.
            if disclosed:
                attempts.append({"reason": "confidential_inference", "nodes": sorted(disclosed)})
                continue
            privacy_reason = rut_protection.reason(text, facts)
            if privacy_reason:
                attempts.append({"reason": privacy_reason})
                continue
            ambiguous = any(predicate in self.policy.families and
                            len(set(private.objects(E[subject], P[predicate]))) > 1
                            for subject, predicate, _ in facts)
            unverified = ambiguous or any(
                rdf_fact(fact) not in private for fact in facts
                if verify_all_facts or fact[1] in self.policy.families
            )
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
            after = probabilities(risk_policy, observer_view(possible, historical + projected), secrets, ratings)
            seen = [fact for _, fact, _ in possible] + [h.claim for h in historical + projected]
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
                    entry["paths"] = explain(risk_policy, seen, goal, labels)
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
