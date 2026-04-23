import akshare as ak
import yfinance as yf
import pandas as pd
import numpy as np
import time

tickers_hk = ["0700.HK","9988.HK","3690.HK","9618.HK","1810.HK",
              "9999.HK","2382.HK","0981.HK","9626.HK","0020.HK",
              "1024.HK","0268.HK","2015.HK","9868.HK","9888.HK",
              "0241.HK","0285.HK","2518.HK","0522.HK","0780.HK",
              "0909.HK","2013.HK","9961.HK","6690.HK",
              "0799.HK","2359.HK","0669.HK","1347.HK","0763.HK"]

def get_quarterly_revenue(code):
    """
    返回单季度营收 Series，index 为 REPORT_DATE
    累计值转单季度：Q1=累计Q1, Q2=累计Q2-Q1, Q3=累计Q3-Q2, Q4=年度-累计Q3
    """
    df = ak.stock_financial_hk_report_em(
        stock=code, symbol="利润表", indicator="季度"
    )
    df = df[df["STD_ITEM_NAME"] == "营业额"][["REPORT_DATE","AMOUNT"]].copy()
    df["REPORT_DATE"] = pd.to_datetime(df["REPORT_DATE"])
    df = df.sort_values("REPORT_DATE").set_index("REPORT_DATE")
    df = df[~df.index.duplicated(keep="last")]

    # 区分Q4（年末12-31）和Q1/Q2/Q3（累计）
    single_q = []
    dates = df.index.tolist()
    for i, d in enumerate(dates):
        val = df.loc[d, "AMOUNT"]
        if d.month == 12:  # Q4是年度值，单季度=年度-前三季度累计
            q3_date = pd.Timestamp(d.year, 9, 30)
            q3_val = df.loc[q3_date, "AMOUNT"] if q3_date in df.index else np.nan
            single_q.append({"date": d, "rev": val - q3_val if not np.isnan(q3_val) else np.nan})
        elif d.month == 9:  # Q3累计，单季度=Q3累计-Q2累计
            q2_date = pd.Timestamp(d.year, 6, 30)
            q2_val = df.loc[q2_date, "AMOUNT"] if q2_date in df.index else np.nan
            single_q.append({"date": d, "rev": val - q2_val if not np.isnan(q2_val) else np.nan})
        elif d.month == 6:  # Q2累计，单季度=Q2累计-Q1
            q1_date = pd.Timestamp(d.year, 3, 31)
            q1_val = df.loc[q1_date, "AMOUNT"] if q1_date in df.index else np.nan
            single_q.append({"date": d, "rev": val - q1_val if not np.isnan(q1_val) else np.nan})
        elif d.month == 3:  # Q1就是单季度
            single_q.append({"date": d, "rev": val})

    s = pd.DataFrame(single_q).set_index("date")["rev"]
    return s.sort_index()

def get_ttm_revenue(single_q_series):
    """滚动4个季度求和得到TTM"""
    return single_q_series.rolling(4, min_periods=4).sum()

def get_hist_mktcap(ticker):
    """历史每日市值 = 收盘价 × 股本"""
    tk = yf.Ticker(ticker)
    hist = tk.history(start="2020-07-01")  # 恒生科技2020年7月发布
    shares = tk.fast_info.shares
    if shares is None or len(hist) == 0:
        return None
    hist["mktcap"] = hist["Close"] * shares
    return hist["mktcap"]

# ── 主循环 ──
all_mktcap = {}
all_ttm_rev = {}

for t in tickers_hk:
    code = t.replace(".HK","").zfill(5)
    try:
        # 营收
        sq = get_quarterly_revenue(code)
        ttm = get_ttm_revenue(sq)
        all_ttm_rev[t] = ttm
        
        # 市值
        mc = get_hist_mktcap(t)
        if mc is not None:
            all_mktcap[t] = mc
        
        print(f"{t} ✓  营收期数={len(sq)}  市值条数={len(mc) if mc is not None else 0}")
        time.sleep(1.5)
        
    except Exception as e:
        print(f"{t} 失败: {e}")
        time.sleep(2)

# ── 合并计算每日PS ──
# 统一到月末频率，避免日期对不上
date_range = pd.date_range("2020-07-01", pd.Timestamp.today(), freq="ME")

ps_series = []
for d in date_range:
    total_mc = 0
    total_rev = 0
    for t in tickers_hk:
        # 市值：取当日或最近一个交易日
        if t in all_mktcap:
            mc_s = all_mktcap[t]
            mc_s.index = mc_s.index.tz_localize(None)
            idx = mc_s.index.asof(d)
            if pd.notna(idx):
                total_mc += mc_s.loc[idx]
        # TTM营收：取最近已公布的季度
        if t in all_ttm_rev:
            rev_s = all_ttm_rev[t].dropna()
            idx = rev_s.index.asof(d)
            if pd.notna(idx):
                total_rev += rev_s.loc[idx]
    
    if total_rev > 0:
        ps_series.append({"date": d, "ps": total_mc / total_rev})

ps_df = pd.DataFrame(ps_series).set_index("date")

# ── 加入 rf 和 PSY ──
try:
    # 直接从已有的沪深300 ERP文件读CN10Y，不重新请求
    erp_ref = pd.read_csv("./data/erp_000300.csv", parse_dates=['Date'])
    erp_ref = erp_ref[['Date', 'Bond_Yield_10Y']].dropna().set_index('Date')
    bond_monthly = erp_ref['Bond_Yield_10Y'].resample('ME').last()
    print(f"   bond_monthly index 样例: {bond_monthly.index[:3].tolist()}")
    print(f"   ps_df index 样例: {ps_df.index[:3].tolist()}")
    ps_df['rf']  = [bond_monthly.asof(d) for d in ps_df.index]
    ps_df['psy'] = (1 / ps_df['ps']) - ps_df['rf']
    print("PSY计算完成")
except Exception as e:
    import traceback
    print(f"❌ PSY计算失败: {e}")
    print(traceback.format_exc())

print(ps_df.tail())
print(f"\n当前PS:  {ps_df['ps'].iloc[-1]:.2f}x")
print(f"历史均值: {ps_df['ps'].mean():.2f}x")
print(f"历史最高: {ps_df['ps'].max():.2f}x")
print(f"历史最低: {ps_df['ps'].min():.2f}x")
print(f"PS当前分位:  {(ps_df['ps'] < ps_df['ps'].iloc[-1]).mean()*100:.0f}%")
if 'psy' in ps_df.columns and ps_df['psy'].notna().any():
    cur_psy = ps_df['psy'].iloc[-1]
    print(f"PSY当前: {cur_psy:.2%}")
    print(f"PSY当前分位: {(ps_df['psy'] < cur_psy).mean()*100:.0f}%")

import os
os.makedirs("./data", exist_ok=True)
ps_df.to_csv("./data/ps_HSTECH.csv", index=True, encoding="utf-8-sig")