#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""档位表：按「本轮首笔成本」固定锚 + 等差档位算出下一档触发价。

数据来源
  成本：data/grid_state.csv（code, name, anchor）—— 自己填
  现价：data/etf_price.csv 最后一个交易日（系统每天 18:30 更新）

口径
  第 1 档 = anchor 本身（首笔买入价）
  第 n 档 = anchor × (1 − 5% × (n−1))，即 95% / 90% / 85% / 80%
  anchor 永不改；清仓后重算 anchor。下一买点由「现价与 anchor 的距离」反推，不记「已买几档」

用法
  python3 grid.py            # 输出到 ~/Desktop/grid_plan.md
"""

import os

import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根（本文件在 grid/ 下）
STATE = os.path.join(BASE, "data", "grid_state.csv")
PRICE = os.path.join(BASE, "data", "etf_price.csv")
TIPS = os.path.join(BASE, "data", "tips_10y.csv")
OUT = os.path.expanduser("~/Desktop/grid_plan.md")
STEP = 5.0


def _live_etf_price(code):
    """不在 etf_price.csv 也不在 erp_*.csv 的标的（如黄金 518880，商品 ETF 无 PE 口径、
    未进 config 监控池），直接从新浪行情取最新价，仅供档位表使用。"""
    import urllib.request

    digits = str(code).split(".")[0]
    if not digits.isdigit():
        return None
    prefix = "sh" if digits.startswith(("51", "58", "56", "50", "52")) else "sz"
    url = f"https://hq.sinajs.cn/list={prefix}{digits}"
    try:
        req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
        raw = urllib.request.urlopen(req, timeout=8).read().decode("gbk", "ignore")
        fields = raw.split('"')[1].split(",")
        price = float(fields[3])  # 名称,今开,昨收,现价,...
        return price if price > 0 else None
    except Exception:
        return None


def _stock_price(code):
    """个股不在 etf_price.csv 里，现价回退到 erp_{code}.csv 的最后一根收盘价。"""
    f = os.path.join(BASE, "data", f"erp_{code}.csv")
    if not os.path.exists(f):
        return None
    try:
        df = pd.read_csv(f).dropna(subset=["IndexClose"])
        return float(df["IndexClose"].iloc[-1]) if len(df) else None
    except Exception:
        return None


def build():
    st = pd.read_csv(STATE, dtype={"code": str})
    px = pd.read_csv(PRICE)
    last = px.iloc[-1]
    asof = last["date"]
    rows = []

    for _, r in st.iterrows():
        code, name = str(r["code"]), r["name"]
        anchor = r["anchor"]
        price = last.get(code)
        if pd.notna(price):
            price = float(price)
        else:
            price = _stock_price(code) or _live_etf_price(code)

        if pd.isna(anchor):
            rows.append({"name": name, "code": code, "anchor": None,
                         "next_no": 1, "trigger": None, "price": price, "gap": None,
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
        rows.append({"name": name, "code": code, "anchor": anchor,
                     "next_no": nxt, "trigger": trig, "price": price, "gap": gap, "note": note})
    return rows, asof


def _gold_context():
    """黄金背景：实际利率。518880 是商品 ETF，无 PE，ERP 对它是错的工具。

    纯规则模板，不经 LLM：所有数字来自 data/tips_10y.csv，**判定用历史分位，
    不用人为设定的阈值**（曾经写过「1.5% 经验阈值」，那个数是凭空定的，已废弃）。
    数据缺失或样本不足时整段不输出，不留半截。
    """
    if not os.path.exists(TIPS):
        return ""
    try:
        d = pd.read_csv(TIPS, encoding="utf-8-sig")
        if d.empty or len(d) < 500:      # 样本太短算分位没有意义
            return ""
        cur = float(d.iloc[-1]["real_yield"])
        nom = float(d.iloc[-1]["nominal_10y"])
        inf = float(d.iloc[-1]["inflation_expectation"])
        asof = str(d.iloc[-1]["date"])
        series = pd.to_numeric(d["real_yield"], errors="coerce").dropna()
        pctl = (series < cur).mean() * 100
        med = series.median()
        lo, hi = series.min(), series.max()
        span = f"{d.iloc[0]['date'][:4]}–{asof[:4]}"
    except Exception:  # noqa: BLE001
        return ""

    return (
        "\n## 黄金背景（518880 是商品 ETF，无 PE，ERP 不适用）\n\n"
        f"实际利率 **{cur:.2f}%** = 名义 10 年期美债 {nom:.2f}% "
        f"− 隐含通胀预期 {inf:.2f}%（{asof}）\n\n"
        f"> 历史位置：{span} 共 {len(series)} 个交易日，中位 {med:.2f}%、"
        f"区间 {lo:.2f}%~{hi:.2f}%。当前 {cur:.2f}% 处于第 **{pctl:.1f}** 百分位。\n"
        "> 机制说明（非统计结论）：实际利率越高，持有黄金这种无息资产的机会成本越大。\n"
        "> 此处只给读数，不产生买卖动作。\n"
    )


def render(rows, asof):
    lines = [f"# 档位表 · 截至 {asof}（ETF/个股/黄金同源，均为当日收盘价）", "",
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
    return "\n".join(lines) + _gold_context()


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
