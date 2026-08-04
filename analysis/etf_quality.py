"""
analysis/etf_quality.py
──────────────────────────────────────────────────────────────────────────────────
ETF 执行质量分析模块
数据源: simple_etf_metrics.csv
补充维度（不替代 ERP 估值判断，仅辅助执行决策）：
  1. 折溢价率   — 当前买入/卖出的执行成本
  2. 换手背离   — 价格走势是否有成交量支撑
  3. 波动/回撤  — 当前风险水位（历史分位）
  4. 超额收益   — ETF 跟踪质量 + 近期相对基准动量
──────────────────────────────────────────────────────────────────────────────
"""

import os
import json
import time
import pandas as pd
import requests
from config_loader import ERP_TO_ETF

_metrics_cache = {}

# ════════════════════════════════════════════════════════════════════════
# AI 解读（DashScope 优先 → qnaigc 兜底 → 规则版 build_etf_quality_block 兜底）
# 与 analysis/trend.py 的三级降级模式保持一致，共用同一节流窗口的思路，
# 但节流状态各模块独立（不同模块的调用彼此不抢占对方的限流配额）。
# ══════════════════════════════════════════════════════════════

_API_MAX_RETRIES = 3
_API_RETRY_BASE_DELAY = 5
_API_CALL_MIN_INTERVAL = 2
_last_api_call_ts = {"t": 0.0}

_DASHSCOPE_API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
_QNAIGC_API_URL = "https://api.qnaigc.com/v1/messages"


def _throttled_post(url, payload, headers):
    elapsed = time.time() - _last_api_call_ts["t"]
    if elapsed < _API_CALL_MIN_INTERVAL:
        time.sleep(_API_CALL_MIN_INTERVAL - elapsed)

    last_exc = None
    for attempt in range(_API_MAX_RETRIES):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=60)
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


def _etf_conclusion_prompt(name, ts_code, conclusions: dict) -> str:
    """注意：喂给AI的是 A/B/C 规则已经算出来的结论文字，不是原始指标数字——
    规则计算（折溢价分档/量能背离/风险分位等）仍由本模块负责，AI只负责把
    这些已有判断组织成一段人话，不重新做判断、不看不到的原始数据。"""
    lines = "\n".join(f"- {k}: {v}" for k, v in conclusions.items())
    return f"""你是一名ETF交易执行顾问。以下是「{name}（{ts_code}）」这只ETF今天的执行质量分析结论（已经过规则计算，你不需要重新判断，只需要把这些结论组织成一段人话）：

{lines}

请只给出一段结论性文字（3-5句话，不用列表、不用小标题），直接回答三个问题：
1. 今天怎么下单（折溢价是否划算，市价/限价/等一等）
2. 现在建仓要不要分批、量能是否支撑当前走势
3. 这只ETF长期跟踪质量如何，是否值得继续持有或该考虑换仓

直接给结论，语气像给自己看的交易笔记，不要输出任何多余的开场白或标题，也不要逐条复述上面的结论列表。"""


def _build_etf_ai_conclusion(name, ts_code, conclusions: dict) -> str | None:
    """三级降级：① DashScope（阿里云百炼，国内数据源优先）→ ② qnaigc（七牛云
    Anthropic 兼容接口，跨境访问更稳）→ ③ 两个AI源都失败返回 None，
    交由调用方（build_etf_quality_block）展示规则版原文兜底。"""
    prompt = _etf_conclusion_prompt(name, ts_code, conclusions)

    # ── 第一级：阿里云百炼 DashScope ──────────────────────────────────
    try:
        payload = {
            "model": "deepseek-v4-pro",
            "max_tokens": 400,
            "enable_thinking": False,
            "messages": [{"role": "user", "content": prompt}]
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.getenv('ALIYUN_API_KEY', '')}",
        }
        resp = _throttled_post(_DASHSCOPE_API_URL, payload, headers)
        data = resp.json()
        conclusion = data["choices"][0]["message"]["content"].strip()
        if conclusion:
            return conclusion
        raise ValueError("空响应")
    except Exception as e:
        print(f"⚠️ DashScope ETF执行质量AI解读失败（尝试降级到 qnaigc）：{type(e).__name__}: {e}")

    # ── 第二级：七牛云 Anthropic 兼容接口（qnaigc）───────────────────
    try:
        payload = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 400,
            "messages": [{"role": "user", "content": prompt}]
        }
        headers = {
            "Content-Type": "application/json",
            "x-api-key": os.getenv("ANTHROPIC_API_KEY", ""),
            "anthropic-version": "2023-06-01",
        }
        resp = _throttled_post(_QNAIGC_API_URL, payload, headers)
        data = resp.json()
        text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
        conclusion = "\n".join(text_blocks).strip()
        if conclusion:
            return conclusion
        raise ValueError("空响应")
    except Exception as e:
        print(f"⚠️ qnaigc ETF执行质量AI解读也失败（已降级到规则版展示）：{type(e).__name__}: {e}")
        return None


def load_etf_metrics() -> pd.DataFrame | None:
    """加载 ETF 执行质量指标（simple_etf_metrics.csv）"""
    if _metrics_cache:
        return _metrics_cache.get("df")

    local_path = "./data/simple_etf_metrics.csv"
    try:
        df = pd.read_csv(local_path, index_col="ts_code")
        print(f"✅ 从本地加载 ETF 指标：{local_path}（{len(df)} 条）")
        _metrics_cache["df"] = df
        return df
    except FileNotFoundError:
        print("⚠️ 未找到 data/simple_etf_metrics.csv，ETF 执行质量模块将跳过，不影响主报告。")
    except Exception as e:
        print(f"⚠️ ETF 指标加载失败：{e}，ETF 执行质量模块将跳过，不影响主报告。")
    _metrics_cache["df"] = None
    return None


def build_etf_quality_block(erp_code: str, etf_df: pd.DataFrame | None) -> str:
    """
    按三个决策场景输出 ETF 执行质量补充块：
      A. 今天怎么下单（折溢价）
      B. 这波量是否真实（资金流）
      C. 风险水位 / 换只ETF（波动 + 超额收益）
    """
    if etf_df is None:
        return ""

    ts_code = ERP_TO_ETF.get(erp_code)
    if ts_code is None:
        return ""

    if ts_code not in etf_df.index:
        return f"\n> ⚠️ ETF {ts_code} 不在今日指标文件中，跳过执行质量分析。\n"

    row = etf_df.loc[ts_code]

    def safe(col, default=float("nan")):
        v = row.get(col, default)
        return default if pd.isna(v) else v

    etf_name         = safe("name", ts_code)
    discount_rate    = safe("latest_discount_rate", 0.0)
    discount_q1y     = safe("discount_quantile_1y", 0.5)
    discount_q3y     = safe("discount_quantile_3y", 0.5)
    discount_5d_chg  = safe("change_5d_discount", 0.0)
    discount_10d_chg = safe("change_10d_discount", 0.0)
    turnover_q       = safe("turnover_quantile", 0.5)
    acceleration     = safe("turnover_acceleration", float("nan"))
    divergence       = bool(safe("is_price_turnover_divergence", False))
    vol_q1y          = safe("volatility_quantile_1y", 0.5)
    dd_q1y           = safe("max_drawdown_quantile_1y", 0.5)
    ann_vol          = safe("annualized_volatility", 0.0)
    max_dd           = safe("max_drawdown", 0.0)
    excess_mean      = safe("excess_return_mean", 0.0)
    tracking_err     = safe("tracking_error", 0.0)
    ma_slope         = safe("ma_trend_slope", 0.0)
    ma5              = safe("excess_return_5d_ma", float("nan"))
    ma10             = safe("excess_return_10d_ma", float("nan"))
    ma20             = safe("excess_return_20d_ma", float("nan"))

    # ══════════════════════════════════════════════════════
    # A. 今天怎么下单 — 折溢价
    # ════════════════════════════════════════════════════
    disc_pct = discount_rate * 100

    if discount_rate < -0.003:
        disc_icon, disc_label = "🟢", f"折价 {disc_pct:.3f}%"
        disc_action = "折价买入，执行成本占优，可直接下单"
    elif discount_rate < -0.0005:
        disc_icon, disc_label = "🟡", f"轻微折价 {disc_pct:.3f}%"
        disc_action = "小幅折价，正常范围，可下单"
    elif discount_rate < 0.0005:
        disc_icon, disc_label = "⚪", f"平价 {disc_pct:.3f}%"
        disc_action = "平价，无额外成本，可下单"
    elif discount_rate < 0.003:
        disc_icon, disc_label = "🟠", f"轻微溢价 {disc_pct:.3f}%"
        disc_action = "小幅溢价，可接受，建议挂单而非市价"
    else:
        disc_icon, disc_label = "🔴", f"溢价 {disc_pct:.3f}%"
        disc_action = "溢价偏高，建议等折价窗口或限价委托"

    q1y_pct = discount_q1y * 100
    q3y_pct = discount_q3y * 100
    if discount_q1y <= 0.2:
        q_label = f"🟢 1年{q1y_pct:.0f}%分位 — 历史少见低折价（买入成本极优）"
    elif discount_q1y <= 0.5:
        q_label = f"🟡 1年{q1y_pct:.0f}%分位 — 折价处于历史中低位"
    elif discount_q1y <= 0.8:
        q_label = f"🟠 1年{q1y_pct:.0f}%分位 — 折价处于历史中高位（偏贵）"
    else:
        q_label = f"🔴 1年{q1y_pct:.0f}%分位 — 历史罕见高溢价，等待"

    d5_str  = f"+{discount_5d_chg*100:.3f}%" if discount_5d_chg >= 0 else f"{discount_5d_chg*100:.3f}%"
    d10_str = f"+{discount_10d_chg*100:.3f}%" if discount_10d_chg >= 0 else f"{discount_10d_chg*100:.3f}%"
    if discount_rate > 0:
        if discount_5d_chg > 0.001:
            trend_label = "溢价扩大 → 买入成本上升，等折价窗口"
        elif discount_5d_chg < -0.001:
            trend_label = "溢价收窄 → 执行成本改善中"
        else:
            trend_label = "折溢价近期稳定"
    else:
        if discount_5d_chg < -0.001:
            trend_label = "折价扩大 → 买入窗口正在打开"
        elif discount_5d_chg > 0.001:
            trend_label = "折价收窄 → 买入窗口趋于关闭，抓紧或等下次"
        else:
            trend_label = "折溢价近期稳定"

    # ═════════════════════════════════════════════════════
    # B. 这波量是否真实 — 资金流
    # ══════════════════════════════════════════════════════
    tq_pct = turnover_q * 100
    if turnover_q >= 0.8:
        tq_icon, tq_label = "🔥", f"1周成交额在52周中处于{tq_pct:.0f}%分位 — 市场高度活跃"
    elif turnover_q >= 0.5:
        tq_icon, tq_label = "🟡", f"1周成交额在52周中处于{tq_pct:.0f}%分位 — 活跃度中等"
    else:
        tq_icon, tq_label = "🧊", f"1周成交额在52周中处于{tq_pct:.0f}%分位 — 成交清淡"

    if pd.notna(acceleration):
        acc_pct = acceleration * 100
        if acceleration > 1.6:
            acc_label = f"🔥 {acc_pct:.0f}% — 本周放量明显（正常≈100%），资金加速涌入"
        elif acceleration > 1.0:
            acc_label = f"🟡 {acc_pct:.0f}% — 本周略高于近4周均值，温和放量"
        elif acceleration > 0.4:
            acc_label = f"🟠 {acc_pct:.0f}% — 本周低于近4周均值，资金动能偏弱"
        else:
            acc_label = f"🧊 {acc_pct:.0f}% — 本周明显缩量，谨慎追入"
    else:
        acc_label = "─ 数据不足"

    if divergence:
        div_label = "⚠️ 背离 — 价格走势与成交量方向相反，需警惕假突破/假跌破"
    else:
        div_label = "✅ 无背离 — 价格与成交量方向一致，走势有量配合"

    # ══════════════════════════════════════════════════════
    # C. 风险水位 / 换只ETF — 波动 + 超额收益
    # ═════════════════════════════════════════════════════
    vol_pct = vol_q1y * 100
    if vol_q1y >= 0.85:
        vol_icon, vol_label = "🔴", f"1年{vol_pct:.0f}%分位 — 波动率历史高位，单次建仓量要小，分批进"
    elif vol_q1y >= 0.6:
        vol_icon, vol_label = "🟠", f"1年{vol_pct:.0f}%分位 — 波动率中高，正常建仓"
    else:
        vol_icon, vol_label = "🟢", f"1年{vol_pct:.0f}%分位 — 波动率偏低"

    dd_pct = dd_q1y * 100
    if dd_q1y >= 0.85:
        dd_label = f"1年{dd_pct:.0f}%分位 — 已充分下跌，风险较释放 ✅"
    elif dd_q1y >= 0.5:
        dd_label = f"1年{dd_pct:.0f}%分位 — 回撤中等，尚有下行空间"
    else:
        dd_label = f"1年{dd_pct:.0f}%分位 — 回撤偏小，下行风险未充分释放，别误以为安全 ⚠️"

    if vol_q1y >= 0.85 and dd_q1y >= 0.7:
        risk_conclusion = "高波动 + 充分回撤 → 适合分批建仓，风险已有释放"
    elif vol_q1y < 0.4 and dd_q1y < 0.3:
        risk_conclusion = "低波动 + 小回撤 → 表面平静但风险未释放，谨慎追高"
    elif vol_q1y >= 0.85:
        risk_conclusion = "波动率高位 → 控制单次建仓量，等波动率回落再加"
    else:
        risk_conclusion = "风险水位正常"

    excess_ann = excess_mean * 250
    if excess_mean > 0.01:
        excess_icon, excess_label = "✅", f"年化超额约 +{excess_ann:.1f}% — 长期跑赢基准，值得持有"
    elif excess_mean > 0:
        excess_icon, excess_label = "✅", f"年化超额约 +{excess_ann:.2f}% — 微正，跟踪正常"
    elif excess_mean > -0.01:
        excess_icon, excess_label = "🟡", f"年化超额约 {excess_ann:.2f}% — 轻微跑输，可接受"
    else:
        excess_icon, excess_label = "🔴", f"年化超额约 {excess_ann:.1f}% — 长期跑输基准，考虑换同指数更优ETF"

    if pd.notna(ma5) and pd.notna(ma20):
        if ma5 > ma20 + 0.001:
            ma_label = "📈 近期超额改善中（5日MA > 20日MA）"
        elif ma5 < ma20 - 0.001:
            ma_label = "📉 近期超额走弱中（5日MA < 20日MA）"
        else:
            ma_label = "➡️ 近期超额持平"
    else:
        ma_label = "─"

    te_label = "⚠️ 偏高，建议关注同类替代品" if tracking_err > 8 else "正常"

    # ══════════════════════════════════════════════════════
    # AI 解读：把上面 A/B/C 已经算出来的结论（不是原始指标）喂给AI，
    # 让AI组织成一段交易笔记式的话；三级降级失败才展示规则版原文。
    # ══════════════════════════════════════════════════════
    conclusions = {
        "折溢价": f"{disc_label}（{q_label.split('—')[-1].strip()}），{trend_label}，判断：{disc_action}",
        "资金活跃度": tq_label.split("—")[-1].strip() if "—" in tq_label else tq_label,
        "资金加速度": acc_label,
        "价量背离": div_label,
        "波动率水位": vol_label,
        "回撤水位": dd_label,
        "综合风险结论": risk_conclusion,
        "超额收益": excess_label,
        "近期动量": ma_label,
        "跟踪误差": f"{tracking_err:.2f}% {te_label}",
    }
    ai_narrative = _build_etf_ai_conclusion(etf_name, ts_code, conclusions)
    if ai_narrative:
        return f"""
---
### ETF 执行质量（{ts_code}）· AI 解读

{ai_narrative}
"""

    alerts = []
    if discount_rate > 0.003:
        alerts.append("溢价偏高→等折价或限价")
    if divergence:
        alerts.append("价格/量背离→确认量能再入")
    if vol_q1y >= 0.85:
        alerts.append("波动高位→分批建仓")
    if dd_q1y < 0.3 and vol_q1y < 0.4:
        alerts.append("低波低撤→风险未释放，别追高")
    if excess_mean < -0.01:
        alerts.append("长期跑输基准→考虑换ETF")

    if alerts:
        exec_line = "⚠️ 注意：" + " · ".join(alerts)
    else:
        exec_line = "✅ 执行条件正常"

    def _ma_str(v):
        return f"{v*100:.4f}%" if pd.notna(v) else "─"

    block = f"""
---
### ETF 执行质量（{ts_code}）

**{exec_line}**

**A · 今天怎么下单（折溢价）**
> 折溢价决定你买入时的实际成本。折价 = 相当于打折买净值；溢价 = 多付钱。

- 当前：{disc_icon} {disc_label}（1年{q1y_pct:.0f}% / 3年{q3y_pct:.0f}%分位）— {q_label.split('—')[-1].strip()} → {disc_action}
- 趋势：5日{d5_str} / 10日{d10_str} → {trend_label}

**B · 这波量是否真实（资金流）**
> 价格涨但量在缩 = 假突破；价格跌但量在涨 = 可能在建仓。量配合才值得跟。

- 资金活跃度：{tq_icon} {tq_label}
- 资金加速度：{acc_label}
- 价格/量背离：{div_label}

**C · 风险水位 / 要不要换ETF（波动 + 超额收益）**
> 波动率高位时分批进；超额收益长期为负时考虑换同指数的其他ETF。

- 年化波动率：{ann_vol:.1f}%，{vol_icon} {vol_label}
- 最大回撤：{max_dd*100:.1f}%，{dd_label}
- 综合风险：{risk_conclusion}
- 超额收益：{excess_icon} {excess_label}
- 近期动量：{ma_label}（MA 5日{_ma_str(ma5)} / 10日{_ma_str(ma10)} / 20日{_ma_str(ma20)}）
- 跟踪误差：{tracking_err:.2f}% {te_label}
"""
    return block


if __name__ == "__main__":
    df = load_etf_metrics()
    if df is not None:
        for code in ["000688", "000300", "399989"]:
            block = build_etf_quality_block(code, df)
            if block:
                print(block)
                print("\n" + "="*80 + "\n")
