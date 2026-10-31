"""High-risk transaction and concurrency contracts for simulated after-sales.

The fault hook is applied only to the injected in-process store instance; it
does not add any public fault-injection parameter or production endpoint.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient


def _snapshot(db_path):
    """Hash all persistent business rows without printing secret fields."""
    with closing(sqlite3.connect(db_path)) as conn:
        tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'aftersales_%' ORDER BY name"
        )]
        result = {}
        for table in tables:
            rows = sorted(conn.execute(f'SELECT * FROM "{table}"').fetchall(), key=repr)
            data = json.dumps(rows, ensure_ascii=False, default=str, separators=(',', ':')).encode('utf-8')
            result[table] = {'rows': len(rows), 'sha256': hashlib.sha256(data).hexdigest()}
        return result


def _modify_one(db_path, field):
    updates = {
        'status': ("UPDATE aftersales_orders SET status='shipped' WHERE order_id=?",),
        'version': ("UPDATE aftersales_orders SET version=version+1 WHERE order_id=?",),
        'amount': ("UPDATE aftersales_orders SET amount_cents=amount_cents+1 WHERE order_id=?",),
    }
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(updates[field][0], ('DEMO-1001',))
        conn.commit()


def _one_shot_fault(env, monkeypatch, fault):
    store = env.app.state.aftersales_store
    original = store.execute
    pending = True

    def invoke(principal, application_id, trace_id=''):
        nonlocal pending
        if pending:
            pending = False
            return original(principal, application_id, trace_id, fault=fault)
        return original(principal, application_id, trace_id)

    monkeypatch.setattr(store, 'execute', invoke)


def _ready(env):
    quote = env.quote()
    response = env.confirm(quote)
    env.eq(response.status_code, 200, 'create valid application')
    env.eq(response.json()['status'], 'ready', 'low-value application ready')
    return response.json()['application_id']


def test_AS_17_before_commit_timeout_rolls_back_every_business_table(env, monkeypatch):
    application_id = _ready(env)
    path = '/aftersales/applications/' + application_id
    before = _snapshot(env.db)
    _one_shot_fault(env, monkeypatch, 'before_commit')
    response = env.call('POST', path + '/execute')
    env.eq(response.status_code, 504, 'timeout reported through HTTP')
    env.eq(response.json()['detail']['code'], 'outcome_unknown', 'timeout result must be queried')
    env.eq(_snapshot(env.db), before, 'transaction rollback leaves every business table unchanged')
    query = env.call('GET', path)
    env.eq(query.status_code, 200, 'application remains queryable')
    env.eq((query.json()['status'], query.json()['operation']), ('ready', None), 'still ready without operation')


def test_AS_18_after_commit_response_loss_is_recoverable(env, monkeypatch):
    application_id = _ready(env)
    path = '/aftersales/applications/' + application_id
    _one_shot_fault(env, monkeypatch, 'after_commit')
    response = env.call('POST', path + '/execute')
    env.eq(response.status_code, 504, 'lost response returns HTTP timeout')
    env.eq(response.json()['detail']['code'], 'outcome_unknown', 'response is explicitly unknown')
    query = env.call('GET', path)
    env.eq(query.status_code, 200, 'query after response loss')
    env.eq(query.json()['status'], 'succeeded', 'commit succeeded before response loss')
    operation = query.json()['operation']['operation_id']
    env.eq(env.sql('SELECT COUNT(*) FROM aftersales_operations WHERE application_id=?', (application_id,)),
           [(1,)], 'one persisted operation after loss')
    retry = env.call('POST', path + '/execute')
    env.eq(retry.status_code, 200, 'replay after query')
    env.eq(retry.json()['operation']['operation_id'], operation, 'retry returns original operation')
    env.eq(env.sql('SELECT COUNT(*) FROM aftersales_operations WHERE application_id=?', (application_id,)),
           [(1,)], 'retry does not duplicate operation')
    env.eq(env.sql('SELECT status FROM aftersales_orders WHERE order_id=?', ('DEMO-1001',)),
           [('cancelled',)], 'committed order state is retained')


@pytest.mark.parametrize('field', ['status', 'version', 'amount'])
def test_AS_19_single_order_change_after_quote_before_confirmation(env, field):
    quote = env.quote()
    old_order = env.sql('SELECT status,version,amount_cents FROM aftersales_orders WHERE order_id=?', ('DEMO-1001',))[0]
    _modify_one(env.db, field)
    new_order = env.sql('SELECT status,version,amount_cents FROM aftersales_orders WHERE order_id=?', ('DEMO-1001',))[0]
    expected_changes = {'status': (True, False, False), 'version': (False, True, False), 'amount': (False, False, True)}
    env.eq(tuple(a != b for a, b in zip(old_order, new_order)), expected_changes[field], 'only designated field changed')
    before = _snapshot(env.db)
    response = env.confirm(quote)
    env.eq(response.status_code, 409, 'stale quote rejected')
    env.eq(response.json()['detail']['code'], 'order_changed', 'specific stale-order error')
    env.eq(_snapshot(env.db), before, 'rejected confirmation creates no durable changes')


@pytest.mark.parametrize('field', ['status', 'version', 'amount'])
def test_AS_20_single_order_change_after_confirmation_before_execution(env, field):
    application_id = _ready(env)
    old_order = env.sql('SELECT status,version,amount_cents FROM aftersales_orders WHERE order_id=?', ('DEMO-1001',))[0]
    _modify_one(env.db, field)
    new_order = env.sql('SELECT status,version,amount_cents FROM aftersales_orders WHERE order_id=?', ('DEMO-1001',))[0]
    expected_changes = {'status': (True, False, False), 'version': (False, True, False), 'amount': (False, False, True)}
    env.eq(tuple(a != b for a, b in zip(old_order, new_order)), expected_changes[field], 'only designated field changed')
    response = env.call('POST', '/aftersales/applications/' + application_id + '/execute')
    env.eq(response.status_code, 409, 'changed order prevents execution')
    env.eq(response.json()['detail']['code'], 'order_changed', 'specific stale-order error')
    env.eq(env.sql('SELECT status FROM aftersales_applications WHERE application_id=?', (application_id,)),
           [('stale',)], 'application transitions to stale')
    env.eq(env.sql('SELECT status,version,amount_cents FROM aftersales_orders WHERE order_id=?', ('DEMO-1001',)),
           [new_order], 'rejected execution leaves order as modified by fixture')
    env.eq(env.sql('SELECT COUNT(*) FROM aftersales_operations WHERE application_id=?', (application_id,)),
           [(0,)], 'no simulated refund')
    env.eq(env.sql("SELECT COUNT(*) FROM aftersales_audit WHERE action='execution_stale' AND application_id=?", (application_id,)),
           [(1,)], 'stale decision recorded in audit')


def _parallel_http(app, method, path, payload=None, count=4):
    """Four isolated TestClients, synchronized before each HTTP request."""
    barrier = threading.Barrier(count, timeout=15)

    def run(index):
        with TestClient(app) as client:
            barrier.wait()
            response = client.request(method, path, headers={'Authorization': 'Bearer demo-token-alice'}, json=payload)
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(run, range(count)))


def test_AS_21_concurrent_confirm_is_idempotent(env):
    quote = env.quote()
    body = {'quote_id': quote['quote_id'], 'confirmation_token': quote['confirmation_token'],
            'idempotency_key': 'concurrent-key', 'confirmed': True}
    results = _parallel_http(env.app, 'POST', '/aftersales/applications', body)
    env.eq([status for status, _ in results], [200] * 4, 'four concurrent confirmations succeed')
    env.eq(len({body['application_id'] for _, body in results}), 1, 'same application returned')
    env.eq(env.sql('SELECT COUNT(*) FROM aftersales_applications'), [(1,)], 'one application persisted')
    env.eq(env.sql('SELECT COUNT(*) FROM aftersales_idempotency'), [(1,)], 'one idempotency record persisted')


def test_AS_22_concurrent_execute_is_idempotent(env):
    application_id = _ready(env)
    results = _parallel_http(env.app, 'POST', '/aftersales/applications/' + application_id + '/execute')
    env.eq([status for status, _ in results], [200] * 4, 'four concurrent executions succeed')
    env.eq(len({body['operation']['operation_id'] for _, body in results}), 1, 'same operation returned')
    env.eq(env.sql('SELECT COUNT(*) FROM aftersales_operations WHERE application_id=?', (application_id,)),
           [(1,)], 'one operation persisted')
    env.eq(env.sql('SELECT status,version FROM aftersales_orders WHERE order_id=?', ('DEMO-1001',)),
           [('cancelled', 2)], 'one state transition')
