"""手書きページ（Excalidraw）を、解答ノートの小問ごとに1枚ずつ作る。

途中式・選んだ根拠・消去法のメモを書いてもらい、採点のときに Claude が全部読むためのページ。
統計検定2級のプロジェクトで使った道具を、解答ノート（rounds/round<N>/**/*.md）から直接ページを作る形にした。

- 1ファイル = 1小問。A4縦（840×1188）。ヘッダの下は全部が書く場所（黒＝解いている間／赤＝直し。区画は分けない）
- ヘッダの右に「← 問題に戻る」。解答ノートのその小問の見出しへのリンク
- 置き場所: ledger/手書き/round<N>/<ノート名>/<大問id>-<小問2桁>.excalidraw.md
  （rounds/ の下に置くと tally.py が解答ノートとして読んでしまうので、外に置く）
- 1周目以外は名前に「_2周目」「_プレテスト」を付ける
  （同じ名前にすると、Obsidian の [[1-01-02.excalidraw]] がどちらを指すか決まらなくなる）
- --link を付けると、解答ノートの各小問の見出しの下に「✏️ [[…|途中式]]」の行を入れる（無い小問だけ）

🔴 ページの形（テンプレートの要素）を変えたら、古い要素の id を RETIRED_IDS に移す。
   iPad の Excalidraw は、開いていた古いページと pull した新しいページを要素の id ごとに混ぜて保存する。
   「削除済み（isDeleted）」の印を高い version で入れておかないと、古い要素が復活する
   （統計検定2級で実際に起きた: 消したはずの区画と旧ヘッダが、新しいページに重なって表示された）。

ページの扱い:
- テンプレートの要素しか無い → 作り直す
- 手書きの線（freedraw）がある → 線を残してテンプレートだけ差し替える（古い要素が生きているときだけ）
- 線以外（文字・画像）が足されている／Drawing を読めない → 触らない（判定できないものは消さない）

使い方: python ledger/手書き/tools/gen_pages.py [--round 1] [--link]
"""
import argparse
import glob
import json
import os
import re
import sys

from excalidraw_io import load_scene

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
HANDWRITING = os.path.dirname(HERE)
LEDGER = os.path.dirname(HANDWRITING)
ROUNDS = os.path.join(LEDGER, "rounds")

W, H = 840, 1188          # A4縦
HEADER_DIV_Y = 96         # ヘッダの下。ここから下は全部が書く場所
UPDATED = 1790000000000
PLUGIN_RELEASE = "2.25.1"
LINK_COLOR = "#1971c2"
TEMPLATE_VERSION = 10     # 端末に残っている古い版より必ず大きくする
TOMBSTONE_VERSION = 100   # 古い要素を消す印。どの版よりも大きくする
LINK_MARK = "✏️"
PRETEST_ROUND = 0

# プラグインは Element Links 節の id を8文字で読むので、id は8文字にそろえる。
TEMPLATE_IDS = {"pageBox0", "hdTitle0", "backLink", "hdLegend", "hdDivide", "zoneLbl0"}
# 形を変える前の版で使っていた要素 {id: 種類}。例: {"zoneOld0": "text", "divOld00": "line"}
RETIRED_IDS = {}
KNOWN_IDS = TEMPLATE_IDS | set(RETIRED_IDS)

NOTE_DAIMON = re.compile(r"^##\s+((\S+).*?)\s*$")
NOTE_SHOMON = re.compile(r"^###\s+(\((\d+)\).*?)\s*$")
UNSAFE_IN_NAME = re.compile(r'[\\/:*?"<>|#^\[\]]')
LINK_BREAKERS = re.compile(r"[|#^\[\]]")


def el_base(eid, etype, x, y, w, h, idx, stroke="#868e96"):
    return {
        "id": eid, "type": etype, "x": x, "y": y, "width": w, "height": h,
        "angle": 0, "strokeColor": stroke, "backgroundColor": "transparent",
        "fillStyle": "solid", "strokeWidth": 1, "strokeStyle": "solid",
        "roughness": 0, "opacity": 100, "groupIds": [], "frameId": None,
        "index": idx, "roundness": None, "seed": sum(map(ord, eid)) * 7919 % (2**31),
        "version": TEMPLATE_VERSION, "versionNonce": sum(map(ord, eid)) * 104729 % (2**31),
        "isDeleted": False, "boundElements": [], "updated": UPDATED,
        "link": None, "locked": True,
    }


def text_el(eid, x, y, txt, idx, size=18, stroke="#868e96", link=None):
    e = el_base(eid, "text", x, y, max(60, int(len(txt) * size * 0.9)), int(size * 1.25), idx, stroke)
    e.update({"text": txt, "rawText": txt, "originalText": txt, "fontSize": size,
              "fontFamily": 2, "textAlign": "left", "verticalAlign": "top",
              "containerId": None, "autoResize": True, "lineHeight": 1.25, "link": link})
    return e


def rect_el(eid, x, y, w, h, idx, stroke="#adb5bd"):
    e = el_base(eid, "rectangle", x, y, w, h, idx, stroke)
    e["roundness"] = {"type": 3}
    return e


def line_el(eid, x, y, w, idx, stroke="#dee2e6"):
    e = el_base(eid, "line", x, y, w, 0, idx, stroke)
    e.update({"points": [[0, 0], [w, 0]], "lastCommittedPoint": None,
              "startBinding": None, "endBinding": None,
              "startArrowhead": None, "endArrowhead": None})
    return e


def tombstones():
    """古い版の要素を消す印。"""
    out = []
    for i, (eid, etype) in enumerate(sorted(RETIRED_IDS.items())):
        e = el_base(eid, etype, 0, 0, 0, 0, f"Z{i}")
        e.update({"isDeleted": True, "version": TOMBSTONE_VERSION})
        if etype == "text":
            e.update({"text": "", "rawText": "", "originalText": "", "fontSize": 14, "fontFamily": 2,
                      "textAlign": "left", "verticalAlign": "top", "containerId": None,
                      "autoResize": True, "lineHeight": 1.25})
        if etype == "line":
            e.update({"points": [[0, 0], [1, 0]], "lastCommittedPoint": None, "startBinding": None,
                      "endBinding": None, "startArrowhead": None, "endArrowhead": None})
        out.append(e)
    return out


def template_elements(title, back_link):
    return [
        rect_el("pageBox0", 0, 0, W, H, "a0"),
        text_el("hdTitle0", 16, 16, title, "a1", size=26, stroke="#495057"),
        text_el("backLink", 620, 20, "← 問題に戻る", "a2", size=20, stroke=LINK_COLOR, link=back_link),
        text_el("hdLegend", 16, 58, "黒＝解いている間の途中式　／　赤＝採点のあとの直し", "a3", size=14),
        line_el("hdDivide", 0, HEADER_DIV_Y, W, "a4"),
        text_el("zoneLbl0", 16, HEADER_DIV_Y + 8, "▼ 途中式・選んだ根拠", "a5", size=14, stroke="#adb5bd"),
    ] + tombstones()


def build_md(title, back_link, user_elements=()):
    """back_link は "[[ノート名#大問の見出し#小問の見出し]]"。テキスト要素の link と Element Links 節の両方に入れる。"""
    elements = template_elements(title, back_link) + list(user_elements)
    scene = {"type": "excalidraw", "version": 2,
             "source": f"https://github.com/zsviczian/obsidian-excalidraw-plugin/releases/tag/{PLUGIN_RELEASE}",
             "elements": elements,
             "appState": {"gridSize": None, "gridStep": 5, "gridModeEnabled": False,
                          "viewBackgroundColor": "#ffffff"},
             "files": {}}
    live_text = [e for e in elements if e.get("type") == "text" and not e.get("isDeleted")]
    te = "".join(f'{e["text"]} ^{e["id"]}\n\n' for e in live_text)
    el = "".join(f'{e["id"]}: {e["link"]}\n\n' for e in live_text if e.get("link"))
    return ("---\n\nexcalidraw-plugin: parsed\ntags: [excalidraw]\n\n---\n"
            "==⚠  Switch to EXCALIDRAW VIEW in the MORE OPTIONS menu of this document. ⚠== "
            "You can decompress Drawing data with the command palette: 'Decompress current Excalidraw file'. "
            "For more info check in plugin settings under 'Saving'\n\n\n"
            "# Excalidraw Data\n\n## Text Elements\n" + te
            + ("## Element Links\n" + el if el else "")
            + "%%\n## Drawing\n```json\n"
            + json.dumps(scene, ensure_ascii=False, indent=2) + "\n```\n%%")


def round_suffix(round_no):
    """1周目は無印。ほかは名前で周回が分かるようにする。"""
    if round_no == 1:
        return ""
    return "_プレテスト" if round_no == PRETEST_ROUND else f"_{round_no}周目"


def page_name(dai, no, round_no=1):
    return UNSAFE_IN_NAME.sub("_", f"{dai}-{int(no):02d}") + round_suffix(round_no)


def back_link(note, dai_heading, sho_heading):
    """「← 問題に戻る」のリンク。大問の見出し → 小問の見出しの順にたどる。

    見出しに `|` `#` `^` `[` `]` があると、Obsidian がリンクの区切りとして読んで壊れる
    （`P(A|B)` の `|` は別名の区切りになる）。そのときは、たどれる所までにする。
    """
    def clean(heading):
        return not LINK_BREAKERS.search(heading)

    if clean(dai_heading) and clean(sho_heading):
        return f"[[{note}#{dai_heading}#{sho_heading}]]"
    if clean(dai_heading):
        return f"[[{note}#{dai_heading}]]"
    return f"[[{note}]]"


def plan(path):
    """("new", 残す要素) 作り直す / ("migrate", 残す要素) 線を残して差し替える / ("keep", 理由) 触らない / ("ok", []) そのまま"""
    if not os.path.exists(path):
        return "new", []
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if "## Embedded Files" in text:
        return "keep", "画像が貼られている"
    try:
        elements = load_scene(text)[0].get("elements", [])
    except ValueError as e:
        return "keep", f"読めない（{e}）"
    # 削除済み（isDeleted）の要素は手書きとして数えないが、消す印として残す（端末側の古い版に勝たせるため）
    user = [e for e in elements if e.get("id") not in KNOWN_IDS]
    live = [e for e in user if not e.get("isDeleted")]
    if any(e.get("type") != "freedraw" for e in live):
        return "keep", "線以外の要素がある"
    stale = any(e.get("id") in RETIRED_IDS and not e.get("isDeleted") for e in elements)
    if not live:
        return "new", user
    if stale:
        return "migrate", user
    return "ok", []


def note_paths(round_no):
    return sorted(glob.glob(os.path.join(ROUNDS, f"round{round_no}", "**", "*.md"), recursive=True))


def read_lines(path):
    """ノートを "\\n" で区切った行にする。CRLF のファイルは各行の末尾に "\\r" が残る（書き戻すときに元へ戻る）。"""
    with open(path, encoding="utf-8", newline="") as f:
        return f.read().split("\n")


def listed_in_note(lines):
    """解答ノート1枚に載っている小問 -> [(大問id, 小問番号, 大問の見出し, 小問の見出し, 行番号)]

    見出しは tally.py と同じ読み方（`## <大問id> …` の下の `### (n) …`）。
    """
    out, dai, dai_heading = [], None, None
    for lineno, line in enumerate(lines):
        m = NOTE_DAIMON.match(line)
        if m:
            dai_heading, dai = m.group(1), m.group(2)
            continue
        m = NOTE_SHOMON.match(line)
        if m and dai:
            out.append((dai, m.group(2), dai_heading, m.group(1), lineno))
    return out


def add_links(path, round_no):
    """解答ノートの各小問の見出しの下に、手書きページへのリンクの行を入れる -> 入れた数。

    既にリンクの行（✏️）がある小問は触らない。改行コードは元のまま書き戻す。
    """
    lines = read_lines(path)
    added = 0
    for dai, no, _dh, _sh, lineno in reversed(listed_in_note(lines)):
        following = lines[lineno + 1] if lineno + 1 < len(lines) else ""
        if LINK_MARK in following:
            continue
        cr = "\r" if lines[lineno].endswith("\r") else ""      # 見出しの行と同じ改行にそろえる
        new = [f"{LINK_MARK} [[{page_name(dai, no, round_no)}.excalidraw|途中式]]{cr}"]
        if following.strip():
            new.append(cr)                 # 選択肢のリストとのあいだを1行あける
        lines[lineno + 1:lineno + 1] = new
        added += 1
    if added:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(lines))
    return added


def generate(round_no, link=False):
    """-> {"new","migrate","ok"} の数・触らなかったページ・線を残したページ・リンクを入れた数"""
    counts = {"new": 0, "migrate": 0, "ok": 0}
    kept, migrated, linked = [], [], 0
    notes = note_paths(round_no)
    if not notes:
        sys.exit(f"解答ノートが見つからない: rounds/round{round_no}/*.md")
    for note_path in notes:
        note = os.path.splitext(os.path.basename(note_path))[0]
        folder = os.path.join(HANDWRITING, f"round{round_no}", note)
        for dai, no, dai_heading, sho_heading, _lineno in listed_in_note(read_lines(note_path)):
            os.makedirs(folder, exist_ok=True)
            name = page_name(dai, no, round_no)
            path = os.path.join(folder, name + ".excalidraw.md")
            action, detail = plan(path)
            if action == "keep":
                kept.append(f"{name}（{detail}）")
                continue
            counts[action] += 1
            if action == "ok":
                continue
            if action == "migrate":
                migrated.append(f"{name}（線 {sum(1 for e in detail if not e.get('isDeleted'))} 本を残した）")
            title = f"{dai_heading}　{sho_heading}"
            label = round_suffix(round_no).lstrip("_")
            if label:
                title += f"　{label}"
            content = build_md(title, back_link(note, dai_heading, sho_heading), detail)
            with open(path, "w", encoding="utf-8", newline="\n") as f:    # 中身を作り終えてから開く（途中で落ちて線を失わない）
                f.write(content)
        if link:
            linked += add_links(note_path, round_no)
    return counts, kept, migrated, linked


def main():
    ap = argparse.ArgumentParser(description="手書きページを解答ノートの小問ごとに作る")
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--link", action="store_true",
                    help="解答ノートの各小問に「✏️ 途中式」のリンクの行を入れる（無い小問だけ）")
    args = ap.parse_args()

    counts, kept, migrated, linked = generate(args.round, args.link)
    print(f"round{args.round}: 作り直し {counts['new']} / 線を残して差し替え {counts['migrate']} / "
          f"そのまま {counts['ok']} / 触らない {len(kept)}"
          + (f" / ノートに入れたリンク {linked}" if args.link else ""))
    for line in migrated + kept:
        print("  ", line)


if __name__ == "__main__":
    main()
