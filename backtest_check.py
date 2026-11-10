#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量体检：对 config.json 每只标的跑止损 + 间距回测，汇总一行写进 data/backtest_verdicts.csv。

取数口径：优先用 etf_code（你实际下单的 ETF，去 .SH/.SZ 后缀），没有 ETF 的用指数价兜底。
结论只落数据、不进报告；用途是看"统一标准能不能无差别套到全部标的"，还是出现了例外。

用法：
    python3 backtest_check.py                 # 全部标的
    python3 backtest_check.py --codes 000300  # 指定
    python3 backtest_check.py --quick         # 每 10 个交易日取一个入场点，先看结果（约快 10 倍）
"""

import argparse
import json
import os
from datetime import datetime, timedelta

import pandas as pd

from backtest_stoploss import fetch, backtest_one, summarize
from backtest_grid import run_code

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_CSV = os.path.join(ROOT, "data", "backtest_verdicts.csv")


def price_code_of(item):
    """etf_code 去后缀优先；无 ETF 时退回指数代码。"""
    etf = (item.get("etf_code") or "").split(".")[0].strip()
    return etf or item["code"]


def check_one(item, years, hold_years, thresholds, steps, tiers, amount, quick):
    code, name = item["code"], item.get("name", item["code"])
    pc = price_code_of(item)
    end = datetime.now()
    s = (end - timedelta(days=365 * years + 30)).strftime("%Y-%m-%d")
    e = end.strftime("%Y-%m-%d")

    df, src = fetch(pc, s, e)
    if df is None or len(df) < 300:
        return {"code": code, "name": name, "price_code": pc,
                "generated": datetime.now().strftime("%Y-%m-%d"),
                "止损结论": "无数据", "间距结论": "无数据", "与现行规则一致": "跳过",
                "备注": "该指数无日线数据源"}
    entry_step = 10 if quick else 1

    # ── 止损 ──
    srows = []
    for th in thresholds:
        det = backtest_one(df, float(th), horizon=60, hold_years=hold_years, entry_step=entry_step)
        if len(det) < 50:
            continue
        srows.append(summarize(det, code, float(th), src, name, min_samples=10 if quick else 30))
    if not srows:
        return {"code": code, "name": name, "price_code": pc,
                "generated": datetime.now().strftime("%Y-%m-%d"),
                "止损结论": "样本不足", "间距结论": "样本不足", "与现行规则一致": "跳过",
                "备注": ""}

    sd = pd.DataFrame(srows)
    valid = sd[sd["sample_ok"]]
    use = valid if len(valid) >= 3 else sd
    n_bad = int((use["median_diff"] < 0).sum())
    stop_verdict = "不设" if n_bad * 2 >= len(use) else "该设"
    best = use.loc[use["median_diff"].idxmax()]
    trig5 = sd.loc[sd["threshold"] == thresholds[0], "trigger_rate"]
    trig5 = float(trig5.iloc[0]) if len(trig5) else float("nan")
    loss_at_best = float(best["median_diff"])  # 负=止损比死扛差

    # ── 间距 ──
    grows, _ = run_code(code, df, [float(x) for x in steps], tiers, amount, hold_years, entry_step=entry_step)
    gd = pd.DataFrame(grows)
    r5 = gd.loc[gd["step"] == 5.0, "total_ret"]
    ret5 = float(r5.iloc[0]) if len(r5) else float("nan")
    gbest = gd.loc[gd["total_ret"].idxmax()]
    gap = ret5 - float(gbest["total_ret"])  # 负=5% 不如最优

    if abs(gap) < 0.05 or float(gbest["step"]) == 5.0:
        grid_verdict, note = "维持5%", ""
    else:
        grid_verdict = f"偏离→{gbest['step']:g}%"
        note = f"最优 {gbest['step']:g}% 比 5% 多 {abs(gap) * 100:.1f} 个点"

    consistent = (stop_verdict == "不设") and (grid_verdict == "维持5%")
    return {
        "code": code, "name": name, "price_code": pc, "source": src,
        "generated": datetime.now().strftime("%Y-%m-%d"),
        "years": years, "hold_years": hold_years,
        "止损结论": stop_verdict,
        "5%止损触发率": round(trig5, 4) if trig5 == trig5 else None,
        "最不差阈值": float(best["threshold"]),
        "止损相对死扛中位差": round(loss_at_best, 4),
        "间距结论": grid_verdict,
        "5%间距两年收益": round(ret5, 4) if ret5 == ret5 else None,
        "最优间距": float(gbest["step"]),
        "最优间距两年收益": round(float(gbest["total_ret"]), 4),
        "5%与最优差距": round(gap, 4),
        "与现行规则一致": "是" if consistent else "否",
        "备注": note,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", nargs="*")
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--hold-years", type=int, default=2)
    ap.add_argument("--thresholds", default="5,8,10,15,20")
    ap.add_argument("--steps", default="3,5,8,10,15")
    ap.add_argument("--tiers", type=int, default=4)
    ap.add_argument("--amount", type=float, default=5000)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--domestic", action="store_true", help="只跑国内标的，海外跳过")
    args = ap.parse_args()

    items = json.load(open(os.path.join(ROOT, "config.json")))["indices"]
    if args.domestic:
        items = [d for d in items if d.get("source") != "yfinance"]
    if args.codes:
        codes = set(args.codes)
        items = [d for d in items
                 if d["code"] in codes or (d.get("etf_code") or "").split(".")[0] in codes]

    thresholds = [float(x) for x in args.thresholds.split(",")]
    steps = [float(x) for x in args.steps.split(",")]

    rows = []
    for i, item in enumerate(items, 1):
        print(f"\n[{i}/{len(items)}] {item.get('name', item['code'])}({item['code']})", flush=True)
        r = check_one(item, args.years, args.hold_years, thresholds, steps,
                      args.tiers, args.amount, args.quick)
        rows.append(r)
        print(f"  → 止损:{r.get('止损结论')} 间距:{r.get('间距结论')} 一致:{r.get('与现行规则一致')}", flush=True)

    out = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    old = pd.read_csv(OUT_CSV, dtype={"code": str}) if os.path.exists(OUT_CSV) else pd.DataFrame()
    if len(old):
        out = pd.concat([old[~old["code"].isin(out["code"])], out], ignore_index=True)
    out.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    print("\n" + "=" * 90)
    print(out[["code", "name", "止损结论", "间距结论", "与现行规则一致", "备注"]].to_string(index=False))
    print("=" * 90)
    bad = out[out["与现行规则一致"] == "否"]
    print(f"\n一致 {len(out) - len(bad)} 只 / 偏离 {len(bad)} 只")
    if len(bad):
        print("偏离标的：")
        print(bad[["code", "name", "止损结论", "间距结论", "备注"]].to_string(index=False))
    print(f"\n结果：{OUT_CSV}")


if __name__ == "__main__":
    main()
