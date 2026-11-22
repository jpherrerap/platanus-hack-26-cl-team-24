"""Text analysis and recipient-specific inference for the two LangGraph workflows."""
import hashlib
from datetime import date
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import Field

from .claude import CANONICAL_IDS, Source, _request, assess_hypotheses, model_id, propose, read_path, retry_invalid
from .graphs import describe
from .guard import Guard
from .model import Clause, Hypothesis, INTRINSIC_FAMILY, Model, Observation, Option, ProtectedLiteral, Task, Triple
from .prompts import DEFAULT_PROMPTS


class CandidateFact(Model):
    fact: Triple
    quote: str = Field(min_length=1)


EMAIL_TASK = "email"
FREE_TASKS = {"freeform", EMAIL_TASK}  # Reviews that answer no task of the case.


class DraftFacts(Model):
    facts: list[CandidateFact] = Field(max_length=64)


class DraftAssessment(DraftFacts):
    # Older reviews stored these fields alongside the extracted facts.
    covers_all_claims: bool | None = None
    fulfills_task: bool | None = None
    has_factual_claims: bool = True


class ClaudeAnalysis:
    def __init__(self, prompts=None):
        self.prompts = prompts if prompts is not None else dict(DEFAULT_PROMPTS)

    def analyze(self, policy, text, _question):
        context = {"text": text, "vocabulary": policy.vocabulary,
                   "protected_families": policy.model_dump()["families"]}
        role = (self.prompts["analyst"] + " Una familia con any_value true admite cualquier valor con identificador válido."
                + CANONICAL_IDS)
        return retry_invalid(lambda correction: _request(role + correction, context, DraftFacts, None, None))

    def adversarial(self, policy, text, view):
        return assess_hypotheses(policy, text, view, prompt=self.prompts["adversary"])

    def source(self, policy, source, grade, context):
        return propose(policy, source, grade, graph_context=context,
                       internal_prompt=self.prompts["ontology"], adversarial_prompt=self.prompts["adversary"])

    def read_path(self, policy, goal, sentences):
        return read_path(policy, goal, sentences)


class SourceInput(Model):
    origin: str = Field(pattern=r"^[a-z][a-z0-9_]{0,95}$")
    at: date
    text: str = Field(min_length=1, max_length=12000)
    recipients: list[str] = Field(default_factory=list, max_length=20)
    protected_literals: list[ProtectedLiteral] = Field(default_factory=list, max_length=64)

    def digest(self):
        excluded = set() if self.protected_literals else {"protected_literals"}
        return hashlib.sha256(self.model_dump_json(exclude=excluded).encode()).hexdigest()


class SourceState(TypedDict, total=False):
    source: SourceInput
    internal: list
    external: list
    count: int
    accept: bool
    digest: str
    accepted: bool
    missing_recipients: list[str]


def source_graph(guard: Guard, model: ClaudeAnalysis):
    def internal(state):
        source = state["source"]
        proposal = Source(origin=source.origin, at=source.at, text=source.text)
        policy = guard.policy_at(source.at)
        for protected in source.protected_literals:
            protected.validate_source(source.text)
            if protected.family != INTRINSIC_FAMILY and protected.family not in policy.families:
                raise ValueError("Familia protegida desconocida")
        declared_values = {protected.value for protected in source.protected_literals}
        events = []
        vocabulary = {}
        for event in model.source(policy, proposal, None, guard.model_context(None, source.at)):
            if event.origin != source.origin or event.at != source.at or event.user is not None:
                raise ValueError("La propuesta no coincide con su fuente")
            for protected in event.protected_literals:
                protected.validate_source(source.text)
                if protected.family != INTRINSIC_FAMILY:
                    raise ValueError("La extracción no puede asignar permisos a valores protegidos")
            for concept in event.vocabulary:
                concept.validate_source(source.text)
                concept.validate_source(event.quote)
                if concept.predicate in vocabulary:
                    raise ValueError("Concepto propuesto más de una vez")
                if concept.predicate in policy.vocabulary or concept.predicate in policy.families:
                    raise ValueError("El concepto propuesto ya está definido en la política")
                vocabulary[concept.predicate] = concept.description
            if event.facts and (not event.quote or not event.quote.strip() or event.quote not in source.text):
                raise ValueError("El hecho requiere una cita literal de la fuente")
            remaining = [literal for literal in event.protected_literals if literal.value not in declared_values]
            if remaining or any((event.facts, event.rules, event.entities, event.vocabulary)):
                events.append(event.model_copy(update={"protected_literals": remaining}))
        if source.protected_literals:
            events.append(Observation(origin=source.origin, at=source.at, quote=source.text,
                                      protected_literals=source.protected_literals))
        policy = policy.model_copy(update={"vocabulary": {**policy.vocabulary, **vocabulary}})
        used = {fact[1] for event in events for fact in event.facts}
        used.update(atom[1] for event in events for rule in event.rules for atom in [*rule.rule.when, rule.rule.then])
        if set(vocabulary) - used:
            raise ValueError("Un concepto propuesto no está vinculado a ningún hecho o regla")
        for event in events:
            for _, predicate, value in event.facts:
                if predicate not in policy.vocabulary and predicate not in policy.families:
                    raise ValueError("Predicado fuera del vocabulario")
                if predicate in policy.families and not policy.families[predicate].accepts(value):
                    raise ValueError("Estado fuera de la familia protegida")
            for rule in event.rules:
                rule.validate_source(policy, source.text)
                rule.validate_source(policy, event.quote)
        return {"internal": events}

    def external(state):
        source = state["source"]
        events = []
        missing = []
        for user in dict.fromkeys(source.recipients):
            proposal = Source(origin=source.origin, at=source.at, user=user, text=source.text)
            policy = guard.recipient_policy(user, source.at)
            vocabulary = dict(policy.vocabulary)
            aliases = {}
            for event in state["internal"]:
                for _, predicate, _ in event.facts:
                    if predicate in policy.vocabulary or predicate in policy.families or predicate in aliases.values():
                        continue
                    index = len(aliases) + 1
                    alias = "relacion_fuente_" + str(index)
                    while alias in vocabulary or alias in policy.families:
                        index += 1
                        alias = "relacion_fuente_" + str(index)
                    aliases[alias] = predicate
                    vocabulary[alias] = "Posible relación expresada en el fragmento literal: " + event.quote
            policy = policy.model_copy(update={"vocabulary": vocabulary})
            proposed = model.source(policy, proposal, "delivered", guard.model_context(user, source.at))
            for event in proposed:
                if event.origin != source.origin or event.at != source.at or event.user != user:
                    raise ValueError("La propuesta no coincide con su destinatario o fuente")
                if event.facts and (not event.quote or not event.quote.strip() or event.quote not in source.text):
                    raise ValueError("El hecho requiere una cita literal de la fuente")
                for _, predicate, value in event.facts:
                    if predicate not in policy.vocabulary and predicate not in policy.families:
                        raise ValueError("Predicado fuera del vocabulario")
                    if predicate in policy.families and not policy.families[predicate].accepts(value):
                        raise ValueError("Estado fuera de la familia protegida")
                for hypothesis in event.hypotheses:
                    hypothesis.validate_source(policy, source.text)
                    hypothesis.validate_source(policy, event.quote)
                if aliases:
                    data = event.model_dump(mode="json")
                    data["facts"] = [[s, aliases.get(p, p), value] for s, p, value in event.facts]
                    for hypothesis in data["hypotheses"]:
                        hypothesis["claim"][1] = aliases.get(hypothesis["claim"][1], hypothesis["claim"][1])
                    event = Observation.model_validate(data)
                events.append(event)
            if not proposed:
                missing.append(user)
        return {"external": events, "missing_recipients": missing}

    def record(state):
        events = state["internal"] + state["external"]
        accepted = False
        if state.get("accept"):
            if state.get("missing_recipients"):
                raise ValueError("No se extrajeron pistas para todos los destinatarios; vuelve a analizar la fuente")
            accepted = guard.observe(events, source_id=state["source"].origin, source_digest=state["digest"])
        return {"count": len(events), "accepted": accepted}

    graph = StateGraph(SourceState)
    graph.add_node("internal", internal)
    graph.add_node("external", external)
    graph.add_node("record", record)
    graph.add_edge(START, "internal")
    graph.add_edge("internal", "external")
    graph.add_edge("external", "record")
    graph.add_edge("record", END)
    return graph.compile()


class ReviewState(TypedDict, total=False):
    text: str
    user: str
    task: str
    question: str
    day: date
    analysis: DraftFacts
    hypotheses: list[Hypothesis]
    context: dict
    internal: dict
    result: dict
    extraction_error: bool


def inspect_graph(guard: Guard, model: ClaudeAnalysis):
    def extract(state):
        if state["task"] not in FREE_TASKS and state["task"] not in guard.case.tasks:
            raise ValueError("Tarea desconocida")
        policy = guard.policy_at(state["day"])
        analysis = model.analyze(policy, state["text"], state["question"])
        for item in analysis.facts:
            _, predicate, value = item.fact
            if not item.quote.strip() or item.quote not in state["text"]:
                raise ValueError("Afirmación sin cita literal")
            if predicate not in policy.vocabulary and predicate not in policy.families:
                raise ValueError("Relación fuera de la política")
            if predicate in policy.families and not policy.families[predicate].accepts(value):
                raise ValueError("Estado fuera de la política")
        # The draft must name the same nodes as the history, or no path can cross from one to the other.
        resolve = guard.resolver()
        analysis = analysis.model_copy(update={"facts": [
            item.model_copy(update={"fact": guard._canonical(item.fact, resolve, policy)}) for item in analysis.facts]})
        return {"analysis": analysis, "context": guard.model_context(state["user"], state["day"]),
                "internal": guard.model_context(None, state["day"])}

    def adversary(state):
        policy = guard.recipient_policy(state["user"], state["day"])
        hypotheses = model.adversarial(policy, state["text"], state["context"])
        for item in hypotheses:
            item.validate_source(policy, state["text"])
        return {"hypotheses": hypotheses}

    def paths(state):
        reader = getattr(model, "read_path", None)
        if reader is None:
            return {}
        extra = [item.fact for item in state["analysis"].facts] + [h.claim for h in state["hypotheses"]]
        # shortcut: reads at most 5 new paths per review; the rest keep policy.paths.weight until a later review.
        pending = guard.unrated_paths(state["user"], state["day"], extra)[:5]
        policy, labels, readings = guard.risk_policy(state["day"]), guard.labels(), []
        for goal, path, key in pending:
            try:
                reading = reader(policy, goal, describe(path, labels))
            except ValueError:
                continue  # An unread path keeps the default weight; the review still runs.
            readings.append((key, reading.relation, reading.confidence if reading.implies_goal else 0.))
        if readings:
            with guard.transaction():
                guard.rate_paths(readings, model_id())
        return {}

    def score(state):
        result, _ = score_candidate(guard, state)
        result["evidence"] = {
            "candidate": [item.model_dump(mode="json") for item in state["analysis"].facts],
            "recipient_history": [{"origin": origin, "fact": fact, "p_known": weight}
                                  for origin, fact, weight in guard.records(state["user"], state["day"])],
        }
        return {"result": result}

    graph = StateGraph(ReviewState)
    graph.add_node("extract", extract)
    graph.add_node("adversary", adversary)
    graph.add_node("paths", paths)
    graph.add_node("score", score)
    graph.add_edge(START, "extract")
    graph.add_edge("extract", "adversary")
    graph.add_edge("adversary", "paths")
    graph.add_edge("paths", "score")
    graph.add_edge("score", END)
    return graph.compile()


def score_candidate(guard, state):
    analysis = state["analysis"]
    original = guard.case
    candidate = original.model_copy(deep=True)
    candidate.clauses["mosaic_candidate"] = Clause(
        text=state["text"], facts=[item.fact for item in analysis.facts], provides=["mosaic_task"])
    candidate.tasks["mosaic_check"] = Task(requires=["mosaic_task"],
                                            options=[Option(clauses=["mosaic_candidate"])])
    guard.case = candidate
    try:
        return guard._decide(state["user"], "mosaic_check", state["day"], state["text"],
                                   {state["text"]: state["hypotheses"]}, model_id(), strict=True,
                                   verify_all_facts=False)
    finally:
        guard.case = original
