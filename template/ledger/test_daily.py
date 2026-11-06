#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""daily.py（一問一答の日替わりページ）のテスト。

    python ledger/test_daily.py

固定すること:
  - ページに出す数・順番（暗記カードは 落とした → 復習期 → 新しいカード）
  - 既にあるページは上書きしない（User が途中まで解いたページを壊さない）
  - タップとメモを読み、recall-log.tsv に1回だけ追記する
  - 素材（一問一答/）と questions.tsv の食い違いを検査で見つける
  - ページの冒頭に Claude の所見を置かない（統計検定2級で読まれなかった）
  - 暗記カードの答えに「⭕ の線」が無ければ検査で見つける
  - 日付をまたいで解いた日が続いたら、量を見直す合図を出す
"""
import datetime as _dt
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daily  # noqa: E402
import tally  # noqa: E402

HEADER = "id\t分野\t型\t正解\t出典\tメモ\t元問題\n"
QUESTIONS = [
    "P-01-1\tM 重回帰\t選択\tD\tプレテスト\t調整済みR²\t",
    "類題-重回帰-01\tM 重回帰\t選択\tC\t類題 プレテスト\t式を選ぶ\tP-01-1",
    "類題-重回帰-02\tM 重回帰\t誤選択\tB\t類題 プレテスト\t誤りを選ぶ\tP-01-1",
    "暗記-重回帰-01\tM 重回帰\t暗記\t-\t暗記カード 重回帰\tR²の式\t",
    "暗記-重回帰-02\tM 重回帰\t暗記\t-\t暗記カード 重回帰\t調整済みの式\t",
    "暗記-推定-01\tJ 推定\t暗記\t-\t暗記カード 推定\t不偏分散\t",
]
RUIDAI = """# 類題 — テスト

---

## 類題-重回帰-01

式はどれ？
二行目の問い。

- A $1 - 0.4$
- B $0.6$
- C $1 - 0.4 \\times \\dfrac{19}{16}$

> [!note]- 解説
> **正解 C**。大きいほうが上。

## 類題-重回帰-02

【誤っているものを選べ】

- A 正しい記述
- B 誤った記述

> [!note]- 解説
> **正解 B**（誤っているもの）。
"""
ANKI_M = """# 暗記カード — 重回帰

## 暗記-重回帰-01

決定係数 $R^2$ の式は？

> [!success]- 答え
> **⭕ の線**: 「1 から、残差の割合を引く」が言えたら⭕
> $1 - S_e / S_T$

## 暗記-重回帰-02

自由度調整済み決定係数の式は？

> [!success]- 答え
> **⭕ の線**: 自由度の比を掛けることが言えたら⭕
> $1 - (1-R^2)\\dfrac{n-1}{n-p-1}$
"""
ANKI_J = """# 暗記カード — 推定

## 暗記-推定-01

不偏分散は何で割る？

> [!success]- 答え
> **⭕ の線**: 「n − 1」が言えたら⭕
> $n-1$
"""
PRETEST = """## P-01 テスト

### (1)
- [ ] A
- [ ] B
- [ ] C
- [ ] D
- [x] E
- [ ] 🤔 自信なし
"""
TODAY = _dt.date(2026, 10, 2)


def remove_tree(path):
    """一時フォルダを消す。git が作る読み取り専用のファイル（.git/objects）は、Windows では先に書き込み可にする。"""
    for root, _dirs, files in os.walk(path):
        for name in files:
            os.chmod(os.path.join(root, name), stat.S_IWRITE | stat.S_IREAD)
    shutil.rmtree(path)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def tap(text, qid, label):
    """ページ本文の qid の問いの中で、`- [ ] {label}` を `- [x] {label}` にする。"""
    start = text.index("%%id: %s%%" % qid)
    pos = text.index("- [ ] " + label, start)
    return text[:pos] + "- [x] " + label + text[pos + len("- [ ] " + label):]


def memo(text, qid, body):
    """qid の問いの ✍️ の行の次に、メモを書き込む。"""
    start = text.index("%%id: %s%%" % qid)
    pos = text.index("✍️", start)
    end = text.index("\n", pos) + 1
    return text[:end] + body + "\n" + text[end:]


class DailyCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.saved_t = (tally.QUESTIONS, tally.ROUNDS_DIR, tally.RECALL_LOG)
        self.saved_d = (daily.ONE_DIR, daily.SRC_DIRS, daily.DAILY_DIR)
        tally.QUESTIONS = os.path.join(self.dir, "questions.tsv")
        tally.ROUNDS_DIR = os.path.join(self.dir, "rounds")
        tally.RECALL_LOG = os.path.join(self.dir, "recall-log.tsv")
        one = os.path.join(self.dir, "一問一答")
        daily.ONE_DIR = one
        daily.SRC_DIRS = {"類題": os.path.join(one, "類題"), "暗記": os.path.join(one, "暗記カード")}
        daily.DAILY_DIR = os.path.join(one, "毎日")
        write(tally.QUESTIONS, HEADER + "\n".join(QUESTIONS) + "\n")
        write(os.path.join(tally.ROUNDS_DIR, "round0", "プレテスト.md"), PRETEST)
        write(tally.RECALL_LOG, "日付\tid\t結果\t確信\t形式\n")
        write(os.path.join(one, "類題", "プレテスト.md"), RUIDAI)
        write(os.path.join(one, "暗記カード", "重回帰.md"), ANKI_M)
        write(os.path.join(one, "暗記カード", "推定.md"), ANKI_J)

    def tearDown(self):
        tally.QUESTIONS, tally.ROUNDS_DIR, tally.RECALL_LOG = self.saved_t
        daily.ONE_DIR, daily.SRC_DIRS, daily.DAILY_DIR = self.saved_d
        remove_tree(self.dir)

    def recall(self, rows):
        write(tally.RECALL_LOG, "日付\tid\t結果\t確信\t形式\n" + "".join(r + "\n" for r in rows))

    def ids_in(self, text):
        return [line[len("%%id: "):-2] for line in text.splitlines() if line.startswith("%%id: ")]

    # ------------------------------------------------------------ 作る
    def test_make_page_has_both_kinds_with_memo_and_taps(self):
        path = daily.make_page(TODAY, cards=10, ruidai=10)
        self.assertTrue(path.endswith("2026-10-02.md"))
        text = read(path)
        self.assertEqual(sorted(self.ids_in(text)), sorted(
            ["類題-重回帰-01", "類題-重回帰-02", "暗記-重回帰-01", "暗記-重回帰-02", "暗記-推定-01"]))
        self.assertEqual(sum(1 for l in text.splitlines() if l.startswith("✍️ メモ")), 5)
        self.assertIn("- [ ] C $1 - 0.4", text)          # 選択肢はタップできる形に
        self.assertIn("- [ ] ⭕", text)
        self.assertIn("- [ ] ❌", text)
        self.assertIn("二行目の問い。", text)            # 問いは複数行でも全部出す
        self.assertIn("### 1. 重回帰", text)             # 分野の記号（M）は出さない
        self.assertNotIn("M 重回帰", text)
        self.assertIn("**かかった時間**", text)
        # 暗記カードが類題より先
        self.assertLess(text.index("暗記-"), text.index("類題-"))

    def test_ruidai_heading_shows_where_it_came_from(self):
        text = read(daily.make_page(TODAY, cards=1, ruidai=1))
        self.assertIn("### 1. 重回帰（プレテストの類題）", text)

    def test_wrong_answers_without_ruidai_are_listed(self):
        """誤答（🔴・⬜）なのに類題が無い問題を出す（朝の過去問の類題の足し忘れに気づくため）。"""
        self.assertEqual(daily.missing_ruidai(tally.load_questions()), [])   # P-01 は類題あり
        write(tally.QUESTIONS, read(tally.QUESTIONS)
              + "2015-11-1\tB 1変数の要約\t選択\tB\t問題集 p.1\t\t\n"
              + "2015-11-2\tB 1変数の要約\t選択\tC\t問題集 p.1\t\t\n")
        write(os.path.join(tally.ROUNDS_DIR, "round1", "2015年11月.md"),
              "## 2015-11 テスト\n\n### (1)\n- [x] A\n- [ ] 🤔 自信なし\n\n"
              "### (2)\n- [x] C\n- [x] 🤔 自信なし\n")
        self.assertEqual(daily.missing_ruidai(tally.load_questions()), ["2015-11-1"])  # (2) は正解

    def test_make_does_not_overwrite(self):
        path = daily.make_page(TODAY, cards=10, ruidai=10)
        write(path, "途中まで解いた")
        self.assertIsNone(daily.make_page(TODAY, cards=10, ruidai=10))
        self.assertEqual(read(path), "途中まで解いた")

    def test_make_respects_counts(self):
        text = read(daily.make_page(TODAY, cards=2, ruidai=1))
        ids = self.ids_in(text)
        self.assertEqual([i for i in ids if i.startswith("暗記")], ["暗記-重回帰-01", "暗記-重回帰-02"])
        self.assertEqual(len([i for i in ids if i.startswith("類題")]), 1)

    def test_flashcard_order_missed_then_review_then_new(self):
        self.recall(["2026-10-01\t暗記-推定-01\tx\t\t暗記",
                     "2026-10-01\t暗記-重回帰-01\to\t\t暗記"])
        ids = self.ids_in(read(daily.make_page(TODAY, cards=10, ruidai=0)))
        self.assertEqual(ids, ["暗記-推定-01", "暗記-重回帰-01", "暗記-重回帰-02"])

    def test_new_cards_keep_a_share_when_reviews_fill_the_page(self):
        """復習の日が来たカードで枠が埋まっても、新しいカードを毎日 new_min 枚は入れる。"""
        self.recall(["2026-10-01\t暗記-推定-01\to\t\t暗記",
                     "2026-10-01\t暗記-重回帰-01\to\t\t暗記"])
        ids = self.ids_in(read(daily.make_page(TODAY, cards=2, ruidai=0, new_min=1)))
        self.assertEqual(ids, ["暗記-重回帰-01", "暗記-重回帰-02"])   # 復習1枚（台帳の順）＋新しい1枚

    def test_missed_ruidai_comes_back_first(self):
        """前日に落とした類題は、まだ一度も出していない類題より先に出す（🔴でも同じ）。"""
        self.recall(["2026-10-01\t類題-重回帰-02\tx\t\t類題"])
        ids = self.ids_in(read(daily.make_page(TODAY, cards=0, ruidai=10)))
        self.assertEqual(ids, ["類題-重回帰-02", "類題-重回帰-01"])

    def test_new_ruidai_from_past_exams_come_before_pretest(self):
        """まだ出していない類題は、過去問の類題をプレテストの類題より先に出す。
        同じ優先度で混ぜていたら、プレテストの類題が枠を取り、過去問の類題が何日も出なかった。"""
        write(tally.QUESTIONS, read(tally.QUESTIONS)
              + "類題-重回帰-09\tM 重回帰\t選択\tA\t類題 過去問 2015年11月\t\tP-01-1\n")
        write(os.path.join(daily.SRC_DIRS["類題"], "過去問1回目.md"),
              "# 類題\n\n## 類題-重回帰-09\n\n問い\n\n- A あ\n- B い\n\n"
              "> [!note]- 解説\n> **正解 A**。\n")
        ids = self.ids_in(read(daily.make_page(TODAY, cards=0, ruidai=10)))
        self.assertEqual(ids, ["類題-重回帰-09", "類題-重回帰-01", "類題-重回帰-02"])

    def test_same_priority_rotates_fields(self):
        """優先度が同じなら分野を交互に出す（同じ分野が何問も続かない）。"""
        rows = [(0, -999, "類題-1変数-01", {"分野": "B 1変数"}),
                (0, -999, "類題-1変数-02", {"分野": "B 1変数"}),
                (0, -999, "類題-推定-01", {"分野": "J 推定"}),
                (0, -999, "類題-推定-02", {"分野": "J 推定"}),
                (2, -999, "類題-時系列-01", {"分野": "E 時系列"})]
        self.assertEqual([r[2] for r in daily.interleave(rows)],
                         ["類題-1変数-01", "類題-推定-01", "類題-1変数-02", "類題-推定-02", "類題-時系列-01"])

    def test_sleeping_flashcard_is_left_out(self):
        self.recall(["2026-10-02\t暗記-重回帰-01\to\t\t暗記"])     # 今日、確信ありで正解
        ids = self.ids_in(read(daily.make_page(TODAY, cards=10, ruidai=0)))
        self.assertNotIn("暗記-重回帰-01", ids)

    def test_page_has_no_remarks_section_from_claude(self):
        """ページの冒頭に Claude の所見は置かない。問いは、使い方の次にすぐ始まる。"""
        text = read(daily.make_page(TODAY, cards=1, ruidai=1))
        self.assertNotIn("Claude）", text)
        self.assertNotIn("所見", text)
        headings = [l for l in text.splitlines() if l.startswith("## ")]
        self.assertEqual(headings, ["## 暗記カード（1枚）", "## 類題（1問）"])

    # ------------------------------------------------------------ 量を見直す合図
    def solved_on(self, day, late):
        """day のページを作って1問タップし、最後のコミットの時刻（その日の夜 or 翌朝）を返す。"""
        path = daily.make_page(day, cards=1, ruidai=0, new_min=0)
        qid = self.ids_in(read(path))[0]
        write(path, tap(read(path), qid, "❌"))
        stamp = _dt.datetime(day.year, day.month, day.day, 22, 0, tzinfo=daily.JST)
        return path, stamp + _dt.timedelta(hours=10 if late else 0)

    def test_pace_counts_consecutive_late_days(self):
        times = dict(self.solved_on(_dt.date(2026, 10, d), late) for d, late in ((1, False), (2, True), (3, True)))
        untouched = daily.make_page(_dt.date(2026, 10, 4), cards=1, ruidai=0)   # まだ解いていないページは数えない
        rows, streak = daily.pace(commit_time=times.get)
        self.assertEqual(streak, 2)
        self.assertEqual([r["late"] for r in rows], [False, True, True, False])
        self.assertEqual(rows[3]["tapped"], 0)
        self.assertTrue(os.path.exists(untouched))

    def test_pace_streak_stops_at_a_day_solved_in_time(self):
        times = dict(self.solved_on(_dt.date(2026, 10, d), late) for d, late in ((1, True), (2, True), (3, False)))
        _rows, streak = daily.pace(commit_time=times.get)
        self.assertEqual(streak, 0)

    def test_pace_without_git_history_counts_nothing(self):
        self.solved_on(_dt.date(2026, 10, 1), True)
        rows, streak = daily.pace(commit_time=lambda _path: None)
        self.assertEqual(streak, 0)
        self.assertFalse(rows[0]["late"])

    # ------------------------------------------------------------ 書き込んだ時刻（本物の git で確かめる）
    def git(self, args, cwd=None, env=None):
        full = dict(os.environ, **(env or {}))
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                        "-c", "commit.gpgsign=false"] + args,
                       cwd=cwd or self.dir, env=full, check=True, capture_output=True)

    def committed_page(self):
        """ページを1枚、書いたのは 10/3 23:30・コミットし直したのは 10/4 00:20 として積む。"""
        path = daily.make_page(_dt.date(2026, 10, 3), cards=1, ruidai=0)
        self.git(["init", "-q"])
        self.git(["add", "-A"])
        self.git(["commit", "-q", "-m", "solved"],
                 env={"GIT_AUTHOR_DATE": "2026-10-03T23:30:00+09:00",
                      "GIT_COMMITTER_DATE": "2026-10-04T00:20:00+09:00"})
        return path

    @unittest.skipUnless(shutil.which("git"), "git が無い")
    def test_commit_time_is_none_outside_git_and_for_untracked_pages(self):
        path = daily.make_page(_dt.date(2026, 10, 3), cards=1, ruidai=0)
        self.assertIsNone(daily.last_commit_time(path))          # git の管理下に無い
        self.git(["init", "-q"])
        self.assertIsNone(daily.last_commit_time(path))          # まだコミットしていない

    @unittest.skipUnless(shutil.which("git"), "git が無い")
    def test_commit_time_uses_the_time_it_was_written(self):
        """コミットし直した時刻（rebase でずれる）ではなく、書いた時刻で見る。"""
        at = daily.last_commit_time(self.committed_page())
        self.assertEqual(at, _dt.datetime(2026, 10, 3, 23, 30, tzinfo=daily.JST))
        rows, streak = daily.pace()
        self.assertEqual((rows[0]["late"], streak), (False, 0))

    @unittest.skipUnless(shutil.which("git"), "git が無い")
    def test_commit_time_is_none_in_a_shallow_clone(self):
        """履歴を途中までしか持たない clone では、どのページも最後のコミットの時刻に見える。数えない。"""
        path = self.committed_page()
        write(os.path.join(self.dir, "later.txt"), "あとの日のコミット")
        self.git(["add", "-A"])
        self.git(["commit", "-q", "-m", "later"], env={"GIT_AUTHOR_DATE": "2026-10-09T10:00:00+09:00",
                                                       "GIT_COMMITTER_DATE": "2026-10-09T10:00:00+09:00"})
        clone = tempfile.mkdtemp()
        try:
            self.git(["clone", "-q", "--depth", "1", "file://" + self.dir.replace("\\", "/"), clone], cwd=clone)
            cloned = os.path.join(clone, os.path.relpath(path, self.dir))
            self.assertTrue(os.path.exists(cloned))
            self.assertIsNone(daily.last_commit_time(cloned))
        finally:
            remove_tree(clone)

    # ------------------------------------------------------------ 読む
    def solved_page(self):
        path = daily.make_page(TODAY, cards=10, ruidai=10)
        text = read(path)
        text = tap(text, "類題-重回帰-01", "C")
        text = tap(text, "類題-重回帰-01", "🤔")
        text = tap(text, "類題-重回帰-02", "A")
        text = tap(text, "暗記-重回帰-01", "⭕")
        text = tap(text, "暗記-推定-01", "❌")
        text = memo(text, "暗記-推定-01", "n か n-1 か迷った\n途中までは分かっていた")
        text = text.replace("**かかった時間**: 分", "**かかった時間**: 28 分")
        write(path, text)
        return path

    def recall_rows(self):
        return [l for l in read(tally.RECALL_LOG).splitlines()[1:] if l.strip()]

    def test_ingest_records_taps(self):
        self.solved_page()
        pages = daily.ingest(TODAY)
        self.assertEqual(sorted(self.recall_rows()), sorted([
            "2026-10-02\t類題-重回帰-01\to\t?\t類題",
            "2026-10-02\t類題-重回帰-02\tx\t\t類題",
            "2026-10-02\t暗記-重回帰-01\to\t\t暗記",
            "2026-10-02\t暗記-推定-01\tx\t\t暗記",
        ]))
        self.assertEqual(pages[0]["minutes"], 28)
        items = {i["id"]: i for i in pages[0]["items"]}
        self.assertEqual(items["暗記-推定-01"]["memo"], "n か n-1 か迷った\n途中までは分かっていた")
        self.assertEqual(items["類題-重回帰-02"]["choice"], "A")
        self.assertNotIn("暗記-重回帰-02", items)       # 手を付けていない問いは記録しない

    def test_ingest_is_idempotent(self):
        self.solved_page()
        daily.ingest(TODAY)
        pages = daily.ingest(TODAY)
        self.assertEqual(len(self.recall_rows()), 4)
        self.assertEqual(pages, [])                       # 新しく記録したページだけを返す

    def test_show_reads_page_without_recording(self):
        """採点のセッションが解説を書くために見る。記録は定期実行の ingest に任せる（書く人を1人にする）。"""
        self.solved_page()
        page = daily.show(TODAY)
        self.assertEqual(self.recall_rows(), [])          # 書き込まない
        self.assertEqual(page["minutes"], 28)
        items = {i["id"]: i for i in page["items"]}
        self.assertTrue(items["類題-重回帰-01"]["result"])
        self.assertFalse(items["類題-重回帰-02"]["result"])
        self.assertFalse(items["暗記-推定-01"]["result"])
        self.assertEqual(items["暗記-推定-01"]["memo"], "n か n-1 か迷った\n途中までは分かっていた")
        self.assertNotIn("暗記-重回帰-02", items)         # 手を付けていない問いは出さない
        daily.ingest(TODAY)
        self.assertEqual(len(self.recall_rows()), 4)      # あとからの記録はふだんどおり入る
        self.assertEqual(daily.show(TODAY)["items"], page["items"])   # 記録のあとも同じものが見える

    def test_show_without_page_returns_none(self):
        self.assertIsNone(daily.show(TODAY))

    def test_untouched_page_records_nothing(self):
        daily.make_page(TODAY, cards=10, ruidai=10)
        self.assertEqual(daily.ingest(TODAY), [])
        self.assertEqual(self.recall_rows(), [])

    def test_memo_only_item_is_reported_but_not_recorded(self):
        path = daily.make_page(TODAY, cards=10, ruidai=10)
        write(path, memo(read(path), "暗記-重回帰-02", "全然わからない"))
        pages = daily.ingest(TODAY)
        self.assertEqual(self.recall_rows(), [])
        self.assertEqual(pages[0]["items"][0]["memo"], "全然わからない")
        self.assertIsNone(pages[0]["items"][0]["result"])

    # ------------------------------------------------------------ 崩れた入力（2026-10-01 レビュー）
    def test_empty_checkbox_does_not_crash(self):
        path = self.solved_page()
        write(path, read(path).replace("- [ ] 🤔 自信なし", "- [x] ", 1))
        daily.ingest(TODAY)
        self.assertEqual(len(self.recall_rows()), 4)

    def test_memo_keeps_lines_that_look_like_markup(self):
        path = daily.make_page(TODAY, cards=10, ruidai=10)
        body = "- [忘れていた]のでやり直す\n---\n# 見出しっぽいけどメモ\n続きのメモ"
        write(path, tap(memo(read(path), "暗記-推定-01", body), "暗記-推定-01", "❌"))
        items = {i["id"]: i for i in daily.ingest(TODAY)[0]["items"]}
        self.assertEqual(items["暗記-推定-01"]["memo"], body)

    def test_memo_written_below_the_answer_is_kept(self):
        path = daily.make_page(TODAY, cards=10, ruidai=10)
        text = tap(read(path), "暗記-推定-01", "❌")
        start = text.index("%%id: 暗記-推定-01%%")
        pos = text.index("- [x] ❌", start)
        text = text[:pos] + "答えを見て思い出した\n" + text[pos:]
        write(path, text)
        items = {i["id"]: i for i in daily.ingest(TODAY)[0]["items"]}
        self.assertEqual(items["暗記-推定-01"]["memo"], "答えを見て思い出した")

    def test_two_choices_are_not_graded_but_reported(self):
        path = daily.make_page(TODAY, cards=0, ruidai=10)
        write(path, tap(tap(read(path), "類題-重回帰-01", "A"), "類題-重回帰-01", "C"))
        pages = daily.ingest(TODAY)
        self.assertEqual(self.recall_rows(), [])
        item = pages[0]["items"][0]
        self.assertIsNone(item["result"])
        self.assertEqual(item["choices"], ["A", "C"])

    def test_missing_recall_log_is_created_with_header(self):
        os.remove(tally.RECALL_LOG)
        self.solved_page()
        daily.ingest(TODAY)
        self.assertTrue(read(tally.RECALL_LOG).startswith("日付\tid\t結果\t確信\t形式\n"))
        self.assertEqual(len(self.recall_rows()), 4)
        self.assertEqual(daily.ingest(TODAY), [])          # 2回目は重複しない

    # ------------------------------------------------------------ 素材の検査
    def test_check_passes_on_consistent_sources(self):
        self.assertEqual(daily.check(), [])

    def test_check_finds_mismatches(self):
        write(os.path.join(daily.SRC_DIRS["類題"], "プレテスト.md"),
              RUIDAI.replace("**正解 C**", "**正解 B**").replace("## 類題-重回帰-02", "## 類題-重回帰-99"))
        errors = "\n".join(daily.check())
        self.assertIn("類題-重回帰-01", errors)           # 解説の正解と台帳の正解が違う
        self.assertIn("類題-重回帰-02", errors)           # 台帳にあるのに素材が無い
        self.assertIn("類題-重回帰-99", errors)           # 素材にあるのに台帳に無い

    def test_check_finds_flashcard_without_pass_line(self):
        """暗記カードの答えに「ここまで言えたら⭕」が無いと、迷った問いが全部 ❌ になる。"""
        write(os.path.join(daily.SRC_DIRS["暗記"], "推定.md"),
              ANKI_J.replace("> **⭕ の線**: 「n − 1」が言えたら⭕\n", ""))
        errors = daily.check()
        self.assertEqual(len(errors), 1)
        self.assertIn("暗記-推定-01", errors[0])
        self.assertIn("⭕ の線", errors[0])

    def test_check_wants_the_pass_line_at_the_head_of_the_answer(self):
        """「⭕ の線」が答えのあとに書いてあっても通さない（答えを読む前に、線が目に入る場所に置く）。"""
        write(os.path.join(daily.SRC_DIRS["暗記"], "推定.md"),
              ANKI_J.replace("> **⭕ の線**: 「n − 1」が言えたら⭕\n> $n-1$\n",
                             "> $n-1$\n> **⭕ の線**: 「n − 1」が言えたら⭕\n"))
        errors = daily.check()
        self.assertEqual(len(errors), 1)
        self.assertIn("暗記-推定-01", errors[0])

    def test_make_skips_a_source_without_its_answer(self):
        """折りたたみ（答え）が欠けた素材が1問あっても、その日のページは作れる（欠けは check が知らせる）。"""
        write(os.path.join(daily.SRC_DIRS["暗記"], "推定.md"), "# 暗記カード\n\n## 暗記-推定-01\n\n不偏分散は何で割る？\n")
        ids = self.ids_in(read(daily.make_page(TODAY, cards=10, ruidai=0)))
        self.assertEqual(ids, ["暗記-重回帰-01", "暗記-重回帰-02"])
        self.assertTrue(any("暗記-推定-01" in e for e in daily.check()))

    def test_recall_log_keeps_its_line_endings(self):
        with open(tally.RECALL_LOG, "w", encoding="utf-8", newline="") as f:
            f.write("日付\tid\t結果\t確信\t形式\r\n2026-10-01\t暗記-推定-01\to\t\t暗記\r\n")
        self.solved_page()
        daily.ingest(TODAY)
        with open(tally.RECALL_LOG, "r", encoding="utf-8", newline="") as f:
            text = f.read()
        self.assertEqual(text.count("\n"), text.count("\r\n"))        # LF だけの行が混ざらない
        self.assertEqual(len(self.recall_rows()), 5)


if __name__ == "__main__":
    unittest.main(verbosity=1)
