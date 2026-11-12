"""Where the tests look for the Lean executable: AUDIT_TEST_LEAN when set, else the repository toolchain folder
that tools/toolchain.json names for this platform (installed by "node tools/setup-toolchain.mjs")."""
import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('toolchain_for_tests', ROOT / 'tools/toolchain.py')
_toolchain = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_toolchain)


def lean_path():
    override = os.environ.get('AUDIT_TEST_LEAN')
    if override:
        return Path(override)
    try:
        return _toolchain.lean_executable(ROOT)
    except _toolchain.UnsupportedPlatformError:
        return ROOT / 'tmp/toolchains/lean-unsupported-platform/bin/lean'
