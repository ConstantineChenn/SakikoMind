"""High-risk authorization and confirmation contracts for simulated after-sales.

All requests use the test client's in-process HTTP path and synthetic users.
The pre/post table digests include complete rows without emitting token hashes.
"""
import hashlib
import json
import sqlite3
from contextlib import closing

import pytest


TABLES = (
    'aftersales_orders', 'aftersales_policies', 'aftersales_quotes',
    'aftersales_applications', 'aftersales_idempotency', 'aftersales_approvals',
    'aftersales_operations', 'aftersales_audit',
)


def business_snapshot(env):
    """Hash every full row per table to detect updates as well as additions.

    Values, including confirmation-token hashes, are not printed on failure.
    Row ordering is normalized so physical SQLite row order is immaterial.
    """
    with closing(sqlite3.connect(env.db)) as connection:
        snapshot = {}
        for table in TABLES:
            rows = connection.execute(f'SELECT * FROM {table}').fetchall()
            encoded_rows = sorted(json.dumps(row, ensure_ascii=False, separators=(',', ':')) for row in rows)
            snapshot[table] = hashlib.sha256('\n'.join(encoded_rows).encode('utf-8')).hexdigest()
        return snapshot


def assert_denied_without_changes(env, response, expected_status, before, code=None):
    env.eq(response.status_code, expected_status, 'unauthorized request status')
    if code is not None:
        env.eq(response.json()['detail']['code'], code, 'business error code')
    env.eq(business_snapshot(env), before, 'all business table rows unchanged after rejection')


def confirmed_application(env):
    quote = env.quote('DEMO-1002')
    response = env.confirm(quote)
    env.eq(response.status_code, 200, 'authorized application setup')
    application = response.json()
    env.eq(application['status'], 'pending_approval', 'authorized application pending approval')
    return quote, application


@pytest.mark.parametrize('endpoint', [
    'order-read', 'quote-prepare', 'confirm', 'application-read',
    'review', 'execute', 'audit', 'chat',
])
@pytest.mark.parametrize('actor', [None, 'unknown'], ids=['missing-token', 'invalid-token'])
def test_AS_12_all_routes_require_authentication(env, endpoint, actor):
    quote, application = confirmed_application(env)
    path = '/aftersales/applications/' + application['application_id']
    routes = {
        'order-read': ('GET', '/aftersales/orders/DEMO-1002', None),
        'quote-prepare': ('POST', '/aftersales/quotes', {'order_id': 'DEMO-1002', 'reason': 'test'}),
        'confirm': ('POST', '/aftersales/applications', {'quote_id': quote['quote_id'],
            'confirmation_token': quote['confirmation_token'], 'idempotency_key': 'auth-matrix-new', 'confirmed': True}),
        'application-read': ('GET', path, None),
        'review': ('POST', path + '/review', {'approved': True, 'note': 'test'}),
        'execute': ('POST', path + '/execute', None),
        'audit': ('GET', path + '/audit', None),
        'chat': ('POST', '/aftersales/chat', {'message': '查询订单'}),
    }
    method, request_path, body = routes[endpoint]
    before = business_snapshot(env)
    response = env.call(method, request_path, actor=actor, body=body)
    assert_denied_without_changes(env, response, 401, before)
    env.eq(response.json(), {'detail': '需要有效的售后访问令牌'}, 'no protected response fields')


def test_AS_13_bob_cannot_confirm_alices_quote(env):
    quote = env.quote()
    before = business_snapshot(env)
    response = env.call('POST', '/aftersales/applications', actor='bob', body={
        'quote_id': quote['quote_id'], 'confirmation_token': quote['confirmation_token'],
        'idempotency_key': 'bob-new-key', 'confirmed': True,
    })
    assert_denied_without_changes(env, response, 404, before, 'not_found')
    env.eq(env.sql('SELECT COUNT(*) FROM aftersales_applications'), [(0,)], 'no application')
    env.eq(env.sql('SELECT COUNT(*) FROM aftersales_idempotency'), [(0,)], 'no idempotency record')


@pytest.mark.parametrize('action', ['read', 'execute', 'audit'])
def test_AS_14_bob_cannot_access_alices_application(env, action):
    _, application = confirmed_application(env)
    path = '/aftersales/applications/' + application['application_id']
    method, suffix = {'read': ('GET', ''), 'execute': ('POST', '/execute'), 'audit': ('GET', '/audit')}[action]
    before = business_snapshot(env)
    response = env.call(method, path + suffix, actor='bob')
    assert_denied_without_changes(env, response, 404, before, 'not_found')
    env.eq('application_id' in response.json(), False, 'response contains no application data')


@pytest.mark.parametrize('decision', [True, False], ids=['approve', 'reject'])
def test_AS_15_reviewer_cannot_review_own_application(env, decision):
    from core.aftersales_store import Principal

    _, application = confirmed_application(env)
    # The server assigns reviewer role and Alice identity to a separate synthetic token.
    env.app.state.aftersales_tokens['demo-token-self-reviewer'] = Principal('alice', 'reviewer')
    before = business_snapshot(env)
    response = env.call('POST', '/aftersales/applications/' + application['application_id'] + '/review',
                        actor='self-reviewer', body={'approved': decision, 'note': 'synthetic self review'})
    assert_denied_without_changes(env, response, 403, before, 'self_review_forbidden')
    env.eq(env.sql('SELECT COUNT(*) FROM aftersales_approvals'), [(0,)], 'no self-approval persisted')


@pytest.mark.parametrize('kind', ['wrong-token', 'other-quote-token', 'expired-quote'])
def test_AS_16_confirmation_credential_boundaries(env, kind):
    quote = env.quote()
    token = quote['confirmation_token']
    if kind == 'wrong-token':
        token = 'synthetic-incorrect-confirmation'
    elif kind == 'other-quote-token':
        token = env.quote('DEMO-1002')['confirmation_token']
    else:
        with closing(sqlite3.connect(env.db)) as connection:
            connection.execute('UPDATE aftersales_quotes SET expires_at=? WHERE quote_id=?',
                               ('2000-01-01T00:00:00+00:00', quote['quote_id']))
            connection.commit()
    before = business_snapshot(env)
    response = env.call('POST', '/aftersales/applications', body={
        'quote_id': quote['quote_id'], 'confirmation_token': token,
        'idempotency_key': 'unused-key-' + kind, 'confirmed': True,
    })
    expected = (409, 'quote_expired') if kind == 'expired-quote' else (403, 'invalid_confirmation')
    assert_denied_without_changes(env, response, expected[0], before, expected[1])
    env.eq(env.sql('SELECT COUNT(*) FROM aftersales_applications'), [(0,)], 'no application created')
    env.eq(env.sql('SELECT COUNT(*) FROM aftersales_idempotency'), [(0,)], 'no idempotency record created')
