#!/usr/bin/env python3
"""Produce a single-download installer without a mutable helper download."""
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
template = (root / 'src/installer.sh.in').read_text()
result = template
for name in ('storage', 'adoption', 'nosana'):
    helper = (root / ('src/' + name + '.py')).read_text().rstrip()
    marker = '@@' + name.upper() + '_PY@@'
    assert result.count(marker) == 1
    delimiter = 'HOST' if name == 'nosana' else name.upper()
    assert '\nNOSANA_' + delimiter + '_PY\n' not in helper
    result = result.replace(marker, helper)
target = root / 'installer-beta.sh'
if sys.argv[1:] == ['--check']:
    if target.read_text() != result:
        raise SystemExit('installer-beta.sh is stale; run python3 tools/build.py')
elif sys.argv[1:]:
    raise SystemExit('Usage: tools/build.py [--check]')
else:
    target.write_text(result)
    target.chmod(0o755)
