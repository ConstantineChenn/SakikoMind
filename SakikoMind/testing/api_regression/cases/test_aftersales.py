"""Acceptance contracts based on docs/aftersales.md (simulated policies)."""
import sqlite3
from contextlib import closing

import pytest


@pytest.mark.parametrize('actor', [None, 'unknown'], ids=['missing', 'invalid'])
@pytest.mark.smoke
def test_AS_01_authentication(env, actor):
    before = env.counts()
    response = env.call('GET', '/aftersales/orders/DEMO-1001', actor=actor)
    env.eq(response.status_code, 401, 'missing or invalid bearer rejected')
    env.eq(env.counts(), before, 'no writes for unauthenticated request')


@pytest.mark.parametrize('body', [
    {}, {'order_id': 'DEMO-1001'}, {'order_id': '', 'reason': 'x'},
    {'order_id': 'DEMO-1001', 'reason': ''}, {'order_id': 1001, 'reason': 'x'},
    {'order_id': 'DEMO-1001', 'reason': 'x', 'user_id': 'bob'},
], ids=['empty', 'missing-reason', 'blank-order', 'blank-reason', 'bad-type', 'forged-user'])
def test_AS_02_invalid_parameters(env, body):
    before = env.counts()
    response = env.call('POST', '/aftersales/quotes', body=body)
    env.eq(response.status_code, 422, 'invalid input rejected')
    env.eq(env.counts(), before, 'invalid input causes no business records')


def test_AS_03_cross_user_isolation(env):
    before = env.counts()
    response = env.call('GET', '/aftersales/orders/DEMO-1001', actor='bob')
    env.eq(response.status_code, 404, 'other customer cannot read order')
    env.eq(env.counts(), before, 'rejected read does not write')
    response = env.call('POST', '/aftersales/quotes', actor='bob',
                        body={'order_id': 'DEMO-1001', 'reason': 'x'})
    env.eq(response.status_code, 404, 'other customer cannot prepare quote')
    env.eq(env.counts(), before, 'rejected quote does not write')


def test_AS_04_shipped_order_rejected(env):
    before = env.counts()
    response = env.call('POST', '/aftersales/quotes', body={'order_id': 'DEMO-1003', 'reason': 'x'})
    env.eq(response.status_code, 409, 'shipped order cannot be cancelled')
    env.eq(response.json()['detail']['code'], 'order_not_cancellable', 'business error code')
    env.eq(env.counts(), before, 'invalid order state does not write')


@pytest.mark.parametrize('value', [False, None, 'true', 1], ids=['false', 'null', 'string', 'integer'])
def test_AS_05_explicit_confirmation(env, value):
    quote = env.quote()
    before = env.counts()
    response = env.call('POST', '/aftersales/applications', body={'quote_id': quote['quote_id'],
        'confirmation_token': quote['confirmation_token'], 'idempotency_key': 'key', 'confirmed': value})
    env.eq(response.status_code, 422, 'only boolean true constitutes explicit confirmation')
    env.eq(env.counts(), before, 'unconfirmed request creates no application')


@pytest.mark.parametrize('amount,required', [(9999, False), (10000, True)], ids=['below', 'at-threshold'])
def test_AS_06_approval_threshold(env, amount, required):
    with closing(sqlite3.connect(env.db)) as db:
        db.execute('UPDATE aftersales_orders SET amount_cents=? WHERE order_id=?', (amount, 'DEMO-1001'))
        db.commit()
    quote = env.quote()
    env.eq(quote['amount_cents'], amount, 'quote amount')
    env.eq(quote['approval_required'], required, 'v1 policy threshold')
    response = env.confirm(quote)
    env.eq(response.status_code, 200, 'confirmation status')
    env.eq(response.json()['status'], 'pending_approval' if required else 'ready', 'application state')


@pytest.mark.smoke
def test_AS_07_approved_business_chain(env):
    order = env.call('GET', '/aftersales/orders/DEMO-1002')
    env.eq(order.status_code, 200, 'own order query')
    env.eq((order.json()['user_id'], order.json()['amount_cents'], order.json()['status']),
           ('alice', 29900, 'paid'), 'initial order')
    quote = env.quote('DEMO-1002')
    env.eq(quote['approval_required'], True, 'approval required')
    response = env.confirm(quote)
    env.eq(response.status_code, 200, 'confirm application')
    application = response.json()
    env.eq(application['status'], 'pending_approval', 'pending approval')
    path = '/aftersales/applications/' + application['application_id']
    before = env.counts()
    denied = env.call('POST', path + '/execute')
    env.eq(denied.status_code, 409, 'execution before approval denied')
    env.eq(env.counts(), before, 'denied execution causes no operation')
    approval = env.call('POST', path + '/review', actor='reviewer', body={'approved': True, 'note': '同意'})
    env.eq(approval.status_code, 200, 'reviewer approves')
    env.eq(approval.json()['status'], 'ready', 'ready after approval')
    result = env.call('POST', path + '/execute')
    env.eq(result.status_code, 200, 'simulated execution')
    env.eq(result.json()['status'], 'succeeded', 'operation succeeded')
    trace = result.headers.get('x-trace-id')
    operation = result.json()['operation']['operation_id']
    current = env.call('GET', path)
    env.eq((current.json()['status'], current.json()['operation']['operation_id']), ('succeeded', operation), 'persisted application')
    env.eq(env.sql('SELECT status, amount_cents FROM aftersales_orders WHERE order_id=?', ('DEMO-1002',)),
           [('cancelled', 29900)], 'independent order SQL')
    env.eq(env.sql('SELECT operation_id,user_id,amount_cents FROM aftersales_operations WHERE application_id=?',
                   (application['application_id'],)), [(operation, 'alice', 29900)], 'independent operation SQL')
    audit = env.call('GET', path + '/audit')
    env.eq(audit.status_code, 200, 'audit query')
    env.eq(any(event['trace_id'] == trace for event in audit.json()['events']), True, 'audit correlates execution trace')


@pytest.mark.smoke
def test_AS_08_idempotent_confirm_and_execution(env):
    quote = env.quote()
    first = env.confirm(quote)
    second = env.confirm(quote)
    env.eq((first.status_code, second.status_code), (200, 200), 'replayed confirmations')
    app_id = first.json()['application_id']
    env.eq(second.json()['application_id'], app_id, 'same application returned')
    conflicting = env.call('POST', '/aftersales/applications', body={'quote_id': quote['quote_id'],
        'confirmation_token': 'different-synthetic-secret', 'idempotency_key': 'synthetic-key', 'confirmed': True})
    env.eq((conflicting.status_code, conflicting.json()['detail']['code']), (409, 'idempotency_conflict'), 'different payload conflicts')
    path = '/aftersales/applications/' + app_id + '/execute'
    one, two = env.call('POST', path), env.call('POST', path)
    env.eq((one.status_code, two.status_code), (200, 200), 'execute replay')
    env.eq(one.json()['operation']['operation_id'], two.json()['operation']['operation_id'], 'same operation returned')
    env.eq(env.sql('SELECT COUNT(*) FROM aftersales_applications'), [(1,)], 'one application')
    env.eq(env.sql('SELECT COUNT(*) FROM aftersales_operations'), [(1,)], 'one operation')


def test_AS_09_rejected_review_cannot_execute(env):
    quote = env.quote('DEMO-1002')
    app = env.confirm(quote).json()
    path = '/aftersales/applications/' + app['application_id']
    denied = env.call('POST', path + '/review', body={'approved': True, 'note': 'x'})
    env.eq(denied.status_code, 403, 'customer cannot review')
    review = env.call('POST', path + '/review', actor='reviewer', body={'approved': False, 'note': '拒绝'})
    env.eq((review.status_code, review.json()['status']), (200, 'rejected'), 'rejected state')
    response = env.call('POST', path + '/execute')
    env.eq(response.status_code, 409, 'rejected application not executable')
    env.eq(env.sql('SELECT COUNT(*) FROM aftersales_operations'), [(0,)], 'no operation')
    env.eq(env.sql('SELECT status FROM aftersales_orders WHERE order_id=?', ('DEMO-1002',)), [('paid',)], 'order remains paid')


def test_AS_10_order_state_competition(env):
    quote = env.quote()
    result = env.confirm(quote)
    env.eq(result.status_code, 200, 'confirm while valid')
    app_id = result.json()['application_id']
    with closing(sqlite3.connect(env.db)) as db:
        db.execute('UPDATE aftersales_orders SET status=?,version=version+1 WHERE order_id=?', ('shipped', 'DEMO-1001'))
        db.commit()
    response = env.call('POST', '/aftersales/applications/' + app_id + '/execute')
    env.eq(response.status_code, 409, 'changed order prevents execution')
    env.eq(env.sql('SELECT status FROM aftersales_applications WHERE application_id=?', (app_id,)), [('stale',)], 'application marked stale')
    env.eq(env.sql('SELECT COUNT(*) FROM aftersales_operations'), [(0,)], 'no refund operation')


def test_AS_11_chat_can_prepare_but_not_confirm(env):
    class Planner:
        async def plan(self, message):
            return {'action': 'prepare', 'order_id': 'DEMO-1001', 'reason': '不再需要'}
    env.app.state.aftersales_planner = Planner()
    response = env.call('POST', '/aftersales/chat', body={'message': '取消订单'})
    env.eq(response.status_code, 200, 'planner returns prepare')
    env.eq(response.json()['action'], 'prepare', 'prepare action')
    env.eq(env.sql('SELECT COUNT(*) FROM aftersales_applications'), [(0,)], 'no implicit application')
    env.eq(env.sql('SELECT COUNT(*) FROM aftersales_operations'), [(0,)], 'no implicit refund')
