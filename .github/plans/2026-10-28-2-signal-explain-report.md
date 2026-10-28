# 计划书：信号解释报告（入选理由 + 落选对比 + LLM 深度解读）

- 日期：2026-10-28
- 分支：`分支3`（图表升级 Phase 0/1 已提交：`c2f1f0c`、`d421d5f`）
- 状态：**执行中** —— Phase 0 ✅（`6183fae`）、Phase 1 ✅（本次提交）；下一步 Phase 2（今日页对比表 + 瀑布图）
- 环境：Python **3.13.15**（`.venv`）；streamlit **1.63.0**、plotly **7.0.0**、pandas **3.0.5**、numpy 2.5.2；pytest 9.1.1
- 硬约束：**不新增依赖**（INSTRUCTION.md:35、README.md:176/214）；LLM 走现有 OpenAI 兼容客户端（`src/stock_plan/llm/client.py`）

---

## 1. 目标与范围

### 1.1 用户确认的需求（5 问访谈结论）

| 维度 | 结论 |
| --- | --- |
| 使用范围 | 现在只有自己用，但**代码按将来多人设计**：并发闸门、结果缓存、结构化输出这些便宜的先做掉，不返工 |
| 页面形态 | **双档**：数据层先把对比表和图画出来（秒出、不调 LLM），点按钮才调 LLM 写深度解读 |
| 落选范围 | **紧随的 3 名**（入选之后第 1–3 名）+ **每类硬过滤淘汰各 1 只样本**（ST / 停牌 / 流动性 / 次新） |
| 可视化 | 对比表 + **一张分差瀑布图** |
| 风险优先 | ① 最怕 LLM 编造数据 ② 最怕点了等十几秒或卡住 ③ 最怕改坏现有信号页 |

### 1.2 现状诊断（代码事实）

| 事实 | 位置 | 对方案的含义 |
| --- | --- | --- |
| `generate_signals` 先对**全市场**打分再 `.head(top_n)` | `src/stock_plan/signal/generator.py:120` | 落选者与因子分数已在内存，**不需要新数据源、不需要重算** |
| 淘汰规则已经是可解释的扣分 | `src/stock_plan/strategy/builtin.py:86-97`：偏离 ma20 > 10% 扣 100（追高排除）、ma20 < ma60 扣 100（趋势向下）、偏离 > 5% 扣 30、偏离 < -5% 扣 50、RSI > 75 扣 20 | 「落后原因」可以**由代码给出确定答案**，LLM 只负责写成话 |
| 硬过滤只返回通过者，淘汰原因丢失 | `src/stock_plan/factors/filter.py:40-77`（ST / 无日线 / 次新 <60 根 / 停牌 / 20 日均额 < 5000 万） | 需要新增「带原因的过滤」，否则落选样本无从解释 |
| LLM 每次调用无对话历史，只发 system+user | `src/stock_plan/llm/client.py:68-92` | 不会串对话；但**单例上的 `last_error` 是共享可变状态**，并发会串错误提示 |
| 失败静默降级成 mock 模板 | `src/stock_plan/llm/client.py:90-92` | 用户会把「限流/断网」误读成「模型胡说」，必须改成显式失败 |
| 无缓存、无限流、无 token 预算 | 全仓 grep 无 `cache_data`/信号量 | 多人同时点＝同 key 并发 → 429，且重复烧额度 |

### 1.3 范围内

- LLM 基础设施加固：输入预算、结果缓存、并发闸门、显式失败、错误归因到请求
- 事实层：`SignalReport`（入选 5 + 落选 3 + 硬过滤样本 4 + 分差 + 扣分归因）
- 页面：今日信号页新增「为什么是这 5 只」区块（对比表 + 瀑布图 + 深度解读按钮）
- LLM 叙事层：JSON 白名单输出 + 真实性闸门 + 代码模板兜底

### 1.4 范围外

- 不改现有信号表结构、不删现有「入选理由」展开区（新增区块默认折叠，旧界面不变）
- 不做多用户账号体系、不做公网部署、不上队列/Redis（公网多人时再立项）
- 不改 `data/` 抓取逻辑、不改策略打分数值（只把扣分**拆出来可解释**，数值必须逐位一致）
- 不做 App / 小程序（沿用计划书 `2026-10-28-1` 第 7 节的预留纪律）

---

## 2. 核心设计：事实与叙事分离

**三层结构**，每层职责单一、可独立测试：

| 层 | 由谁产出 | 内容 | 校验 |
| --- | --- | --- | --- |
| ① 事实层 | Python 代码 | 排名、综合分、各因子分、扣分项（规则名 + 分值 + 规则说明）、硬过滤原因、分差、候选池统计、数据截止日 | 纯函数，单测覆盖 |
| ② 叙事层 | LLM | 只写中文解释（为什么赢、差在哪、风险提示），**不许输出任何阿拉伯数字**，不许新增股票代码 | JSON 字段白名单 + code 白名单 + 数字闸门 |
| ③ 兜底层 | Python 模板 | LLM 失败/超时/校验不过时，用①的扣分明细直接拼句子 | 必然可用，页面标注「规则解释，非模型生成」 |

**为什么这样能解决"信息真实度"**：所有数字都来自①，②只是把①写成研报口吻；②即使撒谎也只能撒谎在措辞上，无法伪造分数。数字闸门（叙事中出现 `[0-9]` 即判不合格）是最后一道保险。

### 2.1 数据结构（`src/stock_plan/signal/report.py`）

```python
@dataclass
class Penalty:              # 一条扣分事实
    rule: str               # 如 "dev_ma20_too_high"
    label: str              # 如 "偏离 ma20 +12.3%，追高排除"
    points: float           # -100 / -50 / -30 / -20
    basis: float | None     # 触发该规则的原始数值（偏离率/RSI 等）

@dataclass
class CandidateFacts:       # 入选者与落选者共用
    code: str; name: str; rank: int; score: float
    trend_score: float; fund_score: float
    vol_ratio: float | None; rsi14: float | None; dev_ma20: float | None
    penalties: list[Penalty]; reasons: list[str]
    entry_price / exit_price / stop_loss / hold_days  # 仅入选者

@dataclass
class RejectedSample:       # 硬过滤淘汰样本
    code: str; name: str; reason: str      # st / no_bars / new_listing / suspended / illiquid
    reason_text: str; metrics: dict        # 上市天数 / 20 日均额 / 最新成交量等

@dataclass
class SignalReport:
    strategy_fingerprint: str; generated_at: str; data_until: str
    universe: dict          # 总数 / 有缓存 / 硬过滤淘汰各原因计数 / 参与打分
    picked: list[CandidateFacts]        # Top N（默认 5）
    runners_up: list[CandidateFacts]    # 紧随 3 名
    rejected: list[RejectedSample]      # 每类硬过滤各 1 只
    entry_gap: dict                     # 入选末位 vs 落选第一名的分差与构成
```

### 2.2 LLM 提示与输出（`src/stock_plan/llm/analyzer.py` 新增 `explain_signal_report`）

- 输入：`SignalReport` 压平成 ≤ 1200 字的紧凑文本（只含代码/名称/排名/分数/扣分项/分差），经 `llm/budget.py` 预算裁剪
- 输出：严格 JSON
  ```json
  {"summary": "...", "picked": [{"code": "...", "why": "..."}],
   "runners_up": [{"code": "...", "why_behind": "...", "key_gap": "..."}],
   "risk_note": "..."}
  ```
- 校验（任一不过 → 模板兜底，并在页面显示原因）：JSON 可解析 / 字段齐全 / `code` ⊆ 事实集合 / 叙述字段不含 `[0-9]` / 单字段 ≤ 120 字
- 参数：`temperature=0.2`、`max_tokens` 上限 800、`timeout=30s`

---

## 3. 模块划分

| 模块 | 职责 | 交互方式 | 状态 |
| --- | --- | --- | --- |
| `src/stock_plan/factors/filter.py` | 新增 `filter_universe_explained()`：返回 (通过, 淘汰样本+原因)，原 `filter_universe()` 改为其薄封装 | 被 `strategy/builtin.py`、`signal/generator.py` 调用 | 修改 ✅ |
| `src/stock_plan/strategy/builtin.py` | 新增 `score_with_penalties()`：把扣分规则从 `score()` 抽出，返回 (分数, 逐行扣分明细)；`score()` 保证数值逐位不变 | 被 report 层调用 | 修改 ✅ |
| `src/stock_plan/signal/generator.py` | 新增 `generate_signal_report()`：复用现有加载/过滤/打分流程，产出 `SignalReport`；原 `generate_signals()` 行为不变（仅并列名次改为固定次键） | 被 `today.py` 调用 | 修改 ✅ |
| `src/stock_plan/signal/report.py` | 事实层数据结构 + 分差/候选池统计/叙事输入序列化 | 纯数据模块 | 新增 ✅ |
| `src/stock_plan/llm/budget.py` | token 估算（中文≈1 字 1 token、ASCII≈4 字符 1 token）+ 分段裁剪 + 截断记录 | 被 analyzer 调用 | 新增 ✅ |
| `src/stock_plan/llm/cache.py` | `(任务, 输入哈希)` → `data/cache/llm/<sha1>.json`，TTL 7 天，`LLM_CACHE_DISABLE=1` 可关 | 被 client 调用 | 新增 ✅ |
| `src/stock_plan/llm/client.py` | 新增 `ChatResult(text, error, cached, usage, elapsed_ms)`；`chat_result()` 支持并发闸门（`Semaphore(2)`）、429/超时退避 1 次、缓存；`last_error` 改 `threading.local`；`chat()` 保留返回 str 以兼容旧调用 | 全站 LLM 调用 | 修改 ✅ |
| `src/stock_plan/llm/analyzer.py` | 新增 `explain_signal_report()` + `_template_narrative()` 兜底 | 被 `today.py` 调用 | 修改 |
| `src/stock_plan/ui/views/today.py` | 新增「为什么是这 5 只」折叠区块：对比表、瀑布图、硬过滤样本卡、深度解读按钮 | 调 report + analyzer | 修改 |
| `src/stock_plan/ui/widgets.py` | 新增 `score_waterfall_fig()`（分差瀑布），复用 `chart_style` | 被 today 调用 | 修改 |
| `tests/test_signal_report.py` | 事实层：排序/分差/扣分归因/硬过滤分类/数值黄金对比 | pytest | 新增 ✅ |
| `tests/test_llm_guardrails.py` | 预算截断、缓存命中/过期、并发上限、错误隔离、5 种坏输出全部兜底 | pytest | 新增 ✅ |

---

## 4. 阶段拆分与完成标准

### Phase 0 — LLM 基础设施加固（先做，UI 零变化）✅ 已完成（`6183fae`）

- 内容：`llm/budget.py`、`llm/cache.py`、`ChatResult` + 并发闸门 + 退避 + `threading.local` 错误归因
- 完成标准：
  - 全量 `pytest` 绿；现有 LLM 页面（信号解释/复盘/策略生成）行为不变，mock 提示仍在
  - 新增测试覆盖：预算截断记录、缓存二次调用不触发真实 client、并发峰值 ≤ 2、两线程错误不互串
  - 失败时页面文案从「【离线规则模式】」改为显式「本次调用失败：限流/超时（可重试）」
- 预期产出：1 个 commit（`(phase 0) harden llm client with budget, cache and concurrency guard`）

### Phase 1 — 事实层（无 UI、无 LLM）✅ 已完成

- 内容：`filter_universe_explained`、`score_with_penalties`、`signal/report.py`、`generate_signal_report`
- 完成标准：
  - `score()` 改造前后数值**逐位一致**（黄金测试：固定 fixture 上 `pytest.approx`）→ 已由 `tests/test_signal_report.py::test_score_with_penalties_matches_legacy_score` 锁死
  - `generate_signals()` 输出与改造前完全一致（同 fixture 断言 code/score/价格全等）→ 已由 `test_report_matches_generate_signals` 锁死；顺带修掉一处**并列名次不确定**：排名改用「分数降序 + 代码升序」固定次键（`_rank_factor_rows`），否则信号页与报告可能各排一次、并列时选出不同的人
  - `SignalReport` 三项齐备：入选 5、落选 3、硬过滤每类 1 只（用构造的小样本验证四类原因都能命中）→ 已覆盖
- 实测（真实缓存数据）：股票列表 5576 只、有日线 5379 只、通过硬过滤 4130 只（ST 197／无日线 49／流动性不足 1200），日线截至 2026-10-27；入选第 5 名与紧随第 1 名**同为 87.6 分**（分差 0.0），报告如实标注"门槛非常接近"——这正是本功能要暴露的信息
- 性能注意：冷启动约 47.7s（主要是 5379 只股票的 180 日窗口加载），页面必须走 `st.cache_data`（`today.py:59` 已有同款缓存范式）
- 预期产出：1 个 commit（`(phase 1) explain why each signal was picked and who missed`）

### Phase 2 — 页面即时对比（不调 LLM）

- 内容：`today.py` 新区块（默认折叠）+ 对比表 + 瀑布图 + 硬过滤样本卡
- 完成标准：
  - 现有信号表、「入选理由」展开区、发送到模拟盘/推送按钮**全部不受影响**
  - 样张截图（合成数据 + Edge headless）经用户确认观感
  - 首屏渲染不新增超过 300ms（报告与信号共用一次打分）
- 预期产出：1 个 commit + 截图

### Phase 3 — LLM 深度解读

- 内容：`explain_signal_report` + 数字闸门 + 模板兜底 + 按钮（缓存命中显示「缓存于 x 分钟前」）
- 完成标准：
  - 5 种坏输出（非 JSON / 缺字段 / 多余 code / 含阿拉伯数字 / 空串）全部自动兜底，页面标注原因
  - mock 模式（无 key）可完整跑通，展示模板叙事
  - 真实调用路径用假 client 验证（不烧额度）
- 预期产出：1 个 commit（`(phase 3) llm narrative for signal comparison`）

### Phase 4 — 回归、文档与归档

- 内容：全站 `pytest`、真实数据截图、`CHANGELOG.md`、测试报告、计划书归档、README 补一节
- 完成标准：
  - 全量测试绿（Phase 1 后基线 **270 passed**）
  - 测试报告 `tests/reports/<YYYY-MM-DD-HH-mm>.report-*.md`
  - 计划书移到 `.github/plans/done/`；`README.md` 说明 `data/cache/llm/` 与 `.env` 三个变量
- 预期产出：1 个 commit + 报告

---

## 5. 测试策略

| 层 | 用例 | 边界 |
| --- | --- | --- |
| 扣分归因 | 追高（偏离 +12%）、趋势向下、偏高（+6%）、趋势转弱（-7%）、RSI 78 | 边界值：偏离恰好 5% / 10%、RSI 恰好 75 |
| 硬过滤 | ST、无日线、次新 59 根、停牌（成交量 0）、20 日均额 4999 万 | 60 根、5000 万整、空换手数据 |
| 事实层排序 | 选中 5 + 落后 3 + 分差计算 | top_n=3/5/10；候选池 <8 只时降级（落选不足 3） |
| 预算 | 超长 prompt 截断、截断计数、空输入 | 中文/英文混合、极长单字段 |
| 缓存 | 命中/过期/禁写/并发同 key | TTL 边界、损坏 JSON 文件 |
| 并发 | Semaphore 上限、429 退避次数 | 超时异常、随机异常 |
| LLM 闸门 | 5 种坏输出 → 兜底；合法输出 → 正常渲染 | 缺 code、code 不在事实集、含「82 分」这类数字 |
| UI 回归 | 今日页渲染冒烟（AppTest，参照 `tests/test_strategy_mgr_table.py`） | 无信号、单只信号、LLM 不可用 |

---

## 6. 风险与限制

| 风险 | 后果 | 对策 |
| --- | --- | --- |
| LLM 编造数字/原因 | 用户误信，破坏"真实度" | 数字闸门 + code 白名单 + 代码模板兜底 + 页面标注生成方式 |
| 点了等很久/卡住 | 用户以为程序死了 | 表格图秒出；LLM 独立按钮 + 30s 超时 + 缓存 + 明确失败文案 |
| 改坏现有信号页 | 每天用的功能不可用 | 新区块默认折叠；不改旧表结构；Phase 1 黄金测试锁数值；Phase 2 回归 |
| ST 过滤是「今天的」状态 | 解释样本暗示历史真实 | 报告文案固定写「当前状态」，并在页面 caption 引用 `filter.py:12-28` 的已知偏差说明 |
| 扣分规则再复制一份 | 两处规则漂移 | 只在 `builtin.py` 抽一次，`score()` 与归因共用同一函数；黄金测试锁数值 |
| 数字闸门误杀 | 正常叙事被判不合格 | 兜底模板仍可读；把误杀原因显示给用户，便于后续放宽 |
| 缓存目录进仓库 | 脏 diff | 确认 `.gitignore` 覆盖 `data/`；缓存可随时删除重建 |

---

## 7. 文件清单

**新增（6）**
- `.github/plans/2026-10-28-2-signal-explain-report.md`（本文件）
- `src/stock_plan/signal/report.py`
- `src/stock_plan/llm/budget.py`
- `src/stock_plan/llm/cache.py`
- `tests/test_signal_report.py`
- `tests/test_llm_guardrails.py`

**修改（9）**
- `src/stock_plan/llm/client.py`
- `src/stock_plan/llm/analyzer.py`
- `src/stock_plan/factors/filter.py`
- `src/stock_plan/strategy/builtin.py`
- `src/stock_plan/signal/generator.py`
- `src/stock_plan/ui/views/today.py`
- `src/stock_plan/ui/widgets.py`
- `README.md`
- `CHANGELOG.md`

**删除**：无

---

## 8. 待用户确认

1. 本计划书是否批准（批准后按 Phase 0 → 4 执行，每阶段一个 commit）。
2. 图表升级那边：Phase 1 样张是否通过？通过则 Phase 2（对比页 / 持仓页 / 今日页套同一套视觉规范）与本计划谁先做（建议先做本计划的 Phase 0/1，因为它会改 `today.py`，与图表的 Phase 2 有文件交叉，先做完再统一套视觉）。
