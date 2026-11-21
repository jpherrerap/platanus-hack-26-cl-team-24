import argparse
import json
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

from .graphs import role_graph, user_grants
from .guard import Guard
from .model import Batch, Case, Policy

ACTIONS = {"allow": ("PERMITIR", "32"), "repair": ("REPARAR", "33"), "review": ("REVISIÓN HUMANA", "31")}
REASONS = {"unmapped_draft": "no coincide con ninguna respuesta del catálogo",
           "incomplete_task": "la alternativa no completa la tarea",
           "unverified_content": "contiene hechos sin respaldo en el grafo interno"}
# Installed wheels carry the examples as isde/examples; a source checkout keeps them at the repo root.
HERE = Path(__file__).resolve().parent
EXAMPLES = HERE / "examples" if (HERE / "examples").is_dir() else HERE.parent / "examples"


def show(value):
    print(json.dumps(value, indent=2, ensure_ascii=False))


def painter(stream):
    color = stream.isatty() and "NO_COLOR" not in os.environ
    return lambda text, code: f"\033[{code}m{text}\033[0m" if color else text


def pct(value):
    return f"{value:.0%}"


def fact(triple):
    return "{} · {} = {}".format(*triple)


def story(guard, user, task, at, draft, paint):
    """Deliver one demo request and narrate it as four numbered steps for a live audience."""
    # Context is captured before delivery, otherwise it would include the answer being judged.
    context = guard.model_context(user, at)
    _, allowed = user_grants(guard.policy, role_graph(guard.schema, guard.case, at), user)
    result = guard.run(user, task, at, f"demo_{user}", draft=draft)
    label, code = ACTIONS[result["action"]]
    lines = ["", paint(f"━━ {user} · rol {', '.join(context['roles']) or 'sin rol'} ━━", "1"),
             paint("1. Contexto", "1") + f"   pide la tarea «{task}»"]
    if allowed:
        lines.append(f"   su rol puede recibir: {', '.join(sorted(allowed))}")
    lines += [f"   ya pudo saber ({pct(k['p_known'])}): {fact(k['fact'])}"
              for k in context["possible_knowledge"]] or ["   no tiene pistas previas"]
    lines += [paint("2. Borrador del agente", "1"), f"   «{draft}»", paint("3. Evaluación", "1")]
    for number, attempt in enumerate(result["attempts"], 1):
        prefix = "   borrador:" if number == 1 else f"   alternativa {number}:"
        if "reason" in attempt:
            lines.append(f"{prefix} {REASONS.get(attempt['reason'], attempt['reason'])}")
            continue
        blocked = {(b["subject"], b["family"], b["status"]) for b in attempt["blocked"]}
        for change in attempt["changes"]:
            goal = (change["subject"], change["family"], change["status"])
            rule = guard.policy.families[change["family"]]
            if goal not in blocked:
                verdict = f"bajo el umbral ({pct(rule.absolute)} o +{pct(rule.increase)})"
            elif change["direct"]:
                verdict = paint("RETENIDO", "31") + ": lo revela directamente"
            elif change["after"] >= rule.absolute:
                verdict = paint("RETENIDO", "31") + f": alcanza {pct(rule.absolute)}"
            else:
                verdict = paint("RETENIDO", "31") + f": sube al menos +{pct(rule.increase)}"
            lines += [f"{prefix} P({fact(goal)}) {pct(change['before'])} → {pct(change['after'])}", f"      {verdict}"]
        if not attempt["changes"]:
            restricted = " restringida para su rol" if allowed else ""
            lines.append(f"{prefix} ninguna conclusión protegida{restricted} aumenta")
    why = {"allow": "ninguna deducción protegida supera el umbral",
           "repair": "se entrega la mejor alternativa segura",
           "review": "ninguna alternativa es admisible; no se entrega nada"}[result["action"]]
    lines.append("   decisión: " + paint(label, f"1;{code}") + f" · {why}")
    lines.append(paint("4. Bandeja de entrada", "1"))
    lines += [f"   «{item['text']}»" for item in guard.inbox(user)] or ["   (vacía)"]
    return result, "\n".join(lines)


def demo(guard, policy, case, history, as_json):
    at = date(2026, 1, 4)
    guard.observe(Batch.load(history).observations)
    task = next(iter(case.tasks))
    # The agent proposes the most useful catalog answer; ISDE decides what is actually delivered.
    draft = " ".join(case.clauses[key].text for key in case.tasks[task].options[0].clauses)
    users = list(dict.fromkeys(a.user for a in case.assignments))
    paint, results = painter(sys.stdout), []
    if not as_json:
        families = ", ".join(f"{name} ({family.description.rstrip('.')})" for name, family in policy.families.items())
        print(paint(f"ISDE · {case.org}/{case.operation} · {at.isoformat()}", "1"))
        print(f"Conclusión protegida: {families}")
        print("El agente propone el mismo borrador a cada persona; ISDE decide qué llega a su bandeja.")
    for user in users:
        result, text = story(guard, user, task, at, draft, paint)
        results.append({"user": user, **result})
        if not as_json:
            print(text)
    if as_json:
        show(results)
    else:
        print("\n" + paint("Resumen", "1") + " · " + " · ".join(
            f"{r['user']} {paint(ACTIONS[r['action']][0], ACTIONS[r['action']][1])}" for r in results))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="isde", description="ISDE · evaluación local de respuestas de agentes")
    parser.add_argument("--policy", default=str(EXAMPLES / "policy.yaml"))
    parser.add_argument("--case", default=str(EXAMPLES / "case.yaml"))
    parser.add_argument("--db", default="isde.sqlite")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="Caso aislado y reproducible en memoria").add_argument(
        "--json", action="store_true", help="Salida JSON en lugar del relato")
    commands.add_parser("serve", help="Demo visual en http://127.0.0.1 (en memoria, sin entregas)").add_argument(
        "--port", type=int, default=8000)
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
        if args.command == "serve":
            from .serve import serve
            serve(policy, case, Path(args.case).with_name("history.yaml"), args.port)
            return 0
        guard = Guard(":memory:" if args.command == "demo" else args.db, policy, case)
        if args.command == "demo":
            demo(guard, policy, case, Path(args.case).with_name("history.yaml"), args.json)
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
            show(guard.run(args.user, args.task, at, getattr(args, "request_id", None), draft=args.draft))
        return 0
    except (ValueError, KeyError, OSError, ImportError, sqlite3.Error) as exc:
        # One line for the operator; any remaining multi-line detail stays out of the terminal.
        print(f"ISDE: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}", file=sys.stderr)
        return 2
    finally:
        if guard:
            guard.close()
