"""Formal toy experiments using the public policy loader."""
import unittest
from datetime import date
from pathlib import Path

from isde.guard import Guard
from isde.graphs import observer_view, probabilities
from isde.model import Assignment, Case, Chain, Clause, Family, Hypothesis, Observation, Option, Policy, Task

DAY = date(2026, 1, 1)
EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "policy.yaml"
YES = "yes"


def fixture(depth=3, delivered=1.0):
    """Encode a => b => ... using one anchor and one identity relation."""
    predicates = ["a"] + [chr(ord("a") + i) for i in range(1, depth + 1)]
    families = {
        name: Family(description=name, statuses=[YES], absolute=.75, increase=.45)
        for name in predicates[-1:]
    }
    chains = [Chain(target="anchor" if index == 0 else "self_link",
                    status=predicates[index], conclusion=predicates[index + 1])
              for index in range(depth)]
    base = Policy.load(EXAMPLE)
    data = base.model_dump()
    data.update(
        vocabulary={name: name for name in ["anchor", "self_link", *predicates[:-1]]},
        roles={"reader": {}},
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


class MultinivelTests(unittest.TestCase):
    def guard(self, depth=3, delivered=1.0, missing_anchor=False, already_knows=False, extra_chain=None):
        data, case, predicates = fixture(depth, delivered)
        if extra_chain:
            data["chains"].append(extra_chain)
        guard = Guard(":memory:", Policy.model_validate(data), case)
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

    def test_el_cargador_acepta_una_cadena_de_tres_niveles(self):
        import tempfile
        import yaml
        data, _, _ = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.yaml"
            path.write_text(yaml.safe_dump(data), encoding="utf-8")
            policy = Policy.load(path)
        self.assertEqual(len(policy.chains), 3)
        self.assertEqual(list(policy.families), ["d"])

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
        # A second route a => c => d shares the same uncertain delivery of a.
        guard, _, _ = self.guard(delivered=.8, extra_chain={"target":"anchor", "status":"a", "conclusion":"c"})
        result = guard.run("ana", "show_a", DAY, draft="a", strict=True)
        secret = result["attempts"][0]["blocked"][0]
        self.assertAlmostEqual(secret["after"], .8)
        self.assertEqual(result["action"], "review")

    def test_ciclo_no_crea_un_hecho_sin_premisas(self):
        guard, _, _ = self.guard(missing_anchor=True, extra_chain={"target":"self_link", "status":"c", "conclusion":"b"})
        before = probabilities(guard.policy, observer_view(guard.records("ana", DAY)))
        self.assertEqual(before.get(("n", "d", YES), 0), 0)
        self.assertEqual(guard.run("ana", "show_a", DAY, draft="a", strict=True)["action"], "allow")

    def test_los_pasos_intermedios_no_necesitan_ser_familias_confidenciales(self):
        guard, _, _ = self.guard()
        self.assertEqual(set(guard.policy.families), {"d"})
        self.assertIn(("n", "b", YES), guard.model_context(None, DAY)["facts"])
        self.assertIn(("n", "c", YES), guard.model_context(None, DAY)["facts"])
        self.assertEqual(guard.run("ana", "show_a", DAY, draft="a", strict=True)["action"], "review")


def unary_fixture(depth=3, delivered=1.0):
    data, case, predicates = fixture(depth, delivered)
    data["vocabulary"] = {name: name for name in predicates[:-1]}
    data["chains"] = []
    data["rules"] = [
        {"when": [["?entity", predicates[index], YES]],
         "then": ["?entity", predicates[index + 1], YES]}
        for index in range(depth)
    ]
    return data, case, predicates


class GeneralRuleTests(unittest.TestCase):
    def guard(self, data, case, private=None, history=None):
        guard = Guard(":memory:", Policy.model_validate(data), case)
        self.addCleanup(guard.close)
        events = [Observation(origin="private", at=DAY, facts=private or [("x", "a", YES)])]
        if history:
            events.append(Observation(origin="history", at=DAY, user="ana", grade="cited", facts=history))
        guard.observe(events)
        return guard

    def test_una_cadena_unaria_se_carga_desde_yaml_y_bloquea_a(self):
        import tempfile
        import yaml
        data, case, _ = unary_fixture()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "policy.yaml"
            path.write_text(yaml.safe_dump(data), encoding="utf-8")
            policy = Policy.load(path)
        self.assertEqual(set(policy.families), {"d"})
        guard = Guard(":memory:", policy, case)
        self.addCleanup(guard.close)
        guard.observe([Observation(origin="private", at=DAY, facts=[("x", "a", YES)])])
        for predicate in ["b", "c", "d"]:
            self.assertIn(("x", predicate, YES), guard.model_context(None, DAY)["facts"])
        result = guard.run("ana", "show_a", DAY, "a1", draft="a", strict=True)
        self.assertEqual((result["action"], result["text"]), ("review", None))
        self.assertEqual(result["attempts"][0]["blocked"][0]["family"], "d")
        self.assertEqual(guard.inbox("ana"), [])

    def test_falta_b_implica_c_y_no_se_deduce_d(self):
        data, case, _ = unary_fixture()
        data["rules"].pop(1)
        guard = self.guard(data, case)
        self.assertNotIn(("x", "d", YES), guard.model_context(None, DAY)["facts"])
        self.assertEqual(guard.run("ana", "show_a", DAY, draft="a", strict=True)["action"], "allow")

    def test_ocho_pasos_con_una_sola_familia_protegida(self):
        data, case, predicates = unary_fixture(depth=8)
        guard = self.guard(data, case)
        self.assertIn(("x", predicates[-1], YES), guard.model_context(None, DAY)["facts"])
        result = guard.run("ana", "show_a", DAY, draft="a", strict=True)
        self.assertEqual(result["action"], "review")
        self.assertEqual(result["attempts"][0]["blocked"][0]["family"], predicates[-1])

    def test_dos_premisas_con_variables_y_conocimiento_separado(self):
        data, case, _ = unary_fixture()
        data["vocabulary"]["q"] = "premisa q"
        data["rules"][0]["when"].append(["?entity", "q", YES])
        guard = self.guard(data, case, private=[("x","a",YES),("x","q",YES)],
                           history=[("x","q",YES)])
        self.assertEqual(guard.run("ana", "show_a", DAY, draft="a", strict=True)["action"], "review")
        self.assertEqual(guard.run("luis", "show_a", DAY, draft="a", strict=True)["action"], "allow")

    def test_otra_entidad_no_cumple_la_premisa_del_mismo_sujeto(self):
        data, case, _ = unary_fixture()
        data["vocabulary"]["q"] = "premisa q"
        data["rules"][0]["when"].append(["?entity", "q", YES])
        guard = self.guard(data, case, private=[("x","a",YES),("y","q",YES)],
                           history=[("y","q",YES)])
        self.assertNotIn(("x", "d", YES), guard.model_context(None, DAY)["facts"])
        self.assertEqual(guard.run("ana", "show_a", DAY, draft="a", strict=True)["action"], "allow")

    def test_relacion_inferida_puede_ser_premisa_de_una_cadena_existente(self):
        data, case, _ = fixture()
        data["vocabulary"]["source"] = "relación inicial"
        data["rules"] = [{"when":[["?x","source","?y"]], "then":["?x","anchor","?y"]}]
        case.clauses["a"] = Clause(text="a", facts=[("x","source","n")], provides=["answer"])
        guard = self.guard(data, case,
                           private=[("x","source","n"),("x","a",YES),("n","self_link","n")],
                           history=[("x","a",YES),("n","self_link","n")])
        self.assertIn(("n","d",YES), guard.model_context(None, DAY)["facts"])
        result = guard.run("ana", "show_a", DAY, draft="a", strict=True)
        self.assertEqual(result["action"], "review")
        self.assertEqual(result["attempts"][0]["blocked"][0]["family"], "d")

    def test_rutas_alternativas_no_suman_dos_veces_la_pista_inicial(self):
        data, case, _ = unary_fixture(delivered=.8)
        data["rules"].append({"when":[["?entity","a",YES]], "then":["?entity","c",YES]})
        guard = self.guard(data, case)
        result = guard.run("ana", "show_a", DAY, draft="a", strict=True)
        self.assertAlmostEqual(result["attempts"][0]["blocked"][0]["after"], .8)

    def test_una_variable_sin_vinculo_en_la_conclusion_se_rechaza(self):
        data, _, _ = unary_fixture()
        data["rules"][0]["then"][0] = "?unknown"
        with self.assertRaisesRegex(ValueError, "variables de la conclusión"):
            Policy.model_validate(data)

    def test_predicados_desconocidos_y_prolog_no_son_aceptados(self):
        data, _, _ = unary_fixture()
        data["rules"][0]["when"][0][1] = "desconocido"
        with self.assertRaisesRegex(ValueError, "Predicado sin definición"):
            Policy.model_validate(data)
        data, _, _ = unary_fixture()
        data["rules"][0]["when"][0][1] = "a),halt,("
        with self.assertRaises(ValueError):
            Policy.model_validate(data)

    def test_estado_negativo_explicito_no_confirma_el_positivo(self):
        data, case, _ = unary_fixture()
        case.clauses["a"] = Clause(text="a no", facts=[("x","a","no")], provides=["answer"])
        guard = self.guard(data, case, private=[("x","a","no")])
        self.assertEqual(guard.run("ana", "show_a", DAY, draft="a no", strict=True)["action"], "allow")

    def test_las_reglas_del_evaluador_adversarial_incluyen_reglas_generales(self):
        import json
        from types import SimpleNamespace
        from isde.claude import assess_hypotheses
        data, _, _ = unary_fixture()
        policy = Policy.model_validate(data)
        captured = {}
        def create(**request):
            captured.update(json.loads(request["messages"][0]["content"]))
            block = SimpleNamespace(type="tool_use", name="select_evidence", input={"hypotheses":[]})
            return SimpleNamespace(content=[block], stop_reason="tool_use")
        client = SimpleNamespace(messages=SimpleNamespace(create=create))
        observer = {"kind":"observer","roles":[],"possible_knowledge":[],"hypotheses":[]}
        assess_hypotheses(policy, "a", observer, client=client)
        self.assertEqual(len(captured["reglas_logicas"]), 3)
        self.assertEqual(captured["vista"], observer)

    def test_la_api_semantica_retiene_a_usando_las_reglas_del_yaml(self):
        import importlib.util
        if not all(importlib.util.find_spec(name) for name in ("fastapi", "langgraph", "httpx")):
            self.skipTest("Instala el extra api")
        import tempfile
        import yaml
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from isde.api import create_app
        from isde.semantic import CandidateFact, DraftAssessment

        data, case, _ = unary_fixture()
        class Model:
            def analyze(self, policy, text, question):
                return DraftAssessment(facts=[CandidateFact(fact=("x","a",YES),quote="a")],
                                       covers_all_claims=True, fulfills_task=True)
            def adversarial(self, policy, text, view):
                return []

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            policy_path, case_path = path / "policy.yaml", path / "case.yaml"
            policy_path.write_text(yaml.safe_dump(data), encoding="utf-8")
            case_path.write_text(yaml.safe_dump(case.model_dump(mode="json")), encoding="utf-8")
            env = {"MOSAIC_POLICY": str(policy_path), "MOSAIC_CASE": str(case_path)}
            with patch.dict("os.environ", env):
                client = TestClient(create_app(db=str(path/"db.sqlite"), model=Model(), seed=False))
            evidence = {"observations":[{"origin":"private", "at":DAY.isoformat(), "facts":[["x","a",YES]]}]}
            self.assertEqual(client.post("/v1/observations", json=evidence).status_code, 200)
            payload = {"review_id":"r1","user":"ana","task":"show_a","question":"¿Qué ocurrió?",
                       "day":DAY.isoformat(),"text":"a"}
            result = client.post("/v1/reviews", json=payload)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["status"], "revise")
            self.assertEqual(client.get("/v1/inbox/ana").json()["messages"], [])

    def test_ciclo_generico_sin_base_no_inventa_conclusion(self):
        data, _, _ = unary_fixture()
        data["rules"].append({"when":[["?entity","b",YES]], "then":["?entity","a",YES]})
        policy = Policy.model_validate(data)
        view = observer_view([("s", ("x","a","no"), 1.0)])
        result = probabilities(policy, view)
        self.assertEqual(result.get(("x","d",YES), 0.0), 0.0)

    def test_tres_premisas_independientes_y_probabilidad_condicional(self):
        data, case, _ = unary_fixture(delivered=.8)
        data["vocabulary"].update({"q":"q","r":"r"})
        data["rules"][0]["when"].extend([["?entity","q",YES],["?entity","r",YES]])
        guard = Guard(":memory:", Policy.model_validate(data), case)
        self.addCleanup(guard.close)
        guard.observe([
            Observation(origin="private", at=DAY, facts=[("x","a",YES),("x","q",YES),("x","r",YES)]),
            Observation(origin="source_q", user="ana", grade="external_hint", confidence=.5,
                        at=DAY, facts=[("x","q",YES)]),
            Observation(origin="source_r", user="ana", grade="external_hint", confidence=.5,
                        at=DAY, facts=[("x","r",YES)]),
        ])
        result = guard.run("ana", "show_a", DAY, draft="a", strict=True)
        self.assertEqual(result["action"], "allow")
        self.assertAlmostEqual(result["attempts"][0]["changes"][0]["after"], .2)

    def test_estado_ajeno_a_la_familia_se_rechaza_tambien_en_premisas(self):
        data, _, _ = unary_fixture()
        data["rules"].append({"when":[["?entity","d","unknown"]], "then":["?entity","a",YES]})
        with self.assertRaisesRegex(ValueError, "Estado fuera de la familia"):
            Policy.model_validate(data)

    def test_la_demo_formal_por_cli_carga_yaml_y_retiene_a(self):
        import json
        import subprocess
        import sys
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            args = [sys.executable, "-m", "isde",
                    "--policy", str(EXAMPLE.parent / "multinivel-policy.yaml"),
                    "--case", str(EXAMPLE.parent / "multinivel-case.yaml"),
                    "--db", str(Path(tmp) / "data.sqlite")]
            subprocess.run([*args, "load", str(EXAMPLE.parent / "multinivel-history.yaml")], check=True)
            raw = subprocess.check_output([*args, "preview", "ana", "show_a", "--at", DAY.isoformat(),
                                           "--draft", "a"], text=True)
            result = json.loads(raw)
            self.assertEqual(result["action"], "review")
            self.assertEqual(result["attempts"][0]["blocked"][0]["family"], "d")

    def test_una_variable_repetida_exige_mismo_sujeto_y_objeto(self):
        data, case, _ = unary_fixture()
        data["vocabulary"]["q"] = "relación"
        data["rules"][0]["when"].append(["?entity","q","?entity"])
        guard = self.guard(data, case, private=[("x","a",YES),("x","q","y")],
                           history=[("x","q","y")])
        self.assertNotIn(("x","d",YES), guard.model_context(None, DAY)["facts"])
        self.assertEqual(guard.run("ana", "show_a", DAY, draft="a", strict=True)["action"], "allow")

    def test_contradiccion_explicita_no_elimina_una_via_de_riesgo(self):
        data, case, _ = unary_fixture()
        guard = self.guard(data, case, private=[("x","a",YES),("x","a","no")])
        result = guard.run("ana", "show_a", DAY, draft="a", strict=True)
        self.assertEqual(result["action"], "review")

    def test_una_regla_con_constantes_y_sujetos_distintos(self):
        data, case, _ = unary_fixture()
        data["rules"][0] = {"when":[["x","a",YES]], "then":["other","b",YES]}
        guard = self.guard(data, case)
        self.assertIn(("other","d",YES), guard.model_context(None, DAY)["facts"])
        result = guard.run("ana", "show_a", DAY, draft="a", strict=True)
        self.assertEqual(result["action"], "review")
        self.assertEqual(result["attempts"][0]["blocked"][0]["subject"], "other")

    def test_un_ciclo_generico_con_base_no_cuenta_la_prueba_dos_veces(self):
        data, case, _ = unary_fixture(delivered=.8)
        data["rules"].append({"when":[["?entity","b",YES]],"then":["?entity","a",YES]})
        guard = self.guard(data, case)
        result = guard.run("ana", "show_a", DAY, draft="a", strict=True)
        self.assertEqual(result["action"], "review")
        self.assertAlmostEqual(result["attempts"][0]["blocked"][0]["after"], .8)

    def test_permisos_de_una_familia_se_aplican_al_final_de_la_cadena(self):
        data, case, _ = unary_fixture()
        data["roles"]["authorized"] = {"receive":["d"]}
        case.assignments[-1].role = "authorized"
        guard = self.guard(data, case)
        self.assertEqual(guard.run("ana","show_a",DAY,draft="a",strict=True)["action"], "review")
        self.assertEqual(guard.run("luis","show_a",DAY,draft="a",strict=True)["action"], "allow")

    def test_variables_unen_distintas_entidades(self):
        data, case, _ = unary_fixture()
        data["vocabulary"] = {"belongs":"relación", "risky":"atributo"}
        data["rules"] = [{"when":[["?person","belongs","?group"],["?group","risky",YES]],
                          "then":["?person","d",YES]}]
        case.clauses["a"] = Clause(text="a", facts=[("x","belongs","group_a")], provides=["answer"])
        guard = self.guard(data, case, private=[("x","belongs","group_a"),("group_a","risky",YES)],
                           history=[("group_a","risky",YES)])
        self.assertIn(("x","d",YES), guard.model_context(None, DAY)["facts"])
        self.assertEqual(guard.run("ana","show_a",DAY,draft="a",strict=True)["action"], "review")

    def test_cierre_interno_y_problog_coinciden_en_grafos_artificiales(self):
        import random
        from isde.graphs import E, P, internal_view, make_schema
        data, _, _ = unary_fixture()
        data["rules"] = [
            {"when":[["?x","a",YES]],"then":["?x","b",YES]},
            {"when":[["?x","b",YES],["?x","c",YES]],"then":["?x","d",YES]},
            {"when":[["?x","c",YES]],"then":["?x","a",YES]},
            {"when":[["?x","b",YES]],"then":["?x","a",YES]},
        ]
        policy = Policy.model_validate(data)
        randomizer = random.Random(42)
        for index in range(12):
            facts = [(entity,predicate,YES)
                     for entity in ["x","y","z"] for predicate in ["a","b","c"]
                     if randomizer.random() < .5]
            graph = internal_view(make_schema(policy), facts, policy)
            probabilities_by_goal = probabilities(policy, observer_view([("shared", fact, 1.0) for fact in facts]))
            for entity in ["x","y","z"]:
                rdf_goal = (E[entity], P["d"], E[YES])
                expected = float(rdf_goal in graph)
                self.assertEqual(probabilities_by_goal.get((entity,"d",YES),0.0), expected,
                                 msg=f"grafo artificial {index}, entidad {entity}")


    def test_hipotesis_adversarial_participa_en_reglas_de_varios_pasos(self):
        data, case, _ = unary_fixture(delivered=.8)
        data["vocabulary"]["q"] = "premisa q"
        data["rules"][0]["when"].append(["?entity","q",YES])
        guard = self.guard(data, case, private=[("x","a",YES),("x","q",YES)])
        note = "pista q"
        guard.observe([Observation(origin="external", at=DAY, user="ana", grade="cited", quote=note,
                                   hypotheses=[Hypothesis(claim=("x","q",YES), quote=note, p_if_seen=.5)])])
        result = guard.run("ana", "show_a", DAY, draft="a", strict=True)
        self.assertEqual(result["action"], "allow")
        self.assertAlmostEqual(result["attempts"][0]["changes"][0]["after"], .4)
        self.assertEqual(guard.hypotheses("luis", DAY), [])
        self.assertNotIn(("x","q",YES),
                         [tuple(item["fact"]) for item in guard.model_context("luis", DAY)["possible_knowledge"]])
