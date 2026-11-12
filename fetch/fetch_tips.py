#!/usr/bin/env python3
"""美国 10 年期实际利率（TIPS）月频数据。

用途：518880 黄金是商品 ETF，无 PE，ERP 对它是错的工具。判断黄金看「实际利率」
= 名义 10 年期美债收益率 − 隐含通胀预期。本脚本取 FRED 的 DFII10（TIPS 实际利率）
与 DGS10（名义），两者相减得隐含通胀预期。

三条设计约束（都是踩过的坑）：
1. 只做月频数据源，日频是噪声不是信号。
2. 请求失败时**不覆盖**已有的 data/tips_10y.csv，只打 warning 并以 0 退出——
   抓取失败覆盖旧值会让报告拿着残缺数据照常发布。
3. 必须带 timeout，FRED 在国内会偶发长时间无响应（原 fetch_fred_bond_history
   没有 timeout，实测卡住超过 1 分钟）。

输出 data/tips_10y.csv，单位是百分数（2.66 表示 2.66%），不是小数。
列：date, real_yield, nominal_10y, inflation_expectation
"""

import sys
import os
import io
import csv
import time

import requests

# 允许从同目录导入 FRED key（fetch_bond_yield.py 第 21 行已配置）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_bond_yield import FRED_API_KEY  # noqa: E402

OUT = "./data/tips_10y.csv"
URL = "https://api.stlouisfed.org/fred/series/observations"
START = "2003-01-01"        # DFII10 起点，存全历史才能算「当前处在第几分位」
TIMEOUT = 15
RETRIES = 3


def fetch_series(series_id):
    """取单条 FRED 序列，返回 {date: value(百分数)}。失败抛异常。"""
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "observation_start": START,
    }
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(URL, params=params, timeout=TIMEOUT)
            data = r.json()
            if "observations" not in data:
                raise ValueError(f"FRED 返回错误: {data.get('error_message')}")
            out = {}
            for o in data["observations"]:
                v = o.get("value")
                if v in (".", "", None):
                    continue  # 节假日 FRED 用 "." 表示缺失
                out[o["date"]] = float(v)
            if not out:
                raise ValueError(f"{series_id} 返回 0 条有效数据")
            return out
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < RETRIES:
                time.sleep(2 * attempt)
    raise RuntimeError(f"{series_id} 取数失败（{RETRIES} 次重试）：{last_err}")


def main():
    try:
        real = fetch_series("DFII10")
        nominal = fetch_series("DGS10")
    except Exception as e:  # noqa: BLE001
        print(f"⚠️  跳过：{e}")
        if os.path.exists(OUT):
            print(f"   保留原有 {OUT}，不覆盖")
        return 0  # 关键：失败不能拖垮整条日报流水线

    dates = sorted(set(real) & set(nominal))
    if not dates:
        print("⚠️  跳过：两条序列没有共同日期")
        if os.path.exists(OUT):
            print(f"   保留原有 {OUT}，不覆盖")
        return 0

    rows = []
    for d in dates:
        rows.append({
            "date": d,
            "real_yield": f"{real[d]:.2f}",
            "nominal_10y": f"{nominal[d]:.2f}",
            "inflation_expectation": f"{nominal[d] - real[d]:.2f}",
        })

    with io.open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["date", "real_yield", "nominal_10y", "inflation_expectation"],
            lineterminator="\r\n",
        )
        w.writeheader()
        w.writerows(rows)

    last = rows[-1]
    print(f"   ✓ {len(rows)} 条, {rows[0]['date']} ~ {last['date']}")
    print(f"   最新：实际 {last['real_yield']}% = 名义 {last['nominal_10y']}% "
          f"− 通胀预期 {last['inflation_expectation']}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
