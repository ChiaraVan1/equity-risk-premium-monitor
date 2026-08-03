"""
report/markdown.py
报告生成模块：决策仪表盘、HTML生成、微信推送、报告保存
"""
import os
import requests
from datetime import datetime
import markdown2
from analysis.dividend_yield_analysis import build_dividend_yield_block

LEGEND_BLOCK = """
---
## 📖 图例说明

| 符号 | 含义 |
|:-----|:-----|
| 🟢 | 低估区间（机会） |
| 🟡 | 中性区间 |
| 🟠 | 偏高区间 |
| 🔴 | 高估区间 |
| 🚨 | 极端风险 / 强制止损 |
| 💰 | 止盈提示 |
| ✅ | 正常 |
| ⚠️ | 需要注意 |
| 🔎 | 观察提示（未持仓，仅供参考不建议操作） |
| 📌 | 自选持仓 |
| 🕓 | 数据新鲜度预警（连续多个更新点未变化） |

胜率🟢≥75% 🟡50-75% 🟠25-50% 🔴<25% · 折溢价💎大折 🟢折 🟡平 🟠溢 🔴大溢 · 波🔴高位 · 量⚠️背离

### 仓位拆分

- **泡沫底仓**：在极度高估时保留的最小仓位（防止踏空）
- **价值主力**：正常估值下的核心持仓
- **投机奇兵**：极度低估时的加仓部分
"""

_ZONE_ORDER = ["🟢 极度低估", "🟢 显著低估", "🟡 合理偏低", "🟠 合理区间", "🔴 高估/规避"]


def _pos_color_class(pct):
    if pct >= 80:
        return "pos-high"
    if pct >= 60:
        return "pos-mid"
    if pct >= 40:
        return "pos-low"
    return "pos-min"


def _is_actionable(r):
    """判断是否属于「🚨 需要处理」置顶区。
    exit_level>0 且实际持仓 → 需要操作；profit_level>0 → 无论是否持仓都提示止盈
    （止盈针对"涨多了要不要落袋"，未持仓就无所谓止盈，但这里保留原逻辑口径一致）。
    exit_level/profit_level 为 -1 表示"无价格数据，无法判断"，不是"确认无信号"（0），
    统一按"不可操作"处理，不进入 alerted。"""
    exit_hit = r.get("exit_level", 0) > 0 and r.get("holding", False)
    profit_hit = r.get("profit_level", 0) > 0
    return exit_hit or profit_hit


def build_summary_block(summary_list: list, output_format: str = "html") -> str:
    """生成决策仪表盘。"""
    if not summary_list:
        return ""

    date_str = datetime.now().strftime("%Y-%m-%d")

    alerted = [r for r in summary_list if _is_actionable(r)]
    unalerted = [r for r in summary_list if not _is_actionable(r)]
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
        "波🔴高位 · 量⚠️背离 · 🚨止损 💰止盈 🔎未持仓观察 · 🕓数据未更新 · 📌=持仓\n\n---"
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
                badge = "📌 " if r.get("holding") else ""
                zone_short = r.get("erp_zone", "")
                exit_line = r.get("exit_message", "") if r.get("exit_level", 0) > 0 else ""
                profit_line = r.get("profit_message", "") if r.get("profit_level", 0) > 0 else ""
                combined = "；".join(x for x in (exit_line, profit_line) if x)
                lead_icon = ("🚨" if r.get("holding") else "🔎") if r.get("exit_level", 0) > 0 else "💰"
                pos = r.get("position", {})
                lines.append(
                    f"\n{lead_icon} {badge}{r['name']} · {zone_short} · {pos.get('total', '─')}%\n"
                    f"　{combined}"
                )

        for group_label, match_fn in zone_groups:
            group_items = [r for r in unalerted if match_fn(r.get("erp_zone", ""))]
            if not group_items:
                continue
            lines.append(f"\n**{group_label} ({len(group_items)})**")
            for r in group_items:
                badge = "📌 " if r.get("holding") else ""
                pos = r.get("position", {})
                vol = r.get("vol_icon", "─")
                disc = r.get("premium_icon", "─")
                divg = "⚠️" if r.get("divergence_flag") else "─"
                action = r.get("action_sentence", "")

                extras = []
                if vol == "🔴":
                    extras.append(f"波{vol}")
                if disc in ("💎", "🔴"):
                    extras.append(f"折{disc}")
                if divg == "⚠️":
                    extras.append(f"量{divg}")
                extra_str = (" · " + " ".join(extras)) if extras else ""

                wo_str = r.get("win_odds_str", "─")
                range_str = r.get("range_str", "─")

                lines.append(
                    f"\n{badge}{r['name']} · {pos.get('total', '─')}%"
                    f"({pos.get('bubble', '─')}+{pos.get('value', '─')}+{pos.get('spec', '─')}){extra_str}"
                    f" · {wo_str} · {range_str} · {action}"
                )
                # 未持仓但回撤已触线：不进「需要处理」置顶区，但补一行观察提示，
                # 避免信息彻底消失。
                if r.get("exit_level", 0) > 0 and not r.get("holding", False):
                    obs_line = r.get("exit_message", "")
                    if obs_line:
                        lines.append(f"　{obs_line}")
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
                badge = "📌 " if r.get("holding") else ""
                zone_short = r.get("erp_zone", "")
                exit_line = r.get("exit_message", "") if r.get("exit_level", 0) > 0 else ""
                profit_line = r.get("profit_message", "") if r.get("profit_level", 0) > 0 else ""
                combined = "；".join(x for x in (exit_line, profit_line) if x)
                lead_icon = ("🚨" if r.get("holding") else "🔎") if r.get("exit_level", 0) > 0 else "💰"
                pos = r.get("position", {})
                rows_html.append(
                    f'<tr class="alert-row">'
                    f'<td class="col-name">{badge}{r["name"]}</td>'
                    f'<td class="col-pos">{pos.get("total", "─")}%</td>'
                    f'<td colspan="2" class="col-action">{lead_icon} {zone_short}｜{combined}</td>'
                    f'</tr>'
                )

        for group_label, match_fn in zone_groups:
            group_items = [r for r in unalerted if match_fn(r.get("erp_zone", ""))]
            if not group_items:
                continue
            rows_html.append(f'<tr><td colspan="4" class="section-header">{group_label}</td></tr>')
            for r in group_items:
                pos = r.get("position", {})
                total_pct = pos.get("total", 0)
                pos_cls = _pos_color_class(total_pct) if isinstance(total_pct, (int, float)) else "pos-min"
                vol = r.get("vol_icon", "─")
                disc = r.get("premium_icon", "─")
                action = r.get("action_sentence", "")
                badge = "📌 " if r.get("holding") else ""
                wo_str = r.get("win_odds_str", "─")
                range_str = r.get("range_str", "─")
                rows_html.append(
                    f'<tr>'
                    f'<td class="col-name">{badge}{r["name"]}</td>'
                    f'<td class="col-pos {pos_cls}">{total_pct}%<br>'
                    f'<span class="col-sub">{pos.get("bubble", "─")}+{pos.get("value", "─")}+{pos.get("spec", "─")}</span></td>'
                    f'<td class="col-sig">波{vol} 折{disc}</td>'
                    f'<td class="col-action">{wo_str} · {range_str} · {action}</td>'
                    f'</tr>'
                )
                # 未持仓但回撤已触线：不进「需要处理」置顶区，紧跟一行观察提示
                if r.get("exit_level", 0) > 0 and not r.get("holding", False):
                    obs_line = r.get("exit_message", "")
                    if obs_line:
                        rows_html.append(
                            f'<tr><td></td>'
                            f'<td colspan="3" class="col-action" style="color:#8b949e;">{obs_line}</td>'
                            f'</tr>'
                        )
        table_html = '<table class="dashboard-table">\n' + "\n".join(rows_html) + "\n</table>"
        return f"{header}\n{legend}\n\n{table_html}\n\n---\n"


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
.section-header {{ background-color: #eaf2fb; font-weight: bold; }}
.alert-row {{ background-color: #fff5f5; }}
.pos-high {{ color: #2e7d32; font-weight: bold; }}
.pos-mid {{ color: #558b2f; }}
.pos-low {{ color: #ef6c00; }}
.pos-min {{ color: #c62828; }}
.col-sub {{ font-size: 0.85em; color: #888; }}
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
    """推送摘要到微信（通过 Server酱 sctapi.ftqq.com）。

    【2026-08-02 修复】之前 requests.post() 的返回结果完全没有被检查——只要这次
    HTTP 请求本身没有网络层异常（DNS/连接失败/超时），无论 Server酱 服务端实际
    是否接受了这条推送，都会打印"✅ 微信推送成功"。Server酱的失败响应通常仍然是
    HTTP 200（把错误码放在响应体的 JSON 里，比如 sckey 无效、超出当日推送额度、
    内容超长被拒等），这类"HTTP 200 但业务失败"的情况完全被吞掉了——这正是日志
    显示"推送成功"但微信实际没收到消息的原因。现在改为：检查 HTTP 状态码 +
    解析响应体里的 code 字段（Server酱约定 0=成功），任何一处不对都打印出具体的
    响应内容，方便诊断（常见原因：SCT_KEY 失效、超出免费额度、desp 内容过长被拒）。
    """
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
        resp = requests.post(url, json=payload, timeout=10)

        if resp.status_code != 200:
            print(f"❌ 微信推送失败：HTTP {resp.status_code}，响应内容：{resp.text[:500]}")
            return

        try:
            result = resp.json()
        except Exception:
            print(f"❌ 微信推送响应无法解析为JSON（HTTP {resp.status_code}）：{resp.text[:500]}")
            return

        # Server酱约定：code == 0 表示服务端真正受理成功；非0一律视为失败，
        # 常见原因：sckey无效/过期、超出当日免费推送额度、desp内容过长被截断拒收。
        if result.get("code") == 0:
            print(f"✅ 微信推送成功（Server酱返回: {result.get('data', {})}）")
        else:
            print(f"❌ 微信推送失败：Server酱返回 code={result.get('code')}，"
                  f"message={result.get('message', '')}，完整响应：{result}")

    except Exception as e:
        print(f"❌ 微信推送失败（网络异常）: {e}")
