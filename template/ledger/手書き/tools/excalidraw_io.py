"""`.excalidraw.md` の Drawing 部分を読む共通処理（gen_pages.py / read_pages.py から使う）。

Obsidian Excalidraw プラグインは Drawing を2通りで保存する:
- ```json            … 平文（設定 compress=false）
- ```compressed-json … LZString の compressToBase64（設定 compress=true。iPad で既定のまま保存すると、こちらになる）
どちらでも elements を返す。追加の依存は無い（LZString の展開をここに実装した）。
統計検定2級のプロジェクトで、iPad が保存した compressed-json のページを読むのに使った。
"""
import json
import re

FENCE = re.compile(r"```(json|compressed-json)\s*\n(.*?)\n```", re.S)
_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="


def lz_decompress_base64(data):
    """LZString.decompressFromBase64 の Python 実装。"""
    data = re.sub(r"\s+", "", data)
    if not data:
        return ""
    values = [_B64.index(c) for c in data]
    pos, val, idx = 32, values[0], 1

    def read_bits(n):
        nonlocal pos, val, idx
        bits, power = 0, 1
        for _ in range(n):
            bit = val & pos
            pos >>= 1
            if pos == 0:
                pos = 32
                val = values[idx] if idx < len(values) else 0
                idx += 1
            if bit:
                bits |= power
            power <<= 1
        return bits

    dictionary = {0: 0, 1: 1, 2: 2}
    enlarge_in, dict_size, num_bits = 4, 4, 3
    first = read_bits(2)
    if first == 2:
        return ""
    w = chr(read_bits(8 if first == 0 else 16))
    dictionary[3] = w
    result = [w]
    while True:
        if idx > len(values) + 1:
            return "".join(result)
        c = read_bits(num_bits)
        if c in (0, 1):
            dictionary[dict_size] = chr(read_bits(8 if c == 0 else 16))
            dict_size += 1
            c = dict_size - 1
            enlarge_in -= 1
        elif c == 2:
            return "".join(result)
        if enlarge_in == 0:
            enlarge_in = 2 ** num_bits
            num_bits += 1
        if c in dictionary:
            entry = dictionary[c]
        elif c == dict_size:
            entry = w + w[0]
        else:
            raise ValueError("compressed-json を展開できない（データが壊れている）")
        result.append(entry)
        dictionary[dict_size] = w + entry[0]
        dict_size += 1
        enlarge_in -= 1
        w = entry
        if enlarge_in == 0:
            enlarge_in = 2 ** num_bits
            num_bits += 1


def join_surrogates(text):
    """LZString は UTF-16 の単位で文字を持つので、絵文字（🔴 など）は2つに割れて戻ってくる。1文字に戻す。
    割れたままだと、ファイルへ書くときに UnicodeEncodeError で落ちる。"""
    return text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def load_scene(text):
    """Drawing の scene(dict) と保存形式（"json" / "compressed-json"）を返す。読めなければ ValueError。"""
    m = FENCE.search(text)
    if not m:
        raise ValueError("Drawing のブロックが見つからない")
    kind, body = m.group(1), m.group(2)
    raw = body if kind == "json" else join_surrogates(lz_decompress_base64(body))
    try:
        return json.loads(raw), kind
    except json.JSONDecodeError as e:
        raise ValueError(f"Drawing の JSON を読めない（{kind}）: {e}") from e
