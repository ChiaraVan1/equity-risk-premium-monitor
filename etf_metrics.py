"""
etf_metrics.py
──────────────────────────────────────────────────────────────────────────────
ETF 执行质量补充模块
数据源: etf_metrics_daily_report.csv
补充维度（不替代 ERP 估值判断，仅辅助执行决策）：
  1. 折溢价率   — 当前买入/卖出的执行成本
  2. 换手背离   — 价格走势是否有成交量支撑
  3. 波动/回撤  — 当前风险水位（历史分位）
  4. 超额收益   — ETF 跟踪质量 + 近期相对基准动量

使用方式：
  在 erp_position.py 中：
    from etf_metrics import load_etf_metrics, build_etf_metrics_block
    _etf_df = load_etf_metrics()                          # 启动时加载一次
    block = build_etf_metrics_block("000688", _etf_df)    # 每个标的调用
──────────────────────────────────────────────────────────────────────────────
"""

import os
import pandas as pd


# ── ERP code → A股 ETF ts_code 映射 ──────────────────────────────────────────
# 一个 ERP 标的可能对应多只 ETF，取流动性最好的主力品种
# EWG/EEM 无对应 A 股 ETF，模块自动跳过
ERP_TO_ETF = {
    "000300": "510300.SH",   # 沪深300   → 华泰柏瑞沪深300ETF（旗舰）
    "000688": "588000.SH",   # 科创50    → 华夏科创50ETF
    "000922": "515180.SH",   # 中证红利  → 中证红利ETF（华泰）
    "399989": "512170.SH",   # 中证医疗  → 医疗ETF华宝
    "931071": "159819.SZ",   # 人工智能  → 人工智能ETF（华夏）
    "HSTECH": "513180.SH",   # 恒生科技  → 恒指科技ETF华夏
    "SPY":    "513500.SH",   # 标普500   → 标普500ETF
    "QQQ":    "159696.SZ",   # 纳斯达克  → 纳斯达克ETF
    "EWQ":    "513080.SH",   # MSCI法国  → 法国ETF
    "EWJ":    "513880.SH",   # MSCI日本  → 日本ETF
    "EWG":    None,           # 无对应A股ETF
    "EEM":    None,           # 无对应A股ETF
    "000069": "510150.SH",   # 消费80
    "930781": "516620.SH",   # 中证影视
    "000989": "159936.SZ",   # 全指可选
    "931139": "515650.SH",   # CS消费50
}

_metrics_cache = {}


def load_etf_metrics() -> pd.DataFrame | None:
    if _metrics_cache:
        return _metrics_cache.get("df")
    import urllib.request, urllib.error, io
    url = "https://github.com/ChiaraVan1/ETF_data_project/releases/latest/download/simple_etf_metrics.csv"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            df = pd.read_csv(io.StringIO(resp.read().decode()), index_col="ts_code")
        _metrics_cache["df"] = df
        return df
    except urllib.error.HTTPError as e:
        print(f"⚠️ ETF 指标文件下载失败（HTTP {e.code}）：{url}\n   ETF 执行质量模块将跳过，不影响主报告。")
    except urllib.error.URLError as e:
        print(f"⚠️ ETF 指标文件网络请求失败：{e.reason}\n   ETF 执行质量模块将跳过，不影响主报告。")
    except Exception as e:
        print(f"⚠️ ETF 指标文件加载异常：{e}\n   ETF 执行质量模块将跳过，不影响主报告。")
    _metrics_cache["df"] = None
    return None


# ── 单项指标解读辅助 ──────────────────────────────────────────────────────────

def _discount_comment(rate: float, quantile_1y: float) -> tuple[str, str]:
    """返回 (状态emoji+文字, 操作提示)"""
    pct = rate * 100
    q = quantile_1y

    if rate < -0.003:
        status = f"🟢 折价 {pct:.3f}%（1年{q*100:.0f}%分位低价区）"
        tip = "折价买入，执行成本占优"
    elif rate < -0.0005:
        status = f"🟡 轻微折价 {pct:.3f}%"
        tip = "小幅折价，正常范围"
    elif rate < 0.0005:
        status = f"⚪ 平价 {pct:.3f}%"
        tip = "平价，无额外成本"
    elif rate < 0.003:
        status = f"🟠 轻微溢价 {pct:.3f}%（1年{q*100:.0f}%分位）"
        tip = "小幅溢价，可接受"
    else:
        status = f"🔴 溢价 {pct:.3f}%（1年{q*100:.0f}%分位高溢区）"
        tip = "⚠️ 溢价偏高，建议等折价或限价委托"

    return status, tip


def _turnover_comment(rate: float, quantile: float, divergence: bool) -> str:
    """换手率 + 背离综合解读"""
    q_pct = quantile * 100
    lines = []

    if quantile >= 0.8:
        lines.append(f"🔥 换手率处于1年 {q_pct:.0f}% 高位，市场高度活跃")
    elif quantile >= 0.5:
        lines.append(f"🟡 换手率中等（1年 {q_pct:.0f}% 分位）")
    else:
        lines.append(f"🧊 换手率偏低（1年 {q_pct:.0f}% 分位），成交清淡")

    if divergence:
        lines.append("⚠️ **价格/换手背离**：价格走势缺乏成交量支撑，需警惕假突破或趋势反转")
    else:
        lines.append("✅ 价格/换手无背离，走势有量配合")

    return "；".join(lines)


def build_etf_metrics_block(erp_code: str, etf_df: pd.DataFrame | None) -> str:
    """
    按三个决策场景输出 ETF 执行质量补充块：
      A. 今天怎么下单（折溢价）
      B. 这波量是否真实（资金流）
      C. 风险水位 / 换只ETF（波动 + 超额收益）
    """
    if etf_df is None:
        return ""

    ts_code = ERP_TO_ETF.get(erp_code)
    if ts_code is None:
        return ""

    if ts_code not in etf_df.index:
        return f"\n> ⚠️ ETF {ts_code} 不在今日指标文件中，跳过执行质量分析。\n"

    row = etf_df.loc[ts_code]

    def safe(col, default=float("nan")):
        v = row.get(col, default)
        return default if pd.isna(v) else v

    etf_name         = safe("name", ts_code)
    discount_rate    = safe("latest_discount_rate", 0.0)
    discount_q1y     = safe("discount_quantile_1y", 0.5)
    discount_q3y     = safe("discount_quantile_3y", 0.5)
    discount_5d_chg  = safe("change_5d_discount", 0.0)
    discount_10d_chg = safe("change_10d_discount", 0.0)
    turnover_q       = safe("turnover_quantile", 0.5)
    acceleration     = safe("turnover_acceleration", float("nan"))
    divergence       = bool(safe("is_price_turnover_divergence", False))
    vol_q1y          = safe("volatility_quantile_1y", 0.5)
    dd_q1y           = safe("max_drawdown_quantile_1y", 0.5)
    ann_vol          = safe("annualized_volatility", 0.0)
    max_dd           = safe("max_drawdown", 0.0)
    excess_mean      = safe("excess_return_mean", 0.0)
    tracking_err     = safe("tracking_error", 0.0)
    ma_slope         = safe("ma_trend_slope", 0.0)
    ma5              = safe("excess_return_5d_ma", float("nan"))
    ma10             = safe("excess_return_10d_ma", float("nan"))
    ma20             = safe("excess_return_20d_ma", float("nan"))

    # ══════════════════════════════════════════════════════
    # A. 今天怎么下单 — 折溢价
    # ══════════════════════════════════════════════════════
    disc_pct = discount_rate * 100

    # 折溢价当前状态
    if discount_rate < -0.003:
        disc_icon, disc_label = "🟢", f"折价 {disc_pct:.3f}%"
        disc_action = "折价买入，执行成本占优，可直接下单"
    elif discount_rate < -0.0005:
        disc_icon, disc_label = "🟡", f"轻微折价 {disc_pct:.3f}%"
        disc_action = "小幅折价，正常范围，可下单"
    elif discount_rate < 0.0005:
        disc_icon, disc_label = "⚪", f"平价 {disc_pct:.3f}%"
        disc_action = "平价，无额外成本，可下单"
    elif discount_rate < 0.003:
        disc_icon, disc_label = "🟠", f"轻微溢价 {disc_pct:.3f}%"
        disc_action = "小幅溢价，可接受，建议挂单而非市价"
    else:
        disc_icon, disc_label = "🔴", f"溢价 {disc_pct:.3f}%"
        disc_action = "溢价偏高，建议等折价窗口或限价委托"

    # 折溢价历史分位
    q1y_pct = discount_q1y * 100
    q3y_pct = discount_q3y * 100
    if discount_q1y <= 0.2:
        q_label = f"🟢 1年{q1y_pct:.0f}%分位 — 历史少见低折价（买入成本极优）"
    elif discount_q1y <= 0.5:
        q_label = f"🟡 1年{q1y_pct:.0f}%分位 — 折价处于历史中低位"
    elif discount_q1y <= 0.8:
        q_label = f"🟠 1年{q1y_pct:.0f}%分位 — 折价处于历史中高位（偏贵）"
    else:
        q_label = f"🔴 1年{q1y_pct:.0f}%分位 — 历史罕见高溢价，等待"

    # 折溢价变化方向（窗口是否在开启/关闭）
    d5_str  = f"+{discount_5d_chg*100:.3f}%" if discount_5d_chg >= 0 else f"{discount_5d_chg*100:.3f}%"
    d10_str = f"+{discount_10d_chg*100:.3f}%" if discount_10d_chg >= 0 else f"{discount_10d_chg*100:.3f}%"
    if discount_5d_chg < -0.001:
        trend_label = "折价扩大 → 买入窗口正在打开"
    elif discount_5d_chg > 0.001:
        trend_label = "折价收窄 → 买入窗口趋于关闭，抓紧或等下次"
    else:
        trend_label = "折溢价近期稳定"

    # ══════════════════════════════════════════════════════
    # B. 这波量是否真实 — 资金流
    # ══════════════════════════════════════════════════════

    # 资金流分位（近1周成交额在52周分布中的位置）
    tq_pct = turnover_q * 100
    if turnover_q >= 0.8:
        tq_icon, tq_label = "🔥", f"1周成交额在52周中处于{tq_pct:.0f}%分位 — 市场高度活跃"
    elif turnover_q >= 0.5:
        tq_icon, tq_label = "🟡", f"1周成交额在52周中处于{tq_pct:.0f}%分位 — 活跃度中等"
    else:
        tq_icon, tq_label = "🧊", f"1周成交额在52周中处于{tq_pct:.0f}%分位 — 成交清淡"

    # 资金流加速度（本周 ÷ 本月，正常≈0.25）
    if pd.notna(acceleration):
        acc_pct = acceleration * 100
        if acceleration > 0.4:
            acc_label = f"🔥 {acc_pct:.0f}% — 本周放量明显（正常≈25%），资金加速涌入"
        elif acceleration > 0.25:
            acc_label = f"🟡 {acc_pct:.0f}% — 本周略高于月均，温和放量"
        elif acceleration > 0.1:
            acc_label = f"🟠 {acc_pct:.0f}% — 本周低于月均，资金动能偏弱"
        else:
            acc_label = f"🧊 {acc_pct:.0f}% — 本周明显缩量，谨慎追入"
    else:
        acc_label = "─ 数据不足"

    # 价格-资金背离
    if divergence:
        div_label = "⚠️ 背离 — 价格走势与成交量方向相反，需警惕假突破/假跌破"
    else:
        div_label = "✅ 无背离 — 价格与成交量方向一致，走势有量配合"

    # ══════════════════════════════════════════════════════
    # C. 风险水位 / 换只ETF — 波动 + 超额收益
    # ══════════════════════════════════════════════════════

    # 波动率分位
    vol_pct = vol_q1y * 100
    if vol_q1y >= 0.85:
        vol_icon, vol_label = "🔴", f"1年{vol_pct:.0f}%分位 — 波动率历史高位，单次建仓量要小，分批进"
    elif vol_q1y >= 0.6:
        vol_icon, vol_label = "🟠", f"1年{vol_pct:.0f}%分位 — 波动率中高，正常建仓"
    else:
        vol_icon, vol_label = "🟢", f"1年{vol_pct:.0f}%分位 — 波动率偏低"

    # 回撤分位
    dd_pct = dd_q1y * 100
    if dd_q1y >= 0.85:
        dd_label = f"1年{dd_pct:.0f}%分位 — 已充分下跌，风险较释放 ✅"
    elif dd_q1y >= 0.5:
        dd_label = f"1年{dd_pct:.0f}%分位 — 回撤中等，尚有下行空间"
    else:
        dd_label = f"1年{dd_pct:.0f}%分位 — 回撤偏小，下行风险未充分释放，别误以为安全 ⚠️"

    # 波动×回撤综合风险结论
    if vol_q1y >= 0.85 and dd_q1y >= 0.7:
        risk_conclusion = "高波动 + 充分回撤 → 适合分批建仓，风险已有释放"
    elif vol_q1y < 0.4 and dd_q1y < 0.3:
        risk_conclusion = "低波动 + 小回撤 → 表面平静但风险未释放，谨慎追高"
    elif vol_q1y >= 0.85:
        risk_conclusion = "波动率高位 → 控制单次建仓量，等波动率回落再加"
    else:
        risk_conclusion = "风险水位正常"

    # 超额收益质量（长期持有价值）
    excess_ann = excess_mean * 250  # 日均 → 年化近似
    if excess_mean > 0.01:
        excess_icon, excess_label = "✅", f"年化超额约 +{excess_ann:.1f}% — 长期跑赢基准，值得持有"
    elif excess_mean > 0:
        excess_icon, excess_label = "✅", f"年化超额约 +{excess_ann:.2f}% — 微正，跟踪正常"
    elif excess_mean > -0.01:
        excess_icon, excess_label = "🟡", f"年化超额约 {excess_ann:.2f}% — 轻微跑输，可接受"
    else:
        excess_icon, excess_label = "🔴", f"年化超额约 {excess_ann:.1f}% — 长期跑输基准，考虑换同指数更优ETF"

    # 超额收益近期动量（MA方向）
    if pd.notna(ma5) and pd.notna(ma20):
        if ma5 > ma20 + 0.001:
            ma_label = "📈 近期超额改善中（5日MA > 20日MA）"
        elif ma5 < ma20 - 0.001:
            ma_label = "📉 近期超额走弱中（5日MA < 20日MA）"
        else:
            ma_label = "➡️ 近期超额持平"
    else:
        ma_label = "─"

    te_label = "⚠️ 偏高，建议关注同类替代品" if tracking_err > 8 else "正常"

    # ══════════════════════════════════════════════════════
    # 综合执行建议（置顶）
    # ══════════════════════════════════════════════════════
    alerts = []
    if discount_rate > 0.003:
        alerts.append("溢价偏高→等折价或限价")
    if divergence:
        alerts.append("价格/量背离→确认量能再入")
    if vol_q1y >= 0.85:
        alerts.append("波动高位→分批建仓")
    if dd_q1y < 0.3 and vol_q1y < 0.4:
        alerts.append("低波低撤→风险未释放，别追高")
    if excess_mean < -0.01:
        alerts.append("长期跑输基准→考虑换ETF")

    if alerts:
        exec_line = "⚠️ 注意：" + " · ".join(alerts)
    else:
        exec_line = "✅ 执行条件正常"

    # MA数值行
    def _ma_str(v):
        return f"{v*100:.4f}%" if pd.notna(v) else "─"

    block = f"""
---
### ETF 执行质量（{etf_name} · {ts_code}）

**{exec_line}**

**A · 今天怎么下单（折溢价）**
> 折溢价决定你买入时的实际成本。折价 = 相当于打折买净值；溢价 = 多付钱。

- 当前：{disc_icon} {disc_label}（1年{q1y_pct:.0f}% / 3年{q3y_pct:.0f}%分位）→ {disc_action}
- 趋势：5日{d5_str} / 10日{d10_str} → {trend_label}
- 历史：{q_label}

**B · 这波量是否真实（资金流）**
> 价格涨但量在缩 = 假突破；价格跌但量在涨 = 可能在建仓。量配合才值得跟。

- 资金活跃度：{tq_icon} {tq_label}
- 资金加速度：{acc_label}
- 价格/量背离：{div_label}

**C · 风险水位 / 要不要换ETF（波动 + 超额收益）**
> 波动率高位时分批进；超额收益长期为负时考虑换同指数的其他ETF。

- 年化波动率：{ann_vol:.1f}%，{vol_icon} {vol_label}
- 最大回撤：{max_dd*100:.1f}%，{dd_label}
- 综合风险：{risk_conclusion}
- 超额收益：{excess_icon} {excess_label}
- 近期动量：{ma_label}（MA 5日{_ma_str(ma5)} / 10日{_ma_str(ma10)} / 20日{_ma_str(ma20)}）
- 跟踪误差：{tracking_err:.2f}% {te_label}
"""
    return block

# ── 本地测试 ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    df = load_etf_metrics()
    if df is not None:
        for code in ["000688", "000300", "399989"]:
            block = build_etf_metrics_block(code, df)
            if block:
                print(block)
                print("\n" + "="*80 + "\n")
