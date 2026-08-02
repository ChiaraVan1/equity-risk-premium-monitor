"""
analysis/trend.py
趋势分析模块：ERP斜率信号、月度趋势、整体趋势块
"""
import pandas as pd
import numpy as np
from scipy import stats


def compute_erp_slope_signal(erp_series: pd.Series) -> dict:
    """基于近20日ERP线性回归斜率，量化当前市场情绪速度。"""
    if len(erp_series) < 20:
        return {"signal": "数据不足", "slope": None, "change_pct": None}

    recent = erp_series.iloc[-20:].copy()
    x = np.arange(len(recent))
    y = recent.values

    # 线性回归
    slope, intercept, r_value, p_value, std_err = stats.linregress(x, y)

    # 20日变化百分比
    change_pct = (recent.iloc[-1] - recent.iloc[0]) / abs(recent.iloc[0]) if recent.iloc[0] != 0 else 0

    if change_pct >= 0.02:
        signal = "🚨 恐慌踩踏"
        description = "PE急速压缩，市场抛售"
    elif change_pct >= 0.008:
        signal = "🟢 估值快速改善"
        description = "估值持续修复"
    elif change_pct > -0.008:
        signal = "🟡 横盘震荡"
        description = "无明显趋势"
    elif change_pct > -0.02:
        signal = "🟠 估值快速恶化"
        description = "估值向贵漂移"
    else:
        signal = "⚠️ 情绪过热"
        description = "市场情绪升温"

    return {
        "signal": signal,
        "description": description,
        "change_pct": change_pct,
        "slope": slope,
    }


def build_trend_block(df, name, erp_series, code, quantiles):
    """生成趋势块。"""
    slope_info = compute_erp_slope_signal(erp_series)

    block = f"""
---
### 趋势分析

**{slope_info['signal']}** - {slope_info['description']}

近20日ERP变化：**{slope_info['change_pct']*100:+.2f}%**
"""
    return block


def build_monthly_trend_ai_block(name, code, monthly_rows, quantiles):
    """生成月度趋势AI分析块（简化版）。"""
    if not monthly_rows or len(monthly_rows) < 3:
        return ""

    recent_months = monthly_rows[-3:]
    
    block = f"""
---
### 月度趋势（近3个月）

"""
    for row in recent_months:
        block += f"- **{row.get('date', '─')}**: ERP {row.get('erp', 0):.2%} "
        block += f"(分位 {row.get('percentile', 0)*100:.0f}%)\n"
    
    return block
