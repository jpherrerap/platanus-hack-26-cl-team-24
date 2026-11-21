"""Formal toy experiments. The public policy loader still rejects the multistep fixture."""
import unittest
from datetime import date
from pathlib import Path

from isde.guard import Guard
from isde.graphs import observer_view, probabilities
from isde.model import Assignment, Case, Chain, Clause, Family, Observation, Option, Policy, Role, Task

DAY = date(2026, 1, 1)
EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "policy.yaml"
YES = "yes"


def fixture(depth=3, delivered=1.0):
    """Encode a => b => ... using one anchor and one identity relation."""
    predicates = ["a"] + [chr(ord("a") + i) for i in range(1, depth + 1)]
    families = {
        name: Family(description=name, statuses=[YES], absolute=.75, increase=.45)
        for name in predicates[1:]
    }
    chains = [Chain(target="anchor" if index == 0 else "self_link",
                    status=predicates[index], conclusion=predicates[index + 1])
              for index in range(depth)]
    base = Policy.load(EXAMPLE)
    data = base.model_dump()
    data.update(
        vocabulary={name: name for name in ["anchor", "self_link", *predicates[:-1]]},
        roles={"reader": {"receive": predicates[1:-1]}},
        families={name: family.model_dump() for name, family in families.items()},
        chains=[chain.model_dump() for chain in chains],
        evidence={**base.evidence, "cited": 1.0, "delivered": delivered},
        assess_drafts=False,
    )
    case = Case(
        org="toy_org", operation="toy", assignments=[
            Assignment(user=user, role="reader", org="toy_org", operation="toy", start=DAY)
            for user in ["ana", "luis"]
        ],
        clauses={"a": Clause(text="a", facts=[("x", "a", YES)], provides=["answer"])},
        tasks={"show_a": Task(requires=["answer"], options=[Option(clauses=["a"])])},
    )
    return data, case, predicates


def bypassed_policy(data):
    """Exercise existing OWL and ProbLog code despite public validation rejecting these rules."""
    base = Policy.load(EXAMPLE)
    return base.model_copy(update={
        "vocabulary": data["vocabulary"],
        "roles": {name: Role.model_validate(role) for name, role in data["roles"].items()},
        "families": {name: Family.model_validate(family) for name, family in data["families"].items()},
        "chains": [Chain.model_validate(chain) for chain in data["chains"]],
        "evidence": data["evidence"],
        "assess_drafts": False,
    })


class MultinivelTests(unittest.TestCase):
    def guard(self, depth=3, delivered=1.0, missing_anchor=False, already_knows=False):
        data, case, predicates = fixture(depth, delivered)
        guard = Guard(":memory:", bypassed_policy(data), case)
        self.addCleanup(guard.close)
        private = [("x", "a", YES), ("x", "anchor", "n"), ("n", "self_link", "n")]
        visible = [("n", "self_link", "n")]
        if not missing_anchor:
            visible.append(("x", "anchor", "n"))
        if already_knows:
            visible.append(("x", "a", YES))
        guard.observe([
            Observation(origin="internal", at=DAY, facts=private),
            Observation(origin="history", at=DAY, user="ana", grade="cited", facts=visible),
        ])
        return guard, data, predicates

    def test_la_politica_actual_rechaza_encadenar_una_conclusion(self):
        data, _, _ = fixture()
        with self.assertRaisesRegex(ValueError, "cadenas de un solo nivel"):
            Policy.model_validate(data)

    @unittest.expectedFailure
    def test_objetivo_el_cargador_deberia_aceptar_una_cadena_de_tres_niveles(self):
        data, _, _ = fixture()
        Policy.model_validate(data)

    def test_tres_niveles_se_deducen_en_owl_y_el_guardrail_retiene_a(self):
        guard, _, _ = self.guard()
        internal = guard.model_context(None, DAY)["facts"]
        for predicate in ["b", "c", "d"]:
            self.assertIn(("n", predicate, YES), internal)
        before = probabilities(guard.policy, observer_view(guard.records("ana", DAY)))
        self.assertEqual(before.get(("n", "d", YES), 0), 0)
        result = guard.run("ana", "show_a", DAY, "r1", draft="a", strict=True)
        self.assertEqual((result["action"], result["text"]), ("review", None))
        blocked = result["attempts"][0]["blocked"]
        self.assertEqual([(item["family"], item["after"]) for item in blocked], [("d", 1.0)])
        self.assertEqual(guard.inbox("ana"), [])

    def test_sin_una_premisa_intermedia_el_mismo_a_no_demuestra_d(self):
        guard, _, _ = self.guard(missing_anchor=True)
        result = guard.run("ana", "show_a", DAY, "r1", draft="a", strict=True)
        self.assertEqual((result["action"], result["text"]), ("allow", "a"))
        self.assertEqual(guard.inbox("ana"), [{"id": "r1", "text": "a"}])

    def test_antes_y_despues_con_el_mismo_historial(self):
        guard, _, _ = self.guard(delivered=.8)
        before = probabilities(guard.policy, observer_view(guard.records("ana", DAY)))
        result = guard.run("ana", "show_a", DAY, draft="a", strict=True)
        secret = result["attempts"][0]["blocked"][0]
        self.assertEqual(before.get(("n", "d", YES), 0), 0)
        self.assertAlmostEqual(secret["after"], .8)
        self.assertEqual(result["action"], "review")

    def test_profundidades_dos_tres_y_cuatro(self):
        for depth in (2, 3, 4):
            with self.subTest(depth=depth):
                guard, _, predicates = self.guard(depth=depth)
                final = predicates[-1]
                self.assertIn(("n", final, YES), guard.model_context(None, DAY)["facts"])
                result = guard.run("ana", "show_a", DAY, draft="a", strict=True)
                self.assertEqual(result["action"], "review")
                self.assertEqual(result["attempts"][0]["blocked"][0]["family"], final)

    def test_destinatarios_con_diferente_conocimiento(self):
        guard, _, _ = self.guard()
        guard.observe([Observation(origin="luis_history", user="luis", grade="cited", at=DAY,
                                   facts=[("n", "self_link", "n")])])
        self.assertEqual(guard.run("ana", "show_a", DAY, draft="a", strict=True)["action"], "review")
        self.assertEqual(guard.run("luis", "show_a", DAY, draft="a", strict=True)["action"], "allow")

    def test_si_a_ya_es_conocido_la_respuesta_no_suma_otra_fuga_indirecta(self):
        guard, _, _ = self.guard(already_knows=True)
        before = probabilities(guard.policy, observer_view(guard.records("ana", DAY)))
        self.assertEqual(before[("n", "d", YES)], 1.0)
        self.assertEqual(guard.run("ana", "show_a", DAY, draft="a", strict=True)["action"], "allow")

    def test_dos_caminos_comparten_la_misma_pista_sin_contarla_dos_veces(self):
        guard, _, _ = self.guard(delivered=.8)
        # A second route a => c => d shares the same uncertain delivery of a.
        guard.policy.chains.append(Chain(target="anchor", status="a", conclusion="c"))
        result = guard.run("ana", "show_a", DAY, draft="a", strict=True)
        secret = result["attempts"][0]["blocked"][0]
        self.assertAlmostEqual(secret["after"], .8)
        self.assertEqual(result["action"], "review")

    def test_ciclo_no_crea_un_hecho_sin_premisas(self):
        guard, _, _ = self.guard(missing_anchor=True)
        guard.policy.chains.append(Chain(target="self_link", status="c", conclusion="b"))
        before = probabilities(guard.policy, observer_view(guard.records("ana", DAY)))
        self.assertEqual(before.get(("n", "d", YES), 0), 0)
        self.assertEqual(guard.run("ana", "show_a", DAY, draft="a", strict=True)["action"], "allow")

    def test_el_motor_actual_requiere_tratar_pasos_intermedios_como_familias(self):
        data, case, _ = fixture()
        data["families"] = {"d": data["families"]["d"]}
        data["roles"]["reader"]["receive"] = []
        guard = Guard(":memory:", bypassed_policy(data), case)
        self.addCleanup(guard.close)
        guard.observe([
            Observation(origin="internal", at=DAY,
                        facts=[("x", "a", YES), ("x", "anchor", "n"), ("n", "self_link", "n")]),
            Observation(origin="history", user="ana", grade="cited", at=DAY,
                        facts=[("x", "anchor", "n"), ("n", "self_link", "n")]),
        ])
        with self.assertRaises(KeyError):
            guard.run("ana", "show_a", DAY, draft="a", strict=True)
