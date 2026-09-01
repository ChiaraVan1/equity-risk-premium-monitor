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

def build_unified_valuation_block(df, code, val_series=None, win_rate=None, odds_ratio=None,
                                   metric_name="ERP", price_col="PE"):
    """生成统一估值分档区块（核心决策块）。

    【2026-08-02 恢复说明】此前版本只保留了估值分档表格，把原版里的
    「历史均值+样本数」「当前PE及历史均值/最高/最低」「综合评级」三块
    全部丢了；估值分档也从原版"按胜率分6档（含'严重高估'/'危险泡沫'独立档位）"
    简化成了"按原始值比大小分5档"。现按原始 erp_position.py（2160行版，
    commit e1b472e）里 build_unified_valuation_block() 的逻辑原样恢复。
    """
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

    if win_rate is None:
        win_rate = (val_series < current_val).mean()
    if odds_ratio is None:
        odds_ratio = calc_odds(current_val, val_series)

    # ── 估值分档：按胜率（历史分位）分6档，不是按原始值和阈值比大小 ──
    # 二者理论上接近但不完全等价（分位点本身也是估计值），原版用胜率分档，
    # 这里保持一致，避免"胜率58%却显示合理区间"这种口径不一致的观感。
    if win_rate >= 0.90:
        zone_icon, zone_name = "🟢", "极度低估"
    elif win_rate >= 0.75:
        zone_icon, zone_name = "🟢", "显著低估"
    elif win_rate >= 0.50:
        zone_icon, zone_name = "🟡", "合理偏低"
    elif win_rate >= 0.25:
        zone_icon, zone_name = "🟠", "合理区间"
    elif win_rate >= 0.10:
        zone_icon, zone_name = "🔴", "严重高估"
    else:
        zone_icon, zone_name = "🚨", "危险泡沫"
    zone = f"{zone_icon} {zone_name}"

    win_str = f"{win_rate*100:.0f}%" if win_rate is not None and win_rate == win_rate else "─"
    if odds_ratio is None:
        odds_str = "极高（已超P90极度低估区）"
    elif odds_ratio != odds_ratio:
        odds_str = "─"
    else:
        odds_str = f"{odds_ratio:.2f}x"

    p10_val = quantiles['P10']
    p90_val = quantiles['P90']
    upside = current_val - p10_val
    downside = p90_val - current_val

    # ── 综合评级 ──────────────────────────────────────────────────────
    if odds_ratio is None:
        rating = "🟢 极佳买点"
    elif odds_ratio == 0.0:
        rating = "🚨 已进入极度高估区，规避"
    elif win_rate is not None and win_rate == win_rate and win_rate >= 0.60 and odds_ratio >= 2.0:
        rating = "🟢 极佳机会 — 胜率>60% + 赔率>2"
    elif win_rate is not None and win_rate == win_rate and win_rate >= 0.50 and odds_ratio >= 1.0:
        rating = "🟡 可参与 — 胜率≥50% + 赔率≥1"
    else:
        rating = "🔴 不参与 — 胜率或赔率不达标"

    # ── 当前PE（或PS/其它价格倍数指标）的历史统计，找不到列时优雅跳过 ──
    pe_row = ""
    if df is not None and price_col in getattr(df, "columns", []):
        p_series = df[price_col].dropna()
        if len(p_series) > 0:
            pe_row = (f"| 当前 {price_col} | {p_series.iloc[-1]:.1f}x | 历史均值 {p_series.mean():.1f}x，"
                      f"最高 {p_series.max():.1f}x，最低 {p_series.min():.1f}x |\n")

    block = f"""
---
### 核心估值决策（基于 {metric_name} 框架）

<details>
<summary>**综合评级：{rating}**</summary>

> 胜率 = {metric_name}历史分位（越高代表当前越便宜）
> 赔率 = {metric_name}回落盈利空间（当前{metric_name} − P10） / {metric_name}走高亏损空间（P90 − 当前{metric_name}）
> 当前 {metric_name} = **{current_val:.2%}**，历史分位 = **{win_str}** {zone}

| 指标 | 数值 | 说明 |
|:-----|-----:|:-----|
| 历史均值 | {val_series.mean():.2%} | {len(val_series)}条样本 |
| **胜率** | **{win_str}** | 历史上 {win_str} 的时间比现在更贵（{metric_name}更低） |
| **赔率（盈亏比）** | **{odds_str}** | 盈利空间 {upside:.2%} / 亏损空间 {downside:.2%} |
{pe_row}
</details>
"""
    return block


# ══════════════════════════════════════════════════════════════════════
#  持仓状态
# ══════════════════════════════════════════════════════════════════════

def is_holding(code: str) -> bool:
    """检查该标的是否在持仓列表中。
    取真值。"""
    return bool(HOLDING_CATEGORY.get(code, False))
