"""
analysis/llm_client.py
公共 LLM 调用层：阿里云百炼 DashScope（OpenAI 兼容接口）单通道。

- 统一节流：连续两次调用至少间隔 _MIN_INTERVAL 秒
- 统一 429 退避：5s → 10s → 20s，优先遵循 Retry-After，最多 _MAX_RETRIES 次
- 失败（重试耗尽 / 空响应 / 网络异常）直接抛异常，由调用方决定兜底：
    sentiment.py → 返回「未知」；etf_quality.py / trend.py → 展示规则版原文

换模型/换厂商只改这一个文件（或设环境变量 LLM_MODEL）。
"""
import os
import time

import requests

DASHSCOPE_API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
DEFAULT_MODEL = os.getenv("LLM_MODEL", "deepseek-v4-pro")

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 5     # 秒，每次重试翻倍
_MIN_INTERVAL = 2         # 秒，两次调用最小间隔
_last_call_ts = {"t": 0.0}


def call_llm(prompt: str, max_tokens: int = 500, model: str | None = None) -> str:
    """发送单轮 prompt，返回模型文本。失败抛异常（requests 异常或 ValueError）。"""
    elapsed = time.time() - _last_call_ts["t"]
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)

    payload = {
        "model": model or DEFAULT_MODEL,
        "max_tokens": max_tokens,
        "enable_thinking": False,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {os.getenv('ALIYUN_API_KEY', '')}",
    }

    for attempt in range(_MAX_RETRIES):
        resp = requests.post(DASHSCOPE_API_URL, json=payload, headers=headers, timeout=60)
        _last_call_ts["t"] = time.time()

        if resp.status_code == 429 and attempt < _MAX_RETRIES - 1:
            wait = _RETRY_BASE_DELAY * (2 ** attempt)
            retry_after = resp.headers.get("retry-after")
            if retry_after:
                try:
                    wait = max(wait, float(retry_after))
                except ValueError:
                    pass
            time.sleep(wait)
            continue

        resp.raise_for_status()  # 非 2xx（含重试耗尽的 429）抛 HTTPError
        text = resp.json()["choices"][0]["message"]["content"].strip()
        if not text:
            raise ValueError("模型返回空响应")
        return text

    raise RuntimeError("unreachable")
