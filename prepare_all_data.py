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
from fetch.freshness import check_date_freshness, print_freshness_summary, write_freshness_report

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


# ═════════════════════════════════════════════════════════════════════
#  ETF 价格序列
# ══════════════════════════════════════════════════════════════════════

ETF_PRICE_PATH = "./data/etf_price.csv"
_ETF_PRICE_CACHE = {}

# 【2026-08-06】simple_etf_metrics.py 增量拉取优化新增的两个历史缓存文件，
# 结构和 etf_price.csv 一样（date为索引的宽表），一并纳入下面的新鲜度校验。
ETF_NAV_PATH = "./data/etf_nav.csv"
INDEX_PCT_PATH = "./data/index_pct.csv"


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


# ═════════════════════════════════════════════════════════════════════
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

    # 【2026-08-06 修复】原来这里还有 ("fetch/fetch_ps.py", "HSTECH PS/PSY 数据")，
    # 排在 fetch_bond_yield_incremental.py 后面。但 fetch_bond_yield_incremental.py
    # 的 main() 内部已经会调用 update_hstech_ps()（增量、自带国债兜底、有ffill）
    # 更新同一个 data/ps_HSTECH.csv；紧接着再跑 fetch_ps.py 会把这个结果整个
    # 用"从2020年全量重算"的旧逻辑覆盖掉——増量版本白跑了，且被更弱的全量版覆盖。
    # 现在移除这一项，PS/PSY 数据统一由 update_hstech_ps() 负责。
    # fetch/fetch_ps.py 文件本身保留，作为手动全量重算工具（怀疑增量结果累积误差时用）。
    scripts = []
    if os.getenv("SKIP_ETF_METRICS", "false").lower() != "true":
        scripts.append(("fetch/simple_etf_metrics.py", "ETF 指标数据"))
    else:
        print("\n   ⏭️ 本地已完整更新ETF数据：仅跳过 AkShare ETF 抓取，继续更新其他数据和报告")

    scripts.extend([
        ("fetch/fetch_bond_yield_incremental.py", "国债 PE 增量数据（含 HSTECH PS/PSY）"),
        ("analysis/dividend_yield_analysis.py", "股息率数据"),  # __main__ 入口在这个文件
    ])

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
                    if script == "fetch/simple_etf_metrics.py":
                        print("   ❗ 核心数据源失败，终止流程")
                        sys.exit(1)
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

    # 6. 汇总数据新鲜度校验（Shiller / ETF价格 之前一直没人校验，这里补上；
    #    国债PE/PS/股息率的校验各自在对应脚本里已经做完并写过 data/freshness_report.json，
    #    这里的 write_freshness_report 会按 label 和它们合并，不会互相覆盖）
    print("\n6️⃣  汇总数据新鲜度校验...")
    try:
        extra_checks = [
            check_date_freshness(
                label="Shiller-CAPE",
                path=SHILLER_PATH,
                date_col=None,  # xls 没有统一日期列，退化为文件 mtime 校验
                max_staleness_days=100,  # 季度更新的数据源，放宽阈值
            ),
            check_date_freshness(
                label="ETF价格序列",
                path=ETF_PRICE_PATH,
                date_col=None,  # 日期是index不是普通列，同样退化为mtime校验
                max_staleness_days=3,
            ),
            check_date_freshness(
                label="ETF净值序列",
                path=ETF_NAV_PATH,
                date_col=None,  # 日期是index不是普通列，退化为mtime校验
                max_staleness_days=3,
            ),
            check_date_freshness(
                label="基准指数涨跌幅序列",
                path=INDEX_PCT_PATH,
                date_col=None,  # 日期是index不是普通列，退化为mtime校验
                max_staleness_days=3,
            ),
        ]
        print_freshness_summary(extra_checks)
        write_freshness_report(extra_checks)
    except Exception as e:
        print(f"   ⚠️  新鲜度汇总校验异常: {e}")

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
