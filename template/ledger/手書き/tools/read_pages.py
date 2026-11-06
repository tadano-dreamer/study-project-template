"""手書きページのうち「書き込みのあるもの」だけを PNG にする（Claude が Read で読むため）。

SVG を HTML で包み、Chrome のヘッドレスで PNG にする。PIL などの追加の依存は要らない。

- 対象: ファイル1枚、または フォルダ（下を再帰的に探す）
- 書き込み（freedraw）が0本のページは「空」として一覧に出すだけで、画像にしない
- 線の色は書かれたまま描く（色の読み替えはしない）。読むときは 黒系（#1e1e1e・#495057）＝途中式 ／ 赤系（#e03131 など）＝採点のあとの直し
- 読めないページが1枚あっても止めない。最後に「読めなかったページ」として一覧に出し、終了コード 1 を返す

使い方:
  python ledger/手書き/tools/read_pages.py <ページ or フォルダ> <出力フォルダ> [--scale 1.5]
  出力フォルダは ASCII のパス（一時フォルダなど）にする。Chrome に全角パスを渡さないため。
  Chrome の場所が下の候補と違うときは、環境変数 CHROME_PATH に実行ファイルのパスを入れる。
"""
import argparse
import glob
import os
import re
import shutil
import subprocess
import sys

from excalidraw_io import load_scene

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CHROMES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]
CHROME_COMMANDS = ["google-chrome", "chromium", "chromium-browser", "chrome"]
PAGE_W, PAGE_H = 840, 1188
PAD = 20


def chrome():
    """Chrome（か Edge・Chromium）の実行ファイル。見つからなければ、置き方を言って止まる。"""
    env = os.environ.get("CHROME_PATH")
    if env:
        if os.path.exists(env):
            return env
        sys.exit(f"CHROME_PATH のファイルがありません: {env}")
    for path in CHROMES:
        if os.path.exists(path):
            return path
    for name in CHROME_COMMANDS:
        found = shutil.which(name)
        if found:
            return found
    sys.exit("Chrome が見つかりません。環境変数 CHROME_PATH に実行ファイルのパスを入れてください")


def load_elements(path):
    """平文（json）でも圧縮（compressed-json・iPad の既定）でも読む。削除済みの要素は除く。"""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    try:
        scene, _ = load_scene(text)
    except ValueError as e:
        raise ValueError(f"{e}: {path}") from e
    return [e for e in scene.get("elements", []) if not e.get("isDeleted")]


def to_svg(elements, scale):
    strokes, rects, texts = [], [], []
    for e in elements:
        t, ox, oy = e.get("type"), e.get("x", 0), e.get("y", 0)
        if t in ("freedraw", "line", "arrow"):
            pts = [(ox + px, oy + py) for px, py in e.get("points", [])]
            if pts:
                width = 2.2 if t == "freedraw" else 1.0
                strokes.append((e.get("strokeColor", "#1e1e1e"), pts, width))
        elif t == "rectangle":
            rects.append((e.get("strokeColor", "#1e1e1e"), ox, oy, e.get("width", 0), e.get("height", 0)))
        elif t == "text":
            texts.append((ox, oy, e.get("text", ""), e.get("strokeColor", "#1e1e1e"), e.get("fontSize", 18)))

    # ページ枠とはみ出した線の両方が入る範囲
    xs = [0, PAGE_W] + [x for _, ps, _ in strokes for x, _ in ps]
    ys = [0, PAGE_H] + [y for _, ps, _ in strokes for _, y in ps]
    minx, maxx, miny, maxy = min(xs) - PAD, max(xs) + PAD, min(ys) - PAD, max(ys) + PAD
    w, h = (maxx - minx) * scale, (maxy - miny) * scale

    def sx(x):
        return (x - minx) * scale

    def sy(y):
        return (y - miny) * scale

    parts = [f'<rect x="{sx(x):.1f}" y="{sy(y):.1f}" width="{rw * scale:.1f}" height="{rh * scale:.1f}" '
             f'fill="none" stroke="{c}" stroke-width="1.5" rx="6"/>' for c, x, y, rw, rh in rects]
    for c, ps, width in strokes:
        d = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in ps)
        parts.append(f'<polyline points="{d}" fill="none" stroke="{c}" stroke-width="{width * scale / 1.5:.1f}" '
                     f'stroke-linecap="round" stroke-linejoin="round"/>')
    for x, y, txt, c, size in texts:
        safe = txt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        parts.append(f'<text x="{sx(x):.1f}" y="{sy(y) + size * scale:.1f}" fill="{c}" '
                     f'font-family="Yu Gothic UI, Meiryo, sans-serif" font-size="{size * scale:.0f}" '
                     f'xml:space="preserve">{safe}</text>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" height="{h:.0f}" '
           f'viewBox="0 0 {w:.0f} {h:.0f}"><rect width="100%" height="100%" fill="white"/>'
           + "".join(parts) + "</svg>")
    return svg, int(w), int(h)


def render(path, out_dir, scale, serial):
    elements = load_elements(path)
    n_draw = sum(1 for e in elements if e.get("type") == "freedraw")
    if n_draw == 0:
        return None, 0
    svg, w, h = to_svg(elements, scale)
    stem = os.path.basename(path).replace(".excalidraw.md", "")
    # Chrome に全角パスを渡さないため、出力名は ASCII にする（元の名前は一覧で対応づける）。
    # 名前が全部日本語のページどうしが同じ名前にならないよう、通し番号を頭に付ける
    safe = "%03d_%s" % (serial, re.sub(r"[^0-9A-Za-z_-]", "_", stem))
    html = os.path.join(out_dir, f"{safe}.html")
    png = os.path.join(out_dir, f"{safe}.png")
    with open(html, "w", encoding="utf-8") as f:
        f.write('<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;padding:0;'
                f'background:#fff}}</style></head><body>{svg}</body></html>')
    if os.path.exists(png):
        os.remove(png)
    r = subprocess.run([chrome(), "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
                        f"--screenshot={png}", f"--window-size={w},{h}", "--default-background-color=FFFFFFFF",
                        "file:///" + html.replace("\\", "/")],
                       capture_output=True)
    if r.returncode != 0 or not os.path.exists(png) or os.path.getsize(png) == 0:
        raise RuntimeError(f"PNG を作れなかった: {path}\n  Chrome の出力: "
                           + r.stderr.decode("utf-8", "replace")[-800:])
    return png, n_draw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target")
    ap.add_argument("out_dir")
    ap.add_argument("--scale", type=float, default=1.5)
    args = ap.parse_args()

    if os.path.isdir(args.target):
        pages = sorted(glob.glob(os.path.join(args.target, "**", "*.excalidraw.md"), recursive=True))
    else:
        pages = [args.target]
    os.makedirs(args.out_dir, exist_ok=True)

    empty, failed = [], []
    for serial, p in enumerate(pages, 1):
        name = os.path.basename(p).replace(".excalidraw.md", "")
        try:
            png, n = render(p, args.out_dir, args.scale, serial)
        except (ValueError, RuntimeError, OSError) as e:
            failed.append(f"{name}: {e}")           # 1枚のせいで、残りのページを読み損ねない
            continue
        if png:
            print(f"書き込みあり  {name}  線 {n} 本 -> {png}")
        else:
            empty.append(name)
    print(f"空（書き込みなし） {len(empty)} 枚" + (f": {' '.join(empty)}" if len(empty) <= 40 else ""))
    if failed:
        print(f"⚠ 読めなかったページ {len(failed)} 枚（書き込みがあるかもしれない。Obsidian で開いて確かめる）")
        for line in failed:
            print("  ", line)
        sys.exit(1)


if __name__ == "__main__":
    main()
