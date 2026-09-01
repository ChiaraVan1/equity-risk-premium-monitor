"""
analysis/sentiment.py
情绪分析模块：热度信号、基本面预警（东方财富快讯 + AI 两阶段判断）

【2026-08-02 恢复说明】
build_fundamental_alert_block() 在重构中被替换成了纯本地关键词计数的桩函数——
完全没有调用 AI，也没有"正常/关注/疑似暴雷"三档判断、置信度、摘要、逐条新闻
正负面分类、宽基指数跳过判定，等于砍掉了 README 里描述的整套
"本地粗筛 → AI相关性过滤 → AI最终判断"两阶段AI管道（全文件唯一调用AI做基本面
判断的模块）。

现在从重构前原始 erp_position.py（commit e1b472e，857-1064行 + 相关API重试
辅助函数）完整取回，只做了一处架构适配：旧版内部自己拉取/缓存东方财富快讯
（_fetch_em_news_df 全局缓存），新版由 prepare_all_data.py 统一拉取一次后
通过 news_df 参数传入所有标的复用，避免21个标的各打一次接口；本文件内的
_em_news_search 相应改为在传入的 news_df 里做本地关键词匹配，而不是自己发请求。
"""
import os
import json
import time

import requests

from analysis.popularity_signal import build_popularity_block, compute_popularity_confirmation
from config_loader import FUNDAMENTAL_KEYWORDS

# ══════════════════════════════════════════════════════════════════════
#  AI 调用（Anthropic兼容接口，七牛云 api.qnaigc.com）+ 限流重试
# ══════════════════════════════════════════════════════════════════════

_ANTHROPIC_API_URL = "https://api.qnaigc.com/v1/messages"

_API_MAX_RETRIES = 3
_API_RETRY_BASE_DELAY = 5     # 秒，每次重试翻倍：5s, 10s, 20s
_API_CALL_MIN_INTERVAL = 2    # 秒，连续两次AI调用之间的最小间隔
_last_api_call_ts = {"t": 0.0}


def _call_anthropic_with_retry(payload, headers):
    """
    调用 AI 接口，带限流感知的重试 + 间隔控制。
    - 连续两次调用之间至少间隔 _API_CALL_MIN_INTERVAL 秒（无论上次成功与否）
    - 遇到 429 时按 _API_RETRY_BASE_DELAY * 2^attempt 退避重试，最多 _API_MAX_RETRIES 次
    - 优先遵循响应头 Retry-After（如果有）
    - 重试耗尽后抛出最后一次的异常，交由调用方的 except 块处理
    """
    elapsed = time.time() - _last_api_call_ts["t"]
    if elapsed < _API_CALL_MIN_INTERVAL:
        time.sleep(_API_CALL_MIN_INTERVAL - elapsed)

    last_exc = None
    for attempt in range(_API_MAX_RETRIES):
        try:
            resp = requests.post(_ANTHROPIC_API_URL, json=payload, headers=headers, timeout=60)
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
                resp.raise_for_status()  # 重试耗尽，抛出 429

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


# ══════════════════════════════════════════════════════════════════════
#  基本面暴雷预警模块（东方财富快讯本地粗筛 + AI 二次过滤 + 结构化返回）
# ══════════════════════════════════════════════════════════════════════

_SKIP_FUNDAMENTAL_CODES = {
    "000300",  # 沪深300 - 宽基
    "000688",  # 科创50 - 宽基
    "SPY", "QQQ",  # 美股宽基
    "EWQ", "EWG", "EWJ", "EEM",  # MSCI 国别宽基
}


def _em_news_search(news_df, keywords: list, max_results: int = 8) -> list:
    """
    本地关键词粗筛：在传入的东方财富快讯（news_df，由 prepare_all_data.py 统一拉取）
    标题+摘要里匹配任意关键词命中。粗筛允许一定假阳性（比如"军工"命中无关新闻），
    交由后续 AI 二次过滤判断是否真正与该标的相关，而不是在这一步就追求精确匹配。
    """
    if news_df is None or len(news_df) == 0 or not keywords:
        return []

    pattern = "|".join(keywords)
    try:
        mask = (
            news_df["标题"].str.contains(pattern, na=False) |
            news_df["摘要"].str.contains(pattern, na=False)
        )
    except Exception:
        return []

    matched = news_df[mask].head(max_results)

    results = []
    for _, row in matched.iterrows():
        results.append({
            "title": str(row.get("标题", "")),
            "url": str(row.get("链接", "")),
            "content": str(row.get("摘要", ""))[:150],
        })
    return results


def build_fundamental_alert_block(code: str, name: str, news_df=None) -> tuple:
    """基本面暴雷预警：宽基跳过→关键词粗筛→AI相关性过滤→AI暴雷判断。
    全文件唯一调用AI做基本面判断的模块。返回: (summary_dict, markdown_block)"""
    _empty = {"alert_level": "─", "confidence": "─", "summary": ""}

    if code in _SKIP_FUNDAMENTAL_CODES:
        return (
            {"alert_level": "N/A", "confidence": "─", "summary": "宽基/国别指数，基本面预警不适用"},
            "\n> ℹ️ 基本面预警：宽基/国别指数成分股高度分散，单一基本面暴雷对指数影响有限，"
            "本模块不适用。请关注上方「减仓/清仓信号」中的价格回撤提示。\n",
        )

    keywords = FUNDAMENTAL_KEYWORDS.get(code, [])

    if not keywords:
        return _empty, ""

    candidates = _em_news_search(news_df, keywords, max_results=8)

    if not candidates:
        # 硬性短路：没有任何关键词命中时，绝不让AI用训练知识"脑补"判断。
        return (
            {"alert_level": "─", "confidence": "─", "summary": "未获取到相关新闻"},
            "\n> ⚠️ 基本面预警：本地关键词粗筛未命中任何近期快讯，"
            "本次跳过 AI 判断。结果为「未知」，请人工核实，不代表基本面正常。\n",
        )

    # ── 第一步：AI 相关性过滤 ──────────────────────────────────────────
    # 关键词粗筛允许假阳性（如"军工"命中无关新闻），这一步让AI逐条剔除
    # 真正不相关的新闻，避免"沪深300/监管"这类宽泛关键词把无关新闻带入
    # 最终判断，拉低判断质量。
    candidates_list_str = "\n".join(
        f"{i+1}. {c['title']}：{c['content']}" for i, c in enumerate(candidates)
    )
    filter_prompt = f"""以下是通过关键词粗筛得到的近期财经快讯候选列表，可能包含与"{name}({code})"无关的新闻（粗筛允许误报）。
请逐条判断每条新闻是否真正与"{name}({code})"的基本面相关（即报道的是该行业/指数本身或其核心成分股的情况，而非仅因字面关键词撞车）。

候选新闻：
{candidates_list_str}

请严格按以下JSON格式输出，不要输出任何其他内容：
{{"relevant_indices": [与"{name}"真正相关的新闻序号列表，如 [1, 3]，如果一条都不相关则为空列表 []]}}"""

    try:
        filter_payload = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 500,
            "messages": [{"role": "user", "content": filter_prompt}]
        }
        headers = {
            "Content-Type": "application/json",
            "x-api-key": os.getenv("ANTHROPIC_API_KEY", ""),
            "anthropic-version": "2023-06-01",
        }
        filter_resp = _call_anthropic_with_retry(filter_payload, headers)
        filter_data = filter_resp.json()
        filter_text_blocks = [b["text"] for b in filter_data.get("content", []) if b.get("type") == "text"]
        filter_raw = "\n".join(filter_text_blocks).strip().replace("```json", "").replace("```", "").strip()
        filter_result = json.loads(filter_raw)
        relevant_indices = filter_result.get("relevant_indices", [])
        all_results = [candidates[i - 1] for i in relevant_indices if 1 <= i <= len(candidates)]
    except Exception as e:
        # 相关性过滤失败：保守起见，不要把未经过滤的粗筛结果直接当作"相关新闻"
        # 喂给最终判断（会重新引入假阳性问题），明确标记为未知。
        return (
            {"alert_level": "─", "confidence": "─", "summary": f"相关性过滤失败：{e}"},
            f"\n> ⚠️ 基本面预警：AI相关性过滤步骤发生异常（{e}），跳过本次判断。"
            "结果为「未知」，不代表基本面正常。\n",
        )

    if not all_results:
        return (
            {"alert_level": "─", "confidence": "─", "summary": "粗筛命中均与标的无关"},
            "\n> ℹ️ 基本面预警：本地关键词粗筛命中的新闻经AI判断均与该标的无关，"
            "本次无可用新闻进行基本面判断。结果为「未知」，不代表基本面正常。\n",
        )

    news_snippets = "\n".join(
        f"- [{r.get('title','')}]({r.get('url','')})：{r.get('content','')[:150]}"
        for r in all_results
    )
    search_block = f"以下是近期相关新闻（来自实时快讯，已经过相关性过滤）：\n{news_snippets}"
    sources_from_search = [r.get("url", "") for r in all_results if r.get("url")]

    prompt = f"""你是一位专业的股票基本面分析师。请根据以下实时新闻，判断"{name}({code})"近期是否存在重大基本面负面事件，并对每条新闻做正负面分类。

{search_block}

判断标准（以下任一即为"疑似暴雷"）：
- 核心成分股出现重大财务造假、业绩暴雷、退市风险
- 行业遭遇超预期强监管、重大政策打压
- 宏观层面出现系统性风险（如金融危机苗头、主权债务危机）
- 指数或ETF本身出现清盘、停牌等结构性风险

同时请对上面每一条新闻单独判断其对"{name}({code})"是利好（positive）、利空（negative）还是中性（neutral）：
- positive：对行业/标的有积极影响（如政策支持、业绩超预期、需求增长）
- negative：对行业/标的有消极影响（如监管收紧、业绩下滑、负面舆情）
- neutral：纯客观陈述，无明显方向性影响（如指数行情播报、无关联背景新闻）

请严格按以下JSON格式输出，不要输出任何其他内容：
{{"alert_level": "正常" | "关注" | "疑似暴雷", "confidence": "低" | "中" | "高", "summary": "不超过80字的摘要", "sources": ["来源1", "来源2"], "news_sentiment": ["positive" | "negative" | "neutral", ...]}}

news_sentiment 数组的长度和顺序必须与上面新闻列表一一对应（共{len(all_results)}条）。"""

    try:
        payload = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 1000,
            "messages": [{"role": "user", "content": prompt}]
        }
        headers = {
            "Content-Type": "application/json",
            "x-api-key": os.getenv("ANTHROPIC_API_KEY", ""),
            "anthropic-version": "2023-06-01",
        }
        resp = _call_anthropic_with_retry(payload, headers)
        data = resp.json()

        text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
        raw_text = "\n".join(text_blocks).strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(raw_text)

        alert_level = result.get("alert_level", "正常")
        confidence = result.get("confidence", "低")
        summary = result.get("summary", "")
        sources = result.get("sources") or sources_from_search[:3]

        # 逐条新闻正负面计数。AI返回数组长度若与新闻数不一致（解析异常/模型未严格遵循格式），
        # 不强行对齐，计数置为不可用而非猜测性截断/补齐，避免产出虚假的精确数字。
        news_sentiment = result.get("news_sentiment", [])
        if isinstance(news_sentiment, list) and len(news_sentiment) == len(all_results):
            positive_count = sum(1 for s in news_sentiment if s == "positive")
            negative_count = sum(1 for s in news_sentiment if s == "negative")
            neutral_count = sum(1 for s in news_sentiment if s == "neutral")
            sentiment_available = True
        else:
            positive_count = negative_count = neutral_count = 0
            sentiment_available = False

        if alert_level == "疑似暴雷":
            level_icon = "🚨"
            action_tip = "**⚠️ 需人工确认后才可触发减仓/清仓操作，请立即核查。**"
        elif alert_level == "关注":
            level_icon = "⚠️"
            action_tip = "建议持续关注，暂不需要立即操作。"
        else:
            level_icon = "✅"
            action_tip = "近期无重大基本面异常。"

        sources_md = "\n".join(f"  - {s}" for s in sources) if sources else "  - 无"

        result_dict = {
            "alert_level": alert_level,
            "confidence": confidence,
            "summary": summary,
            "positive_count": positive_count,
            "negative_count": negative_count,
            "neutral_count": neutral_count,
            "sentiment_available": sentiment_available,
        }

        if sentiment_available:
            sentiment_line = f"📊 当日相关新闻：🟢正面 {positive_count} 条 · 🔴负面 {negative_count} 条 · ⚪中性 {neutral_count} 条（共{len(all_results)}条）"
        else:
            sentiment_line = "📊 当日相关新闻：正负面计数不可用（模型输出格式异常）"

        markdown_str = f"""
---
### 基本面暴雷预警（东方财富快讯本地粗筛 + AI 二次过滤判断，需人工确认）

> 🔍 数据源：东方财富全球财经快讯（akshare，免key）+ AI 相关性过滤 + AI 判断。**本模块仅供参考，不自动触发任何交易动作。**

{level_icon} **{alert_level}**（置信度：{confidence}）

{action_tip}

{sentiment_line}

**摘要：** {summary}

**参考来源：**
{sources_md}
"""
        return result_dict, markdown_str

    except requests.exceptions.Timeout:
        return (
            {"alert_level": "─", "confidence": "─", "summary": "超时"},
            "\n> ⚠️ 基本面预警：API请求超时（已重试），跳过。本次结果为「未知」，不代表基本面正常。\n",
        )
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        return (
            {"alert_level": "─", "confidence": "─", "summary": f"HTTP {status}"},
            f"\n> ⚠️ 基本面预警：API返回错误（HTTP {status}，已重试{_API_MAX_RETRIES}次），跳过。本次结果为「未知」，不代表基本面正常。\n",
        )
    except Exception as e:
        return (
            {"alert_level": "─", "confidence": "─", "summary": str(e)},
            f"\n> ⚠️ 基本面预警：发生异常（{e}），跳过。本次结果为「未知」，不代表基本面正常。\n",
        )


# ══════════════════════════════════════════════════════════════════════
#  综合情绪块（热度信号 + 基本面预警）
# ══════════════════════════════════════════════════════════════════════

def build_sentiment_block(code, name, etf_code, erp_percentile, news_df=None):
    """生成综合情绪块（热度 + 基本面预警）。"""
    blocks = []

    # 1. 热度信号
    popularity_result = compute_popularity_confirmation(code, erp_percentile)
    popularity_block = build_popularity_block(code, erp_percentile, precomputed=popularity_result)
    if popularity_block:
        blocks.append(popularity_block)

    # 2. 基本面预警
    _, fundamental_block = build_fundamental_alert_block(code, name, news_df)
    if fundamental_block:
        blocks.append(fundamental_block)

    return "\n".join(blocks)
