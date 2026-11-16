"""Fail-closed authored syntax and syntactic transcription guards (phase C).

Public inspect_rules returns {"Rules.ID": Alias(parameters, body)}. Alias and
Expr are immutable syntax trees; Expr(kind, value, children) contains no executed
Lean or source values. Rule parameters are ordered names. detect_transcription
also accepts string alias bodies for legacy replay, but never unfolds names in
SourceBound.Facts/Report: those generated fact leaves are opaque. generated_aliases
builds Derived aliases from the typed DAG and already-regenerated generated files;
its caller must verify those files before using the aliases. Unsupported bodies
remain unclassified when reached, never guessed or evaluated.

Section 6 matching inventory: numeric terms versus digit-bearing identifiers,
projections and universes; signs/bases/decimals/grouping/rates/dates/units;
comments/nested comments versus strings/raw strings/chars and commands; True
versus Bool.true, names and aliases; identity versus arithmetic, restatements,
hypotheses, empty quantifiers and contradiction; source/report and scalar/list/
derived dependencies; generated versus authored modules. Pairwise overlaps are
tested by cross-products within these categories, including comment+literal,
projection+numeric, alias+True, alias+identity, binder+let, beta+record and
report+derived. Root True also records transcription. Unsupported syntax records
unclassified rather than claiming absence of transcription. Runtime failure,
timeout, applicability, sensitivity, status and default/profile overlap tests
belong to source_bound_proofs and integration, not this syntax-only module.

The authored catalogue is intentionally finite: + - * / %, comparisons, Bool
operators, List.sum/length/map/filter/foldl/all/any/append/reverse. No numeral,
numeric constructor/conversion (including open Int.ofNat), general Std
call, string, char, type universe, coercion or proof term is accepted. Numeric
results of closed computations are rejected even without literal tokens. Empty
lists are structural seeds, never policy constants. Record fields use the ABI's
f_ names. Enum constructors are accepted only in exhaustive match patterns.
This is a syntax boundary, not a proof against algorithmically hidden constants.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import audit
from source_bound_contract import (
    ContractError, GeneratedFile, GuardBudget, GuardFlag, GuardResult, ListType, Operation,
    RecordType, Reference, RenderedClaim, ResolvedFacts, RuleRegistration,
    ScalarType, lean_type_binding, report_origin_leaves,
)


@dataclass(frozen=True)
class Expr:
    kind: str
    value: str = ''
    children: tuple[Expr, ...] = ()


@dataclass(frozen=True)
class Alias:
    parameters: tuple[str, ...]
    body: Expr


_IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_\']*(?:\.[A-Za-z_][A-Za-z0-9_\']*)*')
_SIMPLE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')
_NUMBER = re.compile(r'(?:0[xX][0-9A-Fa-f]+|0[bB][01]+|[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)')
_OPERATORS = (':=', '=>', '->', '<->', '==', '!=', '<=', '>=', '&&', '||', '::', '++')
_PREC = {'→': 10, '->': 10, '↔': 15, '<->': 15, '∨': 20, '||': 20,
         '∧': 25, '&&': 25, '=': 30, '≠': 30, '==': 30, '!=': 30,
         '<': 30, '>': 30, '≤': 30, '≥': 30, '<=': 30, '>=': 30, '∈': 30,
         '::': 35, '++': 35, '+': 40, '-': 40, '*': 50, '/': 50, '%': 50}
_STOP = {'then', 'else', 'in', 'with', 'def', 'end', 'namespace', 'import'}
_FORBIDDEN_TERMS = {'by', 'do', 'where', 'nomatch', 'termination_by', 'decreasing_by',
                    'sorry', 'admit', 'partial', 'unsafe', 'match_failure', 'Type', 'Sort'}


def _tokens(text: str) -> list[str]:
    """Skip nested comments; reject literals rather than masking them away."""
    result, i = [], 0
    while i < len(text):
        if text[i].isspace():
            i += 1
        elif text.startswith('--', i):
            end = text.find('\n', i)
            i = len(text) if end < 0 else end + 1
        elif text.startswith('/-', i):
            depth, i = 1, i + 2
            while depth and i < len(text):
                if text.startswith('/-', i):
                    depth, i = depth + 1, i + 2
                elif text.startswith('-/', i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            if depth:
                raise ContractError('Unterminated Lean comment')
        elif text[i] in {'"', "'", '#', '`', '@', '«', '»', '\\'}:
            raise ContractError('Literal or unsupported Lean syntax')
        else:
            match = _IDENT.match(text, i) or _NUMBER.match(text, i)
            if match:
                result.append(match.group())
                i = match.end()
                continue
            op = next((op for op in _OPERATORS if text.startswith(op, i)), None)
            if op:
                result.append(op)
                i += len(op)
            elif text[i] in '()[]{}.,:;|+-*/%=<>¬!∧∨→↔≤≥≠∀∃∈⟨⟩':
                result.append(text[i])
                i += 1
            else:
                raise ContractError(f'Unsupported Lean character: {text[i]!r}')
    if len(result) > 20000:
        raise ContractError('Syntax token budget exceeded')
    return result


class _Parser:
    def __init__(self, tokens: list[str]):
        self.tokens, self.i = tokens, 0

    def peek(self) -> str:
        return self.tokens[self.i] if self.i < len(self.tokens) else ''

    def take(self, expected: str | None = None) -> str:
        token = self.peek()
        if not token or (expected is not None and token != expected):
            raise ContractError(f'Expected {expected or "term"}, found {token or "end of input"}')
        self.i += 1
        return token

    def name(self) -> str:
        name = self.take()
        if not _SIMPLE.fullmatch(name) or name in _STOP:
            raise ContractError('Expected a simple binder name')
        return name

    def expression(self, minimum: int = 0, stop: frozenset[str] = frozenset()) -> Expr:
        token = self.take()
        if token in stop or token in _STOP:
            raise ContractError('Missing expression')
        if token == '(':
            node = self.expression(stop=frozenset({')', ',', ':'}))
            if self.peek() == ',':
                values = [node]
                while self.peek() == ',':
                    self.take(',')
                    values.append(self.expression(stop=frozenset({',', ')'})))
                node = Expr('tuple', children=tuple(values))
            elif self.peek() == ':':
                self.take(':')
                annotation = self.expression(stop=frozenset({')'}))
                node = Expr('annotation', children=(node, annotation))
            self.take(')')
        elif token == '[':
            values = []
            while self.peek() != ']':
                values.append(self.expression(stop=frozenset({',', ']'})))
                if self.peek() != ',':
                    break
                self.take(',')
            self.take(']')
            node = Expr('list', children=tuple(values))
        elif token == '{':
            fields = []
            while self.peek() != '}':
                field = self.name()
                self.take(':=')
                fields.append(Expr('field', field, (self.expression(stop=frozenset({',', '}'})),)))
                if self.peek() != ',':
                    break
                self.take(',')
            self.take('}')
            if len({f.value for f in fields}) != len(fields):
                raise ContractError('Duplicate record field')
            node = Expr('record', children=tuple(sorted(fields, key=lambda f: f.value)))
        elif token == 'let':
            name = self.name()
            annotation = Expr('unknown')
            if self.peek() == ':':
                self.take(':')
                annotation = self.expression(stop=frozenset({':='}))
            self.take(':=')
            value = self.expression(stop=frozenset({';', 'in'}))
            if self.take() not in {';', 'in'}:
                raise ContractError('Let requires ; or in')
            node = Expr('let', name, (annotation, value, self.expression(stop=stop)))
        elif token in {'fun', '∀', '∃'}:
            binders = []
            while self.peek() not in {'=>', ',', '∈'}:
                if self.peek() == '(':
                    self.take('(')
                    name = self.name()
                    self.take(':')
                    typ = self.expression(stop=frozenset({')'}))
                    self.take(')')
                else:
                    name, typ = self.name(), Expr('unknown')
                    if self.peek() == ':':
                        self.take(':')
                        typ = self.expression(stop=frozenset({',', '=>'}))
                binders.append((name, typ))
            domain = None
            if self.peek() == '∈':
                self.take('∈')
                domain = self.expression(stop=frozenset({','}))
            self.take('=>' if token == 'fun' else ',')
            if not binders or (domain is not None and len(binders) != 1):
                raise ContractError('Unsupported binder group')
            node = self.expression(stop=stop)
            if domain is not None:
                membership = Expr('binary', '∈', (Expr('name', binders[0][0]), domain))
                node = Expr('binary', '→' if token == '∀' else '∧', (membership, node))
            for name, typ in reversed(binders):
                node = Expr({'fun': 'lambda', '∀': 'forall', '∃': 'exists'}[token], name, (typ, node))
        elif token == 'if':
            condition = self.expression(stop=frozenset({'then'}))
            self.take('then')
            yes = self.expression(stop=frozenset({'else'}))
            self.take('else')
            node = Expr('if', children=(condition, yes, self.expression(stop=stop)))
        elif token == 'match':
            subject = self.expression(stop=frozenset({'with'}))
            self.take('with')
            branches = []
            while self.peek() == '|':
                self.take('|')
                pattern = self.expression(stop=frozenset({'=>'}))
                self.take('=>')
                body = self.expression(stop=stop | frozenset({'|'}))
                branches.append(Expr('branch', children=(pattern, body)))
            if not branches:
                raise ContractError('Match requires branches')
            node = Expr('match', children=(subject, *branches))
        elif token in {'¬', '!', '-'}:
            node = Expr('unary', token, (self.expression(60, stop),))
        elif _IDENT.fullmatch(token) and token not in _FORBIDDEN_TERMS:
            node = Expr('name', token)
        elif _NUMBER.fullmatch(token):
            node = Expr('number', token)
        else:
            raise ContractError(f'Unsupported term: {token}')
        while self.peek() and self.peek() not in stop and self.peek() not in _STOP:
            token = self.peek()
            if token == '.':
                self.take('.')
                field = self.take()
                if not (_SIMPLE.fullmatch(field) or re.fullmatch(r'[1-9][0-9]*', field)):
                    raise ContractError('Unsupported projection')
                node = Expr('projection', field, (node,))
            elif token in _PREC and _PREC[token] >= minimum:
                self.take()
                precedence = _PREC[token]
                rhs = self.expression(precedence if token in {'→', '->', '::'} else precedence + 1, stop)
                node = Expr('binary', {'->': '→', '<->': '↔', '<=': '≤', '>=': '≥'}.get(token, token), (node, rhs))
            elif minimum <= 70 and (token in {'(', '[', '{'} or
                                      (_IDENT.fullmatch(token) and token not in _STOP) or
                                      _NUMBER.fullmatch(token)):
                node = Expr('apply', children=(node, self.expression(71, stop)))
            else:
                break
        return node


def parse_expression(text: str) -> Expr:
    """Parse one complete term. Unsupported syntax raises ContractError."""
    parser = _Parser(_tokens(text))
    result = parser.expression()
    if parser.peek():
        raise ContractError(f'Unconsumed syntax: {parser.peek()}')
    return result


def _application(node: Expr) -> tuple[Expr, tuple[Expr, ...]]:
    arguments = []
    while node.kind == 'apply':
        node, arg = node.children
        arguments.append(arg)
    return node, tuple(reversed(arguments))


def _expand_name(node: Expr, bound: set[str], aliases: dict[str, object]) -> Expr:
    if node.kind != 'name':
        return node
    parts = node.value.split('.')
    if node.value.startswith(('SourceBound.Facts.', 'SourceBound.Report.', 'SourceBound.Derived.')):
        node = Expr('name', '.'.join(parts[:3]))
        for field in parts[3:]:
            node = Expr('projection', field, (node,))
        return node
    if len(parts) > 1 and (parts[0] in bound or parts[0] in aliases):
        node = Expr('name', parts[0])
        for field in parts[1:]:
            node = Expr('projection', field, (node,))
    return node


def _normalize(node: Expr, aliases: dict[str, object], env: dict[str, Expr],
               active: tuple[str, ...], fuel: list[int], depth: int = 0) -> Expr:
    fuel[0] -= 1
    if fuel[0] < 0:
        raise ContractError('Normalization budget exceeded')
    if node.kind == 'unsupported':
        raise ContractError(node.value)
    node = _expand_name(node, set(env), aliases)
    if node.kind == 'name':
        if node.value in env:
            return env[node.value]
        if node.value.startswith(('SourceBound.Facts.', 'SourceBound.Report.')):
            return node
        if node.value in aliases:
            if node.value in active:
                raise ContractError('Recursive alias')
            alias = aliases[node.value]
            if isinstance(alias, str):
                alias = Alias((), parse_expression(alias))
            if not isinstance(alias, Alias):
                raise ContractError('Unsupported alias representation')
            body = alias.body
            for parameter in reversed(alias.parameters):
                body = Expr('lambda', parameter, (Expr('unknown'), body))
            # Definition free names must never capture a caller's local binder.
            return _normalize(body, aliases, {}, (*active, node.value), fuel, depth)
        if node.value.startswith('SourceBound.Derived.'):
            raise ContractError('Missing generated alias')
        return node
    if node.kind == 'let':
        _, value, body = node.children
        value = _normalize(value, aliases, env, active, fuel, depth)
        return _normalize(body, aliases, {**env, node.value: value}, active, fuel, depth)
    if node.kind in {'lambda', 'forall', 'exists'}:
        typ, body = node.children
        # Unique internal names prevent capture during subsequent beta reduction.
        # A separate pass alpha-canonicalizes these after all substitution.
        ident = node.value if node.value.startswith('@') else '@' + str(fuel[0])
        variable = Expr('bound', ident)
        return Expr(node.kind, ident, (
            _normalize(typ, aliases, env, active, fuel, depth),
            _normalize(body, aliases, {**env, node.value: variable}, active, fuel, depth + 1)))
    if node.kind == 'match':
        subject = _normalize(node.children[0], aliases, env, active, fuel, depth)
        branches = []
        for branch in node.children[1:]:
            pattern, body = branch.children
            local = dict(env)
            if pattern.kind == 'binary' and pattern.value == '::':
                if any(c.kind != 'name' or not _SIMPLE.fullmatch(c.value) for c in pattern.children):
                    raise ContractError('Unsupported match normalization')
                names = tuple(c.value for c in pattern.children)
                if len(set(names)) != len(names):
                    raise ContractError('Duplicate pattern binder')
                # Represent pattern binding scope explicitly for alpha comparison.
                for name in reversed(names):
                    body = Expr('lambda', name, (Expr('unknown'), body))
                pattern = Expr('pattern', 'cons')
            elif pattern.kind == 'list' and not pattern.children:
                pattern = Expr('pattern', 'nil')
            elif pattern.kind != 'name' or '.' not in pattern.value:
                raise ContractError('Unsupported match normalization')
            branches.append(Expr('branch', children=(pattern,
                _normalize(body, aliases, local, active, fuel, depth))))
        return Expr('match', children=(subject, *branches))
    if node.kind == 'annotation':
        return _normalize(node.children[0], aliases, env, active, fuel, depth)
    children = tuple(_normalize(c, aliases, env, active, fuel, depth) for c in node.children)
    if node.kind == 'apply' and children[0].kind == 'lambda':
        lam, argument = children
        def substitute(term: Expr) -> Expr:
            if term == Expr('bound', lam.value):
                return argument
            return Expr(term.kind, term.value, tuple(substitute(c) for c in term.children))
        return _normalize(substitute(lam.children[1]), aliases, {}, active, fuel, depth)
    if node.kind == 'projection':
        base = children[0]
        if base.kind == 'record':
            matches = [f.children[0] for f in base.children if f.value == node.value]
            if len(matches) != 1:
                raise ContractError('Unknown explicit record projection')
            return matches[0]
        if base.kind == 'tuple' and node.value.isdecimal():
            index = int(node.value) - 1
            if index < 0 or index >= len(base.children):
                raise ContractError('Unknown tuple projection')
            return base.children[index]
    # No arithmetic, comparison, match or conditional reduction.
    return Expr(node.kind, node.value, children)


def _alpha(node: Expr, env: dict[str, str] | None = None, depth: int = 0) -> Expr:
    env = env or {}
    if node.kind == 'bound':
        if node.value not in env:
            raise ContractError('Escaping normalized binder')
        return Expr('bound', env[node.value])
    if node.kind in {'lambda', 'forall', 'exists'}:
        return Expr(node.kind, str(depth), (
            _alpha(node.children[0], env, depth),
            _alpha(node.children[1], {**env, node.value: str(depth)}, depth + 1)))
    return Expr(node.kind, node.value, tuple(_alpha(c, env, depth) for c in node.children))


def _trivial(node: Expr) -> bool:
    if node == Expr('name', 'True'):
        return True
    if node.kind == 'forall':
        return _trivial(node.children[1])
    if node.kind != 'binary':
        return False
    a, b = node.children
    if node.value in {'=', '↔'}:
        return a == b
    if node.value == '∧':
        return _trivial(a) and _trivial(b)
    if node.value == '→':
        return a == b or _trivial(b)
    return False


def detect_transcription(target: str, aliases: dict | None = None) -> frozenset[GuardFlag]:
    """Normalize syntax only; unsupported parsing/reduction fails unclassified."""
    try:
        tree = _alpha(_normalize(parse_expression(target), aliases or {}, {}, (), [20000]))
        flags = set()
        if tree == Expr('name', 'True'):
            flags.add(GuardFlag.ROOT_TRUE)
        if _trivial(tree):
            flags.add(GuardFlag.TRANSCRIPTION)
        return frozenset(flags)
    except (ContractError, RecursionError):
        return frozenset({GuardFlag.UNCLASSIFIED})


def generated_aliases(facts: ResolvedFacts,
                      files: tuple[GeneratedFile, ...]) -> dict[str, Alias | str]:
    """Expose generated syntax without unfolding source/report fact values.

    Files must be the trusted deterministic regeneration, verified by the caller.
    Record/field/list/rule syntax comes from the DAG and the generated ABI. Other
    operations use their exact generated declaration bodies (not numeric resolved
    values). The current generator emits one fully qualified def per line. A
    future unsupported declaration form produces an unsupported alias and hence
    UNCLASSIFIED if reached. This function does not execute or evaluate Lean.
    """
    bindings = facts.bindings
    records = facts.document.records()
    values = {value.id: value for value in facts.values}
    derived_files = [f for f in files if f.path == 'models/SourceBound/Derived.lean']
    if len(derived_files) > 1:
        raise ContractError('Duplicate generated Derived module')
    lines = derived_files[0].content.splitlines() if derived_files else []
    bodies: dict[str, str] = {}
    for record in facts.document.derived:
        binding = bindings.values[record.id]
        prefix = f'def {binding.name} : {binding.lean_type} := '
        candidates = [line[len(prefix):] for line in lines if line.startswith(prefix)]
        if len(candidates) > 1:
            raise ContractError('Duplicate generated derived declaration')
        if candidates:
            bodies[record.id] = candidates[0]

    def reference(ref: Reference) -> Expr:
        if ref.id not in bindings.values:
            raise ContractError('Unbound generated alias reference')
        return Expr('name', bindings.values[ref.id].name)

    def generated_body(record) -> Alias | str:
        body = bodies.get(record.id)
        if body is None:
            return Alias((), Expr('unsupported', 'Missing or unsupported generated declaration'))
        typ = bindings.types[record.type]
        # Only a top anonymous wrapper is transformed, using its ABI projection.
        # Nested anonymous constructors (e.g. ratio normalization) remain explicit
        # unsupported syntax until their syntax is supported, not evaluated.
        if (body.startswith('⟨') and body.endswith('⟩') and typ.integer_projection):
            projection = parse_expression(typ.integer_projection.format(value='wrapper'))
            if projection.kind == 'projection' and projection.children == (Expr('name', 'wrapper'),):
                try:
                    value = parse_expression(body[1:-1])
                except (ContractError, RecursionError):
                    return body
                return Alias((), Expr('record', children=(Expr('field', projection.value, (value,)),)))
        # Parse lazily in detect_transcription so unsupported unrelated aliases
        # do not poison a target that never depends on them.
        return body

    result: dict[str, Alias | str] = {}
    for record in facts.document.derived:
        op = record.operation
        if record.id not in bodies:
            alias = generated_body(record)
        elif op == Operation.RECORD:
            if not isinstance(record.type, RecordType):
                raise ContractError('Generated record has a non-record type')
            abi = {wire: lean for wire, lean, _ in bindings.types[record.type].fields}
            if set(abi) != {name for name, _ in record.fields} or len(abi) != len(record.fields):
                raise ContractError('Generated record fields differ from ABI')
            fields = tuple(Expr('field', abi[name], (reference(ref),)) for name, ref in record.fields)
            alias = Alias((), Expr('record', children=tuple(sorted(fields, key=lambda f: f.value))))
        elif op == Operation.FIELD:
            if len(record.inputs) != 1:
                raise ContractError('Generated field requires one record')
            source_type = records[record.inputs[0].id].type
            abi = {wire: lean for wire, lean, _ in bindings.types[source_type].fields}
            if record.field not in abi:
                raise ContractError('Generated field is absent from ABI')
            alias = Alias((), Expr('projection', abi[record.field], (reference(record.inputs[0]),)))
        elif op == Operation.LIST:
            if values[record.id].value == tuple(values[ref.id].value for ref in record.inputs):
                alias = Alias((), Expr('list', children=tuple(reference(ref) for ref in record.inputs)))
            else:
                # Synthetic removal/duplication uses the regenerated expression.
                alias = generated_body(record)
        elif op == Operation.RULE:
            if record.rule is None or not _SIMPLE.fullmatch(record.rule.id):
                raise ContractError('Generated rule call requires a registered rule ID')
            body = Expr('name', f'Rules.{record.rule.id}')
            for ref in record.inputs:
                body = Expr('apply', children=(body, reference(ref)))
            alias = Alias((), body)
        else:
            alias = generated_body(record)
        result[bindings.values[record.id].name] = alias
    return result


_NAT, _INT, _BOOL = ScalarType('nat'), ScalarType('int'), ScalarType('bool')
_PROP = 'Prop'
_CATALOGUE = {'List.sum', 'List.length', 'List.map', 'List.filter', 'List.foldl',
              'List.all', 'List.any', 'List.append', 'List.reverse', 'decide'}


@dataclass(frozen=True)
class _Closure:
    """Typed local lambda with lexical environment, not a computed data value."""
    signature: tuple
    expression: Expr
    environment: dict

    def __iter__(self):
        yield self.signature
        # Merely constructing a function does not provide an independent input.
        yield False


class _RuleChecker:
    def __init__(self, facts: ResolvedFacts, rules: tuple[RuleRegistration, ...]):
        self.facts, self.rules = facts, {r.id: r for r in rules}
        self.bindings = facts.bindings
        self.values = {v.id: v for v in facts.values}
        self.names = {b.name: ident for ident, b in self.bindings.values.items()}
        self.used: set[str] = set()
        self.rule: RuleRegistration | None = None

    def type_expr(self, node: Expr):
        types = set(self.bindings.types) | {_NAT, _INT, _BOOL}
        for rule in self.rules.values():
            types.update(t for _, t in rule.parameters)
            types.add(rule.result_type)
        for typ in types:
            rendered = lean_type_binding(typ, self.facts.document.enum_domains).lean_type
            if parse_expression(rendered) == node:
                return typ
        raise ContractError('Type must match a registered signature or generated ABI type')

    def has_source_dependency(self, ident: str, active: frozenset[str] = frozenset()) -> bool:
        if ident in active:
            raise ContractError('Recursive rule dependency')
        if ident in self.rules:
            return any(self.has_source_dependency(ref.id, active | {ident})
                       for ref in self.rules[ident].dependencies)
        return ident in self.bindings.values and not report_origin_leaves(
            Reference(ident), self.facts, tuple(self.rules.values()))

    def infer(self, node: Expr, env: dict, expected=None):
        node = _expand_name(node, set(env), {})
        if node.kind == 'name' and node.value not in self.names:
            for name in sorted(self.names, key=len, reverse=True):
                if node.value.startswith(name + '.'):
                    tail = node.value[len(name) + 1:].split('.')
                    node = Expr('name', name)
                    for field in tail:
                        node = Expr('projection', field, (node,))
                    break
        kind, children = node.kind, node.children
        if kind == 'number':
            raise ContractError('Numeric literal terms are forbidden')
        if kind == 'name':
            if node.value in env:
                return env[node.value]
            if node.value in {'true', 'false', 'Bool.true', 'Bool.false'}:
                return _BOOL, False
            if node.value in self.names:
                ident = self.names[node.value]
                binding = self.bindings.values[ident]
                if binding.module != 'SourceBound.Facts' or report_origin_leaves(Reference(ident), self.facts, tuple(self.rules.values())):
                    raise ContractError('Rules cannot consume report or derived inputs')
                self.used.add(ident)
                return self.values[ident].type, True
            if node.value.startswith('Rules.') and node.value[6:] in self.rules:
                rule = self.rules[node.value[6:]]
                self.used.add(rule.id)
                if rule.parameters:
                    return ('function', tuple(t for _, t in rule.parameters), rule.result_type), self.has_source_dependency(rule.id)
                return rule.result_type, self.has_source_dependency(rule.id)
            raise ContractError(f'Unbound or forbidden name: {node.value}')
        if kind == 'annotation':
            typ = self.type_expr(children[1])
            actual, open_ = self.infer(children[0], env, typ)
            if actual != typ:
                raise ContractError('Type annotation mismatch')
            return typ, open_
        if kind == 'projection':
            typ, open_ = self.infer(children[0], env)
            if isinstance(typ, tuple) and typ[0] == 'tuple' and node.value.isdecimal():
                index = int(node.value) - 1
                if not 0 <= index < len(typ[1]):
                    raise ContractError('Tuple projection is outside the tuple')
                return typ[1][index], open_
            fields = dict((lean_name, t) for (_, t), (_, lean_name, _) in zip(
                typ.fields, lean_type_binding(typ, self.facts.document.enum_domains).fields)) if isinstance(typ, RecordType) else {}
            if isinstance(typ, ScalarType):
                fields = {'money': {'minor': _INT}, 'date': {'ordinal': _INT},
                          'ratio': {'num': _INT, 'den': _NAT}}.get(typ.kind, {})
            if node.value not in fields:
                raise ContractError('Unsupported field projection')
            return fields[node.value], open_
        if kind == 'tuple':
            values = tuple(self.infer(c, env) for c in children)
            if not all(open_ for _, open_ in values):
                raise ContractError('Tuple elements must be references')
            return ('tuple', tuple(t for t, _ in values)), True
        if kind == 'let':
            annotation, value, body = children
            typ = None if annotation.kind == 'unknown' else self.type_expr(annotation)
            binding = self.infer(value, env, typ)
            actual, open_ = binding
            if typ is not None and actual != typ:
                raise ContractError('Let annotation mismatch')
            return self.infer(body, {**env, node.value: binding}, expected)
        if kind == 'lambda':
            annotation, body = children
            if annotation.kind == 'unknown':
                if not isinstance(expected, tuple) or expected[0] != 'function' or len(expected[1]) != 1:
                    raise ContractError('Lambda needs a known parameter type')
                typ = expected[1][0]
            else:
                typ = self.type_expr(annotation)
            result_expected = expected[2] if isinstance(expected, tuple) and expected[0] == 'function' else None
            result, _ = self.infer(body, {**env, node.value: (typ, True)}, result_expected)
            return _Closure(('function', (typ,), result), node, dict(env))
        if kind == 'list':
            if not children:
                if not isinstance(expected, ListType):
                    raise ContractError('Empty-list seed needs an expected list type')
                return expected, False
            values = [self.infer(c, env) for c in children]
            if not all(t == values[0][0] and o for t, o in values):
                raise ContractError('List elements must be homogeneous references')
            return ListType(values[0][0]), True
        if kind == 'record':
            if not isinstance(expected, RecordType):
                raise ContractError('Record needs a registered record type')
            fields = {f'f_{name}': typ for name, typ in expected.fields}
            if {f.value for f in children} != fields.keys():
                raise ContractError('Record fields do not match generated ABI')
            for field in children:
                typ, open_ = self.infer(field.children[0], env, fields[field.value])
                if typ != fields[field.value] or not open_:
                    raise ContractError('Record fields must be typed references')
            return expected, True
        if kind == 'if':
            condition, _ = self.infer(children[0], env)
            yes, yo = self.infer(children[1], env, expected)
            no, no_ = self.infer(children[2], env, expected)
            if condition not in {_BOOL, _PROP} or yes != no:
                raise ContractError('Ill-typed conditional')
            if isinstance(yes, tuple) and yes[0] == 'function':
                raise ContractError('Function-valued conditionals are unsupported')
            return yes, yo or no_
        if kind == 'binary':
            a, ao = self.infer(children[0], env)
            b, bo = self.infer(children[1], env, a)
            if a != b:
                raise ContractError('Operator operand types disagree')
            op = node.value
            if op in {'+', '-', '*', '/', '%'} and a in {_NAT, _INT}:
                if not (ao or bo):
                    raise ContractError('Closed numeric computation')
                return a, True
            if op in {'=', '≠', '<', '>', '≤', '≥', '==', '!='}:
                if op not in {'=', '≠', '==', '!='} and a not in {_NAT, _INT}:
                    raise ContractError('Only numeric catalogue ordering is supported')
                return (_BOOL if op in {'==', '!='} else _PROP), ao or bo
            if op in {'&&', '||'} and a == _BOOL:
                return _BOOL, ao or bo
            if op in {'∧', '∨', '→', '↔'} and a == _PROP:
                return _PROP, ao or bo
            if op == '++' and isinstance(a, ListType):
                return a, ao or bo
            raise ContractError('Unsupported operator or operands')
        if kind == 'unary':
            typ, open_ = self.infer(children[0], env)
            if (node.value, typ) not in {('-', _INT), ('!', _BOOL), ('¬', _PROP)}:
                raise ContractError('Unsupported unary operator')
            if typ == _INT and not open_:
                raise ContractError('Closed numeric computation')
            return typ, open_
        if kind == 'apply':
            head, args = _application(node)
            # Specialize immediate lambdas at their actual argument openness.
            # Otherwise a bound parameter could launder an empty-list length.
            if head.kind == 'lambda':
                local, body = dict(env), head
                for arg in args:
                    if body.kind != 'lambda':
                        raise ContractError('Too many lambda arguments')
                    annotation, inner = body.children
                    if annotation.kind == 'unknown':
                        actual, arg_open = self.infer(arg, env)
                    else:
                        parameter_type = self.type_expr(annotation)
                        actual, arg_open = self.infer(arg, env, parameter_type)
                        if actual != parameter_type:
                            raise ContractError('Lambda argument type mismatch')
                    local[body.value] = (actual, arg_open)
                    body = inner
                return self.infer(body, local, expected)
            if head.kind == 'name' and head.value in _CATALOGUE:
                return self.catalogue(head.value, args, env, expected)
            function = self.infer(head, env)
            if isinstance(function, _Closure):
                for arg in args:
                    if not isinstance(function, _Closure):
                        raise ContractError('Too many local function arguments')
                    parameter_type = function.signature[1][0]
                    binding = self.infer(arg, env, parameter_type)
                    actual, _ = binding
                    if actual != parameter_type:
                        raise ContractError('Local function argument type mismatch')
                    local = {**function.environment, function.expression.value: binding}
                    function = self.infer(function.expression.children[1], local, expected)
                return function
            typ, open_ = function
            if not isinstance(typ, tuple) or typ[0] != 'function' or len(args) != len(typ[1]):
                raise ContractError('Only fully applied typed rules/lambdas are supported')
            for arg, expected_arg in zip(args, typ[1]):
                actual, arg_open = self.infer(arg, env, expected_arg)
                if actual != expected_arg:
                    raise ContractError('Rule argument type mismatch')
                open_ |= arg_open
            if typ[2] in {_NAT, _INT} and not open_:
                raise ContractError('Closed numeric computation')
            return typ[2], open_
        if kind == 'match':
            return self.match(node, env, expected)
        raise ContractError(f'Unsupported authored expression: {kind}')

    def catalogue(self, name: str, args: tuple[Expr, ...], env: dict, expected):
        if name == 'decide':
            if len(args) != 1:
                raise ContractError('Wrong catalogue arity')
            typ, open_ = self.infer(args[0], env)
            if typ != _PROP:
                raise ContractError('Catalogue argument type mismatch')
            return _BOOL, open_
        arity = 3 if name == 'List.foldl' else 2 if name in {
            'List.map', 'List.filter', 'List.all', 'List.any', 'List.append'} else 1
        if len(args) != arity:
            raise ContractError('Wrong list catalogue arity')
        # Lean's all/any take the list first; map/filter/foldl take it last.
        sequence_index = 0 if name in {'List.all', 'List.any'} else -1
        seq, open_ = self.infer(args[sequence_index], env, expected if isinstance(expected, ListType) else None)
        if not isinstance(seq, ListType):
            raise ContractError('List catalogue needs a list')
        if name in {'List.length', 'List.sum'}:
            if not open_:
                raise ContractError('Closed numeric list computation')
            if name == 'List.sum' and seq.element not in {_NAT, _INT}:
                raise ContractError('List.sum requires numeric projections')
            return (_NAT if name == 'List.length' else seq.element), True
        if name == 'List.reverse':
            return seq, open_
        if name == 'List.append':
            first, fo = self.infer(args[0], env, seq)
            if first != seq:
                raise ContractError('List.append type mismatch')
            return seq, open_ or fo
        if name == 'List.foldl':
            seed, so = self.infer(args[1], env, expected)
            fn, _ = self.infer(args[0], env, ('function', (seed,), ('function', (seq.element,), seed)))
            valid = {('function', (seed, seq.element), seed),
                     ('function', (seed,), ('function', (seq.element,), seed))}
            if fn not in valid or (seed in {_NAT, _INT} and not (so or open_)):
                raise ContractError('List.foldl type or closed seed mismatch')
            return seed, so or open_
        callback_index = 1 if name in {'List.all', 'List.any'} else 0
        fn, _ = self.infer(args[callback_index], env, ('function', (seq.element,), _BOOL))
        if not isinstance(fn, tuple) or fn[0:2] != ('function', (seq.element,)):
            raise ContractError('List callback signature mismatch')
        if name != 'List.map' and fn[2] != _BOOL:
            raise ContractError('List predicate must return Bool')
        return (ListType(fn[2]) if name == 'List.map' else
                seq if name == 'List.filter' else _BOOL), open_

    def match(self, node: Expr, env: dict, expected):
        typ, source_open = self.infer(node.children[0], env)
        constructors = dict(lean_type_binding(typ, self.facts.document.enum_domains).constructors)
        expected_patterns = set(constructors.values()) if constructors else {'nil', 'cons'} if isinstance(typ, ListType) else set()
        if not expected_patterns:
            raise ContractError('Only registered enum and list matches are supported')
        seen, outputs = set(), []
        for branch in node.children[1:]:
            pattern, body = branch.children
            local = dict(env)
            if pattern.kind == 'list' and not pattern.children:
                key = 'nil'
            elif pattern.kind == 'binary' and pattern.value == '::' and isinstance(typ, ListType):
                a, b = pattern.children
                if a.kind != 'name' or b.kind != 'name' or not _SIMPLE.fullmatch(a.value) or not _SIMPLE.fullmatch(b.value) or a.value == b.value:
                    raise ContractError('List pattern needs distinct binders')
                key = 'cons'
                local.update({a.value: (typ.element, source_open), b.value: (typ, source_open)})
            elif pattern.kind == 'name':
                key = pattern.value
            else:
                raise ContractError('Unsupported match pattern')
            if key in seen or key not in expected_patterns:
                raise ContractError('Duplicate or unknown match constructor')
            seen.add(key)
            outputs.append(self.infer(body, local, expected))
        result_type, _ = outputs[0]
        if seen != expected_patterns or any(t != result_type for t, _ in outputs):
            raise ContractError('Match must be exhaustive with one result type')
        if isinstance(result_type, tuple) and result_type[0] == 'function':
            raise ContractError('Function-valued matches are unsupported')
        return result_type, any(o for _, o in outputs)


def inspect_rules(path: Path, facts: ResolvedFacts,
                  rules: tuple[RuleRegistration, ...]) -> dict[str, object]:
    """Run unchanged lexical guard, then validate only registered pure definitions.

    Every declaration/signature/dependency is checked. Generated modules must be
    verified by byte regeneration by the proof runner, never passed here. This
    gate checks syntax/types; Lean compilation remains mandatory afterward.
    """
    try:
        audit.inspect_model(path, local_modules=('SourceBound.Facts',))
    except audit.AuditError as exc:
        raise ContractError(str(exc)) from exc
    parser = _Parser(_tokens(path.read_text(encoding='utf-8')))
    checker = _RuleChecker(facts, rules)
    if len(checker.rules) != len(rules):
        raise ContractError('Duplicate registered rule')
    for rule in rules:
        if not _SIMPLE.fullmatch(rule.id) or not rule.policy or not rule.correspondence.strip():
            raise ContractError('Rule requires an ASCII ID, quoted policy and correspondence')
        names = [n for n, _ in rule.parameters]
        if len(set(names)) != len(names) or any(not _SIMPLE.fullmatch(n) for n in names):
            raise ContractError('Invalid rule parameter names')
        for ref in rule.dependencies:
            if report_origin_leaves(ref, facts, rules):
                raise ContractError('Report-tainted rule dependency')
    parser.take('import')
    parser.take('SourceBound.Facts')
    aliases, graph, namespace = {}, {}, False
    if parser.peek() == 'namespace':
        parser.take('namespace')
        parser.take('Rules')
        namespace = True
    while parser.peek() == 'def':
        parser.take('def')
        name = parser.take()
        ident = name if namespace else name.removeprefix('Rules.')
        full = f'Rules.{ident}'
        if ident not in checker.rules or full in aliases or (not namespace and name != full):
            raise ContractError('Only unique registered Rules definitions are allowed')
        rule = checker.rules[ident]
        parameters = []
        for expected_name, expected_type in rule.parameters:
            parser.take('(')
            actual_name = parser.name()
            parser.take(':')
            typ = parser.expression(stop=frozenset({')'}))
            parser.take(')')
            if actual_name != expected_name or checker.type_expr(typ) != expected_type:
                raise ContractError('Registered rule parameter signature mismatch')
            parameters.append(actual_name)
        parser.take(':')
        result_type = parser.expression(stop=frozenset({':='}))
        if checker.type_expr(result_type) != rule.result_type:
            raise ContractError('Registered rule result signature mismatch')
        parser.take(':=')
        body = parser.expression(stop=frozenset({'def', 'end'}))
        checker.used, checker.rule = set(), rule
        actual_type, open_ = checker.infer(body, {n: (t, True) for n, t in rule.parameters}, rule.result_type)
        if actual_type != rule.result_type:
            raise ContractError('Rule body differs from registered result type')
        if rule.result_type in {_NAT, _INT} and not open_:
            raise ContractError('Closed numeric rule result')
        if checker.used != {r.id for r in rule.dependencies}:
            raise ContractError('Rule dependency registration differs from authored references')
        graph[ident] = checker.used & checker.rules.keys()
        aliases[full] = Alias(tuple(parameters), body)
    if namespace:
        parser.take('end')
        if parser.peek() == 'Rules':
            parser.take('Rules')
    if parser.peek() or len(aliases) != len(rules):
        raise ContractError('Unsupported command or missing registered rule definition')
    def visit(ident: str, active: frozenset[str], complete: set[str]):
        if ident in active:
            raise ContractError('Recursive rule dependency')
        if ident not in complete:
            for dependency in graph[ident]:
                visit(dependency, active | {ident}, complete)
            complete.add(ident)
    complete: set[str] = set()
    for ident in graph:
        visit(ident, frozenset(), complete)
    return aliases


def check_guards(workspace: Path, rendered: RenderedClaim, facts: ResolvedFacts,
                 rules: tuple[RuleRegistration, ...], authored: tuple[GeneratedFile, ...],
                 *, budget: GuardBudget, attempt_directory: Path,
                 sandbox_command: tuple[str, ...]) -> GuardResult:
    """Lazy entrypoint keeps static parsing independent of execution support."""
    from source_bound_proofs import check_proof
    return check_proof(workspace, rendered, facts, rules, authored, budget=budget,
                       attempt_directory=attempt_directory, sandbox_command=sandbox_command)
