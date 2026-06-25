import pandas as pd
import numpy as np
import os
from datetime import datetime
import requests

from etf_metrics import load_etf_metrics, build_etf_metrics_block, ERP_TO_ETF


# ══════════════════════════════════════════════════════════════════════
#  常量 & 配置
# ══════════════════════════════════════════════════════════════════════

SHILLER_PATH = os.getenv("SHILLER_PATH", "./data/ie_data.xls")

# CAPE 分组区间定义（用于 Shiller 长期回报锚）
_CAPE_BINS   = [0,  10,  15,  20,  25,  30,  35,  40,  999]
_CAPE_LABELS = ['<10', '10-15', '15-20', '20-25', '25-30', '30-35', '35-40', '>40']


# ══════════════════════════════════════════════════════════════════════
#  Shiller CAPE 长期回报锚模块
# ══════════════════════════════════════════════════════════════════════

_shiller_cache = {}


def _load_shiller():
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
    ps_path = "./data/ps_HSTECH.csv"
    if not os.path.exists(ps_path):
        return None
    df = pd.read_csv(ps_path, index_col=0, parse_dates=True)
    return df


# ══════════════════════════════════════════════════════════════════════
#  赔率计算（ERP绝对值法）
# ══════════════════════════════════════════════════════════════════════

def calc_odds(cur_val, val_series):
    """
    赔率 = ERP回落盈利空间 / ERP走高亏损空间
    盈利空间 = 当前ERP - P10  （ERP回落到历史最贵边界的距离）
    亏损空间 = P90 - 当前ERP  （ERP走高到历史最便宜边界的距离）

    边界处理：
    - ERP已超P90（极度低估）：downside<=0，返回None
    - ERP已破P10（极度高估）：upside<=0，返回0.0
    """
    p10_val = val_series.quantile(0.10)
    p90_val = val_series.quantile(0.90)

    upside   = cur_val - p10_val   # ERP回落盈利空间
    downside = p90_val - cur_val   # ERP走高亏损空间

    if downside <= 0:
        return None   # 已超P90，极度低估，赔率极高
    elif upside <= 0:
        return 0.0    # 已破P10，极度高估，赔率为零
    else:
        return upside / downside


# ══════════════════════════════════════════════════════════════════════
#  报告构建模块
# ══════════════════════════════════════════════════════════════════════

def build_unified_valuation_block(df, code):
    """
    统一的估值决策模块
    胜率 = ERP历史分位
    赔率 = (当前ERP - P10) / (P90 - 当前ERP)，ERP绝对值法
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

    cur_val      = val_series.iloc[-1]
    cur_p_metric = price_metric_series.iloc[-1]
    avg_p        = price_metric_series.mean()
    max_p        = price_metric_series.max()
    min_p        = price_metric_series.min()

    p10_val = val_series.quantile(0.10)
    p90_val = val_series.quantile(0.90)

    # 胜率
    win_rate = (val_series < cur_val).mean()

    # 赔率（ERP绝对值法）
    odds_ratio = calc_odds(cur_val, val_series)
    if odds_ratio is None:
        odds_str = "极高（已超P90极度低估区）"
    else:
        odds_str = f"{odds_ratio:.2f}x"

    upside   = cur_val - p10_val
    downside = p90_val - cur_val

    # 估值区间
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

    # 综合评级
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
    """
    生成近10个月末 ERP 数据点的趋势模块。
    """
    if code == "HSTECH":
        ps_df = load_ps_data()
        if ps_df is None or "psy" not in ps_df.columns:
            return ""
        recent_psy = ps_df[ps_df["psy"].notna()][["ps", "psy"]].tail(10)
        if len(recent_psy) < 2:
            return ""
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

| 月份 | PS | PSY | 环比 |
|:-----|---:|----:|:-----|
{chr(10).join(psy_rows)}
"""

    valid = df[df['ERP'].notna()][['Date', 'ERP', 'PE']].copy()
    if len(valid) < 2:
        return ""

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

    block = f"""
---
### 近10月 ERP 趋势

> 趋势方向：**{trend_icon}**，区间变化：**{delta_str}**

| 月份 | PE | ERP | 环比变化 |
|:-----|---:|----:|:---------|
{rows_md}
"""
    return block


# ══════════════════════════════════════════════════════════════════════
#  仪表盘 & 图例
# ══════════════════════════════════════════════════════════════════════

# ── 持仓分类映射 ──────────────────────────────────────────────────────────────
HOLDING_CATEGORY = {
    "000300": "低估-宽基",
    "000688": "科技-A股",
    "000922": "",
    "399989": "低估-医疗",
    "931071": "",
    "000069": "低估-消费",
    "930781": "",
    "000989": "",
    "931139": "",
    "SPY":    "",
    "QQQ":    "",
    "EWQ":    "",
    "EWG":    "",
    "EWJ":    "",
    "EEM":    "",
    "HSTECH": "低估-港股科技",
    "399967": "低估-军工",
    "931066": "",
    "930794": "",
    "931946": "",
    "930598": "低估-资源",
}


# ── 动作句生成 ────────────────────────────────────────────────────────────────

def generate_action_sentence(disc, divg, vol, zone_label):
    """
    根据 ETF 执行质量信号和估值区间，生成操作动作句。

    参数直接读取已有变量，不重新计算：
      disc       = etf_discount   （折溢价 emoji）
      divg       = etf_divergence （量价背离 emoji）
      vol        = etf_vol        （波动 emoji）
      zone_label = 估值区间字符串
    """
    # 规避区直接返回
    if zone_label and (zone_label.startswith("🔴") or zone_label.startswith("🚨")):
        return "规避，不建仓"

    # 前缀：量价背离
    prefix = "等量能确认，" if divg == "⚠️" else ""

    # 中段：波动
    if vol == "🔴":
        mid = "分批建仓"
    elif vol == "🟠":
        mid = "建仓，注意分批"
    else:
        mid = "一次建仓"

    # 后缀：折溢价
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

    # 估值分组定义（顺序即显示顺序）
    zone_groups = [
        ("🟢 极度低估", lambda z: z.startswith("🟢 极度低估")),
        ("🟢 显著低估", lambda z: z.startswith("🟢 显著低估")),
        ("🟡 合理偏低", lambda z: z.startswith("🟡")),
        ("🟠 合理区间", lambda z: z.startswith("🟠")),
        ("🔴 高估/规避", lambda z: z.startswith("🔴") or z.startswith("🚨")),
    ]

    header = f"## 📊 决策仪表盘 · {date_str}"
    legend = (
        "胜率/赔率：🟢≥75% 🟡50-75% 🟠25-50% 🔴<25% · 赔率>1x为正 · 🟢极高=已超P90\n\n"
        "折溢价：💎大折价 🟢折价 🟡平价 🟠溢价 🔴大溢价 · 量：✅无背离 ⚠️背离 · 波动：🟢低 🟠中高 🔴高位分批\n\n"
        "估值区间：🟢低估(≥P75) 🟡合理偏低(P50-P75) 🟠合理偏高(P25-P50) 🔴高估(P10-P25) 🚨危险泡沫(<P10)\n\n---"
    )

    if output_format == "markdown":
        # 纯文本版，用于微信推送
        lines = [header, "", legend, ""]
        for group_label, match_fn in zone_groups:
            group_items = [r for r in summary_list if match_fn(r.get("erp_zone", ""))]
            if not group_items:
                continue
            lines.append(f"\n**{group_label}**")
            for r in group_items:
                disc       = r.get("etf_discount",   "─")
                divg       = r.get("etf_divergence", "─")
                vol        = r.get("etf_vol",        "─")
                zone_label = r.get("erp_zone", "")
                action     = generate_action_sentence(disc, divg, vol, zone_label)
                cat        = HOLDING_CATEGORY.get(r["code"], "")
                cat_str    = f" [{cat}]" if cat else ""
                pos_structure = f"{r['b_pct']}+{r['v_pct']}+{r['t_pct']}"
                lines.append(
                    f"\n{r['name']} · {r['total_pct']}%({pos_structure}) · "
                    f"量{divg} 波{vol} 折{disc} · {action}{cat_str}"
                )
        return "\n".join(lines) + "\n\n---\n"

    else:
        # HTML表格版，用于report.html
        rows_html = []
        for group_label, match_fn in zone_groups:
            group_items = [r for r in summary_list if match_fn(r.get("erp_zone", ""))]
            if not group_items:
                continue

            rows_html.append(
                f'<tr><td colspan="4" class="section-header">{group_label}</td></tr>'
            )

            for r in group_items:
                code        = r.get("code", "")
                etf_ticker  = ERP_TO_ETF.get(code, "─")
                etf_display = etf_ticker.split(".")[0] if "." in str(etf_ticker) else str(etf_ticker)

                total_pct     = r["total_pct"]
                pos_cls       = pos_color_class(total_pct)
                pos_structure = f"{r['b_pct']}+{r['v_pct']}+{r['t_pct']}"

                disc       = r.get("etf_discount",   "─")
                divg       = r.get("etf_divergence", "─")
                vol        = r.get("etf_vol",        "─")
                zone_label = r.get("erp_zone", "")

                action  = generate_action_sentence(disc, divg, vol, zone_label)
                cat     = HOLDING_CATEGORY.get(code, "")
                cat_str = f" [{cat}]" if cat else ""

                rows_html.append(
                    f'<tr>'
                    f'<td class="col-name">{r["name"]}<br><span class="col-etf">{etf_display}</span></td>'
                    f'<td class="col-pos {pos_cls}">{total_pct}%<br><span class="col-sub">{pos_structure}</span></td>'
                    f'<td class="col-sig">量{divg}&nbsp;波{vol}&nbsp;折{disc}</td>'
                    f'<td class="col-action">{action}{cat_str}</td>'
                    f'</tr>'
                )

        table_html = (
            '<table class="dashboard-table">\n'
            + "\n".join(rows_html)
            + "\n</table>"
        )

        return f"{header}\n{legend}\n\n{table_html}\n\n---\n"


LEGEND_BLOCK = """
ERP = 1/PE − 无风险利率；PSY = 1/PS − 无风险利率。越高越便宜。
赔率 = ERP回落盈利空间（当前ERP − P10） / ERP走高亏损空间（P90 − 当前ERP）

---
"""


# ══════════════════════════════════════════════════════════════════════
#  推送模块
# ══════════════════════════════════════════════════════════════════════

REPORT_URL = "https://chiaravan1.github.io/equity-risk-premium-monitor/report.html"


def markdown_to_html(md_text: str, date_str: str) -> str:
    """将完整 Markdown 报告转为独立 HTML（内嵌样式，无外部依赖）"""
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
                # 直通原生 HTML（仪表盘表格等）
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

  /* 仪表盘表格 */
  .dashboard-table {{ width: 100%; border-collapse: collapse; }}
  .dashboard-table tr {{ border-bottom: 1px solid #21262d; }}
  .dashboard-table td {{ padding: 10px 8px; vertical-align: middle; border: none; }}

  /* 列宽 */
  .col-name  {{ width: 110px; font-weight: bold; }}
  .col-etf   {{ color: #8b949e; font-size: 11px; }}
  .col-pos   {{ width: 64px; text-align: center; font-size: 20px; font-weight: bold; }}
  .col-sub   {{ font-size: 10px; color: #8b949e; }}
  .col-sig   {{ width: 120px; }}
  .col-action {{ font-size: 12px; }}

  /* 仓位颜色 */
  .pos-high   {{ color: #3fb950; }}
  .pos-mid    {{ color: #d29922; }}
  .pos-low    {{ color: #e3b341; }}
  .pos-min    {{ color: #f85149; }}

  /* 分组标题 */
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
    """保存完整报告为 HTML 到 ./docs/report.html"""
    os.makedirs("./docs", exist_ok=True)
    html = markdown_to_html(full_report_md, date_str)
    with open("./docs/report.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ HTML 报告已保存到 ./docs/report.html")


def send_to_wechat(summary_md: str, date_str: str):
    """只推送仪表盘 + 完整报告链接，避免超出方糖字段长度限制"""
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

    # HSTECH：用 PSY 数据覆盖
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

    # ── 胜率/赔率（供 summary_list 使用）─────────────────────────────
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
    _etf_signal = "─"
    if etf_df is not None:
        try:
            _ts = ERP_TO_ETF.get(code)
            if _ts and _ts in etf_df.index:
                _prem = float(etf_df.loc[_ts].get("latest_discount_rate", float("nan")))
                if _prem == _prem:
                    if   _prem < -0.02:  _etf_signal = "💎"
                    elif _prem < -0.005: _etf_signal = "🟢"
                    elif _prem <  0.005: _etf_signal = "🟡"
                    elif _prem <  0.02:  _etf_signal = "🟠"
                    else:                _etf_signal = "🔴"
        except Exception:
            pass

    # ── ETF 执行质量三信号 ────────────────────────────────────────────
    _etf_discount_signal   = "─"
    _etf_divergence_signal = "─"
    _etf_vol_signal        = "─"
    if etf_df is not None:
        try:
            _ts = ERP_TO_ETF.get(code)
            if _ts and _ts in etf_df.index:
                _row = etf_df.loc[_ts]

                # 折溢价
                _prem = float(_row.get("latest_discount_rate", float("nan")))
                if _prem == _prem:
                    if   _prem < -0.02:  _etf_discount_signal = "💎"
                    elif _prem < -0.005: _etf_discount_signal = "🟢"
                    elif _prem <  0.005: _etf_discount_signal = "🟡"
                    elif _prem <  0.02:  _etf_discount_signal = "🟠"
                    else:                _etf_discount_signal = "🔴"

                # 量价背离
                _div = _row.get("is_price_turnover_divergence", float("nan"))
                if _div == _div:
                    _etf_divergence_signal = "⚠️" if int(_div) == 1 else "✅"

                # 波动率分位
                _vq = float(_row.get("volatility_quantile_1y", float("nan")))
                if _vq == _vq:
                    if   _vq >= 0.85: _etf_vol_signal = "🔴"
                    elif _vq >= 0.60: _etf_vol_signal = "🟠"
                    else:             _etf_vol_signal = "🟢"
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
            "etf_signal": _etf_signal,
            "etf_discount":   _etf_discount_signal,
            "etf_divergence": _etf_divergence_signal,
            "etf_vol":        _etf_vol_signal,
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
"""
        else:
            header_block = f"""
---
# ═══ {name} ({code}) ═══
> 数据源：PS / PSY（营收口径），ERP 数据已停止更新不再展示。　{current_date}
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
"""

    # ── 组装各模块 ────────────────────────────────────────────────────
    unified_block = build_unified_valuation_block(df, code)
    trend_block   = build_trend_block(df, erp_series, code, quantiles)
    etf_block     = build_etf_metrics_block(code, etf_df)
    shiller_block = build_shiller_block(code)

    md = f"""{header_block}
---
### 仓位建议

**{b_msg}** ({b_pct}%)
**{v_msg}** ({v_pct}%)
**{t_msg}** ({t_pct}%)

建议总仓位：**{total_pct}%**（泡沫底仓 {b_pct}% + 价值主力 {v_pct}% + 投机奇兵 {t_pct}%）
{unified_block}{trend_block}{etf_block}{shiller_block}"""
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
        ("399989", "中证医疗"),
        ("931071", "人工智能"),
        ("000069", "消费80"),
        ("930781", "中证影视"),
        ("000989", "全指可选"),
        ("931139", "CS消费50"),
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
        ("931946", "畜牧养殖"),
        ("930598", "稀土产业"),
    ]

    summary_list = []
    report_list  = []
    for code, name in indices:
        report_md = analyze_and_suggest(code, name, _etf_df, summary_list)
        if report_md:
            report_list.append(report_md)

    # 仪表盘排序：估值越低估排越前，同估值区间内按胜率降序
    _zone_order = {
        "🟢 极度低估": 0,
        "🟢 显著低估": 1,
        "🟡 合理偏低": 2,
        "🟠 合理区间": 3,
        "🔴 严重高估": 4,
        "🚨 危险泡沫": 5,
    }
    def _sort_key(r):
        zone_str  = r.get("erp_zone", "")
        zone_rank = next(
            (v for k, v in _zone_order.items() if zone_str.startswith(k)),
            99
        )
        win = r.get("win_rate", 0.0)
        win = win if win == win else 0.0
        return (zone_rank, -win)

    summary_list.sort(key=_sort_key)

    if report_list:
        date_str     = datetime.now().strftime("%Y-%m-%d")
        summary_html = build_summary_block(summary_list, output_format="html")
        summary_wechat = build_summary_block(summary_list, output_format="markdown")

        full_report = (
            "# ERP 策略每日监控报告\n"
            + summary_html
            + LEGEND_BLOCK
            + "".join(report_list)
        )

        # 始终生成 HTML 报告（供 gh-pages 托管）
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
