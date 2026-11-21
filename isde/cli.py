import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .guard import Guard
from .model import Batch, Case, Policy


def show(value):
    print(json.dumps(value, indent=2, ensure_ascii=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description="ISDE · evaluación local de respuestas de agentes")
    parser.add_argument("--policy", default="examples/policy.yaml")
    parser.add_argument("--case", default="examples/case.yaml")
    parser.add_argument("--db", default="isde.sqlite")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="Caso aislado y reproducible en memoria")
    commands.add_parser("key", help="Guardar API key de Claude en el llavero")
    commands.add_parser("load", help="Registrar evidencia desde un adaptador de confianza").add_argument("file")
    for command, help_text in [("interpret", "Incorporar pistas de una tarjeta con triples validados"),
                               ("propose", "Proponer triples desde texto para su revisión")]:
        card = commands.add_parser(command, help=help_text)
        card.add_argument("file")
        card.add_argument("--grade", choices=["possible_access", "external_hint", "delivered", "read", "cited"])
    for cmd in ["preview", "deliver"]:
        run = commands.add_parser(cmd)
        run.add_argument("user")
        run.add_argument("task")
        run.add_argument("--assess", action="store_true", help="Pedir a Claude hipótesis desde la vista del destinatario")
        run.add_argument("--draft", help="Texto propuesto por el agente")
        if cmd == "deliver":
            run.add_argument("request_id")
        else:
            run.add_argument("--at", type=date.fromisoformat, default=date.today())
    commands.add_parser("inbox").add_argument("user")
    args = parser.parse_args(argv)
    guard = None
    try:
        if args.command == "key":
            from .claude import credentials
            credentials(configure=True)
            print("Clave guardada.")
            return 0
        policy, case = Policy.load(args.policy), Case.load(args.case)
        guard = Guard(":memory:" if args.command == "demo" else args.db, policy, case)
        if args.command == "demo":
            history = Path(args.case).with_name("history.yaml")
            guard.observe(Batch.load(history).observations)
            for user in ("ana", "luis", "sofia", "ines"):
                result = guard.run(user, "due", date(2026, 1, 4))
                show({"user": user, **result})
        elif args.command == "load":
            guard.observe(Batch.load(args.file).observations)
        elif args.command == "propose":
            from .claude import Source, propose
            source = Source.load(args.file)
            events = propose(policy, source, args.grade, graph_context=guard.model_context(source.user, source.at))
            show(Batch(observations=events).model_dump(mode="json", exclude_none=True))
        elif args.command == "interpret":
            from .claude import Card, interpret
            card = Card.load(args.file)
            events = interpret(policy, card, args.grade, graph_context=guard.model_context(card.user, card.at))
            guard.observe(events)
            show({"selected": len(events)})
        elif args.command == "inbox":
            show(guard.inbox(args.user))
        else:
            at = getattr(args, "at", None) or date.today()
            show(guard.run(args.user, args.task, at, getattr(args, "request_id", None), draft=args.draft, assess=args.assess))
        return 0
    except (ValueError, KeyError, OSError, ImportError) as exc:
        print(f"ISDE: {exc}", file=sys.stderr)
        return 2
    finally:
        if guard:
            guard.close()
