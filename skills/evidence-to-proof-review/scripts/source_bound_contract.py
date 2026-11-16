"""Frozen, standard-library contract for the opt-in source-bound-v1 profile.

All phases import PROFILE_VERSION, PARSER_VERSION, CATALOGUE_VERSION,
ContractError, the frozen records below, parse_facts, parse_claims and SHAPES.
A (source_bound_facts) provides extract_facts: ExtractFacts, make_fact: MakeFact,
and generate_facts: GenerateFacts. It consumes FactRecord/DerivedRecord,
SourceBinding and RuleRegistration and returns ResolvedFacts/GeneratedFile.
B (source_bound_templates) provides render_claim: RenderClaim. It consumes
ClaimRecord, SHAPES, ResolvedFacts and RuleRegistration and returns RenderedClaim.
C (source_bound_guards/source_bound_proofs) provides check_guards: CheckGuards
and check_proof: CheckProof. It consumes those same records and RenderedClaim,
and returns GuardResult. It uses resolve_status/validate_status; it must preserve
all flags, proof logs and per-leaf sensitivity. Protocols are signatures only:
this module extracts no quoted values, generates no Lean and executes no guards.

Shared imports: SHAPE_VERSION, TypeSpec, ScalarType, ListType, RecordType,
ScalarValue, Value, Reference, ByteSpan, Origin, EvidenceReference,
RuleRegistration, EnumDomain, ScopeRegistration, FactsDocument, FactRecord,
DerivedRecord, Operation, parse_type, validate_dag, GeneratedFile.
A additionally imports SourceBinding, Provenance, ResolvedValue, ResolvedFacts,
ExtractFacts, MakeFact, GenerateFacts.
B additionally imports ClaimRecord, ClaimSlot, SlotKind, SlotSpec, ShapeSpec,
Polarity, ProofRelation, RenderedClaim, RenderClaim, ResolvedFacts.
C additionally imports GuardFlag, FormalStatus, StatusDecision, GuardBudget,
LeafSensitivity, GuardResult, CheckGuards, CheckProof, resolve_status,
validate_status, RenderedClaim, ResolvedFacts, ProofRelation.

Wire grammar (case-sensitive, no inline comments or Markdown unescaping):
``## Fact ID`` uses the exact fields in section 2 of the spec; Name equals ID.
Quote is a literal fenced block immediately after ``Quote:``. Its final line
ending separates it from the closing fence and is not quote content; use an
extra blank line to encode a trailing newline. Other bytes, including CRLF,
are preserved. Select and Quote-start count UTF-8 bytes, never code points.
``## Derived ID`` requires Type, Operation, Inputs (comma-separated IDs; empty
only for list/record). Optional Fields (``name=ID, ...``) is required for record;
Field for field; Rule for filter/rule; Scope for filter. These are references,
not expressions. Operation ``rule`` calls a separately supplied registration.
Types are nat, money(EUR|RON|USD), date, bool, enum(domain), ratio,
list(TYPE), record(name:TYPE,...). int is generated-only (signed subtraction
or date difference), never a quoted scalar fact. Lists may hold generated
records, as required by maximum/conflict, but may not hold nested lists.
``## Claim ID`` requires Report-anchor, Shape, Version, Polarity (positive or
negative), Rules, Scope, Rationale; Rules/Scope may be empty comma-separated
IDs. Slots are individual ``Slot NAME: VALUE`` fields. Data/rule references
are ASCII IDs; field selectors are ASCII names; cmp and inclusive are fixed
catalogue choices. Version is the shape version, currently 1. Unknown shapes,
versions or unresolved claim references retain a not-formalized reason.
Malformed fields/references always reject, even on unsupported shapes.
Optional ``## Summary`` contains only a generated Markdown table; it is never
an authority for records. Arbitrary prose outside a single # title rejects.

Rule registrations are in-memory inputs, not a third authored file schema.
Their policy quotations, signatures, source/rule dependencies and correspondence
notes are explicit; C validates them against authored Rules.lean. Bindings come
from the manifest/registered report, NEVER from facts.md. Structural parsing
checks binding membership and role/path agreement; A verifies hashes, exact
quotes, locale overrides, copied-report evidence, typed values, units, DAG
operation signatures and report taint. B checks relational slot constraints
(the SlotSpec.type strings are normative), applicability outside negation and
source independence. A/B must return a not-formalized result or raise
ContractError on unsupported/invalid input, never silently coerce it. No parse
success certifies evidence, completeness, rule fidelity or a checked proof.

EnumDomain and ScopeRegistration are also caller-supplied, versioned/sourced
registrations retained in FactsDocument. Scope references may name a value or
a ScopeRegistration (so a prose scope citation need not invent a scalar fact).
Slot constraints: T/S/I are shared exact types; integer means nat/int/money
integer projections with compatible units; ordered additionally admits date
and exact ratio. All repeated unit variables must agree. Maximum f and all/any/
count p are unary registered rules over the exact list element type. Conflict
records share scope, issuer and selected-field types. Phase B enforces these
constraints; unsupported combinations produce no target. Rule policy and scope
quotations are independently bound and checked by A, with taint retained by C.

Generated Lean ABI: A produces ResolvedFacts.bindings via build_lean_bindings
and must emit declarations matching that map byte for byte in name and type.
B and C must take names and projections only from the map, including during
mutation regeneration. Types.lean remains unchanged. Generated enum inductives
and record structures live in Facts, before value declarations, so Rules can
use them. Record types are structural (ordered fields), shared across values;
their names contain the full SHA-256 of the canonical type grammar. All enum
and structure declarations derive DecidableEq and Repr. MODULE_IMPORTS fixes
direct imports and MODULE_ORDER fixes Types -> Facts -> Rules -> Derived -> Claims.

B must call claim_rule_input_reason before rendering all/any/count/maximum.
Any transitive report-origin leaf in an implicit predicate/projection operand
requires a not-formalized result with that reason and no target/proof files,
even if another member supplies independent source evidence. Maximum checks
both xs and x; the comparison value v is not a rule input. This gate is outside
polarity/negation. A still checks explicit DAG rule/filter inputs; C retains
taint. report_origin_leaves traverses value, unit, field, scope and rule edges.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from fractions import Fraction
from hashlib import sha256
from pathlib import Path, PurePosixPath
import re
from types import MappingProxyType
from typing import Mapping, Protocol, TypeAlias

PROFILE_VERSION = 'source-bound-v1'
PARSER_VERSION = 'source-bound-parser-v1'
CATALOGUE_VERSION = 'source-bound-catalogue-v1'
SHAPE_VERSION = '1'


class ContractError(ValueError):
    """Malformed/unbound input; callers must retain and disclose the rejection."""


class Origin(StrEnum):
    SOURCE = 'source'
    REPORT = 'report'


@dataclass(frozen=True)
class ScalarType:
    kind: str
    parameter: str | None = None


@dataclass(frozen=True)
class ListType:
    element: ScalarType | RecordType


@dataclass(frozen=True)
class RecordType:
    fields: tuple[tuple[str, TypeSpec], ...]


TypeSpec: TypeAlias = ScalarType | ListType | RecordType
ScalarValue: TypeAlias = int | Fraction | date | bool | str
# Lists are tuples of values; records are tuples of (field name, value) pairs.
Value: TypeAlias = ScalarValue | tuple['Value', ...] | tuple[tuple[str, 'Value'], ...]


@dataclass(frozen=True)
class Reference:
    id: str


@dataclass(frozen=True)
class ByteSpan:
    start: int
    end: int


@dataclass(frozen=True)
class SourceBinding:
    """Trusted manifest projection role/locale and registered passage anchors.

    Per-anchor locales are reviewed manifest-bound overrides. A retains original
    and projection hashes and extraction uncertainty via Provenance.
    """
    path: str
    origin: Origin
    locale: str
    anchors: frozenset[str]
    anchor_locales: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class EvidenceReference:
    source_path: str
    anchor: str
    quote: str
    quote_start: int


@dataclass(frozen=True)
class EnumDomain:
    id: str
    version: str
    labels: tuple[str, ...]  # Exact labels; no synonym inference or invented values.


@dataclass(frozen=True)
class ScopeRegistration:
    id: str
    evidence: tuple[EvidenceReference, ...]
    rationale: str


@dataclass(frozen=True)
class FactRecord:
    id: str
    name: str
    type: ScalarType
    origin: Origin
    asserted_value: str  # Untrusted canonical spelling; A parses and compares it.
    source_path: str
    anchor: str
    quote: str
    quote_start: int
    selection: ByteSpan
    locale: str
    unit_source: Reference | None  # None encodes the wire choice "inline".


class Operation(StrEnum):
    LIST = 'list'
    RECORD = 'record'
    FIELD = 'field'
    FILTER = 'filter'
    SUM = 'sum'
    COUNT = 'count'
    ADD = 'add'
    SUBTRACT = 'subtract'
    MULTIPLY = 'multiply'
    RATIO = 'ratio'
    DATE_ADD = 'date_add'
    RULE = 'rule'


@dataclass(frozen=True)
class DerivedRecord:
    id: str
    type: TypeSpec
    operation: Operation
    inputs: tuple[Reference, ...]
    fields: tuple[tuple[str, Reference], ...] = ()
    field: str | None = None
    rule: Reference | None = None
    scope: tuple[Reference, ...] = ()


@dataclass(frozen=True)
class RuleRegistration:
    id: str  # Rules.ID is the fixed generated Lean reference.
    parameters: tuple[tuple[str, TypeSpec], ...]
    result_type: TypeSpec
    policy: tuple[EvidenceReference, ...]
    correspondence: str
    dependencies: tuple[Reference, ...] = ()  # Source values or other rules.


@dataclass(frozen=True)
class FactsDocument:
    facts: tuple[FactRecord, ...]
    derived: tuple[DerivedRecord, ...]
    enum_domains: tuple[EnumDomain, ...] = ()
    scopes: tuple[ScopeRegistration, ...] = ()

    def records(self) -> dict[str, FactRecord | DerivedRecord]:
        return {record.id: record for record in (*self.facts, *self.derived)}


class Polarity(StrEnum):
    POSITIVE = 'positive'
    NEGATIVE = 'negative'


class ProofRelation(StrEnum):
    SUPPORTS = 'supports'
    REFUTES = 'refutes'


class SlotKind(StrEnum):
    VALUE = 'value'
    RULE = 'rule'
    FIELD = 'field'
    CHOICE = 'choice'


@dataclass(frozen=True)
class SlotSpec:
    name: str
    kind: SlotKind
    type: str  # Normative relational constraint, checked by phase B.
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class ShapeSpec:
    name: str
    slots: tuple[SlotSpec, ...]
    applicability: tuple[str, ...] = ()
    version: str = SHAPE_VERSION


@dataclass(frozen=True)
class ClaimSlot:
    name: str
    kind: SlotKind
    value: Reference | str


@dataclass(frozen=True)
class ClaimRecord:
    id: str
    report_anchor: str
    shape: str
    version: str
    slots: tuple[ClaimSlot, ...]
    polarity: Polarity
    rules: tuple[Reference, ...]
    scope: tuple[Reference, ...]
    rationale: str
    not_formalized_reason: str | None = None
    proof_relation: ProofRelation = ProofRelation.SUPPORTS


def _value(name: str, type: str) -> SlotSpec:
    return SlotSpec(name, SlotKind.VALUE, type)


def _rule(name: str, type: str) -> SlotSpec:
    return SlotSpec(name, SlotKind.RULE, type)


_CMP = SlotSpec('cmp', SlotKind.CHOICE, 'comparison', ('=', '≠', '<', '≤', '>', '≥'))
# Only this registry defines shape and slot names. Ratio uses a ratio-valued
# reference r, rather than authored numerator/denominator literals. Conflict
# field selects the comparable field in records also containing scope/issuer.
SHAPES: Mapping[str, ShapeSpec] = MappingProxyType({s.name: s for s in (
    ShapeSpec('equal', (_value('a', 'T'), _value('b', 'T'))),
    ShapeSpec('sum', (_value('xs', 'list(integer[unit])'), _value('v', 'integer[unit]'))),
    ShapeSpec('compare', (_value('a', 'ordered[unit]'), _value('b', 'ordered[unit]'), _CMP)),
    ShapeSpec('difference', tuple(_value(n, 'integer[unit]') for n in ('a', 'b', 'v'))),
    ShapeSpec('date_add', (_value('start', 'date'), _value('due', 'date'), _value('days', 'nat'))),
    ShapeSpec('within', (_value('event', 'date'), _value('due', 'date'),
                        SlotSpec('inclusive', SlotKind.CHOICE, 'inclusivity', ('true', 'false')))),
    ShapeSpec('all', (_value('xs', 'list(T)'), _rule('p', '(T)->bool')), ('nonempty xs',)),
    ShapeSpec('any', (_value('xs', 'list(T)'), _rule('p', '(T)->bool'))),
    ShapeSpec('count', (_value('xs', 'list(T)'), _rule('p', '(T)->bool'), _value('n', 'nat'), _CMP)),
    ShapeSpec('maximum', (_value('xs', 'list(T)'), _value('x', 'T'),
                          _rule('f', '(T)->ordered[unit]'), _value('v', 'ordered[unit]')),
              ('nonempty xs',)),
    ShapeSpec('conflict', (_value('a', 'record(scope:S,issuer:I,field:T)'),
                           _value('b', 'record(scope:S,issuer:I,field:T)'),
                           SlotSpec('field', SlotKind.FIELD, 'common comparable field'))),
    ShapeSpec('ratio', (_value('a', 'integer[unit]'), _value('b', 'integer[unit]'),
                        _value('r', 'ratio'), _CMP), ('positive b', 'positive r.denominator')),
)})


class GuardFlag(StrEnum):
    ROOT_TRUE = 'root_true'
    BASELINE_FAILED = 'baseline_failed'
    TRANSCRIPTION = 'transcription'
    UNCLASSIFIED = 'unclassified'
    INCOMPLETE = 'incomplete'
    NO_TESTED_SOURCE_SENSITIVITY = 'no_tested_source_sensitivity'


class FormalStatus(StrEnum):
    REJECTED = 'rejected'
    BLOCKED = 'blocked'
    AUXILIARY = 'auxiliary'
    PROVED = 'proved'
    REFUTED = 'refuted'
    NOT_ATTEMPTED = 'not_attempted'


@dataclass(frozen=True)
class StatusDecision:
    status: FormalStatus
    reference_kind: str | None  # proof, auxiliary, or None (no reference allowed).
    reason: str


def resolve_status(*, supported: bool, baseline_checked: bool,
                   flags: frozenset[GuardFlag], relation: ProofRelation) -> StatusDecision:
    """Apply section 5 precedence without dropping any overlapping input flags.

    baseline_checked means the selected Q or not-Q kernel/exact-target/axiom
    check passed, not merely that data elaborated. Unsupported includes unresolved
    inputs/applicability failures; no target is attempted. This is a status
    reducer, not an implementation or certification of any guard.
    """
    if GuardFlag.ROOT_TRUE in flags:
        return StatusDecision(FormalStatus.REJECTED, None, 'Normalized root True')
    if not supported:
        return StatusDecision(FormalStatus.NOT_ATTEMPTED, None, 'not formalized')
    if not baseline_checked or GuardFlag.BASELINE_FAILED in flags:
        return StatusDecision(FormalStatus.BLOCKED, None, 'Baseline not checked successfully')
    if GuardFlag.TRANSCRIPTION in flags:
        return StatusDecision(FormalStatus.AUXILIARY, 'auxiliary', 'Checked transcription')
    if flags & {GuardFlag.UNCLASSIFIED, GuardFlag.INCOMPLETE,
                GuardFlag.NO_TESTED_SOURCE_SENSITIVITY}:
        return StatusDecision(FormalStatus.BLOCKED, None, 'Unclassified, incomplete or no tested source sensitivity')
    return StatusDecision(FormalStatus.PROVED if relation == ProofRelation.SUPPORTS
                          else FormalStatus.REFUTED, 'proof', 'Checked source-sensitive target')


def validate_status(authored: FormalStatus, decision: StatusDecision,
                    reference_kind: str | None) -> None:
    """Reject authored status/reference downgrades or promotions; never rewrite."""
    if authored != decision.status or reference_kind != decision.reference_kind:
        raise ContractError('Authored status or proof reference conflicts with guard precedence')


@dataclass(frozen=True)
class Provenance:
    evidence: EvidenceReference
    selection: ByteSpan
    original_path: str
    original_sha256: str
    projection_sha256: str
    passage_sha256: str
    uncertainty: tuple[str, ...]
    uncertainty_decision: str


@dataclass(frozen=True)
class ResolvedValue:
    id: str
    type: TypeSpec
    value: Value
    source_leaves: tuple[str, ...]
    report_leaves: tuple[str, ...]  # Transitive taint, including rule dependencies.
    provenance: tuple[Provenance, ...]


@dataclass(frozen=True)
class ResolvedFacts:
    document: FactsDocument
    values: tuple[ResolvedValue, ...]
    # Extraction-owned dimensional metadata, retained for synthetic recomputation.
    # Each entry is a scalar/list unit or an ordered tuple of record-field units.
    dimensions: tuple[tuple[str, object], ...] = ()

    @property
    def bindings(self) -> LeanBindingMap:
        """A's deterministic ABI, also available to existing two-argument callers."""
        return build_lean_bindings(self)


MODULE_ORDER = ('SourceBound.Types', 'SourceBound.Facts', 'Rules',
                'SourceBound.Derived', 'SourceBound.Claims')
MODULE_IMPORTS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    'SourceBound.Types': (), 'SourceBound.Facts': ('SourceBound.Types',),
    'Rules': ('SourceBound.Facts',), 'SourceBound.Derived': ('Rules',),
    'SourceBound.Claims': ('SourceBound.Derived',),
})


@dataclass(frozen=True)
class LeanTypeBinding:
    lean_type: str
    module: str | None  # None for Lean built-ins and List application itself.
    integer_projection: str | None = None  # Template with {value}; always Int.
    ratio_projection: tuple[str, str] | None = None  # Int numerator/denominator.
    constructors: tuple[tuple[str, str], ...] = ()  # Exact label -> qualified name.
    fields: tuple[tuple[str, str, str], ...] = ()  # Wire field, Lean field, Lean type.


@dataclass(frozen=True)
class LeanBinding:
    name: str
    lean_type: str
    module: str


@dataclass(frozen=True)
class LeanBindingMap:
    values: Mapping[str, LeanBinding]
    types: Mapping[TypeSpec, LeanTypeBinding]
    module_imports: Mapping[str, tuple[str, ...]]


def lean_value_name(record: FactRecord | DerivedRecord) -> str:
    """No ID rewriting: origin selects the reserved namespace."""
    ident = _identifier(record.id)
    if isinstance(record, DerivedRecord):
        namespace = 'Derived'
    elif record.origin == Origin.SOURCE:
        namespace = 'Facts'
    elif record.origin == Origin.REPORT:
        namespace = 'Report'
    else:
        raise ContractError('Unsupported fact origin')
    return f'SourceBound.{namespace}.{ident}'


def lean_claim_name(ident: str) -> str:
    """Claim declarations belong to SourceBound.Claims."""
    return f'SourceBound.Claims.{_identifier(ident)}'


def lean_enum_constructors(domain: EnumDomain) -> tuple[tuple[str, str], ...]:
    """ASCII letters lower-case; ASCII punctuation -> _; non-ASCII -> uHEX_.

    Preserve every underscore (no trimming/collapsing); prefix label_ prevents
    keywords/digits. Reject collisions, including repeated labels. UTF-8/Unicode
    labels remain exact map keys; this naming transform never normalizes data.
    """
    prefix = f'SourceBound.Enums.{_identifier(domain.id)}'
    if not domain.version or not domain.labels or any(not s for s in domain.labels):
        raise ContractError('Enum domains require version and nonempty labels')
    result = tuple((label, prefix + '.label_' + ''.join(
        char.lower() if char.isascii() and char.isalnum() else
        '_' if char.isascii() else f'u{ord(char):x}_' for char in label))
        for label in domain.labels)
    if len({name for _, name in result}) != len(result):
        raise ContractError(f'Enum constructor collision: {domain.id}')
    return result


def _type_key(type_: TypeSpec) -> str:
    if isinstance(type_, ScalarType):
        return type_.kind if type_.parameter is None else f'{type_.kind}({type_.parameter})'
    if isinstance(type_, ListType):
        return f'list({_type_key(type_.element)})'
    return 'record(' + ','.join(f'{name}:{_type_key(t)}' for name, t in type_.fields) + ')'


def lean_type_binding(type_: TypeSpec, enum_domains: tuple[EnumDomain, ...] = ()) -> LeanTypeBinding:
    """Pure type/projection ABI. Record hashes use UTF-8 canonical type grammar.

    Structure fields are f_<wire name>; constructor is <type>.mk. Lists retain
    wrapper element types (e.g. List (SourceBound.Money SourceBound.Currency.eur));
    arithmetic maps the element's integer projection, never coerces the list.
    """
    if parse_type(_type_key(type_), generated=True) != type_:
        raise ContractError('Noncanonical type')
    if isinstance(type_, ListType):
        element = lean_type_binding(type_.element, enum_domains)
        return LeanTypeBinding(f'List ({element.lean_type})', None)
    if isinstance(type_, RecordType):
        name = 'SourceBound.Records.R_' + sha256(_type_key(type_).encode('utf-8')).hexdigest()
        fields = tuple((n, f'f_{n}', lean_type_binding(t, enum_domains).lean_type)
                       for n, t in type_.fields)
        return LeanTypeBinding(name, 'SourceBound.Facts', fields=fields)
    simple = {
        'nat': LeanTypeBinding('Nat', None, 'Int.ofNat ({value})'),
        'int': LeanTypeBinding('Int', None, '({value})'),
        'bool': LeanTypeBinding('Bool', None),
        'date': LeanTypeBinding('SourceBound.Date', 'SourceBound.Types', '({value}).ordinal'),
        'ratio': LeanTypeBinding('SourceBound.Ratio', 'SourceBound.Types', ratio_projection=(
            '({value}).num', 'Int.ofNat (({value}).den)')),
    }
    if type_.kind in simple:
        return simple[type_.kind]
    if type_.kind == 'money':
        return LeanTypeBinding(f'SourceBound.Money SourceBound.Currency.{type_.parameter.lower()}',
                               'SourceBound.Types', '({value}).minor')
    domains = {d.id: d for d in enum_domains}
    if len(domains) != len(enum_domains) or type_.parameter not in domains:
        raise ContractError('Duplicate or unresolved enum domain')
    return LeanTypeBinding(f'SourceBound.Enums.{type_.parameter}', 'SourceBound.Facts',
                           constructors=lean_enum_constructors(domains[type_.parameter]))


def build_lean_bindings(resolved: ResolvedFacts) -> LeanBindingMap:
    """Build the sole ABI map, sorted by ID/type key, rejecting incomplete values.

    Emit nested field/element type declarations before their parent structures;
    all generated types precede facts. Rules may use the same pure type helper
    for signature-only types; A must include those declarations before Rules.
    """
    records = resolved.document.records()
    values = {v.id: v for v in resolved.values}
    if (len(records) != len(resolved.document.facts) + len(resolved.document.derived)
            or len(values) != len(resolved.values) or records.keys() != values.keys()):
        raise ContractError('Duplicate or incomplete resolved values')
    types = {}

    def add(type_: TypeSpec) -> None:
        if isinstance(type_, ListType):
            add(type_.element)
        elif isinstance(type_, RecordType):
            for _, field_type in type_.fields:
                add(field_type)
        types[type_] = lean_type_binding(type_, resolved.document.enum_domains)

    domains = resolved.document.enum_domains
    if len({d.id for d in domains}) != len(domains):
        raise ContractError('Duplicate enum domain ID')
    for domain in domains:
        add(ScalarType('enum', domain.id))
    bindings = {}
    for ident, record in sorted(records.items()):
        if values[ident].type != record.type:
            raise ContractError(f'Resolved type mismatch: {ident}')
        add(record.type)
        bindings[ident] = LeanBinding(lean_value_name(record), types[record.type].lean_type,
            'SourceBound.Derived' if isinstance(record, DerivedRecord) else 'SourceBound.Facts')
    return LeanBindingMap(MappingProxyType(bindings), MappingProxyType(dict(
        sorted(types.items(), key=lambda item: _type_key(item[0])))), MODULE_IMPORTS)


def lean_comparison_operands(type_: LeanTypeBinding, left: str, right: str) -> tuple[str, str]:
    """Trusted generated expressions only. Ratios compare cross-products in Int.

    For every comparison operator (including equality), n1*d2 versus n2*d1;
    positive denominators remain an independent applicability requirement.
    """
    if type_.ratio_projection:
        numerator, denominator = type_.ratio_projection
        return (f'{numerator.format(value=left)} * {denominator.format(value=right)}',
                f'{numerator.format(value=right)} * {denominator.format(value=left)}')
    if type_.integer_projection:
        return tuple(type_.integer_projection.format(value=v) for v in (left, right))
    raise ContractError('Type has no arithmetic/comparison projection')


def report_origin_leaves(reference: Reference, facts: ResolvedFacts,
                         rules: tuple[RuleRegistration, ...] = ()) -> tuple[str, ...]:
    """Sorted unique transitive report leaves; no source-leaf shortcut or cache trust."""
    validate_dag(facts.document, rules)
    nodes = {n.id: n for n in (*facts.document.facts, *facts.document.derived,
                              *rules, *facts.document.scopes)}
    pending, visited, leaves = [reference.id], set(), set()
    while pending:
        ident = pending.pop()
        if ident in visited:
            continue
        if ident not in nodes:
            raise ContractError(f'Unresolved dependency: {ident}')
        visited.add(ident)
        node = nodes[ident]
        if isinstance(node, FactRecord):
            if node.origin == Origin.REPORT:
                leaves.add(ident)
            edges = (node.unit_source,) if node.unit_source else ()
        elif isinstance(node, DerivedRecord):
            edges = node.inputs + tuple(r for _, r in node.fields) + node.scope
            edges += (node.rule,) if node.rule else ()
        elif isinstance(node, RuleRegistration):
            edges = node.dependencies
        else:
            edges = ()
        pending.extend(edge.id for edge in edges)
    return tuple(sorted(leaves))


def claim_rule_input_reason(claim: ClaimRecord, facts: ResolvedFacts,
                            rules: tuple[RuleRegistration, ...] = ()) -> str | None:
    """B's mandatory gate for implicit rule operands, before polarity/rendering."""
    operands = {'all': ('xs',), 'any': ('xs',), 'count': ('xs',), 'maximum': ('xs', 'x')}
    slots = {s.name: s for s in claim.slots}
    for name in operands.get(claim.shape, ()):
        slot = slots.get(name)
        if slot is None or slot.kind != SlotKind.VALUE or not isinstance(slot.value, Reference):
            raise ContractError(f'Missing or malformed rule operand: {name}')
        leaves = report_origin_leaves(slot.value, facts, rules)
        if leaves:
            return f'not formalized: rule operand {name} has report-origin leaves: {", ".join(leaves)}'
    return None


@dataclass(frozen=True)
class GeneratedFile:
    path: str  # Workspace-relative path; deterministic UTF-8 content.
    content: str


@dataclass(frozen=True)
class RenderedClaim:
    claim: ClaimRecord
    relation: ProofRelation
    proposition: str | None  # Complete Q after polarity; never slot-authored Lean.
    target: str | None  # Q for supports, not-Q for refutes.
    english: str
    dependencies: tuple[Reference, ...]
    files: tuple[GeneratedFile, ...]
    not_formalized_reason: str | None = None


@dataclass(frozen=True)
class GuardBudget:
    mutants: int = 64
    process_seconds: int = 10
    theorem_seconds: int = 120


@dataclass(frozen=True)
class LeafSensitivity:
    leaf_ids: tuple[str, ...]  # Identical provenance selections are interned.
    scheduled: int
    attempted: int
    confirmed: int  # Target failure AND checked negation with applicability.
    inconclusive: int
    untestable: bool
    logs: tuple[str, ...]


@dataclass(frozen=True)
class GuardResult:
    flags: frozenset[GuardFlag]
    baseline_checked: bool
    decision: StatusDecision
    leaves: tuple[LeafSensitivity, ...]
    logs: tuple[str, ...]
    proof_reference: str | None
    module_to_unit: tuple[tuple[str, str], ...]


class ExtractFacts(Protocol):
    def __call__(self, workspace: Path, document: FactsDocument,
                 bindings: tuple[SourceBinding, ...], rules: tuple[RuleRegistration, ...]
                 ) -> ResolvedFacts:
        """A: verify immutable provenance, parse/compare values, evaluate the DAG.

        Reject bad tokens, unit/locale ambiguity, hash/quote mismatches, copied
        report evidence, invalid operations or report-tainted rule inputs with
        ContractError. Preserve uncertainty; no rounding or repaired tokens.
        Return ResolvedFacts whose bindings are the generated declaration ABI.
        """
        ...


class MakeFact(Protocol):
    def __call__(self, workspace: Path, *, id: str, type: ScalarType,
                 source_path: str, anchor: str, quote: str, selected_token: str,
                 bindings: tuple[SourceBinding, ...], occurrence: int | None = None,
                 quote_occurrence: int | None = None, unit_source: Reference | None = None,
                 context: FactsDocument | None = None
                 ) -> FactRecord:
        """A: produce a complete record, computing UTF-8 offsets and asserted value.

        Role and locale come from bindings. Occurrences are zero-based; without
        an explicit index repeated selected tokens OR repeated quotes reject.
        Missing/non-token selections, bad indices and extraction failures raise
        ContractError. Agents must never count byte offsets by hand.
        Context supplies quoted unit facts and enum domains when needed; missing
        context for a referenced unit/domain rejects rather than guessing.
        """
        ...


class GenerateFacts(Protocol):
    def __call__(self, resolved: ResolvedFacts, rules: tuple[RuleRegistration, ...]
                 ) -> tuple[GeneratedFile, ...]:
        """A: deterministic Types/Facts/Derived Lean and provenance/summary Markdown.

        Return bytes-as-UTF-8-text artifacts without writing or executing them;
        unsupported values/types raise ContractError. Quote text is never Lean.
        Names/types must match resolved.bindings byte for byte; use MODULE_IMPORTS.
        """
        ...


class RecomputeFacts(Protocol):
    def __call__(self, facts: ResolvedFacts, replacements: Mapping[str, Value],
                 rules: tuple[RuleRegistration, ...] = ()) -> ResolvedFacts:
        """Recompute the synthetic DAG without re-extracting values from quotes.

        Preserve documentary records, provenance, types and report values. Only
        independent source selections or supported structural list mutations
        may be replaced; dependent values must be recomputed, not cached.
        """
        ...


class RenderClaim(Protocol):
    def __call__(self, claim: ClaimRecord, facts: ResolvedFacts,
                 rules: tuple[RuleRegistration, ...], relation: ProofRelation) -> RenderedClaim:
        """B: check slot types/units, source independence and applicability; render.

        Unsupported/unresolved/inapplicable claims return a reason and no target
        or proof files. Malformed references raise ContractError. Applicable
        output includes fixed English, Q, selected target and by-decide artifact.
        Applicability failures must never become proofs of the negated claim.
        Use facts.bindings for names/types/projections. A non-None result from
        claim_rule_input_reason requires that reason and no target/proof files.
        """
        ...


class CheckGuards(Protocol):
    def __call__(self, workspace: Path, rendered: RenderedClaim, facts: ResolvedFacts,
                 rules: tuple[RuleRegistration, ...], authored: tuple[GeneratedFile, ...],
                 *, budget: GuardBudget, attempt_directory: Path,
                 sandbox_command: tuple[str, ...]) -> GuardResult:
        """C: syntax/transcription guards plus full retained sensitivity schedule.

        Use fresh isolated compilation units; preserve overlapping flags and all
        logs. Missing reviewed no-network sandbox, timeout, unsupported syntax or
        incomplete schedule never counts as success; return blocked/incomplete
        as appropriate. Malformed or inconsistent input raises ContractError.
        Regenerate baseline/mutant declarations using facts.bindings only.
        """
        ...


class CheckProof(CheckGuards, Protocol):
    """C: public proof entrypoint with CheckGuards signature and failure contract.

    Also verifies byte-for-byte regeneration, exact target and axioms. No cached
    baseline or mutant objects; retain module-to-unit mapping and proof attempt.
    """


def _identifier(value: str) -> str:
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value, re.ASCII):
        raise ContractError(f'Expected ASCII identifier, not Lean text: {value!r}')
    return value


def _split(value: str, delimiter: str = ',') -> list[str]:
    """Split type/field lists at top level, rejecting unbalanced parentheses."""
    parts, start, depth = [], 0, 0
    for i, char in enumerate(value):
        if char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if depth < 0:
                raise ContractError('Unbalanced type parentheses')
        elif char == delimiter and depth == 0:
            parts.append(value[start:i].strip())
            start = i + 1
    if depth:
        raise ContractError('Unbalanced type parentheses')
    parts.append(value[start:].strip())
    return parts


def parse_type(value: str, *, generated: bool = False) -> TypeSpec:
    """Parse only the fixed type language; int is reserved for derived values."""
    if value in {'nat', 'date', 'bool', 'ratio'} | ({'int'} if generated else set()):
        return ScalarType(value)
    match = re.fullmatch(r'(money|enum|list|record)\((.*)\)', value)
    if not match:
        raise ContractError(f'Unsupported type: {value}')
    kind, parameter = match.groups()
    if kind == 'money':
        if parameter not in {'EUR', 'RON', 'USD'}:
            raise ContractError('Unsupported currency')
        return ScalarType(kind, parameter)
    if kind == 'enum':
        return ScalarType(kind, _identifier(parameter))
    if kind == 'list':
        element = parse_type(parameter, generated=generated)
        if isinstance(element, ListType):
            raise ContractError('Nested lists are unsupported')
        return ListType(element)
    fields = []
    for part in _split(parameter):
        name, separator, field_type = part.partition(':')
        if not separator:
            raise ContractError('Record fields require name:TYPE')
        fields.append((_identifier(name.strip()), parse_type(field_type.strip(), generated=generated)))
    if len({name for name, _ in fields}) != len(fields):
        raise ContractError('Duplicate record field')
    return RecordType(tuple(fields))


def _references(value: str) -> tuple[Reference, ...]:
    return tuple(Reference(_identifier(v.strip())) for v in value.split(',')) if value else ()


def _path(workspace: Path, value: str) -> Path:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or '..' in path.parts or '\\' in value or str(path) != value:
        raise ContractError(f'Path must be canonical and workspace-relative: {value}')
    root = workspace.resolve()
    resolved = (root / value).resolve()
    if not resolved.is_relative_to(root):
        raise ContractError(f'Path escapes workspace: {value}')
    if not resolved.is_file():
        raise ContractError(f'Missing bound source: {value}')
    return resolved


def _records(text: str, kinds: set[str]) -> list[tuple[str, str, dict[str, str]]]:
    lines = text.splitlines(keepends=True)
    records, current, summary, title = [], None, False, False
    index = 0
    while index < len(lines):
        line = lines[index].rstrip('\r\n')
        index += 1
        if not line.strip():
            continue
        if line.startswith('# ') and current is None and not records and not title:
            title = True
            continue
        if line == '## Summary':
            if summary:
                raise ContractError('Duplicate Summary')
            summary, current = True, None
            continue
        if summary:
            if not line.startswith('|') or not line.endswith('|'):
                raise ContractError('Summary must contain only a generated table')
            continue
        if line.startswith('## '):
            heading = line[3:].split(' ')
            if len(heading) != 2 or heading[0] not in kinds:
                raise ContractError(f'Unknown record heading: {line}')
            kind, ident = heading
            _identifier(ident)
            current = {}
            records.append((kind, ident, current))
            continue
        if current is None:
            raise ContractError(f'Unexpected content outside a record: {line}')
        key, colon, value = line.partition(':')
        if not colon or not re.fullmatch(r'[A-Za-z][A-Za-z -]*|Slot [A-Za-z_][A-Za-z0-9_]*', key):
            raise ContractError(f'Malformed field: {line}')
        if key in current:
            raise ContractError(f'Duplicate field: {key}')
        if key == 'Quote':
            if value.strip() or index >= len(lines):
                raise ContractError('Quote requires a literal fenced block')
            fence = lines[index].rstrip('\r\n')
            if not re.fullmatch(r'`{3,}|~{3,}', fence):
                raise ContractError('Quote requires a bare literal fence')
            index += 1
            content = []
            while index < len(lines) and lines[index].rstrip('\r\n') != fence:
                content.append(lines[index])
                index += 1
            if index == len(lines):
                raise ContractError('Unterminated Quote fence')
            index += 1
            value = ''.join(content)
            if value.endswith('\r\n'):
                value = value[:-2]
            elif value.endswith('\n'):
                value = value[:-1]
        else:
            value = value.strip()
        current[key] = value
    ids = [ident for _, ident, _ in records]
    if len(set(ids)) != len(ids):
        raise ContractError('Duplicate ID')
    return records


def _fields(fields: Mapping[str, str], required: set[str], optional: set[str] = frozenset(),
            empty: set[str] = frozenset()) -> None:
    unknown, missing = fields.keys() - required - optional, required - fields.keys()
    if unknown or missing:
        raise ContractError(f'Unknown fields {sorted(unknown)}; missing fields {sorted(missing)}')
    if any(not value and name not in empty for name, value in fields.items()):
        raise ContractError('Empty required field')


def _enum(cls, value):
    try:
        return cls(value)
    except ValueError as exc:
        raise ContractError(f'Unsupported {cls.__name__}: {value}') from exc


def _unsigned(value: str) -> int:
    if not re.fullmatch('[0-9]+', value, re.ASCII):
        raise ContractError('Byte offsets must be unsigned ASCII integers')
    return int(value)


def parse_facts(text: str, *, workspace: Path, bindings: tuple[SourceBinding, ...],
                rules: tuple[RuleRegistration, ...] = (),
                enum_domains: tuple[EnumDomain, ...] = (),
                scopes: tuple[ScopeRegistration, ...] = ()) -> FactsDocument:
    """Parse facts.md text and reject structural errors; no value/quote extraction.

    Includes forward references and unit/rule dependencies in DAG validation.
    Sources must exist and be manifest-bound; symlink escapes also reject.
    Raises ContractError; returns immutable records in authored order per kind.
    """
    bound = {b.path: b for b in bindings}
    if len(bound) != len(bindings):
        raise ContractError('Duplicate source binding')
    facts, derived = [], []
    for kind, ident, f in _records(text, {'Fact', 'Derived'}):
        if kind == 'Fact':
            _fields(f, {'Name', 'Type', 'Origin', 'Value', 'Source', 'Anchor', 'Quote-start',
                        'Select', 'Locale', 'Unit-source', 'Quote'})
            if f['Name'] != ident:
                raise ContractError('Fact Name must equal its heading ID')
            type_ = parse_type(f['Type'])
            if not isinstance(type_, ScalarType):
                raise ContractError('Quoted facts must be scalar; use derived list/record')
            origin = _enum(Origin, f['Origin'])
            resolved = _path(workspace, f['Source'])
            binding = bound.get(f['Source'])
            if binding is None:
                raise ContractError('Unbound source path')
            relative = resolved.relative_to(workspace.resolve()).as_posix()
            if origin != binding.origin or (origin == Origin.SOURCE and
                    (not f['Source'].startswith('input/sources/') or not relative.startswith('input/sources/'))):
                raise ContractError('Origin and path disagree')
            if origin == Origin.REPORT and (f['Source'].startswith('input/sources/') or
                                            relative.startswith('input/sources/')):
                raise ContractError('Origin and path disagree')
            if f['Anchor'] not in binding.anchors:
                raise ContractError('Unbound source anchor')
            locale = dict(binding.anchor_locales).get(f['Anchor'], binding.locale)
            if f['Locale'] not in {'en', 'ro'} or f['Locale'] != locale:
                raise ContractError('Locale is not manifest-bound')
            span = f['Select'].split(':')
            if len(span) != 2:
                raise ContractError('Select requires start:end')
            start, end = map(_unsigned, span)
            quote_bytes = f['Quote'].encode('utf-8')
            if not 0 <= start < end <= len(quote_bytes):
                raise ContractError('Select outside quote or empty')
            try:
                quote_bytes[:start].decode('utf-8')
                quote_bytes[start:end].decode('utf-8')
            except UnicodeDecodeError as exc:
                raise ContractError('Select splits a UTF-8 character') from exc
            unit = None if f['Unit-source'] == 'inline' else Reference(_identifier(f['Unit-source']))
            facts.append(FactRecord(ident, f['Name'], type_, origin, f['Value'], f['Source'],
                                    f['Anchor'], f['Quote'], _unsigned(f['Quote-start']),
                                    ByteSpan(start, end), f['Locale'], unit))
        else:
            _fields(f, {'Type', 'Operation', 'Inputs'}, {'Fields', 'Field', 'Rule', 'Scope'}, {'Inputs', 'Fields'})
            operation = _enum(Operation, f['Operation'])
            required = {Operation.RECORD: {'Fields'}, Operation.FIELD: {'Field'},
                        Operation.FILTER: {'Rule', 'Scope'}, Operation.RULE: {'Rule'}}.get(operation, set())
            if (f.keys() & {'Fields', 'Field', 'Rule', 'Scope'}) != required:
                raise ContractError('Operation metadata does not match operation')
            fields = []
            if f.get('Fields'):
                for field in f['Fields'].split(','):
                    name, separator, ref = field.strip().partition('=')
                    if not separator:
                        raise ContractError('Fields require name=reference')
                    fields.append((_identifier(name.strip()), Reference(_identifier(ref.strip()))))
                if len({name for name, _ in fields}) != len(fields):
                    raise ContractError('Duplicate derived record field')
            inputs = _references(f['Inputs'])
            if operation == Operation.RECORD and inputs:
                raise ContractError('Record dependencies belong in Fields; Inputs must be empty')
            arity = {Operation.FIELD: 1, Operation.FILTER: 1, Operation.SUM: 1,
                     Operation.COUNT: 1, Operation.ADD: 2, Operation.SUBTRACT: 2,
                     Operation.MULTIPLY: 2, Operation.RATIO: 2, Operation.DATE_ADD: 2}
            if operation in arity and len(inputs) != arity[operation]:
                raise ContractError('Wrong operation input count')
            derived.append(DerivedRecord(ident, parse_type(f['Type'], generated=True), operation,
                inputs, tuple(fields), _identifier(f['Field']) if 'Field' in f else None,
                Reference(_identifier(f['Rule'])) if 'Rule' in f else None,
                _references(f.get('Scope', ''))))
    document = FactsDocument(tuple(facts), tuple(derived), enum_domains, scopes)
    validate_dag(document, rules)
    return document


def validate_dag(document: FactsDocument, rules: tuple[RuleRegistration, ...] = ()) -> None:
    """Reject duplicate/dangling IDs and cycles, including unit and rule edges.

    A is responsible for type signatures and transitive report-origin taint.
    Rule registration policy correspondence is verified by A/C, not by this DAG.
    """
    nodes = (*document.facts, *document.derived, *rules, *document.scopes)
    if len({n.id for n in nodes}) != len(nodes):
        raise ContractError('Duplicate ID across facts, derived records or rules')
    if len({d.id for d in document.enum_domains}) != len(document.enum_domains):
        raise ContractError('Duplicate enum domain ID')
    for domain in document.enum_domains:
        _identifier(domain.id)
        if not domain.version or not domain.labels or any(not label for label in domain.labels):
            raise ContractError('Enum domains require version and nonempty labels')
        if len(set(domain.labels)) != len(domain.labels):
            raise ContractError('Duplicate enum domain label')
    values = document.records()
    scopes = {s.id for s in document.scopes}
    rule_ids = {r.id for r in rules}
    graph = {}
    for n in nodes:
        _identifier(n.id)
        if isinstance(n, FactRecord):
            edges = (n.unit_source,) if n.unit_source else ()
            if n.unit_source and n.unit_source.id not in {f.id for f in document.facts}:
                raise ContractError('Unit-source must reference a quoted fact')
        elif isinstance(n, DerivedRecord):
            edges = n.inputs + tuple(r for _, r in n.fields) + n.scope + ((n.rule,) if n.rule else ())
            if any(r.id not in values for r in n.inputs + tuple(r for _, r in n.fields)):
                raise ContractError('Unresolved dependency or non-value operation input')
            if any(r.id not in values and r.id not in scopes for r in n.scope):
                raise ContractError('Unresolved scope reference')
            if n.rule and n.rule.id not in rule_ids:
                raise ContractError('Unregistered rule reference')
        elif isinstance(n, RuleRegistration):
            edges = n.dependencies
        else:
            if not n.evidence or not n.rationale:
                raise ContractError('Scope registration requires evidence and rationale')
            edges = ()
        graph[n.id] = tuple(_identifier(edge.id) for edge in edges)
    done, active = set(), set()
    for ident in graph:
        stack = [(ident, False)]
        while stack:
            current, leaving = stack.pop()
            if leaving:
                active.remove(current)
                done.add(current)
            elif current not in graph:
                raise ContractError(f'Unresolved dependency: {current}')
            elif current in active:
                raise ContractError(f'Derived dependency cycle: {current}')
            elif current not in done:
                active.add(current)
                stack.append((current, True))
                stack.extend((dependency, False) for dependency in reversed(graph[current]))


def parse_claims(text: str, *, facts: FactsDocument, report_anchors: frozenset[str],
                 rules: tuple[RuleRegistration, ...] = ()) -> tuple[ClaimRecord, ...]:
    """Parse claims.md; typed slot categories are fixed by SHAPES.

    Relational type/unit checking is phase B. Unresolved IDs produce an explicit
    not-formalized reason, not a guessed value or free theorem. Syntax, unknown
    fields, duplicate IDs and slots containing Lean text raise ContractError.
    """
    validate_dag(facts, rules)
    values, registered = facts.records(), {rule.id: rule for rule in rules}
    scopes = {scope.id for scope in facts.scopes}
    claims = []
    for _, ident, fields in _records(text, {'Claim'}):
        slots = {key[5:]: value for key, value in fields.items() if key.startswith('Slot ')}
        f = {key: value for key, value in fields.items() if not key.startswith('Slot ')}
        _fields(f, {'Report-anchor', 'Shape', 'Version', 'Polarity', 'Rules', 'Scope', 'Rationale'},
                {'Proof-relation'},
                empty={'Rules', 'Scope'})
        if f['Report-anchor'] not in report_anchors:
            raise ContractError('Unregistered report anchor')
        _identifier(f['Shape'])
        if not re.fullmatch('[0-9]+', f['Version'], re.ASCII):
            raise ContractError('Shape Version must be an unsigned integer')
        shape = SHAPES.get(f['Shape'])
        specs = {s.name: s for s in shape.slots} if shape else {}
        if shape and slots.keys() != specs.keys():
            raise ContractError('Unknown or missing shape slots')
        reasons = []
        if not shape or f['Version'] != SHAPE_VERSION:
            reasons.append('Unsupported shape/version: not formalized')
        parsed = []
        for name, value in slots.items():
            _identifier(name)
            kind = specs[name].kind if shape else SlotKind.VALUE
            if kind == SlotKind.CHOICE:
                if value not in specs[name].choices:
                    raise ContractError(f'Unsupported catalogue choice for {name}')
                slot_value = value
            elif kind == SlotKind.FIELD:
                slot_value = _identifier(value)
            else:
                slot_value = Reference(_identifier(value))
                if value not in (registered if kind == SlotKind.RULE else values):
                    reasons.append(f'Unresolved {kind.value} slot {name}: {value}')
            parsed.append(ClaimSlot(name, kind, slot_value))
        rule_refs, scope = _references(f['Rules']), _references(f['Scope'])
        for ref in rule_refs:
            if ref.id not in registered:
                reasons.append(f'Unregistered rule: {ref.id}')
        for ref in scope:
            if ref.id not in values and ref.id not in scopes:
                reasons.append(f'Unresolved scope reference: {ref.id}')
        used_rules = {s.value.id for s in parsed if s.kind == SlotKind.RULE}
        if not used_rules <= {r.id for r in rule_refs}:
            raise ContractError('Rule slots must be listed in Rules')
        claims.append(ClaimRecord(ident, f['Report-anchor'], f['Shape'], f['Version'], tuple(parsed),
            _enum(Polarity, f['Polarity']), rule_refs, scope, f['Rationale'], '; '.join(reasons) or None,
            _enum(ProofRelation, f.get('Proof-relation', 'supports'))))
    return tuple(claims)
