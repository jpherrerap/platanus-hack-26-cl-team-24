"""RDF views, scoped role inheritance, and probability of an observer's knowledge."""
import warnings
from collections import defaultdict
from datetime import date
from decimal import Decimal

# ProbLog's docstrings trigger a SyntaxWarning the first time Python 3.12+ compiles them; hide it from the demo.
warnings.filterwarnings("ignore", "invalid escape sequence", SyntaxWarning)
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


def internal_view(schema, facts):
    graph = Graph() + schema
    for fact in facts:
        graph.add(rdf_fact(fact))
    DeductiveClosure(OWLRL_Semantics).expand(graph)
    return graph


def observer_view(records):
    # Keep "possibly known" statements qualified by source, never as asserted truth.
    graph = Graph()
    for origin, fact, weight in records:
        node = BNode()
        graph.add((node, RDF.type, RDF.Statement))
        for prop, term in zip((RDF.subject, RDF.predicate, RDF.object), rdf_fact(fact)):
            graph.add((node, prop, term))
        graph.add((node, V.origin, Literal(origin)))
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
    for chain in policy.chains:
        names.update((chain.target, chain.status))
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
    for chain in policy.chains:
        c, target, status = (symbols[k] for k in (chain.conclusion, chain.target, chain.status))
        lines.append(f"known(T,{c},S) :- known(D,{target},T), known(D,{status},S).")

    # Query only candidate subjects, not every vocabulary symbol.
    targets = defaultdict(set)
    for facts in grouped.values():
        for subject, predicate, value in facts:
            if predicate in policy.families:
                targets[predicate].add(subject)
            for chain in policy.chains:
                if predicate == chain.target:
                    targets[chain.conclusion].add(value)
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
