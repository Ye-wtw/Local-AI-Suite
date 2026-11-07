# -*- coding: utf-8 -*-
"""gen_d10.py - generate the D10 board-game dimension (chess).
All answers are COMPUTED by runner/chess_core.py, never hand-written.
Question types:
  mate1      - find the mate in one move
  fencount   - count pieces of a given type (FEN parsing / spatial reasoning)
  fenking    - locate a king / ask whose turn / is side in check
  reach      - list squares a named piece can legally move to
  legality   - is a given move legal
Usage: python scripts/gen_d10.py [--out datasets/generated]
"""
import json, os, sys, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "runner"))
from chess_core import (parse_fen, legal_moves, pseudo_moves, mates_in_1, move_san, move_to_uci,
                        in_check, rc_to_sq, render_ascii, is_checkmate)

CHOOSE_NL = chr(10)

# Engine-verified puzzle positions: (fen, theme)
POSITIONS = [
    ("6k1/5ppp/8/8/8/8/8/R3K3 w - - 0 1", "底线杀"),
    ("6k1/5ppp/8/8/8/8/5PPP/3Q2K1 w - - 0 1", "后到底线"),
    ("6rk/6pp/8/6N1/8/8/8/6K1 w - - 0 1", "闷杀"),
    ("7k/8/6K1/8/8/8/8/7R w - - 0 1", "车王配合"),
    ("6k1/8/6K1/8/8/8/8/R7 w - - 0 1", "车扫底线"),
    ("6k1/6pp/8/8/8/8/8/4Q1K1 w - - 0 1", "后占底线"),
    ("7k/R7/6K1/8/8/8/8/8 w - - 0 1", "车a8"),
    ("4r1k1/5ppp/8/8/8/8/5PPP/4R1K1 w - - 0 1", "车吃e8"),
    ("2r3k1/5ppp/8/8/8/8/5PPP/2R3K1 w - - 0 1", "车吃c8"),
    ("6k1/5ppp/8/8/8/8/5PPP/1R4K1 w - - 0 1", "车b8"),
    ("5rk1/5ppp/8/8/8/8/5PPP/3Q2K1 w - - 0 1", "后d8"),
    ("6k1/5ppp/8/8/8/8/5PPP/2Q3K1 w - - 0 1", "后c8"),
    ("r5k1/5ppp/8/8/8/8/5PPP/3Q2K1 w - - 0 1", "后d8吃线"),
    ("6k1/5p1p/6p1/8/8/8/8/R3K3 w - - 0 1", "非杀对照"),
    ("4k3/8/8/8/4r3/8/4N3/4K3 w - - 0 1", "马被牵制"),
    ("4k3/8/8/8/4r3/8/4R3/4K3 w - - 0 1", "车被牵制"),
    ("4k3/4r3/8/8/8/8/4Q3/4K3 w - - 0 1", "后被牵制"),
    ("4k3/8/8/8/8/8/4P3/4K2r w - - 0 1", "有车威胁"),
    ("6k1/8/8/8/8/2b5/1N6/4K3 w - - 0 1", "马对王牵制"),
]

PIECE_CN = {"P": "兵", "N": "马", "B": "象", "R": "车", "Q": "后", "K": "王",
            "p": "兵", "n": "马", "b": "象", "r": "车", "q": "后", "k": "王"}


def count_pieces(state, letter):
    return sum(1 for p in state["board"].values() if p == letter)


def gen_position_questions(fen, theme, idx):
    rows = []
    st = parse_fen(fen)
    side = st["side"]
    side_cn = "白方" if side == "w" else "黑方"

    # ---- mate in 1 ----
    mates = mates_in_1(st)
    if mates:
        sans = [move_san(st, m) for m in mates]
        ucis = [move_to_uci(m) for m in mates]
        rows.append({
            "id": "d10_mate1_%03d" % idx, "dim": "D10", "source": "selfmade-chess",
            "license": "CC0", "subcat": "mate-in-1", "type": "chess",
            "fen": fen, "theme": theme, "side_to_move": side,
            "question": ("以下是国际象棋局面(FEN): %s" % fen) + CHOOSE_NL +
                        "现在轮到%s走。请找出一步将死(一步杀)的着法。" % side_cn + CHOOSE_NL +
                        "用 UCI 格式(如 e2e4)或代数记法(如 Qh5#)回答,最后一行写: answer: <着法>",
            "answer_uci": ucis, "answer_san": sans,
            "expect": {"any_of_uci": ucis, "any_of_san": sans, "scoring": "matches any mating move"},
            "notes": "engine-verified mate in 1 (%s)" % theme})

    # ---- piece counting ----
    for letter, cn in (("P", "白兵"), ("p", "黑兵"), ("R", "白车"), ("N", "白马"), ("Q", "白后"), ("B", "白象")):
        n = count_pieces(st, letter)
        if n == 0:
            continue
        rows.append({
            "id": "d10_fencount_%03d_%s" % (idx, letter), "dim": "D10", "source": "selfmade-chess",
            "license": "CC0", "subcat": "fen-parse", "type": "chess",
            "fen": fen, "theme": theme,
            "question": ("局面 FEN: %s" % fen) + CHOOSE_NL +
                        "棋盘上共有多少个%s?请只回答数字,最后一行写: answer: <数字>" % cn,
            "answer": str(n),
            "expect": {"value": str(n), "scoring": "exact number"},
            "notes": "piece count from FEN"})
        break  # one counting question per position

    # ---- king location / check status ----
    wk, bk = None, None
    for rc, p in st["board"].items():
        if p == "K":
            wk = rc_to_sq(rc)
        elif p == "k":
            bk = rc_to_sq(rc)
    if wk and bk:
        in_chk = in_check(st, side)
        rows.append({
            "id": "d10_fenking_%03d" % idx, "dim": "D10", "source": "selfmade-chess",
            "license": "CC0", "subcat": "fen-parse", "type": "chess",
            "fen": fen, "theme": theme,
            "question": ("局面 FEN: %s" % fen) + CHOOSE_NL +
                        "轮到%s走。此时%s的王是否正被将军?回答「是」或「否」,最后一行写: answer: <是/否>"
                        % (side_cn, side_cn),
            "answer": "是" if in_chk else "否",
            "expect": {"value": "是" if in_chk else "否", "scoring": "exact"},
            "notes": "check detection"})

    # ---- reachable squares for one piece ----
    if mates or idx % 2 == 0:
        for rc, p in sorted(st["board"].items(), key=lambda kv: str(kv[0])):
            if (p.isupper()) != (side == "w"):
                continue
            if p.upper() not in ("N", "B", "R", "Q"):
                continue
            dests = sorted(set(rc_to_sq(m[1]) for m in legal_moves(st) if m[0] == rc))
            if len(dests) < 2:
                continue
            rows.append({
                "id": "d10_reach_%03d" % idx, "dim": "D10", "source": "selfmade-chess",
                "license": "CC0", "subcat": "piece-reach", "type": "chess",
                "fen": fen, "theme": theme,
                "question": ("局面 FEN: %s" % fen) + CHOOSE_NL +
                            "轮到%s走。位于 %s 的%s,在合法着法中可以走到哪些格子?请列出全部格子。"
                            % (side_cn, rc_to_sq(rc), PIECE_CN.get(p, p)),
                "answer": dests,
                "expect": {"squares": dests, "scoring": "set comparison"},
                "notes": "legal destination squares"})
            break

    # ---- legality question ----
    if side == "w":
        all_legal = legal_moves(st)
        legal_ucis = set(move_to_uci(m) for m in all_legal)
        # find a move that looks plausible but is illegal
        # prefer a SUBTLE illegal move: pseudo-legal (piece geometry allows it) but
        # forbidden because it leaves the king in check (pin / must-answer-check)
        pseudo_ucis = set(move_to_uci(m) for m in pseudo_moves(st))
        # ONLY ask when a geometrically-legal-looking move is actually forbidden
        # (pin, must-answer-check). Trivially illegal moves are worthless as questions.
        subtle = sorted(pseudo_ucis - legal_ucis)
        illegal = subtle[0] if subtle else None
        if illegal:
            rows.append({
                "id": "d10_legal_%03d" % idx, "dim": "D10", "source": "selfmade-chess",
                "license": "CC0", "subcat": "legality", "type": "chess",
                "fen": fen, "theme": theme,
                "question": ("局面 FEN: %s" % fen) + CHOOSE_NL +
                            "轮到白方走。着法 %s 是否合法?回答「合法」或「不合法」,最后一行写: answer: <合法/不合法>"
                            % illegal,
                "answer": "不合法",
                "expect": {"value": "不合法", "scoring": "exact"},
                "notes": "illegal move detection"})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "datasets", "generated"))
    args = ap.parse_args()
    rows = []
    for i, (fen, theme) in enumerate(POSITIONS):
        try:
            rows.extend(gen_position_questions(fen, theme, i))
        except Exception as e:
            print("  [skip] %s: %s" % (theme, e))
    out = os.path.join(args.out, "D10_generated.jsonl")
    os.makedirs(args.out, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + chr(10))
    from collections import Counter
    c = Counter(r["subcat"] for r in rows)
    print("D10_generated.jsonl: %d 题" % len(rows))
    for k, v in sorted(c.items()):
        print("   %-14s %d" % (k, v))
    print("-> " + out)


if __name__ == "__main__":
    main()
