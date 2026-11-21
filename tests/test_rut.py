"""Exercise the agent's actual delivery boundary using synthetic data and no live directory."""
import unittest
from datetime import date
from pathlib import Path

from isde.guard import Guard
from isde.model import Assignment, Case, Clause, Observation, Option, Policy, Task

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
DAY = date(2026, 1, 7)
# Invented test value, never sourced from a person.
BODY = "98000001"
RUT = "98.000.001-K"
PERSON = "persona_demo"
DIRECTORY = (PERSON, "directory_reference", "id_" + BODY + "k")
TYPED = (PERSON, "personal_rut", "rut_" + BODY + "_k")
SAFE = "Puedes completar la identificación en el portal."


class RutDeliveryTests(unittest.TestCase):
    def setUp(self):
        policy = Policy.load(EXAMPLES / "policy.yaml")
        # The directory can contain these fields. Neither is configured as a secret.
        policy.vocabulary["directory_reference"] = "Referencia interna"
        self.case = Case.load(EXAMPLES / "case.yaml")
        self.guard = Guard(":memory:", policy, self.case)
        self.addCleanup(self.guard.close)

    def observe(self, *facts):
        self.guard.observe([Observation(origin="synthetic_directory", at=DAY, facts=list(facts))])

    def task(self, *options):
        alternatives = []
        for index, (text, facts) in enumerate(options):
            key = f"answer_{index}"
            self.case.clauses[key] = Clause(text=text, facts=facts, provides=["answer"])
            alternatives.append(Option(clauses=[key]))
        self.case.tasks["identify"] = Task(requires=["answer"], options=alternatives)

    def deliver(self, draft=None, user="ana"):
        return self.guard.run(user, "identify", DAY, request_id="case", draft=draft)

    def test_missing_value_does_not_fill_in_private_fact(self):
        self.task((f"Su identificador es {RUT}.", [DIRECTORY]))
        self.assertEqual(self.deliver()["action"], "review")
        self.assertEqual(self.guard.inbox("ana"), [])

    def test_unlabelled_private_value_is_not_published(self):
        self.observe(DIRECTORY)
        self.task((f"Para el formulario de la persona, usa {RUT}.", [DIRECTORY]))
        self.assertEqual(self.deliver()["action"], "review")
        self.assertEqual(self.guard.inbox("ana"), [])

    def test_invented_value_without_any_secret_or_tag_is_held(self):
        self.task((f"Completa la casilla con {RUT}.", []))
        self.assertEqual(self.deliver()["action"], "review")
        self.assertEqual(self.guard.inbox("ana"), [])

    def test_sensitive_fallback_is_itself_checked(self):
        self.observe(DIRECTORY)
        self.task((RUT, [DIRECTORY]), (SAFE, []))
        draft = "Puedo facilitarte el dato para completar el formulario."
        result = self.deliver(draft)
        self.assertEqual((result["action"], result["text"]), ("repair", SAFE))
        self.assertEqual(self.guard.inbox("ana"), [{"id": "case", "text": SAFE}])

    def test_confirming_known_number_without_printing_it_is_held_when_tagged(self):
        self.observe(TYPED)
        self.task(("Sí, coincide con el número que ya tenías.", [TYPED]))
        self.assertEqual(self.deliver()["action"], "review")
        self.assertEqual(self.guard.inbox("ana"), [])

    def test_only_body_from_known_private_rut_is_held(self):
        self.observe(TYPED)
        self.task(("La parte numérica es " + BODY + ".", []))
        self.assertEqual(self.deliver()["action"], "review")
        self.assertEqual(self.guard.inbox("ana"), [])

    def test_explicit_label_with_a_mistyped_check_digit(self):
        self.task(("Su RUT es 98.000.001-3.", []))
        self.assertEqual(self.deliver()["action"], "review")
        self.assertEqual(self.guard.inbox("ana"), [])

    def test_formats_without_a_keyword(self):
        for i, value in enumerate([RUT, "98000001-k", "98000001K", "98.000.001–k", "98.000.006-0", TYPED[2]]):
            with self.subTest(i=i):
                self.task((f"El dato de la persona es {value}.", []))
                user = f"test_{i}"
                self.case.assignments.append(Assignment(user=user, role="vendor", org=self.case.org,
                                                        operation=self.case.operation, start=DAY))
                self.assertEqual(self.deliver(user=user)["action"], "review")
                self.assertEqual(self.guard.inbox(user), [])

    def test_join_with_recipient_history_without_any_number_in_the_reply(self):
        field = ("ficha_azul", "personal_rut", TYPED[2])
        holder = ("ficha_azul", "identifies_person", PERSON)
        self.observe(field, holder)
        self.guard.observe([Observation(origin="prior_email", user="luis", grade="cited",
                                        at=DAY, facts=[field])])
        text = "La ficha azul corresponde a Persona Demo."
        self.task((text, [holder]))
        self.assertEqual(self.deliver(user="ana")["action"], "allow")
        self.assertEqual(self.deliver(user="luis")["action"], "review")
        self.assertEqual(self.guard.inbox("luis"), [])

    def test_safe_help_and_unrelated_number(self):
        text = "Puedes consultar tu RUT en el portal. El folio de la solicitud es 42017."
        self.task((text, []))
        self.assertEqual(self.deliver()["action"], "allow")
        self.assertEqual(self.guard.inbox("ana")[0]["text"], text)
