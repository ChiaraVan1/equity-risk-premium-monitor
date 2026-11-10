#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""成本回撤止损阈值回测（逐点入场法 / rolling entry）。

对每只标的的每个交易日 S 视为一次买入（成本 = 当日收盘）：
  - 设阈值 X%：持有期间若某日收盘 <= 成本×(1-X%)，当日收盘卖出，之后不再买回
  - 不设阈值：同一买入点一路持有到期末
两者配对比较。

用途：只回答「该不该设阈值」，不回答「设多少」。
样本少（3 只票）时结论不可外推到其他标的。

用法：
    python3 backtest_stoploss.py --codes 600585 601006 600600 --years 10
    python3 backtest_stoploss.py --codes 510300 588000 --years 6 --thresholds 5,10,20
"""

import argparse
import os
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

CACHE_DIR = "data/backtest"
RESULT_DIR = "results"


# ─────────────────────────── 取数 ───────────────────────────

def _clean(df, code):
    """统一成 date/close/volume 三列，升序、去重、剔除停牌。"""
    if df is None or len(df) == 0:
        return None
    d = df.copy()
    d.columns = [str(c) for c in d.columns]
    d = d.rename(columns={"日期": "date", "收盘": "close", "成交量": "volume"})
    if not {"date", "close"} <= set(d.columns):
        return None
    d["date"] = pd.to_datetime(d["date"])
    d["close"] = pd.to_numeric(d["close"], errors="coerce")
    if "volume" in d.columns:
        d["volume"] = pd.to_numeric(d["volume"], errors="coerce").fillna(0)
    else:
        d["volume"] = 1.0  # 缓存文件只存 date/close，视为有效交易日
    d = d.dropna(subset=["close"])
    d = d[d["close"] > 0]
    d = d.sort_values("date").drop_duplicates("date", keep="last")
    # 停牌日：成交量 0（个股常见，ETF 基本没有）
    d = d[d["volume"] > 0]
    return d[["date", "close"]].reset_index(drop=True)


def fetch(code, start_date, end_date):
    """先按 ETF 口径取，失败再回退个股口径；返回 (df, 数据源)。"""
    import akshare as ak

    cache = os.path.join(CACHE_DIR, f"{code}_{start_date}_{end_date}.csv")
    if os.path.exists(cache):
        d = pd.read_csv(cache)
        return _clean(d, code), "cache"

    s8, e8 = start_date.replace("-", ""), end_date.replace("-", "")
    etf_like = code.startswith(("51", "56", "58", "159", "588"))

    attempts = []
    if etf_like:
        attempts = [("ETF", ak.fund_etf_hist_em), ("个股", ak.stock_zh_a_hist)]
    else:
        attempts = [("个股", ak.stock_zh_a_hist), ("ETF", ak.fund_etf_hist_em)]

    for label, fn in attempts:
        try:
            df = fn(symbol=code, period="daily", start_date=s8, end_date=e8, adjust="qfq")
            d = _clean(df, code)
            if d is not None and len(d) >= 120:
                os.makedirs(CACHE_DIR, exist_ok=True)
                d.to_csv(cache, index=False)
                return d, label
        except Exception:
            continue
        time.sleep(1)
    return None, "failed"


# ─────────────────────────── 回测 ───────────────────────────

def backtest_one(df, threshold, horizon=60):
    """逐点入场。返回明细 DataFrame。"""
    c = df["close"].values.astype(float)
    dates = df["date"].values
    n = len(c)
    rows = []

    for i in range(n - 1):
        cost = c[i]
        thr = cost * (1 - threshold / 100.0)
        fut = c[i + 1:]
        hit = np.nonzero(fut <= thr)[0]
        hold_ret = c[-1] / cost - 1.0  # 死扛：持有到期末

        if len(hit):
            j = i + 1 + int(hit[0])
            exit_price = c[j]
            cut_ret = exit_price / cost - 1.0
            k = min(j + horizon, n - 1)
            post = c[k] / exit_price - 1.0
            rows.append((i, 1, j - i, cut_ret, hold_ret, post, float(cost)))
        else:
            rows.append((i, 0, n - 1 - i, hold_ret, hold_ret, np.nan, float(cost)))

    out = pd.DataFrame(rows, columns=[
        "entry_idx", "triggered", "hold_days", "cut_ret", "hold_ret", "post_horizon_ret", "cost"
    ])
    out["entry_date"] = pd.to_datetime(dates[out["entry_idx"].values])
    return out


def summarize(detail, code, threshold, source, name, min_samples=30):
    trig = detail[detail["triggered"] == 1]
    m = {
        "code": code,
        "name": name,
        "source": source,
        "threshold": threshold,
        "entries": len(detail),
        "triggered": len(trig),
        "trigger_rate": len(trig) / len(detail) if len(detail) else np.nan,
        "avg_hold_days": trig["hold_days"].mean() if len(trig) else np.nan,
        "cut_ret": trig["cut_ret"].mean() if len(trig) else np.nan,
        "hold_ret_same": trig["hold_ret"].mean() if len(trig) else np.nan,
        "diff": (trig["cut_ret"] - trig["hold_ret"]).mean() if len(trig) else np.nan,
        "median_diff": (trig["cut_ret"] - trig["hold_ret"]).median() if len(trig) else np.nan,
        "win_rate": (trig["cut_ret"] > trig["hold_ret"]).mean() if len(trig) else np.nan,
        "post_down_rate": (trig["post_horizon_ret"] < 0).mean() if len(trig) else np.nan,
        "post_ret": trig["post_horizon_ret"].mean() if len(trig) else np.nan,
    }
    m["sample_ok"] = len(trig) >= min_samples
    return m


# ─────────────────────────── 输出 ───────────────────────────

PCT = lambda v: "—" if pd.isna(v) else f"{v * 100:.1f}%"


def print_table(rows):
    cols = [
        ("标的", lambda r: f"{r['name']}({r['code']})"),
        ("阈值", lambda r: f"{r['threshold']:g}%"),
        ("入场点", lambda r: str(r["entries"])),
        ("触发次数", lambda r: str(r["triggered"])),
        ("触发率", lambda r: PCT(r["trigger_rate"])),
        ("平均持有日", lambda r: "—" if pd.isna(r["avg_hold_days"]) else f"{r['avg_hold_days']:.0f}"),
        ("止损后60日续跌", lambda r: PCT(r["post_down_rate"])),
        ("止损收益", lambda r: PCT(r["cut_ret"])),
        ("死扛收益", lambda r: PCT(r["hold_ret_same"])),
        ("收益差均值", lambda r: PCT(r["diff"])),
        ("收益差中位", lambda r: PCT(r["median_diff"])),
        ("止损更优占比", lambda r: PCT(r["win_rate"])),
        ("样本", lambda r: "✓" if r["sample_ok"] else "不足"),
    ]
    width = [max(len(h), 10) for h, _ in cols]
    print("  ".join(h.ljust(w) for (h, _), w in zip(cols, width)))
    print("  ".join("-" * w for w in width))
    for r in rows:
        print("  ".join(str(f(r)).ljust(w) for (_, f), w in zip(cols, width)))


def judge(rows):
    """判定「该不该设」：以配对中位收益差为主，连续下跌率仅作解释。

    成立条件：中位收益差 > 0 且止损更优占比 > 55%，且在 ≥2 只标的、≥3 个阈值上同时成立。
    """
    valid = [r for r in rows if r["sample_ok"]]
    if not valid:
        return "样本不足，无法判断", [], "无有效样本"
    winners = [r for r in valid if r["median_diff"] > 0 and r["win_rate"] > 0.55]
    codes_hit = {r["code"] for r in winners}
    ths_hit = {r["threshold"] for r in winners}
    ok = len(codes_hit) >= 2 and len(ths_hit) >= 3
    verdict = "该设" if ok else "不该设"
    note = f"满足条件 {len(winners)} 组（{len(codes_hit)} 只票、{len(ths_hit)} 个阈值）"
    return verdict, valid, note


def write_report(rows, verdict, valid, note, horizon):
    os.makedirs(RESULT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    csv_path = os.path.join(RESULT_DIR, f"backtest_stoploss_{stamp}.csv")
    pd.DataFrame(rows).to_csv(csv_path, index=False, encoding="utf-8-sig")

    md = [f"# 成本回撤止损阈值回测（逐点入场法）", "",
          f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M}",
          f"- 口径：每个交易日为一次买入，成本=当日收盘；触发即以当日收盘卖出且不再买回；对照为同一入场点持有到期末",
          f"- 止损后观察窗口：{horizon} 个交易日",
          f"- **结论：{verdict}**（{note}）", ""]

    md.append("| 标的 | 阈值 | 入场点 | 触发次数 | 触发率 | 止损后60日续跌 | 止损收益 | 死扛收益 | 收益差均值 | 收益差中位 | 止损更优占比 | 平均持有日 | 样本 |")
    md.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        md.append("| {} | {:g}% | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            f"{r['name']}({r['code']})", r["threshold"], r["entries"], r["triggered"],
            PCT(r["trigger_rate"]), PCT(r["post_down_rate"]), PCT(r["cut_ret"]),
            PCT(r["hold_ret_same"]), PCT(r["diff"]), PCT(r["median_diff"]), PCT(r["win_rate"]),
            "—" if pd.isna(r["avg_hold_days"]) else f"{r['avg_hold_days']:.0f}",
            "✓" if r["sample_ok"] else "不足"))

    md += ["", "## 口径说明与局限", "",
           "1. 止损后不再买回，是保守口径：真实操作可能低位重新入场，实际差距会小于表中数字。",
           "2. 样本仅覆盖所列标的，且多为周期/高股息股，结论不外推到其他品种。",
           "3. 未计入交易成本与滑点；阈值越近触发越频繁，实际损耗更大。",
           "4. 判定规则事先写死（收益差中位 > 0 且 止损更优占比 > 55%，需 ≥2 只票、≥3 阈值同时成立），避免看到结果再挑理由。", "",
           f"明细 CSV：`{csv_path}`"]
    md_path = os.path.join(RESULT_DIR, f"backtest_stoploss_{stamp}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    return csv_path, md_path


# ─────────────────────────── 主流程 ───────────────────────────

NAMES = {
    "600585": "海螺水泥", "601006": "大秦铁路", "600600": "青岛啤酒",
    "510300": "沪深300ETF", "588000": "科创50ETF",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", nargs="+", required=True)
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--thresholds", default="5,8,10,15,20")
    ap.add_argument("--horizon", type=int, default=60)
    args = ap.parse_args()

    end = datetime.now()
    start = end - timedelta(days=365 * args.years + 30)
    s, e = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    ths = [float(x) for x in args.thresholds.split(",")]

    all_rows = []
    for code in args.codes:
        name = NAMES.get(code, code)
        print(f"\n取数 {name}({code}) {s} ~ {e} ...", flush=True)
        df, source = fetch(code, s, e)
        if df is None:
            print(f"  ✗ 取数失败，跳过")
            continue
        span = f"{df['date'].iloc[0]:%Y-%m-%d} ~ {df['date'].iloc[-1]:%Y-%m-%d}"
        print(f"  ✓ {len(df)} 个交易日，{span}，来源 {source}")
        if len(df) < 250:
            print("  ✗ 样本不足 250 日，跳过")
            continue
        for x in ths:
            d = backtest_one(df, x, args.horizon)
            all_rows.append(summarize(d, code, x, source, name))

    if not all_rows:
        print("无可用结果")
        return

    print("\n" + "=" * 130)
    print_table(all_rows)
    print("=" * 130)

    verdict, valid, note = judge(all_rows)
    print(f"\n判定：{verdict}    {note}")
    csv_path, md_path = write_report(all_rows, verdict, valid, note, args.horizon)
    print(f"明细：{csv_path}\n结论：{md_path}")


if __name__ == "__main__":
    main()
