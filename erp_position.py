import pandas as pd
import numpy as np
import os
from datetime import datetime
import requests

from etf_metrics import load_etf_metrics, build_etf_metrics_block


# ══════════════════════════════════════════════════════════════════════
#  Shiller CAPE 长期回报锚模块
#  数据源: Shiller ie_data.xls (Data sheet)
#  逻辑:
#    1. 读取历史 CAPE 和 Real 10Y Excess Annualized Returns
#    2. 按 CAPE 区间分组，直接取同组的历史回报分布
#    3. 输出当前 CAPE 所在组的均值、分位、样本数
# ══════════════════════════════════════════════════════════════════════

SHILLER_PATH = os.getenv("SHILLER_PATH", "./data/ie_data.xls")

_shiller_cache = {}   # 避免多次读文件

# CAPE 分组区间定义
_CAPE_BINS   = [0,  10,  15,  20,  25,  30,  35,  40,  999]
_CAPE_LABELS = ['<10', '10-15', '15-20', '20-25', '25-30', '30-35', '35-40', '>40']


# ── 顶部总览表 ────────────────────────────────────────────────────────────────
_price_cache = {}   # 避免重复读文件

PRICE_CSV_URL = os.getenv(
    "PRICE_CSV_URL",
    "https://github.com/ChiaraVan1/ETF_data_project/releases/latest/download/etf_price.csv"
)

def _load_price_series(code: str) -> pd.Series | None:
    """
    读取 etf_price.csv 中对应指数的价格序列。
    优先读本地文件，不存在时从 GitHub Release 下载（与 load_etf_metrics 同样模式）。
    """
    if code in _price_cache:
        return _price_cache[code]

    # 先试本地
    for local_path in ["./etf_price.csv", "./data/etf_price.csv"]:
        if os.path.exists(local_path):
            try:
                df = pd.read_csv(local_path, index_col=0, parse_dates=True)
                if code in df.columns:
                    s = df[code].dropna()
                    _price_cache[code] = s
                    return s
            except Exception:
                pass

    # 本地没有，从 Release 下载
    try:
        resp = requests.get(PRICE_CSV_URL, timeout=15)
        resp.raise_for_status()
        from io import StringIO
        df = pd.read_csv(StringIO(resp.text), index_col=0, parse_dates=True)
        if code not in df.columns:
            return None
        # 缓存所有列，下次不再下载
        for col in df.columns:
            _price_cache[col] = df[col].dropna()
        return _price_cache.get(code)
    except Exception as e:
        print(f"⚠️ etf_price.csv 下载失败: {e}")
        return None


def calc_win_rate_and_odds(df: pd.DataFrame, current_erp: float,
                           code: str = "",
                           window_pct: float = 0.03,
                           forward_days: int = 252) -> dict | None:
    """
    基于历史ERP序列 + 真实ETF价格，计算当前估值位置的胜率和赔率。

    方法：
    - 找出历史上所有 ERP 在 current_erp ± window_pct 范围内的日期
    - 查这些日期 forward_days 个交易日后的真实价格涨跌幅
    - 胜率 = 上涨次数 / 总次数
    - 赢时赔率 = 赢时中位涨幅 / 输时中位跌幅绝对值
    """
    price_series = _load_price_series(code)

    erp_col = df[['Date', 'ERP']].dropna().reset_index(drop=True)
    if len(erp_col) < forward_days + 10:
        return None

    returns = []
    for i, row in erp_col.iterrows():
        erp_val = row['ERP']
        if abs(erp_val - current_erp) > window_pct:
            continue
        date = row['Date']

        if price_series is not None:
            # 用真实价格计算回报
            future_idx = price_series.index.searchsorted(date) + forward_days
            if future_idx >= len(price_series):
                continue
            price_now    = price_series.asof(date)
            price_future = price_series.iloc[future_idx]
            if price_now is None or price_now == 0:
                continue
            ret = (price_future - price_now) / price_now
        else:
            # 降级方案：用ERP变化近似（无价格数据时）
            future_i = i + forward_days
            if future_i >= len(erp_col):
                continue
            future_erp = erp_col.loc[future_i, 'ERP']
            ret = -(future_erp - erp_val) / abs(erp_val) if erp_val != 0 else 0

        returns.append(ret)

    if len(returns) < 5:
        return None

    r = pd.Series(returns)
    wins   = r[r > 0]
    losses = r[r <= 0]

    win_rate     = len(wins) / len(r)
    median_gain  = wins.median()   if len(wins)   > 0 else 0.0
    median_loss  = abs(losses.median()) if len(losses) > 0 else 0.0
    odds_ratio   = (median_gain / median_loss) if median_loss > 0 else float('inf')
    used_price   = price_series is not None

    return {
        "win_rate":    win_rate,
        "median_gain": median_gain,
        "median_loss": median_loss,
        "odds_ratio":  odds_ratio,
        "n_samples":   len(r),
        "window_pct":  window_pct,
        "forward_days": forward_days,
        "used_price":  used_price,   # True=真实价格，False=ERP近似
    }


def build_valuation_space_block(df: pd.DataFrame, current_erp: float, code: str) -> str:
    """
    方案一：基于ERP分位的估值空间法（你图中的逻辑）
    
    方法：
    - 当前ERP分位 = 历史上ERP低于当前的比例
    - 胜率（图中定义）= 1 - ERP分位（历史上比现在更贵的概率）
    - 赔率（图中定义）= 上行空间 = (历史高估区阈值 - 当前) / 当前（用ERP反向推算价格空间）
    """
    erp_series = df['ERP'].dropna()
    if len(erp_series) < 100:
        return "\n> ⚠️ ERP数据不足，无法计算估值空间。\n"
    
    current_erp = erp_series.iloc[-1]
    
    # 计算分位
    percentile = (erp_series < current_erp).mean()
    win_rate_by_percentile = 1 - percentile  # 图中定义的胜率
    
    # 历史极端值（用ERP分位）
    erp_high_90 = erp_series.quantile(0.90)   # 高ERP = 便宜区
    erp_low_10  = erp_series.quantile(0.10)   # 低ERP = 昂贵区
    erp_median  = erp_series.median()
    
    # 上行空间：从当前ERP到"昂贵区"（低ERP）的差值
    # 注意：ERP下降 = 价格上涨
    # 上行空间 ≈ (当前ERP - 历史低ERP区) / 当前ERP × 弹性系数
    # 简化：直接用ERP变化率，假设1% ERP变化对应1%价格变化
    upside_pct = (current_erp - erp_low_10) / abs(current_erp) if current_erp > erp_low_10 else 0
    downside_pct = (erp_high_90 - current_erp) / abs(current_erp) if erp_high_90 > current_erp else 0
    
    # 估值区间判断
    if percentile >= 0.75:
        zone_icon = "🟢"
        zone_name = "极度低估"
        zone_desc = "ERP处于历史高位，股票相对债券很便宜"
        action = "极佳买点，建议重仓"
    elif percentile >= 0.50:
        zone_icon = "🟡"
        zone_name = "合理偏低"
        zone_desc = "中性偏便宜，可以逐步建仓"
        action = "可分批买入"
    elif percentile >= 0.25:
        zone_icon = "🟠"
        zone_name = "合理偏高"
        zone_desc = "中性偏贵，谨慎加仓"
        action = "持有或减仓"
    else:
        zone_icon = "🔴"
        zone_name = "严重高估"
        zone_desc = "ERP处于历史低位，股票很贵"
        action = "建议规避"
    
    # 样本警告
    n_samples = len(erp_series)
    sample_warning = f"\n> ⚠️ 样本量 {n_samples} 天，历史区间可能不包含完整周期。" if n_samples < 500 else ""
    
    # 期望值（简化版）
    expected = win_rate_by_percentile * upside_pct - (1 - win_rate_by_percentile) * downside_pct
    
    # 综合评级
    if win_rate_by_percentile >= 0.30 and upside_pct >= 0.15:
        rating = "🟢 高胜率 + 高赔率，极佳买点"
    elif win_rate_by_percentile >= 0.30 and upside_pct >= 0.10:
        rating = "🟢 胜率尚可 + 赔率合理，较好买点"
    elif win_rate_by_percentile >= 0.25 and upside_pct >= 0.10:
        rating = "🟡 胜率中等 + 赔率一般，可参与"
    elif win_rate_by_percentile >= 0.20 and upside_pct >= 0.05:
        rating = "🟡 胜率赔率均衡，中性"
    elif win_rate_by_percentile < 0.15 and upside_pct < 0.05:
        rating = "🚨 低胜率 + 低赔率，双杀，规避"
    elif win_rate_by_percentile < 0.15:
        rating = "🔴 低胜率，谨慎"
    else:
        rating = "🟠 中性偏弱"
    
    block = f"""
---
### 方案一：估值空间法（基于ERP分位）

> 方法：利用ERP历史分位判断当前估值位置。
> 当前ERP = **{current_erp:.2%}**，历史分位 = **{percentile:.1%}**
{sample_warning}

| 指标 | 数值 | 说明 |
|:-----|-----:|:-----|
| 当前ERP分位 | **{percentile:.1%}** | {zone_icon} **{zone_name}** |
| 图中定义胜率 | **{win_rate_by_percentile:.1%}** | 历史上比现在更贵的概率 |
| 上行空间（赔率） | **{upside_pct:.1%}** | 假设回到历史10%分位的涨幅 |
| 下行风险 | **{downside_pct:.1%}** | 假设回到历史90%分位的跌幅 |
| 期望值 | **{expected:+.1%}** | 胜率×上行空间 − 败率×下行风险 |

| 分位点 | ERP值 | 估值状态 |
|:-------|------:|:---------|
| P90 | {erp_series.quantile(0.90):.2%} | 极度低估（便宜区） |
| P75 | {erp_series.quantile(0.75):.2%} | 显著低估 |
| P50 | {erp_series.quantile(0.50):.2%} | 价值中枢 |
| P25 | {erp_series.quantile(0.25):.2%} | 进入高估 |
| P10 | {erp_series.quantile(0.10):.2%} | 极度高估（昂贵区） |

**综合评级：{rating}**
> 📌 提示：方案一基于历史分位假设估值会回归；方案二（下方）基于真实历史回测。
"""
    return block


def build_win_rate_block(df: pd.DataFrame, current_erp: float, code: str) -> str:
    """方案二：生成胜率赔率 markdown 模块（基于历史回测）"""
    monthly_codes = {'EWQ', 'EWG', 'EWJ', 'EEM', 'HSTECH'}
    forward_days = 12 if code in monthly_codes else 252
    span_label = "个月" if code in monthly_codes else "个交易日"

    result = None
    for window in [0.03, 0.05, 0.08]:
        result = calc_win_rate_and_odds(df, current_erp,
                                        code=code,
                                        window_pct=window,
                                        forward_days=forward_days)
        if result and result["n_samples"] >= 8:
            break

    if result is None or result["n_samples"] < 5:
        return "\n> ⚠️ 历史样本不足，无法计算胜率赔率。\n"

    win_rate = result["win_rate"]
    median_gain = result["median_gain"]
    median_loss = result["median_loss"]
    odds_ratio = result["odds_ratio"]
    n = result["n_samples"]
    window = result["window_pct"]

    # 期望收益（正确公式）
    expected_return = win_rate * median_gain - (1 - win_rate) * median_loss

    # 综合评级
    if win_rate >= 0.6 and odds_ratio >= 1.5:
        rating = "🟢 高胜率 + 高赔率，极佳买点"
    elif win_rate >= 0.6 and odds_ratio >= 1.0:
        rating = "🟢 高胜率 + 合理赔率，较好买点"
    elif win_rate >= 0.5 and odds_ratio >= 1.5:
        rating = "🟡 胜率中等 + 赔率较高，可参与"
    elif win_rate >= 0.5 and odds_ratio >= 1.0:
        rating = "🟡 胜率赔率均衡，中性"
    elif win_rate < 0.4 and odds_ratio < 1.0:
        rating = "🚨 低胜率 + 低赔率，双杀，规避"
    elif win_rate < 0.4:
        rating = "🔴 低胜率，谨慎"
    elif odds_ratio < 1.0:
        rating = "🟠 低赔率，涨幅有限"
    else:
        rating = "🟠 中性偏弱"

    sample_warning = f"\n> ⚠️ 样本量仅 {n} 个，统计可靠性有限，仅供参考。" if n < 15 else ""

    data_source = "真实ETF价格" if result.get("used_price") else "ERP变化近似（价格数据缺失）"

    block = f"""
---
### 方案二：历史回测法（持有{forward_days}{span_label}维度）

> 方法：历史上 ERP 在当前值 ±{window:.0%} 范围内的时点（共 **{n}** 个），
> 持有 {forward_days}{span_label} 后的回报分布。数据来源：**{data_source}**。
{sample_warning}

| 指标 | 数值 | 说明 |
|:-----|-----:|:-----|
| 胜率 | **{win_rate:.0%}** | 持有后上涨的历史概率 |
| 赔率（盈亏比） | **{odds_ratio:.2f}x** | 赢时中位涨幅 / 输时中位跌幅 |
| 赢时中位回报 | +{median_gain:.1%} | 历史赢局的典型涨幅 |
| 输时中位回报 | -{median_loss:.1%} | 历史输局的典型跌幅 |
| 期望收益 | **{expected_return:+.1%}** | 胜率×赢时收益 − 败率×输时损失 |

**综合评级：{rating}**
"""
    return block


def build_integrated_block(df: pd.DataFrame, current_erp: float, code: str) -> str:
    """
    方案三：整合评估方案（结合方案一和方案二）
    使用更合理的区间划分：P75-P90（低估区）、P25-P75（中性区）、P10-P25（高估区）
    """
    erp_series = df['ERP'].dropna()
    if len(erp_series) < 100:
        return "\n> ⚠️ ERP数据不足，无法计算整合方案。\n"
    
    current_erp = erp_series.iloc[-1]
    percentile = (erp_series < current_erp).mean()
    
    # 定义三个核心区间（更严格的划分）
    # 便宜区：P75-P90（前25%的位置）
    # 昂贵区：P10-P25（后25%的位置）
    # 中性区：P25-P75（中间50%）
    
    cheap_threshold = erp_series.quantile(0.75)        # P75
    very_cheap_threshold = erp_series.quantile(0.90)   # P90
    expensive_threshold = erp_series.quantile(0.25)    # P25
    very_expensive_threshold = erp_series.quantile(0.10)  # P10
    
    # ============================================================
    # 第一部分：估值区间判断（基于更严格的阈值）
    # ============================================================
    if current_erp >= very_cheap_threshold:
        zone = "🚀 极度低估区（>P90）"
        zone_color = "🟢🟢"
        multiple = 1.5
    elif current_erp >= cheap_threshold:
        zone = "🟢 低估区（P75-P90）"
        zone_color = "🟢"
        multiple = 1.2
    elif current_erp >= erp_series.quantile(0.50):
        zone = "🟡 合理偏低区（P50-P75）"
        zone_color = "🟡"
        multiple = 0.9
    elif current_erp >= expensive_threshold:
        zone = "🟠 合理偏高区（P25-P50）"
        zone_color = "🟠"
        multiple = 0.7
    elif current_erp >= very_expensive_threshold:
        zone = "🔴 高估区（P10-P25）"
        zone_color = "🔴"
        multiple = 0.5
    else:
        zone = "🚨 泡沫区（<P10）"
        zone_color = "🚨"
        multiple = 0.3
    
    # ============================================================
    # 第二部分：历史回测（方案二的核心）
    # ============================================================
    monthly_codes = {'EWQ', 'EWG', 'EWJ', 'EEM', 'HSTECH'}
    forward_days = 12 if code in monthly_codes else 252
    span_label = "个月" if code in monthly_codes else "个交易日"
    
    result = None
    for window in [0.03, 0.05, 0.08]:
        result = calc_win_rate_and_odds(df, current_erp, code=code,
                                        window_pct=window, forward_days=forward_days)
        if result and result["n_samples"] >= 8:
            break
    
    if result is None or result["n_samples"] < 5:
        return "\n> ⚠️ 历史样本不足，无法计算整合方案。\n"
    
    win_rate = result["win_rate"]
    median_gain = result["median_gain"]
    median_loss = result["median_loss"]
    odds_ratio = result["odds_ratio"]
    n = result["n_samples"]
    
    # 期望收益（正确公式）
    expected_return = win_rate * median_gain - (1 - win_rate) * median_loss
    
    # ============================================================
    # 第三部分：整合评级（结合理论和实际）
    # ============================================================
    
    # 理论评级（基于分位）
    if current_erp >= cheap_threshold:
        theory_rating = "🟢 理论估值便宜"
        theory_score = 2
    elif current_erp >= erp_series.quantile(0.50):
        theory_rating = "🟡 理论估值中性"
        theory_score = 1
    else:
        theory_rating = "🔴 理论估值昂贵"
        theory_score = 0
    
    # 实际评级（基于回测）
    if win_rate >= 0.55 and odds_ratio >= 1.2:
        actual_rating = "🟢 实际表现优秀"
        actual_score = 2
    elif win_rate >= 0.50 and odds_ratio >= 1.0:
        actual_rating = "🟡 实际表现一般"
        actual_score = 1
    else:
        actual_rating = "🔴 实际表现差"
        actual_score = 0
    
    # 综合得分
    total_score = theory_score + actual_score
    if total_score >= 3:
        final_rating = "🟢🟢 强烈推荐买入"
        action = "建议重仓（70-80%）"
    elif total_score >= 2:
        final_rating = "🟢 推荐买入"
        action = "建议分批建仓（40-60%）"
    elif total_score >= 1:
        final_rating = "🟡 中性持有"
        action = "持有观望，不加减仓"
    else:
        final_rating = "🔴 建议规避"
        action = "减仓或空仓"
    
    # ============================================================
    # 第四部分：置信度（考虑样本量和区间位置）
    # ============================================================
    
    # 样本量置信度
    if n >= 50:
        sample_confidence = "高"
    elif n >= 20:
        sample_confidence = "中"
    else:
        sample_confidence = "低"
    
    # 区间位置置信度（越极端越可信）
    if current_erp >= very_cheap_threshold or current_erp <= very_expensive_threshold:
        position_confidence = "高（极端位置）"
    elif current_erp >= cheap_threshold or current_erp <= expensive_threshold:
        position_confidence = "中（接近极端）"
    else:
        position_confidence = "低（中间位置）"
    
    # ============================================================
    # 输出整合报告
    # ============================================================
    
    block = f"""
---
### 方案三：整合评估（理论+实际）

> 整合方案一（理论估值）和方案二（历史回测），给出综合判断。

#### 📊 输入数据

| 指标 | 数值 | 说明 |
|:-----|-----:|:-----|
| 当前ERP | **{current_erp:.2%}** | |
| ERP历史分位 | **{percentile:.1%}** | 历史上{percentile:.0%}时间比现在便宜 |
| 理论估值区间 | **{zone}** | {zone_color} |
| 历史回测胜率 | **{win_rate:.1%}** | 类似ERP买入后上涨概率 |
| 历史回测盈亏比 | **{odds_ratio:.2f}x** | 赢时收益 / 输时损失 |
| 期望收益 | **{expected_return:+.1%}** | 历史类似位置的平均期望 |
| 有效样本数 | **{n}** 个 | |

#### 🎯 评估得分

| 维度 | 评分 | 说明 |
|:-----|:----:|:-----|
| 理论估值 | {theory_score}/2 | {theory_rating} |
| 历史回测 | {actual_score}/2 | {actual_rating} |
| **综合得分** | **{total_score}/4** | |

#### 📌 最终结论

**{final_rating}**

> {action}
>
> 置信度：样本量置信度【{sample_confidence}】、位置置信度【{position_confidence}】
>
> 💡 提示：当理论与实际一致时（如都看好或都看空），结论可靠性更高。

---
### 补充：历史回测细节

| 指标 | 数值 | 说明 |
|:-----|-----:|:-----|
| 胜率 | **{win_rate:.1%}** | 上涨概率 |
| 赢时中位回报 | **+{median_gain:.1%}** | 典型涨幅 |
| 输时中位回报 | **-{median_loss:.1%}** | 典型跌幅 |
| 盈亏比 | **{odds_ratio:.2f}x** | 盈亏不对称性 |
| 期望收益 | **{expected_return:+.1%}** | 长期平均结果 |
"""
    return block


# ── 顶部总览表 ────────────────────────────────────────────────────────────────
def build_summary_block(summary_list: list) -> str:
    """所有标的的信号灯 + 仓位一览，放在报告最顶部"""
    if not summary_list:
        return ""
    rows = []
    for r in summary_list:
        zone_short = r["erp_zone"].split("(")[0].strip()   # 去掉 (P75-P90) 括号
        rows.append(
            f"| {r['name']} ({r['code']}) "
            f"| {zone_short} "
            f"| **{r['total_pct']}%** "
            f"| {r['b_pct']}+{r['v_pct']}+{r['t_pct']} |"
        )
    rows_md = "\n".join(rows)
    return f"""## 📊 今日总览 · {datetime.now().strftime('%m-%d')}

| 标的 | 估值 | 总仓位 | 底仓+价值+投机 |
|:----|:-----|------:|:-------------|
{rows_md}

---
"""


# ── 颜色图例（插入报告顶部一次） ──────────────────────────────────────────────
LEGEND_BLOCK = """---
**估值颜色说明**（适用于全报告所有 ERP / PS / PSY 区间标注）

| 颜色 | 含义 | ERP 对应分位 |
|:----:|:-----|:------------|
| 🟢 | 低估（便宜） | >= P75 |
| 🟡 | 合理偏低 | P50 – P75 |
| 🟠 | 合理偏高 / 开始高估 | P25 – P50 |
| 🔴 | 高估 | P10 – P25 |
| 🚨 | 极度高估 / 危险泡沫 | < P10 |

> ERP（股权风险溢价）= 1/PE − 无风险利率。ERP 越高表示股票相对债券越便宜，ERP 越低（尤其负值）表示估值越贵。
---
"""


def _load_shiller():
    """读取并缓存 Shiller 数据，返回 (grouped_stats, full_valid_df, cape_now)"""
    if _shiller_cache:
        return _shiller_cache["grouped"], _shiller_cache["valid"], _shiller_cache["cape_now"]

    if not os.path.exists(SHILLER_PATH):
        return None, None, None

    df = pd.read_excel(SHILLER_PATH, engine="xlrd", sheet_name="Data",
                       header=7, skiprows=[8])
    df.columns = df.columns.str.strip()
    df = df.rename(columns={
        "CAPE":      "cape",
        "Returns.2": "excess_return_10y",   # Real 10Y Excess Annualized Returns
    })
    df = df[pd.to_numeric(df["Date"], errors="coerce").notna()].copy()

    valid = df[["cape", "excess_return_10y"]].dropna().copy()
    valid["cape_bin"] = pd.cut(valid["cape"], bins=_CAPE_BINS, labels=_CAPE_LABELS)

    grouped = valid.groupby("cape_bin", observed=True)["excess_return_10y"].agg(
        count="count",
        mean="mean",
        p10=lambda x: x.quantile(0.10),
        p25=lambda x: x.quantile(0.25),
        p50=lambda x: x.quantile(0.50),
        p75=lambda x: x.quantile(0.75),
        p90=lambda x: x.quantile(0.90),
    )

    cape_now = df["cape"].dropna().iloc[-1]

    _shiller_cache["grouped"]  = grouped
    _shiller_cache["valid"]    = valid
    _shiller_cache["cape_now"] = cape_now
    return grouped, valid, cape_now


def build_shiller_block(code):
    """
    仅对 SPY 生成 Shiller 补充模块。
    返回 markdown 字符串，失败时返回空字符串。
    """
    if code != "SPY":  # CAPE 是 S&P 500 专属指标，不适用于 QQQ
        return ""

    grouped, valid, cape_now = _load_shiller()
    if grouped is None:
        return "\n> ⚠️ 未找到 Shiller 数据文件，跳过长期回报锚分析。\n"

    cape_bin_series = pd.cut([cape_now], bins=_CAPE_BINS, labels=_CAPE_LABELS)
    current_bin = cape_bin_series[0]

    if current_bin not in grouped.index:
        return "\n> ⚠️ 当前 CAPE 超出历史分组范围，无法匹配。\n"

    g = grouped.loc[current_bin]
    n = int(g["count"])

    mean_pct = (valid["excess_return_10y"] < g["mean"]).mean()

    if mean_pct >= 0.90:
        zone = "🟢 极度乐观（历史顶部）"
    elif mean_pct >= 0.75:
        zone = "🟢 显著乐观"
    elif mean_pct >= 0.50:
        zone = "🟡 中性偏好"
    elif mean_pct >= 0.25:
        zone = "🟠 中性偏差"
    elif mean_pct >= 0.10:
        zone = "🔴 长期回报预期偏低"
    else:
        zone = "🚨 历史罕见低回报区"

    sample_warning = f"\n> ⚠️ 当前 CAPE 区间（{current_bin}x）历史样本仅 {n} 个月，参考时请注意统计可靠性。" if n < 30 else ""

    block = f"""
---
### Shiller 长期回报锚（基于150年历史分组均值）

> 方法：将历史所有月份按 CAPE 区间分组，取当前 CAPE 所在组的实际回报分布。
> 当前 CAPE **{cape_now:.1f}x**，落入分组：**{current_bin}x**（共 {n} 个历史月份）
{sample_warning}

| 指标 | 数值 | 说明 |
|:-----|-----:|:-----|
| 当前 CAPE | **{cape_now:.1f}x** | Shiller 最新值 |
| 同区间历史均值 | **{g['mean']:.2%}** | **{zone}** |
| 均值的全历史分位 | **P{mean_pct*100:.0f}** | 历史 {mean_pct*100:.0f}% 时间比现在更悲观 |

| 同 CAPE 区间的历史回报分布 | 超额回报 |
|:--------------------------|--------:|
| P90（乐观情景） | {g['p90']:.2%} |
| P75 | {g['p75']:.2%} |
| P50（中位数） | {g['p50']:.2%} |
| P25 | {g['p25']:.2%} |
| P10（悲观情景） | {g['p10']:.2%} |
"""
    return block


def build_trend_block(df, erp_series, code, quantiles):
    """
    生成近10个有效 ERP 数据点的趋势模块。
    - 日频指数（A股/美股）取最近10个交易日
    - 月频指数（EWQ/EWG/EWJ/EEM/HSTECH）取最近10个月末
    HSTECH 额外展示 PSY 近期趋势。
    """
    monthly_codes = {'EWQ', 'EWG', 'EWJ', 'EEM', 'HSTECH'}

    recent = df[df['ERP'].notna()][['Date', 'ERP', 'PE', 'Bond_Yield_10Y']].tail(10).copy()
    if len(recent) < 2:
        return ""

    x = np.arange(len(recent))
    slope = np.polyfit(x, recent['ERP'].values, 1)[0]
    n_days = len(recent)
    span_label = "月" if code in monthly_codes else "交易日"

    if slope > 0.0005:
        trend_icon = "持续走高"
    elif slope < -0.0005:
        trend_icon = "持续走低"
    else:
        trend_icon = "基本横盘"

    delta = recent['ERP'].iloc[-1] - recent['ERP'].iloc[0]
    delta_str = f"+{delta:.2%}" if delta >= 0 else f"{delta:.2%}"

    rows = []
    prev_erp = None
    for _, row in recent.iterrows():
        erp_val = row['ERP']
        pe_val  = row['PE']

        if erp_val >= quantiles["P75"]:
            zone_icon = "🟢"
        elif erp_val >= quantiles["P50"]:
            zone_icon = "🟡"
        elif erp_val >= quantiles["P25"]:
            zone_icon = "🟠"
        else:
            zone_icon = "🔴"

        if prev_erp is not None:
            diff = erp_val - prev_erp
            arrow = f"▲{diff:.2%}" if diff > 0 else (f"▼{abs(diff):.2%}" if diff < 0 else "─")
        else:
            arrow = "─"
        prev_erp = erp_val

        date_str = row['Date'].strftime("%m-%d") if code not in monthly_codes else row['Date'].strftime("%Y-%m")
        pe_str   = f"{pe_val:.1f}x" if pd.notna(pe_val) else "N/A"
        rows.append(f"| {date_str} | {pe_str} | **{erp_val:.2%}** {zone_icon} | {arrow} |")

    rows_md = "\n".join(rows)

    # HSTECH 额外展示 PSY 趋势
    psy_section = ""
    if code == "HSTECH":
        ps_path = "./data/ps_HSTECH.csv"
        if os.path.exists(ps_path):
            ps_df = pd.read_csv(ps_path, index_col=0, parse_dates=True)
            if "psy" in ps_df.columns:
                recent_psy = ps_df[ps_df["psy"].notna()][["ps", "psy"]].tail(10)
                if len(recent_psy) >= 2:
                    psy_rows = []
                    prev_psy = None
                    for d, r in recent_psy.iterrows():
                        arrow = "─"
                        if prev_psy is not None:
                            diff = r["psy"] - prev_psy
                            arrow = f"▲{diff:.2%}" if diff > 0 else (f"▼{abs(diff):.2%}" if diff < 0 else "─")
                        prev_psy = r["psy"]
                        psy_rows.append(f"| {d.strftime('%Y-%m')} | {r['ps']:.2f}x | **{r['psy']:.2%}** | {arrow} |")
                    psy_section = f"""
#### PSY 近期趋势（营收口径）

| 月份 | PS | PSY | 环比 |
|:-----|---:|----:|:-----|
{chr(10).join(psy_rows)}
"""

    block = f"""
---
### 近{n_days}{span_label} ERP 趋势

> 趋势方向：**{trend_icon}**，区间变化：**{delta_str}**

| 日期 | PE | ERP | 环比变化 |
|:-----|---:|----:|:---------|
{rows_md}
{psy_section}"""
    return block


def send_to_wechat(content):
    sct_key = os.getenv("SCT_KEY")
    if not sct_key:
        print("⚠️ 未找到 SCT_KEY，推送跳过。")
        return
    url = f"https://sctapi.ftqq.com/{sct_key}.send"
    data = {
        "title": f"ERP 决策报告 ({datetime.now().strftime('%Y-%m-%d')})",
        "desp": content
    }
    try:
        res = requests.post(url, data=data)
        print(f"✅ 方糖推送结果: {res.text}")
    except Exception as e:
        print(f"❌ 推送失败: {e}")


def analyze_and_suggest(code, name, etf_df=None, summary_list=None):
    file_path = f"./data/erp_{code}.csv"
    if not os.path.exists(file_path):
        print(f"❌ 未找到 {name} ({code}) 的数据文件")
        return

    df = pd.read_csv(file_path)
    df['Date'] = pd.to_datetime(df['Date'])
    erp_series = df['ERP'].dropna()

    min_samples = 50 if code in ('SPY', 'QQQ', 'EWQ', 'EWG', 'EWJ', 'EEM', 'HSTECH') else 250
    if len(erp_series) < min_samples:
        print(f"\n⚠️ {name} ({code}) 有效样本不足 ({len(erp_series)} < {min_samples})，跳过分析。")
        return

    mean_erp = erp_series.mean()
    # 欧日美负利率指数，用2022年后数据做锚
    if code in ('EWQ', 'EWG', 'EWJ', 'SPY', 'QQQ'):
        anchor = df[df['Date'] >= pd.Timestamp('2022-01-01')]['ERP'].dropna()
        if len(anchor) >= 30:
            erp_series = anchor

    quantiles = {
        "P95": erp_series.quantile(0.95),
        "P90": erp_series.quantile(0.90),
        "P75": erp_series.quantile(0.75),
        "P50": erp_series.quantile(0.50),
        "P25": erp_series.quantile(0.25),
        "P10": erp_series.quantile(0.10),
    }

    current_erp = erp_series.iloc[-1]
    current_date = df['Date'].iloc[-1].date()

    if current_erp >= quantiles["P90"]:
        erp_zone = "🟢 极度低估 (>=P90)"
    elif current_erp >= quantiles["P75"]:
        erp_zone = "🟢 显著低估 (P75-P90)"
    elif current_erp >= quantiles["P50"]:
        erp_zone = "🟡 合理偏低 (P50-P75)"
    elif current_erp >= quantiles["P25"]:
        erp_zone = "🟠 合理区间 (P25-P50)"
    elif current_erp >= quantiles["P10"]:
        erp_zone = "🔴 严重高估 (P10-P25)"
    else:
        erp_zone = "🚨 危险泡沫 (<P10)"

    if current_erp >= quantiles["P50"]:
        b_msg, b_pct = "泡沫仓: 已进入相对便宜击球区，30% 底仓应长期锁定", 30
    elif current_erp >= quantiles["P25"]:
        b_msg, b_pct = "泡沫仓: 尚未达到远期目标价，底仓持有不动", 30
    else:
        b_msg, b_pct = "泡沫仓: 触发极致远期溢价，考虑收割最后的筹码", 5

    if current_erp >= quantiles["P75"]:
        v_msg, v_pct = "价值仓: 足够便宜的价格，40% 核心主力必须在场", 40
    elif current_erp >= quantiles["P50"]:
        v_msg, v_pct = "价值仓: 估值修复中，建议持有 30%-40% 主力仓位", 35
    elif current_erp >= quantiles["P25"]:
        v_msg, v_pct = "价值仓: 回到合理估值区间，开始减持主力仓位", 10
    else:
        v_msg, v_pct = "价值仓: 估值已高，价值段位应已全部离场", 0

    if current_erp >= quantiles["P95"]:
        t_msg, t_pct = "投机仓: 触发极端惯性下跌，30% 预备队全额出击", 30
    elif current_erp >= quantiles["P90"]:
        t_msg, t_pct = "投机仓: 极低估区，保持 20% 仓位积极做T降本", 20
    elif current_erp >= quantiles["P50"]:
        t_msg, t_pct = "投机仓: 震荡区间，维持 10% 灵活部做T", 10
    else:
        t_msg, t_pct = "投机仓: 溢价区基本只卖不买，缩减至 5% 观察", 5

    total_pct = v_pct + b_pct + t_pct

    # ── HSTECH 专属：PS / PSY 补充模块 ──────────────────────────────────────
    ps_block = ""
    if code == "HSTECH":
        ps_path = "./data/ps_HSTECH.csv"
        if os.path.exists(ps_path):
            ps_df = pd.read_csv(ps_path, index_col=0, parse_dates=True).dropna(subset=["ps"])
            if len(ps_df) >= 6:
                cur_ps  = ps_df["ps"].iloc[-1]
                ps_pct  = (ps_df["ps"] < cur_ps).mean()
                ps_zone = (
                    "🟢 极度低估 (历史低位)" if ps_pct <= 0.10 else
                    "🟢 显著低估"            if ps_pct <= 0.25 else
                    "🟡 合理偏低"            if ps_pct <= 0.50 else
                    "🟠 合理偏高"            if ps_pct <= 0.75 else
                    "🔴 严重高估"            if ps_pct <= 0.90 else
                    "🚨 危险泡沫 (历史高位)"
                )

                psy_rows = ""
                cur_psy = np.nan
                psy_zone = "N/A"
                psy_pct = np.nan
                if "psy" in ps_df.columns:
                    psy_s = ps_df["psy"].dropna()
                    if len(psy_s) >= 6:
                        cur_psy  = psy_s.iloc[-1]
                        cur_rf   = ps_df["rf"].iloc[-1] if "rf" in ps_df.columns else np.nan
                        psy_pct  = (psy_s < cur_psy).mean()
                        psy_zone = (
                            "🟢 极度低估" if psy_pct >= 0.90 else
                            "🟢 显著低估" if psy_pct >= 0.75 else
                            "🟡 合理偏低" if psy_pct >= 0.50 else
                            "🟠 合理偏高" if psy_pct >= 0.25 else
                            "🔴 严重高估" if psy_pct >= 0.10 else
                            "🚨 危险泡沫"
                        )
                        rf_str = f"{cur_rf:.2%}" if pd.notna(cur_rf) else "N/A"
                        psy_rows = f"""
| 当前 PSY | **{cur_psy:.2%}** | **{psy_zone}（历史{psy_pct*100:.0f}%分位）** |
| PSY 历史均值 | {psy_s.mean():.2%} | 无风险利率={rf_str} |
| PSY P75 | {psy_s.quantile(0.75):.2%} | 显著低估门槛 |
| PSY P25 | {psy_s.quantile(0.25):.2%} | 进入高估门槛 |"""

                ps_block = f"""
---
### PS / PSY 估值（恒生科技补充，基于营收口径）

| 指标 | 数值 | 估值区间 |
|:-----|-----:|:---------|
| 当前 PS | **{cur_ps:.2f}x** | **{ps_zone}（历史{ps_pct*100:.0f}%分位）** |
| PS 历史均值 | {ps_df["ps"].mean():.2f}x | 样本{len(ps_df)}个月 |
| PS 历史最低 | {ps_df["ps"].min():.2f}x | |
| PS 历史最高 | {ps_df["ps"].max():.2f}x |{psy_rows} |

> PSY = 1/PS − 中国10年期国债收益率，衡量营收口径下相对无风险利率的超额回报，适用于PE因亏损公司失真时的替代指标。
"""

    shiller_block   = build_shiller_block(code)
    trend_block     = build_trend_block(df, erp_series, code, quantiles)
    etf_block       = build_etf_metrics_block(code, etf_df)
    
    # 方案一：估值空间法（基于ERP分位）
    valuation_space_block = build_valuation_space_block(df, current_erp, code)
    
    # 方案二：历史回测法
    win_rate_block = build_win_rate_block(df, current_erp, code)
    
    # 方案三：整合评估方案
    integrated_block = build_integrated_block(df, current_erp, code)

    # ── 追加到顶部总览 ────────────────────────────────────────────────────────
    if summary_list is not None:
        summary_list.append({
            "name": name, "code": code,
            "erp_zone": erp_zone,
            "total_pct": total_pct,
            "b_pct": b_pct, "v_pct": v_pct, "t_pct": t_pct,
        })

    md = f"""## {name} ({code}) 决策报告
日期: {current_date}

| 指标 | 数值 | 估值区间 |
|:-----|-----:|:---------|
| 当前 ERP | **{current_erp:.2%}** | **{erp_zone}** |
| 历史均值 | {mean_erp:.2%} | {len(erp_series)}条样本 |

| 分位点 | ERP值 | 估值状态 |
|:-------|------:|:---------|
| P90 | {quantiles["P90"]:.2%} | 极度低估 |
| P75 | {quantiles["P75"]:.2%} | 显著低估 |
| P50 | {quantiles["P50"]:.2%} | 价值中枢 |
| P25 | {quantiles["P25"]:.2%} | 进入高估 |
| P10 | {quantiles["P10"]:.2%} | 极度高估 |
{valuation_space_block}{win_rate_block}{integrated_block}{trend_block}
---
### 仓位建议

**{b_msg}** ({b_pct}%)
**{v_msg}** ({v_pct}%)
**{t_msg}** ({t_pct}%)

建议总仓位：**{total_pct}%**（泡沫底仓 {b_pct}% + 价值主力 {v_pct}% + 投机奇兵 {t_pct}%）
{etf_block}{shiller_block}{ps_block}"""
    print(md)
    return md


if __name__ == "__main__":
    # 启动时加载一次 ETF 指标数据（失败不影响主流程）
    try:
        _etf_df = load_etf_metrics()
    except Exception as e:
        print(f"⚠️ ETF 指标加载意外失败：{e}，主报告继续运行。")
        _etf_df = None

    indices = [
        ("000300", "沪深300"),
        ("000688", "科创50"),
        ("000922", "中证红利"),
        ("399989", "中证医疗"),
        ("931071", "人工智能"),
        ("SPY",    "S&P 500"),
        ("QQQ",    "Nasdaq 100"),
        ("EWQ",    "MSCI France"),
        ("EWG",    "MSCI Germany"),
        ("EWJ",    "MSCI Japan"),
        ("EEM",    "MSCI Emerging"),
        ("HSTECH", "恒生科技指数"),
    ]

    summary_list = []
    report_list = []
    for code, name in indices:
        report_md = analyze_and_suggest(code, name, _etf_df, summary_list)
        if report_md:
            report_list.append(report_md)

    if report_list:
        full_report = (
            "# ERP 策略每日监控报告\n"
            + build_summary_block(summary_list)   # ← 总览在最前
            + LEGEND_BLOCK
            + "".join(report_list)
        )
        print("正在生成报告并准备推送...")
        send_to_wechat(full_report)
    else:
        print("❌ 未生成任何有效报告，请检查数据文件。")
