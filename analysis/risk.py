"""
analysis/risk.py
风险分析模块：止损信号、止盈信号、回撤反弹统计
"""
import pandas as pd
import numpy as np

from config_loader import HOLDING_CATEGORY

# ══════════════════════════════════════════════════════════════════════
#  止损阈值配置（集中管理，方便调整）
# ══════════════════════════════════════════════════════════════════════

_DD_L1 = 0.10   # 第一级：回撤≥10% + 跌破MA20 → 减仓1/3
_DD_L2 = 0.15   # 第二级：回撤≥15% + 跌破MA60 → 减仓至底仓
_DD_L3 = 0.20   # 第三级：回撤≥20%            → 全清止损
_QQQ_SINGLE_DAY_DROP = 0.05  # QQQ单日急跌阈值

# ── 止盈阈值（止损的镜像逻辑）────────────────────────────────────────
_TP_L1 = 0.15   # 第一级：现价相对MA20乖离率 ≥15% → 止盈1/3
_TP_L2 = 0.25   # 第二级：现价相对MA60乖离率 ≥25% → 止盈至底仓
_TP_L3 = 0.40   # 第三级：现价相对MA20乖离率 ≥40% → 止盈过半（硬性，不因估值区间豁免）
_TP_OVERVALUED_PCTL = 0.25  # ERP/PSY历史分位 < 此值 视为"高估区"，触发动作升级


def compute_ma_stats(price_s: pd.Series):
    """计算MA20/MA60/MA120。样本不足时对应档位返回np.nan（不要求满窗口才有值，
    比如只有25天数据也能算出MA20，避免新标的/数据较短的标的止损止盈信号整体失效）。"""
    n = len(price_s)
    ma20 = price_s.iloc[-min(20, n):].mean() if n >= 5 else np.nan
    ma60 = price_s.iloc[-min(60, n):].mean() if n >= 20 else np.nan
    ma120 = price_s.iloc[-min(120, n):].mean() if n >= 30 else np.nan
    return ma20, ma60, ma120


# ══════════════════════════════════════════════════════════════════════
#  减仓 / 清仓信号
# ══════════════════════════════════════════════════════════════════════

def compute_exit_signal_summary(erp_code: str, current_erp_percentile: float,
                                 price_series=None, holding: bool = True) -> dict:
    """三级回撤止损(level 0-3)+QQQ单日急跌信号，返回结构化字典（含verdict文案和中间计算值，
    供build_exit_signal_block复用，避免重复算dd/MA）。

    设计原则：ERP框架（逆向估值）与均线/回撤（趋势跟踪）冲突时，用估值分位对趋势信号做
    "降级"而非简单屏蔽——低估区（current_erp_percentile>=0.5）触发L1/L2时降级处理，
    L3（回撤20%硬止损）无论估值区间均执行，不豁免。

    holding=False（未持仓）时不改变level计算（仍照常判断是否触线），但verdict文案改为
    "观察提示"而非"建议操作"——因为没有仓位可减，只是提醒你这个标的的价格结构已经很差。
    """
    price_s = price_series

    qqq_drop_note = ""
    if erp_code == "QQQ" and price_s is not None and len(price_s) >= 2:
        today_price = price_s.iloc[-1]
        prev_price = price_s.iloc[-2]
        single_day_chg = (today_price - prev_price) / prev_price
        if single_day_chg <= -_QQQ_SINGLE_DAY_DROP:
            drop_pct = single_day_chg * 100
            if current_erp_percentile < 0.50:
                qqq_drop_note = f"⚠️ QQQ单日急跌{drop_pct:.1f}%，估值偏贵，建议减仓1/3"
            else:
                qqq_drop_note = f"📢 QQQ单日急跌{drop_pct:.1f}%，但处于低估区，可能是加仓机会"

    if price_s is None or len(price_s) < 5:
        return {
            "level": -1, "verdict_icon": "─",
            "message": "─ 无ETF价格数据，跳过",
            "qqq_drop_note": qqq_drop_note, "has_data": False,
        }

    cur_price = price_s.iloc[-1]
    lookback = min(120, len(price_s))
    recent_high = price_s.iloc[-lookback:].max()
    dd = (cur_price - recent_high) / recent_high
    dd_pct = dd * 100

    ma20, ma60, ma120 = compute_ma_stats(price_s)

    below_ma20 = cur_price < ma20 if pd.notna(ma20) else False
    below_ma60 = cur_price < ma60 if pd.notna(ma60) else False

    in_undervalued = current_erp_percentile >= 0.50

    max_level = 0
    is_protected = False  # 低估区降级提示（未实际触发减仓）

    if dd <= -_DD_L1 and below_ma20:
        if in_undervalued:
            is_protected = True
        else:
            max_level = max(max_level, 1)

    if dd <= -_DD_L2 and below_ma60:
        if in_undervalued:
            max_level = max(max_level, 1)  # 低估区降级：L2→减至底仓，归为1级展示
        else:
            max_level = max(max_level, 2)

    if dd <= -_DD_L3:
        max_level = max(max_level, 3)

    if max_level == 0 and is_protected:
        verdict_icon = "🛡️"
        message = f"🛡️ 低估区保护 — 回撤{dd_pct:.1f}%触发条件但ERP低估，降级为观察提示"
    elif max_level == 0:
        verdict_icon = "✅"
        message = "✅ 无减仓信号 — 价格结构健康，持仓不动"
    elif not holding:
        # 未持仓：不建议"减仓/止损"这类操作动作，仅作观察提示，避免误导，
        # 但仍要让它出现在报告里——不因未持仓而整条隐藏。
        icon_map = {1: "🔎", 2: "🔎", 3: "🔎"}
        verdict_icon = icon_map[max_level]
        level_tag = "L3" if max_level == 3 else ("L2" if max_level == 2 else "L1")
        message = f"{verdict_icon} 未持仓观察 — 回撤{dd_pct:.1f}%达{level_tag}，无需操作"
    elif max_level == 1:
        if in_undervalued:
            verdict_icon = "⚠️"
            message = f"⚠️ 减仓预警（低估区降级）— 回撤{dd_pct:.1f}%，建议减至底仓，保留泡沫仓30%"
        else:
            verdict_icon = "⚠️"
            message = f"⚠️ 第一级减仓预警 — 回撤{dd_pct:.1f}%且跌破MA20，建议减持1/3仓位"
    elif max_level == 2:
        verdict_icon = "🔴"
        message = f"🔴 第二级清仓预警 — 回撤{dd_pct:.1f}%且跌破MA60，建议减至底仓（保留泡沫仓30%）"
    else:
        verdict_icon = "🚨"
        note = "（注：低估区但20%硬止损无豁免）" if in_undervalued else ""
        message = f"🚨 强制全清止损 — 回撤{dd_pct:.1f}%触及硬止损线，止损优先{note}"

    return {
        "level": max_level, "verdict_icon": verdict_icon,
        "message": message, "qqq_drop_note": qqq_drop_note,
        "has_data": True,
        "cur_price": cur_price, "recent_high": recent_high,
        "dd": dd, "dd_pct": dd_pct,
        "ma20": ma20, "ma60": ma60, "ma120": ma120,
        "below_ma20": below_ma20, "below_ma60": below_ma60,
    }


def build_exit_signal_block(erp_code: str, current_erp_percentile: float,
                             price_series=None, holding: bool = True) -> str:
    """止损信号详情区块（含表格），直接复用compute_exit_signal_summary算好的数值。"""
    summary = compute_exit_signal_summary(erp_code, current_erp_percentile, price_series, holding)

    qqq_drop_block = ""
    if erp_code == "QQQ" and summary.get("qqq_drop_note") and price_series is not None and len(price_series) >= 2:
        today_price = price_series.iloc[-1]
        prev_price = price_series.iloc[-2]
        drop_pct = (today_price - prev_price) / prev_price * 100
        qqq_drop_block = f"""
---
### QQQ 单日急跌信号

**{summary['qqq_drop_note']}**

| 今日收盘 | 昨日收盘 | 单日变化 | 阈值 |
|--------:|---------:|---------:|-----:|
| {today_price:.3f} | {prev_price:.3f} | **{drop_pct:.2f}%** | -{_QQQ_SINGLE_DAY_DROP*100:.0f}% |
"""

    if not summary["has_data"]:
        return qqq_drop_block + "\n> ⚠️ 减仓信号：无ETF价格数据，跳过。\n"

    cur_price = summary["cur_price"]
    recent_high = summary["recent_high"]
    dd = summary["dd"]
    dd_pct = summary["dd_pct"]
    ma20 = summary["ma20"]
    ma60 = summary["ma60"]
    ma120 = summary["ma120"]
    below_ma20 = summary["below_ma20"]
    below_ma60 = summary["below_ma60"]
    below_ma120 = cur_price < ma120 if pd.notna(ma120) else False

    in_undervalued = current_erp_percentile >= 0.50

    alerts = []
    if dd <= -_DD_L1 and below_ma20:
        if not holding:
            alerts.append(
                f"🔎 回撤 {dd_pct:.1f}%（≥{_DD_L1*100:.0f}%）且跌破MA20={ma20:.3f}"
                f"，但未持仓，仅供观察，无需操作"
            )
        elif in_undervalued:
            alerts.append(
                f"📢 回撤 {dd_pct:.1f}%（≥{_DD_L1*100:.0f}%）且跌破MA20={ma20:.3f}"
                f"，但ERP处于低估区（{current_erp_percentile:.0%}分位），建议持有观察而非减仓"
            )
        else:
            alerts.append(
                f"回撤 {dd_pct:.1f}%（≥{_DD_L1*100:.0f}%）且跌破MA20={ma20:.3f}"
                f" → 建议减持1/3仓位"
            )

    if dd <= -_DD_L2 and below_ma60:
        if not holding:
            alerts.append(
                (f"🔎 回撤 {dd_pct:.1f}%（≥{_DD_L2*100:.0f}%）且跌破MA60={ma60:.3f}"
                 f"，但未持仓，仅供观察，无需操作")
                if pd.notna(ma60) else
                f"🔎 回撤 {dd_pct:.1f}%（≥{_DD_L2*100:.0f}%）且跌破MA60，但未持仓，仅供观察"
            )
        elif in_undervalued:
            alerts.append(
                (f"⚠️ 回撤 {dd_pct:.1f}%（≥{_DD_L2*100:.0f}%）且跌破MA60={ma60:.3f}"
                 f"，低估区降级处理：建议减仓至底仓（保留泡沫仓30%），而非全清")
                if pd.notna(ma60) else
                f"⚠️ 回撤 {dd_pct:.1f}%（≥{_DD_L2*100:.0f}%）且跌破MA60，低估区降级：减至底仓"
            )
        else:
            alerts.append(
                (f"回撤 {dd_pct:.1f}%（≥{_DD_L2*100:.0f}%）且跌破MA60={ma60:.3f}"
                 f" → 趋势破坏，减至底仓（只保留泡沫仓30%）")
                if pd.notna(ma60) else
                f"回撤 {dd_pct:.1f}%（≥{_DD_L2*100:.0f}%），减至底仓"
            )

    if dd <= -_DD_L3:
        if not holding:
            alerts.append(
                f"🔎 回撤 {dd_pct:.1f}%（≥{_DD_L3*100:.0f}%），已达硬止损阈值"
                f"，但未持仓，仅供观察，无需操作；若后续建仓需重新评估"
            )
        else:
            note = "（注：当前为低估区，但20%是硬止损，判断失误须认错）" if in_undervalued else ""
            alerts.append(
                f"🚨 回撤 {dd_pct:.1f}%（≥{_DD_L3*100:.0f}%），触发强制止损线"
                f"{note} → 全部清仓，止损优先"
            )

    level_line = summary["message"]

    ma20_str = f"{ma20:.3f}" if pd.notna(ma20) else "─"
    ma60_str = f"{ma60:.3f}" if pd.notna(ma60) else "─"
    ma120_str = f"{ma120:.3f}" if pd.notna(ma120) else "─"

    def dd_status(threshold, actual_dd):
        return f"🔴 ≥{threshold*100:.0f}%" if actual_dd <= -threshold else f"✅ <{threshold*100:.0f}%"

    alerts_md = "\n".join(f"  - {a}" for a in alerts) if alerts else "  - 无"

    erp_zone_label = f"低估区（{current_erp_percentile:.0%}分位，ERP≥P50）" if in_undervalued \
        else f"高估区（{current_erp_percentile:.0%}分位，ERP<P50）"

    holding_note = "" if holding else "\n> 🔎 当前标记为**未持仓**：以下所有信号仅为观察提示，不代表需要操作。\n"

    exit_block = f"""
---
### 减仓 / 清仓信号
{holding_note}
> ERP区间：**{erp_zone_label}**
> 阈值：L1 回撤{_DD_L1*100:.0f}%+MA20 → 减1/3 · L2 回撤{_DD_L2*100:.0f}%+MA60 → 减至底仓 · L3 回撤{_DD_L3*100:.0f}% → 全清（硬止损）
> 低估区时L1/L2降级处理，L3硬止损无论何种情况均执行

{level_line}

| 指标 | 数值 | 状态 |
|:-----|-----:|:-----|
| 当前价格 | {cur_price:.3f} | ─ |
| 近期最高（120日内） | {recent_high:.3f} | ─ |
| 从高点回撤 | **{dd_pct:.1f}%** | {dd_status(_DD_L3, dd)} |
| MA20 | {ma20_str} | {"🔴 跌破" if below_ma20 else "✅ 站上"} |
| MA60 | {ma60_str} | {"🔴 跌破" if below_ma60 else "✅ 站上"} |
| MA120 | {ma120_str} | {"🔴 跌破" if below_ma120 else "✅ 站上"} |

**触发条件：**
{alerts_md}

> ⚠️ 基本面暴雷属于独立预警，见下方「基本面预警」模块。
"""
    return qqq_drop_block + exit_block


# ══════════════════════════════════════════════════════════════════════
#  止盈信号模块（减仓信号的镜像：乖离率过热 → 逐级止盈）
# ══════════════════════════════════════════════════════════════════════

def compute_profit_signal_summary(erp_code: str, current_erp_percentile: float, price_series=None) -> dict:
    """止盈信号，镜像compute_exit_signal_summary：用乖离率代替回撤。高估区时L1/L2动作升级。"""
    price_s = price_series

    if price_s is None or len(price_s) < 20:
        return {
            "level": -1, "verdict_icon": "─",
            "message": "─ 无ETF价格数据，跳过", "has_data": False,
        }

    cur_price = price_s.iloc[-1]
    ma20, ma60, _ = compute_ma_stats(price_s)

    dev20 = (cur_price - ma20) / ma20 if pd.notna(ma20) and ma20 != 0 else np.nan
    dev60 = (cur_price - ma60) / ma60 if pd.notna(ma60) and ma60 != 0 else np.nan

    is_overvalued = current_erp_percentile < _TP_OVERVALUED_PCTL

    base_level = 0
    if pd.notna(dev20) and dev20 >= _TP_L1:
        base_level = max(base_level, 1)
    if pd.notna(dev60) and dev60 >= _TP_L2:
        base_level = max(base_level, 2)
    if pd.notna(dev20) and dev20 >= _TP_L3:
        base_level = max(base_level, 3)

    # 高估区升级：L1→L2，L2→L3；L3本身已是最高级，始终执行不豁免
    final_level = base_level
    escalated = False
    if is_overvalued and base_level in (1, 2):
        final_level = base_level + 1
        escalated = True

    dev20_pct = dev20 * 100 if pd.notna(dev20) else float("nan")

    if final_level == 0:
        verdict_icon = "✅"
        message = "✅ 无止盈信号 — 未出现明显乖离过热"
    elif final_level == 1:
        verdict_icon = "💰"
        message = f"💰 第一级止盈提示 — 现价相对MA20乖离{dev20_pct:.1f}%，建议止盈1/3锁定收益"
    elif final_level == 2:
        esc_note = f"（乖离{_TP_L1*100:.0f}%触发但估值已处高估区，升级处理）" if escalated else ""
        verdict_icon = "💰"
        message = f"💰 第二级止盈提示 — 乖离过热{esc_note}，建议止盈至底仓"
    else:
        if escalated:
            verdict_icon = "🟨"
            message = ("🟨 第三级强止盈（高估区升级）— L2乖离过热触发，估值处于高估区，"
                        "动作升级为止盈过半仓位")
        else:
            verdict_icon = "🟨"
            message = (f"🟨 第三级强止盈 — 乖离{dev20_pct:.1f}%（≥{_TP_L3*100:.0f}%极端过热，硬性阈值），"
                        "建议止盈过半仓位，不因估值区间豁免")

    return {
        "level": final_level, "verdict_icon": verdict_icon,
        "message": message, "has_data": True,
        "cur_price": cur_price, "ma20": ma20, "ma60": ma60,
        "dev20": dev20, "dev60": dev60,
    }


def build_profit_signal_block(erp_code: str, current_erp_percentile: float, price_series=None) -> str:
    """止盈信号详情区块，直接复用compute_profit_signal_summary算好的数值。"""
    summary = compute_profit_signal_summary(erp_code, current_erp_percentile, price_series)

    if not summary["has_data"]:
        return "\n> ⚠️ 止盈信号：无ETF价格数据，跳过。\n"

    cur_price = summary["cur_price"]
    ma20 = summary["ma20"]
    ma60 = summary["ma60"]
    dev20 = summary["dev20"]
    dev60 = summary["dev60"]

    is_overvalued = current_erp_percentile < _TP_OVERVALUED_PCTL

    def dev_status(threshold, actual_dev):
        if pd.notna(actual_dev) and actual_dev >= threshold:
            return f"🔴 ≥{threshold*100:.0f}%"
        return f"✅ <{threshold*100:.0f}%"

    dev20_str = f"{dev20:+.1%}" if pd.notna(dev20) else "─"
    dev60_str = f"{dev60:+.1%}" if pd.notna(dev60) else "─"
    ma20_str = f"{ma20:.3f}" if pd.notna(ma20) else "─"
    ma60_str = f"{ma60:.3f}" if pd.notna(ma60) else "─"

    valuation_label = f"高估区（{current_erp_percentile:.0%}分位，触发动作升级）" if is_overvalued \
        else f"非高估区（{current_erp_percentile:.0%}分位）"

    profit_block = f"""
---
### 止盈信号

> 估值区间：**{valuation_label}**
> 阈值：L1 乖离MA20≥{_TP_L1*100:.0f}% → 止盈1/3 · L2 乖离MA60≥{_TP_L2*100:.0f}% → 止盈至底仓 · L3 乖离MA20≥{_TP_L3*100:.0f}% → 止盈过半（硬性）
> 高估区时L1/L2动作升一级，L3无论估值区间均执行

{summary['message']}

| 指标 | 数值 | 状态 |
|:-----|-----:|:-----|
| 当前价格 | {cur_price:.3f} | ─ |
| MA20 | {ma20_str} | 乖离 {dev20_str}｜{dev_status(_TP_L1, dev20)} |
| MA60 | {ma60_str} | 乖离 {dev60_str}｜{dev_status(_TP_L2, dev60)} |

> 💡 止盈信号与减仓/止损信号相互独立：止盈针对"涨多了要不要落袋"，止损针对"跌多了要不要认赔"。两者可能同时不触发，也可能未来在剧烈震荡中先后触发。
"""
    return profit_block


# ══════════════════════════════════════════════════════════════════════
#  回撤反弹统计
# ══════════════════════════════════════════════════════════════════════

def compute_range_drawdown_rebound(erp_code: str, price_series=None, lookback: int = 120) -> dict:
    """120天窗口内：峰值→峰值之后的谷值回撤 + 谷值→现在反弹。
    谷值必须发生在峰值之后（严格按时间先后顺序），否则不构成真正的"回撤"——
    如果谷值早于峰值，那衡量的是涨幅而非回撤，方向会反。
    返回None表示无价格数据或样本不足。"""
    if price_series is None or len(price_series) < 5:
        return None

    window = price_series.iloc[-min(lookback, len(price_series)):]
    peak_pos = window.values.argmax()
    high = window.iloc[peak_pos]
    post_peak = window.iloc[peak_pos:]
    low = post_peak.min()
    cur = price_series.iloc[-1]

    dd_high_to_low = (low - high) / high if high != 0 else float("nan")
    rebound_from_low = (cur - low) / low if low != 0 else float("nan")

    return {
        "high": high, "low": low, "cur": cur,
        "dd": dd_high_to_low, "rebound": rebound_from_low,
    }
