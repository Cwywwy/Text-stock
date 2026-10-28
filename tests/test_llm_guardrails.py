# -*- coding: utf-8 -*-
"""LLM 基础设施守卫（Phase 0）：预算裁剪、落盘缓存、并发闸门、错误隔离。

这些测试不联网、不烧额度：真实调用一律替换成假 inner client，
只验证「我们自己的四件事」是否成立：

- 预算：中文按 1 字 1 token 估、超限按优先级砍、砍了什么要说得出
- 缓存：同输入第二次不再调用、参数变了算不同请求、过期/损坏/禁用都不炸
- 并发：同一 key 同时最多 2 个真实请求（多出来的排队，不是并发打出去）
- 错误：两个线程同时失败，各自只看到自己的 `last_error`（旧的全局字段会串台）
"""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

from stock_plan.llm import budget, cache
from stock_plan.llm import client as client_module
from stock_plan.llm.budget import ELLIPSIS, Section, estimate_tokens, fit_sections, truncate_text
from stock_plan.llm.client import ChatResult, LLMClient, _retryable


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------
class _Resp:
    """伪造 OpenAI 响应对象。"""

    def __init__(self, content: str, prompt_tokens: int = 12, completion_tokens: int = 7):
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=content))]
        self.usage = SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )


class _Recorder:
    """记录 create 调用的假 inner client。"""

    def __init__(self, reply="模型回复", *, delay=0.0, failures=0, exc=None):
        self.reply = reply
        self.delay = delay
        self.remaining_failures = failures
        self.exc = exc
        self.calls: list[dict] = []
        self.active = 0
        self.peak = 0
        self._lock = threading.Lock()
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        with self._lock:
            self.calls.append(kwargs)
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.remaining_failures > 0:
                self.remaining_failures -= 1
                raise self.exc or TimeoutError("read timed out")
            return _Resp(self.reply)
        finally:
            with self._lock:
                self.active -= 1


def _make_client(recorder: _Recorder, **kwargs) -> LLMClient:
    """构造一个「看起来真实连接、实际打桩」的客户端。"""
    client = LLMClient(api_key="test-key", base_url="http://127.0.0.1:9/v1", model="test-model", **kwargs)
    assert client.mock is False, "构造参数应让客户端进入真实调用分支"
    client._client = recorder
    return client


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    """把缓存目录指到临时目录，并确保缓存开关是开的。"""
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "llm-cache")
    monkeypatch.delenv("LLM_CACHE_DISABLE", raising=False)
    monkeypatch.delenv("LLM_CACHE_TTL_HOURS", raising=False)
    return tmp_path / "llm-cache"


@pytest.fixture
def no_sleep(monkeypatch):
    """退避不真等，测试才快。"""
    monkeypatch.setattr(client_module, "_backoff", lambda seconds: None)


# ---------------------------------------------------------------------------
# 预算
# ---------------------------------------------------------------------------
def test_estimate_tokens_cjk_heavier_than_ascii() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("中" * 10) == 10
    assert estimate_tokens("a" * 40) == 10
    # 中英混排：10 个汉字 + 40 个字母 = 20
    assert estimate_tokens("中" * 10 + "a" * 40) == 20


def test_truncate_text_keeps_short_text_untouched() -> None:
    text = "短文本，不需要截断。"
    assert truncate_text(text, 100) == text


def test_truncate_text_respects_budget_and_marks_ellipsis() -> None:
    text = "这" * 200
    out = truncate_text(text, 20)
    assert out.endswith(ELLIPSIS)
    assert estimate_tokens(out) <= 20
    assert len(out) < len(text)


def test_truncate_text_zero_budget_returns_empty() -> None:
    assert truncate_text("任意文本", 0) == ""
    assert truncate_text("任意文本", -5) == ""


def test_fit_sections_drops_low_priority_first() -> None:
    sections = [
        Section("策略参数", "持有 15 天，止盈 4 倍 ATR。", priority=0),
        Section("历史成交量", "逐日成交明细。" * 500, priority=9),
    ]
    fitted = fit_sections(sections, max_tokens=60)
    assert "策略参数" in fitted.text
    assert "历史成交量" in fitted.dropped, "只剩 1% 预算时塞开头一段只会误导模型，应整段省略"
    assert fitted.lost is True
    assert "已省略" in fitted.loss_note()
    assert fitted.estimated_tokens <= 60


def test_fit_sections_truncates_oversized_section() -> None:
    sections = [Section("指标", "收益率 " * 400, priority=0)]
    fitted = fit_sections(sections, max_tokens=50)
    assert fitted.truncated == ("指标",)
    assert fitted.dropped == ()
    assert fitted.estimated_tokens <= 50
    assert "已截断" in fitted.loss_note()


def test_fit_sections_zero_budget_drops_everything() -> None:
    fitted = fit_sections([Section("A", "内容"), Section("B", "内容")], max_tokens=0)
    assert fitted.text == ""
    assert fitted.dropped == ("A", "B")


def test_fit_sections_keeps_input_order_in_output() -> None:
    sections = [
        Section("背景", "低优先级但先出现", priority=5),
        Section("任务", "高优先级", priority=0),
    ]
    fitted = fit_sections(sections, max_tokens=200)
    assert fitted.text.index("背景") < fitted.text.index("任务")


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------
def test_cache_roundtrip(isolated_cache) -> None:
    entry = cache.store("signal-explain", "payload-1", text="叙事正文", meta={"model": "m"})
    assert entry is not None
    hit = cache.load("signal-explain", "payload-1")
    assert hit is not None
    assert hit.text == "叙事正文"
    assert hit.meta["model"] == "m"
    assert hit.describe_age() == "刚刚"
    # 换成别的输入就是 miss
    assert cache.load("signal-explain", "payload-2") is None


def test_cache_expires_after_ttl(isolated_cache) -> None:
    cache.store("task", "p", text="旧结果")
    later = time.time() + (cache.ttl_hours() + 1) * 3600
    assert cache.load("task", "p", now=later) is None


def test_cache_corrupted_file_is_removed(isolated_cache) -> None:
    key = cache.make_key("task", "p")
    path = cache.cache_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{不是 JSON", encoding="utf-8")
    assert cache.load("task", "p") is None
    assert not path.exists(), "损坏文件应被删除，否则每次调用都要解析一遍"


def test_cache_disabled_by_env(isolated_cache, monkeypatch) -> None:
    monkeypatch.setenv("LLM_CACHE_DISABLE", "1")
    assert cache.store("task", "p", text="结果") is None
    assert cache.load("task", "p") is None


def test_cache_stats_counts_entries(isolated_cache) -> None:
    cache.store("t", "a", text="1")
    cache.store("t", "b", text="2")
    info = cache.stats()
    assert info["entries"] == 2
    assert info["bytes"] > 0
    assert info["enabled"] is True


# ---------------------------------------------------------------------------
# 客户端：缓存与降级
# ---------------------------------------------------------------------------
def test_chat_result_hits_cache_on_second_call(isolated_cache) -> None:
    recorder = _Recorder("第一版叙事")
    client = _make_client(recorder)

    first = client.chat_result("s", "u", task="explain")
    assert first.ok and first.cached is False and first.error == ""
    assert recorder.calls, "第一次应真的调用模型"

    second = client.chat_result("s", "u", task="explain")
    assert second.cached is True
    assert second.text == "第一版叙事"
    assert len(recorder.calls) == 1, "第二次必须命中缓存，不能再调一次"


def test_chat_result_different_temperature_is_different_request(isolated_cache) -> None:
    recorder = _Recorder("叙事")
    client = _make_client(recorder)
    client.chat_result("s", "u", task="t", temperature=0.2)
    client.chat_result("s", "u", task="t", temperature=0.9)
    assert len(recorder.calls) == 2


def test_chat_result_extracts_usage(isolated_cache) -> None:
    client = _make_client(_Recorder("ok"))
    result = client.chat_result("s", "u", task="t")
    assert result.usage["total_tokens"] == 19
    assert result.elapsed_ms >= 0


def test_mock_mode_reports_error_instead_of_silence(isolated_cache) -> None:
    # 坑：不能靠「传空 api_key」造 mock —— 构造函数会用 .env 里的配置兜底，
    # 结果真的打出去了（耗时 3.6s、消耗了一次额度）。这里显式把客户端置为 mock。
    client = _make_client(_Recorder("不会用到"))
    client.mock = True
    client._client = None
    result = client.chat_result("系统", "用户", use_cache=False)
    assert result.mock is True
    assert result.error, "未配置必须给出失败原因，不能静默"
    assert "离线规则模式" in result.text
    assert client.last_error == result.error


def test_chat_compat_returns_plain_text(isolated_cache) -> None:
    client = _make_client(_Recorder("纯文本回复"))
    assert client.chat("s", "u") == "纯文本回复"


# ---------------------------------------------------------------------------
# 客户端：重试与并发
# ---------------------------------------------------------------------------
def test_retryable_error_is_retried_once(isolated_cache, no_sleep) -> None:
    recorder = _Recorder("重试成功", failures=1, exc=TimeoutError("read timed out"))
    client = _make_client(recorder)
    result = client.chat_result("s", "u", task="t", use_cache=False)
    assert result.error == ""
    assert result.text == "重试成功"
    assert len(recorder.calls) == 2


def test_non_retryable_error_fails_fast(isolated_cache) -> None:
    recorder = _Recorder("不会被用到", failures=9, exc=ValueError("bad request"))
    client = _make_client(recorder)
    result = client.chat_result("s", "u", task="t", use_cache=False)
    assert "ValueError" in result.error
    assert result.mock is True
    assert len(recorder.calls) == 1, "参数错误不该重试，重试只会浪费时间与额度"


def test_retryable_classification() -> None:
    assert _retryable(TimeoutError("t")) is True
    assert _retryable(RuntimeError("boom")) is False
    rate = RuntimeError("rl")
    rate.status_code = 429  # type: ignore[attr-defined]
    assert _retryable(rate) is True
    bad = RuntimeError("bad")
    bad.status_code = 400  # type: ignore[attr-defined]
    assert _retryable(bad) is False


def test_concurrency_is_capped(isolated_cache) -> None:
    recorder = _Recorder("ok", delay=0.04)
    client = _make_client(recorder, max_concurrency=2)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            client.chat_result("s", "u", task="t", use_cache=False)
        except BaseException as exc:  # pragma: no cover - 只为收集异常
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    assert len(recorder.calls) == 6
    assert recorder.peak <= 2, f"并发峰值 {recorder.peak} 超过上限 2"


def test_last_error_is_thread_local(isolated_cache, no_sleep) -> None:
    """两个线程同时失败，各自只看到自己的原因（旧的全局字段会串台）。"""

    class _PerThreadFail:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kwargs):
            user = kwargs["messages"][1]["content"]
            raise RuntimeError(f"boom-{user}")

    client = _make_client(_PerThreadFail())  # type: ignore[arg-type]
    barrier = threading.Barrier(2)
    seen: dict[str, str] = {}
    lock = threading.Lock()

    def worker(user: str) -> None:
        barrier.wait(timeout=5)
        client.chat_result("s", user, task="t", use_cache=False)
        with lock:
            seen[user] = client.last_error

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("A", "B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert "boom-A" in seen["A"]
    assert "boom-B" in seen["B"]
    assert "boom-B" not in seen["A"], "线程 A 看到了线程 B 的错误 = 串台"


def test_chat_result_is_frozen_and_flags() -> None:
    result = ChatResult(text="t")
    assert result.ok is True and result.degraded is False
    with pytest.raises(Exception):
        result.text = "改不了"  # type: ignore[misc]
