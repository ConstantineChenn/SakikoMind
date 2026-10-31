"""Small HTTP/SQLite helpers; failure output always redacts secret fields."""
import json
import sqlite3
import traceback
from contextlib import closing

SENSITIVE = {'authorization', 'confirmation_token', 'token', 'token_hash', 'api_key', 'access_token', 'cookie', 'set-cookie'}


def redact(value, secrets=()):
    if isinstance(value, dict):
        return {str(k): ('[REDACTED]' if str(k).lower() in SENSITIVE else redact(v, secrets)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in sorted(secrets, key=len, reverse=True):
            if secret:
                value = value.replace(secret, '[REDACTED]')
    return value


def collect_secrets(value):
    result = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in SENSITIVE and isinstance(child, str) and child:
                result.add(child)
                if child.startswith('Bearer '):
                    result.add(child[7:])
            result.update(collect_secrets(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            result.update(collect_secrets(child))
    return result


class Context:
    def __init__(self, client, db, app):
        self.client, self.db, self.app, self.last = client, db, app, {}
        self.secrets = set()

    def call(self, method, path, actor='alice', body=None):
        headers = {} if actor is None else {'Authorization': f'Bearer demo-token-{actor}'}
        # Only controlled, synthetic tokens are ever sent; diagnostics never retain token values.
        self.secrets.update(collect_secrets(headers))
        self.secrets.update(collect_secrets(body))
        self.last = {'method': method, 'path': redact(path, self.secrets), 'request_headers': redact(headers, self.secrets), 'request_body': redact(body, self.secrets)}
        try:
            response = self.client.request(method, path, headers=headers, json=body)
        except Exception as error:
            # Preserve diagnostics from the application, including chained failures,
            # without allowing pytest to render the unredacted original exception.
            stack = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
            diagnostic = {'error_type': type(error).__name__,
                          'message': redact(str(error), self.secrets),
                          'traceback': redact(stack, self.secrets),
                          'http': redact(self.last, self.secrets)}
            raise AssertionError(json.dumps(diagnostic, ensure_ascii=False)) from None
        try:
            response_body = response.json()
        except ValueError:
            response_body = '<non-json response omitted>'
        self.secrets.update(collect_secrets(response_body))
        self.last.update(response_status=response.status_code, response_body=redact(response_body, self.secrets),
                         trace_id=response.headers.get('x-trace-id'))
        return response

    def eq(self, actual, expected, label):
        if actual != expected:
            diagnostic = {'check': label, 'expected': redact(expected, self.secrets), 'actual': redact(actual, self.secrets), 'http': redact(self.last, self.secrets)}
            raise AssertionError(json.dumps(diagnostic, ensure_ascii=False, default=str))

    def sql(self, statement, parameters=()):
        with closing(sqlite3.connect(self.db)) as connection:
            return connection.execute(statement, parameters).fetchall()

    def counts(self):
        counts = {name: self.sql('SELECT COUNT(*) FROM ' + name)[0][0] for name in
                  ('aftersales_quotes', 'aftersales_applications', 'aftersales_idempotency',
                   'aftersales_approvals', 'aftersales_operations', 'aftersales_audit')}
        counts['orders'] = self.sql('SELECT order_id,user_id,status,amount_cents,version FROM aftersales_orders ORDER BY order_id')
        return counts

    def quote(self, order='DEMO-1001'):
        result = self.call('POST', '/aftersales/quotes', body={'order_id': order, 'reason': '不再需要'})
        self.eq(result.status_code, 200, 'quote status')
        return result.json()

    def confirm(self, quote, key='synthetic-key'):
        return self.call('POST', '/aftersales/applications', body={'quote_id': quote['quote_id'],
            'confirmation_token': quote['confirmation_token'], 'idempotency_key': key, 'confirmed': True})
