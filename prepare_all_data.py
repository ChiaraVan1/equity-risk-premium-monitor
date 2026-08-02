"""
prepare_all_data.py
准备所有数据：调度数据获取脚本、加载本地数据、抓取外部数据
在 erp_position.py 运行前调用，确保所有分析所需的数据都已就绪
"""
import os
import sys
import subprocess
import time
from datetime import datetime

import pandas as pd
import numpy as np

from config_loader import HOLDING_CATEGORY, FUNDAMENTAL_KEYWORDS, INDICES_LIST
from analysis.etf_quality import load_etf_metrics
from fetch.dividend_yield_fetch import ensure_dividend_data_fresh

# ══════════════════════════════════════════════════════════════════════
#  Shiller CAPE 长期回报锚模块
# ══════════════════════════════════════════════════════════════════════

SHILLER_PATH = os.getenv("SHILLER_PATH", "./data/ie_data.xls")

_CAPE_BINS   = [0,  10,  15,  20,  25,  30,  35,  40,  999]
_CAPE_LABELS = ['<10', '10-15', '15-20', '20-25', '25-30', '30-35', '35-40', '>40']

_shiller_cache = {}


def load_shiller():
    """读取并缓存Shiller CAPE数据，按CAPE区间分组预算历史回报统计（仅SPY用）。"""
    if _shiller_cache:
        return _shiller_cache["grouped"], _shiller_cache["valid"], _shiller_cache["cape_now"]

    if not os.path.exists(SHILLER_PATH):
        return None, None, None

    df = pd.read_excel(SHILLER_PATH, engine="xlrd", sheet_name="Data",
                       header=7, skiprows=[8])
    df.columns = df.columns.str.strip()
    df = df.rename(columns={
        "CAPE":      "cape",
        "Returns.2": "excess_return_10y",
    })
    df = df[pd.to_numeric(df["Date"], errors="coerce").notna()].copy()

    valid = df[["cape", "excess_return_10y"]].dropna().copy()
    valid["cape_bin"] = pd.cut(valid["cape"], bins=_CAPE_BINS, labels=_CAPE_LABELS)

    grouped = valid.groupby("cape_bin", observed=True)["excess_return_10y"].agg(
        count="count",
        mean="mean",
        p10=lambda x: x.quantile(0.10),
        p25=lambda x: x.quantile(0.25),
        p50=lambda x: x.quantile(0.50),
        p75=lambda x: x.quantile(0.75),
        p90=lambda x: x.quantile(0.90),
    )

    cape_now = df["cape"].dropna().iloc[-1]

    _shiller_cache["grouped"]  = grouped
    _shiller_cache["valid"]    = valid
    _shiller_cache["cape_now"] = cape_now
    return grouped, valid, cape_now


# ══════════════════════════════════════════════════════════════════════
#  HSTECH PS / PSY 模块
# ══════════════════════════════════════════════════════════════════════

def load_ps_data():
    """读取HSTECH专用PS/PSY数据（港股科技盈利波动大，PE失真，改用PS口径）。"""
    ps_path = "./data/ps_HSTECH.csv"
    if not os.path.exists(ps_path):
        return None
    df = pd.read_csv(ps_path, index_col=0, parse_dates=True)
    return df


# ══════════════════════════════════════════════════════════════════════
#  ETF 价格序列
# ══════════════════════════════════════════════════════════════════════

ETF_PRICE_PATH = "./data/etf_price.csv"
_ETF_PRICE_CACHE = {}


def load_etf_price_series(erp_code: str):
    """读取标的价格序列（全局缓存，避免重复读CSV），无数据返回None。"""
    global _ETF_PRICE_CACHE
    if "df" not in _ETF_PRICE_CACHE:
        if not os.path.exists(ETF_PRICE_PATH):
            return None
        try:
            df = pd.read_csv(ETF_PRICE_PATH, index_col=0, parse_dates=True)
            _ETF_PRICE_CACHE["df"] = df
        except Exception:
            return None

    df = _ETF_PRICE_CACHE["df"]
    if erp_code not in df.columns:
        return None
    return df[erp_code].dropna().sort_index()


# ══════════════════════════════════════════════════════════════════════
#  东方财富快讯（基本面预警数据源）
# ══════════════════════════════════════════════════════════════════════

import akshare as ak

_EM_NEWS_CACHE = {"df": None, "fetched_at": 0.0}
_EM_NEWS_CACHE_TTL_SECONDS = 30 * 60


def fetch_em_news_df():
    """拉取东方财富全球财经快讯（ak.stock_info_global_em），30分钟内复用缓存。"""
    now = time.time()
    if _EM_NEWS_CACHE["df"] is not None and (now - _EM_NEWS_CACHE["fetched_at"]) < _EM_NEWS_CACHE_TTL_SECONDS:
        return _EM_NEWS_CACHE["df"]

    try:
        df = ak.stock_info_global_em()
        _EM_NEWS_CACHE["df"] = df
        _EM_NEWS_CACHE["fetched_at"] = now
        return df
    except Exception as e:
        print(f"⚠️  东方财富快讯拉取失败: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════
#  主准备函数
# ══════════════════════════════════════════════════════════════════════

def prepare_all_data():
    """
    准备所有分析所需的数据。
    返回: (shiller_data, ps_data, etf_metrics_df, news_df)
    """
    print("\n" + "=" * 60)
    print("📊 数据准备阶段")
    print("=" * 60)

    # ⚙️  第1步：运行所有数据生成脚本
    print("\n⚙️  第1步：运行所有数据生成脚本...")

    scripts = [
        ("fetch/simple_etf_metrics.py", "ETF 指标数据"),
        ("fetch/fetch_bond_yield_incremental.py", "国债 PE 增量数据"),
        ("fetch/fetch_ps.py", "HSTECH PS/PSY 数据"),
        ("analysis/dividend_yield_analysis.py", "股息率数据"),  # __main__ 入口在这个文件
    ]

    for script, desc in scripts:
        if os.path.exists(script):
            print(f"\n   ▶️  {desc}: 运行 {script}...")
            try:
                result = subprocess.run(
                    [sys.executable, script],
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                if result.returncode == 0:
                    print(f"   ✓ {desc} 完成")
                else:
                    print(f"   ⚠️  {script} 返回码 {result.returncode}")
                    if result.stderr:
                        print(f"      {result.stderr[:200]}")
            except subprocess.TimeoutExpired:
                print(f"   ⚠️  {script} 超时（>5分钟）")
            except Exception as e:
                print(f"   ⚠️  {script} 异常: {e}")
        else:
            print(f"   ⚠️  {script} 不存在，跳过")

    # 📦 第2步：加载已准备好的数据
    print("\n📦 第2步：加载数据...")

    # 1. 加载 Shiller CAPE 数据
    print("\n1️⃣  加载 Shiller CAPE 数据...")
    try:
        shiller_grouped, shiller_valid, shiller_cape_now = load_shiller()
        print("   ✓ Shiller CAPE 加载完成")
    except Exception as e:
        print(f"   ⚠️  Shiller CAPE 加载失败: {e}")
        shiller_grouped, shiller_valid, shiller_cape_now = None, None, None

    # 2. 加载 PS/PSY 数据
    print("\n2️⃣  加载 HSTECH PS/PSY 数据...")
    try:
        ps_data = load_ps_data()
        if ps_data is not None:
            print(f"   ✓ PS/PSY 数据加载完成（{len(ps_data)} 行）")
        else:
            print("   ⚠️  PS/PSY 数据文件不存在")
    except Exception as e:
        print(f"   ⚠️  PS/PSY 加载失败: {e}")
        ps_data = None

    # 3. 加载 ETF 指标
    print("\n3️⃣  加载 ETF 执行质量指标...")
    try:
        etf_df = load_etf_metrics()
        print("   ✓ ETF 指标加载完成")
    except Exception as e:
        print(f"   ⚠️  ETF 指标加载失败: {e}")
        etf_df = None

    # 4. 确保股息率数据新鲜
    print("\n4️⃣  检查股息率数据新鲜度...")
    try:
        ensure_dividend_data_fresh()
        print("   ✓ 股息率数据检查完成")
    except Exception as e:
        print(f"   ⚠️  股息率数据检查失败: {e}")

    # 5. 拉取东方财富快讯（基本面预警）
    print("\n5️⃣  拉取东方财富快讯...")
    try:
        news_df = fetch_em_news_df()
        if news_df is not None:
            print(f"   ✓ 快讯加载完成（{len(news_df)} 条）")
        else:
            print("   ⚠️  快讯加载失败")
    except Exception as e:
        print(f"   ⚠️  快讯拉取异常: {e}")
        news_df = None

    print("\n" + "=" * 60)
    print("✅ 数据准备完成\n")

    return {
        "shiller_grouped": shiller_grouped,
        "shiller_valid": shiller_valid,
        "shiller_cape_now": shiller_cape_now,
        "ps_data": ps_data,
        "etf_df": etf_df,
        "news_df": news_df,
    }
