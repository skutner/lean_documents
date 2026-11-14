"""Shared set-up for campaign tests: generated cases, sealed keys and stand-in agents. No model, no ALA.

The stand-in agents may read the answer keys, because a test has to know what a right or a wrong
answer sheet is. The runner under test never does.
"""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
import blindness  # noqa: E402
import generate  # noqa: E402
import run_blind  # noqa: E402

ARMS = 'plain,skill-no-lean,skill-lean'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dumps_any_integer(value):
    """`json.dumps`, also for a sheet with an integer of more than 4300 digits, which Python 3.11 and later refuse
    to write. The limit is lifted for the writing only: the runner and the checker read the file under the limit
    of the interpreter they run on."""
    if not hasattr(sys, 'set_int_max_str_digits'):
        return json.dumps(value)
    limit = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(0)
    try:
        return json.dumps(value)
    finally:
        sys.set_int_max_str_digits(limit)


class Campaign:
    """One campaign layout under `root`, with every folder where a real campaign would have it."""

    def __init__(self, root, dossiers=3, seed=11, home=True):
        self.root = Path(root)
        self.fixtures, self.draft = self.root / 'generated/fixtures', self.root / 'generated/keys'
        self.keys, self.arena, self.out = self.root / 'sealed', self.root / 'arena', self.root / 'out'
        self.summary = generate.write_suite(self.fixtures, self.draft, seed, dossiers, list(generate.FAMILIES))
        self.arena.mkdir(parents=True)
        blindness.seal_keys(self.draft, self.keys, [self.arena])
        self.lean = self.root / 'toolchain/bin/lean'
        self.lean.parent.mkdir(parents=True)
        self.lean.write_text('not a real Lean\n', encoding='utf-8')
        self.home = self.root / 'agent-home' if home else None
        if home:
            (self.home / '.codex').mkdir(parents=True)
            (self.home / '.codex/auth.json').write_text('{"token": "made-up-login"}\n', encoding='utf-8')
        self.by_report, self.calls = {}, []
        for path in sorted(self.draft.glob('*.json')):
            key = json.loads(path.read_text(encoding='utf-8'))
            for variant in ('correct', 'mixed'):
                report = (self.fixtures / path.stem / f'report-{variant}.md').read_bytes()
                self.by_report[sha(report)] = (path.stem, variant,
                                               [row for row in key['expectations'] if row['variant'] == variant])

    def argv(self, command='run', **options):
        values = {'fixtures': self.fixtures, 'keys': self.keys, 'arena': self.arena, 'out': self.out, 'arms': ARMS,
                  'agent': 'codex', 'model': 'stand-in-model', 'minutes': 30, 'repeats': 1, 'lean': self.lean}
        if self.home:
            values['agent-home'] = self.home
        values.update({name.replace('_', '-'): value for name, value in options.items()})
        argv = [command]
        for name, value in values.items():
            if value is True:
                argv.append('--' + name)
            elif value is not None and value is not False:
                argv += ['--' + name, str(value)]
        return argv

    def launcher(self, policy):
        """A launcher that plays the agent. `policy(call)` returns what the agent hands in and how it ends."""
        def launch(command, cwd, env, seconds, log):
            cwd = Path(cwd)
            dossier, variant, rows = self.by_report[sha((cwd / 'case/report.md').read_bytes())]
            # The arm comes from the runner's own stage records, not from how the review was set up, so a test
            # can tell when a review recorded under one arm was launched with the set-up of another.
            arm = next(path.parts[-3] for path in self.out.glob('*/records/index.json')
                       if cwd.name in json.loads(path.read_text(encoding='utf-8')))
            call = {'command': command, 'cwd': cwd, 'env': env, 'seconds': seconds, 'log': log, 'arm': arm,
                    'dossier': dossier, 'variant': variant, 'rows': rows,
                    'home': Path(command[command.index('--home') + 1]) if '--home' in command else None,
                    'home_files': None,
                    'instructions': (cwd / 'instructions.md').read_text(encoding='utf-8'),
                    'staged': sorted(p.relative_to(cwd).as_posix() for p in cwd.rglob('*') if p.is_file())}
            if call['home']:      # listed now: the runner removes the home when the review ends
                call['home_files'] = sorted(p.relative_to(call['home']).as_posix()
                                            for p in call['home'].rglob('*') if p.is_file())
            self.calls.append(call)
            outcome = policy(call)
            if outcome.get('sheet') is not None:
                (cwd / run_blind.SHEET).write_text(dumps_any_integer(outcome['sheet']), encoding='utf-8')
            return {'ended': outcome.get('ended', 'exit_0'), 'exit_code': outcome.get('exit_code', 0),
                    'seconds': outcome.get('seconds', 12.0)}
        return launch

    def run(self, policy, **options):
        """Run the campaign with a stand-in agent. Returns the runner's exit status."""
        options.setdefault('skip_probe', True)
        overrides = {'launch': self.launcher(policy), 'ala': {'entry': '/stand-in/bin/ala.mjs', 'version': 'stand-in',
                                                              'commit': None}, 'node': 'node'}
        return run_blind.main(self.argv('run', **options), overrides)

    def units(self):
        return {path.stem: json.loads(path.read_text(encoding='utf-8')) for path in sorted((self.out / 'units').glob('*.json'))}


def sheet(call, state=None):
    """An answer sheet for one review: the right verdicts, or `state(row)` for each claim."""
    claims = []
    for row in call['rows']:
        claims.append({'claim_id': row['claim_id'], 'semantic_state': state(row) if state else row['semantic_state'],
                       'evidence': [{'path': 'case/' + anchor.split('#')[0], 'anchor': anchor.split('#')[1]}
                                    for anchor in row['source_anchors']],
                       'explanation': f'Checked {row["claim_id"]} in {call["cwd"].name}.'})
    return {'claims': claims}


def perfect(call):
    return {'sheet': sheet(call)}


def credulous(call):
    """Accepts every claim, as a reviewer who never checks would."""
    return {'sheet': sheet(call, lambda row: 'supported')}


def echoing(call):
    """Takes what the packet says about itself at its word, and is right about everything else."""
    return {'sheet': sheet(call, lambda row: row.get('echo_state') or row['semantic_state'])}
