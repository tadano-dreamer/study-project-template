#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""演習台帳の集計 — 正誤 × 確信 の4象限で診断する。

    python tally.py                  # 最新周回のサマリ
    python tally.py --round 2        # 周回を指定
    python tally.py --compare 1 2    # 周回比較(逆行率・転換率)
    python tally.py --drill          # recall-drill 用の優先復習リストだけ出す
    python tally.py --detail         # 大問別の明細も出す

前提ファイル (同じフォルダ):
    questions.tsv        問題マスタ。id / 分野 / 型 / 正解 / 出典 / メモ
    rounds/round<N>/**/*.md  解答ノート。人がタップするのはここだけ

★設計上の要点 (E資格 16週間の反省より):
  1. **正誤は自動判定**する。人がタップするのは「選んだ肢」と「🤔自信なし」だけ。
     E資格では ❌ を手でタップさせていたため付け忘れが 27問発生し、判定の正本を
     途中で「解説の貼付有無」へ切り替える是正が必要になった。肢さえ取れば正誤は導ける。
  2. **確信度は1問目から取る**。E資格では「たまたま正解」を演習の終盤に導入したため、
     1周目の⭕がどれだけ本物だったか分からず、逆行率36%という事後指標でしか気づけなかった。
  3. **どの誤答肢を選んだかを残す**。これは「あなた個人の混同ペア」であり、
     教材から一般論を作る道具(NotebookLM 等)には決して作れないデータ。
"""
import argparse
import collections
import datetime as _dt
import glob
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
QUESTIONS = os.path.join(HERE, "questions.tsv")
ROUNDS_DIR = os.path.join(HERE, "rounds")
RECALL_LOG = os.path.join(HERE, "recall-log.tsv")

DAIMON = re.compile(r"^##\s+(\S+)\s*(.*)$")
SHOMON = re.compile(r"^###\s+\((\d+)\)")
CHECKED = re.compile(r"^\s*-\s+\[[xX]\]\s+(.*)$")

# 4象限
FIXED, LUCKY, WRONG_BELIEF, UNLEARNED = "✅定着", "⚠️たまたま正解", "🔴誤解", "⬜未学習"
QUADRANT_ORDER = [WRONG_BELIEF, LUCKY, UNLEARNED, FIXED]


# ---------------------------------------------------------------- 問題マスタ
def load_questions():
    """questions.tsv -> {id: {"分野","型","正解","出典","メモ"}}"""
    if not os.path.exists(QUESTIONS):
        sys.exit(f"問題マスタがありません: {QUESTIONS}\n"
                 f"  → drill-ledger スキルで生成してください。")
    out, header = {}, None
    with open(QUESTIONS, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            cells = line.split("\t")
            if header is None:
                header = [c.strip() for c in cells]
                continue
            row = dict(zip(header, [c.strip() for c in cells]))
            if row.get("id"):
                out[row["id"]] = row
    return out


# ---------------------------------------------------------------- 解答ノート
def parse_note(path):
    """1ファイルを読む -> {小問id: {"choice","unsure","manual_wrong"}}"""
    subs, dai, cur = {}, None, None
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            m = DAIMON.match(line)
            if m:
                dai, cur = m.group(1), None
                continue
            m = SHOMON.match(line)
            if m and dai:
                cur = f"{dai}-{m.group(1)}"
                subs.setdefault(cur, {"choice": None, "unsure": False,
                                      "manual_wrong": False, "manual_right": False})
                continue
            m = CHECKED.match(line)
            if not (m and cur):
                continue
            body = m.group(1).strip()
            if not body:
                continue
            if body[0] in "ABCDEF" and (len(body) == 1 or not body[1].isalnum()):
                subs[cur]["choice"] = body[0]
            elif body.startswith("🤔"):
                subs[cur]["unsure"] = True
            elif body.startswith("❌"):
                subs[cur]["manual_wrong"] = True
            elif body.startswith("⭕") or body.startswith("○"):
                subs[cur]["manual_right"] = True
    return subs


def load_round(n):
    """周回 n の全ノートを読む -> {小問id: 記録}"""
    patterns = [
        os.path.join(ROUNDS_DIR, f"round{n}", "**", "*.md"),
        os.path.join(ROUNDS_DIR, f"round{n}.md"),
    ]
    files = []
    for p in patterns:
        files.extend(glob.glob(p, recursive=True))
    merged = {}
    for path in sorted(files):
        merged.update(parse_note(path))
    return merged, files


def available_rounds():
    found = set()
    for p in glob.glob(os.path.join(ROUNDS_DIR, "round*")):
        m = re.search(r"round(\d+)", os.path.basename(p))
        if m:
            found.add(int(m.group(1)))
    return sorted(found)


# ---------------------------------------------------------------- 判定
def judge(rec, q):
    """記録と問題マスタから (着手したか, 正解か, 象限) を返す。

    正誤は原則ここで**自動判定**する。選択肢のない問題(記述・計算)だけ手動の ❌ を見る。
    """
    answer = (q.get("正解") or "").strip()
    choice = rec.get("choice")
    manual = rec.get("manual_wrong")

    if answer and answer not in ("-", "—", ""):
        if not choice:
            return False, None, None            # 未着手
        correct = (choice == answer)
    else:
        # 選択肢のない問題(記述・計算)は ⭕ か ❌ のどちらかを必ずタップさせる。
        # 「無印＝正解」にすると未着手と区別がつかず、静かに取りこぼす。
        right = rec.get("manual_right")
        if not (manual or right):
            return False, None, None            # 未着手
        correct = bool(right) and not manual

    unsure = rec.get("unsure", False)
    if correct:
        return True, True, (LUCKY if unsure else FIXED)
    return True, False, (UNLEARNED if unsure else WRONG_BELIEF)


def collect(rnd, questions):
    """周回の記録 -> [(id, q, rec, correct, quadrant)] 着手済みのみ"""
    rows = []
    for qid, rec in rnd.items():
        q = questions.get(qid)
        if q is None:
            continue
        attempted, correct, quad = judge(rec, q)
        if attempted:
            rows.append((qid, q, rec, correct, quad))
    return rows


# ---------------------------------------------------------------- 出力
def pct(a, b):
    return f"{a / b * 100:.0f}%" if b else "—"


def report(n, questions, detail=False):
    rnd, files = load_round(n)
    if not files:
        sys.exit(f"{n}周目の解答ノートが見つかりません: {ROUNDS_DIR}/round{n}/")
    rows = collect(rnd, questions)
    total, att = len(questions), len(rows)
    cor = sum(1 for r in rows if r[3])

    print(f"# {n}周目 集計\n")
    print(f"- 着手 **{att}/{total} 問** ({pct(att, total)}) ｜ "
          f"正解 **{cor}/{att} = {pct(cor, att)}**  ({len(files)} ファイル)\n")

    # --- 4象限 ---
    quads = collections.Counter(r[4] for r in rows)
    print("## 正誤 × 確信 の4象限\n")
    print("| | 確信あり | 確信なし(🤔) |")
    print("|---|---|---|")
    print(f"| **正解** | {FIXED} {quads[FIXED]} | {LUCKY} **{quads[LUCKY]}** |")
    print(f"| **不正解** | {WRONG_BELIEF} **{quads[WRONG_BELIEF]}** | {UNLEARNED} {quads[UNLEARNED]} |")
    print()
    print(f"> {WRONG_BELIEF} = 自信を持って間違えた。**知識そのものが間違っている**ので、概念の再学習が要る。")
    print(f"> {LUCKY} = 当たったが根拠がない。**本番で落とす最有力候補**。根拠の言語化が要る。")
    print(f"> この2つは処方が違う。不正解だけを追うと {LUCKY} が丸ごと見えなくなる。\n")

    if att:
        real = quads[FIXED] / att * 100
        print(f"- ★**確信のある正解 = {quads[FIXED]}/{att} = {real:.1f}%** "
              f"（見かけの正答率 {pct(cor, att)} との差が、そのまま実力の水増し分）\n")

    # --- 分野別 ---
    by_field = collections.defaultdict(lambda: [0, 0, 0])   # 着手/正解/定着
    for _qid, q, _rec, correct, quad in rows:
        f = q.get("分野") or "(未分類)"
        by_field[f][0] += 1
        by_field[f][1] += bool(correct)
        by_field[f][2] += (quad == FIXED)
    print("## 分野別\n")
    print("| 分野 | 着手 | 正解 | 正答率 | 確信ある正解 |")
    print("|---|---|---|---|---|")
    for f in sorted(by_field, key=lambda k: by_field[k][1] / max(by_field[k][0], 1)):
        a, c, s = by_field[f]
        print(f"| {f} | {a} | {c} | **{pct(c, a)}** | {pct(s, a)} |")
    print()

    # --- 設問タイプ別（設問反転の検出）---
    by_type = collections.defaultdict(lambda: [0, 0])
    for _qid, q, _rec, correct, _quad in rows:
        t = q.get("型") or "選択"
        by_type[t][0] += 1
        by_type[t][1] += bool(correct)
    if len(by_type) > 1:
        print("## 設問タイプ別\n")
        print("| 型 | 着手 | 正解 | 正答率 |")
        print("|---|---|---|---|")
        for t in sorted(by_type):
            a, c = by_type[t]
            print(f"| {t} | {a} | {c} | **{pct(c, a)}** |")
        inv = by_type.get("誤選択")
        oth = by_type.get("選択")
        if inv and oth and inv[0] >= 3:
            gap = (oth[1] / oth[0] - inv[1] / inv[0]) * 100
            if gap >= 10:
                print(f"\n> ⚠️ **設問反転で {gap:.0f}pt 落としている**"
                      f"（「最も不適切なものを選べ」型だけ正答率が低い）。"
                      f"これは知識ではなく読み方の問題。**注意ではなく手順で潰す。**")
        print()

    # --- 混同ペア（誤答肢の集計）---
    confusions = collections.Counter()
    for qid, q, rec, correct, _quad in rows:
        if correct or not rec.get("choice"):
            continue
        ans = (q.get("正解") or "").strip()
        if ans and ans not in ("-", "—"):
            confusions[(q.get("分野") or "(未分類)", ans, rec["choice"])] += 1
    if confusions:
        print("## 混同パターン（正解 → 実際に選んだ肢）\n")
        print("> ★これは**あなた個人が実際に取り違えたペア**。教材の一般論ではないので、")
        print("> ここから作る対比表が一番効く。recall-drill はこれを起点に出題する。\n")
        for (field, ans, got), cnt in confusions.most_common(12):
            print(f"- {field}: 正解 `{ans}` → `{got}` を選択 … {cnt}件")
        print()

    if detail:
        print("## 大問別\n")
        by_dai = collections.defaultdict(lambda: [0, 0])
        for qid, _q, _rec, correct, _quad in rows:
            dai = qid.rsplit("-", 1)[0]
            by_dai[dai][0] += 1
            by_dai[dai][1] += bool(correct)
        print("| 大問 | 着手 | 正解 | 正答率 |")
        print("|---|---|---|---|")
        for dai in sorted(by_dai):
            a, c = by_dai[dai]
            print(f"| `{dai}` | {a} | {c} | {pct(c, a)} |")
        print()

    return rows


def compare(a, b, questions):
    ra = collect(load_round(a)[0], questions)
    rb = collect(load_round(b)[0], questions)
    if not ra or not rb:
        sys.exit(f"周回 {a} と {b} の両方に記録が必要です。")
    pa = {qid: correct for qid, _q, _r, correct, _quad in ra}
    pb = {qid: correct for qid, _q, _r, correct, _quad in rb}
    both = set(pa) & set(pb)
    if not both:
        sys.exit("2つの周回に共通する着手済みの問題がありません。")

    regress = [q for q in both if pa[q] and not pb[q]]      # 逆行 ⭕→❌
    convert = [q for q in both if not pa[q] and pb[q]]      # 転換 ❌→⭕
    kept = [q for q in both if pa[q] and pb[q]]
    stuck = [q for q in both if not pa[q] and not pb[q]]

    print(f"# {a}周目 → {b}周目 の比較（共通 {len(both)} 問）\n")
    print(f"- ✅ 維持 (⭕→⭕): {len(kept)}")
    print(f"- 🆙 **転換 (❌→⭕): {len(convert)}/{len(convert) + len(stuck)} "
          f"= {pct(len(convert), len(convert) + len(stuck))}** ← 復習が効いた度合い")
    print(f"- ⚠️ **逆行 (⭕→❌): {len(regress)}/{len(kept) + len(regress)} "
          f"= {pct(len(regress), len(kept) + len(regress))}** ← 前周の⭕がどれだけ本物だったか")
    print(f"- 🔴 残存 (❌→❌): {len(stuck)}\n")

    if regress:
        lucky_before = 0
        rec_a = load_round(a)[0]
        for q in regress:
            if rec_a.get(q, {}).get("unsure"):
                lucky_before += 1
        print(f"> 逆行 {len(regress)}問のうち **{lucky_before}問は前周で🤔が付いていた** "
              f"= まぐれ当たりが剥がれただけ。残り {len(regress) - lucky_before}問が本当の忘却。\n")
        if lucky_before / max(len(regress), 1) > 0.5:
            print("> ★逆行の過半が「まぐれの剥落」なら、処方は**復習間隔ではなく初回測定の精度**。"
                  "解き直しを増やしても直らない。\n")
        print("### 逆行した問題\n")
        for q in sorted(regress)[:40]:
            qq = questions.get(q, {})
            print(f"- `{q}` {qq.get('分野', '')} {qq.get('メモ', '')}")


def drill_list(n, questions, limit=30):
    """recall-drill 用: 優先度順の復習リスト。"""
    rows = collect(load_round(n)[0], questions)
    if not rows:
        sys.exit(f"{n}周目の記録がありません。")
    rank = {WRONG_BELIEF: 0, LUCKY: 1, UNLEARNED: 2, FIXED: 3}
    rows.sort(key=lambda r: (rank[r[4]], r[0]))
    print(f"# 優先復習リスト（{n}周目 / 上位{limit}件）\n")
    print("> 順序 = 🔴誤解 → ⚠️たまたま正解 → ⬜未学習 → ✅定着(間隔反復)\n")
    for qid, q, rec, _correct, quad in rows[:limit]:
        got = rec.get("choice") or "-"
        ans = q.get("正解") or "-"
        note = f"（`{ans}` を `{got}` と取り違え）" if quad == WRONG_BELIEF and got != "-" else ""
        print(f"- {quad} `{qid}` {q.get('分野', '')} {q.get('メモ', '')} {note}")



# ------------------------------------------------- 想起テスト履歴 (recall-drill)
def load_recall():
    """recall-log.tsv -> {id: [(date, correct, unsure), ...]} 古い順。

    書式(TSV): 日付 / id / 結果(o|x) / 確信(? = 自信なし・空 = 確信あり) / 形式
    recall-drill スキルが1行ずつ追記する。
    ★ここが「フラッシュカードの正答率」の置き場所。E資格ではこれが取れず、
      どのカードが定着したのか最後まで分からなかった。
    """
    hist = collections.defaultdict(list)
    if not os.path.exists(RECALL_LOG):
        return hist
    with open(RECALL_LOG, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#") or line.startswith("日付"):
                continue
            c = line.split("\t")
            if len(c) < 3:
                continue
            try:
                d = _dt.date.fromisoformat(c[0].strip()[:10])
            except ValueError:
                continue
            hist[c[1].strip()].append(
                (d,
                 c[2].strip().lower() in ("o", "⭕", "○", "1", "ok"),
                 (c[3].strip() if len(c) > 3 else "") in ("?", "🤔"))
            )
    for k in hist:
        hist[k].sort(key=lambda t: t[0])
    return hist


def next_queue(n, questions, limit=20, today=None):
    """出題キュー = 台帳の4象限 + 想起テスト履歴（間隔反復）。

    教材から等確率に出す道具（NotebookLM 等）に対して、ここは
      ① あなたが実際に間違えた問題を優先し
      ② 連続正解した問題は指数的に間隔を空けて寝かせ
      ③ 想起テストで落ちた問題は象限を1段昇格させる
    という個人化をかける。これが自作側の存在理由。
    """
    today = today or _dt.date.today()
    rows = collect(load_round(n)[0], questions)
    hist = load_recall()
    base = {WRONG_BELIEF: 0, LUCKY: 1, UNLEARNED: 2, FIXED: 3}

    queue, sleeping = [], 0
    for qid, q, rec, _correct, quad in rows:
        h = hist.get(qid, [])
        score = base[quad]

        # 直近の想起テストで落ちていれば1段昇格（最優先へ寄せる）
        if h and not h[-1][1]:
            score = max(0, score - 1)

        # 確信ある連続正解の数だけ間隔を空ける: 1, 2, 4, 8, 16, 32 日
        streak = 0
        for _d, ok, unsure in reversed(h):
            if ok and not unsure:
                streak += 1
            else:
                break
        if streak:
            gap = 2 ** (streak - 1)
            if (today - h[-1][0]).days < gap:
                sleeping += 1
                continue                      # まだ寝かせる
            score += 0.5                      # 復習期は来たが、誤答より優先度は下

        last = h[-1][0] if h else None
        idle = (today - last).days if last else 999
        queue.append((score, -idle, qid, q, quad, len(h), last))

    queue.sort(key=lambda t: (t[0], t[1]))
    print("# 次に出す問題（%d周目の台帳 + 想起テスト履歴 / 上位%d件）\n" % (n, limit))
    print("- 出題候補 %d 問 ｜ 間隔反復で寝かせ中 %d 問 ｜ 基準日 %s\n"
          % (len(queue), sleeping, today))
    print("| 優先 | id | 分野 | 象限 | 想起回数 | 最終 | メモ |")
    print("|---|---|---|---|---|---|---|")
    for i, (_s, _idle, qid, q, quad, cnt, last) in enumerate(queue[:limit], 1):
        print("| %d | `%s` | %s | %s | %d | %s | %s |"
              % (i, qid, q.get("分野", ""), quad, cnt, last or "—", q.get("メモ", "")))
    if not queue:
        print("\n> 出題候補がありません（全問が間隔反復の待機中）。次の復習日まで空けてよい状態です。")


def recall_summary():
    """想起テストの通算成績。E資格でこれが取れなかったのが最大の穴だった。"""
    hist = load_recall()
    if not hist:
        sys.exit("想起テストの記録がありません: %s\n"
                 "  → recall-drill スキルを実行すると追記されます。" % RECALL_LOG)
    flat = [(qid, d, ok, un) for qid, h in hist.items() for (d, ok, un) in h]
    total = len(flat)
    ok = sum(1 for _q, _d, o, _u in flat if o)
    sure_ok = sum(1 for _q, _d, o, u in flat if o and not u)
    by_day = collections.defaultdict(lambda: [0, 0])
    for _q, d, o, _u in flat:
        by_day[d][0] += 1
        by_day[d][1] += bool(o)

    print("# 想起テスト 通算\n")
    print("- 出題 **%d 回** / 対象 %d 問 ｜ 正解 **%d = %s** ｜ 確信ある正解 %d = %s\n"
          % (total, len(hist), ok, pct(ok, total), sure_ok, pct(sure_ok, total)))
    print("## 日付別\n")
    print("| 日付 | 出題 | 正解 | 正答率 |")
    print("|---|---|---|---|")
    for d in sorted(by_day):
        a, c = by_day[d]
        print("| %s | %d | %d | %s |" % (d, a, c, pct(c, a)))

    hard = sorted(((qid, sum(1 for _d, o, _u in h if not o), len(h))
                   for qid, h in hist.items()), key=lambda t: (-t[1], t[0]))
    hard = [x for x in hard if x[1] >= 2]
    if hard:
        print("\n## 何度出しても落ちる問題（2回以上ミス）\n")
        print("> ここは出題を増やしても直らない。**教材側を作り直す**か、"
              "捨てる判断をする対象。\n")
        for qid, miss, cnt in hard[:15]:
            print("- `%s` … %d/%d ミス" % (qid, miss, cnt))


def main():
    ap = argparse.ArgumentParser(description="演習台帳の集計")
    ap.add_argument("--round", type=int, help="集計する周回 (既定=最新)")
    ap.add_argument("--compare", nargs=2, type=int, metavar=("A", "B"), help="周回比較")
    ap.add_argument("--drill", action="store_true", help="優先復習リストだけ出す")
    ap.add_argument("--detail", action="store_true", help="大問別の明細も出す")
    ap.add_argument("--next", type=int, nargs="?", const=20, metavar="N",
                    help="次に出す問題のキュー(想起テスト履歴と間隔反復を加味)")
    ap.add_argument("--recall", action="store_true", help="想起テストの通算成績")
    args = ap.parse_args()

    questions = load_questions()
    rounds = available_rounds()
    if not rounds:
        sys.exit(f"解答ノートがありません: {ROUNDS_DIR}/round1/")

    if args.recall:
        recall_summary()
        return
    if args.compare:
        compare(args.compare[0], args.compare[1], questions)
        return
    # 注意: --round 0 (プレテスト) は 0 が偽値なので `or` で書くと最新周回に落ちる。
    n = args.round if args.round is not None else rounds[-1]
    if args.next:
        next_queue(n, questions, limit=args.next)
        return
    if args.drill:
        drill_list(n, questions)
        return
    report(n, questions, detail=args.detail)


if __name__ == "__main__":
    main()
