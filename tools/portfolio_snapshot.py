#!/usr/bin/env python3
"""产出 data/portfolio_total.csv（6% 上限检查的分母与分子），并可选推到 GitHub Secret。

数据源两条，按优先级：
    1. 本地基金工作台 API  http://127.0.0.1:8091/api/watchlist
       → watchlist.total（全账户总资产）、watchlist.funds[].amount（各只市值）、computedAt（时点）
       ★ 只认本地实例。线上发布的副本可能是旧版 holdings（实测现金桶差 20 万），不能当源。
    2. 兜底：解析最新月度复盘定稿 ~/Desktop/portfolio-postmortem-YYYY-MM.md
       → 「全账户」行 + 「逐品种」表。API 没开时才用，市值是月末值。

用法：
    python3 tools/portfolio_snapshot.py           # 只生成本地文件
    python3 tools/portfolio_snapshot.py --push    # 生成后同步到 Secret PORTFOLIO_TOTAL
"""
import argparse
import calendar
import csv
import glob
import json
import os
import re
import subprocess
import urllib.request

DESKTOP = os.path.expanduser("~/Desktop")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(ROOT, "data", "portfolio_total.csv")
API_URL = "http://127.0.0.1:8091/api/watchlist"
REPO = "ChiaraVan1/equity-risk-premium-monitor"


def from_api():
    with urllib.request.urlopen(API_URL, timeout=10) as r:
        d = json.load(r)["watchlist"]
    total = float(d["total"])
    holdings = {}
    for f in d.get("funds", []):
        code = str(f.get("code", ""))
        if not re.fullmatch(r"\d{6}", code):
            continue
        holdings[code] = (
            f.get("name", ""),
            int(round(f.get("shares") or 0)),
            float(f.get("amount") or 0),
        )
    asof = (d.get("computedAt") or "")[:10]
    return total, holdings, asof


def from_postmortem():
    files = sorted(glob.glob(os.path.join(DESKTOP, "portfolio-postmortem-*.md")))
    if not files:
        raise RuntimeError("没有 ~/Desktop/portfolio-postmortem-*.md")
    path = files[-1]
    text = open(path, encoding="utf-8").read()

    m = re.search(r"\|\s*\*\*全账户\*\*\s*\|\s*\*\*([\d,]+\.?\d*)\*\*", text)
    if not m:
        raise RuntimeError("复盘里没有「| **全账户** | **数字** |」行，表格结构变了")
    total = float(m.group(1).replace(",", ""))

    holdings = {}
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip().replace("*", "") for c in line.strip().strip("|").split("|")]
        if len(cells) < 6 or not re.fullmatch(r"\d{6}", cells[2]):
            continue
        try:
            shares = int(cells[3].replace(",", "") or 0)
            mv = float(cells[5].replace(",", ""))
        except ValueError:
            continue
        holdings[cells[2]] = (cells[1], shares, mv)

    ym = re.search(r"(\d{4}-\d{2})", os.path.basename(path)).group(1)
    y, mo = int(ym[:4]), int(ym[5:7])
    asof = f"{ym}-{calendar.monthrange(y, mo)[1]:02d}"
    return total, holdings, asof, os.path.basename(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true", help="同步到 GitHub Secret PORTFOLIO_TOTAL")
    args = ap.parse_args()

    try:
        total, holdings, asof = from_api()
        source = "本地基金工作台 API（当日实时）"
    except Exception as e:
        print(f"本地 API 不可用（{e}），回落到复盘定稿")
        total, holdings, asof, fname = from_postmortem()
        source = f"月度复盘 {fname}（月末值，会偏旧）"

    rows = [{"key": "TOTAL", "name": "全账户总资产", "shares": "",
             "market_value": f"{total:.2f}", "asof": asof}]
    for code, (name, shares, mv) in sorted(holdings.items()):
        rows.append({"key": code, "name": name, "shares": shares,
                     "market_value": f"{mv:.2f}", "asof": asof})

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["key", "name", "shares", "market_value", "asof"])
        w.writeheader()
        w.writerows(rows)

    print(f"来源：{source}")
    print(f"全账户总资产 {total:,.2f}（{asof}），持仓 {len(holdings)} 只，6% 上限 {total * 0.06:,.2f}")
    print(f"已写入 {OUT_PATH}")

    if args.push:
        with open(OUT_PATH, "rb") as f:
            subprocess.run(["gh", "secret", "set", "PORTFOLIO_TOTAL", "--repo", REPO],
                           stdin=f, check=True)
        print("已推送到 Secret PORTFOLIO_TOTAL")


if __name__ == "__main__":
    main()
