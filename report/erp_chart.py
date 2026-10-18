"""
report/erp_chart.py
──────────────────────────────────────────────────────────────────────────────
生成「各标的 ERP 历史分位」横向条形图 PNG（供微信推送 / 报告页引用）。

- 读取 data/erp_*.csv，对每个标的取其全历史 ERP 序列，计算【最新 ERP 在自身
  历史中的百分位】（0~100%，越大 = 当前越接近该标的自身历史高位）。
- 因 ERP 高 = 越低估/便宜，故：
    分位越高 → 当前越接近自身历史低估区（暖色/红橙）
    分位越低 → 当前越接近自身历史高估区（冷色/绿蓝）
- 图按分位降序排列（顶部 = 最被低估），x 轴为分位百分比，并标注各标的
  历史起始年份，便于识别历史较短、分位参考意义较弱的新标的。
- 输出：docs/erp_chart.png，供 gh-pages 部署后获得稳定 URL，
  供 Server酱 desp 里用 ![ERP](url) 嵌入微信正文。

依赖：matplotlib（CI 已在 requirements.txt 安装）。
无 matplotlib 时降级跳过并打印警告，不中断主流程。
──────────────────────────────────────────────────────────────────────────────
"""
import os
import sys

# 保证能从仓库根目录 import 相对内容
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_OUT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "erp_chart.png")


def _percentile_rank(value: float, series: list[float]) -> float:
    """value 在 series 中的分位（0~100）。等于/小于 value 的样本占比。"""
    if not series:
        return 0.0
    below = sum(1 for x in series if x <= value)
    return below / len(series) * 100.0


def load_erp_percentiles(data_dir: str = "./data") -> list[dict]:
    """读取所有 erp_*.csv，返回 [{code, name, date, erp, pct, start, n}]。

    - pct：最新 ERP 在自身全历史中的分位（0~100）。
    - start：该标的历史起始日期（用于识别历史较短的新标的）。
    - n：有效 ERP 样本数。
    返回按 pct 降序（顶部 = 最被低估）。
    """
    import csv

    items = []
    for fname in sorted(os.listdir(data_dir)):
        if not (fname.startswith("erp_") and fname.endswith(".csv")):
            continue
        code = fname[4:-4]
        path = os.path.join(data_dir, fname)
        try:
            with open(path, encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
        except Exception:
            continue
        if not rows:
            continue
        series = []
        for r in rows:
            try:
                series.append((r["Date"], float(r["ERP"]), r.get("IndexName", "")))
            except (KeyError, TypeError, ValueError):
                continue
        if not series:
            continue
        series.sort(key=lambda x: x[0])  # 按日期升序，取最新
        last_date, last_erp, name = series[-1]
        name = name or code
        pct = _percentile_rank(last_erp, [s[1] for s in series])
        items.append({
            "code": code,
            "name": name,
            "date": last_date,
            "erp": last_erp,
            "pct": round(pct, 1),
            "start": series[0][0][:4],  # 起始年份，用于识别历史较短的新标的
            "n": len(series),
        })
    return sorted(items, key=lambda x: x["pct"])  # 升序：barh 第一个在底部，故分位最高(最低估)在顶部


def render_chart(items: list[dict], out_path: str = _OUT_PATH) -> bool:
    """用 matplotlib 渲染横向条形图（ERP 历史分位）。成功返回 True，降级返回 False。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
    except ImportError:
        print("⚠️ 未安装 matplotlib，跳过 ERP 分位图生成（不影响报告/推送）")
        return False

    if not items:
        print("⚠️ 无 ERP 数据，跳过 ERP 分位图生成")
        return False

    # 中文字体：优先用 findfont 直接定位系统已安装的 CJK 字体（可靠，
    # 尤其 CI 装 fonts-noto-cjk 后 Noto Sans CJK SC 会被 findfont 命中）。
    # 之前的实现主用 addfont()，但对 .ttc 集合返回 None，导致 _chosen 恒为 None，
    # 中文渲染成方块（乱码）。findfont(fallback_to_default=True) 可正确命中。
    _cjk_names = [
        "Noto Sans CJK SC", "Noto Sans CJK TC", "Noto Sans CJK JP",
        "PingFang SC", "Heiti SC", "Microsoft YaHei", "WenQuanYi Zen Hei",
        "WenQuanYi Micro Hei", "SimHei", "SimSun", "Source Han Sans SC",
        "Source Han Sans CN", "STHeiti", "Arial Unicode MS", "Droid Sans Fallback",
    ]
    _chosen = None
    for _fn in _cjk_names:
        try:
            _p = font_manager.findfont(_fn, fallback_to_default=True)
            # 确认命中真实 CJK 字体（而非 fallback 到默认的 DejaVu Sans）
            if _p and not _p.lower().endswith(("dejavusans.ttf", "dejavusansmono.ttf")):
                _chosen = _fn
                break
        except Exception:
            continue

    # 兜底：主动扫描字体目录注册（仅当 findfont 全部未命中时）
    if not _chosen:
        _font_files = []
        _font_dirs = [
            "/usr/share/fonts", "/usr/local/share/fonts",       # Linux (CI)
            "/System/Library/Fonts", "/Library/Fonts",           # macOS
            "C:/Windows/Fonts",                                  # Windows
        ]
        for _d in _font_dirs:
            if os.path.isdir(_d):
                for _root, _dirs, _files in os.walk(_d):
                    for _fn in _files:
                        if _fn.lower().endswith((".ttf", ".ttc", ".otf")):
                            _lp = os.path.join(_root, _fn)
                            _key = _fn.lower()
                            if any(k in _key for k in ["pingfang", "heiti", "hiragino", "noto",
                                                       "yahei", "microsoft yahei", "wenquanyi",
                                                       "wqy", "arpluming", "uming", "simhei",
                                                       "simsun", "source han", "droid sans fallback",
                                                       "arial unicode", "stheiti"]):
                                _font_files.append(_lp)
        for _fp in _font_files:
            try:
                _name = font_manager.fontManager.addfont(_fp)
                _got = _name[0] if isinstance(_name, list) else _name
                if _got:
                    _chosen = _got
                    break
            except Exception:
                continue

    if _chosen:
        plt.rcParams["font.sans-serif"] = [_chosen]
    plt.rcParams["axes.unicode_minus"] = False

    # y 轴只显示指数中文名（去掉 code）+ 历史起始年份（识别短历史新标的）
    names = [f"{i['name']}（{i['start']}）" for i in items]
    pcts = [i["pct"] for i in items]

    fig, ax = plt.subplots(figsize=(10, max(6, 0.38 * len(items))))

    # 配色：分位高 = 偏低估（暖色），分位低 = 偏贵（冷色）
    colors = []
    for v in pcts:
        if v >= 75:
            colors.append("#c62828")     # 分位≥75 → 显著低估（深红/暖）
        elif v >= 50:
            colors.append("#ef6c00")     # 50~75 → 偏低估（橙）
        elif v >= 25:
            colors.append("#0288d1")     # 25~50 → 偏贵（蓝）
        else:
            colors.append("#2e7d32")     # 分位<25 → 显著偏贵（深绿/冷）
    bars = ax.barh(names, pcts, color=colors, edgecolor="#333333", linewidth=0.5)

    # 50% 中位参考线：右侧 = 高于自身历史中位（偏低估），左侧 = 偏贵
    ax.axvline(50, color="#333333", linewidth=1.2, linestyle="--", alpha=0.7)
    ax.set_title(f"各标的 ERP 历史分位（{items[0]['date']}）", fontsize=13, pad=14)
    ax.set_xlabel("最新 ERP 在自身历史中的分位（%） ｜ 越高越接近历史低估区")
    ax.xaxis.set_major_formatter(lambda x, _: f"{x:.0f}%")

    # 数值标签：主标分位，尾部附当前 ERP 绝对值（小字）
    for bar, item in zip(bars, items):
        v = item["pct"]
        ax.text(v + 1.5, bar.get_y() + bar.get_height() / 2,
                f"{v:.0f}%", va="center", ha="left", fontsize=9,
                fontweight="bold", color="#333333")
        ax.text(v + 1.5, bar.get_y() + bar.get_height() / 2 - 0.13,
                f"ERP {item['erp']*100:.1f}%", va="center", ha="left",
                fontsize=7, color="#888888")

    ax.set_xlim(left=0, right=105)
    ax.grid(axis="x", linestyle=":", alpha=0.4)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"✅ ERP 分位图已生成: {out_path}")
    return True


def build_erp_chart(data_dir: str = "./data", out_path: str = _OUT_PATH) -> str | None:
    """入口：生成分位图并返回微信推送可用的 URL（gh-pages 部署后）。"""
    items = load_erp_percentiles(data_dir)
    if not render_chart(items, out_path):
        return None
    return "https://chiaravan1.github.io/equity-risk-premium-monitor/erp_chart.png"


if __name__ == "__main__":
    url = build_erp_chart()
    if url:
        print(f"微信可用的 ERP 分位图 URL: {url}")
