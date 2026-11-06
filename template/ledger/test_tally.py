#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tally.py の出題キュー（--next）と、直前の教材の材料（--drill）のテスト。

    python ledger/test_tally.py

統計検定2級のプロジェクトで再現した症状を固定する:
  1. 解釈ノートの一問一答（周回に着手記録が無い問題）が候補に入らない
  2. 過去問1周目のファイル（未記入）を置いた瞬間、プレテストの🔴を含めて候補が0件になる
  3. 0件のときに「全問が寝かせ中」と誤った理由を出す
  4. --drill が1周回しか見ず、解き直していない誤答と一問一答の記録が直前の材料に入らない
"""
import contextlib
import datetime as _dt
import io
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tally  # noqa: E402

HEADER = "id\t分野\t型\t正解\t出典\tメモ\t元問題\n"
TODAY = _dt.date(2026, 10, 1)


def note(dai, answers):
    """解答ノート1枚ぶんの本文。answers = [(小問番号, 選んだ肢 or None, 自信なし)]"""
    out = ["## %s テスト\n" % dai]
    for n, choice, unsure in answers:
        out.append("### (%d)" % n)
        for c in "ABCDE":
            out.append("- [%s] %s" % ("x" if c == choice else " ", c))
        out.append("- [%s] 🤔 自信なし\n" % ("x" if unsure else " "))
    return "\n".join(out) + "\n"


class QueueCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.saved = (tally.QUESTIONS, tally.ROUNDS_DIR, tally.RECALL_LOG)
        tally.QUESTIONS = os.path.join(self.dir, "questions.tsv")
        tally.ROUNDS_DIR = os.path.join(self.dir, "rounds")
        tally.RECALL_LOG = os.path.join(self.dir, "recall-log.tsv")
        # プレテスト: P-01 は🔴誤解、P-02 は✅定着、P-03 は⬜未学習、P-04 は未回答
        self.questions([
            "P-01-1\tM 重回帰\t選択\tD\tプレテスト\t調整済みR²\t",
            "P-02-1\tH 確率変数と分布\t選択\tC\tプレテスト\t分散\t",
            "P-03-1\tE 時系列\t選択\tB\tプレテスト\t移動平均\t",
            "P-04-1\tJ 推定\t選択\tE\tプレテスト\t比率の区間\t",
        ])
        self.round(0, "プレテスト.md", note("P-01", [(1, "E", False)])
                   + note("P-02", [(1, "C", False)])
                   + note("P-03", [(1, "C", True)])
                   + note("P-04", [(1, None, False)]))
        self.recall([])

    def tearDown(self):
        tally.QUESTIONS, tally.ROUNDS_DIR, tally.RECALL_LOG = self.saved
        shutil.rmtree(self.dir)

    # ------------------------------------------------------------ 道具
    def questions(self, rows, append=False):
        with open(tally.QUESTIONS, "a" if append else "w", encoding="utf-8") as f:
            if not append:
                f.write(HEADER)
            for r in rows:
                f.write(r + "\n")

    def round(self, n, name, body):
        d = os.path.join(tally.ROUNDS_DIR, "round%d" % n)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(body)

    def recall(self, rows):
        with open(tally.RECALL_LOG, "w", encoding="utf-8") as f:
            f.write("日付\tid\t結果\t確信\t形式\n")
            for r in rows:
                f.write(r + "\n")

    def queue(self):
        q, sleeping, warns = tally.build_queue(tally.load_questions(), TODAY)
        return {t[2]: t[4] for t in q}, [t[2] for t in q], sleeping, warns

    # ------------------------------------------------------------ 今までどおり
    def test_pretest_only(self):
        quads, order, _s, warns = self.queue()
        self.assertEqual(quads, {"P-01-1": tally.WRONG_BELIEF,
                                 "P-02-1": tally.FIXED,
                                 "P-03-1": tally.UNLEARNED})
        self.assertEqual(order[0], "P-01-1")        # 🔴誤解が先頭
        self.assertNotIn("P-04-1", quads)           # 未回答は出さない
        self.assertEqual(warns, [])

    # ------------------------------------------------------------ 症状1
    def test_note_item_inherits_source_quadrant(self):
        self.questions(["N-01\tM 重回帰\t選択\tA\t解釈ノート 重回帰\t比は大きいほうが上\tP-01-1"],
                       append=True)
        quads, order, _s, _w = self.queue()
        self.assertEqual(quads["N-01"], tally.WRONG_BELIEF)
        self.assertLess(order.index("N-01"), order.index("P-03-1"))   # ⬜より前

    def test_note_item_with_unanswered_source_is_unlearned(self):
        self.questions(["N-02\tJ 推定\t選択\tA\t解釈ノート 推定\t割合で揃える\tP-04-1"], append=True)
        quads, _o, _s, _w = self.queue()
        self.assertEqual(quads["N-02"], tally.UNLEARNED)

    def test_note_item_with_two_sources_takes_most_severe(self):
        self.questions(["N-03\tM 重回帰\t選択\tA\t解釈ノート\t2つの元\tP-02-1, P-01-1"], append=True)
        quads, _o, _s, _w = self.queue()
        self.assertEqual(quads["N-03"], tally.WRONG_BELIEF)

    def test_unknown_source_is_warned_not_dropped(self):
        self.questions(["N-04\tM 重回帰\t選択\tA\t解釈ノート\t誤記\tP-99-1"], append=True)
        quads, _o, _s, warns = self.queue()
        self.assertEqual(quads["N-04"], tally.UNLEARNED)
        self.assertTrue(any("P-99-1" in w for w in warns))

    def test_note_item_without_source_is_warned(self):
        self.questions(["N-05\tM 重回帰\t選択\tA\t解釈ノート\t元が空\t"], append=True)
        quads, _o, _s, warns = self.queue()
        self.assertNotIn("N-05", quads)
        self.assertTrue(any("N-05" in w for w in warns))

    # ------------------------------------------------------------ 一問一答の2種類（2026-10-01）
    def test_kind_of(self):
        self.assertEqual(tally.kind_of({"型": "暗記", "出典": "暗記カード 重回帰"}), "暗記")
        self.assertEqual(tally.kind_of({"型": "選択", "出典": "類題 プレテスト"}), "類題")
        self.assertEqual(tally.kind_of({"型": "選択", "出典": "解釈ノート 推定"}), "類題")
        self.assertEqual(tally.kind_of({"型": "選択", "出典": "プレテスト"}), "演習")

    def test_flashcard_without_source_is_unlearned_candidate(self):
        self.questions(["暗記-重回帰-01\tM 重回帰\t暗記\t-\t暗記カード 重回帰\tR²の式\t"], append=True)
        quads, _o, _s, warns = self.queue()
        self.assertEqual(quads["暗記-重回帰-01"], tally.UNLEARNED)
        self.assertEqual(warns, [])

    def test_ruidai_without_source_is_warned(self):
        self.questions(["類題-重回帰-09\tM 重回帰\t選択\tA\t類題 プレテスト\t元が空\t"], append=True)
        quads, _o, _s, warns = self.queue()
        self.assertNotIn("類題-重回帰-09", quads)
        self.assertTrue(any("類題-重回帰-09" in w for w in warns))

    def test_next_queue_filters_by_kind(self):
        self.questions(["暗記-重回帰-01\tM 重回帰\t暗記\t-\t暗記カード 重回帰\tR²の式\t",
                        "類題-重回帰-01\tM 重回帰\t選択\tA\t類題 プレテスト\t式\tP-01-1"], append=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            tally.next_queue(tally.load_questions(), limit=10, today=TODAY, kind="暗記")
        out = buf.getvalue()
        self.assertIn("暗記-重回帰-01", out)
        self.assertNotIn("類題-重回帰-01", out)
        self.assertNotIn("P-01-1", out)

    def test_unattempted_exam_question_is_not_a_candidate(self):
        self.questions(["2015-11-1\tA データと分布の記述\t選択\tB\t問題集 p.1\t\t"], append=True)
        quads, _o, _s, warns = self.queue()
        self.assertNotIn("2015-11-1", quads)
        self.assertEqual(warns, [])

    # ------------------------------------------------------------ 症状2
    def test_empty_new_round_does_not_hide_pretest(self):
        self.questions(["2015-11-1\tA データと分布の記述\t選択\tB\t問題集 p.1\t\t"], append=True)
        self.round(1, "2015年11月.md", note("2015-11", [(1, None, False)]))
        quads, _o, _s, _w = self.queue()
        self.assertEqual(quads.get("P-01-1"), tally.WRONG_BELIEF)
        self.assertEqual(len(quads), 3)

    def test_latest_round_wins(self):
        self.round(1, "解き直し.md", note("P-01", [(1, "D", False)]))   # 1周目で自信ありの正解
        quads, _o, _s, _w = self.queue()
        self.assertEqual(quads["P-01-1"], tally.FIXED)

    def test_round_report_is_not_mixed(self):
        """周回ごとの集計（report / compare）は周回を混ぜない。"""
        self.round(1, "解き直し.md", note("P-01", [(1, "D", False)]))
        q = tally.load_questions()
        self.assertEqual(len(tally.collect(tally.load_round(0)[0], q)), 3)
        self.assertEqual(len(tally.collect(tally.load_round(1)[0], q)), 1)

    def test_report_denominator_is_the_rounds_questions(self):
        """着手率の分母は、その周回の解答ノートの問題数。マスタにノートの一問一答が増えても変わらない。"""
        self.questions(["N-01\tM 重回帰\t記述\t-\t解釈ノート\t比\tP-01-1",
                        "N-02\tM 重回帰\t記述\t-\t解釈ノート\t比\tP-01-1"], append=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            tally.report(0, tally.load_questions())
        self.assertIn("着手 **3/4 問**", buf.getvalue())

    # ------------------------------------------------------------ 想起テストの履歴
    def test_recall_failure_promotes_and_streak_sleeps(self):
        self.questions(["N-01\tM 重回帰\t選択\tA\t解釈ノート\t比\tP-01-1",
                        "N-06\tE 時系列\t選択\tA\t解釈ノート\t移動平均\tP-03-1"], append=True)
        self.recall(["2026-09-30\tN-06\tx\t\t一問一答",       # ⬜ → 落とした → 1段昇格
                     "2026-09-30\tN-01\to\t\t一問一答"])      # 確信ある正解1回 → 1日寝かせる
        today = _dt.date(2026, 9, 30)
        q, sleeping, _w = tally.build_queue(tally.load_questions(), today)
        ids = [t[2] for t in q]
        self.assertNotIn("N-01", ids)
        self.assertEqual(sleeping, 1)
        score = {t[2]: t[0] for t in q}
        self.assertEqual(score["N-06"], tally.SEVERITY[tally.UNLEARNED] - 1)

    # ------------------------------------------------------------ 症状4（直前の教材の材料）
    def second_round_and_cards(self):
        """1周目: P-01 は解き直さない／P-02 は落とす（逆行）／P-03 は確信ありで正解／P-04 は自信なく当たる。
        暗記カード: 01 は直近で言えた／02 は直近で落とした。"""
        self.round(1, "解き直し.md", note("P-02", [(1, "A", False)])
                   + note("P-03", [(1, "B", False)])
                   + note("P-04", [(1, "E", True)]))
        self.questions(["暗記-重回帰-01\tM 重回帰\t暗記\t-\t暗記カード 重回帰\tR²の式\t",
                        "暗記-重回帰-02\tM 重回帰\t暗記\t-\t暗記カード 重回帰\t調整済みの式\t"], append=True)
        self.recall(["2026-09-29\t暗記-重回帰-01\tx\t\t暗記",
                     "2026-09-30\t暗記-重回帰-01\to\t\t暗記",
                     "2026-09-29\t暗記-重回帰-02\to\t\t暗記",
                     "2026-09-30\t暗記-重回帰-02\tx\t\t暗記"])

    def test_drill_gathers_every_round_and_the_recall_log(self):
        self.second_round_and_cards()
        weak = tally.remaining_weak(tally.load_questions())
        wrong = {row[0]: row for row in weak["wrong"]}
        self.assertEqual(sorted(wrong), ["P-01-1", "P-02-1"])
        self.assertEqual(wrong["P-01-1"][3], 0)            # 解き直していない誤答は、前の周回の記録のまま出る
        self.assertFalse(wrong["P-01-1"][5])
        self.assertEqual(wrong["P-02-1"][3], 1)
        self.assertTrue(wrong["P-02-1"][5])                # 前の周回では正解だった（逆行）
        self.assertEqual([row[0] for row in weak["lucky"]], ["P-04-1"])
        self.assertEqual([(r[0], r[2], r[3]) for r in weak["recall"]], [("暗記-重回帰-02", 1, 2)])

    def test_drill_leaves_out_what_is_already_fixed(self):
        self.second_round_and_cards()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            tally.final_review(tally.load_questions())
        out = buf.getvalue()
        self.assertNotIn("P-03-1", out)                    # 一番新しい記録が確信ありの正解
        self.assertNotIn("暗記-重回帰-01", out)             # 直近の1回で言えたカード
        self.assertIn("前の周回では正解だった", out)
        self.assertIn("2回のうち1回落とした", out)

    def test_drill_lists_weak_fields_first(self):
        """1 と 2 は正答率の低い分野から（時間が切れたときに落ちるのが、強い分野になるように）。"""
        self.questions(["Q-01-1\tZ 強い分野\t選択\tA\t問題集\t\t", "Q-01-2\tZ 強い分野\t選択\tA\t問題集\t\t",
                        "Q-01-3\tZ 強い分野\t選択\tA\t問題集\t\t"], append=True)
        self.round(1, "強い分野.md", note("Q-01", [(1, "A", False), (2, "A", False), (3, "B", False)]))
        weak = tally.remaining_weak(tally.load_questions())
        self.assertLess(weak["field_rate"]["M 重回帰"], weak["field_rate"]["Z 強い分野"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            tally.final_review(tally.load_questions())
        out = buf.getvalue()
        self.assertLess(out.index("P-01-1"), out.index("Q-01-3"))     # 重回帰（0/1）が、強い分野（2/3）より先

    def test_drill_keeps_a_wrong_answer_until_the_question_itself_is_solved_again(self):
        """一問一答で当たっても、演習の誤答は外さない（類題は正解で原題は誤答、が実際にあった）。"""
        self.recall(["2026-09-30\tP-01-1\to\t\t一問一答"])
        weak = tally.remaining_weak(tally.load_questions())
        self.assertIn("P-01-1", [row[0] for row in weak["wrong"]])

    def run_main(self, *args):
        buf = io.StringIO()
        saved = sys.argv
        sys.argv = ["tally.py"] + list(args)
        try:
            with contextlib.redirect_stdout(buf):
                tally.main()
        finally:
            sys.argv = saved
        return buf.getvalue()

    def test_drill_without_round_looks_at_everything_and_with_round_at_one(self):
        self.second_round_and_cards()
        self.assertIn("直前の教材の材料", self.run_main("--drill"))
        one = self.run_main("--drill", "--round", "0")
        self.assertIn("優先復習リスト（0周目", one)                    # --round 0 が最新周回に落ちない
        self.assertNotIn("暗記-重回帰-02", one)

    def test_next_zero_is_still_the_queue(self):
        self.assertIn("次に出す問題", self.run_main("--next", "0"))

    def test_drill_with_nothing_left_says_so(self):
        self.round(1, "解き直し.md", note("P-01", [(1, "D", False)]) + note("P-03", [(1, "B", False)]))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            tally.final_review(tally.load_questions())
        self.assertIn("まだ直っていないものはありません", buf.getvalue())

    # ------------------------------------------------------------ 症状3
    def test_empty_queue_says_why(self):
        self.questions([], append=False)
        self.recall([])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            tally.next_queue(tally.load_questions(), limit=5, today=TODAY)
        self.assertIn("着手記録が無く", buf.getvalue())
        self.assertNotIn("待機中", buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=1)
