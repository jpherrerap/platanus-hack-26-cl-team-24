"""RDF views, scoped role inheritance, and probability of an observer's knowledge."""
import json
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


def plain(graph):
    """Entity-predicate-entity triples of a graph, without RDF/OWL vocabulary."""
    prefixes = (str(E), str(P), str(E))
    return {tuple(str(term).removeprefix(prefix) for term, prefix in zip(triple, prefixes)) for triple in graph
            if all(str(term).startswith(prefix) for term, prefix in zip(triple, prefixes))}


def value_nodes(policy, facts):
    """Family states are values: a path may end on one but never bridge two entities through it."""
    values = {status for family in policy.families.values() for status in family.statuses}
    values.update(value for _, predicate, value in facts if predicate in policy.families)
    return values


def find_paths(facts, start, goal, *, max_length, max_degree, blocked=frozenset(), limit=10):
    """Simple paths from start to goal, walking each edge in either direction."""
    adjacency = defaultdict(list)
    for fact in sorted(set(facts)):
        adjacency[fact[0]].append((fact[2], fact))
        adjacency[fact[2]].append((fact[0], fact))
    found = []

    def walk(node, visited, steps):
        for following, fact in adjacency[node]:
            if following == goal:
                found.append(steps + [fact])
            elif (following not in visited and following not in blocked and len(steps) + 1 < max_length
                  and len(adjacency[following]) <= max_degree):
                walk(following, visited | {following}, steps + [fact])

    if start != goal:
        walk(start, {start}, [])
    return sorted(found, key=len)[:limit]


def derived(facts, rules):
    """Conclusions the policy rules draw from these facts alone: the deterministic reading of a path."""
    graph = Graph()
    for fact in facts:
        graph.add(rdf_fact(fact))
    return plain(close_rules(graph, rules)) - set(facts)


def signature(goal, path):
    return json.dumps([list(goal), [list(fact) for fact in path]], separators=(",", ":"))


def describe(path, labels=None):
    """One subject-verb-object sentence per edge, readable by an operator or a model."""
    labels = labels or {}
    return [f"{labels.get(s, s)} {p.replace('_', ' ')} {labels.get(o, o)}" for s, p, o in path]


def path_goals(policy, facts, goals):
    """For each goal, the observer's paths between its two ends and whether a rule turns the path into the goal."""
    facts = set(facts)
    blocked = value_nodes(policy, facts | set(goals))
    rules = logical_rules(policy)
    result = {}
    for goal in goals:
        paths = find_paths(facts - {goal}, goal[0], goal[2], max_length=policy.paths.max_length,
                           max_degree=policy.paths.max_degree, blocked=blocked)
        # One edge between the ends is a different relation, not a path; keep it only when a rule reads the goal from it.
        result[goal] = [(path, backed) for path in paths
                        if (backed := goal in derived(path, rules)) or len(path) > 1]
    return result


def explain(policy, facts, goal, labels=None):
    rules = logical_rules(policy)
    return [{"edges": [list(fact) for fact in path], "text": describe(path, labels), "rule": backed,
             "relations": sorted(list(fact) for fact in derived(path, rules) if {fact[0], fact[2]} == {goal[0], goal[2]}),
             "signature": signature(goal, path)}
            for path, backed in path_goals(policy, facts, [goal])[goal]]


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


def probabilities(policy: Policy, view: Graph, secrets=(), ratings=None):
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
    targets = defaultdict(lambda: defaultdict(set))
    for subject, predicate, value in potential:
        subject_text, predicate_text, value_text = str(subject), str(predicate), str(value)
        if subject_text.startswith(str(E)) and predicate_text.startswith(str(P)) and value_text.startswith(str(E)):
            family = predicate_text.removeprefix(str(P))
            if family in policy.families:
                targets[family][subject_text.removeprefix(str(E))].add(value_text.removeprefix(str(E)))
    # A path between the ends of an internal secret is a possible inference even without a declared rule.
    ratings = ratings or {}
    observed = plain(potential)
    nodes = {term for fact in observed for term in (fact[0], fact[2])}
    goals = [goal for goal in secrets if goal[1] in policy.families and goal[0] in nodes and goal[2] in nodes]
    count = 0
    for goal, paths in path_goals(policy, observed, goals).items():
        for path, backed in paths:
            rate = ratings.get(signature(goal, path), policy.paths.weight)
            if backed or rate <= 0:
                continue
            body = ", ".join(f"known({','.join(symbols[term] for term in fact)})" for fact in path)
            lines.append(f"{rate:.6f}::path({count}).")
            lines.append(f"known({','.join(symbols[term] for term in goal)}) :- path({count}), {body}.")
            targets[goal[1]][goal[0]].add(goal[2])
            count += 1
    queries = {}
    for family, subjects in targets.items():
        for subject, reachable in subjects.items():
            statuses = reachable if policy.families[family].any_value else policy.families[family].statuses
            for status in statuses:
                fact = (subject, family, status)
                args = ",".join(symbols[value] for value in fact)
                query = f"known({args})"
                queries[query] = fact
                lines.append(f"query({query}).")
    if not queries:
        return {}
    result = get_evaluatable().create_from(PrologString("\n".join(lines))).evaluate()
    return {queries[str(term).replace(" ", "")]: float(value) for term, value in result.items()}
