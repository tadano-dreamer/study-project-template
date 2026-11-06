#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最終確認ノート（materials/最終確認ノート.pdf）を作る。

    python materials/最終確認ノート/build.py                    # PDF を作り、はみ出したページを報告する
    python materials/最終確認ノート/build.py --preview <dir>    # ついでに各ページを PNG にする（目で確かめる用。PyMuPDF が要る）

本番の約1週間前に作る「直前の教材」。くり返し落とした論点を、1ページ1トピックにまとめる。
材料は `python ledger/tally.py --drill`（全周回と一問一答の記録から、まだ直っていないものだけ）。

ページの中身は pages/*.html（1ファイル = A4 1枚 = 1トピック・名前の順に並ぶ）。見た目は style.css。
ページの中の {{PAGE}} はページ番号に置きかわる。
Chrome のヘッドレス印刷で PDF にする。Chrome は日本語を含む出力先に書けないことがあるので、一時フォルダを経由する。
Chrome の場所が下の候補と違うときは、環境変数 CHROME_PATH に実行ファイルのパスを入れる。
"""
import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_PDF = os.path.join(os.path.dirname(HERE), "最終確認ノート.pdf")
TMP = os.path.join(tempfile.gettempdir(), "finalnote")
CHROMES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]
CHROME_COMMANDS = ["google-chrome", "chromium", "chromium-browser", "chrome"]

# ページの中身が枠（A4）からはみ出したら data-over に px を書く。--dump-dom で読む
CHECK_JS = """
<script>
window.addEventListener('load', () => {
  document.querySelectorAll('.page').forEach((p, i) => {
    let bottom = 0;
    const top = p.getBoundingClientRect().top;
    p.querySelectorAll(':scope > *:not(.foot)').forEach(c => {
      bottom = Math.max(bottom, c.getBoundingClientRect().bottom - top);
    });
    const over = Math.round(bottom - (p.clientHeight - 16));
    p.setAttribute('data-over', String(over));
  });
});
</script>
"""


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


def assemble():
    """pages/*.html を1枚の HTML にまとめる -> (HTML, ページのファイル名の一覧)"""
    with open(os.path.join(HERE, "style.css"), encoding="utf-8") as f:
        css = f.read()
    files = sorted(glob.glob(os.path.join(HERE, "pages", "*.html")))
    if not files:
        sys.exit("ページがありません: materials/最終確認ノート/pages/*.html")
    total = len(files)
    parts = []
    for i, path in enumerate(files, 1):
        with open(path, encoding="utf-8") as f:
            body = f.read()
        parts.append("<!-- %s -->\n%s" % (os.path.basename(path), body.replace("{{PAGE}}", "%d / %d" % (i, total))))
    html = ("<!doctype html><html lang='ja'><head><meta charset='utf-8'>"
            "<title>最終確認ノート</title><style>%s</style></head><body>\n%s\n%s</body></html>"
            % (css, "\n".join(parts), CHECK_JS))
    return html, [os.path.basename(p) for p in files]


def overflow(exe, url):
    """各ページが枠から何 px はみ出したか -> [px]（負なら余白）"""
    dom = subprocess.run([exe, "--headless", "--disable-gpu", "--window-size=900,1200",
                          "--virtual-time-budget=3000", "--dump-dom", url],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.decode("utf-8", "replace")
    return [int(v) for v in re.findall(r'class="page[^"]*"[^>]*data-over="(-?\d+)"', dom)]


def page_count_and_preview(preview_dir):
    """PDF のページ数を数え、頼まれていれば各ページを PNG にする。PyMuPDF が無ければ None。"""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        if preview_dir:
            sys.exit("--preview には PyMuPDF が要ります（pip install pymupdf）")
        return None
    doc = fitz.open(OUT_PDF)
    if preview_dir:
        os.makedirs(preview_dir, exist_ok=True)
        for i, page in enumerate(doc, 1):
            page.get_pixmap(dpi=110).save(os.path.join(preview_dir, "p%02d.png" % i))
        print("PNG: %s" % preview_dir)
    return doc.page_count


def main():
    ap = argparse.ArgumentParser(description="最終確認ノートの PDF を作る")
    ap.add_argument("--preview", help="各ページの PNG を書き出すフォルダ")
    args = ap.parse_args()

    os.makedirs(TMP, exist_ok=True)
    html, names = assemble()
    tmp_html = os.path.join(TMP, "note.html")
    tmp_pdf = os.path.join(TMP, "note.pdf")
    with open(tmp_html, "w", encoding="utf-8") as f:
        f.write(html)
    if os.path.exists(tmp_pdf):
        os.remove(tmp_pdf)
    url = "file:///" + tmp_html.replace("\\", "/").lstrip("/")
    exe = chrome()
    subprocess.run([exe, "--headless", "--disable-gpu", "--no-pdf-header-footer", "--print-to-pdf-no-header",
                    "--print-to-pdf=" + tmp_pdf, url],
                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    if not os.path.exists(tmp_pdf):
        sys.exit("PDF を作れませんでした")
    shutil.copyfile(tmp_pdf, OUT_PDF)
    print("PDF: %s（%d KB）" % (OUT_PDF, os.path.getsize(OUT_PDF) // 1024))

    pages = page_count_and_preview(args.preview)
    if pages is None:
        print("（PyMuPDF が無いので、PDF のページ数は確かめていません）")
    elif pages != len(names):
        print("⚠ ページ数が合いません: ファイル %d 枚・PDF %d ページ" % (len(names), pages))

    overs = overflow(exe, url)
    bad = 0
    for name, over in zip(names, overs):
        bad += over > 0
        print("  %-28s %s" % (name, "⚠ はみ出し %dpx" % over if over > 0 else "余白 %dpx" % -over))
    if len(overs) != len(names):
        print("⚠ はみ出しの検査を読めませんでした（%d / %d）" % (len(overs), len(names)))
        bad += 1
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
