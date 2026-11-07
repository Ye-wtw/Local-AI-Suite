# -*- coding: utf-8 -*-
"""chess_core.py - minimal pure-Python chess engine (no dependencies).
Purpose: generate and VERIFY machine-checkable chess puzzles (mate-in-1, FEN parsing,
legality judgement). Board is a dict {(rank,file): piece}; ranks 0..7 = 1..8, files 0..7 = a..h.
White pieces are uppercase, black lowercase.
"""
FILES = "abcdefgh"

def sq_to_rc(sq):
    """'e4' -> (3, 4)  (rank index, file index)"""
    if len(sq) != 2 or sq[0] not in FILES or sq[1] not in "12345678":
        return None
    return (int(sq[1]) - 1, FILES.index(sq[0]))

def rc_to_sq(rc):
    r, f = rc
    return FILES[f] + str(r + 1)

def inside(r, f):
    return 0 <= r < 8 and 0 <= f < 8

def parse_fen(fen):
    """Parse a FEN string. Returns dict with board/side/castling/ep/halfmove/fullmove."""
    parts = fen.strip().split()
    if len(parts) < 4:
        raise ValueError("FEN needs at least 4 fields: " + fen)
    rows = parts[0].split("/")
    if len(rows) != 8:
        raise ValueError("FEN board must have 8 ranks: " + fen)
    board = {}
    for i, row in enumerate(rows):
        rank = 7 - i
        file = 0
        for ch in row:
            if ch.isdigit():
                file += int(ch)
            else:
                if file > 7:
                    raise ValueError("rank overflow in FEN: " + fen)
                board[(rank, file)] = ch
                file += 1
    side = "w" if parts[1] == "w" else "b"
    castling = parts[2]
    ep = parts[3] if parts[3] != "-" else None
    half = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
    full = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else 1
    return {"board": board, "side": side, "castling": castling, "ep": ep,
            "halfmove": half, "fullmove": full}

def to_fen(state):
    board, side = state["board"], state["side"]
    rows = []
    for rank in range(7, -1, -1):
        empty = 0
        row = ""
        for file in range(8):
            p = board.get((rank, file))
            if p is None:
                empty += 1
            else:
                if empty:
                    row += str(empty)
                    empty = 0
                row += p
        if empty:
            row += str(empty)
        rows.append(row)
    return "/".join(rows) + " %s %s %s %d %d" % (side, state["castling"], state["ep"] or "-",
                                                 state["halfmove"], state["fullmove"])

def color_of(p):
    return "w" if p.isupper() else "b"

def other(side):
    return "b" if side == "w" else "w"

def render_ascii(state):
    """Text board diagram (for FEN-parsing questions)."""
    lines = ["  +" + "-" * 17 + "+"]
    for rank in range(7, -1, -1):
        cells = []
        for file in range(8):
            p = state["board"].get((rank, file))
            cells.append(p if p else ".")
        lines.append(str(rank + 1) + " | " + " ".join(cells) + " |")
    lines.append("  +" + "-" * 17 + "+")
    lines.append("    " + " ".join(FILES))
    return chr(10).join(lines)

KNIGHT_D = [(1, 2), (2, 1), (-1, 2), (-2, 1), (1, -2), (2, -1), (-1, -2), (-2, -1)]
KING_D = [(0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1)]
BISHOP_D = [(1, 1), (1, -1), (-1, 1), (-1, -1)]
ROOK_D = [(1, 0), (-1, 0), (0, 1), (0, -1)]

def piece_moves(board, rc, piece, ep=None):
    """Pseudo-legal moves for one piece (king safety handled by caller)."""
    r, f = rc
    side = color_of(piece)
    kind = piece.upper()
    out = []
    def add(nr, nf):
        if not inside(nr, nf):
            return False
        t = board.get((nr, nf))
        if t is None:
            out.append(((r, f), (nr, nf)))
            return True
        if color_of(t) != side:
            out.append(((r, f), (nr, nf)))
        return False
    if kind == "N":
        for dr, df in KNIGHT_D:
            nr, nf = r + dr, f + df
            if inside(nr, nf):
                t = board.get((nr, nf))
                if t is None or color_of(t) != side:
                    out.append(((r, f), (nr, nf)))
    elif kind == "K":
        for dr, df in KING_D:
            nr, nf = r + dr, f + df
            if inside(nr, nf):
                t = board.get((nr, nf))
                if t is None or color_of(t) != side:
                    out.append(((r, f), (nr, nf)))
    elif kind in ("B", "R", "Q"):
        dirs = []
        if kind in ("B", "Q"):
            dirs += BISHOP_D
        if kind in ("R", "Q"):
            dirs += ROOK_D
        for dr, df in dirs:
            nr, nf = r + dr, f + df
            while inside(nr, nf):
                t = board.get((nr, nf))
                if t is None:
                    out.append(((r, f), (nr, nf)))
                else:
                    if color_of(t) != side:
                        out.append(((r, f), (nr, nf)))
                    break
                nr += dr
                nf += df
    elif kind == "P":
        step = 1 if side == "w" else -1
        start_rank = 1 if side == "w" else 6
        promo_rank = 7 if side == "w" else 0
        # forward one
        nr, nf = r + step, f
        if inside(nr, nf) and board.get((nr, nf)) is None:
            _push_pawn(out, (r, f), (nr, nf), nr, promo_rank)
            nr2 = r + 2 * step
            if r == start_rank and board.get((nr2, nf)) is None:
                out.append(((r, f), (nr2, nf)))
        # captures
        for df in (-1, 1):
            nr, nf = r + step, f + df
            if not inside(nr, nf):
                continue
            t = board.get((nr, nf))
            if t is not None and color_of(t) != side:
                _push_pawn(out, (r, f), (nr, nf), nr, promo_rank)
            elif t is None and ep and rc_to_sq((nr, nf)) == ep:
                out.append(((r, f), (nr, nf)))
    return out

def _push_pawn(out, src, dst, dst_rank, promo_rank):
    if dst_rank == promo_rank:
        for pc in "QRBN":
            out.append((src, dst, pc))
    else:
        out.append((src, dst))

def find_king(board, side):
    target = "K" if side == "w" else "k"
    for rc, p in board.items():
        if p == target:
            return rc
    return None

def is_attacked(board, rc, by_side):
    """Is square rc attacked by any piece of by_side?"""
    r, f = rc
    # pawns
    step = 1 if by_side == "w" else -1
    pawn = "P" if by_side == "w" else "p"
    for df in (-1, 1):
        if board.get((r - step, f + df)) == pawn:
            return True
    # knights
    kn = "N" if by_side == "w" else "n"
    for dr, df in KNIGHT_D:
        if board.get((r + dr, f + df)) == kn:
            return True
    # king
    kg = "K" if by_side == "w" else "k"
    for dr, df in KING_D:
        if board.get((r + dr, f + df)) == kg:
            return True
    # sliding: bishop/queen
    bq = ("B", "Q") if by_side == "w" else ("b", "q")
    for dr, df in BISHOP_D:
        nr, nf = r + dr, f + df
        while inside(nr, nf):
            p = board.get((nr, nf))
            if p is not None:
                if p in bq:
                    return True
                break
            nr += dr
            nf += df
    rq = ("R", "Q") if by_side == "w" else ("r", "q")
    for dr, df in ROOK_D:
        nr, nf = r + dr, f + df
        while inside(nr, nf):
            p = board.get((nr, nf))
            if p is not None:
                if p in rq:
                    return True
                break
            nr += dr
            nf += df
    return False

def in_check(state, side):
    k = find_king(state["board"], side)
    if k is None:
        return False
    return is_attacked(state["board"], k, other(side))

def apply_move(state, move):
    """Return a new state after applying (src, dst[, promo]). Handles ep and castling."""
    src, dst = move[0], move[1]
    promo = move[2] if len(move) > 2 else None
    board = dict(state["board"])
    piece = board.pop(src)
    side = color_of(piece)
    # en passant capture
    if piece.upper() == "P" and state.get("ep") and rc_to_sq(dst) == state["ep"]:
        cap_rc = (src[0], dst[1])
        board.pop(cap_rc, None)
    # castling: king moves two files
    if piece.upper() == "K" and abs(dst[1] - src[1]) == 2:
        rank = src[0]
        if dst[1] > src[1]:
            rook_from, rook_to = (rank, 7), (rank, 5)
        else:
            rook_from, rook_to = (rank, 0), (rank, 3)
        if rook_from in board:
            board[rook_to] = board.pop(rook_from)
    placed = piece
    if promo:
        placed = promo.upper() if side == "w" else promo.lower()
    board[dst] = placed
    # new ep square
    ep = None
    if piece.upper() == "P" and abs(dst[0] - src[0]) == 2:
        ep = rc_to_sq(((src[0] + dst[0]) // 2, src[1]))
    # castling rights (simplified: drop rights when king/rook moves or rook captured)
    rights = state.get("castling", "-")
    if rights not in ("-", ""):
        keep = []
        for c in rights:
            if c == "K" and (src == (0, 4) or dst == (0, 7) or src == (0, 7)):
                continue
            if c == "Q" and (src == (0, 4) or dst == (0, 0) or src == (0, 0)):
                continue
            if c == "k" and (src == (7, 4) or dst == (7, 7) or src == (7, 7)):
                continue
            if c == "q" and (src == (7, 4) or dst == (7, 0) or src == (7, 0)):
                continue
            keep.append(c)
        rights = "".join(keep) or "-"
    return {"board": board, "side": other(side), "castling": rights, "ep": ep,
            "halfmove": 0, "fullmove": state.get("fullmove", 1) + (1 if side == "b" else 0)}

def pseudo_moves(state):
    side = state["side"]
    out = []
    for rc, p in state["board"].items():
        if color_of(p) != side:
            continue
        out.extend(piece_moves(state["board"], rc, p, state.get("ep")))
    return out

def legal_moves(state):
    """Pseudo-legal moves filtered so the mover's king is not left in check."""
    side = state["side"]
    out = []
    for mv in pseudo_moves(state):
        nxt = apply_move(state, mv)
        if not in_check(nxt, side):
            out.append(mv)
    return out

def is_checkmate(state):
    return in_check(state, state["side"]) and not legal_moves(state)

def is_stalemate(state):
    return (not in_check(state, state["side"])) and not legal_moves(state)

def mates_in_1(state):
    """All legal moves that deliver immediate checkmate."""
    out = []
    for mv in legal_moves(state):
        nxt = apply_move(state, mv)
        if is_checkmate(nxt):
            out.append(mv)
    return out

def move_to_uci(move):
    """(src, dst[, promo]) -> 'e2e4' / 'e7e8q'"""
    s = rc_to_sq(move[0]) + rc_to_sq(move[1])
    if len(move) > 2 and move[2]:
        s += move[2].lower()
    return s

def uci_to_move(uci):
    """'e2e4' -> ((1,4),(3,4)) ; 'e7e8q' -> with promo"""
    u = uci.strip().lower()
    if len(u) < 4:
        return None
    src, dst = sq_to_rc(u[0:2]), sq_to_rc(u[2:4])
    if not src or not dst:
        return None
    if len(u) >= 5 and u[4] in "qrbn":
        return (src, dst, u[4].upper())
    return (src, dst)

def piece_letter(p):
    return p.upper()

def move_san(state, move):
    """Minimal SAN for a move (with disambiguation, check/mate suffix)."""
    src, dst = move[0], move[1]
    piece = state["board"].get(src, "?")
    kind = piece.upper()
    capture = state["board"].get(dst) is not None
    # en passant counts as capture
    if kind == "P" and state.get("ep") and rc_to_sq(dst) == state["ep"]:
        capture = True
    body = ""
    if kind == "P":
        if capture:
            body = FILES[src[1]] + "x" + rc_to_sq(dst)
        else:
            body = rc_to_sq(dst)
        if len(move) > 2 and move[2]:
            body += "=" + move[2].upper()
    else:
        same = []
        for mv in legal_moves(state):
            if mv[1] == dst and mv[0] != src:
                p2 = state["board"].get(mv[0], "")
                if p2.upper() == kind:
                    same.append(mv[0])
        disamb = ""
        if same:
            files = set(s[1] for s in same)
            ranks = set(s[0] for s in same)
            if src[1] not in files:
                disamb = FILES[src[1]]
            elif src[0] not in ranks:
                disamb = str(src[0] + 1)
            else:
                disamb = FILES[src[1]] + str(src[0] + 1)
        body = kind + disamb + ("x" if capture else "") + rc_to_sq(dst)
    nxt = apply_move(state, move)
    if is_checkmate(nxt):
        body += "#"
    elif in_check(nxt, nxt["side"]):
        body += "+"
    return body

SQ_TOKEN = None

def parse_chess_answer(text):
    """Extract a candidate square/piece from a free-text answer.
    Returns dict {uci: str|None, dest: str|None, piece: str|None}
    Accepts: 'e2e4', 'Ra8#', 'Qh5', 'a8=Q', 'Rook to a8', '车到 a8', 'a8'.
    """
    import re
    if not text:
        return {"uci": None, "dest": None, "piece": None}
    # 先只取 answer: 之后的内容 —— 题面里写着 "用 UCI 格式(如 e2e4)" 这样的例子,
    # 直接全文扫描会把那个例子当成答案。实测:模型答对 answer: Nf7#,
    # 判分器却抓了题面举例的 e2e4,判成错(D10 的 mate-in-1 因此系统性偏低)。
    import re as _re
    seg = text.strip()
    # 取**最后一个**非空的 answer: 行。踩过两个坑:
    #   1. 全文扫描会抓到题面举例("如 e2e4"),把正确答案挤掉;
    #   2. 模型会写出 "Answer:**" 这种只有粗体标记的空行,取到的内容是 "**"。
    _cands = []
    for _m in _re.finditer(r"answer\s*[:：]\s*([^\n]*)", seg, _re.I):
        _body = _m.group(1).strip().strip("*").strip()
        if _body:
            _cands.append(_body)
    if _cands:
        seg = _cands[-1]
    t = seg
    # 1) explicit UCI
    m = re.search(r"\b([a-h][1-8])([a-h][1-8])([qrbnQRBN])?\b", t)
    if m:
        uci = (m.group(1) + m.group(2) + (m.group(3).lower() if m.group(3) else ""))
        return {"uci": uci, "dest": m.group(2), "piece": (m.group(3).upper() if m.group(3) else None)}
    # 2) SAN-ish: piece letter + destination square
    m = re.search(r"\b([KQRBN])\s*[a-h]?[1-8]?\s*[xX]?\s*([a-h][1-8])\b", t)
    if m:
        return {"uci": None, "dest": m.group(2), "piece": m.group(1)}
    # 3) promotion form a8=Q
    m = re.search(r"\b([a-h][1-8])\s*=\s*([QRBNqrbn])\b", t)
    if m:
        return {"uci": None, "dest": m.group(1), "piece": m.group(2).upper()}
    # 4) Chinese piece names + square
    cn = {"车": "R", "马": "N", "象": "B", "相": "B", "后": "Q", "王": "K", "兵": "P", "卒": "P"}
    for name, letter in cn.items():
        if name in t:
            m = re.search(r"([a-h][1-8])", t)
            if m:
                return {"uci": None, "dest": m.group(1), "piece": letter}
    # 5) bare square
    m = re.search(r"\b([a-h][1-8])\b", t)
    if m:
        return {"uci": None, "dest": m.group(1), "piece": None}
    return {"uci": None, "dest": None, "piece": None}

def answers_match(state, mating_moves, parsed):
    """Does the parsed answer correspond to one of the mating moves?"""
    if not parsed or not parsed.get("dest"):
        return False, "no square found in answer"
    if parsed.get("uci"):
        for mv in mating_moves:
            if move_to_uci(mv) == parsed["uci"]:
                return True, "uci match " + parsed["uci"]
    for mv in mating_moves:
        dst_sq = rc_to_sq(mv[1])
        if dst_sq != parsed["dest"]:
            continue
        piece = state["board"].get(mv[0], "")
        if parsed.get("piece") and piece.upper() != parsed["piece"]:
            continue
        return True, "destination match %s%s" % (parsed.get("piece") or "", dst_sq)
    return False, "answer %s not among %s" % (parsed.get("dest"),
                                              [move_to_uci(m) for m in mating_moves])
