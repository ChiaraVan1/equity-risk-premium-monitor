"""
analysis/valuation.py
估值分析模块：Shiller CAPE、股息率、赔率、统一估值块
"""
import pandas as pd
import numpy as np

from config_loader import HOLDING_CATEGORY


# ══════════════════════════════════════════════════════════════════════
#  赔率计算
# ══════════════════════════════════════════════════════════════════════

def calc_odds(cur_val, val_series):
    """赔率=盈利空间(cur-P10)/亏损空间(P90-cur)。val_series需是"越高越便宜"的指标。"""
    p10_val = val_series.quantile(0.10)
    p90_val = val_series.quantile(0.90)

    upside   = cur_val - p10_val
    downside = p90_val - cur_val

    if downside <= 0:
        return None
    elif upside <= 0:
        return 0.0
    else:
        return upside / downside


# ══════════════════════════════════════════════════════════════════════
#  Shiller CAPE 分析块
# ══════════════════════════════════════════════════════════════════════

_CAPE_BINS   = [0,  10,  15,  20,  25,  30,  35,  40,  999]
_CAPE_LABELS = ['<10', '10-15', '15-20', '20-25', '25-30', '30-35', '35-40', '>40']


def build_shiller_block(code, shiller_data):
    """Shiller CAPE长期回报锚区块（仅SPY），和ERP框架互补，不是替代。"""
    if code != "SPY":
        return ""

    grouped, valid, cape_now = shiller_data
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


# ══════════════════════════════════════════════════════════════════════
#  统一估值块
# ══════════════════════════════════════════════════════════════════════

def build_unified_valuation_block(df, code, val_series=None, win_rate=None, odds_ratio=None):
    """生成统一估值分档区块（核心决策块）。"""
    if val_series is None or len(val_series) == 0:
        return ""

    current_val = val_series.iloc[-1]
    quantiles = {
        'P10': val_series.quantile(0.10),
        'P25': val_series.quantile(0.25),
        'P50': val_series.quantile(0.50),
        'P75': val_series.quantile(0.75),
        'P90': val_series.quantile(0.90),
    }

    if current_val >= quantiles['P90']:
        zone = "🟢 极度低估"
    elif current_val >= quantiles['P75']:
        zone = "🟢 显著低估"
    elif current_val >= quantiles['P50']:
        zone = "🟡 合理偏低"
    elif current_val >= quantiles['P25']:
        zone = "🟠 合理区间"
    else:
        zone = "🚨 危险泡沫"

    win_str = f"{win_rate*100:.0f}%" if win_rate is not None and win_rate == win_rate else "─"
    odds_str = f"{odds_ratio:.2f}x" if odds_ratio is not None and odds_ratio == odds_ratio else "─"
    if odds_ratio is None or odds_ratio != odds_ratio or odds_ratio > 100:
        odds_str = "∞"

    block = f"""
---
### 核心估值决策

**{zone}**

| 指标 | 当前值 | 历史分位 |
|:-----|------:|--------:|
| ERP | {current_val:.2%} | **{(val_series < current_val).mean()*100:.0f}%** |
| 胜率 | **{win_str}** | （历史该位置胜率） |
| 赔率 | **{odds_str}** | （盈利/亏损比） |

| 估值分档 | ERP阈值 |
|:--------|--------:|
| P90 极度低估 | > {quantiles['P90']:.2%} |
| P75 显著低估 | > {quantiles['P75']:.2%} |
| P50 中位 | > {quantiles['P50']:.2%} |
| P25 高估 | > {quantiles['P25']:.2%} |
| P10 泡沫 | ≤ {quantiles['P10']:.2%} |
"""
    return block


# ══════════════════════════════════════════════════════════════════════
#  持仓状态
# ══════════════════════════════════════════════════════════════════════

def is_holding(code: str) -> bool:
    """检查该标的是否在持仓列表中。
    【2026-08-02 修复】config.json 里 holding 字段本身就是布尔值 True/False，
    不是字符串"持仓"——旧代码 `== "持仓"` 恒为 False，导致 📌 自选标记和
    「🚨 需要处理」置顶区永远判定为"未持仓"，整块功能失效。改为直接取真值。"""
    return bool(HOLDING_CATEGORY.get(code, False))
