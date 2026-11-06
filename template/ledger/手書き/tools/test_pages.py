#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_pages.py（手書きページの生成）のテスト。

    python ledger/手書き/tools/test_pages.py

固定すること:
  - 解答ノートの小問ごとに1枚できる。1周目以外は名前に周回が付く
  - 「← 問題に戻る」は、大問の見出し → 小問の見出しの順にたどるリンク（同じ `### (1)` が何度出ても迷わない）
  - --link は、リンクの無い小問にだけ1行入れる（何度流しても増えない・改行コードを変えない）
  - 書き込みのあるページは作り直さない。古い要素が生きているときだけ、線を残して差し替える
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_pages  # noqa: E402
from excalidraw_io import load_scene  # noqa: E402

NOTE = """# 1章 — 1周目

## 1-01 用語と計算の基本

### (1)
- [ ] A
- [ ] B
- [ ] 🤔 自信なし

### (2)
- [ ] A
- [ ] B
- [ ] 🤔 自信なし

## 1-02 公式の適用

### (1)
- [ ] A
- [ ] B
- [ ] 🤔 自信なし
"""


def stroke(eid="stroke01", color="#1e1e1e"):
    return {"id": eid, "type": "freedraw", "x": 100, "y": 200, "points": [[0, 0], [10, 10]],
            "strokeColor": color, "isDeleted": False}


class PagesCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.saved = (gen_pages.ROUNDS, gen_pages.HANDWRITING, gen_pages.RETIRED_IDS, gen_pages.KNOWN_IDS)
        gen_pages.ROUNDS = os.path.join(self.dir, "rounds")
        gen_pages.HANDWRITING = os.path.join(self.dir, "手書き")
        self.note = self.write_note(1, "1章.md", NOTE)

    def tearDown(self):
        gen_pages.ROUNDS, gen_pages.HANDWRITING, gen_pages.RETIRED_IDS, gen_pages.KNOWN_IDS = self.saved
        shutil.rmtree(self.dir)

    def write_note(self, round_no, name, text, newline="\n"):
        path = os.path.join(gen_pages.ROUNDS, f"round{round_no}", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline=newline) as f:
            f.write(text)
        return path

    def page(self, round_no, note, name):
        return os.path.join(gen_pages.HANDWRITING, f"round{round_no}", note, name + ".excalidraw.md")

    def read(self, path):
        with open(path, encoding="utf-8", newline="") as f:
            return f.read()

    def add_stroke(self, path, extra=()):
        """ページに手書きの線（と、あれば追加の要素）を足して保存する。"""
        text = self.read(path)
        scene, _kind = load_scene(text)
        scene["elements"] += [stroke()] + list(extra)
        head = text[:text.index("```json")]
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(head + "```json\n" + json.dumps(scene, ensure_ascii=False) + "\n```\n%%")

    # ------------------------------------------------------------ 作る
    def test_one_page_per_question(self):
        counts, kept, _migrated, linked = gen_pages.generate(1)
        self.assertEqual(counts, {"new": 3, "migrate": 0, "ok": 0})
        self.assertEqual((kept, linked), ([], 0))
        for name in ("1-01-01", "1-01-02", "1-02-01"):
            self.assertTrue(os.path.exists(self.page(1, "1章", name)), name)

    def test_back_link_walks_from_the_section_heading(self):
        gen_pages.generate(1)
        text = self.read(self.page(1, "1章", "1-02-01"))
        self.assertIn("backLink: [[1章#1-02 公式の適用#(1)]]", text)
        scene, kind = load_scene(text)
        self.assertEqual(kind, "json")
        titles = [e["text"] for e in scene["elements"] if e["id"] == "hdTitle0"]
        self.assertEqual(titles, ["1-02 公式の適用　(1)"])

    def test_other_rounds_get_the_round_in_the_name(self):
        self.write_note(2, "解き直し.md", NOTE)
        self.write_note(0, "プレテスト.md", NOTE)
        gen_pages.generate(2)
        gen_pages.generate(0)
        self.assertTrue(os.path.exists(self.page(2, "解き直し", "1-01-01_2周目")))
        self.assertTrue(os.path.exists(self.page(0, "プレテスト", "1-01-01_プレテスト")))

    # ------------------------------------------------------------ ノートのリンク
    def test_link_is_added_once(self):
        _c, _k, _m, linked = gen_pages.generate(1, link=True)
        self.assertEqual(linked, 3)
        text = self.read(self.note)
        self.assertIn("### (2)\n✏️ [[1-01-02.excalidraw|途中式]]\n\n- [ ] A", text)
        _c, _k, _m, linked = gen_pages.generate(1, link=True)
        self.assertEqual(linked, 0)
        self.assertEqual(self.read(self.note), text)

    def test_link_keeps_crlf(self):
        path = self.write_note(1, "1章.md", NOTE, newline="\r\n")
        gen_pages.generate(1, link=True)
        text = self.read(path)
        self.assertIn("### (1)\r\n✏️ [[1-01-01.excalidraw|途中式]]\r\n\r\n- [ ] A", text)
        self.assertEqual(text.count("\n"), text.count("\r\n"))        # LF だけの行が混ざらない

    def test_link_follows_the_ending_of_its_own_heading(self):
        """1行目だけ LF で、あとは CRLF のノートでも、足す行は見出しと同じ改行にする。"""
        mixed = NOTE.replace("\n", "\r\n").replace("# 1章 — 1周目\r\n", "# 1章 — 1周目\n", 1)
        path = self.write_note(1, "1章.md", mixed, newline="")
        gen_pages.generate(1, link=True)
        text = self.read(path)
        self.assertIn("### (1)\r\n✏️ [[1-01-01.excalidraw|途中式]]\r\n\r\n- [ ] A", text)
        self.assertEqual(text.count("\n") - text.count("\r\n"), 1)    # LF だけの行は、元からの1行目だけ

    def test_back_link_stops_before_a_heading_obsidian_cannot_follow(self):
        """見出しに | があると、Obsidian は別名の区切りとして読む。たどれる所までのリンクにする。"""
        self.assertEqual(gen_pages.back_link("ノート", "1-01 確率", "(1) P(A|B) の計算"), "[[ノート#1-01 確率]]")
        self.assertEqual(gen_pages.back_link("ノート", "1-01 P(A|B)", "(1)"), "[[ノート]]")
        self.assertEqual(gen_pages.back_link("ノート", "1-01 確率", "(1) 問2〔1〕"), "[[ノート#1-01 確率#(1) 問2〔1〕]]")

    # ------------------------------------------------------------ タブレットが保存した形を読む
    def test_compressed_drawing_with_emoji_is_read(self):
        """タブレットは Drawing を圧縮して保存する。絵文字（🔴）が2つに割れたまま戻ると、書き出しで落ちる。
        下の文字列は、本物の lz-string（compressToBase64）で作ったもの。"""
        packed = ("N4IgLgngDgpiBcIYA8DGBDANgSwCYCd0B3EAGiUxgFsYA7MAZwQG1Q8FwBGM8aORMCjA9ByYYkC8G4BZdwCVRgY8iABAApOAShABfUm1w"
                  "cG3cpFgcAZvhgwCxHsgSdyEBACZyUAPbZ6TeM2YAGUj8AXVJmAFZSMKCgjRigA=")
        scene, kind = load_scene("%%\n## Drawing\n```compressed-json\n" + packed + "\n```\n%%")
        self.assertEqual(kind, "compressed-json")
        self.assertEqual(scene["elements"][0]["text"], "🔴誤解 (1)")
        scene["elements"][0]["text"].encode("utf-8")                  # 割れた文字が残っていれば、ここで落ちる
        self.assertEqual(scene["elements"][1]["points"], [[0, 0], [5, 5]])

    # ------------------------------------------------------------ 書き込みを守る
    def test_page_with_strokes_is_left_alone(self):
        gen_pages.generate(1)
        path = self.page(1, "1章", "1-01-01")
        self.add_stroke(path)
        before = self.read(path)
        counts, _k, _m, _l = gen_pages.generate(1)
        self.assertEqual(counts, {"new": 2, "migrate": 0, "ok": 1})
        self.assertEqual(self.read(path), before)

    def test_page_with_other_elements_is_not_touched(self):
        gen_pages.generate(1)
        path = self.page(1, "1章", "1-01-01")
        self.add_stroke(path, extra=[{"id": "userText", "type": "text", "text": "メモ", "isDeleted": False}])
        before = self.read(path)
        _c, kept, _m, _l = gen_pages.generate(1)
        self.assertEqual(len(kept), 1)
        self.assertEqual(self.read(path), before)

    def test_retired_template_element_is_replaced_and_strokes_survive(self):
        """形を変えたあと: 古い要素が生きているページは、線を残してテンプレートだけ差し替え、消す印を入れる。"""
        gen_pages.generate(1)
        path = self.page(1, "1章", "1-01-01")
        self.add_stroke(path, extra=[{"id": "zoneOld0", "type": "text", "text": "古い区画", "isDeleted": False}])
        gen_pages.RETIRED_IDS = {"zoneOld0": "text"}
        gen_pages.KNOWN_IDS = gen_pages.TEMPLATE_IDS | set(gen_pages.RETIRED_IDS)
        counts, _k, migrated, _l = gen_pages.generate(1)
        self.assertEqual(counts["migrate"], 1)
        self.assertEqual(len(migrated), 1)
        elements = {e["id"]: e for e in load_scene(self.read(path))[0]["elements"]}
        self.assertIn("stroke01", elements)
        self.assertTrue(elements["zoneOld0"]["isDeleted"])
        self.assertEqual(elements["zoneOld0"]["version"], gen_pages.TOMBSTONE_VERSION)


if __name__ == "__main__":
    unittest.main(verbosity=1)
