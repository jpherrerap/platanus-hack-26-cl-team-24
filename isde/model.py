"""Trusted policy and input contracts. Identifiers never contain executable rules."""
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

Id = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,95}$")]
Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Triple = tuple[Id, Id, Id]
RuleTerm = Annotated[str, Field(pattern=r"^(?:[a-z][a-z0-9_]{0,95}|\?[a-z][a-z0-9_]{0,95})$")]
RuleAtom = tuple[RuleTerm, Id, RuleTerm]
Grade = Literal["possible_access", "external_hint", "delivered", "read", "cited"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @classmethod
    def load(cls, path: str | Path):
        # Every input file passes through here, so errors name the file in one Spanish line.
        try:
            data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"No existe el archivo {path}") from exc
        except (OSError, UnicodeDecodeError) as exc:
            raise ValueError(f"No se puede leer el archivo {path}") from exc
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            raise ValueError(f"YAML inválido en {path}" + (f", línea {mark.line + 1}" if mark else "")) from exc
        if not isinstance(data, dict):
            raise ValueError(f"{path} debe contener un mapeo YAML")
        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(map(str, first["loc"])) or "raíz"
            raise ValueError(f"{path} no es válido en {where}: {first['msg'].removeprefix('Value error, ')}") from exc


class Role(Model):
    parents: list[Id] = []
    read: list[Id] = []
    receive: list[Id] = []


class Family(Model):
    description: str
    statuses: Annotated[list[Id], Field(min_length=1)]
    absolute: Probability
    increase: Probability


class Chain(Model):
    # From (document, target, company) and (document, status, signed)
    # infer (company, conclusion, signed).
    target: Id
    status: Id
    conclusion: Id


class Rule(Model):
    when: Annotated[list[RuleAtom], Field(min_length=1)]
    then: RuleAtom

    @model_validator(mode="after")
    def bound_variables(self):
        bound = {term for atom in self.when for term in (atom[0], atom[2]) if term.startswith("?")}
        output = {term for term in (self.then[0], self.then[2]) if term.startswith("?")}
        if output - bound:
            raise ValueError("Las variables de la conclusión deben aparecer en las premisas")
        return self

    def validate_policy(self, policy):
        predicates = policy.vocabulary.keys() | policy.families.keys()
        for _, predicate, value in [*self.when, self.then]:
            if predicate not in predicates:
                raise ValueError("Predicado sin definición")
            if predicate in policy.families and not value.startswith("?"):
                if value not in policy.families[predicate].statuses:
                    raise ValueError("Estado fuera de la familia protegida")


class RuleProposal(Model):
    rule: Rule
    quote: str = Field(min_length=1)
    confidence: Probability

    def validate_source(self, policy, text):
        if not text or not self.quote.strip() or self.quote not in text:
            raise ValueError("La regla requiere una cita literal de la fuente")
        self.rule.validate_policy(policy)


class Policy(Model):
    version: Literal[1]
    roles: dict[Id, Role]
    evidence: dict[Grade, Probability]
    vocabulary: dict[Id, str]
    families: dict[Id, Family]
    chains: list[Chain] = []
    rules: list[Rule] = []
    assess_drafts: bool = False

    @model_validator(mode="after")
    def references(self):
        # Reserve the same meanings in every deployment, including older YAML policies.
        self.vocabulary.setdefault("personal_rut", "RUT/RUN de una persona o ficha, en formato rut_<cuerpo>_<dv>.")
        self.vocabulary.setdefault("identifies_person", "Enlaza una ficha (sujeto) con su titular (objeto).")
        if set(self.evidence) != {"possible_access", "external_hint", "delivered", "read", "cited"}:
            raise ValueError("Faltan grados de evidencia")
        if not self.families:
            raise ValueError("La política requiere una familia protegida")
        for name, role in self.roles.items():
            if set(role.parents) - self.roles.keys() or set(role.receive) - self.families.keys():
                raise ValueError(f"Referencia desconocida en el rol {name}")
            pending, ancestors = list(role.parents), set()
            while pending:
                parent = pending.pop()
                if parent == name:
                    raise ValueError("Ciclo en la jerarquía de roles")
                if parent in self.roles and parent not in ancestors:
                    ancestors.add(parent)
                    pending.extend(self.roles[parent].parents)
        predicates = self.vocabulary.keys() | self.families.keys()
        for chain in self.chains:
            if {chain.target, chain.status, chain.conclusion} - predicates:
                raise ValueError("Predicado sin definición")
        for rule in self.rules:
            rule.validate_policy(self)
        for family in self.families.values():
            if len(set(family.statuses)) != len(family.statuses):
                raise ValueError("Estados repetidos")
        return self


class Scope(Model):
    org: Id
    operation: Id


class Assignment(Scope):
    user: Id
    role: Id
    start: date
    end: date | None = None  # Exclusive.

    @model_validator(mode="after")
    def interval(self):
        if self.end is not None and self.end <= self.start:
            raise ValueError("Vigencia vacía")
        return self


class Clause(Model):
    text: Annotated[str, Field(min_length=1)]
    facts: list[Triple]
    provides: list[Id] = []


class Option(Model):
    clauses: Annotated[list[Id], Field(min_length=1)]


class Task(Model):
    requires: Annotated[list[Id], Field(min_length=1)]
    options: Annotated[list[Option], Field(min_length=1)]


class Case(Scope):
    assignments: list[Assignment]
    clauses: dict[Id, Clause]
    tasks: dict[Id, Task]

    @model_validator(mode="after")
    def option_references(self):
        for task in self.tasks.values():
            for option in task.options:
                if set(option.clauses) - self.clauses.keys():
                    raise ValueError("Cláusula desconocida")
        return self


class Hypothesis(Model):
    claim: Triple
    quote: str = Field(min_length=1)
    p_if_seen: Probability

    def validate_source(self, policy, text):
        _, predicate, value = self.claim
        if not self.quote.strip() or self.quote not in text:
            raise ValueError("La hipótesis requiere una cita literal de la fuente visible")
        if predicate not in policy.vocabulary and predicate not in policy.families:
            raise ValueError("Predicado de hipótesis desconocido")
        if predicate in policy.families and value not in policy.families[predicate].statuses:
            raise ValueError("Estado de hipótesis desconocido")


class ObserverEstimate(Hypothesis):
    origin: str
    at: date
    p_seen: Probability
    assessor: str


class Observation(Model):
    origin: Id  # Stable identity of the underlying evidence, including copies.
    facts: list[Triple] = []
    hypotheses: list[Hypothesis] = []
    rules: list[RuleProposal] = Field(default_factory=list, max_length=32)
    assessor: str = "adapter"
    at: date
    user: Id | None = None  # None is internal only.
    grade: Grade | None = None
    resource: Id | None = None
    quote: str | None = None
    confidence: Probability | None = None  # Optional upper bound on knowledge.

    @model_validator(mode="after")
    def audience(self):
        if not self.facts and not self.hypotheses and not self.rules:
            raise ValueError("La observación está vacía")
        if self.rules and (self.user is not None or not self.quote):
            raise ValueError("Las reglas propuestas requieren fuente interna con cita")
        if self.hypotheses and (self.user is None or not self.quote):
            raise ValueError("Las hipótesis requieren destinatario y texto visible")
        if (self.user is None) != (self.grade is None):
            raise ValueError("Especifica destinatario y grado juntos")
        if self.grade == "possible_access" and self.resource is None:
            raise ValueError("Falta el recurso del permiso")
        if self.grade == "external_hint" and self.confidence is None and self.facts:
            raise ValueError("Una hipótesis requiere una estimación de conocimiento")
        if self.user is None and self.confidence is not None:
            raise ValueError("La confianza de acceso pertenece a un observador")
        return self


class Batch(Model):
    observations: list[Observation]
