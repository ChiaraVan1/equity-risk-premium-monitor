#!/usr/bin/env python3
"""
雪球热榜人气信号模块
=====================================================
核心规则（已与用户确认）：

  热榜排名上升 + 该行业 ERP 处于极度低估区(P75分位以上) → 加仓确认
  热榜排名上升 + 该行业 ERP 处于高估区(P25分位以下)     → 减仓/规避确认
  涨跌方向本身不参与触发判断，只作为参考信息展示。

本模块只产出【展示层的确认/无信号】结果，不直接触发仓位改动。
调用方（analyze_and_suggest）自行决定是否据此手动调整仓位。
=====================================================
"""

import json
import csv
import io
from pathlib import Path
from collections import defaultdict

import requests

INDUSTRY_MAP_PATH = Path(__file__).parent / "industry_map.json"

# xueqiu_hot 仓库里 master.csv 的 raw 地址。跨仓库读取，不依赖本地文件系统。
MASTER_CSV_URL = (
    "https://raw.githubusercontent.com/ChiaraVan1/xueqiu_hot/main/"
    "xueqiu_data/xueqiu_hot_master.csv"
)

# 排名趋势判断所需的最近天数
TREND_LOOKBACK_DAYS = 3


_industry_map_cache = None
_hot_rows_cache = {"rows": None, "url": None}


def load_industry_map() -> dict:
    """加载 行业标签 -> ETF代码 映射表，跳过 _comment 字段。进程内缓存一次即可。"""
    global _industry_map_cache
    if _industry_map_cache is not None:
        return _industry_map_cache
    with open(INDUSTRY_MAP_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    _industry_map_cache = {k: v for k, v in raw.items() if not k.startswith("_")}
    return _industry_map_cache


def load_hot_rows(master_csv_url: str = MASTER_CSV_URL, timeout: int = 15,
                   use_cache: bool = True) -> list[dict]:
    """
    直接从 xueqiu_hot 仓库的 raw.githubusercontent.com 地址拉取 master.csv 并解析。
    请求失败（网络问题/文件不存在/仓库改了路径）时返回空列表，
    调用方（compute_popularity_confirmation）已对空数据做了"数据不足"降级处理，
    不会因此中断整个报告生成流程。

    use_cache=True 时，同一进程内（比如一次跑21个标的的报告）只请求一次网络，
    避免对同一份 master.csv 发起21次重复请求。
    """
    if use_cache and _hot_rows_cache["rows"] is not None and _hot_rows_cache["url"] == master_csv_url:
        return _hot_rows_cache["rows"]

    try:
        resp = requests.get(master_csv_url, timeout=timeout)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"⚠️ 拉取热榜数据失败（{master_csv_url}）：{e}")
        return []

    # utf-8-sig 处理可能存在的 BOM 头，与抓取脚本写入时的编码一致
    text = resp.content.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))

    if use_cache:
        _hot_rows_cache["rows"] = rows
        _hot_rows_cache["url"] = master_csv_url

    return rows


def _rows_by_etf_code(rows: list[dict], industry_map: dict) -> dict:
    """
    按 ETF代码 分组，聚合每个代码下所有映射到它的个股行情记录，
    并按日期排序，供后续计算“排名是否持续上升”。
    """
    grouped = defaultdict(list)
    for r in rows:
        etf_code = industry_map.get(r.get("行业", ""))
        if not etf_code:
            continue
        try:
            rank = int(r["排名"])
        except (ValueError, KeyError):
            continue
        grouped[etf_code].append({
            "日期": r["日期"],
            "排名": rank,
            "涨跌幅": float(r["涨跌幅(%)"]) if r.get("涨跌幅(%)") not in (None, "") else None,
            "成交额": float(r["成交额(亿)"]) if r.get("成交额(亿)") not in (None, "") else None,
            "股票代码": r.get("股票代码", ""),
            "股票名称": r.get("股票名称", ""),
        })
    for etf_code in grouped:
        grouped[etf_code].sort(key=lambda x: x["日期"])
    return grouped


def compute_rank_trend(etf_code: str, rows: list[dict], industry_map: dict,
                        lookback_days: int = TREND_LOOKBACK_DAYS) -> dict:
    """
    计算某 ETF 代码对应行业，最近 lookback_days 天内，
    映射到该行业的个股是否呈现“排名持续上升”（热度上升）趋势。

    判断方法：取最近 lookback_days 个不同交易日中，该行业出现过的
    最好排名（数字越小越靠前），比较首日与末日的最好排名变化。
    排名数字变小 → 视为“排名上升”（热度上升）。

    返回：
      {
        "has_data": bool,
        "rank_rising": bool | None,   # None 表示数据不足以判断
        "best_rank_start": int | None,
        "best_rank_end": int | None,
        "avg_pct_change": float | None,  # 区间内平均涨跌幅，仅供参考展示，不参与判断
        "sample_days": int,
      }
    """
    grouped = _rows_by_etf_code(rows, industry_map)
    series = grouped.get(etf_code, [])

    if not series:
        return {
            "has_data": False, "rank_rising": None,
            "best_rank_start": None, "best_rank_end": None,
            "avg_pct_change": None, "sample_days": 0,
        }

    dates = sorted(set(r["日期"] for r in series))
    recent_dates = dates[-lookback_days:]

    if len(recent_dates) < 2:
        return {
            "has_data": True, "rank_rising": None,
            "best_rank_start": None, "best_rank_end": None,
            "avg_pct_change": None, "sample_days": len(recent_dates),
        }

    def best_rank_on(date):
        day_rows = [r for r in series if r["日期"] == date]
        return min(r["排名"] for r in day_rows) if day_rows else None

    start_rank = best_rank_on(recent_dates[0])
    end_rank   = best_rank_on(recent_dates[-1])

    pct_values = [r["涨跌幅"] for r in series
                  if r["日期"] in recent_dates and r["涨跌幅"] is not None]
    avg_pct = sum(pct_values) / len(pct_values) if pct_values else None

    rank_rising = None
    if start_rank is not None and end_rank is not None:
        rank_rising = end_rank < start_rank  # 数字变小 = 排名上升

    return {
        "has_data": True,
        "rank_rising": rank_rising,
        "best_rank_start": start_rank,
        "best_rank_end": end_rank,
        "avg_pct_change": avg_pct,
        "sample_days": len(recent_dates),
    }


def compute_popularity_confirmation(etf_code: str, erp_percentile: float,
                                     rows: list[dict] = None,
                                     industry_map: dict = None) -> dict:
    """
    核心函数：结合“热榜排名趋势”与“ERP历史分位”，输出确认信号。

    erp_percentile: 该标的当前ERP在历史序列中的分位（0~1，越高越便宜，
                     与 analyze_and_suggest 中 erp_percentile 定义一致）。

    返回：
      {
        "signal": "加仓确认" | "减仓确认" | "无信号" | "数据不足",
        "icon": "🟢" | "🔴" | "─",
        "detail": str,  # 用于报告展示的一句话说明
      }
    """
    if rows is None:
        rows = load_hot_rows()
    if industry_map is None:
        industry_map = load_industry_map()

    trend = compute_rank_trend(etf_code, rows, industry_map)

    if not trend["has_data"] or trend["rank_rising"] is None:
        return {
            "signal": "数据不足", "icon": "─",
            "detail": "热榜数据不足（样本天数过少或该行业近期未上榜），跳过人气信号判断。",
        }

    if not trend["rank_rising"]:
        return {
            "signal": "无信号", "icon": "─",
            "detail": f"热榜排名未见持续上升（{trend['sample_days']}日内最好排名"
                       f" {trend['best_rank_start']}→{trend['best_rank_end']}），"
                       "关注度未提升，暂不构成加仓/减仓确认。",
        }

    # 排名确实在上升，再结合 ERP 分位判断方向
    pct_note = ""
    if trend["avg_pct_change"] is not None:
        pct_note = f"，期间平均涨跌幅 {trend['avg_pct_change']:+.2f}%（仅供参考，不参与判断）"

    if erp_percentile >= 0.75:
        return {
            "signal": "加仓确认", "icon": "🟢",
            "detail": f"热榜排名持续上升（{trend['best_rank_start']}→{trend['best_rank_end']}）"
                       f"，且当前ERP处于历史{erp_percentile:.0%}分位（显著/极度低估区）"
                       f"{pct_note} → 资金开始关注低估机会，加仓确认。",
        }
    elif erp_percentile <= 0.25:
        return {
            "signal": "减仓确认", "icon": "🔴",
            "detail": f"热榜排名持续上升（{trend['best_rank_start']}→{trend['best_rank_end']}）"
                       f"，但当前ERP仅处于历史{erp_percentile:.0%}分位（已进入高估区）"
                       f"{pct_note} → 资金在追逐已经偏贵的标的，减仓/规避确认。",
        }
    else:
        return {
            "signal": "无信号", "icon": "─",
            "detail": f"热榜排名持续上升，但当前ERP处于历史{erp_percentile:.0%}分位（合理区间），"
                       "估值位置不够极端，不构成加仓或减仓确认。",
        }


def build_popularity_block(etf_code: str, erp_percentile: float,
                            rows: list[dict] = None,
                            industry_map: dict = None,
                            precomputed: dict = None) -> str:
    """
    生成可直接插入报告的 Markdown 区块。
    与 build_exit_signal_block 等函数风格保持一致，供 analyze_and_suggest 调用。

    precomputed: 如果调用方（如仪表盘）已经算过一次 compute_popularity_confirmation，
                 可以直接传入结果，避免重复拉取热榜数据、重复计算。
    """
    result = precomputed if precomputed is not None else \
        compute_popularity_confirmation(etf_code, erp_percentile, rows, industry_map)

    return f"""
---
### 热榜人气信号（辅助确认，非独立交易依据）

> 规则：热榜排名持续上升 + ERP≥P75(低估) → 加仓确认；排名持续上升 + ERP≤P25(高估) → 减仓确认；其余情况均为「无信号」。
> 涨跌幅仅作参考展示，不参与判断。

{result['icon']} **{result['signal']}**

{result['detail']}
"""


if __name__ == "__main__":
    # 简单自测：加载映射表并跑一次半导体/950125的判断（假设ERP分位=0.85，模拟低估场景）
    industry_map = load_industry_map()
    rows = load_hot_rows()
    print(f"已加载映射表，共 {len(industry_map)} 个行业标签")
    print(f"已加载热榜记录 {len(rows)} 条")

    test_result = compute_popularity_confirmation("950125", erp_percentile=0.85,
                                                    rows=rows, industry_map=industry_map)
    print("\n测试结果（950125，模拟ERP分位=0.85）：")
    print(test_result)
