"""
fetch/freshness.py
──────────────────────────────────────────────────────────────────────────────
通用数据新鲜度校验工具。两类校验器对应仓库里已经出现过的两种模式：

1. check_date_freshness()  —— "日期口径"：文件里最新一行的日期，距今天（或最近
   一个交易日）超过多少天算过期。适用于有明确日期列的时间序列数据（国债/PE/
   股息率/ETF价格），以及没有日期列、退化成看文件 mtime 的场景（Shiller xls）。

2. check_value_streak()    —— "数值不变口径"：连续 N 次抓取，某些关键字段的值
   完全没变化，大概率是数据源返回了缓存/旧数据而不是真的没变。从
   simple_etf_metrics.py 里原有逻辑抽出来，改成可复用的通用版本。

两者都不抛异常、不中断主流程——只返回结构化结果，由调用方决定要不要报警/中断。
校验结果统一用 write_freshness_report() 落盘到 data/freshness_report.json，
每次运行覆盖式追加当前批次的结果（保留最近一次每个数据源的状态）。
──────────────────────────────────────────────────────────────────────────────
"""

import os
import json
import numpy as np
import pandas as pd
from datetime import datetime

FRESHNESS_REPORT_PATH = "./data/freshness_report.json"


# ══════════════════════════════════════════════════════════════════════
# 1. 日期口径校验
# ══════════════════════════════════════════════════════════════════════

def check_date_freshness(
    label: str,
    path: str,
    date_col: str | None = None,
    max_staleness_days: int = 3,
    skip_weekends: bool = True,
) -> dict:
    """
    校验一个数据文件的"最新日期"是否新鲜。

    参数：
        label:               数据源名称（用于日志/报告，如 "国债PE-erp_000300"）
        path:                文件路径。支持 .csv；非 .csv（如 .xls）时退化为校验 mtime
        date_col:            日期列名。为 None 时（或文件非csv）改用文件 mtime 校验
        max_staleness_days:  允许的最大过期自然日数（已经把周末/节假日缓冲考虑进阈值里，
                              不用再单独调用方处理"今天是周几"）
        skip_weekends:       True 时，若当前是周一，允许的过期天数额外+2（覆盖周末休市）

    返回：
        {label, fresh, last_date/mtime, staleness_days, reason, checked_at}
        文件不存在也返回 fresh=False，而不是抛异常。
    """
    now = datetime.now()
    threshold = max_staleness_days
    if skip_weekends and now.weekday() == 0:  # 周一
        threshold += 2

    result = {
        "label": label,
        "path": path,
        "fresh": False,
        "last_date": None,
        "staleness_days": None,
        "reason": "",
        "checked_at": now.isoformat(),
    }

    if not os.path.exists(path):
        result["reason"] = "文件不存在"
        return result

    try:
        if date_col and path.endswith(".csv"):
            df = pd.read_csv(path, usecols=[date_col])
            dates = pd.to_datetime(df[date_col], errors="coerce").dropna()
            if len(dates) == 0:
                result["reason"] = f"{date_col} 列无有效日期"
                return result
            last_date = dates.max()
            staleness = (now.normalize() - last_date.normalize()).days
            result["last_date"] = last_date.strftime("%Y-%m-%d")
        else:
            # 无日期列（如 .xls）或未指定 date_col：退化成看文件修改时间
            mtime = datetime.fromtimestamp(os.path.getmtime(path))
            staleness = (now - mtime).days
            result["last_date"] = mtime.strftime("%Y-%m-%d")
            result["reason_prefix"] = "按文件修改时间校验（无日期列）"

        result["staleness_days"] = staleness
        result["fresh"] = staleness <= threshold
        if not result["fresh"]:
            result["reason"] = f"已 {staleness} 天未更新（阈值 {threshold} 天）"
        return result

    except Exception as e:
        result["reason"] = f"校验异常: {e}"
        return result


# ══════════════════════════════════════════════════════════════════════
# 2. 数值不变口径校验（连续N次取值完全相同）
# ══════════════════════════════════════════════════════════════════════

def check_value_streak(
    label: str,
    current: dict,
    previous: dict | None,
    watch_fields: list[str],
    streak_state: dict | None,
    streak_threshold: int = 3,
    advance_gate_field: str | None = None,
) -> tuple[dict, dict]:
    """
    对比"这次抓到的值"和"上一次抓到的值"，若关键字段连续 N 次完全相同则标记预警。

    这是给"单个标的/单次抓取"用的原子版本（simple_etf_metrics.py 那种批量场景，
    调用方自己在循环里对每一行调用一次即可）。也适合国债/PE这种"就一个值"的场景，
    比如今天的PE和昨天完全相同，大概率是数据源返回了旧缓存。

    参数：
        label:               标的/数据源标识
        current:             这次抓到的值，如 {"PE": 24.5, "date": "2026-08-06"}
        previous:            上一次的值（没有就传 None，视为首次运行）
        watch_fields:        要盯的字段名列表
        streak_state:        上次持久化的 streak 计数字典，如 {"PE": 2}；首次传 {} 或 None
        streak_threshold:    连续多少次不变才报警
        advance_gate_field:  可选，若提供（如 "date"），只有该字段相较上次真的推进了
                              （比如进入新交易日）才累加streak；同一天内重复运行不计数

    返回：
        (result, new_streak_state)
        result = {label, stale_flag, stale_note, watch_fields_status}
    """
    streak_state = dict(streak_state or {})
    new_streak = {}
    note_parts = []

    should_advance = True
    if advance_gate_field is not None and previous is not None:
        cur_gate = current.get(advance_gate_field)
        prev_gate = previous.get(advance_gate_field)
        should_advance = cur_gate is not None and cur_gate != prev_gate

    for field in watch_fields:
        prev_streak = int(streak_state.get(field, 0) or 0)
        cur_val = current.get(field)
        prev_val = previous.get(field) if previous else None

        if not should_advance:
            streak = prev_streak
        elif (
            previous is not None
            and cur_val is not None
            and prev_val is not None
            and _values_equal(cur_val, prev_val)
        ):
            streak = prev_streak + 1
        else:
            streak = 0

        new_streak[field] = streak
        if streak >= streak_threshold:
            note_parts.append(f"{field} 已连续 {streak} 次取值未变化")

    result = {
        "label": label,
        "stale_flag": len(note_parts) > 0,
        "stale_note": "；".join(note_parts),
        "checked_at": datetime.now().isoformat(),
    }
    return result, new_streak


def _values_equal(a, b, tol: float = 1e-9) -> bool:
    try:
        return abs(float(a) - float(b)) < tol
    except (TypeError, ValueError):
        return a == b


# ══════════════════════════════════════════════════════════════════════
# 3. 汇总报告落盘
# ══════════════════════════════════════════════════════════════════════

def write_freshness_report(results: list[dict], path: str = FRESHNESS_REPORT_PATH):
    """
    把这批校验结果合并写入 data/freshness_report.json。

    ★ 用"合并"而不是"整体覆盖"：因为 fetch/*.py 是被 prepare_all_data.py 以
    独立子进程方式依次运行的（各自进程互不知道彼此），如果每个脚本自己调用
    这个函数时都整体覆盖文件，后跑的脚本会把先跑脚本写的结果冲掉。
    合并策略：按 label 去重，同 label 用本次结果替换旧的，其余 label 保留。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)

    existing_results = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing_results = json.load(f).get("results", [])
        except Exception:
            existing_results = []

    merged = {r["label"]: r for r in existing_results}
    for r in results:
        merged[r["label"]] = r
    merged_results = list(merged.values())

    payload = {
        "generated_at": datetime.now().isoformat(),
        "results": merged_results,
        "stale_count": sum(
            1 for r in merged_results if r.get("fresh") is False or r.get("stale_flag") is True
        ),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload


def print_freshness_summary(results: list[dict]):
    """在日志里打印一份醒目的汇总表，过期项前面加 ❌，正常项加 ✅。"""
    print("\n" + "=" * 60)
    print("🔍 数据新鲜度汇总")
    print("=" * 60)
    any_stale = False
    for r in results:
        is_bad = (r.get("fresh") is False) or (r.get("stale_flag") is True)
        mark = "❌" if is_bad else "✅"
        if is_bad:
            any_stale = True
        detail = r.get("reason") or r.get("stale_note") or ""
        last = r.get("last_date", "")
        print(f" {mark} {r.get('label', '?')}{f' ({last})' if last else ''} {detail}")
    if any_stale:
        print("\n⚠️ 存在数据新鲜度异常，请检查上方标 ❌ 的数据源，报告结论可能不可靠。")
    print("=" * 60)
