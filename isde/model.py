"""Trusted policy and input contracts. Identifiers never contain executable rules."""
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

Id = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,95}$")]
Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Triple = tuple[Id, Id, Id]
Grade = Literal["possible_access", "external_hint", "delivered", "read", "cited"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @classmethod
    def load(cls, path: str | Path):
        return cls.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


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


class Policy(Model):
    version: Literal[1]
    roles: dict[Id, Role]
    evidence: dict[Grade, Probability]
    vocabulary: dict[Id, str]
    families: dict[Id, Family]
    chains: list[Chain]

    @model_validator(mode="after")
    def references(self):
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
        for chain in self.chains:
            if {chain.target, chain.status} - self.vocabulary.keys():
                raise ValueError("Predicado sin definición")
            if chain.conclusion not in self.families:
                raise ValueError("Conclusión sin política")
            if chain.target in self.families or chain.status in self.families:
                raise ValueError("Esta versión admite cadenas de un solo nivel")
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


class Observation(Model):
    origin: Id  # Stable identity of the underlying evidence, including copies.
    facts: Annotated[list[Triple], Field(min_length=1)]
    at: date
    user: Id | None = None  # None is internal only.
    grade: Grade | None = None
    resource: Id | None = None
    quote: str | None = None
    confidence: Probability | None = None  # Optional upper bound on knowledge.

    @model_validator(mode="after")
    def audience(self):
        if (self.user is None) != (self.grade is None):
            raise ValueError("Especifica destinatario y grado juntos")
        if self.grade == "possible_access" and self.resource is None:
            raise ValueError("Falta el recurso del permiso")
        if self.grade == "external_hint" and self.confidence is None:
            raise ValueError("Una hipótesis requiere una estimación de conocimiento")
        if self.user is None and self.confidence is not None:
            raise ValueError("La confianza de acceso pertenece a un observador")
        return self


class Batch(Model):
    observations: list[Observation]
