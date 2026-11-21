"""RDF views, scoped role inheritance, and probability of an observer's knowledge."""
import warnings
from collections import defaultdict
from datetime import date
from decimal import Decimal

# ProbLog's docstrings trigger a SyntaxWarning the first time Python 3.12+ compiles them; hide it from the demo.
warnings.filterwarnings("ignore", "invalid escape sequence", SyntaxWarning, r".*[\\/]problog[\\/]")
from owlrl import DeductiveClosure, OWLRL_Semantics
from problog import get_evaluatable
from problog.program import PrologString
from rdflib import BNode, Graph, Literal, Namespace, RDF, RDFS, OWL
from rdflib.collection import Collection

from .model import Case, Policy, Triple

V = Namespace("urn:isde:vocab:")
E = Namespace("urn:isde:entity:")
P = Namespace("urn:isde:predicate:")
R = Namespace("urn:isde:role:")
A = Namespace("urn:isde:assignment:")


def rdf_fact(fact: Triple):
    subject, predicate, value = fact
    return E[subject], P[predicate], E[value]


def make_schema(policy: Policy):
    graph = Graph()
    for name, role in policy.roles.items():
        graph.add((R[name], RDF.type, OWL.Class))
        for parent in role.parents:
            graph.add((R[name], RDFS.subClassOf, R[parent]))
    for chain in policy.chains:
        inverse, head = BNode(), BNode()
        graph.add((inverse, OWL.inverseOf, P[chain.target]))
        Collection(graph, head, [inverse, P[chain.status]])
        graph.add((P[chain.conclusion], OWL.propertyChainAxiom, head))
    return graph


def role_graph(schema: Graph, case: Case, at: date):
    graph = Graph() + schema
    for index, item in enumerate(case.assignments):
        if (item.org, item.operation) != (case.org, case.operation):
            continue
        if item.start <= at and (item.end is None or at < item.end):
            assignment = A[str(index)]
            graph.add((assignment, V.user, E[item.user]))
            graph.add((assignment, RDF.type, R[item.role]))
    DeductiveClosure(OWLRL_Semantics).expand(graph)
    return graph


def user_grants(policy: Policy, graph: Graph, user: str):
    roles = {name for assignment in graph.subjects(V.user, E[user])
             for name in policy.roles if (assignment, RDF.type, R[name]) in graph}
    read = {resource for name in roles for resource in policy.roles[name].read}
    receive = {family for name in roles for family in policy.roles[name].receive}
    return read, receive


def logical_rules(policy):
    rules = []
    for chain in policy.chains:
        rules.append((
            [("?doc", chain.target, "?target"), ("?doc", chain.status, "?state")],
            ("?target", chain.conclusion, "?state"),
        ))
    rules.extend((rule.when, rule.then) for rule in policy.rules)
    return rules


def _match(graph, atom, bindings):
    subject, predicate, value = atom

    def fixed(term):
        if term.startswith("?"):
            return bindings.get(term)
        return E[term]

    for actual_subject, _, actual_value in graph.triples((fixed(subject), P[predicate], fixed(value))):
        found = bindings.copy()
        valid = True
        for term, actual in ((subject, actual_subject), (value, actual_value)):
            if not term.startswith("?"):
                continue
            if term in found and found[term] != actual:
                valid = False
                break
            found[term] = actual
        if valid:
            yield found


def close_rules(graph, rules):
    # Positive, range-restricted rules cannot create individuals; closure is finite.
    while True:
        additions = set()
        for premises, conclusion in rules:
            matches = [{}]
            for atom in premises:
                matches = [result for binding in matches for result in _match(graph, atom, binding)]
                if not matches:
                    break
            subject, predicate, value = conclusion
            for binding in matches:
                left = binding[subject] if subject.startswith("?") else E[subject]
                right = binding[value] if value.startswith("?") else E[value]
                fact = (left, P[predicate], right)
                if fact not in graph:
                    additions.add(fact)
        if not additions:
            return graph
        for fact in additions:
            graph.add(fact)


def internal_view(schema, facts, policy=None):
    graph = Graph() + schema
    for fact in facts:
        graph.add(rdf_fact(fact))
    DeductiveClosure(OWLRL_Semantics).expand(graph)
    if policy is not None:
        close_rules(graph, logical_rules(policy))
    return graph


def observer_view(records, hypotheses=()):
    # Keep "possibly known" statements qualified by source, never as asserted truth.
    graph = Graph()
    for origin, fact, weight in records:
        node = BNode()
        graph.add((node, RDF.type, RDF.Statement))
        for prop, term in zip((RDF.subject, RDF.predicate, RDF.object), rdf_fact(fact)):
            graph.add((node, prop, term))
        graph.add((node, V.origin, Literal(origin)))
        graph.add((node, V.weight, Literal(weight)))
    for estimate in hypotheses:
        node = BNode()
        graph.add((node, RDF.type, RDF.Statement))
        graph.add((node, RDF.type, V.Interpretation))
        for prop, term in zip((RDF.subject, RDF.predicate, RDF.object), rdf_fact(estimate.claim)):
            graph.add((node, prop, term))
        graph.add((node, V.origin, Literal(estimate.origin)))
        graph.add((node, V.quote, Literal(estimate.quote)))
        graph.add((node, V.date, Literal(estimate.at)))
        graph.add((node, V.assessor, Literal(estimate.assessor)))
        graph.add((node, V.pIfSeen, Literal(estimate.p_if_seen)))
        graph.add((node, V.pSeen, Literal(estimate.p_seen)))
        weight = Decimal(str(estimate.p_seen)) * Decimal(str(estimate.p_if_seen))
        graph.add((node, V.weight, Literal(weight)))
    return graph


def probabilities(policy: Policy, view: Graph):
    grouped = defaultdict(dict)
    entities = set()
    for node in view.subjects(RDF.type, RDF.Statement):
        origin = str(view.value(node, V.origin))
        weight = Decimal(str(view.value(node, V.weight)))
        fact = tuple(str(view.value(node, prop)).removeprefix(str(ns)) for prop, ns
                     in ((RDF.subject, E), (RDF.predicate, P), (RDF.object, E)))
        entities.update((fact[0], fact[2]))
        grouped[origin][fact] = max(grouped[origin].get(fact, Decimal(0)), weight)

    # Numeric symbols keep RDF identifiers separate from executable ProbLog syntax.
    names = set(entities) | set(policy.families)
    for facts in grouped.values():
        for fact in facts:
            names.update(fact)
    for family in policy.families.values():
        names.update(family.statuses)
    rules = logical_rules(policy)
    for premises, conclusion in rules:
        for subject, predicate, value in [*premises, conclusion]:
            names.add(predicate)
            names.update(term for term in (subject, value) if not term.startswith("?"))
    symbols = {name: str(i) for i, name in enumerate(sorted(names))}
    lines = ["known(-1,-1,-1)."]
    for i, facts in enumerate(grouped.values()):
        weights = sorted({p for p in facts.values() if p > 0})
        previous = Decimal(0)
        choices = []
        for j, weight in enumerate(weights):
            choices.append(f"{weight - previous:f}::seen({i},{j})")
            previous = weight
        if choices:
            lines.append("; ".join(choices) + ".")
        # One underlying source is one random draw, even across quotes and copies.
        for fact, weight in facts.items():
            args = ",".join(symbols[value] for value in fact)
            for j, cutoff in enumerate(weights):
                if cutoff <= weight:
                    lines.append(f"known({args}) :- seen({i},{j}).")
    for premises, conclusion in rules:
        variables = {}

        def encode_atom(atom):
            subject, predicate, value = atom

            def term(item):
                if item.startswith("?"):
                    if item not in variables:
                        variables[item] = f"V{len(variables)}"
                    return variables[item]
                return symbols[item]

            return f"known({term(subject)},{symbols[predicate]},{term(value)})"
        body = ", ".join(encode_atom(atom) for atom in premises)
        lines.append(f"{encode_atom(conclusion)} :- {body}.")

    # Discover all reachable subjects, including those reached only through inferred edges.
    potential = Graph()
    for facts in grouped.values():
        for fact in facts:
            potential.add(rdf_fact(fact))
    close_rules(potential, rules)
    targets = defaultdict(set)
    for subject, predicate, _ in potential:
        subject_text, predicate_text = str(subject), str(predicate)
        if subject_text.startswith(str(E)) and predicate_text.startswith(str(P)):
            family = predicate_text.removeprefix(str(P))
            if family in policy.families:
                targets[family].add(subject_text.removeprefix(str(E)))
    queries = {}
    for family, subjects in targets.items():
        for subject in subjects:
            for status in policy.families[family].statuses:
                fact = (subject, family, status)
                args = ",".join(symbols[value] for value in fact)
                query = f"known({args})"
                queries[query] = fact
                lines.append(f"query({query}).")
    if not queries:
        return {}
    result = get_evaluatable().create_from(PrologString("\n".join(lines))).evaluate()
    return {queries[str(term).replace(" ", "")]: float(value) for term, value in result.items()}
