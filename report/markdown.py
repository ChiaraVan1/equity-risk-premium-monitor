"""
report/markdown.py
报告生成模块：HTML生成、微信推送、报告保存
"""
import os
import requests
from datetime import datetime
import markdown2
from dividend_yield import build_dividend_yield_block
from analysis.etf_quality import build_etf_quality_block

LEGEND_BLOCK = """
---
## 📖 图例说明

| 符号 | 含义 |
|:-----|:-----|
| 🟢 | 低估区间（机会） |
| 🟡 | 中性区间 |
| 🟠 | 偏高区间 |
| 🔴 | 高估区间 |
| 🚨 | 极端风险 |
| ✅ | 正常 |
| ⚠️ | 需要注意 |
| 🔎 | 观察提示（未持仓） |
| 📌 | 自选持仓 |

### 仓位拆分

- **泡沫底仓**：在极度高估时保留的最小仓位（防止踏空）
- **价值主力**：正常估值下的核心持仓
- **投机奇兵**：极度低估时的加仓部分
"""

_ZONE_ORDER = ["🟢 极度低估", "🟢 显著低估", "🟡 合理偏低", "🟠 合理区间", "🔴 高估/规避"]


def _format_position(item: dict) -> str:
    pos = item.get("position")
    if not pos:
        return "─"
    return f"{pos['total']}%({pos['bubble']}+{pos['value']}+{pos['spec']})"


def _format_dashboard_line(item: dict) -> str:
    code = item.get("code", "─")
    name = item.get("name", "─")
    holding = item.get("holding", False)
    win_odds = item.get("win_odds_str", "─")
    position = _format_position(item)
    vol_icon = item.get("vol_icon", "─")
    premium_icon = item.get("premium_icon", "─")
    divergence = "量⚠️" if item.get("divergence_flag") else ""
    range_str = item.get("range_str", "─")
    action = item.get("action_sentence", "")

    tag = "📌 " if holding else ""
    markers = " · ".join(
        m for m in [
            f"波{vol_icon}" if vol_icon != "─" else "",
            f"折{premium_icon}" if premium_icon != "─" else "",
            divergence,
        ] if m
    )

    parts = [f"{tag}{name} · {position}"]
    if markers:
        parts.append(markers)
    parts.append(win_odds)
    if range_str and range_str != "─":
        parts.append(range_str)
    if action and action != "无特殊提示":
        parts.append(action)

    line = " · ".join(parts)

    exit_level = item.get("exit_level", 0)
    exit_message = item.get("exit_message", "")
    if exit_level > 0 and exit_message:
        exit_icon = item.get("exit_icon", "🔎")
        line += f"\n　{exit_icon} {exit_message}"

    return f"- {line}"


def build_summary_block(summary_list: list, output_format: str = "html") -> str:
    """生成摘要块（仪表盘）。"""
    if not summary_list:
        return ""

    lines = ["### 📊 决策仪表盘\n"]

    # 🚨 需要处理：持仓且触发 L3 强制止损的标的，置顶单独展示
    urgent = [it for it in summary_list if it.get("holding") and it.get("exit_level", 0) == 3]
    if urgent:
        lines.append("\n#### 🚨 需要处理\n")
        for item in urgent:
            lines.append(_format_dashboard_line(item) + "\n")

    urgent_codes = {it["code"] for it in urgent}

    # 按估值区间分组（其余标的，排除已在「需要处理」里展示过的）
    zones = {}
    for item in summary_list:
        if item["code"] in urgent_codes:
            continue
        zone = item.get("erp_zone", "─")
        zones.setdefault(zone, []).append(item)

    ordered_zones = [z for z in _ZONE_ORDER if z in zones] + [z for z in zones if z not in _ZONE_ORDER]

    for zone in ordered_zones:
        lines.append(f"\n#### {zone}\n")
        for item in zones[zone]:
            lines.append(_format_dashboard_line(item) + "\n")

    return "".join(lines)


def build_etf_ai_interpretation(code: str, name: str, etf_df) -> str:
    """生成ETF解释块。"""
    if etf_df is None:
        return ""

    return build_etf_quality_block(code, etf_df)


def markdown_to_html(md_text: str, date_str: str) -> str:
    """将Markdown转为HTML。"""
    html_content = markdown2.markdown(md_text, extras=["tables", "fenced-code-blocks"])

    html_doc = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ERP 策略每日监控报告 - {date_str}</title>
<style>
body {{ font-family: "Segoe UI", "Microsoft YaHei", sans-serif; line-height: 1.6; max-width: 1200px; margin: 0 auto; padding: 20px; color: #333; }}
h1, h2, h3 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }}
table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
th, td {{ border: 1px solid #ddd; padding: 12px; text-align: left; }}
th {{ background-color: #3498db; color: white; }}
tr:nth-child(even) {{ background-color: #f9f9f9; }}
code {{ background-color: #f4f4f4; padding: 2px 6px; border-radius: 3px; }}
.emoji {{ font-size: 1.2em; }}
</style>
</head>
<body>
<h1>📊 ERP 策略每日监控报告</h1>
<p><strong>生成时间：</strong> {date_str}</p>
{html_content}
</body>
</html>"""
    return html_doc


def save_html_report(full_report_md: str, date_str: str):
    """保存HTML报告到本地。"""
    os.makedirs("./docs", exist_ok=True)

    html_content = markdown_to_html(full_report_md, date_str)

    file_path = f"./docs/report.html"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"✅ HTML报告已保存: {file_path}")


def send_to_wechat(summary_md: str, date_str: str):
    """推送摘要到微信。"""
    sct_key = os.getenv("SCT_KEY")
    if not sct_key:
        print("⚠️ 未设置 SCT_KEY，跳过微信推送")
        return

    try:
        url = f"https://sctapi.ftqq.com/{sct_key}.send"
        payload = {
            "title": f"ERP策略报告 - {date_str}",
            "desp": summary_md,
        }
        requests.post(url, json=payload, timeout=10)
        print("✅ 微信推送成功")
    except Exception as e:
        print(f"❌ 微信推送失败: {e}")
