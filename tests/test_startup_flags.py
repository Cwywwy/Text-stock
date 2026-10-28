# -*- coding: utf-8 -*-
"""启动开关（`stock_plan/ui/startup_flags.py`）单元测试。

覆盖：三种部署形态的默认行为、环境变量真值/假值/写错值、本地发布开关对
`is_cloud()` 的依赖（用 monkeypatch 注入，不碰真实环境），以及 `describe()` 摘要。

Revision History
----------------
2026-10-29  Cwywwy  首次创建：国内服务器部署阶段 2（启动副作用收敛）。
"""

from __future__ import annotations

import pytest

from stock_plan.data import snapshot
from stock_plan.ui import startup_flags


class TestServiceMode:
    """STOCK_PLAN_SERVICE 判定：只认真值，写错的值按「不是服务器」处理。"""

    @pytest.mark.parametrize("env", [{}, {"STOCK_PLAN_SERVICE": ""}, {"STOCK_PLAN_SERVICE": "0"}])
    def test_default_is_not_service(self, env):
        assert startup_flags.is_service_mode(env) is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " On ", "1 "])
    def test_truthy_values(self, value):
        assert startup_flags.is_service_mode({"STOCK_PLAN_SERVICE": value}) is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "staging", "2"])
    def test_falsy_or_bogus_values(self, value):
        assert startup_flags.is_service_mode({"STOCK_PLAN_SERVICE": value}) is False

    def test_missing_key(self):
        assert startup_flags.is_service_mode({}) is False


class TestRemoteStrategySync:
    """service 模式默认关（境外 raw 域名常超时），可用 STOCK_PLAN_REMOTE_SYNC 覆盖。"""

    def test_local_keeps_historical_behaviour(self):
        assert startup_flags.remote_strategy_sync_enabled({}) is True

    def test_service_mode_skips_by_default(self):
        assert startup_flags.remote_strategy_sync_enabled({"STOCK_PLAN_SERVICE": "1"}) is False

    def test_service_mode_can_opt_in(self):
        env = {"STOCK_PLAN_SERVICE": "1", "STOCK_PLAN_REMOTE_SYNC": "1"}
        assert startup_flags.remote_strategy_sync_enabled(env) is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", " 0 "])
    def test_explicit_off_works_locally_too(self, value):
        assert startup_flags.remote_strategy_sync_enabled({"STOCK_PLAN_REMOTE_SYNC": value}) is False

    def test_unknown_override_value_is_treated_as_on(self):
        assert startup_flags.remote_strategy_sync_enabled({"STOCK_PLAN_REMOTE_SYNC": "maybe"}) is True


class TestLocalPublish:
    """本机自动发布：本机开、云端关、服务器关。cloud 参数用于注入，避免依赖真实环境。"""

    def test_local_opens_publish(self):
        assert startup_flags.local_publish_enabled({}, cloud=False) is True

    def test_cloud_closes_publish(self):
        assert startup_flags.local_publish_enabled({}, cloud=True) is False

    def test_service_mode_closes_even_when_not_cloud(self):
        env = {"STOCK_PLAN_SERVICE": "1"}
        assert startup_flags.local_publish_enabled(env, cloud=False) is False

    def test_service_mode_and_cloud_both_closed(self):
        env = {"STOCK_PLAN_SERVICE": "1"}
        assert startup_flags.local_publish_enabled(env, cloud=True) is False

    def test_falls_back_to_is_cloud_when_not_injected(self, monkeypatch):
        monkeypatch.setattr(snapshot, "is_cloud", lambda: True)
        assert startup_flags.local_publish_enabled({}) is False

    def test_default_env_is_read_when_env_not_given(self, monkeypatch):
        monkeypatch.setenv("STOCK_PLAN_SERVICE", "1")
        assert startup_flags.local_publish_enabled(None, cloud=False) is False


class TestDescribe:
    """describe() 只用于日志，但内容必须能区分形态，且不能抛异常。"""

    def test_service_mode_summary(self, monkeypatch):
        monkeypatch.setattr(snapshot, "is_cloud", lambda: False)
        summary = startup_flags.describe({"STOCK_PLAN_SERVICE": "1"})
        assert "service_mode=True" in summary
        assert "cloud=False" in summary
        assert "remote_strategy_sync=False" in summary
        assert "local_publish=False" in summary

    def test_local_summary(self, monkeypatch):
        monkeypatch.setattr(snapshot, "is_cloud", lambda: False)
        monkeypatch.delenv("STOCK_PLAN_SERVICE", raising=False)
        monkeypatch.delenv("STOCK_PLAN_REMOTE_SYNC", raising=False)
        summary = startup_flags.describe(None)
        assert "service_mode=False" in summary
        assert "remote_strategy_sync=True" in summary
        assert "local_publish=True" in summary
