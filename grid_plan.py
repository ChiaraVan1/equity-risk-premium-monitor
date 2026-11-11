#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网格档位：按固定成本价 + 等差 5% 档位，算出"下一买点"。

数据来源
  成本价：data/grid_state.csv（code, name, anchor）—— 本地手填；云端由 Secret GRID_STATE 还原
  现价：data/etf_price.csv 最后一个交易日（不复权实际价，与券商成本同口径）

口径
  第 1 档 = 成本价本身，第 n 档 = 成本 × (1 − 5%×(n−1))
  成本价冻结，成交不改锚；价格跌到哪条线就买哪档
"""

import os

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "data", "grid_state.csv")
PRICE = os.path.join(BASE, "data", "etf_price.csv")
STEP = 5.0

_state_cache = None
_price_cache = None


def load_state():
    global _state_cache
    if _state_cache is not None:
        return _state_cache
    if not os.path.exists(STATE):
        _state_cache = {}
        return _state_cache
    try:
        df = pd.read_csv(STATE, dtype={"code": str})
        _state_cache = {
            str(r["code"]): float(r["anchor"])
            for _, r in df.iterrows()
            if pd.notna(r.get("anchor"))
        }
    except Exception:
        _state_cache = {}
    return _state_cache


def latest_prices():
    global _price_cache
    if _price_cache is not None:
        return _price_cache
    if not os.path.exists(PRICE):
        _price_cache = {}
        return _price_cache
    try:
        last = pd.read_csv(PRICE).iloc[-1]
        _price_cache = {k: float(v) for k, v in last.items() if k != "date" and pd.notna(v)}
    except Exception:
        _price_cache = {}
    return _price_cache


def next_buy_point(code):
    """返回 (触发价, 第几档, 距现价百分比) 或 None。"""
    anchor = load_state().get(str(code))
    price = latest_prices().get(str(code))
    if not anchor or not price:
        return None
    for k in range(0, 8):
        level = anchor * (1 - STEP * k / 100.0)
        if level < price:
            return level, k + 1, (level / price - 1) * 100
    return None


def build_grid_block(code):
    """给日报用的单只一行档位说明；无成本数据时返回空串。"""
    anchor = load_state().get(str(code))
    if not anchor:
        return ""
    nxt = next_buy_point(code)
    if not nxt:
        return ""
    level, n, gap = nxt
    if n == 1:
        return f"**网格档位**：现价高于成本 {anchor:.4f}，跌回成本才加仓\n\n"
    return f"**网格档位**：成本 {anchor:.4f} · 下一买点 {level:.4f}（第 {n} 档，还差 {abs(gap):.1f}%）\n\n"


if __name__ == "__main__":
    st = load_state()
    if not st:
        print("没有可用的成本数据（data/grid_state.csv）")
    for c in st:
        r = next_buy_point(c)
        if r:
            print(f"{c}: 下一买点 {r[0]:.4f}（第 {r[1]} 档，{r[2]:+.1f}%）")
