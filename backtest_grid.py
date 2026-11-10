#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分批加仓间距回测：同预算下 3/5/8/10/15% 间距横向比较。

每个交易日视为一次建仓起点：
  - 首笔投入 1 档
  - 之后价格每跌破 step% 加 1 档（档位按首笔成本几何递减），最多 N 档
  - 跌不到位就持币，不追
  - 从首笔起固定持有 2 年，到期结算

预算固定 = 档数 × 单档金额，所有间距同预算，差异只在买入时机。
收益率与单档金额无关（等额加仓），金额只影响绝对数。

用法：
    python3 backtest_grid.py --codes 600585 601006 600600 510300 --step 3,5,8,10,15
"""

import argparse
import os
import time
from datetime import datetime

import numpy as np
import pandas as pd

from backtest_stoploss import fetch, RESULT_DIR


def simulate(c, i, end_idx, step, tiers, amount):
    """返回该入场点在窗口末的表现。"""
    budget = tiers * amount
    base = c[i]
    shares = amount / base
    cash = budget - amount
    used = 1
    # 第 k 档触发价（k=1..tiers-1），按首笔成本几何递减
    triggers = [base * (1 - step / 100.0) ** k for k in range(1, tiers)]

    min_eq = budget
    for j in range(i + 1, end_idx + 1):
        if used < tiers and c[j] <= triggers[used - 1]:
            shares += amount / c[j]
            cash -= amount
            used += 1
        eq = shares * c[j] + cash
        if eq < min_eq:
            min_eq = eq

    end_eq = shares * c[end_idx] + cash
    avg_cost = (used * amount) / shares if shares > 0 else np.nan
    return {
        "tiers_used": used,
        "full": 1 if used >= tiers else 0,
        "total_ret": end_eq / budget - 1.0,
        "avg_cost_vs_entry": avg_cost / base - 1.0,
        "max_dd": min_eq / budget - 1.0,
    }


def run_code(code, df, steps, tiers, amount, hold_years):
    c = df["close"].values.astype(float)
    dates = pd.to_datetime(df["date"]).values
    n = len(c)
    rows, yearly = [], []
    for step in steps:
        recs = []
        for i in range(n - 1):
            target = np.datetime64(pd.Timestamp(dates[i]) + pd.DateOffset(years=int(hold_years)))
            end_idx = int(np.searchsorted(dates, target))
            if end_idx >= n:
                continue
            r = simulate(c, i, end_idx, step, tiers, amount)
            r["entry_date"] = pd.Timestamp(dates[i])
            recs.append(r)
        d = pd.DataFrame(recs)
        d["year"] = d["entry_date"].dt.year
        for yr, ret in d.groupby("year")["total_ret"].mean().items():
            yearly.append({"code": code, "step": step, "year": int(yr), "ret": float(ret)})
        rows.append({
            "code": code,
            "step": step,
            "entries": len(d),
            "avg_tiers": d["tiers_used"].mean(),
            "full_rate": d["full"].mean(),
            "total_ret": d["total_ret"].mean(),
            "avg_cost_vs_entry": d["avg_cost_vs_entry"].mean(),
            "max_dd": d["max_dd"].mean(),
            "ret_median": d["total_ret"].median(),
        })
        print(f"  {code} 间距{step:g}%：{len(d)} 个入场点，平均建仓 {d['tiers_used'].mean():.2f} 档，"
              f"2年收益 {d['total_ret'].mean() * 100:.1f}%，均价相对首日 {d['avg_cost_vs_entry'].mean() * 100:+.1f}%", flush=True)
    return rows, yearly


NAMES = {"600585": "海螺水泥", "601006": "大秦铁路", "600600": "青岛啤酒",
         "510300": "沪深300ETF", "588000": "科创50ETF"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", nargs="+", required=True)
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--step", default="3,5,8,10,15")
    ap.add_argument("--tiers", type=int, default=4)
    ap.add_argument("--amount", type=float, default=5000)
    ap.add_argument("--hold-years", type=int, default=2)
    args = ap.parse_args()

    from datetime import timedelta
    end = datetime.now()
    s = (end - timedelta(days=365 * args.years + 30)).strftime("%Y-%m-%d")
    e = end.strftime("%Y-%m-%d")
    steps = [float(x) for x in args.step.split(",")]
    budget = args.tiers * args.amount

    all_rows = []
    yearly_rows = []
    for code in args.codes:
        print(f"\n{NAMES.get(code, code)}({code}) 预算 {budget:,.0f} 元 = {args.tiers} 档 × {args.amount:,.0f}", flush=True)
        df, source = fetch(code, s, e)
        if df is None or len(df) < 300:
            print("  跳过：数据不足")
            continue
        r, y = run_code(code, df, steps, args.tiers, args.amount, args.hold_years)
        all_rows += r
        yearly_rows += y

    if not all_rows:
        return

    out = pd.DataFrame(all_rows)
    os.makedirs(RESULT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    csv_path = os.path.join(RESULT_DIR, f"backtest_grid_{stamp}.csv")
    out.to_csv(csv_path, index=False, encoding="utf-8-sig")

    # 各间距的横向总表
    agg = out.groupby("step").agg(
        平均档数=("avg_tiers", "mean"),
        打满比例=("full_rate", "mean"),
        两年收益=("total_ret", "mean"),
        收益中位=("ret_median", "mean"),
        均价相对首日=("avg_cost_vs_entry", "mean"),
        最大浮亏=("max_dd", "mean"),
    ).reset_index()
    print("\n" + "=" * 96)
    print(agg.to_string(index=False,
                        formatters={"平均档数": "{:.2f}".format,
                                    "打满比例": "{:.1%}".format,
                                    "两年收益": "{:+.1%}".format,
                                    "收益中位": "{:+.1%}".format,
                                    "均价相对首日": "{:+.2%}".format,
                                    "最大浮亏": "{:.1%}".format}))
    print("=" * 96)

    # 按入场年份拆分：检验排序是否只在一类行情里成立
    ydf = pd.DataFrame(yearly_rows)
    pivot = ydf.pivot_table(index="year", columns="step", values="ret", aggfunc="mean") * 100
    print("\n按入场年份（各标的平均，2 年收益 %）")
    print(pivot.round(1).to_string())
    best = pivot.idxmax(axis=1)
    print("\n每年最优间距：" + "，".join(f"{int(yr)}→{b:g}%" for yr, b in best.items()))
    print("最优间距分布：" + str({f"{k:g}%": int(v) for k, v in best.value_counts().items()}))

    md = [f"# 分批加仓间距回测", "",
          f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M}",
          f"- 预算：{budget:,.0f} 元（{args.tiers} 档 × {args.amount:,.0f}），持币待跌，跌不到位不买",
          f"- 窗口：每个入场日起固定持有 {args.hold_years} 年", "", "## 各间距横向对比", "",
          "| 间距 | 平均建仓档数 | 打满比例 | 2 年收益 | 收益中位 | 买入均价 vs 首日 | 最大浮亏 |",
          "|---|---|---|---|---|---|---|"]
    for r in agg.itertuples():
        md.append(f"| {r.step:g}% | {r.平均档数:.2f} | {r.打满比例:.1%} | {r.两年收益:+.1%} | "
                  f"{r.收益中位:+.1%} | {r.均价相对首日:+.2%} | {r.最大浮亏:.1%} |")
    md += ["", "## 按入场年份拆分（2 年收益 %）", "",
           "| 年份 | " + " | ".join(f"{c:g}%" for c in pivot.columns) + " | 当年最优 |",
           "|---|" + "---|" * (len(pivot.columns) + 1)]
    for yr, row in pivot.iterrows():
        cells = " | ".join("—" if pd.isna(v) else f"{v:+.1f}" for v in row.values)
        md.append(f"| {int(yr)} | {cells} | {row.idxmax():g}% |")
    md += ["", "## 明细", "", "| 标的 | 间距 | 入场点 | 平均档数 | 打满比例 | 2年收益 | 均价vs首日 | 最大浮亏 |",
           "|---|---|---|---|---|---|---|---|"]
    for r in out.itertuples():
        md.append(f"| {NAMES.get(r.code, r.code)} | {r.step:g}% | {r.entries} | {r.avg_tiers:.2f} | "
                  f"{r.full_rate:.1%} | {r.total_ret:+.1%} | {r.avg_cost_vs_entry:+.2%} | {r.max_dd:.1%} |")
    md += ["", "## 说明", "",
           "1. 所有间距共用同一预算，未买出的部分视为现金，计入收益但不参与涨跌。",
           "2. 档位触发价按首笔成本几何递减，一天最多加一档。",
           "3. 收益为 2 年期均值，跨周期混合（牛熊都有），不代表短期体验。"]
    md_path = os.path.join(RESULT_DIR, f"backtest_grid_{stamp}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"\n明细：{csv_path}\n结论：{md_path}")


if __name__ == "__main__":
    main()
