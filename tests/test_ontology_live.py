"""Optional live check of the internal ontology extraction over a fictitious adversarial corpus.

Run with MOSAIC_LIVE_CLAUDE=1 and ANTHROPIC_API_KEY. Thresholds leave room for model variance; the
structural guarantees (no flag triples, every datum with a level) must hold on every source.
"""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import yaml

from isde.claude import Source, propose
from isde.guard import Guard
from isde.model import CONFIDENTIAL, DATA_NODES, Case, Policy, flag_level

ROOT = Path(__file__).resolve().parents[1]
RUN_LIVE = os.environ.get("MOSAIC_LIVE_CLAUDE") == "1" and bool(os.environ.get("ANTHROPIC_API_KEY"))
DAY = date(2026, 11, 22)


def extract(policy, case, item):
    guard = Guard(":memory:", policy, case)
    try:
        return propose(policy, Source(origin=item["id"], at=DAY, text=item["text"]),
                       graph_context=guard.model_context(None, DAY))
    except ValueError as exc:
        return exc
    finally:
        guard.close()


def protected(events, text):
    """A literal counts as protected by a protected value or by a confidential node that names it."""
    literals = [l.value for e in events for l in e.protected_literals]
    nodes = [n for e in events for n in e.entities if n.confidentiality in CONFIDENTIAL]
    return any(text in value or value in text for value in literals) or any(
        text in n.label or (n.label in text and len(n.label) >= 2) for n in nodes)


@unittest.skipUnless(RUN_LIVE, "Usa MOSAIC_LIVE_CLAUDE=1 y ANTHROPIC_API_KEY para llamar a Claude")
class OntologyLiveTests(unittest.TestCase):
    def test_corpus_extraction_is_structural_and_protects_secrets(self):
        policy, case = Policy.load(ROOT / "examples" / "mvp-policy.yaml"), Case.load(ROOT / "examples" / "mvp-case.yaml")
        corpus = yaml.safe_load((ROOT / "tests" / "ontology-corpus.yaml").read_text(encoding="utf-8"))
        with ThreadPoolExecutor(6) as pool:
            results = list(pool.map(lambda item: extract(policy, case, item), corpus))
        failed = {item["id"]: str(r) for item, r in zip(corpus, results) if isinstance(r, Exception)}
        # Fail-closed rejections (an incomplete extraction near a secret) happen in ~7% of sources; more is a regression.
        self.assertLessEqual(len(failed), 3, failed)
        secrets = public = covered = exposed = 0
        for item, events in zip(corpus, results):
            if isinstance(events, Exception):
                continue
            # A predicate a proposed rule reasons over may keep a boolean state, as in (?x, a, yes).
            ruled = {atom[1] for e in events for r in e.rules for atom in [*r.rule.when, r.rule.then]}
            facts = [f for e in events for f in e.facts if f[1] not in ruled]
            self.assertEqual([f for f in facts if flag_level(f[1], f[2]) is not None], [], item["id"])
            self.assertEqual([n.id for e in events for n in e.entities
                              if n.type in DATA_NODES and n.confidentiality is None], [], item["id"])
            secrets += len(item["secret_literals"])
            covered += sum(protected(events, s) for s in item["secret_literals"])
            public += len(item["public_literals"])
            exposed += sum(protected(events, s) for s in item["public_literals"])
        self.assertGreaterEqual(covered / secrets, .8, f"{covered}/{secrets} secretos protegidos")
        self.assertLessEqual(exposed, max(1, public // 10), f"{exposed}/{public} públicos marcados como confidenciales")
