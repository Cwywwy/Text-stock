# -*- coding: utf-8 -*-
"""国内服务器部署物料（`deploy/`）的静态校验测试。

这些文件不在 Windows 上执行，所以测试用「读文件 + 解析」的方式守住最容易犯的错：
- systemd unit 少 section / 少关键 Environment / ExecStart 指向错路径 / 忘了监听 0.0.0.0
- 定时器忘了 Persistent 或 WantedBy
- 部署脚本里写了 CRLF（copy 到 Linux 上会 `bad interpreter`）
- secrets 模板写进真实 token，或 TOML 语法坏掉
- `pull_snapshot.py` 的冷却、锁、干跑逻辑（用 monkeypatch 换掉路径，不联网）

Revision History
----------------
2026-10-29  Cwywwy  首次创建：国内服务器部署阶段 3（部署物料）。
"""

from __future__ import annotations

import importlib.util
import os
import time
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"

UNIT_FILES = ("stock-plan.service", "stock-plan-snapshot.service", "stock-plan-snapshot.timer")


def parse_unit(path: Path) -> dict[str, list[str]]:
    """把 systemd unit 解析成 {section: [行]}，够本测试用（不处理续行合并）。"""
    sections: dict[str, list[str]] = {}
    current = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return sections


def unit_values(unit: dict[str, list[str]], section: str, key: str) -> list[str]:
    """取某个 section 下某个 key 的所有取值（systemd 允许同一 key 出现多次）。"""
    prefix = f"{key}="
    return [line[len(prefix):] for line in unit.get(section, []) if line.startswith(prefix)]


def load_pull_module():
    """按文件路径加载 deploy/pull_snapshot.py（它不在 stock_plan 包里）。"""
    path = DEPLOY / "pull_snapshot.py"
    spec = importlib.util.spec_from_file_location("deploy_pull_snapshot", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def pull(tmp_path, monkeypatch):
    """加载 pull_snapshot 模块，并把状态/锁/日志路径重定向到临时目录。"""
    module = load_pull_module()
    monkeypatch.setattr(module, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(module, "LOCK_PATH", tmp_path / "pull.lock")
    monkeypatch.setattr(module, "LOG_PATH", tmp_path / "logs" / "pull.log")
    monkeypatch.setattr(module, "missing_extras", lambda processed_dir=None: [])
    return module


class TestFilesExist:
    @pytest.mark.parametrize("name", ("bootstrap.sh", "pull_snapshot.py", "README.md",
                                      "secrets.toml.example", *UNIT_FILES))
    def test_required_files(self, name):
        assert (DEPLOY / name).is_file(), f"deploy/{name} 缺失"

    @pytest.mark.parametrize("path", sorted(DEPLOY.glob("*")))
    def test_no_crlf(self, path):
        """Linux 上 CRLF 会让 `#!/usr/bin/env bash` 变成 `bash\\r`，直接跑步起来。"""
        if not path.is_file():
            return
        assert b"\r\n" not in path.read_bytes(), f"{path.name} 含 CRLF，部署到 Linux 前必须转 LF"


class TestServiceUnit:
    def test_sections(self):
        unit = parse_unit(DEPLOY / "stock-plan.service")
        assert {"Unit", "Service", "Install"} <= set(unit)

    def test_runs_as_service_user(self):
        unit = parse_unit(DEPLOY / "stock-plan.service")
        assert unit_values(unit, "Service", "User") == ["stockplan"]
        assert unit_values(unit, "Service", "WorkingDirectory") == ["/opt/stock-plan"]

    def test_pythonpath_points_at_src(self):
        unit = parse_unit(DEPLOY / "stock-plan.service")
        envs = " ".join(unit_values(unit, "Service", "Environment"))
        assert "PYTHONPATH=/opt/stock-plan/src" in envs
        # 关键约束：绝不能 pip install 本包，否则 PROJECT_ROOT 会算到 site-packages。
        # 只看生效行（注释里出现这个词是说明文字，不算违规）。
        active = [
            line for line in (DEPLOY / "stock-plan.service").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        assert not any("pip install" in line for line in active)

    def test_service_mode_switches(self):
        unit = parse_unit(DEPLOY / "stock-plan.service")
        envs = " ".join(unit_values(unit, "Service", "Environment"))
        assert "STOCK_PLAN_SERVICE=1" in envs
        assert "STOCK_PLAN_CLOUD=1" in envs
        assert "STOCK_PLAN_REMOTE_SYNC=0" in envs

    def test_listens_on_all_interfaces(self):
        unit = parse_unit(DEPLOY / "stock-plan.service")
        exec_start = unit_values(unit, "Service", "ExecStart")
        assert len(exec_start) == 1, "ExecStart 只允许一条（多行续写容易配错）"
        command = exec_start[0]
        assert command.startswith("/opt/stock-plan/.venv/bin/python -m streamlit run")
        assert "src/stock_plan/ui/app.py" in command
        assert "--server.address 0.0.0.0" in command
        assert "--server.port 8501" in command
        assert "--server.headless true" in command

    def test_restart_policy(self):
        unit = parse_unit(DEPLOY / "stock-plan.service")
        assert unit_values(unit, "Service", "Restart") == ["always"]
        assert unit_values(unit, "Install", "WantedBy") == ["multi-user.target"]


class TestSnapshotUnits:
    def test_oneshot_pulls_snapshot(self):
        unit = parse_unit(DEPLOY / "stock-plan-snapshot.service")
        assert unit_values(unit, "Service", "Type") == ["oneshot"]
        exec_start = " ".join(unit_values(unit, "Service", "ExecStart"))
        assert "deploy/pull_snapshot.py" in exec_start
        assert "--restart-service" in exec_start
        assert "--max-age-days 7" in exec_start

    def test_snapshot_runs_after_app(self):
        unit = parse_unit(DEPLOY / "stock-plan-snapshot.service")
        assert "After=stock-plan.service" in unit["Unit"]

    def test_timer_schedule(self):
        unit = parse_unit(DEPLOY / "stock-plan-snapshot.timer")
        assert unit_values(unit, "Timer", "OnCalendar") == ["*-*-* 17:10:00"]
        assert unit_values(unit, "Timer", "Persistent") == ["true"]
        assert unit_values(unit, "Timer", "OnBootSec") == ["5min"]
        assert unit_values(unit, "Install", "WantedBy") == ["timers.target"]


class TestBootstrapScript:
    @pytest.fixture()
    def script(self) -> str:
        return (DEPLOY / "bootstrap.sh").read_text(encoding="utf-8")

    def test_strict_mode(self, script):
        assert script.startswith("#!/usr/bin/env bash")
        assert "set -euo pipefail" in script

    def test_installs_every_unit(self, script):
        for name in UNIT_FILES:
            assert name in script, f"bootstrap.sh 没安装 {name}"

    def test_python_version_and_timezone(self, script):
        assert "uv venv --python 3.13" in script
        assert "timedatectl set-timezone Asia/Shanghai" in script

    def test_pulls_first_snapshot(self, script):
        assert "deploy/pull_snapshot.py" in script

    def test_idempotent_user_creation(self, script):
        # 重复执行不能因为用户已存在而失败
        assert "if ! id -u" in script


class TestSecretsExample:
    def test_toml_is_valid(self):
        data = tomllib.loads((DEPLOY / "secrets.toml.example").read_text(encoding="utf-8"))
        assert data["LLM_BASE_URL"].startswith("https://")
        assert data["LLM_MODEL"]
        assert data["STOCK_PLAN_SERVICE"] == "1"
        assert data["STOCK_PLAN_REMOTE_SYNC"] == "0"

    def test_no_real_token_committed(self):
        text = (DEPLOY / "secrets.toml.example").read_text(encoding="utf-8")
        assert "LLM_API_KEY" in text
        # 模板里只能是占位说明；真 key 通常是 32 位以上十六进制/字母数字串
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("LLM_API_KEY"):
                value = stripped.split("=", 1)[1].strip().strip('"')
                assert value and value != "sk-", "模板里不能填真实 key"
                assert len(value) < 40, "疑似把真实 key 写进模板了"


class TestPullSnapshotModule:
    def test_compiles(self):
        source = (DEPLOY / "pull_snapshot.py").read_text(encoding="utf-8")
        compile(source, "pull_snapshot.py", "exec")

    def test_cli_defaults(self, pull):
        args = pull.build_parser().parse_args([])
        assert args.max_age_days == 7
        assert args.min_interval_hours == 6
        assert args.retries >= 1
        assert args.service_name == "stock-plan"
        assert args.force is False

    def test_dry_run_prints_bases_without_downloading(self, pull, capsys):
        assert pull.main(["--dry-run"]) == 0
        out = capsys.readouterr().out
        assert "github.com" in out
        assert "只检查，不下载" in out

    def test_fresh_state_skips_before_network(self, pull, capsys):
        pull.write_state({"pulled_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        assert pull.main([]) == 0
        assert "跳过" in capsys.readouterr().out

    def test_force_bypasses_interval(self, pull, monkeypatch):
        pull.write_state({"pulled_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        calls: list[tuple] = []

        def fake_restore(bases, bars_dir, tmp_dir, **kwargs):
            calls.append((bases, bars_dir, tmp_dir, kwargs))
            return {"bars_count": 1800, "created_at": "2026-10-29 16:30:00",
                    "restored_at": "2026-10-29 17:11:00"}

        monkeypatch.setattr(pull, "restore_with_fallback", fake_restore)
        assert pull.main(["--force"]) == 0
        assert len(calls) == 1
        assert calls[0][3]["max_age_days"] == 7
        assert calls[0][3]["part_workers"] == 4
        state = pull.read_state()
        assert state["bars_count"] == 1800

    def test_retries_then_fails(self, pull, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("所有快照地址均恢复失败")

        monkeypatch.setattr(pull, "restore_with_fallback", boom)
        monkeypatch.setattr(pull.time, "sleep", lambda _seconds: None)
        assert pull.main(["--force", "--retries", "2", "--retry-wait", "0"]) == 1

    def test_missing_extras_triggers_pull_even_when_recent(self, pull, monkeypatch):
        pull.write_state({"pulled_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        monkeypatch.setattr(pull, "missing_extras", lambda processed_dir=None: ["classify"])
        called: list[int] = []
        monkeypatch.setattr(pull, "restore_with_fallback",
                            lambda *a, **k: called.append(1) or {"bars_count": 1800})
        assert pull.main([]) == 0
        assert called == [1]

    def test_lock_is_exclusive(self, pull):
        assert pull.acquire_lock() is True
        assert pull.acquire_lock() is False
        pull.release_lock()
        assert pull.acquire_lock() is True
        pull.release_lock()

    def test_stale_lock_is_taken_over(self, pull):
        pull.LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        pull.LOCK_PATH.write_text("999", encoding="utf-8")
        old = time.time() - 3 * 3600
        os.utime(pull.LOCK_PATH, (old, old))
        assert pull.acquire_lock() is True
        pull.release_lock()

    def test_state_age(self, pull):
        assert pull.last_pull_age_hours({}) is None
        assert pull.last_pull_age_hours({"pulled_at": "not-a-date"}) is None
        age = pull.last_pull_age_hours({"pulled_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        assert age is not None and 0 <= age < 0.1

    def test_restart_failure_is_only_a_warning(self, pull, monkeypatch, capsys):
        def boom(*a, **k):
            raise OSError("systemctl not found")

        monkeypatch.setattr(pull.subprocess, "run", boom)
        pull.restart_service("stock-plan")
        out = capsys.readouterr().out
        assert "警告" in out and "数据已更新" in out

    def test_state_roundtrip(self, pull):
        pull.write_state({"bars_count": 42})
        assert pull.read_state()["bars_count"] == 42


class TestAppIntegration:
    def test_entry_gates_side_effects_with_flags(self):
        source = (ROOT / "src" / "stock_plan" / "ui" / "app.py").read_text(encoding="utf-8")
        assert "local_publish_enabled()" in source
        assert "remote_strategy_sync_enabled()" in source
        # 旧写法必须消失：`if not is_cloud() and ...` 已由 local_publish_enabled() 取代
        assert "if not is_cloud() and" not in source

    def test_flags_module_is_streamlit_free(self):
        source = (ROOT / "src" / "stock_plan" / "ui" / "startup_flags.py").read_text(encoding="utf-8")
        assert "\nimport streamlit" not in source


class TestDocs:
    @pytest.fixture()
    def readme(self) -> str:
        return (DEPLOY / "README.md").read_text(encoding="utf-8")

    def test_covers_required_topics(self, readme):
        for keyword in ("8501", "安全组", "备案", "pull_snapshot.py", "sudoers",
                        "bootstrap.sh", "secrets.toml", "bundle"):
            assert keyword in readme, f"部署手册缺少「{keyword}」相关说明"

    def test_points_at_plan_file(self, readme):
        assert ".github/plans/2026-10-29-1-new-deploy-on-china-cloud-server.md" in readme
