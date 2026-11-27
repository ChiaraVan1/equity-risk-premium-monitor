#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逻辑层验证：ERP 分位 → 之后实际价格走势。

口径说明：
- 分位 = 截至当日、全历史真实样本内的分位（不设近年窗口，与 930794 已定口径一致）
- 收益 = data/etf_price_adj.csv 前复权价，h 个交易日后
- 只在有 ≥2 年 ERP 历史后开始计入（避免早期分位不稳）
"""
import csv
import os
import glob
from statistics import median

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "analysis", "results")
HORIZONS = [20, 60, 120, 250]
MIN_HIST_DAYS = 500


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


def load_erp(code):
    path = os.path.join(REPO, "data", f"erp_{code}.csv")
    if not os.path.exists(path):
        return None
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        for r in rd:
            d = (r.get("Date") or "").strip()
            v = (r.get("ERP") or "").strip()
            c = (r.get("IndexClose") or "").strip()
            if not d or not v or not c:
                continue
            try:
                rows.append((d, float(v), float(c)))
            except ValueError:
                pass
    return rows


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


def bucket(p):
    if p >= 90:
        return "90-100 极低估"
    if p >= 75:
        return "75-90 显著低估"
    if p >= 50:
        return "50-75 中性"
    if p >= 25:
        return "25-50 偏贵"
    return "0-25 高估"


def main():
    os.makedirs(OUT, exist_ok=True)
    events = []
    for path in glob.glob(os.path.join(REPO, "data", "erp_*.csv")):
        code = os.path.basename(path)[4:-4]
        erp = load_erp(code)
        if not erp:
            continue
        series = [(d, c) for d, _, c in erp]
        erp_vals = [v for _, v, _ in erp]
        for i, (d, v, _c) in enumerate(erp):
            if i < MIN_HIST_DAYS:
                continue
            rank = sum(1 for x in erp_vals[: i + 1] if x <= v) / (i + 1) * 100
            ev = dict(code=code, date=d, erp=v, pct=rank)
            for h in HORIZONS:
                ev[f"ret_{h}"] = fwd(series, i, h)
            events.append(ev)

    lines = ["# 逻辑层验证：ERP 分位 → 后续实际收益", ""]
    lines.append(f"- 标的数：{len({e['code'] for e in events})}，事件样本：{len(events)}（价格用 erp 文件自带 IndexClose 全历史）")
    lines.append(f"- 时间跨度：{min(e['date'] for e in events)} ~ {max(e['date'] for e in events)}")
    lines.append("- 分位＝全历史真实样本口径；收益＝前复权价，重叠样本，解读时看方向而非精确值")
    lines.append("")

    lines += ["## 按分位桶的后续收益（%）", "", "| 分位桶 | 事件数 | 20日 | 60日 | 120日 | 250日 |", "|---|---|---|---|---|---|"]
    for b in ["0-25 高估", "25-50 偏贵", "50-75 中性", "75-90 显著低估", "90-100 极低估"]:
        rows = [e for e in events if bucket(e["pct"]) == b]
        cells = [b, len(rows)]
        for h in HORIZONS:
            vals = [r[f"ret_{h}"] for r in rows if r.get(f"ret_{h}") is not None]
            m = sum(vals) / len(vals) if vals else None
            med = median(vals) if vals else None
            cells.append(f"{m:+.1f}／中位{med:+.1f}（n={len(vals)}）" if vals else "—")
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    lines.append("")

    # 单调性检查：分位越高（越低估），未来收益是否越好
    lines += ["## 单调性检查（均值方向）", "", "| 窗口 | 低分位(0-25) | 高分位(90-100) | 差值(高-低) |", "|---|---|---|---|"]
    for h in HORIZONS:
        low = [e[f"ret_{h}"] for e in events if bucket(e["pct"]) == "0-25 高估" and e.get(f"ret_{h}") is not None]
        high = [e[f"ret_{h}"] for e in events if bucket(e["pct"]) == "90-100 极低估" and e.get(f"ret_{h}") is not None]
        if low and high:
            lines.append(f"| {h}日 | {sum(low)/len(low):+.1f}（n={len(low)}） | {sum(high)/len(high):+.1f}（n={len(high)}） | {(sum(high)/len(high)-sum(low)/len(low)):+.1f} |")
        else:
            lines.append(f"| {h}日 | — | — | — |")
    lines.append("")

    # 非重叠样本：每个标的每月只取第一条，避免重叠放大
    lines += ["## 非重叠抽样（每标的每月一条）", "", "| 分位桶 | 样本 | 60日 | 250日 |", "|---|---|---|---|"]
    seen = set()
    uniq = []
    for e in events:
        key = (e["code"], e["date"][:7])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(e)
    for b in ["0-25 高估", "25-50 偏贵", "50-75 中性", "75-90 显著低估", "90-100 极低估"]:
        rows = [e for e in uniq if bucket(e["pct"]) == b]
        cells = [b, len(rows)]
        for h in (60, 250):
            vals = [r[f"ret_{h}"] for r in rows if r.get(f"ret_{h}") is not None]
            cells.append(f"{sum(vals)/len(vals):+.1f}（n={len(vals)}）" if vals else "—")
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    lines.append("")

    # 极端案例：极高估后 250 日仍下跌
    lines += ["## 极低估(≥90)后 250 日跌得最多的 8 例", "", "| 日期 | 标的 | 分位 | ERP | 250日收益 |", "|---|---|---|---|---|"]
    cand = [e for e in events if bucket(e["pct"]) == "90-100 极低估" and e.get("ret_250") is not None]
    cand.sort(key=lambda e: e["ret_250"])
    for e in cand[:8]:
        lines.append(f"| {e['date']} | {e['code']} | {e['pct']:.0f} | {e['erp']:.2f}% | {e['ret_250']:+.1f} |")
    lines.append("")

    md = os.path.join(OUT, "erp_percentile_validation.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open(os.path.join(OUT, "erp_percentile_events.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(events[0].keys()))
        w.writeheader()
        w.writerows(events)
    print("\n".join(lines))
    print(f"\n结论：{md}")


if __name__ == "__main__":
    main()
