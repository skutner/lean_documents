"""Shared evaluator-only fidelity utilities; no campaign runner or agent execution policy.

The injected Lean runner is the execution boundary. ``trusted_lean_runner`` is only
for generated evaluator programs and locally authored fixtures, never campaign files.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import inspect
import json
import os
from pathlib import Path
import random
import subprocess
import time
from typing import Callable

from evalsuite import generate


class FidelityError(ValueError):
    """Material, execution, or reference validation failed; no score is trustworthy."""


@dataclass
class Regenerated:
    key: dict
    case: dict
    claims: list[dict]
    provenance: dict


def regenerate(key: dict) -> Regenerated:
    """Recover predicates without modifying/patching the generator or trusting key counts.

    build_case discards closures. Replay the family builder with its exact RNG seed,
    then associate selected key rows with the original slot predicates. Fail closed
    on version, key drift, or ambiguous claim identity.
    """
    meta = key.get('generator', {})
    if meta.get('version') != generate.GENERATOR_VERSION:
        raise FidelityError('Generator version mismatch; use the original generator tree')
    try:
        family, seed, index = (meta[k] for k in ('family', 'seed', 'index'))
        rebuilt = generate.build_case(family, seed, index)
    except (KeyError, TypeError, generate.GenerationError) as exc:
        raise FidelityError(f'Cannot regenerate key: {exc}') from exc
    if rebuilt['key'] != key:
        raise FidelityError('Key differs from regenerated key (identity, claims, or verdicts)')
    # family_builder also finds the lazily loaded harder families that FAMILIES omits.
    builder = generate.family_builder(family)
    case = builder(random.Random(f'evalsuite:{generate.GENERATOR_VERSION}:{seed}:{index}:{family}'))
    choices = [item for good, errors in case['slots'] for item in [good, *errors]]
    claims = []
    for row in key['expectations']:
        matches = [c for c in choices if c['text'] == row['claim_text']
                   and c['kind'] == row['claim_kind'] and c['error'] == row['error_type']]
        if len(matches) != 1:
            raise FidelityError(f'Ambiguous or missing regenerated predicate: {row["claim_id"]}')
        claims.append(matches[0])
    # The selected builder's module is the source whose draws were replayed. For the
    # four original families that module is generate.py, so their provenance is unchanged.
    module = Path(inspect.getmodule(builder).__file__).resolve()
    provenance = {**meta, 'source_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
                  'source_file': 'evalsuite/' + module.name}
    if module != Path(generate.__file__).resolve():
        provenance['generate_sha256'] = hashlib.sha256(Path(generate.__file__).read_bytes()).hexdigest()
    return Regenerated(key, case, claims, provenance)


def verdict(values: list[bool]) -> str:
    if not values or any(type(value) is not bool for value in values):
        raise FidelityError('A verdict requires a nonempty Boolean truth vector')
    return 'supported' if all(values) else 'undetermined' if any(values) else 'refuted'


def agreement(actual: list[bool], expected: list[bool]) -> dict:
    if not expected or len(actual) != len(expected):
        raise FidelityError('Empty or unequal truth vectors')
    if any(type(v) is not bool for v in [*actual, *expected]):
        raise FidelityError('Truth vectors must contain Booleans')
    correct = sum(a == e for a, e in zip(actual, expected))
    return {'matched': correct, 'total': len(expected), 'rate': correct / len(expected)}


@dataclass(frozen=True)
class LeanResult:
    returncode: int
    stdout: str
    stderr: str = ''
    seconds: float = 0.0


LeanRunner = Callable[[Path], LeanResult]


def trusted_lean_runner(executable: str | Path, timeout: float = 180) -> LeanRunner:
    """Direct Lean, not Lake. Explicit opt-in; this is NOT a sandbox."""
    executable = str(Path(executable).resolve())

    def run(path: Path) -> LeanResult:
        started = time.monotonic()
        env = {k: v for k, v in os.environ.items() if k not in ('LEAN_PATH', 'LEAN_SRC_PATH')}
        try:
            done = subprocess.run([executable, str(path.resolve())], cwd=path.parent,
                                  env=env, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FidelityError(f'Lean execution failed: {exc}') from exc
        return LeanResult(done.returncode, done.stdout, done.stderr, time.monotonic() - started)
    return run


def run_checked(path: Path, runner: LeanRunner) -> LeanResult:
    result = runner(path)
    path.with_suffix('.output.txt').write_text(result.stdout + result.stderr, encoding='utf-8')
    path.with_suffix('.execution.json').write_text(json.dumps({
        'returncode': result.returncode, 'seconds': result.seconds,
        'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
    }, indent=2) + '\n', encoding='utf-8')
    if result.returncode != 0 or 'warning:' in result.stdout or 'warning:' in result.stderr:
        raise FidelityError(f'Lean rejected {path.name}; inspect {path.with_suffix(".output.txt")}')
    return result


def parse_vectors(output: str, lengths: dict[str, int]) -> dict[str, list[bool]]:
    """Require exactly one complete tagged vector per expected output, no silent gaps."""
    found = {}
    for line in output.splitlines():
        if not line.startswith('"FIDELITY|'):
            continue
        try:
            _, tag, bits = json.loads(line).split('|')
        except (ValueError, json.JSONDecodeError) as exc:
            raise FidelityError('Malformed Lean result') from exc
        if tag not in lengths or tag in found or len(bits) != lengths[tag] or set(bits) - {'0', '1'}:
            raise FidelityError(f'Unexpected, duplicate, or incomplete Lean vector: {tag}')
        found[tag] = [bit == '1' for bit in bits]
    if set(found) != set(lengths):
        raise FidelityError(f'Missing Lean vectors: {sorted(set(lengths) - set(found))}')
    return found
