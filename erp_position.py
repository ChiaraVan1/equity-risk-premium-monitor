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

PRICE_CSV_URL = os.getenv(
    "PRICE_CSV_URL",
    "https://github.com/ChiaraVan1/ETF_data_project/releases/latest/download/etf_price.csv"
)


def load_ps_data():
    """加载 HSTECH PS 数据"""
    ps_path = "./data/ps_HSTECH.csv"
    if not os.path.exists(ps_path):
        return None
    df = pd.read_csv(ps_path, index_col=0, parse_dates=True)
    return df


def build_unified_valuation_block(df, code):
    """
    统一的估值决策模块：胜率 = 1 - Percentile；赔率 = 空间法
    支持 ERP (通用) 和 PSY (HSTECH)
    """
    is_hstech = (code == "HSTECH")
    
    if is_hstech:
        data_df = load_ps_data()
        if data_df is None:
            return "\n> ⚠️ 未找到 HSTECH PS 数据。\n"
        val_series = data_df["psy"].dropna()
        price_metric_series = data_df["ps"].dropna()
        m_name, p_name = "PSY", "PS"
    else:
        if df is None or len(df) == 0:
            return "\n> ⚠️ ERP 数据不足。\n"
        val_series = df['ERP'].dropna()
        price_metric_series = df['PE'].dropna()
        m_name, p_name = "ERP", "PE"

    if len(val_series) < 50:
        return "\n> ⚠️ 样本不足，无法计算胜率赔率。\n"

    # 当前值
    cur_val = val_series.iloc[-1]
    cur_p_metric = price_metric_series.iloc[-1]
    
    # 1. 胜率：ERP/PSY 越高越便宜，分位数本身即胜率（高分位 = 高胜率）
    percentile = (val_series < cur_val).mean()
    win_rate = percentile
    
    # 2. 赔率：ERP 分位空间法（与胜率口径统一，避免PE极端值失真）
    mean_val = val_series.mean()
    p10_val  = val_series.quantile(0.10)   # 历史最贵边界（下行风险锚）
    p90_val  = val_series.quantile(0.90)   # 历史最便宜边界（上行空间锚）

    # ERP 历史区间（用于赔率兜底）
    erp_range = max(p90_val - p10_val, 1e-4)

    # 上行空间：当前 ERP/PSY 距离历史 P90 还有多少余地（越便宜越大）
    erp_upside   = max(p90_val - cur_val, 0)
    # 下行风险：当前 ERP/PSY 距离历史 P10 还有多少空间（越贵越大）
    # 若已跌破或贴近 P10（历史最贵），说明风险已极大，用全区间作为下行风险锚
    if cur_val <= p10_val:
        erp_downside = erp_range   # 已破历史最贵边界，全区间为风险
    else:
        erp_downside = cur_val - p10_val   # 正常区间：距P10的实际距离

    # 赔率 = 上行空间 / 下行风险（均用 ERP 绝对值，量纲一致）
    odds_ratio = erp_upside / erp_downside

    # 期望收益展示用：ERP 回归均值 / 跌到P10 对应的涨跌估算（有界不超过100%）
    reward = max(cur_val - mean_val, 0) / (1 + abs(cur_val)) if cur_val > mean_val else 0
    risk   = min(erp_downside / (1 + abs(p10_val)), 1.0)

    # PE 统计（仅用于展示，不参与赔率计算）
    avg_p = price_metric_series.mean()
    max_p = price_metric_series.max()
    min_p = price_metric_series.min()
    
    # 估值区间判断
    if percentile >= 0.75:
        zone_icon = "🟢"
        zone_name = "极度低估"
    elif percentile >= 0.50:
        zone_icon = "🟡"
        zone_name = "合理偏低"
    elif percentile >= 0.25:
        zone_icon = "🟠"
        zone_name = "合理偏高"
    else:
        zone_icon = "🔴"
        zone_name = "严重高估"
    
    # 综合评级（win_rate = ERP历史分位，越高代表越便宜，胜率越高）
    if win_rate >= 0.75 and odds_ratio >= 1.5:
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
    
    # 期望值 = 胜率 × 预期涨幅 - 败率 × 潜在跌幅
    expected_return = win_rate * reward - (1 - win_rate) * risk
    
    block = f"""
---
### 核心估值决策（基于 {m_name} 框架）

> 方法：胜率 = {m_name}历史分位；赔率 = {m_name}上行空间(距P90) / 下行风险(距P10)
> 当前 {m_name} = **{cur_val:.2%}**，历史分位 = **{percentile:.1%}** {zone_icon} **{zone_name}**

| 指标 | 数值 | 说明 |
|:-----|-----:|:-----|
| **胜率** | **{win_rate:.1%}** | {m_name} 历史分位（高分位 = 高胜率） |
| **赔率（盈亏比）** | **{odds_ratio:.2f}x** | {m_name} 距P90上行空间 / 距P10下行风险 |
| {m_name} 上行空间 | **+{erp_upside:.2%}** | 距历史P90（{p90_val:.2%}）的 {m_name} 差值 |
| {m_name} 下行风险 | **-{erp_downside:.2%}** | 距历史P10（{p10_val:.2%}）的 {m_name} 差值 |
| 期望收益(估算) | **{expected_return:+.1%}** | 胜率×涨幅估算 − 败率×跌幅估算 |

| {p_name} 统计 | 数值 |
|:-------------|-----:|
| 当前 {p_name} | **{cur_p_metric:.2f}x** |
| 历史均值 | {avg_p:.2f}x |
| 历史最低 | {min_p:.2f}x |
| 历史最高 | {max_p:.2f}x |

**综合评级：{rating}**
"""
    return block


# ── 顶部总览表（飞行仪表盘）────────────────────────────────────────────────
def build_summary_block(summary_list: list) -> str:
    """决策仪表盘：胜率/赔率/四象限/趋势/ETF折溢价一屏决策"""
    if not summary_list:
        return ""

    def zone_short(z):
        return z.split("(")[0].strip()

    rows = []
    for r in summary_list:
        win      = r.get("win_rate", float("nan"))
        odds     = r.get("odds",     float("nan"))
        trend    = r.get("trend",    "─")
        quadrant = r.get("quadrant", "─")
        etf_sig  = r.get("etf_signal", "─")
        win_str  = f"{win:.0%}"    if win  == win  else "─"
        odds_str = f"{odds:.1f}x"  if odds == odds else "─"
        pos_str  = f"{r['b_pct']}+{r['v_pct']}+{r['t_pct']}={r['total_pct']}%"
        rows.append(
            f"| {r['name']} ({r['code']}) "
            f"| {zone_short(r['erp_zone'])} "
            f"| {win_str} "
            f"| {odds_str} "
            f"| {trend} "
            f"| {quadrant} "
            f"| {etf_sig} "
            f"| {pos_str} |"
        )

    rows_md = "\n".join(rows)
    date_str = datetime.now().strftime("%m-%d")

    legend = """**四象限**：胜率≥50% = 历史上便宜的时候；赔率≥1x = 上行空间 > 下行风险
    🟢持有/加仓（高胜率+高赔率）· 🟡持有勿追（高胜率+低赔率，空间薄）
    🟠观望/投机（低胜率+高赔率，估值贵但有反弹）· 🔴减仓/规避（低胜率+低赔率）
    
    **趋势**：近10日ERP斜率方向
    ↗↗快速走便宜 · ↗缓慢走便宜 · ─横盘 · ↘缓慢走贵 · ↘↘快速走贵
    
    **ETF**：💎显著折价 · 🟢轻微折价 · 🟡平价 · 🟠轻微溢价 · 🔴显著溢价
    """

    return (
        f"## 📊 决策仪表盘 · {date_str}\n\n"
        f"{legend}\n\n"
        f"| 标的 | 估值区间 | 胜率 | 赔率 | 趋势 | 四象限操作 | ETF | 仓位(底+值+投) |\n"
        f"|:----|:--------|-----:|-----:|:----:|:----------|:---:|:-------------|\n"
        f"{rows_md}\n\n"
        f"---\n"
    )


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
    """
    仅对 SPY 生成 Shiller 补充模块。
    返回 markdown 字符串，失败时返回空字符串。
    """
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
        ps_df = load_ps_data()
        if ps_df is not None and "psy" in ps_df.columns:
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
        ps_df = load_ps_data()
        if ps_df is not None:
            ps_df = ps_df.dropna(subset=["ps"])
            if len(ps_df) >= 6:
                cur_ps = ps_df["ps"].iloc[-1]
                ps_pct = (ps_df["ps"] < cur_ps).mean()
                ps_zone = (
                    "🟢 极度低估 (历史低位)" if ps_pct <= 0.10 else
                    "🟢 显著低估" if ps_pct <= 0.25 else
                    "🟡 合理偏低" if ps_pct <= 0.50 else
                    "🟠 合理偏高" if ps_pct <= 0.75 else
                    "🔴 严重高估" if ps_pct <= 0.90 else
                    "🚨 危险泡沫 (历史高位)"
                )

                psy_rows = ""
                cur_psy = np.nan
                psy_zone = "N/A"
                psy_pct = np.nan
                if "psy" in ps_df.columns:
                    psy_s = ps_df["psy"].dropna()
                    if len(psy_s) >= 6:
                        cur_psy = psy_s.iloc[-1]
                        cur_rf = ps_df["rf"].iloc[-1] if "rf" in ps_df.columns else np.nan
                        psy_pct = (psy_s < cur_psy).mean()
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
    trend_block = build_trend_block(df, erp_series, code, quantiles)
    etf_block = build_etf_metrics_block(code, etf_df)
    
    # 核心估值决策模块（统一方案）
    unified_block = build_unified_valuation_block(df, code)

    # ── 追加到顶部总览 ────────────────────────────────────────────────────────
    _p10 = quantiles["P10"]; _p90 = quantiles["P90"]
    _win  = (erp_series < current_erp).mean()
    _rng  = max(_p90 - _p10, 1e-4)
    _up   = max(_p90 - current_erp, 0)
    _dn   = _rng if current_erp <= _p10 else (current_erp - _p10)
    _odds = _up / _dn if _dn > 0 else 0.0

    # 趋势：用近10日ERP线性斜率，标准化到ERP区间，判断强弱方向
    _erp_recent = df["ERP"].dropna().tail(10)
    if len(_erp_recent) >= 3:
        _slope = np.polyfit(np.arange(len(_erp_recent)), _erp_recent.values, 1)[0]
        _slope_norm = _slope / _rng  # 标准化：每步变动占ERP区间的比例
        if   _slope_norm >  0.02: _trend = "↗↗"   # 快速走便宜
        elif _slope_norm >  0.005: _trend = "↗"   # 缓慢走便宜
        elif _slope_norm < -0.02: _trend = "↘↘"   # 快速走贵
        elif _slope_norm < -0.005: _trend = "↘"   # 缓慢走贵
        else:                      _trend = "─"    # 横盘
    else:
        _trend = "─"

    # 四象限操作建议（胜率阈值=50%，赔率阈值=1.0x）
    _high_win  = _win  >= 0.50
    _high_odds = _odds >= 1.0
    if   _high_win and _high_odds:    _quadrant = "🟢持有/加仓"    # 便宜且空间大
    elif _high_win and not _high_odds: _quadrant = "🟡持有勿追"    # 便宜但空间薄
    elif not _high_win and _high_odds: _quadrant = "🟠观望/投机"   # 贵但有反弹空间
    else:                              _quadrant = "🔴减仓/规避"   # 贵且空间小

    # ETF折溢价执行信号（从 etf_df 取，找不到则省略）
    _etf_signal = "─"
    if etf_df is not None:
        try:
            _row = etf_df[etf_df["code"].str.contains(code, na=False)]
            if len(_row) == 0:  # 尝试用名字匹配
                _row = etf_df[etf_df["name"].str.contains(code, na=False)]
            if len(_row) > 0:
                _prem = float(_row.iloc[0].get("premium_rate", float("nan")))
                if _prem == _prem:  # not nan
                    if   _prem < -0.02: _etf_signal = "💎"
                    elif _prem < -0.005: _etf_signal = "🟢"
                    elif _prem <  0.005: _etf_signal = "🟡"
                    elif _prem <  0.02:  _etf_signal = "🟠"
                    else:                _etf_signal = "🔴"
        except Exception:
            pass

    if summary_list is not None:
        summary_list.append({
            "name": name, "code": code,
            "erp_zone": erp_zone,
            "total_pct": total_pct,
            "b_pct": b_pct, "v_pct": v_pct, "t_pct": t_pct,
            "win_rate": _win,
            "odds": _odds,
            "quadrant": _quadrant,
            "trend": _trend,
            "etf_signal": _etf_signal,
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
{unified_block}{trend_block}
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
            + build_summary_block(summary_list)
            + LEGEND_BLOCK
            + "".join(report_list)
        )
        print("正在生成报告并准备推送...")
        send_to_wechat(full_report)
    else:
        print("❌ 未生成任何有效报告，请检查数据文件。")
