"""
pe_band.py —— 极简 PE-Band 图（价格 vs 历史PE分位轨道），批量版

用法：
    python pe_band.py            # 自动扫描 data/erp_*.csv，把所有能算的标的都画进一张图
    python pe_band.py 000300     # 只画单个标的（保留原用法）

逻辑：
  1. 扫描 data/erp_{code}.csv，凑出所有有历史PE数据的code
  2. 和 data/etf_price.csv 的列名取交集（要有价格才能反推EPS）
  3. 对每个code： EPS = Price / PE（反推），用 PE 历史分位数（P10/P50/P90）
     算出几条固定PE倍数，乘回逐日EPS得到轨道价格，和真实价格一起画一个子图
  4. 所有子图竖着按code排序摞在一张图里，输出 docs/pe_band_all.png
     （单个标的模式下输出 docs/pe_band_{code}.png）
"""

import sys
import glob
import os
import re
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
from config_loader import CODE_NAME

# 中文字体：CI环境（Ubuntu）默认没装中文字体，会显示成方框。
for _font in ["WenQuanYi Zen Hei", "Noto Sans CJK SC", "Microsoft YaHei", "SimHei", "PingFang SC"]:
    if _font in {f.name for f in matplotlib.font_manager.fontManager.ttflist}:
        matplotlib.rcParams["font.sans-serif"] = [_font]
        break
matplotlib.rcParams["axes.unicode_minus"] = False


def display_name(code):
    name = CODE_NAME.get(code)
    return f"{code} {name}" if name else code


def discover_codes():
    """扫描 data/erp_{code}.csv，和 etf_price.csv 列名取交集"""
    price_cols = set(pd.read_csv("./data/etf_price.csv", nrows=0).columns) - {"date"}
    codes = []
    for path in sorted(glob.glob("./data/erp_*.csv")):
        m = re.match(r"erp_(.+)\.csv", os.path.basename(path))
        if not m:
            continue
        code = m.group(1)
        if code in price_cols:
            codes.append(code)
    return codes


def load_band_df(code):
    """给定code，返回带轨道价格列的df，没有可用数据时返回None"""
    pe_df = pd.read_csv(f"./data/erp_{code}.csv", parse_dates=["Date"])[["Date", "PE"]].dropna()
    price_df = pd.read_csv("./data/etf_price.csv", parse_dates=["date"])[["date", code]].dropna()
    price_df = price_df.rename(columns={"date": "Date", code: "Price"})

    df = pd.merge(pe_df, price_df, on="Date", how="inner").sort_values("Date")
    if df.empty:
        return None

    df["EPS"] = df["Price"] / df["PE"]
    q = {"上轨(P90)": df["PE"].quantile(0.90),
         "中轨(P50)": df["PE"].quantile(0.50),
         "下轨(P10)": df["PE"].quantile(0.10)}
    for label, pe_mult in q.items():
        df[label] = pe_mult * df["EPS"]
    return df, q


def draw_subplot(ax, code, df, q):
    for label in q:
        ax.plot(df["Date"], df[label], linewidth=1, label=f"{label} {q[label]:.1f}x")
    ax.plot(df["Date"], df["Price"], color="crimson", linewidth=1.3, label="实际价格/点位")
    ax.set_title(f"{display_name(code)} PE-Band", fontsize=10)
    ax.legend(loc="upper left", fontsize=7)
    ax.grid(alpha=0.3)


def main_single(code):
    result = load_band_df(code)
    if result is None:
        print(f"❌ {code} 没有可用的 PE/价格重合数据")
        return
    df, q = result
    fig, ax = plt.subplots(figsize=(11, 5))
    draw_subplot(ax, code, df, q)
    fig.tight_layout()
    os.makedirs("./docs", exist_ok=True)
    out_path = f"./docs/pe_band_{code}.png"
    fig.savefig(out_path, dpi=150)
    print(f"✅ 已保存 {out_path}")


def main_all():
    codes = discover_codes()
    rows = []
    for code in codes:
        result = load_band_df(code)
        if result is not None:
            rows.append((code, *result))
        else:
            print(f"⚠️ 跳过 {code}：无可用重合数据")

    if not rows:
        print("❌ 没有任何标的能生成PE-Band")
        return

    n = len(rows)
    fig, axes = plt.subplots(n, 1, figsize=(11, 4 * n))
    if n == 1:
        axes = [axes]
    for ax, (code, df, q) in zip(axes, rows):
        draw_subplot(ax, code, df, q)

    fig.tight_layout()
    os.makedirs("./docs", exist_ok=True)
    out_path = "./docs/pe_band_all.png"
    fig.savefig(out_path, dpi=150)
    print(f"✅ 已保存 {out_path}（共 {n} 个标的：{', '.join(display_name(c) for c, _, _ in rows)}）")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main_single(sys.argv[1])
    else:
        main_all()
