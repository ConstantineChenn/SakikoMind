import hashlib
import importlib.metadata
import json
import os
import platform
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption('--sakiko-source', default=os.environ.get('SAKIKOMIND_SOURCE') or str(Path(__file__).resolve().parents[2]),
                     help='Path to the SakikoMind Python service directory')


def pytest_configure(config):
    value = config.getoption('--sakiko-source')
    if not value:
        raise pytest.UsageError('Set SAKIKOMIND_SOURCE or pass --sakiko-source PATH')
    root = Path(value).expanduser().resolve()
    if not (root / 'api' / 'aftersales.py').is_file() or not (root / 'core' / 'aftersales_store.py').is_file():
        raise pytest.UsageError('Source path must contain api/aftersales.py and core/aftersales_store.py')
    sys.path.insert(0, str(root))
    config.sakiko_root = root
    config.addinivalue_line('markers', 'api: in-process HTTP contracts with temporary SQLite')
    config.addinivalue_line('markers', 'smoke: critical authenticated business path and idempotency')
    config.addinivalue_line('markers', 'security: authentication and authorization rejection contracts')
    config.addinivalue_line('markers', 'recovery: transaction rollback, response loss and concurrent replay')


def pytest_report_header(config):
    root = config.sakiko_root
    digest = hashlib.sha256((root / 'api' / 'aftersales.py').read_bytes()).hexdigest()
    return f'isolated ASGI + SQLite; python={platform.python_version()}; platform={platform.system()}; api_sha256={digest}'


@pytest.fixture(scope='session', autouse=True)
def report_environment(request, record_testsuite_property):
    import fastapi
    import httpx
    import pydantic
    root = request.config.sakiko_root
    record_testsuite_property('scope', 'inprocess-http-temporary-sqlite-simulated-payment')
    record_testsuite_property('python', platform.python_version())
    record_testsuite_property('platform', platform.platform())
    record_testsuite_property('sqlite', sqlite3.sqlite_version)
    record_testsuite_property('fastapi', fastapi.__version__)
    record_testsuite_property('pydantic', pydantic.__version__)
    record_testsuite_property('httpx', httpx.__version__)
    for relative in ('api/aftersales.py', 'core/aftersales_store.py', 'core/aftersales_planner.py'):
        record_testsuite_property('sha256:' + relative, hashlib.sha256((root / relative).read_bytes()).hexdigest())
    project = Path(__file__).parent
    tracked = [project / 'conftest.py', project / 'pytest.ini', project / 'requirements.txt', project / 'requirements.lock']
    tracked.extend(sorted((project / 'cases').glob('*.py')))
    tracked.extend(sorted((project / 'tools').glob('*.py')))
    for path in tracked:
        relative = path.relative_to(project).as_posix()
        record_testsuite_property('test_sha256:' + relative, hashlib.sha256(path.read_bytes()).hexdigest())
    dependencies = sorted((dist.metadata['Name'], dist.version) for dist in importlib.metadata.distributions()
                          if dist.metadata.get('Name'))
    record_testsuite_property('installed_distributions', json.dumps(dependencies, ensure_ascii=False))


@pytest.fixture
def env():
    from fastapi.testclient import TestClient
    from api.aftersales import create_app
    from core.aftersales_store import AfterSalesStore
    from cases.support import Context

    identities = {
        'demo-token-alice': {'user_id': 'alice', 'role': 'customer'},
        'demo-token-bob': {'user_id': 'bob', 'role': 'customer'},
        'demo-token-reviewer': {'user_id': 'reviewer', 'role': 'reviewer'},
    }
    # Explicitly injecting store/tokens bypasses dotenv, real services and LLM initialization.
    with tempfile.TemporaryDirectory(prefix='sakiko-api-test-') as directory:
        db = Path(directory) / 'isolated.sqlite3'
        store = AfterSalesStore(str(db))
        store.seed_demo()
        app = create_app(store=store, tokens=identities)
        with TestClient(app) as client:
            yield Context(client, db, app)


def pytest_collection_modifyitems(items):
    for item in items:
        if item.path.parent.name != 'cases':
            continue
        item.add_marker(pytest.mark.api)
        if item.path.name == 'test_security_extended.py':
            item.add_marker(pytest.mark.security)
        elif item.path.name == 'test_recovery_extended.py':
            item.add_marker(pytest.mark.recovery)
