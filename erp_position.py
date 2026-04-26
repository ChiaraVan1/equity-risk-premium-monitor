import pandas as pd
import numpy as np
import os
from datetime import datetime
import requests


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


def analyze_and_suggest(code, name):
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

    shiller_block = build_shiller_block(code)
    trend_block   = build_trend_block(df, erp_series, code, quantiles)

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
{trend_block}
---
### 仓位建议

**{b_msg}** ({b_pct}%)
**{v_msg}** ({v_pct}%)
**{t_msg}** ({t_pct}%)

建议总仓位：**{total_pct}%**（泡沫底仓 {b_pct}% + 价值主力 {v_pct}% + 投机奇兵 {t_pct}%）
{shiller_block}{ps_block}"""
    print(md)
    return md


if __name__ == "__main__":
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

    report_list = []
    for code, name in indices:
        report_md = analyze_and_suggest(code, name)
        if report_md:
            report_list.append(report_md)

    if report_list:
        full_report = (
            "# ERP 策略每日监控报告\n"
            + LEGEND_BLOCK
            + "".join(report_list)
        )
        print("正在生成报告并准备推送...")
        send_to_wechat(full_report)
    else:
        print("❌ 未生成任何有效报告，请检查数据文件。")
