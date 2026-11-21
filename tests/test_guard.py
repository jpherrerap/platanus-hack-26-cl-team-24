import concurrent.futures
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from isde.claude import Card, Source, interpret, propose
from isde.graphs import V, observer_view, probabilities, rdf_fact
from rdflib import RDF
from isde.guard import Guard
from isde.model import Assignment, Batch, Case, Clause, Hypothesis, Observation, Option, Policy, Task

ROOT = Path(__file__).resolve().parents[1] / "examples"
DAY = date(2026, 1, 4)
SIGN = ("o17", "signature", "signed")
LINK = ("o17", "counterparty", "betaai")
GOAL = ("betaai", "offer_status", "signed")


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "state.sqlite")
        self.policy, self.case = Policy.load(ROOT / "policy.yaml"), Case.load(ROOT / "case.yaml")
        self.g = Guard(self.path, self.policy, self.case)
        self.addCleanup(self.g.close)
        self.g.observe(Batch.load(ROOT / "history.yaml").observations)

    def decision(self, user, task="due", req=None):
        return self.g.run(user, task, DAY, req)

    def test_two_vendors_same_role(self):
        self.assertEqual(self.decision("ana")["action"], "allow")
        result = self.decision("luis")
        self.assertEqual(result["text"], "El trámite vence el viernes.")
        self.assertEqual(result["action"], "repair")
        self.assertAlmostEqual(result["attempts"][0]["blocked"][0]["after"], .735)

    def test_model_ontologies_are_scoped(self):
        from json import dumps
        self.assertIn("betaai", dumps(self.g.model_context(None, DAY)))
        observed = self.g.model_context("luis", DAY)
        self.assertEqual(observed["roles"], ["vendor"])
        self.assertIn("signed", dumps(observed))
        self.assertNotIn("betaai", dumps(observed))
        self.assertEqual(self.g.model_context("ana", DAY)["possible_knowledge"], [])

    def test_permission_is_evidence_not_knowledge(self):
        result = self.decision("ines")
        self.assertAlmostEqual(result["attempts"][0]["changes"][0]["after"], .1125)
        self.assertEqual(self.decision("ines", "signature")["action"], "review")

    def test_scope_vigencia_e_herencia(self):
        self.assertEqual(self.decision("sofia", "signature")["action"], "allow")
        a = Assignment(user="luis", role="legal", org="alphacorp", operation="elsewhere", start=DAY)
        self.case.assignments.extend([a, a.model_copy(update={"operation":"atlas", "org":"other"}),
                                     a.model_copy(update={"operation":"atlas", "start":date(2025,1,1), "end":DAY})])
        self.assertEqual(self.decision("luis", "signature")["action"], "review")
        self.assertEqual(self.decision("luis")["action"], "repair")

    def test_prior_exposure_survives_role_expiry(self):
        self.case.assignments[-1].end = DAY
        self.assertAlmostEqual(self.decision("ines")["attempts"][0]["changes"][0]["after"], .1125)
        event = Observation(origin="later", user="ines", grade="possible_access", resource="signature_record",
                            facts=[LINK], at=DAY)
        self.g.observe([event])
        self.assertFalse(any(r[0] == "src:later" for r in self.g.records("ines", DAY)))

    def test_correlated_premises_and_copies(self):
        records = [("one", SIGN, .90), ("one", LINK, .75), ("one", SIGN, .90)]
        self.assertAlmostEqual(probabilities(self.policy, observer_view(records))[GOAL], .75)
        records[1] = ("two", LINK, .75)
        self.assertAlmostEqual(probabilities(self.policy, observer_view(records))[GOAL], .675)

    def test_history_is_per_scope_and_recipient(self):
        self.decision("luis", req="r1")
        self.assertEqual(self.g.inbox("ana"), [])
        other = Guard(self.path, self.policy, self.case.model_copy(update={"operation":"elsewhere"}))
        self.addCleanup(other.close)
        self.assertEqual(other.records("luis", DAY), [])
        self.assertEqual(other.inbox("luis"), [])

    def test_preview_review_repair_and_idempotent_inbox(self):
        self.decision("luis")
        self.decision("luis", "signature", "refused")
        self.assertEqual(self.g.inbox("luis"), [])
        first = self.decision("luis", req="once")
        self.assertEqual(self.g.run("luis", "due", date(2026, 1, 5), "once"), first)
        self.assertEqual(self.g.inbox("luis"), [{"id":"once", "text":"El trámite vence el viernes."}])
        self.assertNotIn(LINK, [r[1] for r in self.g.records("luis", DAY)])
        with self.assertRaises(ValueError):
            self.decision("luis", "signature", "once")

    def test_cumulative_across_deliveries_and_concurrency(self):
        self.case.clauses["just_sign"] = Clause(text="O-17 está firmado.", facts=[SIGN], provides=["describe"])
        for t, c in [("who","identify"),("state","just_sign")]:
            self.case.clauses[c].provides = ["describe"]
            self.case.tasks[t] = Task(requires=["describe"], options=[Option(clauses=[c])])
        def release(task):
            guard = Guard(self.path, self.policy, self.case)
            try:
                return guard.run("ana", task, DAY, task)["action"]
            finally:
                guard.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(pool.map(release, ["who","state"]), ["allow","review"])
        self.assertEqual(len(self.g.inbox("ana")), 1)

    def test_protects_negative_and_treats_missing_as_unknown(self):
        negative = ("delta", "offer_status", "unsigned")
        self.case.clauses["negative"] = Clause(text="La oferta para Delta está sin firmar.",
                                              facts=[negative], provides=["answer_signature"])
        self.case.tasks["negative"] = Task(requires=["answer_signature"], options=[Option(clauses=["negative"])])
        self.assertEqual(self.decision("sofia", "negative")["attempts"][0]["reason"], "unverified_content")
        self.g.observe([Observation(origin="delta", facts=[("o22","counterparty","delta"),
                                                          ("o22","signature","unsigned")], at=DAY)])
        self.assertEqual(self.decision("ana", "negative")["action"], "review")
        self.assertEqual(self.decision("sofia", "negative")["action"], "allow")

    def test_recalibration_and_independent_external_hypothesis(self):
        self.g.observe([Observation(origin="outside", facts=[SIGN], at=DAY, grade="external_hint",
                                    user="ana", confidence=.4)])
        self.assertAlmostEqual(self.decision("ana")["attempts"][0]["changes"][0]["after"], .30)
        self.policy.evidence["cited"] = .1
        self.assertEqual(self.decision("luis")["action"], "allow")

    def test_agent_draft_is_bound_to_delivery(self):
        canonical = self.case.clauses["deadline"].text
        simple = self.g.run("luis", "due", DAY, draft=canonical)
        self.assertEqual(simple["action"], "allow")
        raw = "Contenido privado fuera de catálogo"
        safe = self.g.run("ana", "due", DAY, "from_agent", draft=raw)
        self.assertEqual((safe["action"], safe["text"]), ("repair", canonical))
        self.assertNotIn(raw, str(self.g.inbox("ana")))
        with self.assertRaises(ValueError):
            self.g.run("ana", "due", DAY, "from_agent", draft=canonical)

    def test_future_evidence_unavailable(self):
        self.g.observe([Observation(origin="next", facts=[SIGN], at=date(2027,1,1), grade="cited",user="ana")])
        self.assertEqual(self.decision("ana")["action"], "allow")

    def test_unverified_and_incomplete_replies(self):
        self.case.tasks["due"].options = [Option(clauses=["identify"])]
        self.assertEqual(self.decision("ana")["action"], "review")
        self.g.observe([Observation(origin="conflict", facts=[("o17","signature","unsigned")], at=DAY)])
        self.assertEqual(self.decision("sofia", "signature")["action"], "review")


class ClaudeTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy.load(ROOT / "policy.yaml")
        self.card = Card.load(ROOT / "vendor-card.yaml")

    def client(self, items):
        def create(**request):
            self.prompt = request["messages"][0]["content"]
            payload = items(json.loads(self.prompt)) if callable(items) else {"items": items}
            block = SimpleNamespace(type="tool_use",name="select_evidence",input=payload)
            return SimpleNamespace(content=[block], stop_reason="tool_use")
        return SimpleNamespace(messages=SimpleNamespace(create=create))

    def test_external_context_and_grounded_output(self):
        events = interpret(self.policy, self.card, "possible_access",
                           client=self.client([{"snippet":"signature_o17","confidence":.22}]))
        self.assertNotIn("betaai", self.prompt.lower())
        self.assertNotIn("luis", self.prompt)
        self.assertEqual(events[0].facts, [SIGN])
        self.assertEqual(events[0].confidence, .22)
        with self.assertRaises(ValueError):
            interpret(self.policy,self.card,"possible_access",client=self.client([{"snippet":"fake","confidence":1}]))

    def test_discovery_of_new_entities_without_private_labels(self):
        source = Source.load(ROOT / "internal-source.yaml")
        quote = "La contraparte del expediente O-18 es Delta."
        choice = {"fact":["o18","counterparty","delta"],"quote":quote,"confidence":.8}
        events = propose(self.policy, source, client=self.client([choice]))
        self.assertEqual(events[0].facts, [("o18","counterparty","delta")])
        self.assertIsNone(events[0].confidence)
        with self.assertRaises(ValueError):
            propose(self.policy, source, client=self.client([{**choice,"quote":"Texto inventado"}]))

    def guard(self):
        guard = Guard(":memory:", self.policy, Case.load(ROOT / "case.yaml"))
        self.addCleanup(guard.close)
        return guard

    def test_general_hypothesis_is_observer_only_and_combines_as_conditional(self):
        guard = self.guard()
        quote = "Al vencimiento recojan todos los equipos."
        goal = ("cliente_nube", "renewal_outlook", "likely_exit")
        source = Source(origin="email_cliente", at=DAY, user="ana", text=quote)
        pick = {"claim": goal, "quote": quote, "p_if_seen": .8}
        client = self.client(lambda _: {"hypotheses": [pick]})
        events = propose(self.policy, source, "read", graph_context=guard.model_context("ana", DAY), client=client)
        guard.observe(events)
        guard.observe(events)
        estimates = guard.hypotheses("ana", DAY)
        self.assertEqual(len(estimates), 1)
        self.assertEqual((estimates[0].p_seen, estimates[0].p_if_seen), (.9, .8))
        view = observer_view([], estimates)
        self.assertTrue(any(view.subjects(RDF.type, V.Interpretation)))
        self.assertNotIn(rdf_fact(goal), view)
        self.assertAlmostEqual(probabilities(self.policy, view)[goal], .72)
        self.assertEqual(guard.records("ana", DAY), [])
        self.assertEqual(guard.model_context("luis", DAY)["hypotheses"], [])
        self.assertEqual(guard.model_context(None, DAY)["facts"], [])
        self.assertEqual(guard.hypotheses("ana", date(2025, 1, 1)), [])
        self.assertEqual(guard.model_context("ana", DAY)["hypotheses"][0]["quote"], quote)

    def test_hypothesis_can_be_a_premise_for_a_later_disclosure(self):
        g = self.guard()
        g.observe(Batch.load(ROOT / "history.yaml").observations)
        q = "Ya tenemos todas las rúbricas."
        g.observe([Observation(origin="note", user="ana", at=DAY, grade="read", quote=q,
                               hypotheses=[Hypothesis(claim=SIGN, quote=q, p_if_seen=.95)])])
        self.assertEqual(g.run("ana", "due", DAY)["action"], "repair")
        self.assertEqual(g.run("ines", "due", DAY)["action"], "allow")

    def test_rejected_drafts_do_not_change_history_same_number_different_context(self):
        g = self.guard()
        g.observe([Observation(origin="private", at=DAY, facts=[("private_label","counterparty","hidden")])])
        number = "12.345.678-9"
        employee = "En la ficha de la persona figura " + number + "."
        order = "El folio de pedido es " + number + "."
        goal = ("referencia", "information_type", "personal_identifier")
        for key, text in [("employee", employee), ("order", order)]:
            g.case.clauses[key] = Clause(text=text, facts=[], provides=["answer"])
            g.case.tasks[key] = Task(requires=["answer"], options=[Option(clauses=[key])])
        def score(context):
            self.assertNotIn("private_label", str(context))
            self.assertFalse(context["formato_numerico"][0]["checksum_matches"])
            human = context["texto"] == employee
            return {"hypotheses": [{"claim":goal, "quote":context["texto"], "p_if_seen":.90 if human else .08}]}
        client = self.client(score)
        preview = g.run("ana","employee",DAY,assess=True,client=client)
        self.assertAlmostEqual(preview["attempts"][0]["blocked"][0]["after"], .675)
        self.assertEqual((preview["action"], preview["text"]), ("review", None))
        self.assertEqual(g.run("ana","employee",DAY,"held",assess=True,client=client)["action"], "review")
        self.assertEqual(g.hypotheses("ana",DAY), [])
        result = g.run("ana","order",DAY,"one",assess=True,client=client)
        self.assertEqual([h.quote for h in g.hypotheses("ana",DAY)], [order])
        self.assertEqual(g.hypotheses("ana",DAY)[0].p_if_seen, .08)
        self.assertEqual((result["action"],result["text"]), ("allow",order))
        self.assertAlmostEqual(result["attempts"][0]["changes"][0]["after"], .06)
        self.assertEqual(g.run("ana","order",DAY,"one",assess=True,client=object()), result)
        self.assertEqual(g.model_context("luis",DAY)["hypotheses"], [])

    def test_scoped_hypotheses_require_actual_quotes_and_known_predicates(self):
        guard = self.guard()
        q = "Entrega aprobada."
        claim = Hypothesis(claim=SIGN, quote=q, p_if_seen=.8)
        with self.assertRaises(ValueError):
            Observation(origin="paper", at=DAY, quote=q, hypotheses=[claim])
        for change in [{"quote":"otra cita"}, {"claim":("p","invented","q")}, {"p_if_seen":3}]:
            client = self.client(lambda _: {"hypotheses": [{**claim.model_dump(), **change}]})
            source = Source(origin="paper", at=DAY, user="ana", text=q)
            with self.assertRaises(ValueError):
                propose(self.policy, source, "read", client=client, graph_context=guard.model_context("ana",DAY))
        self.assertEqual(guard.hypotheses("ana",DAY), [])
        with self.assertRaises(ValueError):
            propose(self.policy, source, "read", client=object(), graph_context=guard.model_context(None,DAY))

    def test_required_assessment_fails_closed_and_context_change_requires_rereview(self):
        g = self.guard()
        g.observe(Batch.load(ROOT / "history.yaml").observations)
        self.policy.assess_drafts = True
        result = g.run("ana","due",DAY,"a",client=object())
        self.assertEqual(result["attempts"][0]["reason"], "assessment_unavailable")
        def change(_):
            g.observe([Observation(origin="new", at=DAY, user="ana", grade="cited", facts=[SIGN])])
            return {"hypotheses":[]}
        result = g.run("ana","due",DAY,"a",client=self.client(change))
        self.assertEqual(result["attempts"][0]["reason"], "observer_context_changed")
        self.assertEqual(g.inbox("ana"), [])

    def test_repair_saves_only_the_interpretation_of_the_delivered_alternative(self):
        g = self.guard()
        g.observe(Batch.load(ROOT / "history.yaml").observations)
        def evaluate(context):
            text = context["texto"]
            p = .95 if "BetaAI" in text else .05
            return {"hypotheses": [{"claim": ["cliente", "renewal_outlook", "likely_exit"],
                                    "quote": text, "p_if_seen": p}]}
        result = g.run("ana", "due", DAY, "sent", assess=True, client=self.client(evaluate))
        self.assertEqual(result["action"], "repair")
        self.assertEqual([h.quote for h in g.hypotheses("ana",DAY)], [result["text"]])
        self.assertEqual(g.hypotheses("ana",DAY)[0].p_if_seen, .05)
        self.assertNotIn("BetaAI", g.inbox("ana")[0]["text"])

    def test_hypotheses_respect_historical_permissions_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            db = str(Path(folder) / "pilot.sqlite")
            case = Case.load(ROOT / "case.yaml")
            hint = Hypothesis(claim=SIGN, quote="La revisión terminó.", p_if_seen=.85)
            g = Guard(db, self.policy, case)
            try:
                for user in ["ana","ines"]:
                    g.observe([Observation(origin="record", user=user, grade="possible_access",
                                           resource="signature_record", at=DAY,
                                           quote=hint.quote, hypotheses=[hint])])
            finally:
                g.close()
            g = Guard(db, self.policy, case)
            self.addCleanup(g.close)
            self.assertEqual(g.hypotheses("ana", DAY), [])
            self.assertEqual(g.hypotheses("ines", DAY)[0].p_seen, .15)
            different = Guard(db, self.policy, case.model_copy(update={"operation": "other"}))
            self.addCleanup(different.close)
            self.assertEqual(different.hypotheses("ines", DAY), [])
