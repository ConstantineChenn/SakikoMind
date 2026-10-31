"""Verify a deliberate failing case is diagnosable, redacted, and cleaned up."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parent.parent
report = root / 'reports' / 'failure-probe-v2.xml'
report.parent.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='sakiko-probe-marker-') as temp:
    marker = Path(temp) / 'temp-path.txt'
    env = dict(os.environ, SAKIKO_DIAGNOSTIC_PROBE='1', SAKIKO_PROBE_SECRET='PROBE_SECRET_DO_NOT_LEAK_947216', SAKIKO_PROBE_MARKER=str(marker))
    # Explicit test selection: the deliberately failing probe is excluded from normal runs.
    process = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'pytest', '-q',
        'tools/failure_probe_test.py', '--junitxml', str(report)], cwd=root, env=env,
        text=True, encoding='utf-8', errors='replace', stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    content = report.read_text(encoding='utf-8')
    for text in (content, process.stdout):
        if env['SAKIKO_PROBE_SECRET'] in text or 'demo-token-alice' in text:
            raise SystemExit('FAIL: sensitive token appeared in report or console')
    for required in ('expected', 'actual', 'method', 'path', '/aftersales/applications', '[REDACTED]',
                     'traceback', 'controlled application error', 'exception_fault.py', 'aftersales.py', 'line '):
        if required not in content:
            raise SystemExit('FAIL: diagnostic field missing: ' + required)
    for required in ('controlled application error', 'exception_fault.py', 'aftersales.py', '[REDACTED]'):
        if required not in process.stdout:
            raise SystemExit('FAIL: console diagnostic field missing: ' + required)
    if process.returncode != 1 or 'failures="4"' not in content:
        raise SystemExit('FAIL: expected exactly four intentional failed tests')
    for probe in (marker, Path(str(marker) + '-amount'), Path(str(marker) + '-state'), Path(str(marker) + '-exception')):
        if not probe.exists() or Path(probe.read_text(encoding='utf-8')).exists():
            raise SystemExit('FAIL: temporary database directory was not cleaned on failure')
print('PASS: four expected failures are identifiable, redacted, and temporary DBs are removed')
print('Intentional failure report:', report)
