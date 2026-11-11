#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""档位表：按「本轮首笔成本」固定锚 + 等差档位算出下一档触发价。

数据来源
  成本：data/grid_state.csv（code, name, anchor, tiers_bought）—— 自己填
  现价：data/etf_price.csv 最后一个交易日（系统每天 18:30 更新）

口径
  第 1 档 = anchor 本身（首笔买入价）
  第 n 档 = anchor × (1 − 5% × (n−1))，即 95% / 90% / 85% / 80%
  成交只把 tiers_bought 加一，anchor 永不改；清仓后重算 anchor

用法
  python3 grid.py            # 输出到 ~/Desktop/grid_plan.md
"""

import os

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "data", "grid_state.csv")
PRICE = os.path.join(BASE, "data", "etf_price.csv")
OUT = os.path.expanduser("~/Desktop/grid_plan.md")
STEP = 5.0


def build():
    st = pd.read_csv(STATE, dtype={"code": str})
    px = pd.read_csv(PRICE)
    last = px.iloc[-1]
    asof = last["date"]
    rows = []

    for _, r in st.iterrows():
        code, name = str(r["code"]), r["name"]
        anchor, bought = r["anchor"], int(r["tiers_bought"])
        price = last.get(code)
        price = float(price) if pd.notna(price) else None

        if pd.isna(anchor):
            rows.append({"name": name, "code": code, "anchor": None, "bought": bought,
                         "next_no": bought + 1, "trigger": None, "price": price, "gap": None,
                         "note": "待填 anchor"})
            continue

        anchor = float(anchor)
        # 下一买点：从成本往下，找第一个低于现价的档位（不依赖「已买几档」）
        trig, nxt = None, None
        for k in range(0, 8):
            lvl = anchor * (1 - STEP * k / 100.0)
            if price is None or lvl < price:
                trig, nxt = lvl, k + 1
                break
        gap = (trig / price - 1) * 100 if (trig and price) else None
        if trig is None:
            note = "已跌破成本 40% 以上，档位用尽"
        elif nxt == 1:
            note = "高于成本，跌回成本才加仓"
        else:
            note = "距上一档已跌破，等这条线"
        rows.append({"name": name, "code": code, "anchor": anchor, "bought": None,
                     "next_no": nxt, "trigger": trig, "price": price, "gap": gap, "note": note})
    return rows, asof


def render(rows, asof):
    lines = [f"# 档位表 · 数据截至 {asof}", "",
             "| 标的 | 成本 | 现价 | 相对成本 | 下一买点 | 还差 | 说明 |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        if r["anchor"] is None:
            price = "—" if r["price"] is None else f"{r['price']:.4f}"
            lines.append(f"| {r['name']} | — | {price} | — | — | — | 待填成本 |")
            continue
        price = "—" if r["price"] is None else f"{r['price']:.4f}"
        rel = "—" if r["price"] is None else f"{(r['price']/r['anchor']-1)*100:+.1f}%"
        trig = "—" if r["trigger"] is None else f"{r['trigger']:.4f}（第 {r['next_no']} 档）"
        gap = "—" if r["gap"] is None else f"{r['gap']:+.1f}%"
        lines.append(f"| {r['name']} | {r['anchor']:.4f} | {price} | {rel} | {trig} | {gap} | {r['note']} |")
    return "\n".join(lines)


def main():
    rows, asof = build()
    md = render(rows, asof)
    print(md)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(md + "\n")
    miss = [r["name"] for r in rows if r["anchor"] is None]
    if miss:
        print(f"\n还有 {len(miss)} 只没填 anchor：{'、'.join(miss)}")
    print(f"\n已写入 {OUT}")


if __name__ == "__main__":
    main()
