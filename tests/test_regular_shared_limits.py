"""The regular budget includes preparation, and cached tokens cannot expire mid-window."""
import datetime as dt
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from collector_v1.core import Failure
from collector_v1.regular import runner, policy


def guard(monkeypatch,mode):
    c=dict(expires_at='2026-10-30T14:20:00+00:00')
    monkeypatch.setattr(runner,'assert_runtime_actor',lambda _:None)
    monkeypatch.setattr(policy,'window',lambda _:None)
    monkeypatch.setattr(policy,'access_proof',lambda *a:None)
    return runner.RuntimeGuard(Mock(),c,dict(mode=mode))


def test_preparation_and_collection_share_one_physical_ceiling(monkeypatch):
    g=guard(monkeypatch,'dedicated-account-reviewed')
    g.register_api(SimpleNamespace(budget=SimpleNamespace(used=4)))
    g.register_api(SimpleNamespace(budget=SimpleNamespace(used=5596)))
    with pytest.raises(Failure,match='combined_physical_HTTP_budget_exhausted'):g()
    g.client.active.assert_not_called()


def test_cached_token_too_short_for_frozen_slot_stops_before_data(monkeypatch):
    g=guard(monkeypatch,'shared-account-exclusive')
    from kis_shared import runtime
    expiry=policy.timestamp(g.c['expires_at'])-dt.timedelta(minutes=1)
    monkeypatch.setattr(runtime,'current',lambda:SimpleNamespace(token_deadline=expiry))
    with pytest.raises(Failure,match='token_cannot_cover_frozen_execution_window'):g()
    g.client.active.assert_not_called()
