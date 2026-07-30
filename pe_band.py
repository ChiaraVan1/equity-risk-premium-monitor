"""
pe_band.py —— 极简 PE-Band 图（价格 vs 历史PE分位轨道）

用法：
    python pe_band.py 000300
    python pe_band.py SPY

逻辑：
  1. 读 data/erp_{code}.csv 拿 Date/PE
  2. 读 data/etf_price.csv 拿该 code 对应的价格序列
  3. EPS = Price / PE（反推）
  4. 用 PE 历史分位数（P10/P50/P90，可自行改）算出几条固定PE倍数
  5. 轨道价格 = 固定PE倍数 × 逐日EPS，和真实价格画在一张图上
  6. 输出 docs/pe_band_{code}.png
"""

import sys
import pandas as pd
import matplotlib.pyplot as plt

def main(code):
    pe_df = pd.read_csv(f"./data/erp_{code}.csv", parse_dates=["Date"])[["Date", "PE"]].dropna()

    price_df = pd.read_csv("./data/etf_price.csv", parse_dates=["date"])[["date", code]].dropna()
    price_df = price_df.rename(columns={"date": "Date", code: "Price"})

    df = pd.merge(pe_df, price_df, on="Date", how="inner").sort_values("Date")
    if df.empty:
        print(f"❌ {code} 没有可用的 PE/价格重合数据")
        return

    # 反推 EPS
    df["EPS"] = df["Price"] / df["PE"]

    # 固定PE倍数轨道（用全历史分位数，可按需改成滚动窗口）
    q = {"上轨(P90)": df["PE"].quantile(0.90),
         "中轨(P50)": df["PE"].quantile(0.50),
         "下轨(P10)": df["PE"].quantile(0.10)}

    for label, pe_mult in q.items():
        df[label] = pe_mult * df["EPS"]

    # 画图
    fig, ax = plt.subplots(figsize=(11, 5))
    for label in q:
        ax.plot(df["Date"], df[label], linewidth=1, label=f"{label} {q[label]:.1f}x")
    ax.plot(df["Date"], df["Price"], color="crimson", linewidth=1.5, label="实际价格/点位")

    ax.set_title(f"{code} PE-Band（价格 vs 历史PE分位轨道）")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()

    import os
    os.makedirs("./docs", exist_ok=True)
    out_path = f"./docs/pe_band_{code}.png"
    fig.savefig(out_path, dpi=150)
    print(f"✅ 已保存 {out_path}")

if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "000300"
    main(code)
