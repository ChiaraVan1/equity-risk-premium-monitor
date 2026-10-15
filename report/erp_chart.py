"""
report/erp_chart.py
──────────────────────────────────────────────────────────────────────────────
生成「各标的股权风险溢价 ERP」横向条形图 PNG（供微信推送 / 报告页引用）。

- 读取 data/erp_*.csv，取每个标的最新一行 ERP。
- 按 ERP 升序排列（右端 = 越高越低估），标注百分比。
- 以 0 为界：正 = 股票相对债券有溢价（越右越便宜），负 = 无风险利率更高（偏贵）。
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


def load_latest_erp(data_dir: str = "./data") -> list[dict]:
    """读取所有 erp_*.csv，返回 [{code, name, date, erp}]，按 ERP 升序。"""
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
        last = rows[-1]
        try:
            erp = float(last["ERP"])
            date = last.get("Date", "")
            name = last.get("IndexName", code)
        except (KeyError, TypeError, ValueError):
            continue
        items.append({"code": code, "name": name, "date": date, "erp": erp})
    return sorted(items, key=lambda x: x["erp"])


def render_chart(items: list[dict], out_path: str = _OUT_PATH) -> bool:
    """用 matplotlib 渲染横向条形图。成功返回 True，降级返回 False。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
    except ImportError:
        print("⚠️ 未安装 matplotlib，跳过 ERP 图生成（不影响报告/推送）")
        return False

    if not items:
        print("⚠️ 无 ERP 数据，跳过 ERP 图生成")
        return False

    # 中文字体：主动扫描常见字体目录里的 CJK 字体文件并逐个注册，
    # 不依赖 matplotlib 字体缓存（CI 装 fonts-noto-cjk 后 cache 常未刷新，findfont 会找不到）。
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
    _chosen = None
    for _fp in _font_files:
        try:
            _name = font_manager.fontManager.addfont(_fp)
            _chosen = _name[0] if isinstance(_name, list) else _name
            if _chosen:
                break
        except Exception:
            continue
    # 兜底：仍按常见字体名尝试
    if not _chosen:
        for _fn in ["Noto Sans CJK SC", "PingFang SC", "Heiti SC", "Microsoft YaHei",
                    "WenQuanYi Zen Hei", "Arial Unicode MS", "STHeiti", "Source Han Sans SC"]:
            try:
                font_manager.findfont(_fn, fallback_to_default=False)
                _chosen = _fn
                break
            except Exception:
                continue
    if _chosen:
        plt.rcParams["font.sans-serif"] = [_chosen]
    plt.rcParams["axes.unicode_minus"] = False

    # y 轴只显示指数中文名（去掉 code），方便一眼认出是谁
    names = [i["name"] for i in items]
    erps = [i["erp"] for i in items]

    fig, ax = plt.subplots(figsize=(9, max(6, 0.35 * len(items))))
    colors = []
    for v in erps:
        if v >= 0.03:
            colors.append("#c62828")     # 高 ERP → 显著低估（红/暖）
        elif v >= 0:
            colors.append("#ef6c00")     # 中低正值
        else:
            colors.append("#2e7d32")     # 负 ERP → 偏贵（绿）
    bars = ax.barh(names, erps, color=colors, edgecolor="#333333", linewidth=0.5)

    # 0 基准线
    ax.axvline(0, color="#333333", linewidth=1.2, linestyle="--")
    ax.set_title(f"各标的股权风险溢价 ERP（{items[0]['date']}）", fontsize=13, pad=14)
    ax.set_xlabel("ERP = 1/PE − 10年期国债收益率")
    ax.xaxis.set_major_formatter(lambda x, _: f"{x*100:.0f}%")

    # 数值标签
    for bar, v in zip(bars, erps):
        offset = 0.002 if v >= 0 else -0.002
        ha = "left" if v >= 0 else "right"
        ax.text(v + offset, bar.get_y() + bar.get_height() / 2,
                f"{v*100:.2f}%", va="center", ha=ha, fontsize=8, color="#333333")

    ax.set_xlim(left=min(erps) - 0.02, right=max(erps) + 0.02)
    ax.grid(axis="x", linestyle=":", alpha=0.4)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"✅ ERP 图已生成: {out_path}")
    return True


def build_erp_chart(data_dir: str = "./data", out_path: str = _OUT_PATH) -> str | None:
    """入口：生成图并返回微信推送可用的 URL（gh-pages 部署后）。"""
    items = load_latest_erp(data_dir)
    if not render_chart(items, out_path):
        return None
    return "https://chiaravan1.github.io/equity-risk-premium-monitor/erp_chart.png"


if __name__ == "__main__":
    url = build_erp_chart()
    if url:
        print(f"微信可用的 ERP 图 URL: {url}")
