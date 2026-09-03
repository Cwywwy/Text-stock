# -*- coding: utf-8 -*-
"""LLM 配置加载 — 从项目根 .env 读取 LLM 配置（环境变量优先）。

配置项（OpenAI 兼容格式，换服务商只需改 .env 三行）：
    LLM_BASE_URL  如 https://open.bigmodel.cn/api/paas/v4（智谱）
                  或 https://api.openai.com/v1 / 金链专属地址
    LLM_MODEL     如 glm-4-flash / gpt-4o-mini / deepseek-chat
    LLM_API_KEY   服务商控制台获取
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# src/stock_plan/llm/config.py → 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_PATH = PROJECT_ROOT / ".env"


def load_env_file(path: str | Path | None = None) -> dict[str, str]:
    """解析 .env 文件（KEY=VALUE 格式），已存在的环境变量不覆盖。"""
    path = Path(path or ENV_PATH)
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        if key not in os.environ:
            os.environ[key] = value
        loaded[key] = value
    return loaded


@dataclass
class LLMConfig:
    """LLM 连接配置。"""

    api_key: str
    base_url: str
    model: str

    @property
    def ready(self) -> bool:
        """三项齐全才能真实调用。"""
        return bool(self.api_key and self.base_url and self.model)


def get_llm_config() -> LLMConfig:
    """读取 LLM 配置：环境变量优先，其次 Streamlit Cloud Secrets，最后项目根 .env。"""
    load_env_file()
    # 云端部署（Streamlit Community Cloud）：从 st.secrets 读取
    try:
        import streamlit as st

        for key in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
            if key in st.secrets and not os.getenv(key):
                os.environ[key] = str(st.secrets[key])
    except Exception:
        pass
    return LLMConfig(
        api_key=os.getenv("LLM_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip(),
        base_url=os.getenv("LLM_BASE_URL", "").strip(),
        model=os.getenv("LLM_MODEL", "").strip(),
    )
