"""LLM 客户端 — 可插拔的 OpenAI 兼容客户端，配置不齐全时优雅降级为 mock 模式。

文件：src/stock_plan/llm/client.py
作者：Cwywwy
用途：全站唯一的 LLM 出口。负责人人都要用的四件事：
      1. 配置读取与 mock 降级（.env / 环境变量 / st.secrets）
      2. 并发闸门：全局同时最多 N 个真实请求（默认 2），避免同一 key 并发 429
      3. 结果缓存：同任务同输入在 TTL 内直接返回，不重复付费
      4. 错误归因到「这一次调用」（threading.local），不再写全局共享字段
所属模块：llm（基础设施层）

设计要点（对应 .github/plans/2026-10-28-2 的 Phase 0）：
- ``chat_result()`` 返回 ``ChatResult``，把「文本 / 是否失败 / 是否缓存 / 用量 / 耗时」
  一次性交给调用方；页面据此区分「模型胡说」与「这次没连上」。
- ``chat()`` 保留返回 str 的旧接口（几十处调用方不用改），内部走 ``chat_result()``。
- 失败不再静默：``ChatResult.error`` 有值，``text`` 是会明确写着失败原因的降级文本。
- ``last_error`` 改为 ``threading.local`` 支撑的属性：多线程各自记录，互不串台。

修订记录：
2026-10-28 Phase 0：新增 ChatResult / chat_result() / 并发闸门 / 退避重试 / 落盘缓存 /
           last_error 线程隔离；chat() 降为兼容外壳。
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from stock_plan.llm import cache as llm_cache
from stock_plan.llm.config import get_llm_config

# ---------------------------------------------------------------------------
# 并发与重试参数
# ---------------------------------------------------------------------------

#: 全局同时进行的真实调用上限（同一 key 并发太高会被服务商 429）
DEFAULT_MAX_CONCURRENCY = 2

#: 首次 + 1 次退避重试
MAX_ATTEMPTS = 2

#: 退避基数（秒）：第 1 次重试前等 0.5s
BACKOFF_BASE_SECONDS = 0.5


def _default_concurrency() -> int:
    """读取并发上限，环境变量 ``LLM_MAX_CONCURRENCY`` 可覆盖。"""
    raw = os.getenv("LLM_MAX_CONCURRENCY", "").strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return DEFAULT_MAX_CONCURRENCY


#: 进程级信号量：所有 LLMClient 实例共享（单例场景下等价，多实例也不会超发）
_SEMAPHORE = threading.BoundedSemaphore(_default_concurrency())


@dataclass(frozen=True)
class ChatResult:
    """一次 LLM 调用的完整结果。

    Attributes:
        text: 回复正文；失败时是明确写出原因的降级文本（不是空串）。
        error: 失败原因的原始异常文本；成功时为空串。
        cached: 是否命中本地缓存。
        mock: 是否走了 mock/降级分支（未配置或调用失败）。
        usage: token 用量（服务商返回时）或缓存元信息。
        elapsed_ms: 本次耗时（毫秒，含等待并发闸门的时间）。
        task: 任务名（缓存分桶与排查用）。
    """

    text: str
    error: str = ""
    cached: bool = False
    mock: bool = False
    usage: dict[str, Any] = field(default_factory=dict)
    elapsed_ms: int = 0
    task: str = ""

    @property
    def ok(self) -> bool:
        """是否拿到真实模型回复（缓存命中也算）。"""
        return not self.error and not self.mock

    @property
    def degraded(self) -> bool:
        """是否处于降级状态（未配置、失败或走了 mock）。"""
        return self.mock or bool(self.error)


class LLMClient:
    """OpenAI 兼容的 LLM 客户端（可插拔）。

    参数：
        api_key:  API Key。默认取 LLM_API_KEY（.env/环境变量）或 OPENAI_API_KEY。
        base_url: 兼容端点。默认取 LLM_BASE_URL（如 https://open.bigmodel.cn/api/paas/v4）。
        model:    模型名。默认取 LLM_MODEL（如 glm-4-flash）。
        timeout:  单次请求超时（秒）。默认 30。
        max_retries: SDK 层重试次数；业务层另有 1 次退避重试（见 ``chat_result``）。
        max_concurrency: 自定义并发上限；None 表示用全局信号量。
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 30.0,
        max_retries: int = 1,
        max_concurrency: Optional[int] = None,
    ):
        cfg = get_llm_config()
        self.api_key = api_key or cfg.api_key
        self.base_url = base_url or cfg.base_url or "https://api.openai.com/v1"
        self.model = model or cfg.model or "gpt-4o-mini"
        # 必须显式给超时：openai SDK 的默认超时是 600 秒且带自动重试，
        # 网络不通时页面会卡十几分钟（Streamlit 里表现为"一直转圈"），
        # 用户以为程序死了。这里压到 30 秒、只重试 1 次。
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        # 线程本地存储：每个线程只看到自己那次调用的失败原因，不会互相串台
        self._local = threading.local()
        # 自定义并发上限时另建信号量；否则共用进程级信号量
        self._semaphore = (
            _SEMAPHORE
            if max_concurrency is None
            else threading.BoundedSemaphore(max(1, int(max_concurrency)))
        )
        # 三项配置齐全才尝试真实调用，否则 mock
        self.mock = not (self.api_key and (base_url or cfg.base_url))
        self._client = None
        if not self.mock:
            try:
                from openai import OpenAI

                self._client = OpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                    timeout=self.timeout,
                    max_retries=self.max_retries,
                )
            except Exception as exc:
                self.mock = True
                self.last_error = f"{type(exc).__name__}: {exc}"

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    @property
    def last_error(self) -> str:
        """当前线程最近一次调用失败的原因（成功调用会清空）。"""
        return getattr(self._local, "error", "")

    @last_error.setter
    def last_error(self, value: str) -> None:
        self._local.error = value or ""

    @property
    def status_text(self) -> str:
        """连接状态描述（UI 展示用）。"""
        if self.mock:
            return "离线规则模式（LLM 配置不齐全，请检查项目根 .env）"
        return f"已连接：{self.model} @ {self.base_url}"

    # ------------------------------------------------------------------
    # 调用
    # ------------------------------------------------------------------
    def chat_result(
        self,
        system: str,
        user: str,
        *,
        task: str = "chat",
        temperature: float = 0.3,
        use_cache: bool = True,
        max_tokens: int | None = None,
    ) -> ChatResult:
        """发送一次对话请求，返回结构化结果（永不抛异常）。

        流程：mock 检查 → 缓存查 → 并发闸门内真实调用（可退避重试 1 次）→ 写缓存。
        """
        started = time.monotonic()

        def _elapsed() -> int:
            return int((time.monotonic() - started) * 1000)

        if self.mock:
            reason = self.last_error or "LLM 未配置（.env 缺 LLM_API_KEY / LLM_BASE_URL）"
            self.last_error = reason
            return ChatResult(
                text=self._mock_reply(system, user),
                error=reason,
                mock=True,
                elapsed_ms=_elapsed(),
                task=task,
            )

        payload = _cache_payload(system, user, temperature, max_tokens)
        if use_cache:
            entry = llm_cache.load(task, payload)
            if entry is not None:
                self.last_error = ""
                return ChatResult(
                    text=entry.text,
                    cached=True,
                    usage={
                        "cached": True,
                        "cache_age_minutes": round(entry.age_minutes, 1),
                    },
                    elapsed_ms=_elapsed(),
                    task=task,
                )

        error = ""
        usage: dict[str, Any] = {}
        text = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                with self._semaphore:
                    resp = self._client.chat.completions.create(
                        model=self.model,
                        messages=[
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        temperature=temperature,
                        timeout=self.timeout,
                        **({"max_tokens": int(max_tokens)} if max_tokens else {}),
                    )
                text = resp.choices[0].message.content or ""
                usage = _extract_usage(resp)
                error = ""
                break
            except Exception as exc:  # 网络/额度等异常
                error = f"{type(exc).__name__}: {exc}"
                if attempt < MAX_ATTEMPTS and _retryable(exc):
                    _backoff(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
                    continue
                text = self._mock_reply(system, user, error=error)
                break

        self.last_error = error
        if not error and use_cache:
            llm_cache.store(
                task,
                payload,
                text=text,
                meta={"model": self.model, "usage": usage},
            )
        return ChatResult(
            text=text,
            error=error,
            mock=bool(error),
            usage=usage,
            elapsed_ms=_elapsed(),
            task=task,
        )

    def chat(
        self,
        system: str,
        user: str,
        temperature: float = 0.3,
        *,
        task: str = "chat",
        use_cache: bool = True,
        max_tokens: int | None = None,
    ) -> str:
        """兼容旧调用方：返回回复文本。等价于 ``chat_result(...).text``。

        调用失败时**不抛异常**（页面要能继续渲染），但原因同时记在
        ``last_error``（线程本地）与 ``ChatResult.error`` 上，上层可据此告诉
        用户"这次是网络/额度问题，不是策略问题"。
        """
        return self.chat_result(
            system,
            user,
            task=task,
            temperature=temperature,
            use_cache=use_cache,
            max_tokens=max_tokens,
        ).text

    # ------------------------------------------------------------------
    # 降级文本
    # ------------------------------------------------------------------
    @staticmethod
    def _mock_reply(system: str, user: str, error: str = "") -> str:
        """mock 模式：不调用外部 API，返回规则化模板文本。"""
        head = "【LLM 离线规则模式（.env 配置不齐全或调用失败）】"
        if error:
            head += f"\n（调用失败：{error}）"
        return (
            f"{head}\n"
            "系统提示：\n"
            f"{system[:200]}\n"
            "用户输入：\n"
            f"{user[:500]}\n"
            "提示：在项目根 .env 中填写 LLM_BASE_URL / LLM_MODEL / LLM_API_KEY 后重启应用，"
            "即可启用真实 LLM 分析。"
        )


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _cache_payload(
    system: str, user: str, temperature: float, max_tokens: int | None
) -> str:
    """缓存键的输入指纹：同一 prompt + 同一参数才算同一次请求。"""
    return f"{temperature}\x00{max_tokens}\x00{system}\x00{user}"


def _extract_usage(resp: Any) -> dict[str, Any]:
    """尽量从服务商响应里取 token 用量（字段缺失时返回空字典）。"""
    raw = getattr(resp, "usage", None)
    if raw is None:
        return {}
    out: dict[str, Any] = {}
    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = getattr(raw, name, None)
        if isinstance(value, int):
            out[name] = value
    return out


def _backoff(seconds: float) -> None:
    """退避等待（独立函数便于测试替换，避免 monkeypatch 全局 time.sleep）。"""
    time.sleep(seconds)


def _retryable(exc: Exception) -> bool:
    """是否值得退避重试：限流、超时、连接失败、5xx 才重试；4xx 参数错误不重试。"""
    name = type(exc).__name__.lower()
    if any(token in name for token in ("ratelimit", "timeout", "connection", "internalserver")):
        return True
    status = getattr(exc, "status_code", None)
    try:
        code = int(status)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return code == 429 or 500 <= code < 600


def get_client() -> LLMClient:
    """获取全局 LLM 客户端（惰性单例）。"""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


_client: Optional[LLMClient] = None
