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

REPORT_URL = "https://chiaravan1.github.io/equity-risk-premium-monitor/report.html"

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
#  报告构建模块
# ══════════════════════════════════════════════════════════════════════

def build_unified_valuation_block(df, code):
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

    cur_val = val_series.iloc[-1]
    cur_p_metric = price_metric_series.iloc[-1]

    percentile = (val_series < cur_val).mean()
    win_rate = percentile

    mean_val = val_series.mean()
    p10_val  = val_series.quantile(0.10)
    p90_val  = val_series.quantile(0.90)

    erp_range = max(p90_val - p10_val, 1e-4)
    erp_upside   = max(p90_val - cur_val, 0)
    if cur_val <= p10_val:
        erp_downside = erp_range
    else:
        erp_downside = cur_val - p10_val

    odds_ratio = erp_upside / erp_downside

    reward = max(cur_val - mean_val, 0) / (1 + abs(cur_val)) if cur_val > mean_val else 0
    risk   = min(erp_downside / (1 + abs(p10_val)), 1.0)

    avg_p = price_metric_series.mean()
    max_p = price_metric_series.max()
    min_p = price_metric_series.min()

    if percentile >= 0.90:
        zone_icon = "🟢"
        zone_name = "极度低估"
    elif percentile >= 0.75:
        zone_icon = "🟢"
        zone_name = "显著低估"
    elif percentile >= 0.50:
        zone_icon = "🟡"
        zone_name = "合理偏低"
    elif percentile >= 0.25:
        zone_icon = "🟠"
        zone_name = "合理区间"
    elif percentile >= 0.10:
        zone_icon = "🔴"
        zone_name = "严重高估"
    else:
        zone_icon = "🚨"
        zone_name = "危险泡沫"

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

    expected_return = win_rate * reward - (1 - win_rate) * risk

    block = f"""
---
### 核心估值决策（基于 {m_name} 框架）

> 方法：胜率 = {m_name}历史分位；赔率 = {m_name}还能走高多少（距P90）/ {m_name}可能回落多少（距P10）
> 当前 {m_name} = **{cur_val:.2%}**，历史分位 = **{percentile:.1%}** {zone_icon} **{zone_name}**

| 指标 | 数值 | 说明 |
|:-----|-----:|:-----|
| **胜率** | **{win_rate:.1%}** | [{m_name}视角] 历史有{win_rate:.1%}的时间比现在更贵（ERP更低） |
| **赔率（盈亏比）** | **{odds_ratio:.2f}x** | [价格视角] 潜在涨幅空间 / 潜在跌幅风险 |
| {m_name} 还能走高 | **+{erp_upside:.2%}** | [{m_name}视角] 距历史P90（{p90_val:.2%}）还差多少，=0表示已超P90 |
| {m_name} 可能回落 | **-{erp_downside:.2%}** | [{m_name}视角] 若回落至历史P10（{p10_val:.2%}），对应价格上涨空间 |
| 期望收益(估算) | **{expected_return:+.1%}** | [价格视角] 胜率×涨幅估算 − 败率×跌幅估算 |

**综合评级：{rating}**
"""
    return block


def build_trend_block(df, erp_series, code, quantiles):
    monthly_codes = {'EWQ', 'EWG', 'EWJ', 'EEM', 'HSTECH'}

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
#  仪表盘
# ══════════════════════════════════════════════════════════════════════

def build_summary_block(summary_list: list) -> str:
    if not summary_list:
        return ""

    date_str = datetime.now().strftime("%Y-%m-%d")

    def graded_icon(val, thresholds, icons):
        if val != val:
            return "─"
        for t, i in zip(thresholds, icons):
            if val >= t:
                return i
        return icons[-1]

    win_thresholds  = [0.75, 0.50, 0.25]
    win_icons       = ["🟢", "🟡", "🟠", "🔴"]
    odds_thresholds = [1.5,  1.0,  0.5]
    odds_icons      = ["🟢", "🟡", "🟠", "🔴"]

    def zone_short(z):
        part = z.split("(")[0].strip()
        return part.replace(" ", "")

    header = f"## 📊 决策仪表盘 · {date_str}"
    legend  = "胜率/赔率：🟢≥75% 🟡50-75% 🟠25-50% 🔴<25% · 赔率>1x为正\n"
    legend += "ETF折溢价：💎大幅折价 🟢折价 🟡平价 🟠溢价 🔴大幅溢价 ─无数据\n"
    legend += "估值区间：🟢低估(≥P75) 🟡合理偏低(P50-P75) 🟠合理偏高(P25-P50) 🔴高估(P10-P25) 🚨危险泡沫(<P10)"

    rows = []
    for r in summary_list:
        win  = r.get("win_rate", float("nan"))
        odds = r.get("odds",     float("nan"))
        win_str  = f"{win:.0%}"   if win  == win  else "─"
        odds_str = f"{odds:.1f}x" if odds == odds else "─"
        wi = graded_icon(win,  win_thresholds,  win_icons)
        oi = graded_icon(odds, odds_thresholds, odds_icons)
        zone = zone_short(r.get("erp_zone", "─"))
        etf  = r.get("etf_signal", "─")
        pos  = f"{r['b_pct']}+{r['v_pct']}+{r['t_pct']}={r['total_pct']}%"

        rows.append(
            f"{r['name']} {zone} · 胜{wi}{win_str} 赔{oi}{odds_str} · ETF{etf} · 仓{pos}"
        )

    body = "\n\n".join(rows)
    return f"{header}\n{legend}\n\n{body}\n\n---\n"


LEGEND_BLOCK = """
ERP = 1/PE − 无风险利率；PSY = 1/PS − 无风险利率。越高越便宜。

---
"""


# ══════════════════════════════════════════════════════════════════════
#  HTML 报告生成
# ══════════════════════════════════════════════════════════════════════

def markdown_to_html(md_text: str, date_str: str) -> str:
    """将 Markdown 报告转为独立 HTML 文件（无外部依赖，内嵌样式）"""
    import re

    def escape(s):
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def convert_md(text):
        lines = text.split("\n")
        html_lines = []
        in_table = False
        in_code = False

        for line in lines:
            # 代码块
            if line.startswith("```"):
                if not in_code:
                    html_lines.append('<pre><code>')
                    in_code = True
                else:
                    html_lines.append('</code></pre>')
                    in_code = False
                continue
            if in_code:
                html_lines.append(escape(line))
                continue

            # 表格
            if line.startswith("|"):
                if not in_table:
                    html_lines.append('<table>')
                    in_table = True
                cells = [c.strip() for c in line.split("|")[1:-1]]
                # 判断是否是分隔行
                if all(re.match(r'^:?-+:?$', c) for c in cells):
                    continue
                # 判断是否是表头（前一行没有table tr）
                tag = "td"
                row_html = "".join(f"<{tag}>{_inline(c)}</{tag}>" for c in cells)
                html_lines.append(f"<tr>{row_html}</tr>")
                continue
            else:
                if in_table:
                    html_lines.append('</table>')
                    in_table = False

            # 标题
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
            else:
                html_lines.append(f'<p>{_inline(line)}</p>')

        if in_table:
            html_lines.append('</table>')

        return "\n".join(html_lines)

    def _inline(text):
        import re
        # Bold
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        # Italic
        text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
        # Inline code
        text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
        return text

    body_html = convert_md(md_text)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ERP 监控报告 · {date_str}</title>
<style>
  :root {{
    --bg: #0d1117;
    --surface: #161b22;
    --surface2: #21262d;
    --border: #30363d;
    --text: #e6edf3;
    --text-muted: #8b949e;
    --green: #3fb950;
    --yellow: #d29922;
    --orange: #db6d28;
    --red: #f85149;
    --blue: #58a6ff;
    --accent: #1f6feb;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg);
    color: var(--text);
    font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
    font-size: 14px;
    line-height: 1.7;
    padding: 24px 16px;
  }}
  .container {{
    max-width: 900px;
    margin: 0 auto;
  }}
  .report-header {{
    border-bottom: 1px solid var(--border);
    padding-bottom: 16px;
    margin-bottom: 24px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }}
  .report-header h1 {{
    font-size: 18px;
    color: var(--blue);
    letter-spacing: 0.05em;
  }}
  .report-date {{
    color: var(--text-muted);
    font-size: 12px;
  }}
  h1 {{ font-size: 20px; color: var(--blue); margin: 24px 0 12px; }}
  h2 {{ font-size: 17px; color: var(--text); margin: 20px 0 10px; border-bottom: 1px solid var(--border); padding-bottom: 6px; }}
  h3 {{ font-size: 15px; color: var(--blue); margin: 16px 0 8px; }}
  h4 {{ font-size: 13px; color: var(--text-muted); margin: 12px 0 6px; }}
  p {{ margin: 6px 0; }}
  hr {{ border: none; border-top: 1px solid var(--border); margin: 20px 0; }}
  blockquote {{
    border-left: 3px solid var(--accent);
    padding: 6px 12px;
    color: var(--text-muted);
    background: var(--surface);
    border-radius: 0 4px 4px 0;
    margin: 8px 0;
    font-size: 13px;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    margin: 10px 0;
    font-size: 13px;
  }}
  td, th {{
    padding: 6px 10px;
    border: 1px solid var(--border);
    text-align: left;
  }}
  tr:nth-child(even) td {{ background: var(--surface); }}
  tr:first-child td {{ background: var(--surface2); font-weight: 600; color: var(--text-muted); }}
  strong {{ color: var(--text); }}
  code {{
    background: var(--surface2);
    padding: 2px 5px;
    border-radius: 3px;
    font-size: 12px;
    color: var(--blue);
  }}
  pre {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 12px;
    overflow-x: auto;
    margin: 10px 0;
  }}
  pre code {{ background: none; padding: 0; color: var(--text); }}
  .section-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px 20px;
    margin: 16px 0;
  }}
  .updated {{
    text-align: center;
    color: var(--text-muted);
    font-size: 11px;
    margin-top: 40px;
    padding-top: 16px;
    border-top: 1px solid var(--border);
  }}
</style>
</head>
<body>
<div class="container">
  <div class="report-header">
    <h1>📊 ERP 策略监控报告</h1>
    <span class="report-date">{date_str}</span>
  </div>
  <div class="content">
    {body_html}
  </div>
  <div class="updated">自动生成于 {date_str} · equity-risk-premium-monitor</div>
</div>
</body>
</html>"""


def save_html_report(full_report_md: str, date_str: str):
    """将完整 Markdown 报告保存为 HTML，输出到 docs/report.html"""
    os.makedirs("./docs", exist_ok=True)
    html = markdown_to_html(full_report_md, date_str)
    path = "./docs/report.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ HTML 报告已保存到 {path}")


# ══════════════════════════════════════════════════════════════════════
#  推送模块（只推仪表盘 + 链接）
# ══════════════════════════════════════════════════════════════════════

def send_to_wechat(summary_md: str, date_str: str):
    """只推送仪表盘 + 完整报告链接"""
    sct_key = os.getenv("SCT_KEY")
    if not sct_key:
        print("⚠️ 未找到 SCT_KEY，推送跳过。")
        return

    content = summary_md + f"\n\n📄 [查看完整报告]({REPORT_URL})"

    url = f"https://sctapi.ftqq.com/{sct_key}.send"
    data = {
        "title": f"ERP 决策报告 ({date_str})",
        "desp": content
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

    # ── 胜率/赔率 ──────────────────────────────────────────────────────
    if code == "HSTECH":
        _psy_s = _hstech_psy_s
        if _psy_s is not None:
            _cur_psy = _psy_s.iloc[-1]
            _p10_psy = _psy_s.quantile(0.10)
            _p90_psy = _psy_s.quantile(0.90)
            _win  = (_psy_s < _cur_psy).mean()
            _rng  = max(_p90_psy - _p10_psy, 1e-4)
            _up   = max(_p90_psy - _cur_psy, 0)
            _dn   = _rng if _cur_psy <= _p10_psy else (_cur_psy - _p10_psy)
            _odds = _up / _dn if _dn > 0 else 0.0
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
        _p10 = quantiles["P10"]; _p90 = quantiles["P90"]
        _win  = (erp_series < current_erp).mean()
        _rng  = max(_p90 - _p10, 1e-4)
        _up   = max(_p90 - current_erp, 0)
        _dn   = _rng if current_erp <= _p10 else (current_erp - _p10)
        _odds = _up / _dn if _dn > 0 else 0.0

    # ── ETF 折溢价信号 ──────────────────────────────────────────────────
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

    if summary_list is not None:
        summary_list.append({
            "name": name, "code": code,
            "erp_zone": erp_zone,
            "total_pct": total_pct,
            "b_pct": b_pct, "v_pct": v_pct, "t_pct": t_pct,
            "win_rate": _win,
            "odds": _odds,
            "etf_signal": _etf_signal,
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
    ]

    date_str = datetime.now().strftime("%Y-%m-%d")
    summary_list = []
    report_list  = []

    for code, name in indices:
        report_md = analyze_and_suggest(code, name, _etf_df, summary_list)
        if report_md:
            report_list.append(report_md)

    # 排序
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
        summary_md = build_summary_block(summary_list)

        full_report_md = (
            "# ERP 策略每日监控报告\n"
            + summary_md
            + LEGEND_BLOCK
            + "".join(report_list)
        )

        # 1. 保存完整 HTML 报告（推送到 gh-pages）
        save_html_report(full_report_md, date_str)

        if os.getenv("DRY_RUN") == "true":
            preview_path = "./output_preview.md"
            with open(preview_path, "w", encoding="utf-8") as f:
                f.write(full_report_md)
            print(f"✅ dry-run 模式，报告已写入 {preview_path}，不推送微信。")
        else:
            # 2. 微信只推送仪表盘 + 链接
            print("正在推送仪表盘到微信...")
            send_to_wechat(summary_md, date_str)
    else:
        print("❌ 未生成任何有效报告，请检查数据文件。")
