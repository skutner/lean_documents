"""Location of the repository-local Lean toolchain.

Reads the same tools/toolchain.json as tools/toolchain.mjs. Kept free of syntax newer than Python 3.9, because the
macOS system interpreter is 3.9 until tools/setup-toolchain.mjs has installed a newer one.
"""
import json
import platform as _platform
import sys
from pathlib import Path

MANIFEST = Path(__file__).resolve().with_name('toolchain.json')
TOOLCHAINS = Path('tmp') / 'toolchains'

_SYSTEMS = {'linux': 'linux', 'darwin': 'darwin'}
# Node names (process.arch) for the machine names Python reports.
_MACHINES = {'x86_64': 'x64', 'amd64': 'x64', 'x64': 'x64', 'aarch64': 'arm64', 'arm64': 'arm64', 'i386': 'ia32', 'i686': 'ia32', 'x86': 'ia32'}


class UnsupportedPlatformError(RuntimeError):
    pass


def load_manifest():
    with open(MANIFEST, encoding='utf-8') as handle:
        return json.load(handle)


def platform_key(system=None, machine=None):
    """Return linux-x64, linux-arm64, darwin-x64 or darwin-arm64, with the error text of platformKey in toolchain.mjs."""
    system = (system if system is not None else sys.platform).lower()
    if system.startswith('linux'):
        system = 'linux'
    machine = (machine if machine is not None else _platform.machine()).lower()
    arch = _MACHINES.get(machine, machine)
    if system not in _SYSTEMS or arch not in ('x64', 'arm64'):
        raise UnsupportedPlatformError('Unsupported platform for the Lean toolchain: %s-%s' % (system, arch))
    return '%s-%s' % (_SYSTEMS[system], arch)


def lean_executable(repo, key=None):
    """Return <repo>/tmp/toolchains/<folder>/bin/lean for the platform key (the current platform when omitted)."""
    key = key or platform_key()
    entry = load_manifest()['lean']['platforms'].get(key)
    if entry is None:
        raise UnsupportedPlatformError('The lean toolchain has no entry for the platform %s' % key)
    return Path(repo) / TOOLCHAINS / entry['folder'] / 'bin' / 'lean'
