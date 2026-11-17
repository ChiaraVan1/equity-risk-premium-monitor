"""个股日收盘价取数（带重试与多源兜底）。

背景：2026-11-13 大秦铁路 601006、青岛啤酒 600600 的收盘价缺失，而同一批次
600585 取到了。原实现把 akshare 调用包在 try/except 里，失败只打印一行
"仅更新 PE" 就继续：PE 有周频兜底、债券收益率正常，ERP 照样算得出来，
于是"价格缺一天"这件事在日报里完全看不出来。

本模块的约定：
1. 取不到价一律重试（3 次，间隔 2s/5s）；
2. 重试失败走备用源：东财直连 kline → 腾讯日 K；
3. 全部失败时写 data/_price_failures.json，由 workflow 显式失败，
   不再静默降级。

对外只有一个函数 get_stock_close(code, start, end) -> DataFrame[Date, IndexClose] | None
"""

import json
import os
import time
import urllib.request
from datetime import datetime

import pandas as pd

FAIL_FILE = os.path.join("data", "_price_failures.json")
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}

EM_KLINE = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?"
            "secid={secid}&fields1=f1,f2&fields2=f51,f53&klt=101&fqt=0"
            "&beg={beg}&end={end}")
TX_KLINE = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
            "param={sym},day,{beg},{end},640,")


def _secid(code):
    """沪深市场号：沪市（6 开头）1，深市 0。"""
    return ("1." if code.startswith("6") else "0.") + code


def _tx_symbol(code):
    return ("sh" if code.startswith("6") else "sz") + code


def _http_json(url, timeout=15):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def _norm(datestr):
    return str(datestr)[:10].replace("/", "-")


def _by_akshare(code, start, end):
    import akshare as ak
    df = ak.stock_zh_a_hist(symbol=code, period="daily",
                            start_date=start, end_date=end, adjust="")[["日期", "收盘"]]
    df.columns = ["Date", "IndexClose"]
    df["Date"] = pd.to_datetime(df["Date"])
    return df.dropna()


def _by_eastmoney(code, start, end):
    j = _http_json(EM_KLINE.format(secid=_secid(code),
                                   beg=start.replace("-", ""),
                                   end=end.replace("-", "")))
    klines = (j.get("data") or {}).get("klines") or []
    rows = []
    for k in klines:
        parts = k.split(",")
        if len(parts) >= 2 and parts[1] not in ("", "-"):
            rows.append({"Date": pd.to_datetime(_norm(parts[0])),
                         "IndexClose": float(parts[1])})
    return pd.DataFrame(rows)


def _by_tencent(code, start, end):
    j = _http_json(TX_KLINE.format(sym=_tx_symbol(code),
                                   beg=_norm(start), end=_norm(end)))
    d = (j.get("data") or {}).get(_tx_symbol(code)) or {}
    kl = d.get("day") or d.get("qfqday") or []
    rows = []
    for x in kl:
        if len(x) >= 3 and x[2] not in ("", "-"):
            rows.append({"Date": pd.to_datetime(_norm(x[0])),
                         "IndexClose": float(x[2])})
    return pd.DataFrame(rows)


def _to_ymd(s):
    s = str(s).replace("-", "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def record_failure(code, reason):
    """把失败写进清单；workflow 会因这个文件非空而把任务标红。"""
    items = []
    if os.path.exists(FAIL_FILE):
        try:
            with open(FAIL_FILE, encoding="utf-8") as f:
                items = json.load(f)
        except Exception:
            items = []
    items = [x for x in items if x.get("code") != code]
    items.append({"code": code, "reason": str(reason)[:200],
                  "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    os.makedirs(os.path.dirname(FAIL_FILE), exist_ok=True)
    with open(FAIL_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def reset_failures():
    if os.path.exists(FAIL_FILE):
        os.remove(FAIL_FILE)


def get_stock_close(code, start, end, retries=3):
    """返回 DataFrame[Date, IndexClose]；彻底失败返回 None 并记失败清单。"""
    beg, fin = _to_ymd(start), _to_ymd(end)
    last_err = None

    for i in range(retries):
        try:
            df = _by_akshare(code, beg.replace("-", ""), fin.replace("-", ""))
            if len(df):
                return df
            last_err = "akshare 返回空"
        except Exception as e:
            last_err = f"akshare: {type(e).__name__}: {e}"
        if i < retries - 1:
            time.sleep(2 if i == 0 else 5)

    for name, fn in (("东财直连", _by_eastmoney), ("腾讯", _by_tencent)):
        try:
            df = fn(code, beg, fin)
            if len(df):
                print(f"   ↪ [{code}] akshare 失败，{name} 补到 {len(df)} 行")
                return df
            last_err = f"{name} 返回空"
        except Exception as e:
            last_err = f"{name}: {type(e).__name__}: {e}"

    print(f"   ❌ [{code}] 三个价格源都失败（{last_err}），记入 _price_failures.json")
    record_failure(code, last_err)
    return None
