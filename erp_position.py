import pandas as pd
import numpy as np
import os
import time
from datetime import datetime
import requests
import akshare as ak

from etf_metrics import load_etf_metrics, build_etf_metrics_block, ERP_TO_ETF
from popularity_signal import build_popularity_block, compute_popularity_confirmation

# ══════════════════════════════════════════════════════════════════════
#  常量 & 配置
# ══════════════════════════════════════════════════════════════════════

SHILLER_PATH = os.getenv("SHILLER_PATH", "./data/ie_data.xls")

_CAPE_BINS   = [0,  10,  15,  20,  25,  30,  35,  40,  999]
_CAPE_LABELS = ['<10', '10-15', '15-20', '20-25', '25-30', '30-35', '35-40', '>40']

# ── 减仓阈值配置（集中管理，方便调整）────────────────────────────────
_DD_L1 = 0.10   # 第一级：回撤≥10% + 跌破MA20 → 减仓1/3
_DD_L2 = 0.15   # 第二级：回撤≥15% + 跌破MA60 → 减仓至底仓
_DD_L3 = 0.20   # 第三级：回撤≥20%            → 全清止损
_QQQ_SINGLE_DAY_DROP = 0.05  # QQQ单日急跌阈值

# ── API 重试 / 限流配置（集中管理）──────────────────────────────────
_API_MAX_RETRIES = 3
_API_RETRY_BASE_DELAY = 5     # 秒，每次重试翻倍：5s, 10s, 20s
_API_CALL_MIN_INTERVAL = 2    # 秒，连续两次AI调用之间的最小间隔

_SERPER_MAX_RETRIES = 2
_SERPER_RETRY_BASE_DELAY = 3  # 秒，每次重试翻倍：3s, 6s

_last_api_call_ts = {"t": 0.0}


# ══════════════════════════════════════════════════════════════════════
#  Shiller CAPE 长期回报锚模块
# ══════════════════════════════════════════════════════════════════════

_shiller_cache = {}


def _load_shiller():
    """读取并缓存Shiller CAPE数据，按CAPE区间分组预算历史回报统计（仅SPY用）。"""
    if _shiller_cache:
        return _shiller_cache["grouped"], _shiller_cache["valid"], _shiller_cache["cape_now"]

    if not os.path.exists(SHILLER_PATH):
        return None, None, None

    df = pd.read_excel(SHILLER_PATH, engine="xlrd", sheet_name="Data",
                       header=7, skiprows=[8])
    df.columns = df.columns.str.strip()
    df = df.rename(columns={
        "CAPE":      "cape",
        "Returns.2": "excess_return_10y",
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
    """Shiller CAPE长期回报锚区块（仅SPY），和ERP框架互补，不是替代。"""
    if code != "SPY":
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


# ══════════════════════════════════════════════════════════════════════
#  HSTECH PS / PSY 模块
# ══════════════════════════════════════════════════════════════════════

def load_ps_data():
    """读取HSTECH专用PS/PSY数据（港股科技盈利波动大，PE失真，改用PS口径）。"""
    ps_path = "./data/ps_HSTECH.csv"
    if not os.path.exists(ps_path):
        return None
    df = pd.read_csv(ps_path, index_col=0, parse_dates=True)
    return df


# ══════════════════════════════════════════════════════════════════════
#  赔率计算（ERP绝对值法）
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
#  数据新鲜度校验模块（PE/PS 连续N个更新点数值未变 → 预警）
#  用途：抓取失败后 process_incremental() 里的 ffill 会静默用旧值补今天，
#  或人工填入的 QQQ_PE_TODAY / HS_TECH_PE_TODAY 忘记更新，
#  两种情况都会让数值"看起来正常"但其实已经停止更新，靠此模块兜底识别。
# ══════════════════════════════════════════════════════════════════════

FRESHNESS_STALE_POINTS = 3  # 连续N个数据点（非日历天，含隐式ffill重复值）数值不变即判定为"未更新"


def check_metric_freshness(value_series: pd.Series, stale_points: int = FRESHNESS_STALE_POINTS) -> dict:
    """
    检测 PE/PS 序列是否连续 stale_points 个最新数据点数值完全未变化。
    入参：已去除 NaN、按日期升序排列、index 为日期的 Series（如 df.set_index('Date')['PE']）。
    返回：{is_stale, unchanged_count, last_value, last_date, first_unchanged_date}
    """
    s = value_series.dropna()
    if len(s) == 0:
        return {"is_stale": False, "unchanged_count": 0, "last_value": None,
                "last_date": None, "first_unchanged_date": None}

    last_value = s.iloc[-1]
    unchanged_count = 1
    for v in s.iloc[-2::-1]:
        if v == last_value:
            unchanged_count += 1
        else:
            break

    tail_dates = s.index[-unchanged_count:]
    return {
        "is_stale": unchanged_count >= stale_points,
        "unchanged_count": unchanged_count,
        "last_value": last_value,
        "last_date": s.index[-1],
        "first_unchanged_date": tail_dates[0] if len(tail_dates) else None,
    }


def build_freshness_note(freshness: dict, metric_name: str) -> str:
    """把 check_metric_freshness 的结果转成一行提示文案，非预警状态返回空字符串。"""
    if not freshness.get("is_stale"):
        return ""
    n = freshness["unchanged_count"]
    since = freshness["first_unchanged_date"]
    since_str = since.strftime("%Y-%m-%d") if hasattr(since, "strftime") else str(since)
    val = freshness["last_value"]
    return (f"⚠️ **新鲜度预警**：{metric_name} 已连续 {n} 个更新周期数值未变化"
            f"（自 {since_str} 起固定在 {val:.2f}），请检查数据源/手动填值是否失效。")


# ══════════════════════════════════════════════════════════════════════
#  ERP 斜率信号模块
# ══════════════════════════════════════════════════════════════════════

_SLOPE_EXTREME_THRESHOLD = 0.02
_SLOPE_MIN_HISTORY = 60          # 自适应阈值所需的最少历史"20日变化"样本数
_SLOPE_EXTREME_PCTL = 0.90       # 历史分位阈值：|delta| 落在历史 90 分位之外视为极端
_SLOPE_MODERATE_PCTL = 0.65      # 历史分位阈值：60% 处视为"快速改善/恶化"


def compute_erp_slope_signal(erp_series: pd.Series) -> dict:
    """近20日ERP变化的斜率信号（🚨🟢🟡🟠⚠️五档）。阈值改为自适应历史分位，而非固定2%——
    因ERP=1/PE压缩性，高PE标的固定阈值会永远触发不了；样本不足60条时退回固定阈值。"""
    if len(erp_series) < 21:
        return {"slope_20d": np.nan, "delta_20d": np.nan,
                "signal": "数据不足", "signal_icon": "─", "desc": ""}

    recent = erp_series.dropna().iloc[-21:]
    x = np.arange(len(recent))
    slope = np.polyfit(x, recent.values, 1)[0]
    delta = recent.iloc[-1] - recent.iloc[0]

    # ── 自适应阈值：用该标的自己历史上所有"20日变化"的分布来判断当前变化是否极端 ──
    # 原因：ERP = 1/PE − rf 是倒数关系，在高PE区间（如百倍PE的题材股）天然压缩，
    # 固定的绝对阈值（如2个百分点）对低PE标的合适，但对高PE标的可能永远触发不了。
    # 改用"当前变化在该标的自己历史分布中的分位"，同一套代码逻辑对所有标的自适应。
    all_deltas = erp_series.dropna().diff(20).dropna()

    if len(all_deltas) >= _SLOPE_MIN_HISTORY:
        pos_pctl = (all_deltas < delta).mean()   # delta 在历史分布中的分位（越高=越罕见地大）
        neg_pctl = (all_deltas > delta).mean()   # delta 在历史分布中的分位（越高=越罕见地小/负）
        abs_extreme  = pos_pctl >= _SLOPE_EXTREME_PCTL
        abs_moderate = pos_pctl >= _SLOPE_MODERATE_PCTL
        neg_extreme  = neg_pctl >= _SLOPE_EXTREME_PCTL
        neg_moderate = neg_pctl >= _SLOPE_MODERATE_PCTL
        pctl_note = f"（历史分位 P{pos_pctl*100:.0f}）"
    else:
        # 历史样本不足时退回固定阈值，避免分位数无意义
        abs_extreme  = delta >= _SLOPE_EXTREME_THRESHOLD
        abs_moderate = delta >= _SLOPE_EXTREME_THRESHOLD * 0.4
        neg_extreme  = delta <= -_SLOPE_EXTREME_THRESHOLD
        neg_moderate = delta <= -_SLOPE_EXTREME_THRESHOLD * 0.4
        pctl_note = "（历史样本不足，用固定阈值）"

    if abs_extreme:
        signal, signal_icon = "恐慌踩踏", "🚨"
        desc = (f"近20日ERP急速飙升 {delta:.2%}（斜率 {slope*100:.3f}%/日）{pctl_note}"
                "— 市场处于恐慌抛售期，PE急速压缩，历史上往往是买点临近的强化信号，但需警惕基本面是否同步恶化。")
    elif abs_moderate:
        signal, signal_icon = "估值快速改善", "🟢"
        desc = (f"近20日ERP持续走高 {delta:.2%}（斜率 {slope*100:.3f}%/日）{pctl_note}"
                "— 估值快速修复，买入窗口正在打开。")
    elif neg_extreme:
        signal, signal_icon = "情绪过热", "⚠️"
        desc = (f"近20日ERP急速坠落 {delta:.2%}（斜率 {slope*100:.3f}%/日）{pctl_note}"
                "— 市场情绪快速升温，估值泡沫化加速，警戒高位。")
    elif neg_moderate:
        signal, signal_icon = "估值快速恶化", "🟠"
        desc = (f"近20日ERP持续走低 {delta:.2%}（斜率 {slope*100:.3f}%/日）{pctl_note}"
                "— 估值向贵的方向漂移，需提高警惕。")
    else:
        signal, signal_icon = "横盘震荡", "🟡"
        desc = (f"近20日ERP变化 {delta:+.2%}（斜率 {slope*100:.3f}%/日）{pctl_note}"
                "— 估值无明显趋势，保持既有仓位。")

    return {"slope_20d": slope, "delta_20d": delta,
            "signal": signal, "signal_icon": signal_icon, "desc": desc}


# ══════════════════════════════════════════════════════════════════════
#  减仓信号模块
# ══════════════════════════════════════════════════════════════════════

ETF_PRICE_PATH = "./etf_price.csv"
_ETF_PRICE_CACHE = {}


def _load_etf_price_series(erp_code: str):
    """读取标的价格序列（全局缓存，避免重复读CSV），无数据返回None。"""
    global _ETF_PRICE_CACHE
    if "df" not in _ETF_PRICE_CACHE:
        if not os.path.exists(ETF_PRICE_PATH):
            return None
        try:
            df = pd.read_csv(ETF_PRICE_PATH, index_col=0, parse_dates=True)
            _ETF_PRICE_CACHE["df"] = df
        except Exception:
            return None
    df = _ETF_PRICE_CACHE.get("df")
    if df is None or erp_code not in df.columns:
        return None
    return df[erp_code].dropna()


def _compute_ma_stats(price_s: pd.Series):
    """计算MA20/MA60/MA120，供止损/止盈信号共用（样本不足返回np.nan）。"""
    n = len(price_s)
    ma20  = price_s.iloc[-min(20,  n):].mean() if n >= 5  else np.nan
    ma60  = price_s.iloc[-min(60,  n):].mean() if n >= 20 else np.nan
    ma120 = price_s.iloc[-min(120, n):].mean() if n >= 30 else np.nan
    return ma20, ma60, ma120


def compute_exit_signal_summary(erp_code: str, current_erp_percentile: float) -> dict:
    """三级回撤止损(level 0-3)+QQQ单日急跌信号，返回结构化字典（含verdict文案和中间计算值，
    供build_exit_signal_block复用，避免重复算dd/MA）。"""
    price_s = _load_etf_price_series(erp_code)

    qqq_drop_note = ""
    if erp_code == "QQQ" and price_s is not None and len(price_s) >= 2:
        today_price = price_s.iloc[-1]
        prev_price  = price_s.iloc[-2]
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
            "verdict_line": "─ 无ETF价格数据，跳过",
            "qqq_drop_note": qqq_drop_note, "has_data": False,
        }

    cur_price   = price_s.iloc[-1]
    lookback    = min(120, len(price_s))
    recent_high = price_s.iloc[-lookback:].max()
    dd          = (cur_price - recent_high) / recent_high
    dd_pct      = dd * 100

    ma20, ma60, ma120 = _compute_ma_stats(price_s)

    below_ma20 = cur_price < ma20 if pd.notna(ma20) else False
    below_ma60 = cur_price < ma60 if pd.notna(ma60) else False

    in_undervalued = current_erp_percentile >= 0.50

    max_level    = 0
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
        verdict_line = f"🛡️ 低估区保护 — 回撤{dd_pct:.1f}%触发条件但ERP低估，降级为观察提示"
    elif max_level == 0:
        verdict_icon = "✅"
        verdict_line = "✅ 无减仓信号 — 价格结构健康，持仓不动"
    elif max_level == 1:
        if in_undervalued:
            verdict_icon = "⚠️"
            verdict_line = f"⚠️ 减仓预警（低估区降级）— 回撤{dd_pct:.1f}%，建议减至底仓，保留泡沫仓30%"
        else:
            verdict_icon = "⚠️"
            verdict_line = f"⚠️ 第一级减仓预警 — 回撤{dd_pct:.1f}%且跌破MA20，建议减持1/3仓位"
    elif max_level == 2:
        verdict_icon = "🔴"
        verdict_line = f"🔴 第二级清仓预警 — 回撤{dd_pct:.1f}%且跌破MA60，建议减至底仓（保留泡沫仓30%）"
    else:
        verdict_icon = "🚨"
        verdict_line = f"🚨 强制全清止损 — 回撤{dd_pct:.1f}%触及硬止损线，止损优先{'（注：低估区但20%硬止损无豁免）' if in_undervalued else ''}"

    return {
        "level": max_level, "verdict_icon": verdict_icon,
        "verdict_line": verdict_line, "qqq_drop_note": qqq_drop_note,
        "has_data": True,
        "cur_price": cur_price, "recent_high": recent_high,
        "dd": dd, "dd_pct": dd_pct,
        "ma20": ma20, "ma60": ma60, "ma120": ma120,
        "below_ma20": below_ma20, "below_ma60": below_ma60,
    }


def build_exit_signal_block(erp_code: str, current_erp_percentile: float) -> str:
    """止损信号详情区块（含表格），直接复用compute_exit_signal_summary算好的数值。"""
    summary = compute_exit_signal_summary(erp_code, current_erp_percentile)

    qqq_drop_block = ""
    if erp_code == "QQQ" and summary["qqq_drop_note"]:
        price_s = _load_etf_price_series(erp_code)
        if price_s is not None and len(price_s) >= 2:
            today_price = price_s.iloc[-1]
            prev_price  = price_s.iloc[-2]
            drop_pct    = (today_price - prev_price) / prev_price * 100
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

    cur_price   = summary["cur_price"]
    recent_high = summary["recent_high"]
    dd          = summary["dd"]
    dd_pct      = summary["dd_pct"]
    ma20        = summary["ma20"]
    ma60        = summary["ma60"]
    ma120       = summary["ma120"]
    below_ma20  = summary["below_ma20"]
    below_ma60  = summary["below_ma60"]
    below_ma120 = cur_price < ma120 if pd.notna(ma120) else False

    in_undervalued = current_erp_percentile >= 0.50

    alerts = []
    if dd <= -_DD_L1 and below_ma20:
        if in_undervalued:
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
        if in_undervalued:
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
        alerts.append(
            f"🚨 回撤 {dd_pct:.1f}%（≥{_DD_L3*100:.0f}%），触发强制止损线"
            f"{'（注：当前为低估区，但20%是硬止损，判断失误须认错）' if in_undervalued else ''}"
            f" → 全部清仓，止损优先"
        )

    level_line = summary["verdict_line"]

    ma20_str  = f"{ma20:.3f}"  if pd.notna(ma20)  else "─"
    ma60_str  = f"{ma60:.3f}"  if pd.notna(ma60)  else "─"
    ma120_str = f"{ma120:.3f}" if pd.notna(ma120) else "─"

    def dd_status(threshold, actual_dd):
        return f"🔴 ≥{threshold*100:.0f}%" if actual_dd <= -threshold else f"✅ <{threshold*100:.0f}%"

    alerts_md = "\n".join(f"  - {a}" for a in alerts) if alerts else "  - 无"

    erp_zone_label = f"低估区（{current_erp_percentile:.0%}分位，ERP≥P50）" if in_undervalued \
                     else f"高估区（{current_erp_percentile:.0%}分位，ERP<P50）"

    exit_block = f"""
---
### 减仓 / 清仓信号

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

_TP_L1 = 0.15   # 第一级：现价相对MA20乖离率 ≥15% → 止盈1/3
_TP_L2 = 0.25   # 第二级：现价相对MA60乖离率 ≥25% → 止盈至底仓
_TP_L3 = 0.40   # 第三级：现价相对MA20乖离率 ≥40% → 止盈过半（硬性，不因估值区间豁免）
_TP_OVERVALUED_PCTL = 0.25  # ERP/PSY历史分位 < 此值 视为"高估区"，触发动作升级


def compute_profit_signal_summary(erp_code: str, current_erp_percentile: float) -> dict:
    """止盈信号，镜像compute_exit_signal_summary：用乖离率代替回撤。高估区时L1/L2动作升级。"""
    price_s = _load_etf_price_series(erp_code)

    if price_s is None or len(price_s) < 20:
        return {
            "level": -1, "verdict_icon": "─",
            "verdict_line": "─ 无ETF价格数据，跳过", "has_data": False,
        }

    cur_price = price_s.iloc[-1]
    ma20, ma60, _ = _compute_ma_stats(price_s)

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
        verdict_line = "✅ 无止盈信号 — 未出现明显乖离过热"
    elif final_level == 1:
        verdict_icon = "💰"
        verdict_line = f"💰 第一级止盈提示 — 现价相对MA20乖离{dev20_pct:.1f}%，建议止盈1/3锁定收益"
    elif final_level == 2:
        esc_note = f"（乖离{_TP_L1*100:.0f}%触发但估值已处高估区，升级处理）" if escalated else ""
        verdict_icon = "💰"
        verdict_line = f"💰 第二级止盈提示 — 乖离过热{esc_note}，建议止盈至底仓"
    else:
        if escalated:
            # 由 L2 升级而来（未必真正触及L3乖离阈值，因高估区叠加而升级动作）
            verdict_icon = "🟨"
            verdict_line = ("🟨 第三级强止盈（高估区升级）— L2乖离过热触发，估值处于高估区，"
                             "动作升级为止盈过半仓位")
        else:
            verdict_icon = "🟨"
            verdict_line = (f"🟨 第三级强止盈 — 乖离{dev20_pct:.1f}%（≥{_TP_L3*100:.0f}%极端过热，硬性阈值），"
                             "建议止盈过半仓位，不因估值区间豁免")

    return {
        "level": final_level, "verdict_icon": verdict_icon,
        "verdict_line": verdict_line, "has_data": True,
        "cur_price": cur_price, "ma20": ma20, "ma60": ma60,
        "dev20": dev20, "dev60": dev60,
    }


def build_profit_signal_block(erp_code: str, current_erp_percentile: float) -> str:
    """止盈信号详情区块，直接复用compute_profit_signal_summary算好的数值。"""
    summary = compute_profit_signal_summary(erp_code, current_erp_percentile)

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
    ma20_str  = f"{ma20:.3f}" if pd.notna(ma20) else "─"
    ma60_str  = f"{ma60:.3f}" if pd.notna(ma60) else "─"

    valuation_label = f"高估区（{current_erp_percentile:.0%}分位，触发动作升级）" if is_overvalued \
                       else f"非高估区（{current_erp_percentile:.0%}分位）"

    profit_block = f"""
---
### 止盈信号

> 估值区间：**{valuation_label}**
> 阈值：L1 乖离MA20≥{_TP_L1*100:.0f}% → 止盈1/3 · L2 乖离MA60≥{_TP_L2*100:.0f}% → 止盈至底仓 · L3 乖离MA20≥{_TP_L3*100:.0f}% → 止盈过半（硬性）
> 高估区时L1/L2动作升一级，L3无论估值区间均执行

{summary['verdict_line']}

| 指标 | 数值 | 状态 |
|:-----|-----:|:-----|
| 当前价格 | {cur_price:.3f} | ─ |
| MA20 | {ma20_str} | 乖离 {dev20_str}｜{dev_status(_TP_L1, dev20)} |
| MA60 | {ma60_str} | 乖离 {dev60_str}｜{dev_status(_TP_L2, dev60)} |

> 💡 止盈信号与减仓/止损信号相互独立：止盈针对"涨多了要不要落袋"，止损针对"跌多了要不要认赔"。两者可能同时不触发，也可能未来在剧烈震荡中先后触发。
"""
    return profit_block


# ══════════════════════════════════════════════════════════════════════
#  基本面暴雷预警模块（东方财富快讯本地粗筛 + AI 二次过滤 + 结构化返回）
# ══════════════════════════════════════════════════════════════════════

_ANTHROPIC_API_URL  = "https://api.qnaigc.com/v1/messages"


def _call_anthropic_with_retry(payload, headers):
    """
    调用 AI 接口，带限流感知的重试 + 间隔控制。
    - 连续两次调用之间至少间隔 _API_CALL_MIN_INTERVAL 秒（无论上次成功与否）
    - 遇到 429 时按 _API_RETRY_BASE_DELAY * 2^attempt 退避重试，最多 _API_MAX_RETRIES 次
    - 优先遵循响应头 Retry-After（如果有）
    - 重试耗尽后抛出最后一次的异常，交由调用方的 except 块处理
    """
    elapsed = time.time() - _last_api_call_ts["t"]
    if elapsed < _API_CALL_MIN_INTERVAL:
        time.sleep(_API_CALL_MIN_INTERVAL - elapsed)

    last_exc = None
    for attempt in range(_API_MAX_RETRIES):
        try:
            resp = requests.post(_ANTHROPIC_API_URL, json=payload, headers=headers, timeout=60)
            _last_api_call_ts["t"] = time.time()

            if resp.status_code == 429:
                wait = _API_RETRY_BASE_DELAY * (2 ** attempt)
                retry_after = resp.headers.get("retry-after")
                if retry_after:
                    try:
                        wait = max(wait, float(retry_after))
                    except ValueError:
                        pass
                if attempt < _API_MAX_RETRIES - 1:
                    time.sleep(wait)
                    continue
                resp.raise_for_status()  # 重试耗尽，抛出 429

            resp.raise_for_status()
            return resp

        except requests.exceptions.HTTPError as e:
            last_exc = e
            if e.response is not None and e.response.status_code == 429 and attempt < _API_MAX_RETRIES - 1:
                continue
            raise
        except requests.exceptions.RequestException as e:
            last_exc = e
            raise

    raise last_exc


_SKIP_FUNDAMENTAL_CODES = {
    "000300",  # 沪深300 - 宽基
    "000688",  # 科创50 - 宽基
    "SPY", "QQQ",  # 美股宽基
    "EWQ", "EWG", "EWJ", "EEM",  # MSCI 国别宽基
}

_EM_NEWS_CACHE = {"df": None, "fetched_at": 0.0}
_EM_NEWS_CACHE_TTL_SECONDS = 1800  # 30分钟内复用同一批快讯，避免21个标的各拉一次


def _fetch_em_news_df():
    """
    拉取东方财富全球财经快讯（ak.stock_info_global_em），30分钟内复用缓存。
    覆盖窗口通常只有最近几小时（约200条），不依赖任何API key。
    失败时返回 None，由调用方决定如何降级。
    """
    now = time.time()
    if _EM_NEWS_CACHE["df"] is not None and (now - _EM_NEWS_CACHE["fetched_at"]) < _EM_NEWS_CACHE_TTL_SECONDS:
        return _EM_NEWS_CACHE["df"]

    last_exc = None
    for attempt in range(_SERPER_MAX_RETRIES + 1):
        try:
            df = ak.stock_info_global_em()
            if df is None or len(df) == 0:
                return None
            _EM_NEWS_CACHE["df"] = df
            _EM_NEWS_CACHE["fetched_at"] = now
            return df
        except Exception as e:
            last_exc = e
            if attempt < _SERPER_MAX_RETRIES:
                time.sleep(_SERPER_RETRY_BASE_DELAY * (2 ** attempt))
                continue

    print(f"⚠️ 东方财富快讯拉取失败（已重试{_SERPER_MAX_RETRIES}次）：{last_exc}")
    return None


def _em_news_search(keywords: list[str], max_results: int = 8) -> list[dict]:
    """
    本地关键词粗筛：在最近的东方财富快讯标题+摘要里匹配任意关键词命中。
    粗筛允许一定假阳性（比如"军工"命中无关新闻），交由后续 AI 二次过滤判断
    是否真正与该标的相关，而不是在这一步就追求精确匹配。
    """
    df = _fetch_em_news_df()
    if df is None or len(df) == 0:
        return []

    if not keywords:
        return []

    pattern = "|".join(keywords)
    try:
        mask = (
            df["标题"].str.contains(pattern, na=False) |
            df["摘要"].str.contains(pattern, na=False)
        )
    except Exception:
        return []

    matched = df[mask].head(max_results)

    results = []
    for _, row in matched.iterrows():
        results.append({
            "title":   str(row.get("标题", "")),
            "url":     str(row.get("链接", "")),
            "content": str(row.get("摘要", ""))[:150],
        })
    return results


def _fundamental_keywords() -> dict:
    """
    本地关键词粗筛词表，仅覆盖行业/主题类指数（宽基/国别指数走 _SKIP_FUNDAMENTAL_CODES
    直接跳过，不需要在此列出）。每个标的的关键词同时包含"标的核心词"和"风险类词"，
    粗筛时只要任一关键词命中即可（粗筛允许假阳性，交给 AI 二次过滤）。
    """
    return {
        "000922": ["红利", "分红", "高股息"],
        "000015": ["红利", "分红", "高股息"],
        "399989": ["医药", "医疗", "集采", "药监", "创新药"],
        "931071": ["人工智能", "AI", "大模型", "算力"],
        "000069": ["消费", "零售", "餐饮"],
        "930781": ["影视", "票房", "广电", "传媒"],
        "HSTECH": ["恒生科技", "互联网", "港股科技", "反垄断"],
        "399967": ["军工", "国防", "军贸", "武器装备"],
        "931066": ["军工", "国防", "军贸", "武器装备"],
        "930794": ["中美互联网", "中概股", "互联网", "中美关系"],
        "930598": ["稀土", "出口管制", "稀有金属"],
        "000819": ["有色金属", "铜价", "铝价", "锂", "稀有金属"],
        "950125": ["半导体", "芯片", "半导体设备", "半导体材料", "国产替代"],
        "399975": ["证券", "券商", "两融", "投行", "IPO"],
    }


def build_fundamental_alert_block(code: str, name: str) -> tuple[dict, str]:
    """基本面暴雷预警：宽基跳过→关键词粗筛→AI相关性过滤→AI暴雷判断。全文件唯一调用AI的模块。"""
    _empty = {"alert_level": "─", "confidence": "─", "summary": ""}

    if code in _SKIP_FUNDAMENTAL_CODES:
        return (
            {"alert_level": "N/A", "confidence": "─", "summary": "宽基/国别指数，基本面预警不适用"},
            "\n> ℹ️ 基本面预警：宽基/国别指数成分股高度分散，单一基本面暴雷对指数影响有限，"
            "本模块不适用。请关注上方「减仓/清仓信号」中的价格回撤提示。\n",
        )

    keywords = _fundamental_keywords().get(code)
    if not keywords:
        return _empty, ""

    import json

    candidates = _em_news_search(keywords, max_results=8)

    if not candidates:
        # 硬性短路：没有任何关键词命中时，绝不让AI用训练知识"脑补"判断。
        return (
            {"alert_level": "─", "confidence": "─", "summary": "未获取到相关新闻"},
            "\n> ⚠️ 基本面预警：本地关键词粗筛未命中任何近期快讯，"
            "本次跳过 AI 判断。结果为「未知」，请人工核实，不代表基本面正常。\n",
        )

    # ── 第一步：AI 相关性过滤 ──────────────────────────────────────────
    # 关键词粗筛允许假阳性（如"军工"命中无关新闻），这一步让AI逐条剔除
    # 真正不相关的新闻，避免"沪深300/监管"这类宽泛关键词把无关新闻带入
    # 最终判断，拉低判断质量。
    candidates_list_str = "\n".join(
        f"{i+1}. {c['title']}：{c['content']}" for i, c in enumerate(candidates)
    )
    filter_prompt = f"""以下是通过关键词粗筛得到的近期财经快讯候选列表，可能包含与"{name}({code})"无关的新闻（粗筛允许误报）。
请逐条判断每条新闻是否真正与"{name}({code})"的基本面相关（即报道的是该行业/指数本身或其核心成分股的情况，而非仅因字面关键词撞车）。

候选新闻：
{candidates_list_str}

请严格按以下JSON格式输出，不要输出任何其他内容：
{{"relevant_indices": [与"{name}"真正相关的新闻序号列表，如 [1, 3]，如果一条都不相关则为空列表 []]}}"""

    try:
        filter_payload = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 500,
            "messages": [{"role": "user", "content": filter_prompt}]
        }
        headers = {
            "Content-Type":      "application/json",
            "x-api-key":         os.getenv("ANTHROPIC_API_KEY", ""),
            "anthropic-version": "2023-06-01",
        }
        filter_resp = _call_anthropic_with_retry(filter_payload, headers)
        filter_data = filter_resp.json()
        filter_text_blocks = [b["text"] for b in filter_data.get("content", []) if b.get("type") == "text"]
        filter_raw = "\n".join(filter_text_blocks).strip().replace("```json", "").replace("```", "").strip()
        filter_result = json.loads(filter_raw)
        relevant_indices = filter_result.get("relevant_indices", [])
        all_results = [candidates[i - 1] for i in relevant_indices if 1 <= i <= len(candidates)]
    except Exception as e:
        # 相关性过滤失败：保守起见，不要把未经过滤的粗筛结果直接当作"相关新闻"
        # 喂给最终判断（会重新引入假阳性问题），明确标记为未知。
        return (
            {"alert_level": "─", "confidence": "─", "summary": f"相关性过滤失败：{e}"},
            f"\n> ⚠️ 基本面预警：AI相关性过滤步骤发生异常（{e}），跳过本次判断。"
            "结果为「未知」，不代表基本面正常。\n",
        )

    if not all_results:
        return (
            {"alert_level": "─", "confidence": "─", "summary": "粗筛命中均与标的无关"},
            "\n> ℹ️ 基本面预警：本地关键词粗筛命中的新闻经AI判断均与该标的无关，"
            "本次无可用新闻进行基本面判断。结果为「未知」，不代表基本面正常。\n",
        )

    news_snippets = "\n".join(
        f"- [{r.get('title','')}]({r.get('url','')})：{r.get('content','')[:150]}"
        for r in all_results
    )
    search_block = f"以下是近期相关新闻（来自实时快讯，已经过相关性过滤）：\n{news_snippets}"
    sources_from_search = [r.get("url", "") for r in all_results if r.get("url")]

    prompt = f"""你是一位专业的股票基本面分析师。请根据以下实时新闻，判断"{name}({code})"近期是否存在重大基本面负面事件，并对每条新闻做正负面分类。

{search_block}

判断标准（以下任一即为"疑似暴雷"）：
- 核心成分股出现重大财务造假、业绩暴雷、退市风险
- 行业遭遇超预期强监管、重大政策打压
- 宏观层面出现系统性风险（如金融危机苗头、主权债务危机）
- 指数或ETF本身出现清盘、停牌等结构性风险

同时请对上面每一条新闻单独判断其对"{name}({code})"是利好（positive）、利空（negative）还是中性（neutral）：
- positive：对行业/标的有积极影响（如政策支持、业绩超预期、需求增长）
- negative：对行业/标的有消极影响（如监管收紧、业绩下滑、负面舆情）
- neutral：纯客观陈述，无明显方向性影响（如指数行情播报、无关联背景新闻）

请严格按以下JSON格式输出，不要输出任何其他内容：
{{"alert_level": "正常" | "关注" | "疑似暴雷", "confidence": "低" | "中" | "高", "summary": "不超过80字的摘要", "sources": ["来源1", "来源2"], "news_sentiment": ["positive" | "negative" | "neutral", ...]}}

news_sentiment 数组的长度和顺序必须与上面新闻列表一一对应（共{len(all_results)}条）。"""

    try:
        payload = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 1000,
            "messages": [{"role": "user", "content": prompt}]
        }
        headers = {
            "Content-Type":      "application/json",
            "x-api-key":         os.getenv("ANTHROPIC_API_KEY", ""),
            "anthropic-version": "2023-06-01",
        }
        resp = _call_anthropic_with_retry(payload, headers)
        data = resp.json()

        text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
        raw_text    = "\n".join(text_blocks).strip().replace("```json", "").replace("```", "").strip()
        result      = json.loads(raw_text)

        alert_level = result.get("alert_level", "正常")
        confidence  = result.get("confidence", "低")
        summary     = result.get("summary", "")
        sources     = result.get("sources") or sources_from_search[:3]

        # 逐条新闻正负面计数。AI返回数组长度若与新闻数不一致（解析异常/模型未严格遵循格式），
        # 不强行对齐，计数置为不可用而非猜测性截断/补齐，避免产出虚假的精确数字。
        news_sentiment = result.get("news_sentiment", [])
        if isinstance(news_sentiment, list) and len(news_sentiment) == len(all_results):
            positive_count = sum(1 for s in news_sentiment if s == "positive")
            negative_count = sum(1 for s in news_sentiment if s == "negative")
            neutral_count  = sum(1 for s in news_sentiment if s == "neutral")
            sentiment_available = True
        else:
            positive_count = negative_count = neutral_count = 0
            sentiment_available = False

        if alert_level == "疑似暴雷":
            level_icon = "🚨"
            action_tip = "**⚠️ 需人工确认后才可触发减仓/清仓操作，请立即核查。**"
        elif alert_level == "关注":
            level_icon = "⚠️"
            action_tip = "建议持续关注，暂不需要立即操作。"
        else:
            level_icon = "✅"
            action_tip = "近期无重大基本面异常。"

        sources_md = "\n".join(f"  - {s}" for s in sources) if sources else "  - 无"

        result_dict = {
            "alert_level": alert_level,
            "confidence":  confidence,
            "summary":     summary,
            "positive_count": positive_count,
            "negative_count": negative_count,
            "neutral_count":  neutral_count,
            "sentiment_available": sentiment_available,
        }

        if sentiment_available:
            sentiment_line = f"📊 当日相关新闻：🟢正面 {positive_count} 条 · 🔴负面 {negative_count} 条 · ⚪中性 {neutral_count} 条（共{len(all_results)}条）"
        else:
            sentiment_line = "📊 当日相关新闻：正负面计数不可用（模型输出格式异常）"

        markdown_str = f"""
---
### 基本面暴雷预警（东方财富快讯本地粗筛 + AI 二次过滤判断，需人工确认）

> 🔍 数据源：东方财富全球财经快讯（akshare，免key）+ AI 相关性过滤 + AI 判断。**本模块仅供参考，不自动触发任何交易动作。**

{level_icon} **{alert_level}**（置信度：{confidence}）

{action_tip}

{sentiment_line}

**摘要：** {summary}

**参考来源：**
{sources_md}
"""
        return result_dict, markdown_str

    except requests.exceptions.Timeout:
        return (
            {"alert_level": "─", "confidence": "─", "summary": "超时"},
            "\n> ⚠️ 基本面预警：API请求超时（已重试），跳过。本次结果为「未知」，不代表基本面正常。\n",
        )
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        return (
            {"alert_level": "─", "confidence": "─", "summary": f"HTTP {status}"},
            f"\n> ⚠️ 基本面预警：API返回错误（HTTP {status}，已重试{_API_MAX_RETRIES}次），跳过。本次结果为「未知」，不代表基本面正常。\n",
        )
    except Exception as e:
        return (
            {"alert_level": "─", "confidence": "─", "summary": str(e)},
            f"\n> ⚠️ 基本面预警：发生异常（{e}），跳过。本次结果为「未知」，不代表基本面正常。\n",
        )


# ══════════════════════════════════════════════════════════════════════
#  报告构建模块
# ══════════════════════════════════════════════════════════════════════

def build_unified_valuation_block(df, code, val_series=None, win_rate=None, odds_ratio=None):
    """核心估值决策区块：胜率+赔率+综合评级。win_rate/odds_ratio建议由调用方传入，
    避免和analyze_and_suggest重复计算（历史上曾因此对SPY/QQQ等锚定标的口径不一致）。"""
    is_hstech = (code == "HSTECH")

    if is_hstech:
        data_df = load_ps_data()
        if data_df is None:
            return "\n> ⚠️ 未找到 HSTECH PS 数据。\n"
        price_metric_series = data_df["ps"].dropna()
        if val_series is None:
            val_series = data_df["psy"].dropna()
        m_name, p_name = "PSY", "PS"
    else:
        if df is None or len(df) == 0:
            return "\n> ⚠️ ERP 数据不足。\n"
        price_metric_series = df['PE'].dropna()
        if val_series is None:
            val_series = df['ERP'].dropna()
        m_name, p_name = "ERP", "PE"

    if len(val_series) < 50:
        return "\n> ⚠️ 样本不足，无法计算胜率赔率。\n"

    cur_val      = val_series.iloc[-1]
    cur_p_metric = price_metric_series.iloc[-1]
    avg_p        = price_metric_series.mean()
    max_p        = price_metric_series.max()
    min_p        = price_metric_series.min()

    p10_val = val_series.quantile(0.10)
    p90_val = val_series.quantile(0.90)

    if win_rate is None:
        win_rate = (val_series < cur_val).mean()
    if odds_ratio is None:
        odds_ratio = calc_odds(cur_val, val_series)
    if odds_ratio is None:
        odds_str = "极高（已超P90极度低估区）"
    else:
        odds_str = f"{odds_ratio:.2f}x"

    upside   = cur_val - p10_val
    downside = p90_val - cur_val

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

    if odds_ratio is None:
        rating = "🟢 已进入极度低估区，极佳买点"
    elif odds_ratio == 0.0:
        rating = "🚨 已进入极度高估区，规避"
    elif win_rate >= 0.75 and odds_ratio >= 1.5:
        rating = "🟢 高胜率 + 高赔率，极佳买点"
    elif win_rate >= 0.75 and odds_ratio >= 1.0:
        rating = "🟢 胜率尚可 + 赔率合理，较好买点"
    elif win_rate >= 0.50 and odds_ratio >= 1.0:
        rating = "🟡 胜率中等 + 赔率一般，可参与"
    elif win_rate >= 0.50 and odds_ratio >= 0.8:
        rating = "🟡 胜率赔率均衡，中性"
    elif win_rate < 0.25 and odds_ratio < 0.5:
        rating = "🚨 低胜率 + 低赔率，双杀，规避"
    elif win_rate < 0.25:
        rating = "🔴 低胜率，谨慎"
    else:
        rating = "🟠 中性偏弱"

    block = f"""
---
### 核心估值决策（基于 {m_name} 框架）

> 胜率 = {m_name}历史分位（越高代表当前越便宜）
> 赔率 = {m_name}回落盈利空间（当前{m_name} − P10） / {m_name}走高亏损空间（P90 − 当前{m_name}）
> 当前 {m_name} = **{cur_val:.2%}**，历史分位 = **{win_rate:.1%}** {zone_icon} **{zone_name}**

| 指标 | 数值 | 说明 |
|:-----|-----:|:-----|
| **胜率** | **{win_rate:.1%}** | 历史上 {win_rate:.1%} 的时间比现在更贵（{m_name}更低） |
| **赔率（盈亏比）** | **{odds_str}** | 盈利空间 {upside:.2%} / 亏损空间 {downside:.2%} |
| 当前 {p_name} | {cur_p_metric:.1f}x | 历史均值 {avg_p:.1f}x，最高 {max_p:.1f}x，最低 {min_p:.1f}x |

**综合评级：{rating}**
"""
    return block


def build_trend_block(df, erp_series, code, quantiles):
    """近10月趋势表格+斜率信号。内部月度polyfit（10月周期）和compute_erp_slope_signal
    （20日周期）是两个不同时间尺度的独立指标，非重复计算，不合并。"""
    if code == "HSTECH":
        ps_df = load_ps_data()
        if ps_df is None or "psy" not in ps_df.columns:
            return ""
        recent_psy = ps_df[ps_df["psy"].notna()][["ps", "psy"]].tail(10)
        if len(recent_psy) < 2:
            return ""

        slope_info = compute_erp_slope_signal(ps_df["psy"].dropna())

        psy_rows = []
        prev_psy = None
        for d, r in recent_psy.iterrows():
            arrow = "─"
            if prev_psy is not None:
                diff = r["psy"] - prev_psy
                arrow = f"▲{diff:.2%}" if diff > 0 else (f"▼{abs(diff):.2%}" if diff < 0 else "─")
            prev_psy = r["psy"]
            psy_rows.append(f"| {d.strftime('%Y-%m')} | {r['ps']:.2f}x | **{r['psy']:.2%}** | {arrow} |")
        return f"""
---
### 近10月 PSY 趋势（营收口径）

> 📐 近20日斜率信号：{slope_info['signal_icon']} **{slope_info['signal']}** — {slope_info['desc']}

| 月份 | PS | PSY | 环比 |
|:-----|---:|----:|:-----|
{chr(10).join(psy_rows)}
"""

    valid = df[df['ERP'].notna()][['Date', 'ERP', 'PE']].copy()
    if len(valid) < 2:
        return ""

    slope_info = compute_erp_slope_signal(erp_series)

    valid['YM'] = valid['Date'].dt.to_period('M')
    month_end = valid.groupby('YM').last().reset_index(drop=True)
    recent = month_end.tail(10).copy()

    x = np.arange(len(recent))
    slope = np.polyfit(x, recent['ERP'].values, 1)[0]

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

        date_str = row['Date'].strftime("%Y-%m")
        pe_str   = f"{pe_val:.1f}x" if pd.notna(pe_val) else "N/A"
        rows.append(f"| {date_str} | {pe_str} | **{erp_val:.2%}** {zone_icon} | {arrow} |")

    rows_md = "\n".join(rows)

    return f"""
---
### 近10月 ERP 趋势

> 趋势方向：**{trend_icon}**，区间变化：**{delta_str}**
> 📐 近20日斜率信号：{slope_info['signal_icon']} **{slope_info['signal']}** — {slope_info['desc']}

| 月份 | PE | ERP | 环比变化 |
|:-----|---:|----:|:---------|
{rows_md}
"""


# ══════════════════════════════════════════════════════════════════════
#  仪表盘 & 图例
# ══════════════════════════════════════════════════════════════════════

# 持仓标记：True = 我持有该标的对应的ETF/指数，仅用于在仪表盘显示📌徽章
# 及生成「我的持仓·减仓信号」置顶区域。不再承载分类文字。
HOLDING_CATEGORY = {
    "000300": True,
    "000688": True,
    "000922": False,
    "000015": False,
    "399989": True,
    "931071": False,
    "000069": True,
    "930781": False,
    "SPY":    True,
    "QQQ":    False,
    "EWQ":    False,
    "EWG":    False,
    "EWJ":    False,
    "EEM":    False,
    "HSTECH": True,
    "399967": True,
    "931066": False,
    "930794": False,
    "930598": True,
    "000819": False,   # 有色金属，不再持仓
    "950125": False,   # 半导体材料设备，不再持仓
    "399975": False,
}


def is_holding(code: str) -> bool:
    """是否实际持仓（仅用于仪表盘📌徽章展示）。"""
    return bool(HOLDING_CATEGORY.get(code, False))


def generate_action_sentence(disc, divg, vol, zone_label):
    """把折溢价/量价背离/波动率信号拼成一句操作建议文案（正常标的用）。"""
    if zone_label and (zone_label.startswith("🔴") or zone_label.startswith("🚨")):
        return "规避，不建仓"
    prefix = "等量能确认，" if divg == "⚠️" else ""
    if vol == "🔴":
        mid = "分批建仓"
    elif vol == "🟠":
        mid = "建仓，注意分批"
    else:
        mid = "一次建仓"
    if disc == "🔴":
        suffix = "，等折价再入"
    elif disc in ["💎", "🟢"]:
        suffix = "，折价窗口开着"
    else:
        suffix = ""
    return prefix + mid + suffix


def build_summary_block(summary_list: list, output_format: str = "html") -> str:
    if not summary_list:
        return ""

    date_str = datetime.now().strftime("%Y-%m-%d")

    def pos_color_class(pct):
        if pct >= 80: return "pos-high"
        if pct >= 60: return "pos-mid"
        if pct >= 40: return "pos-low"
        return "pos-min"

    # 触发止损/止盈的标的单独置顶，跳出估值区间分类，避免被埋没在正常条目里
    alerted   = [r for r in summary_list if r.get("exit_level", 0) > 0 or r.get("profit_level", 0) > 0]
    unalerted = [r for r in summary_list if r.get("exit_level", 0) == 0 and r.get("profit_level", 0) == 0]
    stale_list = [r for r in summary_list if r.get("stale_flag") == "⚠️"]

    zone_groups = [
        ("🟢 极度低估", lambda z: z.startswith("🟢 极度低估")),
        ("🟢 显著低估", lambda z: z.startswith("🟢 显著低估")),
        ("🟡 合理偏低", lambda z: z.startswith("🟡")),
        ("🟠 合理区间", lambda z: z.startswith("🟠")),
        ("🔴 高估/规避", lambda z: z.startswith("🔴") or z.startswith("🚨")),
    ]

    header = f"## 📊 决策仪表盘 · {date_str}"
    legend = (
        "胜率🟢≥75% 🟡50-75% 🟠25-50% 🔴<25% · 折溢价💎大折 🟢折 🟡平 🟠溢 🔴大溢 · "
        "波🔴高位 · 量⚠️背离 · 人气🟢加仓确认 🔴减仓确认（热榜排名上升+ERP分位共振） · "
        "🚨止损 💰止盈 · 🕓数据未更新 · 📌=持仓\n\n---"
    )

    if output_format == "markdown":
        lines = [header, "", legend, ""]

        if stale_list:
            lines.append(f"\n**🕓 数据新鲜度预警 ({len(stale_list)})**")
            for r in stale_list:
                lines.append(f"\n🕓 {r['name']} · {r.get('stale_note', '')}")

        if alerted:
            lines.append(f"\n**🚨 需要处理 ({len(alerted)})**")
            for r in alerted:
                badge      = "📌 " if is_holding(r["code"]) else ""
                zone_short = r.get("erp_zone", "").split("(")[0].strip()
                pop_icon   = r.get("popularity_icon", "─")
                pop_str    = f" · 人气{pop_icon}" if pop_icon in ("🟢", "🔴") else ""
                exit_line   = r.get("exit_verdict_line", "") if r.get("exit_level", 0) > 0 else ""
                profit_line = r.get("profit_verdict_line", "") if r.get("profit_level", 0) > 0 else ""
                combined = "；".join(x for x in (exit_line, profit_line) if x)
                lead_icon = "🚨" if r.get("exit_level", 0) > 0 else "💰"
                lines.append(
                    f"\n{lead_icon} {badge}{r['name']} · {zone_short} · {r['total_pct']}%\n"
                    f"　{combined}{pop_str}"
                )

        for group_label, match_fn in zone_groups:
            group_items = [r for r in unalerted if match_fn(r.get("erp_zone", ""))]
            if not group_items:
                continue
            lines.append(f"\n**{group_label} ({len(group_items)})**")
            for r in group_items:
                badge      = "📌 " if is_holding(r["code"]) else ""
                zone_label = r.get("erp_zone", "")
                disc = r.get("etf_discount", "─")
                divg = r.get("etf_divergence", "─")
                vol  = r.get("etf_vol", "─")
                action = generate_action_sentence(disc, divg, vol, zone_label)

                # 只有异常字段才展示，正常状态（✅/🟡/─）不重复列出，减少噪音
                extras = []
                if vol == "🔴":
                    extras.append(f"波{vol}")
                if disc in ("💎", "🔴"):
                    extras.append(f"折{disc}")
                if divg == "⚠️":
                    extras.append(f"量{divg}")
                pop_icon = r.get("popularity_icon", "─")
                if pop_icon in ("🟢", "🔴"):
                    extras.append(f"人气{pop_icon}")
                extra_str = (" · " + " ".join(extras)) if extras else ""

                lines.append(
                    f"\n{badge}{r['name']} · {r['total_pct']}%"
                    f"({r['b_pct']}+{r['v_pct']}+{r['t_pct']}){extra_str} · {action}"
                )
        return "\n".join(lines) + "\n\n---\n"

    else:
        rows_html = []

        if stale_list:
            rows_html.append('<tr><td colspan="4" class="section-header">🕓 数据新鲜度预警</td></tr>')
            for r in stale_list:
                rows_html.append(
                    f'<tr class="alert-row">'
                    f'<td class="col-name">{r["name"]}</td>'
                    f'<td colspan="3" class="col-action">🕓 {r.get("stale_note", "")}</td>'
                    f'</tr>'
                )

        if alerted:
            rows_html.append('<tr><td colspan="4" class="section-header">🚨 需要处理</td></tr>')
            for r in alerted:
                code       = r.get("code", "")
                badge      = "📌 " if is_holding(code) else ""
                zone_short = r.get("erp_zone", "").split("(")[0].strip()
                pop_icon   = r.get("popularity_icon", "─")
                pop_str    = f"｜人气{pop_icon}" if pop_icon in ("🟢", "🔴") else ""
                exit_line   = r.get("exit_verdict_line", "") if r.get("exit_level", 0) > 0 else ""
                profit_line = r.get("profit_verdict_line", "") if r.get("profit_level", 0) > 0 else ""
                combined = "；".join(x for x in (exit_line, profit_line) if x)
                lead_icon = "🚨" if r.get("exit_level", 0) > 0 else "💰"
                rows_html.append(
                    f'<tr class="alert-row">'
                    f'<td class="col-name">{badge}{r["name"]}</td>'
                    f'<td class="col-pos">{r["total_pct"]}%</td>'
                    f'<td colspan="2" class="col-action">{lead_icon} {zone_short}｜{combined}{pop_str}</td>'
                    f'</tr>'
                )

        for group_label, match_fn in zone_groups:
            group_items = [r for r in unalerted if match_fn(r.get("erp_zone", ""))]
            if not group_items:
                continue
            rows_html.append(f'<tr><td colspan="4" class="section-header">{group_label}</td></tr>')
            for r in group_items:
                code       = r.get("code", "")
                total_pct  = r["total_pct"]
                pos_cls    = pos_color_class(total_pct)
                disc       = r.get("etf_discount", "─")
                divg       = r.get("etf_divergence", "─")
                vol        = r.get("etf_vol", "─")
                zone_label = r.get("erp_zone", "")
                action     = generate_action_sentence(disc, divg, vol, zone_label)
                badge      = "📌 " if is_holding(code) else ""
                pop_icon   = r.get("popularity_icon", "─")
                pop_str    = f" 人气{pop_icon}" if pop_icon in ("🟢", "🔴") else ""
                rows_html.append(
                    f'<tr>'
                    f'<td class="col-name">{badge}{r["name"]}</td>'
                    f'<td class="col-pos {pos_cls}">{total_pct}%<br>'
                    f'<span class="col-sub">{r["b_pct"]}+{r["v_pct"]}+{r["t_pct"]}</span></td>'
                    f'<td class="col-sig">波{vol} 折{disc}{pop_str}</td>'
                    f'<td class="col-action">{action}</td>'
                    f'</tr>'
                )
        table_html = '<table class="dashboard-table">\n' + "\n".join(rows_html) + "\n</table>"
        return f"{header}\n{legend}\n\n{table_html}\n\n---\n"


LEGEND_BLOCK = f"""
ERP = 1/PE − 无风险利率；PSY = 1/PS − 无风险利率。越高越便宜。
赔率 = ERP回落盈利空间（当前ERP − P10） / ERP走高亏损空间（P90 − 当前ERP）
止损：L1 回撤{_DD_L1*100:.0f}%+MA20减1/3 · L2 回撤{_DD_L2*100:.0f}%+MA60减至底仓 · L3 回撤{_DD_L3*100:.0f}%全清（硬止损）
止盈（镜像逻辑）：L1 乖离MA20≥{_TP_L1*100:.0f}%止盈1/3 · L2 乖离MA60≥{_TP_L2*100:.0f}%止盈至底仓 · L3 乖离MA20≥{_TP_L3*100:.0f}%止盈过半（硬性）
低估区止损L1/L2降级，高估区止盈L1/L2升级；两者L3均无豁免 · QQQ单日跌{_QQQ_SINGLE_DAY_DROP*100:.0f}%触发独立预警
🕓数据新鲜度预警：PE/PS连续{FRESHNESS_STALE_POINTS}个更新点数值未变化时触发（多为抓取失败被ffill掩盖，或QQQ/HSTECH手动填值忘记更新）

---
"""


# ══════════════════════════════════════════════════════════════════════
#  推送模块
# ══════════════════════════════════════════════════════════════════════

REPORT_URL = "https://chiaravan1.github.io/equity-risk-premium-monitor/report.html"


def markdown_to_html(md_text: str, date_str: str) -> str:
    """极简自制Markdown→HTML转换器（仅支持本项目用到的语法子集）。"""
    import re

    def _inline(text):
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
        text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
        return text

    def convert_md(text):
        lines = text.split("\n")
        html_lines = []
        in_table = False
        in_code = False
        for line in lines:
            if line.startswith("```"):
                if not in_code:
                    html_lines.append('<pre><code>')
                    in_code = True
                else:
                    html_lines.append('</code></pre>')
                    in_code = False
                continue
            if in_code:
                html_lines.append(line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
                continue
            if line.startswith("|"):
                if not in_table:
                    html_lines.append('<table>')
                    in_table = True
                cells = [c.strip() for c in line.split("|")[1:-1]]
                if all(re.match(r'^:?-+:?$', c) for c in cells):
                    continue
                row_html = "".join(f"<td>{_inline(c)}</td>" for c in cells)
                html_lines.append(f"<tr>{row_html}</tr>")
                continue
            else:
                if in_table:
                    html_lines.append('</table>')
                    in_table = False
            if line.startswith("# "):
                html_lines.append(f'<h1>{_inline(line[2:])}</h1>')
            elif line.startswith("## "):
                html_lines.append(f'<h2>{_inline(line[3:])}</h2>')
            elif line.startswith("### "):
                html_lines.append(f'<h3>{_inline(line[4:])}</h3>')
            elif line.startswith("#### "):
                html_lines.append(f'<h4>{_inline(line[5:])}</h4>')
            elif line.startswith("> "):
                html_lines.append(f'<blockquote>{_inline(line[2:])}</blockquote>')
            elif line.startswith("---"):
                html_lines.append('<hr>')
            elif line.strip() == "":
                html_lines.append('<br>')
            elif line.startswith("<"):
                html_lines.append(line)
            else:
                html_lines.append(f'<p>{_inline(line)}</p>')
        if in_table:
            html_lines.append('</table>')
        return "\n".join(html_lines)

    body_html = convert_md(md_text)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ERP 监控报告 · {date_str}</title>
<style>
  :root {{
    --bg: #0d1117; --surface: #161b22; --surface2: #21262d;
    --border: #30363d; --text: #e6edf3; --muted: #8b949e;
    --green: #3fb950; --blue: #58a6ff; --accent: #1f6feb;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: 'SF Mono', 'Fira Code', monospace; font-size: 14px; line-height: 1.7; padding: 24px 16px; }}
  .wrap {{ max-width: 900px; margin: 0 auto; }}
  h1 {{ font-size: 20px; color: var(--blue); margin: 24px 0 12px; }}
  h2 {{ font-size: 17px; margin: 20px 0 10px; border-bottom: 1px solid var(--border); padding-bottom: 6px; }}
  h3 {{ font-size: 15px; color: var(--blue); margin: 16px 0 8px; }}
  h4 {{ font-size: 13px; color: var(--muted); margin: 12px 0 6px; }}
  p {{ margin: 6px 0; }}
  hr {{ border: none; border-top: 1px solid var(--border); margin: 20px 0; }}
  blockquote {{ border-left: 3px solid var(--accent); padding: 6px 12px; color: var(--muted); background: var(--surface); border-radius: 0 4px 4px 0; margin: 8px 0; font-size: 13px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 13px; }}
  td {{ padding: 6px 10px; border: 1px solid var(--border); }}
  tr:nth-child(even) td {{ background: var(--surface); }}
  tr:first-child td {{ background: var(--surface2); font-weight: 600; color: var(--muted); }}
  strong {{ color: var(--text); }}
  code {{ background: var(--surface2); padding: 2px 5px; border-radius: 3px; font-size: 12px; color: var(--blue); }}
  pre {{ background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 12px; overflow-x: auto; margin: 10px 0; }}
  pre code {{ background: none; padding: 0; color: var(--text); }}
  .footer {{ text-align: center; color: var(--muted); font-size: 11px; margin-top: 40px; padding-top: 16px; border-top: 1px solid var(--border); }}
  .dashboard-table {{ width: 100%; border-collapse: collapse; }}
  .dashboard-table tr {{ border-bottom: 1px solid #21262d; }}
  .dashboard-table td {{ padding: 10px 8px; vertical-align: middle; border: none; }}
  .alert-row {{ background: rgba(248, 81, 73, 0.08); }}
  .col-name   {{ width: 110px; font-weight: bold; }}
  .col-etf    {{ color: #8b949e; font-size: 11px; }}
  .col-pos    {{ width: 64px; text-align: center; font-size: 20px; font-weight: bold; }}
  .col-sub    {{ font-size: 10px; color: #8b949e; }}
  .col-sig    {{ width: 100px; }}
  .col-action {{ font-size: 12px; }}
  .pos-high   {{ color: #3fb950; }}
  .pos-mid    {{ color: #d29922; }}
  .pos-low    {{ color: #e3b341; }}
  .pos-min    {{ color: #f85149; }}
  .section-header {{
    font-size: 10px; color: #8b949e;
    text-transform: uppercase; letter-spacing: 1px;
    border-bottom: 1px solid #21262d; padding: 12px 0 5px;
    border: none;
  }}
</style>
</head>
<body>
<div class="wrap">
<h1>📊 ERP 策略监控报告 · {date_str}</h1>
{body_html}
<div class="footer">自动生成于 {date_str} · equity-risk-premium-monitor</div>
</div>
</body>
</html>"""


def save_html_report(full_report_md: str, date_str: str):
    """转HTML并写入./docs/report.html，供GitHub Pages展示。"""
    os.makedirs("./docs", exist_ok=True)
    html = markdown_to_html(full_report_md, date_str)
    with open("./docs/report.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ HTML 报告已保存到 ./docs/report.html")


def send_to_wechat(summary_md: str, date_str: str):
    """通过方糖(Server酱)推送仪表盘摘要到微信，未配置SCT_KEY则跳过。"""
    sct_key = os.getenv("SCT_KEY")
    if not sct_key:
        print("⚠️ 未找到 SCT_KEY，推送跳过。")
        return
    url = f"https://sctapi.ftqq.com/{sct_key}.send"
    data = {
        "title": f"ERP 决策报告 ({date_str})",
        "desp": summary_md + f"\n\n📄 [查看完整报告]({REPORT_URL})",
    }
    try:
        res = requests.post(url, data=data)
        print(f"✅ 方糖推送结果: {res.text}")
    except Exception as e:
        print(f"❌ 推送失败: {e}")


# ══════════════════════════════════════════════════════════════════════
#  主分析函数
# ══════════════════════════════════════════════════════════════════════

def analyze_and_suggest(code, name, etf_df=None, summary_list=None):
    """单个标的主分析函数：读数据→算仓位建议→串联各信号模块→汇总summary_list→拼接报告。
    超200行，未来可考虑拆成_compute_all_signals()+_build_report_markdown()。"""
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

    current_erp  = erp_series.iloc[-1]
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

    _hstech_psy_s = None
    if code == "HSTECH":
        _ps_df = load_ps_data()
        if _ps_df is not None and "psy" in _ps_df.columns:
            _hstech_psy_s = _ps_df["psy"].dropna()
            current_erp = _hstech_psy_s.iloc[-1]
            erp_series  = _hstech_psy_s
            quantiles = {
                "P95": _hstech_psy_s.quantile(0.95),
                "P90": _hstech_psy_s.quantile(0.90),
                "P75": _hstech_psy_s.quantile(0.75),
                "P50": _hstech_psy_s.quantile(0.50),
                "P25": _hstech_psy_s.quantile(0.25),
                "P10": _hstech_psy_s.quantile(0.10),
            }

    # ── 新鲜度校验（PE/PS 连续N个更新点未变化）──────────────────────────
    if code == "HSTECH":
        _freshness_metric_name = "PS"
        _freshness = (check_metric_freshness(_ps_df["ps"])
                      if _ps_df is not None and "ps" in _ps_df.columns
                      else {"is_stale": False, "unchanged_count": 0, "last_value": None,
                            "last_date": None, "first_unchanged_date": None})
    else:
        _freshness_metric_name = "PE"
        _freshness = check_metric_freshness(df.set_index('Date')['PE'])
    _freshness_note = build_freshness_note(_freshness, _freshness_metric_name)
    _freshness_line = ("\n> " + _freshness_note) if _freshness_note else ""
    _stale_flag = "⚠️" if _freshness["is_stale"] else "─"

    # ── 仓位建议 ──────────────────────────────────────────────────────
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

    # ── 胜率/赔率 ─────────────────────────────────────────────────────
    if code == "HSTECH":
        _psy_s = _hstech_psy_s
        if _psy_s is not None:
            _win  = (_psy_s < _psy_s.iloc[-1]).mean()
            _odds = calc_odds(_psy_s.iloc[-1], _psy_s)
            erp_zone = (
                "🟢 极度低估 (>=P90)" if _win >= 0.90 else
                "🟢 显著低估 (P75-P90)" if _win >= 0.75 else
                "🟡 合理偏低 (P50-P75)" if _win >= 0.50 else
                "🟠 合理区间 (P25-P50)" if _win >= 0.25 else
                "🔴 严重高估 (P10-P25)" if _win >= 0.10 else
                "🚨 危险泡沫 (<P10)"
            )
        else:
            _win, _odds = float("nan"), float("nan")
    else:
        _win  = (erp_series < current_erp).mean()
        _odds = calc_odds(current_erp, erp_series)

    # ── ETF折溢价执行信号 ─────────────────────────────────────────────
    _etf_signal            = "─"
    _etf_discount_signal   = "─"
    _etf_divergence_signal = "─"
    _etf_vol_signal        = "─"
    if etf_df is not None:
        try:
            _ts = ERP_TO_ETF.get(code)
            if _ts and _ts in etf_df.index:
                _row  = etf_df.loc[_ts]
                _prem = float(_row.get("latest_discount_rate", float("nan")))
                if _prem == _prem:
                    if   _prem < -0.02:  _etf_signal = _etf_discount_signal = "💎"
                    elif _prem < -0.005: _etf_signal = _etf_discount_signal = "🟢"
                    elif _prem <  0.005: _etf_signal = _etf_discount_signal = "🟡"
                    elif _prem <  0.02:  _etf_signal = _etf_discount_signal = "🟠"
                    else:                _etf_signal = _etf_discount_signal = "🔴"
                _div = _row.get("is_price_turnover_divergence", float("nan"))
                if _div == _div:
                    _etf_divergence_signal = "⚠️" if int(_div) == 1 else "✅"
                _vq = float(_row.get("volatility_quantile_1y", float("nan")))
                if _vq == _vq:
                    if   _vq >= 0.85: _etf_vol_signal = "🔴"
                    elif _vq >= 0.60: _etf_vol_signal = "🟠"
                    else:             _etf_vol_signal = "🟢"
        except Exception:
            pass

    # ── 斜率信号 ──────────────────────────────────────────────────────
    slope_info = compute_erp_slope_signal(erp_series)

    # ── 减仓信号 ──────────────────────────────────────────────────────
    erp_percentile = (erp_series < current_erp).mean()
    exit_summary    = compute_exit_signal_summary(code, erp_percentile)
    exit_block      = build_exit_signal_block(code, erp_percentile)
    _exit_signal       = exit_summary["verdict_icon"]
    _exit_verdict_line = exit_summary["verdict_line"]
    if exit_summary["qqq_drop_note"]:
        _exit_verdict_line = _exit_verdict_line + f"；{exit_summary['qqq_drop_note']}"

    # ── 止盈信号（镜像减仓信号）───────────────────────────────────────
    profit_summary = compute_profit_signal_summary(code, erp_percentile)
    profit_block   = build_profit_signal_block(code, erp_percentile)
    _profit_signal       = profit_summary["verdict_icon"]
    _profit_verdict_line = profit_summary["verdict_line"]

    # ── 热榜人气确认信号（辅助确认，非独立交易依据）──────────────────
    # 只算一次，详情区块和仪表盘置顶区共用，避免重复拉取热榜数据
    popularity_result = compute_popularity_confirmation(code, erp_percentile)
    popularity_block   = build_popularity_block(code, erp_percentile, precomputed=popularity_result)

    # ── 基本面预警 ────────────────────────────────────────────────────
    fundamental_result, fundamental_block = build_fundamental_alert_block(code, name)
    _fund_alert = fundamental_result.get("alert_level", "─")
    _fund_pos   = fundamental_result.get("positive_count", 0)
    _fund_neg   = fundamental_result.get("negative_count", 0)
    _fund_sentiment_available = fundamental_result.get("sentiment_available", False)

    if summary_list is not None:
        # 未被下游读取的字段：odds/etf_signal/slope_signal/fundamental_*（win_rate除外，排序用）；
        # etf_signal与etf_discount、exit_verdict_icon与exit_signal、profit_verdict_icon与profit_signal 各为重复值
        summary_list.append({
            "name": name, "code": code,
            "erp_zone": erp_zone,
            "total_pct": total_pct,
            "b_pct": b_pct, "v_pct": v_pct, "t_pct": t_pct,
            "win_rate": _win,
            "odds": _odds,
            "etf_signal":         _etf_signal,
            "etf_discount":       _etf_discount_signal,
            "etf_divergence":     _etf_divergence_signal,
            "etf_vol":            _etf_vol_signal,
            "slope_signal":       slope_info["signal_icon"],
            "exit_signal":        _exit_signal,
            "exit_verdict_icon":  _exit_signal,
            "exit_verdict_line":  _exit_verdict_line,
            "exit_level":         exit_summary["level"],
            "profit_signal":       _profit_signal,
            "profit_verdict_icon": _profit_signal,
            "profit_verdict_line": _profit_verdict_line,
            "profit_level":         profit_summary["level"],
            "fundamental_alert":  _fund_alert,
            "fundamental_positive": _fund_pos,
            "fundamental_negative": _fund_neg,
            "fundamental_sentiment_available": _fund_sentiment_available,
            "popularity_icon":   popularity_result.get("icon", "─"),
            "popularity_signal": popularity_result.get("signal", "数据不足"),
            "stale_flag":  _stale_flag,
            "stale_note":  _freshness_note,
        })

    # ── 报告头部 ──────────────────────────────────────────────────────
    if code == "HSTECH":
        if _hstech_psy_s is not None:
            header_block = f"""
---
# ═══ {name} ({code}) ═══
> 数据源：PS / PSY（营收口径），ERP 数据已停止更新不再展示。　{current_date}

| 指标 | 数值 | 估值区间 |
|:-----|-----:|:---------|
| 当前 PSY | **{current_erp:.2%}** | **{erp_zone}** |
| 历史均值 | {erp_series.mean():.2%} | {len(erp_series)}条样本 |

| 分位点 | PSY值（越高越便宜） | 价格估值状态 |
|:-------|-------------------:|:------------|
| P90 | {quantiles["P90"]:.2%} | 极度低估 |
| P75 | {quantiles["P75"]:.2%} | 显著低估 |
| P50 | {quantiles["P50"]:.2%} | 价值中枢 |
| P25 | {quantiles["P25"]:.2%} | 进入高估 |
| P10 | {quantiles["P10"]:.2%} | 极度高估 |
{_freshness_line}
"""
        else:
            header_block = f"""
---
# ═══ {name} ({code}) ═══
> 数据源：PS / PSY（营收口径），ERP 数据已停止更新不再展示。　{current_date}
{_freshness_line}
"""
    else:
        header_block = f"""
---
# ═══ {name} ({code}) ═══

| 指标 | 数值 | 估值区间 |
|:-----|-----:|:---------|
| 当前 ERP | **{current_erp:.2%}** | **{erp_zone}** |
| 历史均值 | {mean_erp:.2%} | {len(erp_series)}条样本 |

| 分位点 | ERP值（越高越便宜） | 价格估值状态 |
|:-------|-------------------:|:------------|
| P90 | {quantiles["P90"]:.2%} | 极度低估 |
| P75 | {quantiles["P75"]:.2%} | 显著低估 |
| P50 | {quantiles["P50"]:.2%} | 价值中枢 |
| P25 | {quantiles["P25"]:.2%} | 进入高估 |
| P10 | {quantiles["P10"]:.2%} | 极度高估 |
{_freshness_line}
"""

    unified_block    = build_unified_valuation_block(df, code, val_series=erp_series, win_rate=_win, odds_ratio=_odds)
    trend_block      = build_trend_block(df, erp_series, code, quantiles)
    exit_block_final = exit_block
    etf_block        = build_etf_metrics_block(code, etf_df)
    shiller_block    = build_shiller_block(code)

    if exit_summary["level"] > 0:
        position_block = f"""
---
### 仓位建议

{exit_summary['verdict_icon']} 已触发减仓/清仓预警（详见下方「减仓 / 清仓信号」模块），暂不展示常规仓位建议（3+4+3拆分）。
低估位置参考：P75 = {quantiles['P75']:.2%}（显著低估） / P90 = {quantiles['P90']:.2%}（极度低估）
"""
    elif profit_summary["level"] > 0:
        position_block = f"""
---
### 仓位建议

{profit_summary['verdict_icon']} 已触发止盈预警（详见下方「止盈信号」模块），暂不展示常规仓位建议（3+4+3拆分）。
高估位置参考：P25 = {quantiles['P25']:.2%}（进入高估） / P10 = {quantiles['P10']:.2%}（极度高估）
"""
    else:
        position_block = f"""
---
### 仓位建议

**{b_msg}** ({b_pct}%)
**{v_msg}** ({v_pct}%)
**{t_msg}** ({t_pct}%)

建议总仓位：**{total_pct}%**（泡沫底仓 {b_pct}% + 价值主力 {v_pct}% + 投机奇兵 {t_pct}%）
"""

    md = f"""{header_block}{position_block}{unified_block}{trend_block}{exit_block_final}{profit_block}{popularity_block}{fundamental_block}{etf_block}{shiller_block}"""
    print(md)
    return md


# ══════════════════════════════════════════════════════════════════════
#  入口
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    try:
        _etf_df = load_etf_metrics()
    except Exception as e:
        print(f"⚠️ ETF 指标加载意外失败：{e}，主报告继续运行。")
        _etf_df = None

    indices = [
        ("000300", "沪深300"),
        ("000688", "科创50"),
        ("000922", "中证红利"),
        ("000015", "上证红利"),
        ("399989", "中证医疗"),
        ("931071", "人工智能"),
        ("000069", "消费80"),
        ("930781", "中证影视"),
        ("399975", "证券公司"),
        ("SPY",    "S&P 500"),
        ("QQQ",    "Nasdaq 100"),
        ("EWQ",    "MSCI France"),
        ("EWG",    "MSCI Germany"),
        ("EWJ",    "MSCI Japan"),
        ("EEM",    "MSCI Emerging"),
        ("HSTECH", "恒生科技指数"),
        ("399967", "中证军工"),
        ("931066", "军工龙头"),
        ("930794", "中美互联网"),
        ("930598", "稀土产业"),
        ("000819", "有色金属"),
        ("950125", "半导体材料设备"),
    ]

    summary_list = []
    report_list  = []
    for code, name in indices:
        report_md = analyze_and_suggest(code, name, _etf_df, summary_list)
        if report_md:
            report_list.append(report_md)

    _zone_order = {
        "🟢 极度低估": 0, "🟢 显著低估": 1, "🟡 合理偏低": 2,
        "🟠 合理区间": 3, "🔴 严重高估": 4, "🚨 危险泡沫": 5,
    }
    def _sort_key(r):
        zone_str  = r.get("erp_zone", "")
        zone_rank = next((v for k, v in _zone_order.items() if zone_str.startswith(k)), 99)
        win = r.get("win_rate", 0.0)
        win = win if win == win else 0.0
        return (zone_rank, -win)

    summary_list.sort(key=_sort_key)

    if report_list:
        date_str       = datetime.now().strftime("%Y-%m-%d")
        summary_html   = build_summary_block(summary_list, output_format="html")
        summary_wechat = build_summary_block(summary_list, output_format="markdown")

        full_report = (
            "# ERP 策略每日监控报告\n"
            + summary_html
            + LEGEND_BLOCK
            + "".join(report_list)
        )

        save_html_report(full_report, date_str)

        if os.getenv("DRY_RUN") == "true":
            preview_path = "./output_preview.md"
            with open(preview_path, "w", encoding="utf-8") as f:
                f.write(full_report)
            print(f"✅ dry-run 模式，报告已写入 {preview_path}，不推送微信。")
        else:
            print("正在生成报告并准备推送...")
            send_to_wechat(summary_wechat, date_str)
    else:
        print("❌ 未生成任何有效报告，请检查数据文件。")
