"""Campaign set-up over any of the six families, for the fidelity tests.

tests/evalsuite_campaign_fixture.py builds only the four original families. This subclass
changes nothing in that module: it only chooses which families its generator call cycles through.
"""
from pathlib import Path
import sys
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import evalsuite_campaign_fixture as base  # noqa: E402
from evalsuite_campaign_fixture import Campaign, perfect, run_blind, ARMS  # noqa: E402,F401


class FamilyCampaign(Campaign):
    """One dossier per family, cycling through `families` (default: all six) in generator order."""

    def __init__(self, root, dossiers=None, seed=11, home=True, families=None):
        # The six families that have a fidelity interface, in generator order. The generator has more
        # families than these; an interface arm refuses a dossier of a family without an interface.
        names = list(families or ('ledger', 'deadlines', 'approvals', 'packet', 'casework', 'procurement'))
        original = base.generate.write_suite

        def write_suite(fixtures, keys, seed_, count, _original_families):
            return original(fixtures, keys, seed_, count, names)
        with mock.patch.object(base.generate, 'write_suite', write_suite):
            super().__init__(root, dossiers=dossiers or len(names), seed=seed, home=home)
