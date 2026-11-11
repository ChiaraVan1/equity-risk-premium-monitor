#!/usr/bin/env python3
"""从最新月度复盘定稿解析「全账户总资产」与各标的市值，产出 data/portfolio_total.csv。

为什么要有这个文件：
    日报里那句「该只市值不超过总资产 6%」一直是死文案——分母从来没进过系统。
    分母的权威来源只有一处：~/Desktop/portfolio-postmortem-YYYY-MM.md。

怎么用：
    每月复盘定稿后跑一次本脚本 → 生成 data/portfolio_total.csv（本地 gitignore）
    → 同步到 GitHub Secret PORTFOLIO_TOTAL（云端 workflow 还原它）。
    用户不需要手抄任何数字，复盘那步由助手执行。

解析对象（复盘定稿的两张表）：
    2.1 子口径：  | **全账户** | **437,034.94** | ...
    2.2 逐品种：  | 组 | 名称 | 代码 | 期初份额 | 期初市值 | 期末市值 | ...
"""
import csv
import glob
import os
import re
import sys

DESKTOP = os.path.expanduser("~/Desktop")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(ROOT, "data", "portfolio_total.csv")


def latest_postmortem():
    files = sorted(glob.glob(os.path.join(DESKTOP, "portfolio-postmortem-*.md")))
    return files[-1] if files else None


def month_end(ym):
    """'2026-10' → '2026-10-31'"""
    import calendar
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{ym}-{calendar.monthrange(y, m)[1]:02d}"


def parse(path):
    text = open(path, encoding="utf-8").read()

    m = re.search(r"\|\s*\*\*全账户\*\*\s*\|\s*\*\*([\d,]+\.?\d*)\*\*", text)
    if not m:
        raise SystemExit("解析失败：复盘里没有「| **全账户** | **数字**」这一行，表格结构可能变了")
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
    return total, holdings, month_end(ym)


def main():
    p = latest_postmortem()
    if not p:
        raise SystemExit("未找到 ~/Desktop/portfolio-postmortem-*.md，无法取总资产基准")

    total, holdings, asof = parse(p)
    rows = [{"key": "TOTAL", "name": "全账户总资产", "shares": "", "market_value": f"{total:.2f}", "asof": asof}]
    for code, (name, shares, mv) in sorted(holdings.items()):
        rows.append({"key": code, "name": name, "shares": shares,
                     "market_value": f"{mv:.2f}", "asof": asof})

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["key", "name", "shares", "market_value", "asof"])
        w.writeheader()
        w.writerows(rows)

    print(f"来源：{os.path.basename(p)}")
    print(f"全账户总资产：{total:,.2f}（{asof}）")
    print(f"持仓 {len(holdings)} 只，6% 上限单只 {total * 0.06:,.2f} 元")
    print(f"已写入 {OUT_PATH}")


if __name__ == "__main__":
    main()
