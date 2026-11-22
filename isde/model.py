"""Trusted policy and input contracts. Identifiers never contain executable rules."""
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, field_validator, model_validator

Id = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,95}$")]
Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Triple = tuple[Id, Id, Id]


def fold_id(value):
    """Claude writes identifiers with accents, capitals or leading digits; fold them to the canonical form.

    Other characters stay and fail validation: folding never invents separators (O-18 is not o_18)."""
    if not isinstance(value, str):
        return value
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    number = folded.lstrip("$€£ ")
    if number[:1].isdigit():
        # Amounts, percentages and dates are values; their punctuation carries no identity, unlike O-18.
        number = re.sub(r"[^a-z0-9]+", "_", number.replace("%", "_pct")).strip("_")
        return "v_" + number if re.fullmatch(r"[a-z0-9_]+", number) else folded
    return folded


def fold_ids(value):
    if not isinstance(value, (list, tuple)):
        return value
    return [fold_id(v) for v in value]


# Only for triples proposed by the model; quotes stay literal and trusted inputs keep strict identifiers.
ModelTriple = Annotated[Triple, BeforeValidator(fold_ids)]
ModelId = Annotated[Id, BeforeValidator(fold_id)]
RuleTerm = Annotated[str, Field(pattern=r"^(?:[a-z][a-z0-9_]{0,95}|\?[a-z][a-z0-9_]{0,95})$")]
RuleAtom = tuple[RuleTerm, Id, RuleTerm]
Grade = Literal["possible_access", "external_hint", "delivered", "read", "cited"]
NodeType = Literal["persona", "organizacion", "documento", "evento", "rol", "fuente", "dato", "valor", "entidad"]
INTRINSIC_FAMILY = "intrinsic_sensitivity"
Confidentiality = Literal["publico", "interno", "confidencial", "restringido"]
LEVELS = ["publico", "interno", "confidencial", "restringido"]
CONFIDENTIAL = {"confidencial", "restringido"}
DATA_NODES = {"dato", "valor"}
ACTOR_NODES = {"persona", "organizacion", "rol", "fuente"}
BOOLEAN_FLAGS = {"verdadero", "verdadera", "falso", "falsa", "true", "false", "si", "no", "yes"}
# Words that state confidentiality; as a triple object they label the subject, they are not a node.
LEVEL_WORDS = {"publico": "publico", "publica": "publico", "interno": "interno", "interna": "interno",
               "privado": "confidencial", "privada": "confidencial", "reservado": "confidencial",
               "reservada": "confidencial", "confidencial": "confidencial", "sensible": "confidencial",
               "restringido": "restringido", "restringida": "restringido", "secreto": "restringido",
               "secreta": "restringido"}
SHARE_WORDS = {"compartir", "compartirse", "compartible", "compartido", "publicar", "publicarse", "publicable",
               "publicado", "divulgar", "divulgarse", "divulgable", "comunicar", "comunicarse", "comunicado", "revelar",
               "revelarse", "revelado", "difundir", "difundirse", "difundido", "divulgado", "enviar", "anunciar",
               "anunciarse", "anunciado", "publicamente"}
LEVEL_FILLER = {"informacion", "dato", "datos", "credencial", "uso", "solo", "de", "caracter", "muy", "altamente",
                "estrictamente", "es", "y"}
# Predicates about confidentiality itself; only there do "publico" or "interno" label the subject.
LEVEL_PREDICATES = {"confidencialidad", "confidencial", "clasificacion", "nivel", "privacidad", "restriccion",
                    "restriction", "sensibilidad", "visibilidad", "caracter", "publicidad", "divulgacion", "publicacion",
                    "es"}


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
    statuses: list[Id] = []
    any_value: bool = False
    absolute: Probability
    increase: Probability

    @model_validator(mode="after")
    def values(self):
        if self.any_value == bool(self.statuses):
            raise ValueError("Elige cualquier valor o una lista no vacía de estados")
        return self

    def accepts(self, value):
        return self.any_value or value in self.statuses


def intrinsic_family():
    return Family(description="Información potencialmente sensible identificada por el análisis adversarial.",
                  any_value=True, absolute=.75, increase=.45)


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
                raise ValueError(f"Predicado sin definición: {predicate}")
            if predicate in policy.families and not value.startswith("?"):
                if not policy.families[predicate].accepts(value):
                    raise ValueError("Estado fuera de la familia protegida")


class RuleProposal(Model):
    rule: Rule
    quote: str = Field(min_length=1)
    confidence: Probability

    def validate_source(self, policy, text):
        if not text or not self.quote.strip() or self.quote not in text:
            raise ValueError("La regla requiere una cita literal de la fuente")
        self.rule.validate_policy(policy)


class VocabularyProposal(Model):
    predicate: Id
    description: Annotated[str, Field(min_length=1, max_length=500)]
    quote: Annotated[str, Field(min_length=1)]
    confidence: Probability

    def validate_source(self, text):
        if self.predicate == INTRINSIC_FAMILY:
            raise ValueError("El predicado de sensibilidad intrínseca está reservado")
        if not self.description.strip() or not self.quote.strip() or not text or self.quote not in text:
            raise ValueError("El concepto requiere descripción y cita literal de la fuente")


class PathSearch(Model):
    max_length: int = Field(default=3, ge=1, le=5)
    # An unrated path without a matching rule is advisory: 0.4 keeps two cited hops (0.98²·0.4) below the default increase.
    weight: Probability = .4
    max_degree: int = Field(default=12, ge=2, le=1000)  # Hubs like a shared company node are endpoints, not bridges.


class Policy(Model):
    version: Literal[1]
    roles: dict[Id, Role]
    evidence: dict[Grade, Probability]
    vocabulary: dict[Id, str]
    families: dict[Id, Family]
    chains: list[Chain] = []
    rules: list[Rule] = []
    assess_drafts: bool = False
    paths: PathSearch = PathSearch()

    @model_validator(mode="after")
    def references(self):
        # Reserve the same meanings in every deployment, including older YAML policies.
        self.vocabulary.setdefault("personal_rut", "RUT/RUN de una persona o ficha, en formato rut_<cuerpo>_<dv>.")
        self.vocabulary.setdefault("identifies_person", "Enlaza una ficha (sujeto) con su titular (objeto).")
        if INTRINSIC_FAMILY in self.families or INTRINSIC_FAMILY in self.vocabulary:
            raise ValueError("El identificador intrinsic_sensitivity está reservado")
        if set(self.evidence) != {"possible_access", "external_hint", "delivered", "read", "cited"}:
            raise ValueError("Faltan grados de evidencia")
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
            missing = {chain.target, chain.status, chain.conclusion} - predicates
            if missing:
                raise ValueError("Predicado sin definición: " + ", ".join(sorted(missing)))
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
    claim: ModelTriple
    quote: str = Field(min_length=1)
    p_if_seen: Probability

    @field_validator("claim")
    @classmethod
    def intrinsic_subject(cls, claim):
        # Models often put the reserved family first; no node may carry that name, so the swap is unambiguous.
        return (claim[1], INTRINSIC_FAMILY, claim[2]) if claim[0] == INTRINSIC_FAMILY else claim

    def fit(self, policy):
        """Model output can't extend the vocabulary here; an undefined relation keeps its risk as intrinsic sensitivity."""
        if self.claim[1] in policy.vocabulary or self.claim[1] in policy.families:
            return self
        return self.model_copy(update={"claim": (self.claim[0], INTRINSIC_FAMILY, self.claim[2])})

    def validate_source(self, policy, text):
        _, predicate, value = self.claim
        if not self.quote.strip() or self.quote not in text:
            raise ValueError("La hipótesis requiere una cita literal de la fuente visible")
        if predicate != INTRINSIC_FAMILY and predicate not in policy.vocabulary and predicate not in policy.families:
            raise ValueError("Predicado de hipótesis desconocido")
        if predicate in policy.families and not policy.families[predicate].accepts(value):
            raise ValueError("Estado de hipótesis desconocido")


class ObserverEstimate(Hypothesis):
    origin: str
    at: date
    p_seen: Probability
    assessor: str


class Entity(Model):
    id: Id
    type: NodeType = "entidad"
    label: str = Field(min_length=1, max_length=200)
    aliases: list[Id] = Field(default_factory=list, max_length=16)
    confidentiality: Confidentiality | None = None


def stricter(*levels):
    """Confidentiality only rises: the strictest stated level wins, and None means not stated."""
    return max((level for level in levels if level), key=LEVELS.index, default=None)


def flag_level(predicate, value):
    """Level carried by a triple whose object is a flag, not an entity; None for an ordinary triple.

    Returns "" for a flag that states no level (a bare boolean): it must still not become a node."""
    words, terms = value.split("_"), predicate.split("_")
    if value.startswith("no_"):
        # A negated disclosure ("no_publicar") restricts; another negated state ("no_firmado") is a real value.
        return "confidencial" if SHARE_WORDS & set(words) else None
    if value in BOOLEAN_FLAGS:
        negative = value in {"falso", "falsa", "false", "no"}
        if SHARE_WORDS & set(terms):
            # puede_publicarse falso and no_debe_publicarse verdadero both restrict.
            return "confidencial" if negative != ("no" in terms) else "publico"
        level = stricter(*(LEVEL_WORDS.get(w) for w in terms))
        # es_publico falso says only that the datum is not public.
        return ("interno" if level == "publico" else "") if negative else level or ""
    # token_interno names a node; only a value made of level and filler words is a label.
    if not set(words) & LEVEL_WORDS.keys() or set(words) - LEVEL_WORDS.keys() - LEVEL_FILLER:
        return None
    level = stricter(*(LEVEL_WORDS.get(w) for w in words))
    # sector publico is a fact; a weak level labels the subject only under a confidentiality predicate.
    return level if level in CONFIDENTIAL or LEVEL_PREDICATES & set(terms) else None


class ProtectedLiteral(Model):
    value: str = Field(min_length=1, max_length=12000)
    quote: str = Field(min_length=1, max_length=12000)
    family: Id = INTRINSIC_FAMILY

    def validate_source(self, text: str | None):
        if not self.value.strip() or self.value not in self.quote or not text or self.quote not in text:
            raise ValueError("El valor protegido requiere una cita literal de la fuente")


class Observation(Model):
    origin: Id  # Stable identity of the underlying evidence, including copies.
    facts: list[Triple] = []
    hypotheses: list[Hypothesis] = []
    rules: list[RuleProposal] = Field(default_factory=list, max_length=32)
    vocabulary: list[VocabularyProposal] = Field(default_factory=list, max_length=32)
    entities: list[Entity] = Field(default_factory=list, max_length=64)
    protected_literals: list[ProtectedLiteral] = Field(default_factory=list, max_length=64)
    assessor: str = "adapter"
    at: date
    user: Id | None = None  # None is internal only.
    grade: Grade | None = None
    resource: Id | None = None
    quote: str | None = None
    confidence: Probability | None = None  # Optional upper bound on knowledge.

    @model_validator(mode="after")
    def audience(self):
        if not any((self.facts, self.hypotheses, self.rules, self.vocabulary, self.entities, self.protected_literals)):
            raise ValueError("La observación está vacía")
        if self.protected_literals and self.user is not None:
            raise ValueError("Los valores protegidos solo se registran desde fuentes internas")
        for literal in self.protected_literals:
            literal.validate_source(self.quote)
        if self.entities and self.user is not None:
            raise ValueError("Las entidades y sus alias solo se registran desde fuentes internas")
        if self.vocabulary and (self.user is not None or not self.quote):
            raise ValueError("Los conceptos propuestos requieren fuente interna con cita")
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
