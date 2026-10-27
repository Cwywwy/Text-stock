# -*- coding: utf-8 -*-
"""LLM 客户端 — 可插拔的 OpenAI 兼容客户端，配置不齐全时优雅降级为 mock 模式。

设计：
- 配置来源：环境变量 / 项目根 .env（LLM_API_KEY / LLM_BASE_URL / LLM_MODEL）
- 配置不齐全或 openai 包缺失时自动进入 mock 模式（返回规则化模板文本）
- 统一 chat() 接口，供 analyzer 使用
"""
from __future__ import annotations

from typing import Optional

from stock_plan.llm.config import get_llm_config


class LLMClient:
    """OpenAI 兼容的 LLM 客户端（可插拔）。

    参数：
        api_key:  API Key。默认取 LLM_API_KEY（.env/环境变量）或 OPENAI_API_KEY。
        base_url: 兼容端点。默认取 LLM_BASE_URL（如 https://open.bigmodel.cn/api/paas/v4）。
        model:    模型名。默认取 LLM_MODEL（如 glm-4-flash）。
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 30.0,
        max_retries: int = 1,
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
        # 最近一次调用失败的原因（供上层把"为什么降级"显示给用户，而不是静默）
        self.last_error: str = ""
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

    @property
    def status_text(self) -> str:
        """连接状态描述（UI 展示用）。"""
        if self.mock:
            return "离线规则模式（LLM 配置不齐全，请检查项目根 .env）"
        return f"已连接：{self.model} @ {self.base_url}"

    def chat(self, system: str, user: str, temperature: float = 0.3) -> str:
        """发送对话请求，返回回复文本。mock 模式下返回降级提示。

        注意：调用失败时**不抛异常**（UI 需要继续渲染），但会把原因记到
        `self.last_error`，上层可据此告诉用户"这次是网络/额度问题，不是策略问题"。
        之前这里静默吞掉异常，导致页面只显示一句"解析失败"，排查全靠猜。
        """
        if self.mock:
            self.last_error = self.last_error or "LLM 未配置（.env 缺 LLM_API_KEY / LLM_BASE_URL）"
            return self._mock_reply(system, user)
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                timeout=self.timeout,
            )
            self.last_error = ""
            return resp.choices[0].message.content or ""
        except Exception as e:  # 网络/额度等异常时降级
            self.last_error = f"{type(e).__name__}: {e}"
            return self._mock_reply(system, user, error=str(e))

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


def get_client() -> LLMClient:
    """获取全局 LLM 客户端（惰性单例）。"""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


_client: Optional[LLMClient] = None
