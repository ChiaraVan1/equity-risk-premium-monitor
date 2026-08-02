"""
analysis/utils.py
分析工具函数：新鲜度检查、格式化、辅助计算
"""
import pandas as pd

FRESHNESS_STALE_POINTS = 3

def check_metric_freshness(value_series: pd.Series, stale_points: int = FRESHNESS_STALE_POINTS) -> dict:
    """检测PE/PS序列是否连续stale_points个最新数据点数值完全未变化。"""
    s = value_series.dropna()
    s = s[s.index.dayofweek < 5]
    if len(s) == 0:
        return {"is_stale": False, "unchanged_count": 0, "last_value": None,
                "last_date": None, "first_unchanged_date": None}

    last_value = s.iloc[-1]
    unchanged_count = 0
    first_unchanged_date = None

    for i in range(len(s) - 1, -1, -1):
        if abs(s.iloc[i] - last_value) < 1e-9:
            unchanged_count += 1
            first_unchanged_date = s.index[i]
        else:
            break

    is_stale = unchanged_count >= stale_points
    return {
        "is_stale": is_stale,
        "unchanged_count": unchanged_count,
        "last_value": last_value,
        "last_date": s.index[-1] if len(s) > 0 else None,
        "first_unchanged_date": first_unchanged_date,
    }

def build_freshness_note(freshness: dict, metric_name: str) -> str:
    """生成新鲜度说明。"""
    if not freshness["is_stale"]:
        return ""

    return (f"\n> ⚠️ 数据新鲜度预警：{metric_name} 连续 {freshness['unchanged_count']} 个交易日未更新，"
            f"最后值为 {freshness['last_value']:.4f}（{freshness['first_unchanged_date'].strftime('%Y-%m-%d')} 起）\n")

def generate_action_sentence(disc: dict, divg: dict, vol: dict, zone_label: str) -> str:
    """生成执行动作建议。"""
    actions = []

    if disc.get("premium", 0) > 0.02:
        actions.append("⚠️ 溢价过高")
    if divg.get("divergence", False):
        actions.append("📉 量价背离")
    if vol.get("high", False):
        actions.append("🌊 波动偏高")

    if not actions:
        return "无特殊提示"
    return " | ".join(actions)

def _format_win_odds(r: dict) -> str:
    """格式化胜率和赔率。

    注意：calc_odds() 在 downside<=0（当前ERP已超P90，赔率理论上无限大）时返回 None，
    这种情况必须显示为 "∞"，而不是当成"数据缺失"显示 "─"——
    这里要和 valuation.py::build_unified_valuation_block 里的判断逻辑保持一致，
    否则仪表盘和详情页会出现同一标的赔率一个显示"∞"、一个显示"─"的不一致。
    """
    win = r.get("win_rate", 0.0)
    odds = r.get("odds_ratio", None)

    win_str = f"{win*100:.0f}%" if win == win else "─"

    if odds is None or odds != odds:
        # None / NaN 都可能来自 calc_odds 的"极高赔率"分支，统一显示为 ∞
        odds_str = "∞"
    elif odds > 100:
        odds_str = "∞"
    elif odds <= 0:
        odds_str = "─"
    else:
        odds_str = f"{odds:.2f}x"

    return f"胜{win_str}·赔{odds_str}"

def _format_range(r: dict) -> str:
    """格式化范围统计。"""
    dd = r.get("dd", None)
    rebound = r.get("rebound", None)

    if dd is None or dd != dd:
        dd_str = "─"
    else:
        dd_str = f"{dd*100:.1f}%"

    if rebound is None or rebound != rebound:
        rebound_str = "─"
    else:
        rebound_str = f"{rebound*100:.1f}%"

    return f"回撤{dd_str} · 反弹{rebound_str}"

def safe_action_markers(etf_df) -> dict:
    """从 etf_df 里尽量安全地提取 波动/量能/折溢价 标记，供仪表盘展示。

    etf_quality.py 里具体的列名未逐一核对过，这里做了多重 key 兜底 +
    try/except，任何字段取不到都优雅降级为"─"，不会因为列名对不上而报错。
    如果你发现某个标的的标记始终是"─"，多半是这里的候选列名跟
    analysis/etf_quality.py 实际输出的列名不一致，需要对照该文件调整
    _pick() 里的候选名单。
    """
    result = {"vol_icon": "─", "vol_flag": False,
              "divergence_flag": False,
              "premium_icon": "─", "premium_val": 0.0}

    if etf_df is None:
        return result

    def _pick(row_or_df, candidates):
        for c in candidates:
            try:
                v = row_or_df[c]
                if hasattr(v, "iloc"):
                    v = v.iloc[-1]
                if v == v:  # not NaN
                    return v
            except Exception:
                continue
        return None

    try:
        row = etf_df.iloc[-1] if hasattr(etf_df, "iloc") else etf_df

        vol_pct = _pick(row, ["vol_percentile", "volatility_percentile", "vol_pct"])
        if vol_pct is not None:
            if vol_pct >= 0.75:
                result["vol_icon"] = "🔴"
                result["vol_flag"] = True
            elif vol_pct >= 0.5:
                result["vol_icon"] = "🟠"
            else:
                result["vol_icon"] = "🟢"

        divg = _pick(row, ["divergence", "price_volume_divergence", "is_divergence"])
        if divg:
            result["divergence_flag"] = True

        premium = _pick(row, ["premium_discount", "premium_rate", "discount_premium"])
        if premium is not None:
            result["premium_val"] = premium
            if premium <= -0.02:
                result["premium_icon"] = "💎"
            elif premium < 0:
                result["premium_icon"] = "🟢"
            elif premium == 0:
                result["premium_icon"] = "🟡"
            elif premium < 0.02:
                result["premium_icon"] = "🟠"
            else:
                result["premium_icon"] = "🔴"
    except Exception:
        pass

    return result
