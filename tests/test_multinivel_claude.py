"""Optional live reasoning checks using only artificial facts and rules."""
import os
import unittest
from pathlib import Path

from isde.claude import assess_hypotheses
from isde.model import Family, Policy

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "policy.yaml"
RUN_LIVE = os.environ.get("MOSAIC_LIVE_CLAUDE") == "1" and bool(os.environ.get("ANTHROPIC_API_KEY"))
TEXT = "En la entidad nodo, la propiedad a es verdadera."
OBSERVER = {"kind": "observer", "roles": [], "possible_knowledge": [], "hypotheses": []}


def abstract_policy(complete):
    policy = Policy.load(EXAMPLE)
    policy.vocabulary.update({
        "a": "Para cualquier entidad, si a es verdadera entonces b es verdadera.",
        "b": ("Para cualquier entidad, si b es verdadera entonces c es verdadera."
              if complete else "b es una propiedad abstracta."),
        "c": "Para cualquier entidad, si c es verdadera entonces d es verdadera.",
    })
    policy.families["d"] = Family(
        description="d es una propiedad abstracta confidencial. El estado yes indica que d es verdadera.",
        statuses=["yes"], absolute=.75, increase=.45,
    )
    policy.chains = []
    return policy


@unittest.skipUnless(RUN_LIVE, "Usa MOSAIC_LIVE_CLAUDE=1 y ANTHROPIC_API_KEY para llamar a Claude")
class MultinivelClaudeTests(unittest.TestCase):
    def confidence(self, complete):
        hypotheses = assess_hypotheses(abstract_policy(complete), TEXT, OBSERVER)
        return max((h.p_if_seen for h in hypotheses if h.claim == ("nodo", "d", "yes")), default=0.0)

    def test_cadena_completa_el_modelo_propone_d(self):
        self.assertGreaterEqual(self.confidence(complete=True), .5)

    def test_cadena_incompleta_no_debe_concluir_d_con_confianza_alta(self):
        self.assertLess(self.confidence(complete=False), .5)
