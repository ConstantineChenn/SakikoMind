"""Controlled stand-in for a failing application dependency; no real credentials."""
import os


def failing_store(request):
    raise RuntimeError('controlled application error: Bearer demo-token-alice confirmation_token='
                       + os.environ['SAKIKO_PROBE_SECRET'])
