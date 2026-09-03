# 盘前选股系统

基于 A 股技术面 + 基本面选股，提供盘前信号（目标买入/卖出价、止损、持仓周期）、回测引擎与模拟交易，通过本地 Streamlit Web 仪表板查看。

> 详细设计见 [plan.md](./plan.md)。

## 环境要求

- Windows
- Python 3.13（由 uv 自动管理）

## 安装

```powershell
# 1. 安装 uv（若未安装）
irm https://astral.sh/uv/install.ps1 | iex

# 2. 进入项目目录并同步依赖
cd "d:\Vscode\stock plan"
uv sync
```

## 启动

```powershell
uv run streamlit run src/stock_plan/ui/app.py
```

浏览器打开 http://localhost:8501 即可使用。

## 项目结构

```
stock plan/
├── pyproject.toml          # uv 项目配置与依赖
├── plan.md                 # 项目计划书（含 Phase Tasklist）
├── data/
│   ├── raw/                # 原始数据（AKShare 落盘）
│   ├── processed/          # 清洗后数据（Parquet）
│   └── db/                 # SQLite（策略/回测/模拟交易记录）
├── src/stock_plan/
│   ├── data/               # 数据获取与缓存
│   ├── factors/            # 因子计算
│   ├── strategy/           # 策略引擎
│   ├── backtest/           # 回测引擎
│   ├── signal/             # 盘前信号生成
│   ├── simulator/          # 模拟交易
│   ├── llm/                # LLM Agent（V3 已接入，配置见项目根 .env）
│   └── ui/
│       └── views/          # Streamlit 页面（10 页平铺导航）
├── .streamlit/config.toml  # 深色主题配置
├── .env / .env.example     # LLM 等敏感配置（不入源码）
└── tests/                  # 测试用例
```

## 开发进度

当前 **Phase 0–13 全部完成**（MVP + V1 增强 + V2 远期 + V3 增强版 + V4-Text 分支 + V5 LLM策略生成/持仓诊断）。

- ✅ Phase 0 环境与骨架
- ✅ Phase 1 数据层
- ✅ Phase 2 因子层
- ✅ Phase 3 策略层
- ✅ Phase 4 信号层
- ✅ Phase 5 回测层
- ✅ Phase 6 模拟交易
- ✅ Phase 7 UI 仪表板
- ✅ Phase 8 MVP 集成验证
- ✅ Phase 9 V1 增强（多策略对比 / Walk-Forward / 详细报告 / LLM / 可视化拼装）
- ✅ Phase 10 V2 远期（LLM 复盘 / 新闻舆情 / 多周期并存 / 推送）
- ✅ Phase 11 V3 增强版（平铺 9 页导航 / 7 策略注册表 / 四大师研究页 / 均线自由组合 / 智谱 GLM-4-Flash 接入 / 深色主题）
- ✅ Phase 12 V4-Text（分支1：量价配置 / 板块自定义筛选 / 界面亲民化+新手指南 / 回测收益曲线 / 共享 UI 组件）
- ✅ Phase 13 V5（分支1：LLM 自然语言→结构化参数→保存策略全站打通 / 持仓诊断：清仓减仓做T建议 + 单股重回测）

## LLM 配置（可选）

复制 `.env.example` 为 `.env`，填入 API Key 即可启用真实 LLM 分析（默认智谱 GLM-4-Flash，免费）：

```ini
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
LLM_MODEL=glm-4-flash
LLM_API_KEY=你的key
```

未配置时自动降级为离线规则模式，所有功能仍可正常使用。