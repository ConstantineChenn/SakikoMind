"""Intentionally failing diagnostic probe; not collected by the default suite."""
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get('SAKIKO_DIAGNOSTIC_PROBE') != '1',
                                reason='Intentional failure probe must be explicitly enabled')


def test_expected_failure_for_report_redaction(env):
    Path(os.environ['SAKIKO_PROBE_MARKER']).write_text(str(env.db.parent), encoding='utf-8')
    body = {'quote_id': 'missing', 'confirmation_token': os.environ['SAKIKO_PROBE_SECRET'],
            'idempotency_key': 'probe', 'confirmed': True}
    response = env.call('POST', '/aftersales/applications', body=body)
    env.eq(response.status_code, 200, 'deliberate diagnostic probe')


@pytest.mark.parametrize('mutation', ['amount', 'state'])
def test_expected_failure_after_committed_sql(env, mutation):
    marker = Path(os.environ['SAKIKO_PROBE_MARKER'] + '-' + mutation)
    marker.write_text(str(env.db.parent), encoding='utf-8')
    with closing(sqlite3.connect(env.db)) as db:
        if mutation == 'amount':
            db.execute('UPDATE aftersales_orders SET amount_cents=? WHERE order_id=?', (10000, 'DEMO-1001'))
        else:
            db.execute('UPDATE aftersales_orders SET status=?,version=version+1 WHERE order_id=?', ('shipped', 'DEMO-1001'))
        db.commit()
    response = env.call('GET', '/aftersales/orders/DEMO-1001')
    env.eq(response.status_code, 201, 'deliberate failure after closed SQL connection')


def test_direct_application_exception_is_diagnosable_and_redacted(env, monkeypatch):
    from api import aftersales
    from tools.exception_fault import failing_store
    marker = Path(os.environ['SAKIKO_PROBE_MARKER'] + '-exception')
    marker.write_text(str(env.db.parent), encoding='utf-8')
    monkeypatch.setattr(aftersales, 'get_store', failing_store)
    env.call('POST', '/aftersales/applications', body={
        'quote_id': 'missing', 'confirmation_token': os.environ['SAKIKO_PROBE_SECRET'],
        'idempotency_key': 'probe', 'confirmed': True,
    })
