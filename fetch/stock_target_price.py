#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拉取 fund-daily-report 的「合理买入价」正本，落缓存 data/stock_target_price.json。

设计原则（2026-11-27 定）：
- 正本只有一个：fund-daily-report/target_price/config.json（作者目标价，含 target_old 留痕）
- ERP 仓库只读、不写、不维护第二份
- 三级来源：本地文件 → raw.githubusercontent → GitHub API（私有仓库需 FD_GH_TOKEN / GH_PAT）
- 全部失败时：保留上次缓存并大声 WARN；无缓存才退出 1
"""
import base64
import datetime as dt
import json
import os
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(REPO, "data", "stock_target_price.json")
RAW = "https://raw.githubusercontent.com/ChiaraVan1/fund-daily-report/main/target_price/config.json"
LOCAL = os.environ.get("FD_TARGET_CONFIG") or os.path.expanduser(
    "~/DoubaoWork/fund-daily-report/target_price/config.json"
)


def _get_json(url, timeout=20):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def from_local():
    if not os.path.exists(LOCAL):
        raise FileNotFoundError(LOCAL)
    with open(LOCAL, encoding="utf-8") as f:
        return json.load(f), "local"


def from_raw():
    return _get_json(RAW), "raw"


def from_api():
    tk = os.environ.get("FD_GH_TOKEN") or os.environ.get("GH_PAT") or os.environ.get("GH_TOKEN")
    if not tk:
        raise RuntimeError("私有仓库且无 token，需配置 FD_GH_TOKEN")
    req = urllib.request.Request(
        "https://api.github.com/repos/ChiaraVan1/fund-daily-report/contents/target_price/config.json",
        headers={"Authorization": f"Bearer {tk}", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        j = json.loads(r.read().decode("utf-8"))
    return json.loads(base64.b64decode(j["content"]).decode("utf-8")), "api"


def normalize(cfg, source):
    stocks = {}
    for s in cfg.get("stocks", []):
        if not isinstance(s, dict):
            continue
        code = (s.get("secid") or s.get("code") or "").split(".")[-1].strip()
        if not code:
            continue
        target = s.get("target")
        if target in (None, ""):
            continue
        try:
            target = float(target)
        except (TypeError, ValueError):
            continue
        stocks[code] = {
            "name": s.get("name"),
            "target": target,
            "target_old": s.get("target_old"),
            "target_old_date": s.get("target_old_date"),
            "grade": s.get("grade"),
        }
    return {
        "updated": cfg.get("updated"),
        "source": source,
        "fetched_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "method": cfg.get("method"),
        "stocks": stocks,
    }


def main():
    errors = []
    for fn in (from_local, from_raw, from_api):
        try:
            cfg, src = fn()
            data = normalize(cfg, src)
            if not data["stocks"]:
                raise RuntimeError("正本里没有可用的 target")
            os.makedirs(os.path.dirname(CACHE), exist_ok=True)
            with open(CACHE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"✅ 合理买入价已缓存：{len(data['stocks'])} 只，版本 {data['updated']}，来源 {src}")
            return 0
        except Exception as e:
            errors.append(f"{fn.__name__}: {type(e).__name__} {e}")

    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as f:
            old = json.load(f)
        print("⚠️ 正本获取失败，沿用上次缓存：版本 "
              f"{old.get('updated')}（{len(old.get('stocks', {}))} 只），已抓取于 {old.get('fetched_at')}")
        for e in errors:
            print(f"   - {e}")
        return 0

    print("❌ 正本获取失败且无可用缓存，目标价本次不可用")
    for e in errors:
        print(f"   - {e}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
