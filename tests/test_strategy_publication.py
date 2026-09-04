# -*- coding: utf-8 -*-
"""共享策略发布服务测试。

覆盖策略配置指纹、公共策略命名限制、不可覆盖规则和远端镜像。

Revision History:
    2026-09-04  create shared public strategy publication tests
"""
from __future__ import annotations

import pytest

from stock_plan.strategy import publication


def _config() -> dict:
    return {"weights": {"trend_score": 1.0}, "rules": {}, "params": {"hold_days": 20}}


def test_strategy_fingerprint_is_stable() -> None:
    assert publication.strategy_fingerprint({"b": 2, "a": 1}) == publication.strategy_fingerprint({"a": 1, "b": 2})


def test_strategy_fingerprint_changes_with_config() -> None:
    assert publication.strategy_fingerprint(_config()) != publication.strategy_fingerprint(
        {"weights": {"trend_score": 0.5}, "rules": {}, "params": {"hold_days": 20}}
    )


def test_submit_public_strategy_creates_pending_record(monkeypatch) -> None:
    stored: dict = {}
    monkeypatch.setattr(publication, "load_public_strategies", lambda: {})
    monkeypatch.setattr(publication, "_write_remote_json", lambda _name, payload: stored.update(payload))

    record = publication.submit_public_strategy("共享趋势", _config(), "builder")

    assert record["status"] == "pending"
    assert record["fingerprint"] == publication.strategy_fingerprint(_config())
    assert stored["strategies"]["共享趋势"]["source"] == "builder"


@pytest.mark.parametrize("name", ["", "a", "x" * 31])
def test_submit_public_strategy_rejects_invalid_name(monkeypatch, name: str) -> None:
    monkeypatch.setattr(publication, "load_public_strategies", lambda: {})
    with pytest.raises(ValueError, match="策略名称"):
        publication.submit_public_strategy(name, _config(), "builder")


def test_submit_public_strategy_rejects_existing_name(monkeypatch) -> None:
    monkeypatch.setattr(publication, "load_public_strategies", lambda: {"共享趋势": {}})
    with pytest.raises(ValueError, match="已存在"):
        publication.submit_public_strategy("共享趋势", _config(), "builder")


def test_load_missing_remote_json_returns_empty(monkeypatch) -> None:
    from urllib.error import HTTPError

    def fail(*_args, **_kwargs):
        raise HTTPError("https://example.invalid/file.json", 404, "missing", {}, None)

    monkeypatch.setattr(publication.urllib.request, "urlopen", fail)
    assert publication._read_remote_json("missing.json") == {}

