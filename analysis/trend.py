"""
analysis/trend.py
趋势分析模块：ERP斜率信号（自适应分位阈值）、近10月AI趋势解读、HSTECH PSY趋势
"""
import os
import json
import time

import pandas as pd
import numpy as np
import requests

# ══════════════════════════════════════════════════════════════════════
# AI 调用（DashScope / 阿里云百炼，OpenAI 兼容模式）
# ══════════════════════════════════════════════════════════════════════

_API_MAX_RETRIES = 3
_API_RETRY_BASE_DELAY = 5      # 秒，每次重试翻倍：5s, 10s, 20s
_API_CALL_MIN_INTERVAL = 2     # 秒，连续两次AI调用之间的最小间隔
_last_api_call_ts = {"t": 0.0}

_DASHSCOPE_API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"


def _call_dashscope_with_retry(payload, headers):
    """调用阿里云百炼(DashScope, OpenAI兼容模式)接口，429限流退避重试 + 调用间隔控制。"""
    elapsed = time.time() - _last_api_call_ts["t"]
    if elapsed < _API_CALL_MIN_INTERVAL:
        time.sleep(_API_CALL_MIN_INTERVAL - elapsed)

    last_exc = None
    for attempt in range(_API_MAX_RETRIES):
        try:
            resp = requests.post(_DASHSCOPE_API_URL, json=payload, headers=headers, timeout=60)
            _last_api_call_ts["t"] = time.time()

            if resp.status_code == 429:
                wait = _API_RETRY_BASE_DELAY * (2 ** attempt)
                retry_after = resp.headers.get("retry-after")
                if retry_after:
                    try:
                        wait = max(wait, float(retry_after))
                    except ValueError:
                        pass
                if attempt < _API_MAX_RETRIES - 1:
                    time.sleep(wait)
                    continue
                resp.raise_for_status()

            resp.raise_for_status()
            return resp

        except requests.exceptions.HTTPError as e:
            last_exc = e
            if e.response is not None and e.response.status_code == 429 and attempt < _API_MAX_RETRIES - 1:
                continue
            raise
        except requests.exceptions.RequestException as e:
            last_exc = e
            raise

    raise last_exc


def _erp_monthly_trend_ai_prompt(name, code, monthly_rows, quantiles):
    monthly_str = "\n".join(f"{d}: PE={pe:.1f}x, ERP={erp:.2%}" for d, pe, erp in monthly_rows)
    return f"""你是一个说话直白的投资助手，要把"{name}({code})"近10个月的估值数据，讲给完全不懂金融术语的普通人听。

{monthly_str}

历史分位：P90={quantiles['P90']:.2%} P75={quantiles['P75']:.2%} P50={quantiles['P50']:.2%} P25={quantiles['P25']:.2%} P10={quantiles['P10']:.2%}

要求：
1. 先说清楚近10个月整体是"越来越贵""越来越便宜"还是"来回震荡"，再补充最近1-2个月有没有明显变化，两者都要体现，不能只讲最近1-2个月而漏掉整体走势。
2. 尽量少用"ERP""风险补偿""溢价""分位""估值中枢"这类专业词，优先用"贵/便宜""性价比""处在近期偏高/偏低的位置"这种大白话表达；如果大白话说不清楚或容易产生歧义，也可以用专业词汇。
3. ≤30字，不输出免责声明。
4. 严格输出以下JSON，不要输出其他内容：
{{"trend_summary": "≤30字", "direction": "走高"|"走低"|"震荡"}}"""


_QNAIGC_API_URL = "https://api.qnaigc.com/v1/messages"


def _call_qnaigc_with_retry(payload, headers):
    """调用七牛云 Anthropic 兼容接口（qnaigc）。作为 DashScope 的第二级降级：
    dashscope.aliyuncs.com 面向国内网络优化，GitHub Actions runner 跑在海外机房，
    跨境访问这条链路本身延迟高、偶发超时；qnaigc 对海外访问更稳，因此 DashScope
    失败时先尝试切到这里，两个AI源都失败才交给调用方走规则法兜底。
    限流退避/调用间隔逻辑与 _call_dashscope_with_retry 一致，共用同一个
    _last_api_call_ts 节流（不区分数据源，避免两边加起来仍触发限流）。
    """
    elapsed = time.time() - _last_api_call_ts["t"]
    if elapsed < _API_CALL_MIN_INTERVAL:
        time.sleep(_API_CALL_MIN_INTERVAL - elapsed)

    last_exc = None
    for attempt in range(_API_MAX_RETRIES):
        try:
            resp = requests.post(_QNAIGC_API_URL, json=payload, headers=headers, timeout=60)
            _last_api_call_ts["t"] = time.time()

            if resp.status_code == 429:
                wait = _API_RETRY_BASE_DELAY * (2 ** attempt)
                retry_after = resp.headers.get("retry-after")
                if retry_after:
                    try:
                        wait = max(wait, float(retry_after))
                    except ValueError:
                        pass
                if attempt < _API_MAX_RETRIES - 1:
                    time.sleep(wait)
                    continue
                resp.raise_for_status()

            resp.raise_for_status()
            return resp

        except requests.exceptions.HTTPError as e:
            last_exc = e
            if e.response is not None and e.response.status_code == 429 and attempt < _API_MAX_RETRIES - 1:
                continue
            raise
        except requests.exceptions.RequestException as e:
            last_exc = e
            raise

    raise last_exc


def build_monthly_trend_ai_block(name, code, monthly_rows, quantiles):
    """调用AI生成近10个月趋势一句话解读。三级降级：① 阿里云百炼 DashScope
    （国内数据源，优先尝试）→ ② 七牛云 Anthropic 兼容接口 qnaigc（跨境访问更稳，
    DashScope超时/失败时切换）→ ③ 规则法（两个AI源都失败时，返回 (None, False)
    交由调用方兜底，本函数不做规则判断）。
    """
    prompt = _erp_monthly_trend_ai_prompt(name, code, monthly_rows, quantiles)

    # ── 第一级：阿里云百炼 DashScope ──────────────────────────────────
    try:
        payload = {
            "model": "deepseek-v4-pro",
            "max_tokens": 300,
            "enable_thinking": False,
            "messages": [{"role": "user", "content": prompt}]
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.getenv('ALIYUN_API_KEY', '')}",
        }
        resp = _call_dashscope_with_retry(payload, headers)
        data = resp.json()
        raw = data["choices"][0]["message"]["content"].strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        icon = {"走高": "🟢", "走低": "🔴", "震荡": "🟡"}.get(result.get("direction", ""), "🟡")
        return f"趋势方向：{icon} **{result.get('trend_summary','')}**", True
    except Exception as e:
        print(f"⚠️ DashScope AI趋势解读失败（尝试降级到 qnaigc）：{type(e).__name__}: {e}")

    # ── 第二级：七牛云 Anthropic 兼容接口（qnaigc，跨境访问更稳）──────
    try:
        payload = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 300,
            "messages": [{"role": "user", "content": prompt}]
        }
        headers = {
            "Content-Type": "application/json",
            "x-api-key": os.getenv("ANTHROPIC_API_KEY", ""),
            "anthropic-version": "2023-06-01",
        }
        resp = _call_qnaigc_with_retry(payload, headers)
        data = resp.json()
        text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
        raw = "\n".join(text_blocks).strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        icon = {"走高": "🟢", "走低": "🔴", "震荡": "🟡"}.get(result.get("direction", ""), "🟡")
        return f"趋势方向：{icon} **{result.get('trend_summary','')}**", True
    except Exception as e:
        print(f"⚠️ qnaigc AI趋势解读也失败（已降级到规则法）：{type(e).__name__}: {e}")
        return None, False


# ══════════════════════════════════════════════════════════════════════
# 近20日斜率信号（自适应历史分位阈值）
# ══════════════════════════════════════════════════════════════════════

_SLOPE_EXTREME_THRESHOLD = 0.02
_SLOPE_MIN_HISTORY = 60        # 自适应阈值所需的最少历史"20日变化"样本数
_SLOPE_EXTREME_PCTL = 0.90     # |delta| 落在历史90分位之外视为极端
_SLOPE_MODERATE_PCTL = 0.65    # 60%~处视为"快速改善/恶化"


def compute_erp_slope_signal(erp_series: pd.Series) -> dict:
    """近20日ERP变化的斜率信号（🚨🟢🟡🟠⚠️五档）。

    阈值用自适应历史分位而非固定百分比：ERP=1/PE−rf 是倒数关系，高PE标的
    的ERP天然被压缩，固定绝对阈值（比如2个百分点）对低PE标的合适，但对
    高PE标的可能永远触发不了。这里改用"当前20日变化在该标的自己历史
    分布中的分位"，同一套逻辑对所有标的自适应。历史样本不足60条时退回
    固定阈值。

    注意：这里用的是"绝对差值"(delta = 现值-20日前值)，不是相对涨跌幅——
    ERP经常在0附近，除以起始值算相对涨跌幅在ERP接近0时会爆出离谱的数字。
    """
    if len(erp_series) < 21:
        return {"slope_20d": np.nan, "delta_20d": np.nan,
                "signal": "数据不足", "signal_icon": "─", "desc": ""}

    recent = erp_series.dropna().iloc[-21:]
    x = np.arange(len(recent))
    slope = np.polyfit(x, recent.values, 1)[0]
    delta = recent.iloc[-1] - recent.iloc[0]

    all_deltas = erp_series.dropna().diff(20).dropna()

    if len(all_deltas) >= _SLOPE_MIN_HISTORY:
        pos_pctl = (all_deltas < delta).mean()
        neg_pctl = (all_deltas > delta).mean()
        abs_extreme = pos_pctl >= _SLOPE_EXTREME_PCTL
        abs_moderate = pos_pctl >= _SLOPE_MODERATE_PCTL
        neg_extreme = neg_pctl >= _SLOPE_EXTREME_PCTL
        neg_moderate = neg_pctl >= _SLOPE_MODERATE_PCTL
        pctl_note = f"（历史分位 P{pos_pctl*100:.0f}）"
    else:
        abs_extreme = delta >= _SLOPE_EXTREME_THRESHOLD
        abs_moderate = delta >= _SLOPE_EXTREME_THRESHOLD * 0.4
        neg_extreme = delta <= -_SLOPE_EXTREME_THRESHOLD
        neg_moderate = delta <= -_SLOPE_EXTREME_THRESHOLD * 0.4
        pctl_note = "（历史样本不足，用固定阈值）"

    if abs_extreme:
        signal, signal_icon = "恐慌踩踏", "🚨"
        desc = (f"近20日ERP急速飙升 {delta:.2%}（斜率 {slope*100:.3f}%/日）{pctl_note}"
                "— 市场处于恐慌抛售期，PE急速压缩，历史上往往是买点临近的强化信号，但需警惕基本面是否同步恶化。")
    elif abs_moderate:
        signal, signal_icon = "估值快速改善", "🟢"
        desc = (f"近20日ERP持续走高 {delta:.2%}（斜率 {slope*100:.3f}%/日）{pctl_note}"
                "— 估值快速修复，买入窗口正在打开。")
    elif neg_extreme:
        signal, signal_icon = "情绪过热", "⚠️"
        desc = (f"近20日ERP急速坠落 {delta:.2%}（斜率 {slope*100:.3f}%/日）{pctl_note}"
                "— 市场情绪快速升温，估值泡沫化加速，警戒高位。")
    elif neg_moderate:
        signal, signal_icon = "估值快速恶化", "🟠"
        desc = (f"近20日ERP持续走低 {delta:.2%}（斜率 {slope*100:.3f}%/日）{pctl_note}"
                "— 估值向贵的方向漂移，需提高警惕。")
    else:
        signal, signal_icon = "横盘震荡", "🟡"
        desc = (f"近20日ERP变化 {delta:+.2%}（斜率 {slope*100:.3f}%/日）{pctl_note}"
                "— 估值无明显趋势，保持既有仓位。")

    return {"slope_20d": slope, "delta_20d": delta,
            "signal": signal, "signal_icon": signal_icon, "desc": desc}


# ══════════════════════════════════════════════════════════════════════
# 趋势区块（近10月 + 近20日斜率）
# ══════════════════════════════════════════════════════════════════════

def build_trend_block(df, name, erp_series, code, quantiles, ps_df=None):
    """近10月趋势（月度部分为AI解读，失败时规则法兜底）+ 近20日斜率信号。

    ps_df: HSTECH 专用，传入 data/ps_HSTECH.csv 加载后的 DataFrame
           （由 prepare_all_data.load_ps_data() 提供），非 HSTECH 传 None 即可。
    月度AI/polyfit（10月周期）和 compute_erp_slope_signal（20日周期）是两个
    不同时间尺度的独立指标，非重复计算，不合并。
    """
    if code == "HSTECH":
        if ps_df is None or "psy" not in ps_df.columns:
            return ""
        recent_psy = ps_df[ps_df["psy"].notna()][["ps", "psy"]].tail(10)
        if len(recent_psy) < 2:
            return ""

        slope_info = compute_erp_slope_signal(ps_df["psy"].dropna())

        psy_rows = []
        prev_psy = None
        for d, r in recent_psy.iterrows():
            arrow = "─"
            if prev_psy is not None:
                diff = r["psy"] - prev_psy
                arrow = f"▲{diff:.2%}" if diff > 0 else (f"▼{abs(diff):.2%}" if diff < 0 else "─")
            prev_psy = r["psy"]
            psy_rows.append(f"| {d.strftime('%Y-%m')} | {r['ps']:.2f}x | **{r['psy']:.2%}** | {arrow} |")
        return f"""
---
### 近10月 PSY 趋势（营收口径）

> 📐 近20日斜率信号：{slope_info['signal_icon']} **{slope_info['signal']}** — {slope_info['desc']}

| 月份 | PS | PSY | 环比 |
|:-----|---:|----:|:-----|
{chr(10).join(psy_rows)}
"""

    valid = df[df['ERP'].notna()][['Date', 'ERP', 'PE']].copy()
    if len(valid) < 2:
        return ""

    slope_info = compute_erp_slope_signal(erp_series)

    valid['YM'] = valid['Date'].dt.to_period('M')
    month_end = valid.groupby('YM').last().reset_index(drop=True)
    recent = month_end.tail(10).copy()

    monthly_rows = [(row['Date'].strftime("%Y-%m"), row['PE'], row['ERP']) for _, row in recent.iterrows()]

    monthly_line, _ai_ok = build_monthly_trend_ai_block(name, code, monthly_rows, quantiles)

    if not _ai_ok:
        x = np.arange(len(recent))
        slope = np.polyfit(x, recent['ERP'].values, 1)[0]
        if slope > 0.0005:
            trend_icon = "持续走高"
        elif slope < -0.0005:
            trend_icon = "持续走低"
        else:
            trend_icon = "基本横盘"
        delta = recent['ERP'].iloc[-1] - recent['ERP'].iloc[0]
        delta_str = f"+{delta:.2%}" if delta >= 0 else f"{delta:.2%}"
        monthly_line = f"趋势方向：**{trend_icon}**，区间变化：**{delta_str}**"

    return f"""
---
### 近10月 ERP 趋势

> {monthly_line}
> 📐 近20日斜率信号：{slope_info['signal_icon']} **{slope_info['signal']}** — {slope_info['desc']}
"""


def build_monthly_trend_ai_block_standalone(name, code, monthly_rows, quantiles):
    """兼容旧调用点：单独暴露的月度AI块（正常应通过 build_trend_block 使用）。"""
    return build_monthly_trend_ai_block(name, code, monthly_rows, quantiles)。
