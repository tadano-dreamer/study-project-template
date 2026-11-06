#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一問一答の日替わりページ — 作る・読む・素材を検査する。

    python ledger/daily.py ingest                 # 解いたページを読み、recall-log.tsv に追記する
    python ledger/daily.py make                   # 今日のページを作る（既にあれば何もしない）
    python ledger/daily.py make --date 2026-10-02 --cards 15 --ruidai 6
    python ledger/daily.py show 2026-10-02        # その日のページの正誤とメモを出す（記録しない。解説を書くとき）
    python ledger/daily.py pace                   # 日付をまたいで解いた日が続いていないか（量を見直す合図）
    python ledger/daily.py check                  # 素材（一問一答/）と questions.tsv の食い違いを検査

形の正本は project-plan.md §4「一問一答の形」。
毎日の定期実行が ingest → make の順に呼ぶ（指示文は docs/一問一答の定期実行.md）。

★設計上の要点:
  1. User は pull → タップ → push しかしない（解いたあとの連絡はしない）。
     だから記録はページのタップから機械的に取る。タップの無い問いは記録しない。
  2. 既にあるページは上書きしない。途中まで解いたページを壊さないため。
  3. 「前回落とした → 復習の日が来た → まだ出していない」の順に出す。
     tally.build_queue の順（まだ出していないものが先）のままだと、
     問いを足すたびに落とした問いと復習が後ろへ押し出され、間隔反復が回らない。
  4. ページの冒頭に Claude の所見は書かない。統計検定2級では毎晩書いたが、読まれなかった。
     ✍️ メモへの返事は、間違えた問いの解説（materials/問題の解説/）の中に書く。
  5. 量が多すぎないかは、User の申告ではなく push の時刻で見る（pace）。
     「かかった時間」の欄は9日のうち1日しか書かれず、量を減らす合図にならなかった。
"""
import argparse
import datetime as _dt
import glob
import os
import re
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tally  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ONE_DIR = os.path.join(os.path.dirname(HERE), "一問一答")
SRC_DIRS = {"類題": os.path.join(ONE_DIR, "類題"), "暗記": os.path.join(ONE_DIR, "暗記カード")}
DAILY_DIR = os.path.join(ONE_DIR, "毎日")

# ★1日の量は、立ち上げのときに User と決めた数に書き換える（解説を読む時間まで含めて決める）。
#   統計検定2級では 暗記カード24枚＋類題12問で始め、30分の見込みを超えて、9日のうち7日は日付をまたいだ。
#   下は、それより少なめに置いた仮の値。
DEFAULT_CARDS, DEFAULT_RUIDAI = 15, 6
# 毎日、まだ出していない問いを最低これだけ入れる。
# 0 にすると復習だけで枠が埋まり、新しい問い（その日の誤答から作った類題）が何日も出ない
NEW_MIN = {"暗記": 6, "類題": 3}
LATE_STREAK = 2                         # 日付をまたいで解いた日がこれだけ続いたら、量を減らす案を出す
JST = _dt.timezone(_dt.timedelta(hours=9))
WEEKDAYS = "月火水木金土日"

BLOCK_HEAD = re.compile(r"^##\s+((?:類題|暗記)-\S+)\s*$")
CHOICE = re.compile(r"^-\s+([A-F])\s+(.*)$")
ANSWER_LINE = re.compile(r"\*\*正解\s*([A-F])\*\*")
PAGE_ID = re.compile(r"^%%id:\s*(\S+?)\s*%%\s*$")
CHECKED = re.compile(r"^\s*-\s+\[[xX]\]\s*(.*)$")
CHECKBOX = re.compile(r"^\s*-\s+\[[ xX]\]")
HEADING = re.compile(r"^#{1,6}\s")
MINUTES = re.compile(r"かかった時間\**\s*[:：]\s*(\d+)")
PAGE_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
MEMO_MARK = "✍️"
PASS_LINE = "⭕ の線"                    # 暗記カードの答えの頭に書く「ここまで言えたら⭕」の1行


# ---------------------------------------------------------------- 素材
def load_blocks():
    """一問一答/類題・暗記カード の素材 -> {id: {"kind","path","question","choices","callout"}}"""
    blocks = {}
    for kind, d in SRC_DIRS.items():
        for path in sorted(glob.glob(os.path.join(d, "*.md"))):
            cur, lines = None, []
            with open(path, "r", encoding="utf-8") as f:
                for raw in f.read().splitlines() + ["## 終わり"]:
                    if raw.startswith("## "):
                        if cur:
                            blocks[cur] = dict(split_block(lines), kind=kind, path=path)
                        m = BLOCK_HEAD.match(raw)
                        cur, lines = (m.group(1) if m else None), []
                        continue
                    if cur:
                        lines.append(raw)
    return blocks


def split_block(lines):
    """1問ぶんの行 -> 問い・選択肢・折りたたみ（解説 または 答え）"""
    question, choices, callout = [], [], []
    for line in lines:
        if callout or line.startswith("> [!"):
            callout.append(line)
            continue
        m = CHOICE.match(line)
        if m:
            choices.append((m.group(1), m.group(2)))
        elif not choices:
            question.append(line)
    return {"question": trim(question), "choices": choices, "callout": trim(callout)}


def trim(lines):
    """前後の空行と区切り線を落とす"""
    out = list(lines)
    while out and out[0].strip() in ("", "---"):
        out.pop(0)
    while out and out[-1].strip() in ("", "---"):
        out.pop()
    return out


def check():
    """素材と questions.tsv の食い違い -> [エラー文]。空なら問題なし。"""
    questions = tally.load_questions()
    blocks = load_blocks()
    errors = []
    for qid, q in questions.items():
        kind = tally.kind_of(q)
        if kind in SRC_DIRS and qid not in blocks:
            errors.append("`%s` は questions.tsv にあるのに、一問一答/ に素材がありません" % qid)
    for qid, b in blocks.items():
        q = questions.get(qid)
        if q is None:
            errors.append("`%s` は %s にあるのに、questions.tsv に行がありません" % (qid, rel(b["path"])))
            continue
        if b["kind"] != tally.kind_of(q):
            errors.append("`%s` の種類が食い違っています（素材は %s・台帳は %s）"
                          % (qid, b["kind"], tally.kind_of(q)))
        if not b["question"] or not b["callout"]:
            errors.append("`%s` に問いか折りたたみ（解説・答え）がありません" % qid)
        if b["kind"] == "暗記" and b["callout"] and PASS_LINE not in "".join(b["callout"][1:2]):
            errors.append("`%s` の答えの1行目に「%s」がありません（どこまで言えたら⭕かを、答えの頭に書く）"
                          % (qid, PASS_LINE))
        if b["kind"] == "類題":
            letters = [c for c, _t in b["choices"]]
            m = ANSWER_LINE.search("\n".join(b["callout"]))
            ans = q.get("正解", "")
            if ans not in letters:
                errors.append("`%s` の台帳の正解 %s が選択肢 %s にありません" % (qid, ans, "".join(letters)))
            if not m or m.group(1) != ans:
                errors.append("`%s` の解説の正解（%s）と台帳の正解（%s）が違います"
                              % (qid, m.group(1) if m else "なし", ans))
    return errors


def jst_today():
    """クラウドの定期実行は UTC で動くので、日付は日本時間で決める"""
    return _dt.datetime.now(JST).date()


def rel(path):
    return os.path.relpath(path, os.path.dirname(ONE_DIR)).replace("\\", "/")


# ---------------------------------------------------------------- 作る
def field_label(q):
    """"M 重回帰" -> "重回帰"（分野の記号は User に見せない）"""
    return re.sub(r"^[A-Z]\s+", "", q.get("分野", "")).strip()


def pick(questions, today, kind):
    """その日に出す順に id を並べる: 前回落とした → 復習の日が来た → まだ出していない。

    前回落とした問いは、まだ出していない🔴より先に出す（直した直後のテストが、元の誤りへの戻りを防ぐ）。
    まだ出していないものは、暗記カードは台帳の行の順（弱い分野から入れてある）、
    類題は演習（過去問など）の類題をプレテストの類題より先に、その中で4象限の順に分野を交互に
    （混ぜるとプレテストの類題が枠を取り、本番と同じ形の過去問の類題が出なかった）。
    プレテストの類題かどうかは、`出典` に「プレテスト」が入っているかで見る。"""
    queue, _sleeping, _w = tally.build_queue(questions, today, kind)
    hist = tally.load_recall()
    order = {qid: i for i, qid in enumerate(questions)}
    missed, review, new = [], [], []
    for row in queue:
        h = hist.get(row[2], [])
        if not h:
            new.append(row)
        elif not h[-1][1]:
            missed.append(row)
        else:
            review.append(row)

    def by_age(row):
        return (row[0], row[1], order[row[2]])     # 象限 → 長く出していない順 → 台帳の順

    if kind == "暗記":
        new = sorted(new, key=lambda row: order[row[2]])
    else:
        pretest = [row for row in new if "プレテスト" in row[3].get("出典", "")]
        new = interleave([row for row in new if row not in pretest]) + interleave(pretest)
    return ([row[2] for row in sorted(missed, key=by_age)],
            [row[2] for row in sorted(review, key=by_age)],
            [row[2] for row in new])


def compose(groups, limit, new_min):
    """(落とした, 復習, まだ出していない) から limit 個。まだ出していないものを最低 new_min 個は残す。
    復習だけで枠が埋まると、新しいものが何日も入らない（約100枚・1日30枚だと3日に1回しか入らない）。"""
    missed, review, new = groups
    reserve = min(new_min, len(new), limit)
    head = (missed + review)[:limit - reserve]
    return head + new[:limit - len(head)]


def interleave(rows):
    """優先度（score）が同じ行の中で、分野を交互に並べる。
    build_queue は同じ優先度なら id 順なので、そのままだと同じ分野が何問も続く。"""
    seen = {}
    keyed = []
    for pos, row in enumerate(rows):
        score, field = row[0], row[3].get("分野", "")
        rank = seen.get((score, field), 0)
        seen[(score, field)] = rank + 1
        keyed.append(((score, rank, pos), row))
    return [row for _k, row in sorted(keyed, key=lambda t: t[0])]


def render_item(n, qid, q, block):
    head = field_label(q)
    if block["kind"] == "類題":                    # 出典 `類題 プレテスト` -> （プレテストの類題）
        head += "（%sの類題）" % re.sub(r"^類題\s*", "", q.get("出典", "")).strip()
    out = ["### %d. %s" % (n, head), "%%%%id: %s%%%%" % qid, ""]
    out += block["question"] + [""]
    if block["kind"] == "類題":
        out += ["- [ ] %s %s" % (c, t) for c, t in block["choices"]]
        out += ["- [ ] 🤔 自信なし", "", "%s メモ:" % MEMO_MARK, "", ""]
        out += [retitle(block["callout"], "解説（答えてから開く）")]
        out += block["callout"][1:]
    else:
        out += ["%s メモ:" % MEMO_MARK, "", ""]
        out += [retitle(block["callout"], "答え（思い出してから開く）")]
        out += block["callout"][1:]
        out += ["", "- [ ] ⭕ 言えた", "- [ ] ❌ 言えなかった", "- [ ] 🤔 自信なし"]
    return out + [""]


def retitle(callout, title):
    """`> [!note]- 解説` の見出しだけを差し替える（種類と折りたたみは残す）"""
    m = re.match(r"^(>\s*\[![^\]]+\][-+]?)", callout[0])
    return "%s %s" % (m.group(1) if m else "> [!note]-", title)


def make_page(day, cards=DEFAULT_CARDS, ruidai=DEFAULT_RUIDAI, new_min=None):
    """day のページを作る -> パス。既にあれば作らずに None。
    new_min = まだ出していない問いを最低何個入れるか（既定は種類ごとの NEW_MIN）"""
    path = os.path.join(DAILY_DIR, "%s.md" % day.isoformat())
    if os.path.exists(path):
        return None
    questions = tally.load_questions()
    # 問いか折りたたみ（答え・解説）が欠けた素材は出さない。1問のせいで、その日のページが作れなくなるのを防ぐ
    # （欠けていることは check が知らせる）
    blocks = {qid: b for qid, b in load_blocks().items() if b["question"] and b["callout"]}

    def choose(kind, limit):
        groups = tuple([i for i in g if i in blocks] for g in pick(questions, day, kind))
        return compose(groups, limit, NEW_MIN[kind] if new_min is None else new_min)

    anki = choose("暗記", cards)
    rui = choose("類題", ruidai)

    lines = [
        "# 一問一答 %d/%d（%s）" % (day.month, day.day, WEEKDAYS[day.weekday()]),
        "",
        "> **暗記カード %d枚 → 類題 %d問** の順に。解いたら push するだけ（連絡は要りません）。"
        % (len(anki), len(rui)),
        "> - 暗記カード: 頭の中で答える → 「答え」を開く → ⭕ か ❌ をタップ（迷ったら 🤔 も）。"
        "答えの頭の「%s」まで言えていたら ⭕" % PASS_LINE,
        "> - 類題: 選択肢をタップ（迷ったら 🤔 も）→ 「解説」を開く",
        "> - %s には、何が分かって何が分からないかを、何でも書く（空欄でもよい）。"
        "間違えた問いの解説を書くときに、Claude が全部読む" % MEMO_MARK,
        "> - 全部できなくてもよい。タップした問いだけが記録される",
        "",
        "**かかった時間**: 分（書かなくてもよい）",
        "",
        "---",
        "",
        "## 暗記カード（%d枚）" % len(anki),
        "",
    ]
    if not anki:
        lines += ["（今日出す暗記カードはありません）", ""]
    for n, qid in enumerate(anki, 1):
        lines += render_item(n, qid, questions[qid], blocks[qid])
    lines += ["---", "", "## 類題（%d問）" % len(rui), ""]
    if not rui:
        lines += ["（今日出す類題はありません）", ""]
    for n, qid in enumerate(rui, 1):
        lines += render_item(n, qid, questions[qid], blocks[qid])

    os.makedirs(DAILY_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines).rstrip("\n") + "\n")
    return path


# ---------------------------------------------------------------- 読む
def parse_page(path):
    """ページ1枚 -> {"minutes", "items": [{"id","choice","choices","ok","ng","unsure","memo"}]}（ページ順）

    1問はページの上から「問い（類題は選択肢のタップ）→ ✍️ メモ → 折りたたみの答え → 暗記カードのタップ」。
    この順で読み、User が書いた文は場所によらずメモとして拾う:
      - ✍️ の行から答えの折りたたみ（`> [!`）までは、どんな行もメモ（`- [` や `---` や `#` で始まってもよい）
      - 折りたたみのあとの、タップでも見出しでも区切り線でもない行もメモ（答えを見てから書いた分）
    """
    items, cur, phase, minutes = [], None, None, None
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    for line in lines:
        m = MINUTES.search(line)
        if m and minutes is None:
            minutes = int(m.group(1))
        m = PAGE_ID.match(line)
        if m:
            cur = {"id": m.group(1), "choices": [], "ok": False, "ng": False,
                   "unsure": False, "memo": []}
            items.append(cur)
            phase = "question"
            continue
        if cur is None:
            continue
        if phase != "memo" and line.startswith(MEMO_MARK):
            phase = "memo"
            rest = re.sub(r"^メモ\s*[:：]?", "", line[len(MEMO_MARK):].strip()).strip()
            if rest:
                cur["memo"].append(rest)
            continue
        if phase == "memo":
            if line.startswith("> [!"):
                phase = "answer"
            elif line.strip():
                cur["memo"].append(line.rstrip())
            continue
        if phase == "answer":
            if line.startswith(">"):
                continue
            phase = "after"
        if read_tap(cur, line):
            continue
        if phase == "after" and line.strip() and line.strip() != "---" \
                and not HEADING.match(line) and not CHECKBOX.match(line):
            cur["memo"].append(line.rstrip())
    for it in items:
        it["memo"] = "\n".join(it["memo"])
        it["choice"] = it["choices"][0] if len(it["choices"]) == 1 else None
    return {"minutes": minutes, "items": items}


def read_tap(cur, line):
    """チェックの入ったタップ行なら cur に反映して True。中身が空のチェック行は読み飛ばす。"""
    m = CHECKED.match(line)
    if not m:
        return False
    body = m.group(1).strip()
    if not body:
        return True
    if body[0] in "ABCDEF" and (len(body) == 1 or not body[1].isalnum()):
        if body[0] not in cur["choices"]:
            cur["choices"].append(body[0])
    elif body.startswith("🤔"):
        cur["unsure"] = True
    elif body.startswith("⭕"):
        cur["ok"] = True
    elif body.startswith("❌"):
        cur["ng"] = True
    return True


def judge(item, q):
    """-> True / False / None（判定できない＝手を付けていない・選択肢を2つ以上タップした）"""
    if tally.kind_of(q) == "暗記":
        if item["ng"]:
            return False                         # ⭕ と ❌ の両方なら厳しいほう
        return True if item["ok"] else None
    if item["choice"] is None:
        return None
    return item["choice"] == q.get("正解")


def recorded_pairs():
    pairs = set()
    if not os.path.exists(tally.RECALL_LOG):
        return pairs
    with open(tally.RECALL_LOG, "r", encoding="utf-8") as f:
        for line in f.read().splitlines():
            c = line.split("\t")
            if len(c) >= 2 and c[0].strip() != "日付":
                pairs.add((c[0].strip()[:10], c[1].strip()))
    return pairs


def ingest(today=None):
    """毎日のページを全部読み、まだ記録していないタップを recall-log.tsv に追記する。
    -> 新しく記録したか、メモのあったページ [{"date","minutes","items":[…]}]（解説を書く材料）

    タップの無いメモだけの問いは記録に残らないので、毎回同じメモを出し直さないよう、
    前日と当日のページの分だけ材料に入れる。"""
    today = today or jst_today()
    recent = (today - _dt.timedelta(days=1)).isoformat()
    questions = tally.load_questions()
    done = recorded_pairs()
    rows, pages = [], []
    for path in sorted(glob.glob(os.path.join(DAILY_DIR, "*.md"))):
        m = PAGE_NAME.match(os.path.basename(path))
        if not m:
            continue
        date = m.group(1)
        page = parse_page(path)
        report = []
        for it in page["items"]:
            q = questions.get(it["id"])
            if q is None or (date, it["id"]) in done:
                continue
            result = judge(it, q)
            multi = len(it["choices"]) > 1          # 2つタップ: 採点せず、一覧に出して知らせる
            if result is None and not ((it["memo"] or multi) and date >= recent):
                continue
            kind = tally.kind_of(q)
            if result is not None:
                rows.append("%s\t%s\t%s\t%s\t%s" % (date, it["id"], "o" if result else "x",
                                                    "?" if it["unsure"] else "", kind))
            report.append(report_item(it, q, result))
        if report:
            pages.append({"date": date, "minutes": page["minutes"], "items": report})
    if rows:
        append_recall(rows)
    return pages


def report_item(it, q, result):
    return dict(it, result=result, kind=tally.kind_of(q), field=field_label(q),
                summary=q.get("メモ", ""), answer=q.get("正解", ""))


def show(day):
    """day のページの正誤とメモ -> {"date","minutes","items"}（ページが無ければ None）。
    recall-log.tsv には書かない。採点のセッションが一問一答の解説を書くときに使う
    （記録は定期実行の ingest に任せ、書く人を1人にする）。"""
    date = day.isoformat()
    path = os.path.join(DAILY_DIR, "%s.md" % date)
    if not os.path.exists(path):
        return None
    questions = tally.load_questions()
    page = parse_page(path)
    items = []
    for it in page["items"]:
        q = questions.get(it["id"])
        if q is None:
            continue
        result = judge(it, q)
        if result is None and not it["memo"] and len(it["choices"]) < 2:
            continue                                     # 手を付けていない問い
        items.append(report_item(it, q, result))
    return {"date": date, "minutes": page["minutes"], "items": items}


def git_out(args, cwd):
    """git の出力（前後の空白なし）。git が無い・失敗したときは None。"""
    try:
        r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    except OSError:
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def last_commit_time(path):
    """そのページに最後に書き込んだコミットの時刻（日本時間）。分からなければ None。

    - 書いた時刻（author date）で見る。コミットし直した時刻（committer date）は、定期実行の
      `pull --rebase` で後ろへずれ、その日のうちに解いた日まで「またいだ」に見える
    - 履歴を途中までしか持たない clone（shallow）では、どのファイルも同じコミットに見えるので、数えない
    - git の管理下に無い・まだコミットしていないページも None
    """
    cwd = os.path.dirname(os.path.abspath(path))
    if git_out(["rev-parse", "--is-shallow-repository"], cwd) != "false":
        return None
    stamp = git_out(["log", "-1", "--format=%aI", "--", path], cwd)
    if not stamp:
        return None
    try:
        return _dt.datetime.fromisoformat(stamp).astimezone(JST)
    except ValueError:
        return None


def pace(commit_time=last_commit_time):
    """日替わりページを、解かれた日で並べる -> ([{"date","tapped","total","minutes","at","late"}], 続いた日数)

    late = ページの日付より後の日に、最後の書き込みがコミットされた（日付をまたいで解いた）。
    続いた日数 = 解かれたページを新しい順に見て、late が続いている数。
    タップの無いページ（まだ解いていない）と、コミットの時刻が取れないページは数えない。
    あとから Claude がページを直すと、その日も「またいだ」に見える（解かれたページは書き換えないのが決まり）。
    """
    questions = tally.load_questions()
    rows = []
    for path in sorted(glob.glob(os.path.join(DAILY_DIR, "*.md"))):
        m = PAGE_NAME.match(os.path.basename(path))
        if not m:
            continue
        page = parse_page(path)
        known = [it for it in page["items"] if it["id"] in questions]
        tapped = sum(1 for it in known if judge(it, questions[it["id"]]) is not None)
        at = commit_time(path) if tapped else None
        late = bool(at) and at.date() > _dt.date.fromisoformat(m.group(1))
        rows.append({"date": m.group(1), "tapped": tapped, "total": len(known),
                     "minutes": page["minutes"], "at": at, "late": late})
    streak = 0
    for row in reversed([r for r in rows if r["tapped"] and r["at"]]):
        if not row["late"]:
            break
        streak += 1
    return rows, streak


def print_pace(rows, streak):
    if not rows:
        print("日替わりページがまだありません。")
        return
    print("| ページ | 解いた問い | 最後に書き込んだコミットの時刻 | 日付をまたいだか |")
    print("|---|---|---|---|")
    for r in rows:
        if not r["tapped"]:
            when, late = "—", "まだ解いていない"
        elif not r["at"]:
            when, late = "（git の履歴から分からない）", "分からない"
        else:
            when, late = r["at"].strftime("%m/%d %H:%M"), "またいだ" if r["late"] else "その日のうち"
        print("| %s | %d/%d | %s | %s |" % (r["date"], r["tapped"], r["total"], when, late))
    print()
    if any(r["tapped"] and not r["at"] for r in rows):
        print("⚠️ 書き込んだ時刻が取れないページがあります（まだコミットしていないか、履歴を途中までしか持たない clone）。"
              "clone なら `git fetch --unshallow` のあとで流し直す。下の日数には入っていません。")
    if streak >= LATE_STREAK:
        print("⚠️ 日付をまたいで解いた日が %d 日続いています。**1日の量を減らす案を User に出す**"
              "（量は計画なので、黙って減らさず、案を出して聞く）。" % streak)
    else:
        print("日付をまたいだ日は続いていません（%d 日）。" % streak)


def missing_ruidai(questions):
    """誤答（🔴誤解・⬜未学習）なのに、類題が1問も無い演習の問題 -> [id]（台帳の順）。
    演習を採点したセッションが、誤答ごとに類題を足す。足し忘れると日替わりページに出ないので、
    ingest の最後にこの一覧を出す（定期実行は報告に載せ、次の採点のセッションが足す）。"""
    covered = set()
    for q in questions.values():
        if tally.kind_of(q) == "類題":
            covered.update(s.strip() for s in (q.get("元問題") or "").split(",") if s.strip())
    latest = tally.latest_records(questions)
    wrong = (tally.WRONG_BELIEF, tally.UNLEARNED)
    return [qid for qid, q in questions.items()
            if qid in latest and latest[qid][2] in wrong
            and tally.kind_of(q) == "演習" and qid not in covered]


def append_recall(rows):
    """recall-log.tsv の末尾に行を足す。改行は、いまのファイルと同じものにそろえる（LF と CRLF を混ぜない）。"""
    text = ""
    if os.path.exists(tally.RECALL_LOG):
        with open(tally.RECALL_LOG, "r", encoding="utf-8", newline="") as f:
            text = f.read()
    eol = "\r\n" if "\r\n" in text else "\n"
    with open(tally.RECALL_LOG, "a", encoding="utf-8", newline="") as f:
        if not text:
            f.write("日付\tid\t結果\t確信\t形式" + eol)
        elif not text.endswith("\n"):
            f.write(eol)
        f.write(eol.join(rows) + eol)


def print_ingest(pages):
    """解いたページの正誤とメモの一覧（間違えた問いの解説を書く材料）。"""
    if not pages:
        print("新しく解かれたページはありません。")
        return
    for p in pages:
        done = [i for i in p["items"] if i["result"] is not None]
        ok = sum(1 for i in done if i["result"])
        print("## %s（かかった時間: %s 分）— 記録 %d問・正解 %d" % (
            p["date"], p["minutes"] if p["minutes"] is not None else "未記入", len(done), ok))
        for i in p["items"]:
            mark = {True: "⭕", False: "❌", None: "—"}[i["result"]]
            got = ""
            if i["kind"] == "類題" and len(i["choices"]) > 1:
                got = " 選択肢を %s の%dつタップ（採点せず記録していない）" % ("・".join(i["choices"]), len(i["choices"]))
            elif i["kind"] == "類題" and i["choice"]:
                got = " 選んだ %s／正解 %s" % (i["choice"], i["answer"])
            print("- %s%s `%s`（%s・%s）%s%s" % (mark, "🤔" if i["unsure"] else "", i["id"], i["kind"],
                                               i["field"], i["summary"], got))
            if i["memo"]:
                for line in i["memo"].splitlines():
                    print("    ✍️ %s" % line)
        print()


def main():
    ap = argparse.ArgumentParser(description="一問一答の日替わりページ")
    sub = ap.add_subparsers(dest="cmd", required=True)
    mk = sub.add_parser("make", help="今日のページを作る（既にあれば何もしない）")
    mk.add_argument("--date", type=_dt.date.fromisoformat, help="既定は今日（日本時間）")
    mk.add_argument("--cards", type=int, default=DEFAULT_CARDS, help="暗記カードの枚数")
    mk.add_argument("--ruidai", type=int, default=DEFAULT_RUIDAI, help="類題の問数")
    mk.add_argument("--new-min", type=int,
                    help="まだ出していない問いを最低何個入れるか（既定は 暗記%d・類題%d）"
                    % (NEW_MIN["暗記"], NEW_MIN["類題"]))
    sub.add_parser("ingest", help="解いたページを読み、recall-log.tsv に追記する")
    sh = sub.add_parser("show", help="その日のページの正誤とメモを出す（記録しない。一問一答の解説を書くとき用）")
    sh.add_argument("date", type=_dt.date.fromisoformat, help="ページの日付（例 2026-10-04）")
    sub.add_parser("pace", help="日付をまたいで解いた日が続いていないかを見る（量を見直す合図）")
    sub.add_parser("check", help="素材と questions.tsv の食い違いを検査する")
    args = ap.parse_args()

    if args.cmd == "pace":
        print_pace(*pace())
        return

    if args.cmd == "show":
        page = show(args.date)
        if page is None:
            print("ページがありません: %s.md" % args.date.isoformat())
            sys.exit(1)
        if not page["items"]:
            print("%s のページはありますが、まだ解かれていません（タップもメモもありません）。" % page["date"])
            return
        print_ingest([page])
        return

    if args.cmd == "check":
        errors = check()
        for e in errors:
            print("- " + e)
        print("問題なし" if not errors else "食い違い %d 件" % len(errors))
        sys.exit(1 if errors else 0)
    if args.cmd == "ingest":
        print_ingest(ingest())
        questions = tally.load_questions()
        missing = missing_ruidai(questions)
        if missing:
            print("## 類題がまだ無い誤答 %d 件（採点したセッションで足す）" % len(missing))
            for qid in missing:
                print("- `%s`（%s）%s" % (qid, field_label(questions[qid]), questions[qid].get("メモ", "")))
        else:
            print("類題がまだ無い誤答はありません。")
        return
    day = args.date or jst_today()
    path = make_page(day, args.cards, args.ruidai, args.new_min)
    print("作成: %s" % rel(path) if path else "既にあります: %s.md（何もしていません）" % day.isoformat())


if __name__ == "__main__":
    main()
