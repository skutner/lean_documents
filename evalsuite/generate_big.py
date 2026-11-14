#!/usr/bin/env python3
"""Generates dossiers of the large-document families. Calls no model.

    python3 evalsuite/generate_big.py --fixtures NEW_DIR --keys NEW_DIR --seed N --dossiers 1 --families minutes

The large-document families are one long, irregular dossier each, written so that the facts cannot be pulled out
by a script: no identifier per fact, no table of the deciding facts, and facts that can only be read together with
a second passage. Each lives in its own module beside this file and exports `build(rng)`, like the other families.

This file only adds them to the generator's list at run time and then runs the generator's own command line, so
`generate.py` itself is not changed and every existing family is generated exactly as before.

It also puts the facts of a large family into its answer key (`check_shown.py` reads them). The key of such a family
gains, and nothing else changes (the fixtures are the same bytes):

    per row        `query` (how to recompute the statement's value from the facts without reading its text) and
                   `value_form` (how the value is written)
    top level      `facts`: {family, interpreter (module name), interpreter_sha256 (hash of the module file that made
                   the dossier), events, passages, names, sources ({file: sha256} of every source file written),
                   readers (the names of the flawed readers the dossier has), and `size` when the case carries one}

The family module must export `answer(query, events, reader=None)`, `value_form(query)`, `relevant(query, events)`,
`depends(events)` (for every event, what it cannot be read without, by kind: name, amount, target) and `NONE_VALUE`, and its case must carry `events`, `passages` and `names` and every claim a `query`; a family that
does not yet gives a plain refusal. The keys of every other family are written as before.
"""
import contextlib
import hashlib
import importlib
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate  # noqa: E402

BIG_FAMILIES = {'minutes': 'big_minutes', 'amendments': 'big_amendments', 'succession': 'big_succession'}
INTERFACE = ('answer', 'value_form', 'relevant', 'depends', 'NONE_VALUE')
CASE_PARTS = ('events', 'passages', 'names')


def register():
    """Add the large-document families to the generator's list. A name that is already taken is an error."""
    taken = set(generate.family_names()) & set(BIG_FAMILIES)
    if taken and any(generate.HARD_FAMILIES.get(name) != BIG_FAMILIES[name] for name in taken):
        raise SystemExit(f'Family name already in use: {", ".join(sorted(taken))}')
    generate.HARD_FAMILIES.update(BIG_FAMILIES)


def interpreter(name):
    """The module of a large family, or a plain refusal naming what it still lacks."""
    try:
        module = importlib.import_module(BIG_FAMILIES[name])
    except ImportError as error:
        raise generate.GenerationError(f'Family {name} is not available: {error}')
    missing = [part for part in INTERFACE if not hasattr(module, part)
               or (part != 'NONE_VALUE' and not callable(getattr(module, part)))]
    if missing:
        raise generate.GenerationError(
            f'Family {name} does not export what the checked answers need ({", ".join(missing)}); '
            f'it cannot be generated with its facts yet')
    return module


def plain(value):
    """A copy that holds only what JSON holds (a tuple becomes a list), so a key never shares objects with a case."""
    return json.loads(json.dumps(value))


def module_hash(module):
    return hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()


class Facts:
    """The state of one `build_case` call: which family it builds and the case its builder returned."""
    family, case = None, None


def with_query(original):
    def expectation(dossier, ident, variant, item, case):
        row = original(dossier, ident, variant, item, case)
        family = Facts.family
        if family is None or family not in BIG_FAMILIES:
            return row
        if not isinstance(item.get('query'), dict):
            raise generate.GenerationError(f'Family {family}: the claim {ident} has no query')
        if any(part not in case for part in CASE_PARTS):
            raise generate.GenerationError(f'Family {family}: its case lacks {", ".join(CASE_PARTS)}')
        module = interpreter(family)
        query = plain(item['query'])
        # The key must say what the checker will compute: refuse a family whose interpreter disagrees with it.
        true = module.answer(item['query'], case['events'])
        agrees = 'supported' if true == item.get('stated') else 'refuted'
        if agrees != row['semantic_state']:
            raise generate.GenerationError(
                f'Family {family}: the interpreter gives {true!r} for {ident}, which makes it {agrees}, '
                f'not {row["semantic_state"]}')
        row['query'] = query
        row['value_form'] = plain(module.value_form(item['query']))
        Facts.case = case
        return row
    return expectation


def with_facts(original):
    def build_case(family, seed, index, scale=1):
        Facts.family, Facts.case = family if family in BIG_FAMILIES else None, None
        try:
            result = original(family, seed, index, scale)
            if Facts.family is not None and Facts.case is not None:
                module = interpreter(family)
                case = Facts.case
                result['key']['facts'] = {
                    'family': family, 'interpreter': BIG_FAMILIES[family], 'interpreter_sha256': module_hash(module),
                    'events': plain(case['events']), 'passages': plain(case['passages']),
                    'names': plain(case['names']),
                    # the files the dossier was written with, so that a checker can tell that a campaign's sources are
                    # these; and the flawed readers this dossier has (a family may drop an optional one)
                    'sources': {name: hashlib.sha256(text.encode('utf-8')).hexdigest()
                                for name, text in sorted(case['sources'].items())},
                    'readers': sorted(case.get('readers') or {})}
                if 'size' in case:          # a family that can build a larger dossier says which size it built
                    result['key']['facts']['size'] = plain(case['size'])
            return result
        finally:
            Facts.family, Facts.case = None, None
    return build_case


def with_interface_check(original):
    """Refuse before any folder is made when a chosen large family cannot give the facts."""
    def write_suite(fixtures, keys, seed, dossiers, families, scale=1):
        for name in families:
            if name in BIG_FAMILIES:
                interpreter(name)
        return original(fixtures, keys, seed, dossiers, families, scale)
    return write_suite


@contextlib.contextmanager
def facts_in_keys():
    """For the length of the block, `generate` writes the facts of a large family into its keys. Nesting is safe."""
    saved = generate.expectation, generate.build_case, generate.write_suite
    generate.expectation = with_query(saved[0])
    generate.build_case = with_facts(saved[1])
    generate.write_suite = with_interface_check(saved[2])
    try:
        yield
    finally:
        generate.expectation, generate.build_case, generate.write_suite = saved


def main():
    register()
    with facts_in_keys():
        generate.main()


if __name__ == '__main__':
    main()
