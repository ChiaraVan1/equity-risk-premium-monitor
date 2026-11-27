#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合理买入价展示层（2026-11-27）。

规则（已定）：
- 正本＝日报 target_price/config.json，经 fetch/stock_target_price.py 缓存到 data/stock_target_price.json
- 只做并列提示，不覆盖分位决定的动作
- 到价判定：现价 ≤ 合理买入价 即「已到价」
- 冲突必须显式：分位偏便宜但现价高于买入价时追加 ⚠️
"""
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(REPO, "data", "stock_target_price.json")

_cache = None


def _load():
    global _cache
    if _cache is None:
        try:
            with open(CACHE, encoding="utf-8") as f:
                _cache = json.load(f)
        except Exception:
            _cache = {"updated": None, "stocks": {}}
    return _cache


def build_target_price_suffix(code, price, zone_pct=None):
    """个股行尾的价位层文案；无目标价或无现价时返回空串（不影响其它内容）。"""
    data = _load()
    t = (data.get("stocks") or {}).get(str(code))
    if not t or not price:
        return ""
    target = float(t["target"])
    if target <= 0:
        return ""
    upd = data.get("updated") or ""
    ver = f"{upd[5:]} 版" if len(upd) >= 10 else "版本未知"
    dev = (float(price) / target - 1) * 100

    if dev <= 0:
        return (f" · 合理买入价 {target:.2f}（{ver}）· 现价 {float(price):.2f}"
                f" → 已到价（低 {abs(dev):.1f}%）")

    s = (f" · 合理买入价 {target:.2f}（{ver}）· 现价 {float(price):.2f}"
         f" → 未到价（高 {dev:.1f}%）")
    if zone_pct is not None and zone_pct >= 75:
        s += f" ⚠️ 分位 {zone_pct:.0f}% 偏便宜但现价高于买入价"
    return s


def target_meta():
    d = _load()
    return {"updated": d.get("updated"), "count": len(d.get("stocks") or {})}
