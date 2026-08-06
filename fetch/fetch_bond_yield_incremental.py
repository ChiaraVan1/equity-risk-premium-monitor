import akshare as ak
import yfinance as yf
import requests
import pandas as pd
import numpy as np
import os
import sys
import time
from datetime import datetime, timedelta
from io import StringIO

# 【2026-08-02 目录重组说明】本文件从仓库根目录挪到了 fetch/ 下，被
# prepare_all_data.py 以子进程方式直接运行，手动把仓库根目录加回 sys.path，
# 否则 `from config_loader import` 会报 ModuleNotFoundError。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config_loader import BOND_YIELD_CONFIG

# 同目录下的 freshness.py（数据新鲜度校验工具）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from freshness import (
    check_date_freshness,
    check_value_streak,
    write_freshness_report,
    print_freshness_summary,
)

# ── 配置表 ────────────────────────────────────────────────────────────────────

FRED_API_KEY = "a8ce66c09bbcedfb9e33de739a0dcbfb"

# 本次运行收集到的所有新鲜度校验结果，main() 结尾统一汇总打印+落盘
_freshness_results: list[dict] = []

# QQQ_PE_TODAY 上次成功手动填值的时间戳文件
_QQQ_PE_STATE_PATH = "./data/_qqq_pe_last_fill.json"

# PE 值 streak 状态持久化文件（记录每个指数连续几天 PE 和前一天完全相同）
_PE_STREAK_STATE_PATH = "./data/_pe_value_streak.json"


def _load_json(path: str) -> dict:
    import json
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_json(path: str, data: dict):
    import json
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

BOND_CONFIG = {
    'CN10Y': 'bond_china',
    'US10Y': 'DGS10',
    'FR10Y': 'IRLTLT01FRM156N',
    'DE10Y': 'IRLTLT01DEM156N',
    'JP10Y': 'IRLTLT01JPM156N',
}

# ── 手动填入今日 PE ───────────────────────────────────────────────────────────
_qqq_pe_env = os.environ.get("QQQ_PE_TODAY")
QQQ_PE_TODAY = float(_qqq_pe_env) if _qqq_pe_env else None

# 【2026-08-04】HS_TECH_PE_TODAY 手动填值机制已删除：HSTECH 改用 PS/PSY 口径
# （见 fetch/fetch_ps.py、erp_position.py），不再需要人工每日填PE。

# ── 国债增量获取 ───────────────────────────────────────────────────────────────

def fetch_cn_bond_incremental(start_date, end_date):
    df = ak.bond_china_yield(start_date=start_date, end_date=end_date)
    df = df[df['曲线名称'] == '中债国债收益率曲线'][['日期', '10年']]
    df.columns = ['Date', 'Bond_Yield_10Y']
    df['Date'] = pd.to_datetime(df['Date'])
    df['Bond_Yield_10Y'] = df['Bond_Yield_10Y'] / 100
    return df

def fetch_fred_bond_incremental(series_id, start_date_str):
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "observation_start": start_date_str,
    }
    r = requests.get(url, params=params)
    data = r.json()
    if "observations" not in data:
        raise ValueError(f"FRED 错误: {data.get('error_message')}")
    df = pd.DataFrame(data["observations"])[["date", "value"]]
    df.columns = ['Date', 'Bond_Yield_10Y']
    df['Date'] = pd.to_datetime(df['Date'])
    df['Bond_Yield_10Y'] = pd.to_numeric(df['Bond_Yield_10Y'], errors='coerce') / 100
    return df.dropna().sort_values('Date').reset_index(drop=True)

# ── PE 增量获取 ────────────────────────────────────────────────────────────────

def fetch_worldpe_today():
    url = "https://worldperatio.com/major-stock-index-pe-ratios"
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    r = requests.get(url, headers=headers)
    df = pd.read_html(StringIO(r.text))[0]
    df.columns = ['_'.join(c).strip() for c in df.columns]
    df = df.rename(columns={
        'Unnamed: 1_level_0_Unnamed: 1_level_1': 'symbol',
        'Unnamed: 3_level_0_P/E Ratio▾': 'PE',
    })
    df['PE'] = pd.to_numeric(df['PE'], errors='coerce')
    return dict(zip(df['symbol'], df['PE']))

# ── HSTECH PS 增量更新 ────────────────────────────────────────────────────────

HSTECH_TICKERS = [
    "0700.HK","9988.HK","3690.HK","9618.HK","9999.HK",
    "9888.HK","1024.HK","9626.HK","9961.HK","1698.HK",
    "0780.HK","0981.HK","1347.HK","0992.HK","2382.HK",
    "9660.HK","0100.HK","2513.HK","1810.HK","0300.HK",
    "6690.HK","0285.HK","1211.HK","2015.HK","9868.HK",
    "9866.HK","9863.HK","6618.HK","0241.HK","0020.HK",
]

def _get_single_quarter_revenue(code):
    df = ak.stock_financial_hk_report_em(stock=code, symbol="利润表", indicator="季度")
    df = df[df["STD_ITEM_NAME"] == "营业额"][["REPORT_DATE", "AMOUNT"]].copy()
    df["REPORT_DATE"] = pd.to_datetime(df["REPORT_DATE"])
    df = df.sort_values("REPORT_DATE").set_index("REPORT_DATE")
    df = df[~df.index.duplicated(keep="last")]

    single_q = []
    for d in df.index:
        val = df.loc[d, "AMOUNT"]
        if d.month == 12:
            q3 = pd.Timestamp(d.year, 9, 30)
            q3v = df.loc[q3, "AMOUNT"] if q3 in df.index else np.nan
            single_q.append({"date": d, "rev": val - q3v if pd.notna(q3v) else np.nan})
        elif d.month == 9:
            q2 = pd.Timestamp(d.year, 6, 30)
            q2v = df.loc[q2, "AMOUNT"] if q2 in df.index else np.nan
            single_q.append({"date": d, "rev": val - q2v if pd.notna(q2v) else np.nan})
        elif d.month == 6:
            q1 = pd.Timestamp(d.year, 3, 31)
            q1v = df.loc[q1, "AMOUNT"] if q1 in df.index else np.nan
            single_q.append({"date": d, "rev": val - q1v if pd.notna(q1v) else np.nan})
        elif d.month == 3:
            single_q.append({"date": d, "rev": val})

    return pd.DataFrame(single_q).set_index("date")["rev"].sort_index()


def update_hstech_ps(cn10y_series=None):
    """
    增量更新 ps_HSTECH.csv。
    - 文件不存在：全量重算（从2020-07开始）
    - 文件已存在：重算最近3个月（覆盖营收修订），再合并去重

    ★ 修复：国债数据按 calc_start ~ calc_end 独立拉取，
      不依赖主流程传入的只有30天的 cn10y_series，
      确保 bond_monthly 覆盖完整的计算区间。
      同时对 bond_monthly 做 ffill，防止月末恰好缺失。
    """
    ps_path = "./data/ps_HSTECH.csv"
    os.makedirs("./data", exist_ok=True)

    if os.path.exists(ps_path):
        old_ps = pd.read_csv(ps_path, index_col=0, parse_dates=True)
        last_date = old_ps.index.max()
        calc_start = last_date - pd.DateOffset(months=3)
        action = "增量"
    else:
        old_ps = pd.DataFrame()
        calc_start = pd.Timestamp("2020-07-01")
        action = "全量"

    calc_end = pd.Timestamp.today()
    date_range = pd.date_range(calc_start, calc_end, freq="ME")

    if len(date_range) == 0:
        print("   HSTECH PS 已是最新，无需更新")
        return

    print(f"   -> HSTECH PS {action}计算：{date_range[0].date()} ~ {date_range[-1].date()}，共{len(date_range)}个月末")

    all_mktcap = {}
    all_ttm_rev = {}

    for t in HSTECH_TICKERS:
        code = t.replace(".HK", "").zfill(5)
        try:
            sq = _get_single_quarter_revenue(code)
            ttm = sq.rolling(4, min_periods=4).sum()
            ttm.index = ttm.index.to_period("M").to_timestamp("M")
            all_ttm_rev[t] = ttm[~ttm.index.duplicated(keep="last")]

            tk = yf.Ticker(t)
            hist = tk.history(start=str(calc_start.date()))
            shares = tk.fast_info.shares
            if shares and len(hist) > 0:
                hist.index = hist.index.tz_localize(None)
                mc = hist["Close"] * shares
                mc.index = mc.index.to_period("M").to_timestamp("M")
                all_mktcap[t] = mc.groupby(mc.index).last()

            print(f"      ok {t}")
            time.sleep(1.2)

        except Exception as e:
            print(f"      skip {t}: {e}")
            time.sleep(1.5)

    # ★ 修复：按 calc_start ~ calc_end 独立拉取国债，覆盖完整计算区间
    start_str = calc_start.strftime("%Y%m%d")
    end_str   = calc_end.strftime("%Y%m%d")
    try:
        bond_df = fetch_cn_bond_incremental(start_str, end_str)
        print(f"   国债数据: {start_str} ~ {end_str}，{len(bond_df)} 条")
    except Exception as e:
        print(f"   ⚠️  国债数据获取失败，尝试降级使用传入数据: {e}")
        # 降级：用主流程传入的数据（可能不够用，但总比没有强）
        if cn10y_series is not None:
            bond_df = cn10y_series.copy()
        else:
            bond_df = pd.DataFrame(columns=["Date", "Bond_Yield_10Y"])

    bond_df["Date"] = pd.to_datetime(bond_df["Date"])
    bond_df = bond_df.set_index("Date")["Bond_Yield_10Y"].sort_index()

    # ★ 修复：ffill 防止月末恰好缺失导致整月 rf/PSY 为空
    bond_monthly = bond_df.resample("ME").last().ffill()

    # 按月末合并计算 PS 和 PSY
    new_rows = []
    for d in date_range:
        total_mc = np.nansum([
            all_mktcap[t].get(d, np.nan)
            for t in HSTECH_TICKERS if t in all_mktcap
        ])
        total_rev = np.nansum([
            all_ttm_rev[t].asof(d)
            if (t in all_ttm_rev and not all_ttm_rev[t].empty)
            else 0
            for t in HSTECH_TICKERS
        ])
        if total_rev > 0 and total_mc > 0:
            ps = total_mc / total_rev
            rf = bond_monthly.asof(d) if not bond_monthly.empty else np.nan
            psy = (1 / ps) - rf if pd.notna(rf) else np.nan
            new_rows.append({"date": d, "ps": ps, "rf": rf, "psy": psy})

    new_ps = pd.DataFrame(new_rows).set_index("date")

    if not old_ps.empty:
        combined = pd.concat([old_ps, new_ps])
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
    else:
        combined = new_ps.sort_index()

    # 若旧文件没有 psy 列（首次升级），补算
    if "psy" not in combined.columns and "ps" in combined.columns and not bond_monthly.empty:
        combined["rf"]  = bond_monthly.reindex(combined.index, method="ffill")
        combined["psy"] = (1 / combined["ps"]) - combined["rf"]

    combined.to_csv(ps_path, encoding="utf-8-sig")
    current_ps  = combined["ps"].iloc[-1]
    current_psy = combined["psy"].iloc[-1] if "psy" in combined.columns else np.nan
    ps_pct  = (combined["ps"]  < current_ps).mean()
    psy_pct = (combined["psy"] < current_psy).mean() if "psy" in combined.columns else np.nan
    print(f"   [OK] HSTECH PS {action}完成！共{len(combined)}条")
    print(f"        PS  = {current_ps:.2f}x  (历史{ps_pct*100:.0f}%分位)")
    if pd.notna(current_psy):
        print(f"        PSY = {current_psy:.2%}  (历史{psy_pct*100:.0f}%分位，无风险利率={combined['rf'].iloc[-1]:.2%})")

    _freshness_results.append(
        check_date_freshness(
            label="HSTECH-PS",
            path=ps_path,
            date_col=None,  # index 就是日期，直接按文件 mtime 校验更省事
            max_staleness_days=35,  # 月度数据，放宽阈值
        )
    )


# ── 增量合并保存 ───────────────────────────────────────────────────────────────

def process_incremental(new_pe_df, new_bond_df, code, name, currency, bond_code):
    file_path = f"./data/erp_{code}.csv"

    new_pe_df = new_pe_df.copy()
    new_pe_df['Date'] = pd.to_datetime(new_pe_df['Date'])
    new_bond_df = new_bond_df.copy()
    new_bond_df['Date'] = pd.to_datetime(new_bond_df['Date'])

    if os.path.exists(file_path):
        old_data = pd.read_csv(file_path)
        old_data['Date'] = pd.to_datetime(old_data['Date'])
        old_data = old_data.set_index('Date')
        action = "增量更新"
    else:
        old_data = pd.DataFrame(
            columns=['Bond_Yield_10Y', 'PE', 'IndexCode', 'IndexName', 'Currency', 'BondCode']
        )
        old_data.index = pd.DatetimeIndex([], name='Date')
        action = "全新创建"

    # ★ 修复：国债和PE分别按各自日期只更新对应列，不再整行替换
    # ——避免国债收益率某天被数据源修订时，误将该天已抓到的PE冲成NaN。
    bond_s = new_bond_df.set_index('Date')['Bond_Yield_10Y']
    pe_s = new_pe_df.set_index('Date')['PE']

    all_dates = old_data.index.union(bond_s.index).union(pe_s.index)
    combined = old_data.reindex(all_dates).sort_index()
    combined.loc[bond_s.index, 'Bond_Yield_10Y'] = bond_s.values
    combined.loc[pe_s.index, 'PE'] = pe_s.values

    combined['IndexCode'] = code
    combined['IndexName'] = name
    combined['Currency'] = currency
    combined['BondCode'] = bond_code

    combined['PE'] = combined['PE'].ffill()

    # ★ 修复：对国债收益率做 ffill，防止偶发缺失日导致 ERP 为空
    combined['Bond_Yield_10Y'] = combined['Bond_Yield_10Y'].ffill()

    combined['ERP'] = (1 / combined['PE']) - combined['Bond_Yield_10Y']

    combined = combined.reset_index()
    combined.to_csv(file_path, index=False, encoding='utf-8-sig')
    valid_now = combined['ERP'].notna().sum()
    print(f"   ✅ {name} {action}！总记录: {len(combined)} 天，有效ERP: {valid_now} 天")

    # ── 新鲜度校验 1：最新一行日期是否够新 ──────────────────────────────────
    _freshness_results.append(
        check_date_freshness(
            label=f"ERP-{code}",
            path=file_path,
            date_col="Date",
            max_staleness_days=3,  # 日频数据，覆盖单个假日
        )
    )

    # ── 新鲜度校验 2：今天的 PE 是否和昨天完全相同（怀疑数据源返回旧缓存）──
    valid_pe = combined[['Date', 'PE']].dropna().sort_values('Date')
    if len(valid_pe) >= 2:
        today_row = {"date": str(valid_pe.iloc[-1]['Date']), "PE": float(valid_pe.iloc[-1]['PE'])}
        yesterday_row = {"date": str(valid_pe.iloc[-2]['Date']), "PE": float(valid_pe.iloc[-2]['PE'])}

        streak_state = _load_json(_PE_STREAK_STATE_PATH)
        code_state = streak_state.get(code, {})

        pe_result, new_streak = check_value_streak(
            label=f"PE数值-{code}",
            current=today_row,
            previous=yesterday_row,
            watch_fields=["PE"],
            streak_state=code_state,
            streak_threshold=3,
            advance_gate_field="date",
        )
        streak_state[code] = new_streak
        _save_json(_PE_STREAK_STATE_PATH, streak_state)
        _freshness_results.append(pe_result)

# ── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    global _freshness_results
    _freshness_results = []

    os.makedirs('./data', exist_ok=True)

    if QQQ_PE_TODAY is None:
        print("⚠️  警告: QQQ_PE_TODAY 未填写，今日 QQQ ERP 将不会更新！")
        print("    请访问 https://www.gurufocus.com/economic_indicators/6778/nasdaq-100-pe-ratio")
        print("    查询今日值后填入脚本顶部的 QQQ_PE_TODAY 再运行。\n")

        qqq_state = _load_json(_QQQ_PE_STATE_PATH)
        days_since = None
        if qqq_state.get("last_fill"):
            days_since = (datetime.now() - datetime.fromisoformat(qqq_state["last_fill"])).days
            if days_since >= 2:
                print(f"❗❗ QQQ_PE_TODAY 已连续 {days_since} 天未填写，请尽快更新，否则 QQQ 的 ERP 会一直卡在旧值！\n")

        _freshness_results.append({
            "label": "QQQ_PE_TODAY",
            "fresh": days_since is not None and days_since < 2,
            "last_date": qqq_state.get("last_fill", "从未成功填写过")[:10] if qqq_state.get("last_fill") else None,
            "staleness_days": days_since,
            "reason": "本次未填写" + (f"，距上次成功填写已 {days_since} 天" if days_since is not None else "，且从未成功填写过"),
            "checked_at": datetime.now().isoformat(),
        })
    else:
        print(f"✅ QQQ 今日 PE: {QQQ_PE_TODAY}（来源: GuruFocus TTM）\n")
        _save_json(_QQQ_PE_STATE_PATH, {"last_fill": datetime.now().isoformat(), "value": QQQ_PE_TODAY})
        _freshness_results.append({
            "label": "QQQ_PE_TODAY",
            "fresh": True,
            "last_date": datetime.now().strftime("%Y-%m-%d"),
            "staleness_days": 0,
            "reason": "",
            "checked_at": datetime.now().isoformat(),
        })

    print("ℹ️  恒生科技已改用 PS/PSY 口径，不再依赖 HS_TECH_PE_TODAY。\n")

    end_date_str   = datetime.now().strftime("%Y%m%d")
    start_date_str = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
    fred_start     = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")

    print("=" * 60)
    print(f"增量更新 [{start_date_str} -> {end_date_str}]")

    # 1. 国债增量
    print("\n--- 1. 获取国债增量 ---")
    bonds = {}
    try:
        bonds['CN10Y'] = fetch_cn_bond_incremental(start_date_str, end_date_str)
        print(f"   ✓ 中国国债: {len(bonds['CN10Y'])} 条")
    except Exception as e:
        print(f"   ❌ 中国国债失败: {e}")

    for bond_code, series_id in BOND_CONFIG.items():
        if bond_code == 'CN10Y':
            continue
        try:
            bonds[bond_code] = fetch_fred_bond_incremental(series_id, fred_start)
            print(f"   ✓ {bond_code}: {len(bonds[bond_code])} 条")
        except Exception as e:
            print(f"   ❌ {bond_code} 失败: {e}")

    # 2. 今日 PE
    print("\n--- 2. 获取今日 PE ---")
    try:
        pe_today_dict = fetch_worldpe_today()
        for sym in ['SPY', 'EWQ', 'EWG', 'EWJ', 'EEM']:
            if sym in pe_today_dict:
                print(f"   ✓ {sym}: {pe_today_dict[sym]}")
        if QQQ_PE_TODAY is not None:
            print(f"   ✓ QQQ: {QQQ_PE_TODAY} (手动填入, GuruFocus TTM)")
    except Exception as e:
        print(f"   ❌ worldperatio 失败: {e}")
        pe_today_dict = {}

    # 3. 更新各指数
    print("\n--- 3. 更新各指数 ERP ---")
    today = pd.Timestamp(datetime.now().date())

    for code, name, currency, bond_code, pe_source in BOND_YIELD_CONFIG:
        if bond_code not in bonds:
            print(f"   ⚠️ [{code}] 国债 {bond_code} 未获取，跳过")
            continue
        try:
            if pe_source == 'csindex':
                pe_df = ak.stock_zh_index_hist_csindex(
                    symbol=code,
                    start_date=start_date_str,
                    end_date=end_date_str
                )[['日期', '滚动市盈率']]
                pe_df.columns = ['Date', 'PE']
                time.sleep(1)

            elif pe_source == 'worldpe':
                if code not in pe_today_dict:
                    raise ValueError(f"worldperatio 未返回 {code}")
                pe_df = pd.DataFrame({'Date': [today], 'PE': [pe_today_dict[code]]})

            elif pe_source == 'manual':
                if code == 'QQQ':
                    if QQQ_PE_TODAY is None:
                        print(f"   ⚠️ [{code}] QQQ_PE_TODAY 未填写，跳过今日更新")
                        continue
                    pe_df = pd.DataFrame({'Date': [today], 'PE': [float(QQQ_PE_TODAY)]})
                elif code == 'HSTECH':
                    # 【2026-08-04】HSTECH 已改用 PS/PSY 口径，PE 不再维护，直接跳过
                    continue
                else:
                    raise ValueError(f"未知 manual 指数: {code}")

            else:
                raise ValueError(f"未知 pe_source: {pe_source}")

            process_incremental(pe_df, bonds[bond_code], code, name, currency, bond_code)

        except Exception as e:
            print(f"   ❌ [{code}] {name} 失败: {e}")

    # 4. 更新 HSTECH PS/PSY
    # update_hstech_ps 现在自己按 calc_start 拉国债，不再依赖这里传入的30天数据
    # 仍传入 cn10y_series 作为降级备用
    print("\n--- 4. 更新 HSTECH PS/PSY ---")
    try:
        update_hstech_ps(cn10y_series=bonds.get("CN10Y"))
    except Exception as e:
        print(f"   ❌ HSTECH PS 更新失败: {e}")

    print("\n" + "=" * 60)
    print("增量同步完成。")

    print_freshness_summary(_freshness_results)
    write_freshness_report(_freshness_results)

if __name__ == "__main__":
    main()
