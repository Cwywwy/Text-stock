# -*- coding: utf-8 -*-
"""数据更新页「维护者 / 普通用户」两套排版的回归测试。

覆盖两件事：
1. maintainer_mode() 的判定优先级（显式环境变量 > 是否云端部署）；
2. 两种排版下按钮的出场顺序 —— 云端把「增量更新」压到快照按钮之后并塞进折叠区，
   本机则相反。顺序用 AppTest 读页面元素锁住，防止以后调版式时又把大按钮顶到最前。
"""

from __future__ import annotations

import pytest

from stock_plan.ui.views import update_center as uc


@pytest.fixture()
def cloud(monkeypatch):
    """把 is_cloud() 钉成指定值（maintainer_mode 是函数内 import，patch 模块属性即可）。"""
    def _set(value: bool) -> None:
        monkeypatch.setattr("stock_plan.data.snapshot.is_cloud", lambda: value)

    return _set


def test_explicit_env_wins_over_cloud(monkeypatch, cloud):
    cloud(True)
    monkeypatch.setenv(uc.MAINTAINER_ENV, "1")
    assert uc.maintainer_mode() is True

    monkeypatch.setenv(uc.MAINTAINER_ENV, "0")
    assert uc.maintainer_mode() is False

    cloud(False)
    monkeypatch.setenv(uc.MAINTAINER_ENV, "off")
    assert uc.maintainer_mode() is False


@pytest.mark.parametrize("value", ["1", "true", "YES", "on", " 1 "])
def test_truthy_env_values(monkeypatch, cloud, value):
    cloud(True)
    monkeypatch.setenv(uc.MAINTAINER_ENV, value)
    assert uc.maintainer_mode() is True


@pytest.mark.parametrize("value", ["0", "false", "No", "off", " 0 "])
def test_falsy_env_values(monkeypatch, cloud, value):
    cloud(False)
    monkeypatch.setenv(uc.MAINTAINER_ENV, value)
    assert uc.maintainer_mode() is False


def test_default_follows_cloud_flag(monkeypatch, cloud):
    monkeypatch.delenv(uc.MAINTAINER_ENV, raising=False)
    cloud(False)
    assert uc.maintainer_mode() is True
    cloud(True)
    assert uc.maintainer_mode() is False


def _run_page(tmp_path, *, maintainer: str) -> object:
    """在 AppTest 里跑 render()，把重活（状态扫描 / 云徽章 / 进度轮询 / 数据源抓取）替换掉。"""
    script = tmp_path / "app_under_test.py"
    script.write_text(
        "import os\n"
        f"os.environ['{uc.MAINTAINER_ENV}'] = {maintainer!r}\n"
        "import streamlit as st\n"
        "from stock_plan.ui.views import update_center as uc\n"
        "uc._data_status = lambda: None\n"
        "uc._cloud_badge = lambda: None\n"
        "uc._update_panel = lambda: None\n"
        "uc._extra_sources_panel = lambda: None\n"
        "uc.render()\n",
        encoding="utf-8",
    )
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(script), default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    return at


def test_visitor_layout_puts_snapshot_first(tmp_path):
    at = _run_page(tmp_path, maintainer="0")
    labels = [b.label for b in at.button]
    assert "⬇️ 同步到最新快照" in labels
    assert labels.index("⬇️ 同步到最新快照") < labels.index("🔄 立即增量更新")
    # 增量更新前面必须有一句「非管理者无需点击」的警示
    assert any("非管理者无需点击" in w.value for w in at.warning)


def test_maintainer_layout_puts_increment_first(tmp_path):
    at = _run_page(tmp_path, maintainer="1")
    labels = [b.label for b in at.button]
    assert labels.index("🔄 立即增量更新") < labels.index("⬇️ 立即从快照恢复")
    assert not any("非管理者无需点击" in w.value for w in at.warning)
