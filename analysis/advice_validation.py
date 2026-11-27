#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""建议层验证：gh-pages 每日报告快照里的建议 vs 之后实际走势。

注意：报告格式随时间变化——"动作：可买一档/只持有/减仓"字段只在 2026-11-10 之后出现，
因此动作类建议目前样本仅十余个交易日，不具备统计结论的条件；
真正有历史可供回溯的是「分位」与「网格档位·下一买点」两类字段。
"""
import csv
import os
import re
import subprocess
from statistics import median

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "analysis", "results")
BRANCH = "origin/gh-pages"
HORIZONS = [5, 10, 20, 40, 60]


def sh(*args):
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True).stdout


def parse_report(html):
    m = re.search(r"生成时间[：:]\s*(?:<strong>)?(\d{4}-\d{2}-\d{2})", html)
    gen = m.group(1) if m else None
    rows = []
    for block in html.split("<h2>")[1:]:
        head = block.split("</h2>")[0]
        cm = re.search(r"（([A-Za-z0-9._]+)）", head)
        if not cm:
            continue
        name = re.sub(r"（.*", "", head).strip()
        code = cm.group(1)
        pct = re.search(r"分位\s*(\d+)\s*%", block)
        verdict = re.search(r"分位\s*\d+\s*%\s*→\s*([^<\n]+)", block)
        action = re.search(r"动作：\s*([^<]*)", block)
        erpv = re.search(r"当前 ERP：\s*([0-9.]+)%", block)
        pos = re.findall(r"泡沫仓.*?\((\d+)%\)|价值仓.*?\((\d+)%\)|投机仓.*?\((\d+)%\)", block)
        grid = re.search(
            r"成本\s*([0-9.]+)\s*·\s*下一买点\s*([0-9.]+)（第\s*(\d+)\s*档，还差\s*([0-9.]+)%）",
            block,
        )
        rows.append(
            dict(
                gen_date=gen,
                code=code,
                name=name,
                pct=int(pct.group(1)) if pct else None,
                verdict=(verdict.group(1).strip() if verdict else None),
                action=(action.group(1).strip() if action else None),
                anchor=float(grid.group(1)) if grid else None,
                buy_point=float(grid.group(2)) if grid else None,
                tier=int(grid.group(3)) if grid else None,
                gap_pct=float(grid.group(4)) if grid else None,
                erp_value=float(erpv.group(1)) if erpv else None,
            )
        )
    return rows


def load_prices():
    series = {}
    with open(os.path.join(REPO, "data", "etf_price_adj.csv"), encoding="utf-8-sig") as f:
        rd = csv.reader(f)
        header = next(rd)
        for c in header[1:]:
            series[c] = []
        for row in rd:
            if not row:
                continue
            for c, v in zip(header[1:], row[1:]):
                if v:
                    try:
                        series[c].append((row[0], float(v)))
                    except ValueError:
                        pass
    for c in series:
        series[c].sort()
    return series


def idx_on(series, date):
    lo, hi, ans = 0, len(series) - 1, -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if series[mid][0] <= date:
            ans = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return ans


def fwd(series, i, h):
    j = i + h
    if i < 0 or j >= len(series):
        return None
    return (series[j][1] / series[i][1] - 1) * 100


def abucket(a):
    if not a:
        return "无动作字段"
    if "可买一档" in a:
        return "可买一档"
    if "只持有" in a:
        return "只持有"
    if "减" in a:
        return "考虑减一档"
    return "其他"


def pbucket(p):
    if p >= 90:
        return "90-100 极低估"
    if p >= 75:
        return "75-90 显著低估"
    if p >= 50:
        return "50-75 中性"
    if p >= 25:
        return "25-50 偏贵"
    return "0-25 高估"


def erp_pct_lookup(code, date):
    """按 erp_*.csv 全历史自算分位：<=当日 ERP 值的占比。"""
    path = os.path.join(REPO, "data", f"erp_{code}.csv")
    if not os.path.exists(path):
        return None
    vals = []
    with open(path, encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        for r in rd:
            d = (r.get("Date") or "").strip()
            v = (r.get("ERP") or "").strip()
            if not d or not v:
                continue
            try:
                vals.append((d, float(v)))
            except ValueError:
                pass
    idx = None
    for i, (d, _) in enumerate(vals):
        if d <= date:
            idx = i
        else:
            break
    if idx is None or idx < 60:
        return None
    target = None
    for i in range(idx, -1, -1):
        if vals[i][0] == date:
            target = vals[i][1]
            break
    if target is None:
        target = vals[idx][1]
    return sum(1 for _, v in vals[: idx + 1] if v <= target) / (idx + 1) * 100


def main():
    os.makedirs(OUT, exist_ok=True)
    commits = sh("git", "log", BRANCH, "--pretty=%H|%ad", "--date=short", "--reverse").strip().splitlines()
    ledger = []
    for line in commits:
        sha, cdate = line.split("|")
        html = sh("git", "show", f"{sha}:report.html")
        if not html:
            continue
        for r in parse_report(html):
            r["gen_date"] = r["gen_date"] or cdate
            r["snapshot_date"] = cdate
            r["sha"] = sha[:8]
            ledger.append(r)

    fields = ["snapshot_date", "gen_date", "sha", "code", "name", "pct", "verdict",
              "action", "anchor", "buy_point", "tier", "gap_pct", "erp_value"]
    with open(os.path.join(OUT, "advice_ledger.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in ledger:
            w.writerow({k: r.get(k) for k in fields})

    px = load_prices()
    events = []
    for r in ledger:
        if r["code"] not in px:
            continue
        if not r["pct"]:
            r["pct_calc"] = erp_pct_lookup(r["code"], r["gen_date"])
        else:
            r["pct_calc"] = None
        if not r["pct"] and r["pct_calc"] is None:
            continue
        s = px[r["code"]]
        i = idx_on(s, r["gen_date"])
        if i < 0:
            continue
        e = dict(r)
        if not e.get("pct"):
            e["pct"] = round(r["pct_calc"]) if r.get("pct_calc") is not None else None
        e["pct_src"] = "报告" if r.get("pct") else "自算"
        e["price"] = s[i][1]
        for h in HORIZONS:
            e[f"ret_{h}"] = fwd(s, i, h)
        if r["buy_point"]:
            for h in HORIZONS:
                win = s[i + 1: i + 1 + h]
                hit = next((k for k, (_, p) in enumerate(win) if p <= r["buy_point"]), None)
                e[f"hit_{h}"] = 1 if hit is not None else 0
                if hit is not None:
                    e[f"hit_days_{h}"] = hit + 1
        events.append(e)

    lines = ["# 建议层验证：报告里的建议 vs 之后实际走势", ""]
    lines.append(f"- 报告快照：{len(commits)} 份（{ledger[0]['gen_date']} ~ {ledger[-1]['gen_date']}）")
    lines.append(f"- 可对照条目：{len(events)} 条（仅统计能匹配到前复权价格的标的）")
    act_dates = sorted({e['gen_date'] for e in events if e.get('action')})
    if act_dates:
        lines.append(f"- 「动作」字段仅 {act_dates[0]} 起出现，共 {len(act_dates)} 个交易日，样本不足以下结论")
    lines.append("")

    lines += ["## 1. 「下一买点」到价命中率", "", "| 窗口 | 样本 | 命中 | 命中率 | 命中中位天数 |", "|---|---|---|---|---|"]
    for h in HORIZONS:
        rows = [e for e in events if e.get(f"hit_{h}") is not None]
        hits = [e for e in rows if e[f"hit_{h}"]]
        rate = f"{len(hits)/len(rows)*100:.1f}%" if rows else "—"
        md = f"{median([e[f'hit_days_{h}'] for e in hits]):.0f}" if hits else "—"
        lines.append(f"| {h} 日 | {len(rows)} | {len(hits)} | {rate} | {md} |")
    lines.append("")

    header = "| 动作 | 条目 | " + " | ".join(f"{h}日" for h in HORIZONS) + " |"
    sep = "|---" * (len(HORIZONS) + 2) + "|"
    lines += ["## 2. 按动作分组的后续收益（%）", "", header, sep]
    groups = {}
    for e in events:
        groups.setdefault(abucket(e["action"]), []).append(e)
    for g, rows in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        cells = [g, len(rows)]
        for h in HORIZONS:
            vals = [r[f"ret_{h}"] for r in rows if r.get(f"ret_{h}") is not None]
            cells.append(f"{sum(vals)/len(vals):+.1f}(n={len(vals)})" if vals else "—")
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    lines.append("")

    hdr3 = "| 分位桶 | 条目 | " + " | ".join(f"{h}日" for h in HORIZONS) + " |"
    lines += ["## 3. 按分位分组的后续收益（%）", "", hdr3, sep]
    g2 = {}
    for e in events:
        g2.setdefault(pbucket(e["pct"]), []).append(e)
    for b in sorted(g2):
        rows = g2[b]
        cells = [b, len(rows)]
        for h in HORIZONS:
            vals = [r[f"ret_{h}"] for r in rows if r.get(f"ret_{h}") is not None]
            cells.append(f"{sum(vals)/len(vals):+.1f}(n={len(vals)})" if vals else "—")
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    lines.append("")

    md = os.path.join(OUT, "advice_validation.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    print(f"\n台账：{os.path.join(OUT, 'advice_ledger.csv')}\n结论：{md}")


if __name__ == "__main__":
    main()
