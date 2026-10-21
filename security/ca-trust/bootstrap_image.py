"""Offline image installer/venv-seed repair using hash-reviewed public wheels."""
import glob
import hashlib
import importlib.util
import re
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tempfile

ROOT = Path(__file__).resolve().parent
PIP_HASH = 'fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70'
CERTIFI_HASH = '42d35340cc5b9ba78e818ddbff0c5b1c2b779de448a340fd1b989f5fa8670e1b'

def run(*args):
    subprocess.run(list(args), check=True)

def main():
    wheels = ROOT / 'wheels'
    pip = wheels / 'pip-26.2.2-py3-none-any.whl'
    certifi = wheels / 'certifi-2026.10.19-py3-none-any.whl'
    for path, expected in [(pip, PIP_HASH), (certifi, CERTIFI_HASH)]:
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Bootstrap wheel identity mismatch')
    # Upstream CPython names its bundled wheel using this reviewed version constant.
    # Debian instead discovers /usr/share/python-wheels; both paths are verified below.
    module = importlib.util.find_spec('ensurepip')
    if module and module.origin:
        path = Path(module.origin)
        source = path.read_text()
        source, changed = re.subn(r'^_PIP_VERSION = [\"\'][0-9.]+[\"\']$',
                                  '_PIP_VERSION = "26.2.2"', source, flags=re.M)
        if changed:
            path.write_text(source)
    # Keep future venv creation from reintroducing an affected pip bundle.
    for directory in [Path('/usr/share/python-wheels'), Path(sysconfig.get_path('stdlib')) / 'ensurepip/_bundled']:
        if not directory.is_dir() or not list(directory.glob('pip-*.whl')):
            continue
        destination = directory / pip.name
        shutil.copyfile(pip, destination)
        destination.chmod(0o644)
        for old in directory.glob('pip-*.whl'):
            if old != destination:
                old.unlink()
    run(sys.executable, '-I', '-m', 'pip', 'install', '--no-index', '--no-deps', '--require-hashes',
        '--break-system-packages', '--find-links', str(wheels), '-r', str(ROOT / 'requirements-bootstrap.txt'))
    run(sys.executable, '-I', str(ROOT / 'verify_trust.py'), '--system', '--python')
    with tempfile.TemporaryDirectory(prefix='ca-seed-check-') as temporary:
        run(sys.executable, '-I', '-m', 'venv', temporary)
        python = str(Path(temporary) / 'bin/python')
        result = subprocess.check_output([python, '-I', '-m', 'pip', '--version'], text=True)
        if not result.startswith('pip 26.2.2 '):
            raise ValueError('Fresh venv used an unexpected pip seed')
        run(python, '-I', str(ROOT / 'verify_trust.py'), '--system', '--python')

if __name__ == '__main__':
    main()
