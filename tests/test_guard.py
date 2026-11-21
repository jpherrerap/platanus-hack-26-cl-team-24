import concurrent.futures
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from isde.cli import main
from isde.claude import Card, Source, interpret, propose
from isde.graphs import observer_view, probabilities
from isde.guard import Guard
from isde.model import Assignment, Batch, Case, Clause, Observation, Option, Policy, Task

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

    def test_draft_whitespace_and_unknown_task(self):
        draft = "  El trámite vence el viernes.\n El expediente O-17 corresponde a BetaAI. "
        self.assertEqual(self.g.run("ana", "due", DAY, draft=draft)["action"], "allow")
        self.assertEqual(self.g.run("ana", "due", DAY, draft="")["action"], "repair")
        with self.assertRaises(ValueError):
            self.decision("ana", "missing")

    def test_future_evidence_unavailable(self):
        self.g.observe([Observation(origin="next", facts=[SIGN], at=date(2027,1,1), grade="cited",user="ana")])
        self.assertEqual(self.decision("ana")["action"], "allow")

    def test_unverified_and_incomplete_replies(self):
        self.case.tasks["due"].options = [Option(clauses=["identify"])]
        self.assertEqual(self.decision("ana")["action"], "review")
        self.g.observe([Observation(origin="conflict", facts=[("o17","signature","unsigned")], at=DAY)])
        self.assertEqual(self.decision("sofia", "signature")["action"], "review")


class ServeTests(unittest.TestCase):
    def setUp(self):
        from isde.serve import respond
        self.g = Guard(":memory:", Policy.load(ROOT / "policy.yaml"), Case.load(ROOT / "case.yaml"))
        self.addCleanup(self.g.close)
        self.g.observe(Batch.load(ROOT / "history.yaml").observations)
        self.get = lambda path: respond(self.g, path)

    def test_page_and_preview_without_delivery(self):
        status, kind, page, nonce = self.get("/")
        self.assertEqual((status, kind.split(";")[0]), (200, "text/html"))
        self.assertIn(f'<script nonce="{nonce}">', page)
        self.assertIn('<option value="sofia">', page)
        status, _, body, _ = self.get("/api/preview?user=luis&task=due")
        self.assertEqual((status, body["action"], body["text"]), (200, "repair", "El trámite vence el viernes."))
        self.assertEqual(body["observer"]["kind"], "observer")
        self.assertNotIn("betaai", str(body["observer"]))
        self.assertEqual(self.g.inbox("luis"), [])

    def test_boundary_validation_and_escaping(self):
        self.assertEqual(self.get("/api/preview?user=mallory&task=due")[:3:2], (400, {"error": "Destinatario desconocido"}))
        self.assertEqual(self.get("/api/preview?user=ana&task=x")[0], 400)
        self.assertEqual(self.get("/api/preview?user=ana&task=due&draft=" + "a" * 2001)[0], 400)
        self.assertEqual(self.get("/nada")[0], 404)
        self.g.case.tasks["<script>"] = self.g.case.tasks["due"]
        self.assertNotIn("<script>", self.get("/")[2].split("<option")[-1])
        _, _, body, _ = self.get("/api/preview?user=ana&task=due&draft=%3Cb%3Ehola")
        self.assertEqual((body["draft"], body["action"]), ("<b>hola", "repair"))


class ClaudeTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy.load(ROOT / "policy.yaml")
        self.card = Card.load(ROOT / "vendor-card.yaml")

    def client(self, items):
        def create(**request):
            self.prompt = request["messages"][0]["content"]
            block = SimpleNamespace(type="tool_use",name="select_evidence",input={"items":items})
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


class DemoTests(unittest.TestCase):
    def demo(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["--policy", str(ROOT / "policy.yaml"), "--case", str(ROOT / "case.yaml"), "demo", *extra])
        self.assertEqual(code, 0)
        return out.getvalue()

    def test_demo_tells_the_story(self):
        text = self.demo()
        luis = text[text.index("━━ luis"):text.index("━━ sofia")]
        for line in ("1. Contexto", "ya pudo saber (98%): o17 · signature = signed", "2. Borrador del agente",
                     "P(betaai · offer_status = signed) 0% → 74%", "RETENIDO", "decisión: REPARAR",
                     "4. Bandeja de entrada\n   «El trámite vence el viernes.»\n"):
            self.assertIn(line, luis)
        self.assertIn("su rol puede recibir: offer_status", text)
        self.assertIn("0% → 11%", text)
        self.assertIn("Resumen · ana PERMITIR · luis REPARAR · sofia PERMITIR · ines PERMITIR", text)
        self.assertNotIn("\033[", text)

    def test_demo_json_parses(self):
        results = json.loads(self.demo("--json"))
        self.assertEqual([r["user"] for r in results], ["ana", "luis", "sofia", "ines"])
        self.assertEqual(results[1]["text"], "El trámite vence el viernes.")


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", str(self.dir / "state.sqlite"), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_demo_runs_outside_repo_root(self):
        cwd = os.getcwd()
        os.chdir(self.dir)
        self.addCleanup(os.chdir, cwd)
        code, out, _ = self.cli("demo")
        self.assertEqual(code, 0)
        self.assertIn("━━ ines", out)

    def test_input_errors_are_one_spanish_line(self):
        (self.dir / "bad.yaml").write_text("a: [1\n")
        (self.dir / "empty.yaml").write_text("")
        (self.dir / "wrong.yaml").write_text("observations: 3\n")
        cases = [(["load", str(self.dir / "missing.yaml")], "No existe el archivo"),
                 (["load", str(self.dir / "bad.yaml")], "YAML inválido"),
                 (["load", str(self.dir / "empty.yaml")], "debe contener un mapeo"),
                 (["load", str(self.dir / "wrong.yaml")], "no es válido en observations"),
                 (["preview", "luis", "nope"], "Tarea desconocida: nope"),
                 (["--db", str(self.dir / "no" / "x.sqlite"), "inbox", "luis"], "No se puede abrir la base de datos")]
        with mock.patch("isde.claude.credentials", return_value=None):
            cases.append((["propose", str(ROOT / "internal-source.yaml")], "Configura la clave"))
            cases.append((["interpret", str(ROOT / "vendor-card.yaml"), "--grade", "possible_access"], "Configura la clave"))
            for argv, message in cases:
                code, _, err = self.cli(*argv)
                self.assertEqual(code, 2, argv)
                self.assertEqual(err.count("\n"), 1, err)
                self.assertIn(message, err)
