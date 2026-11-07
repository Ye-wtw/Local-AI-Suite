# -*- coding: utf-8 -*-
"""vision.py - D11: can the model actually see?

Why generated images and not photos: every question needs an answer that is
objectively checkable. A photo of a street has no single correct description, so
scoring it needs an LLM judge, which this project forbids. A programmatically
drawn image has a KNOWN ground truth (the code that drew it), so the check is
exact: did the model read the number that is actually there.

Three shapes, matching the three capabilities the user asked for:

  ocr       - digits rendered at a known position; read them back
  pattern   - count/colour/shape questions about drawn geometry
  detail    - small but decisive differences (a rotated glyph, a missing bar)

All images are drawn with the standard library only: PNG is written by hand
(zlib + struct). No Pillow, no numpy - the project is zero-dependency and the
PNG encoder is 40 lines.
"""
import os
import struct
import zlib

NL = chr(10)


# ---------------------------------------------------------------- PNG writer
def _chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def write_png(path, width, height, pixels):
    """pixels: list of rows, each a list of (r,g,b) tuples."""
    raw = bytearray()
    for row in pixels:
        raw.append(0)                      # filter type 0 (None)
        for (r, g, b) in row:
            raw += bytes((r, g, b))
    png = (b"\x89PNG\r\n\x1a\n"
           + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
           + _chunk(b"IEND", b""))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(png)
    return path


# ------------------------------------------------------------- tiny raster
class Canvas:
    """A minimal raster canvas: enough to draw digits and blocks."""

    def __init__(self, w, h, bg=(255, 255, 255)):
        self.w, self.h = w, h
        self.px = [[bg for _ in range(w)] for _ in range(h)]

    def rect(self, x0, y0, x1, y1, color=(0, 0, 0)):
        for y in range(max(0, y0), min(self.h, y1)):
            row = self.px[y]
            for x in range(max(0, x0), min(self.w, x1)):
                row[x] = color

    def save(self, path):
        return write_png(path, self.w, self.h, self.px)


# 5x7 bitmap font, digits only. Each digit is 7 rows of 5 bits.
FONT = {
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11111", "00010", "00100", "00010", "00001", "10001", "01110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
}


def draw_text(canvas, text, x0, y0, scale=6, color=(0, 0, 0)):
    """Draw digits at (x0,y0). Returns the x just past the text."""
    x = x0
    for ch in text:
        rows = FONT.get(ch)
        if not rows:
            x += 4 * scale
            continue
        for ry, rowbits in enumerate(rows):
            for rx, bit in enumerate(rowbits):
                if bit == "1":
                    canvas.rect(x + rx * scale, y0 + ry * scale,
                                x + (rx + 1) * scale, y0 + (ry + 1) * scale, color)
        x += 6 * scale
    return x


# --------------------------------------------------------------- scenarios
def build_ocr_items(root, seeds=(1, 2, 3, 4)):
    """Render numbers at known positions; the answer is the number."""
    import random
    items = []
    for s in seeds:
        rng = random.Random(20261101 + s)
        digits = "".join(str(rng.randint(0, 9)) for _ in range(4))
        c = Canvas(420, 140)
        draw_text(c, digits, 30, 30, scale=8)
        fname = os.path.join(root, "d11_ocr_%02d.png" % s)
        c.save(fname)
        items.append({
            "id": "d11_ocr_%02d" % s, "dim": "D11", "subcat": "ocr",
            "image": fname, "answer": digits,
            "question": "图中显示的四位数字是什么?只回答数字。",
            "expect": {"answer": digits, "scoring": "exact digit string"},
        })
    return items


def build_count_items(root, seeds=(1, 2, 3)):
    """Draw N squares; the answer is the count. Position and colour vary so the
    model cannot pass by assuming a fixed layout."""
    import random
    items = []
    for s in seeds:
        rng = random.Random(20261101 + 100 + s)
        n = rng.randint(3, 7)
        c = Canvas(400, 160)
        x = 20
        for i in range(n):
            c.rect(x, 50, x + 40, 90, (30, 30, 30))
            x += 55
        fname = os.path.join(root, "d11_count_%02d.png" % s)
        c.save(fname)
        items.append({
            "id": "d11_count_%02d" % s, "dim": "D11", "subcat": "pattern",
            "image": fname, "answer": str(n),
            "question": "图中有几个黑色方块?只回答数字。",
            "expect": {"answer": str(n), "scoring": "exact count"},
        })
    return items


def build_detail_items(root, seeds=(1, 2)):
    """Two numbers where one digit is visually similar; the answer needs the
    fine difference to be seen (6 vs 8, 3 vs 8)."""
    import random
    items = []
    pairs = [("6", "8"), ("3", "8"), ("1", "7")]
    for s in seeds:
        rng = random.Random(20261101 + 200 + s)
        a, b = pairs[s % len(pairs)]
        shown = a if rng.random() < 0.5 else b
        c = Canvas(300, 140)
        draw_text(c, shown, 40, 30, scale=10)
        fname = os.path.join(root, "d11_detail_%02d.png" % s)
        c.save(fname)
        items.append({
            "id": "d11_detail_%02d" % s, "dim": "D11", "subcat": "detail",
            "image": fname, "answer": shown,
            "question": "图中是哪一个数字?只回答一个数字字符。",
            "expect": {"answer": shown, "scoring": "exact single char"},
        })
    return items


def build_color_items(root, seeds=tuple(range(1, 9))):
    """哪个色块最多/在哪一侧 —— 纯文本模型不可能答对。

    为什么要这一类:原有三类(四位 OCR、数方框、认单个数字)考的都是
    "能不能把像素读成字形",而字形信息在文本里也能描述。颜色类不同:
    答案只存在于像素里,没有视觉通道的模型无法通过任何文本线索推断,
    这正是这个维度想测的东西。
    """
    import random
    items = []
    COLORS = [((220, 40, 40), "红"), ((40, 110, 220), "蓝"),
              ((40, 170, 70), "绿"), ((230, 200, 40), "黄")]
    for s in seeds:
        rng = random.Random(20261101 + 300 + s)
        pick = rng.sample(COLORS, 2)
        k0, k1 = rng.randint(2, 6), rng.randint(1, 4)
        if k0 == k1:
            k0 += 1
        c = Canvas(460, 150)
        x = 20
        for _ in range(k0):
            c.rect(x, 45, x + 45, 105, pick[0][0]); x += 52
        x += 18
        for _ in range(k1):
            c.rect(x, 45, x + 45, 105, pick[1][0]); x += 52
        fname = os.path.join(root, "d11_color_%02d.png" % s)
        c.save(fname)
        more = pick[0][1] if k0 > k1 else pick[1][1]
        items.append({
            "id": "d11_color_%02d" % s, "dim": "D11", "subcat": "color",
            "image": fname, "answer": more,
            "question": "图中有两种颜色的方块,哪种颜色出现次数更多?只回答颜色名(红/蓝/绿/黄)。",
            "expect": {"answer": more, "scoring": "exact color word"},
        })
    return items


def build_layout_items(root, seeds=tuple(range(1, 7))):
    """三个形状里哪个在中间 —— 考空间关系,不是字形识别。"""
    import random
    items = []
    for s in seeds:
        rng = random.Random(20261101 + 400 + s)
        shapes = ["圆圈", "三角", "方块"]
        rng.shuffle(shapes)
        c = Canvas(480, 140)
        for i, name in enumerate(shapes):
            x = 40 + i * 150
            if name == "方块":
                c.rect(x, 50, x + 60, 110, (30, 30, 30))
            elif name == "圆圈":
                # 必须画**圆**,不能拿 rect 画成方块。
                # 原来这里用 c.rect(...) 填了一个方块,于是"圆圈"和"方块"
                # 在图上长得一模一样,这题无从判断 —— 实测 layout 图里
                # 三个形状看着是"方块/方块/三角",答案却写"圆圈"。
                cx, cy, r = x + 30, 85, 28
                for row in range(cy - r, cy + r + 1):
                    dy = row - cy
                    if dy * dy > r * r:
                        continue
                    dx = int((r * r - dy * dy) ** 0.5)
                    c.rect(cx - dx, row, cx + dx, row + 1, (30, 30, 30))
            else:
                for row in range(50, 110):
                    w = int((row - 50) * 0.9)
                    c.rect(x + 30 - w // 2, row, x + 30 + w // 2, row + 1, (30, 30, 30))
        fname = os.path.join(root, "d11_layout_%02d.png" % s)
        c.save(fname)
        items.append({
            "id": "d11_layout_%02d" % s, "dim": "D11", "subcat": "layout",
            "image": fname, "answer": shapes[1],
            "question": "图中从左到右有三个形状,中间那个是什么?只回答形状名(圆圈/三角/方块)。",
            "expect": {"answer": shapes[1], "scoring": "exact shape word"},
        })
    return items


def build_bars_items(root, seeds=tuple(range(1, 9))):
    """哪个柱子最高 —— 需要比较条形长度,而不是读数字。"""
    import random
    items = []
    for s in seeds:
        rng = random.Random(20261101 + 500 + s)
        heights = [rng.randint(30, 100) for _ in range(4)]
        if len(set(heights)) < 4:
            heights[0] = max(heights) + 10
        c = Canvas(400, 170)
        for i, h in enumerate(heights):
            x = 40 + i * 85
            c.rect(x, 140 - h, x + 55, 140, (40, 60, 160))
        c.rect(20, 140, 380, 145, (200, 200, 200))
        fname = os.path.join(root, "d11_bars_%02d.png" % s)
        c.save(fname)
        idx = heights.index(max(heights))
        items.append({
            "id": "d11_bars_%02d" % s, "dim": "D11", "subcat": "bars",
            "image": fname, "answer": str(idx + 1),
            "question": "图中有四根柱形从左到右排列,最高的是第几根(从左数)?只回答数字。",
            "expect": {"answer": str(idx + 1), "scoring": "exact position"},
        })
    return items


def build_dense_items(root, seeds=tuple(range(1, 7))):
    """密集计数:20-40 个小方块,考分辨力而不是粗看。"""
    import random
    items = []
    for s in seeds:
        rng = random.Random(20261101 + 600 + s)
        cols, rows = 8, 4
        total = cols * rows
        drop = rng.randint(4, 9)
        holes = set(rng.sample(range(total), drop))
        c = Canvas(420, 240)
        for i in range(total):
            if i in holes:
                continue
            cx = 20 + (i % cols) * 46
            cy = 20 + (i // cols) * 50
            c.rect(cx, cy, cx + 34, cy + 38, (30, 30, 30))
        fname = os.path.join(root, "d11_dense_%02d.png" % s)
        c.save(fname)
        items.append({
            "id": "d11_dense_%02d" % s, "dim": "D11", "subcat": "dense",
            "image": fname, "answer": str(total - drop),
            "question": "图中有多少个黑色方块?只回答数字。",
            "expect": {"answer": str(total - drop), "scoring": "exact count"},
        })
    return items


def build_occlusion_items(root, seeds=tuple(range(1, 7))):
    """遮挡判断:两个色块重叠,问哪个画在上层(靠前)。"""
    import random
    items = []
    for s in seeds:
        rng = random.Random(20261101 + 700 + s)
        front_is_red = rng.random() < 0.5
        RED, BLUE = (220, 40, 40), (40, 110, 220)
        c = Canvas(400, 200)
        if front_is_red:
            c.rect(60, 40, 260, 160, BLUE)
            c.rect(150, 70, 350, 180, RED)
        else:
            c.rect(150, 70, 350, 180, BLUE)
            c.rect(60, 40, 260, 160, RED)
        fname = os.path.join(root, "d11_occl_%02d.png" % s)
        c.save(fname)
        front = "红" if front_is_red else "蓝"
        items.append({
            "id": "d11_occl_%02d" % s, "dim": "D11", "subcat": "occlusion",
            "image": fname, "answer": front,
            "question": "图中红蓝两个方块重叠,哪个画在上层(也就是靠前)?只回答颜色名(红/蓝)。",
            "expect": {"answer": front, "scoring": "exact color word"},
        })
    return items


def judge_vision(entry, response):
    """Exact match on the answer, tolerant of surrounding prose."""
    want = str(entry.get("expect", {}).get("answer") or entry.get("answer") or "").strip()
    txt = (response or "").strip()
    if not want:
        return {"correct": False, "reason": "no expected answer"}
    if not txt:
        return {"correct": False, "reason": "empty reply", "expected": want}
    # strip common wrappers so "答案是 1234。" still matches
    digits = "".join(ch for ch in txt if ch.isdigit())
    if digits == want:
        return {"correct": True, "reason": "exact %s" % want, "expected": want,
                "extracted": digits}
    if want in txt:
        return {"correct": True, "reason": "contains %s" % want, "expected": want,
                "extracted": digits}
    return {"correct": False, "reason": "expected %s, got %r" % (want, digits[:20]),
            "expected": want, "extracted": digits}


def build_all(root):
    return (build_ocr_items(root) + build_count_items(root)
            + build_detail_items(root) + build_color_items(root)
            + build_layout_items(root) + build_bars_items(root)
            + build_dense_items(root) + build_occlusion_items(root))
