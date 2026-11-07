# -*- coding: utf-8 -*-
"""parquet_lite.py - read Parquet files with nothing but the Python standard library.

The project rule is "zero third-party dependencies", so pyarrow is not available,
yet five of the authoritative public benchmarks only ship as .parquet. This module
implements the slice of the format those files actually use:

  footer      thrift compact protocol -> schema tree, row groups, column chunks
  codecs      UNCOMPRESSED, SNAPPY (raw block format)
  pages       DATA_PAGE (v1), DATA_PAGE_V2, DICTIONARY_PAGE
  encodings   PLAIN, PLAIN_DICTIONARY, RLE_DICTIONARY, RLE, BIT_PACKED,
              DELTA_BINARY_PACKED, DELTA_LENGTH_BYTE_ARRAY, DELTA_BYTE_ARRAY
  levels      RLE / bit-packed hybrid (definition + repetition)
  layout      flat scalars, LIST<T>, and struct-of-lists (max repetition 1)

Anything outside that raises UnsupportedEncoding naming BOTH the column and the
encoding, so an unfamiliar file fails loudly instead of silently yielding empty
rows.

    import parquet_lite
    t = parquet_lite.read_parquet("x.parquet")
    t.num_rows, t.columns["question"][:3]
"""

import struct

TYPE_NAMES = {0: "BOOLEAN", 1: "INT32", 2: "INT64", 3: "INT96", 4: "FLOAT",
              5: "DOUBLE", 6: "BYTE_ARRAY", 7: "FIXED_LEN_BYTE_ARRAY"}
CODEC_NAMES = {0: "UNCOMPRESSED", 1: "SNAPPY", 2: "GZIP", 3: "LZO",
               4: "BROTLI", 5: "LZ4", 6: "ZSTD", 7: "LZ4_RAW"}
ENC_NAMES = {0: "PLAIN", 1: "GROUP_VAR_INT", 2: "PLAIN_DICTIONARY", 3: "RLE",
             4: "BIT_PACKED", 5: "DELTA_BINARY_PACKED",
             6: "DELTA_LENGTH_BYTE_ARRAY", 7: "DELTA_BYTE_ARRAY",
             8: "RLE_DICTIONARY", 9: "BYTE_STREAM_SPLIT"}

REQUIRED, OPTIONAL, REPEATED = 0, 1, 2
T_BOOLEAN, T_INT32, T_INT64, T_INT96 = 0, 1, 2, 3
T_FLOAT, T_DOUBLE, T_BYTE_ARRAY, T_FIXED = 4, 5, 6, 7


class UnsupportedEncoding(Exception):
    """A column uses a page encoding / codec this reader does not implement."""

class _T(object):
    """Thrift compact-protocol reader over a bytes buffer."""

    __slots__ = ("b", "i")

    def __init__(self, b, i=0):
        self.b = b
        self.i = i

    def byte(self):
        v = self.b[self.i]
        self.i += 1
        return v

    def varint(self):
        r = 0
        s = 0
        while True:
            v = self.byte()
            r |= (v & 0x7f) << s
            if not (v & 0x80):
                return r
            s += 7

    def zigzag(self):
        n = self.varint()
        return (n >> 1) ^ -(n & 1)

    def binary(self):
        n = self.varint()
        v = self.b[self.i:self.i + n]
        self.i += n
        return v


def _tskip(r, t):
    if t in (1, 2):
        return
    if t == 3:
        r.i += 1
        return
    if t in (4, 5, 6):
        r.zigzag()
        return
    if t == 7:
        r.i += 8
        return
    if t == 8:
        r.binary()
        return
    if t in (9, 10):
        h = r.byte()
        sz, et = h >> 4, h & 0x0f
        if sz == 15:
            sz = r.varint()
        for _ in range(sz):
            _tskip(r, et)
        return
    if t == 11:
        sz = r.varint()
        if sz:
            h = r.byte()
            kt, vt = h >> 4, h & 0x0f
            for _ in range(sz):
                _tskip(r, kt)
                _tskip(r, vt)
        return
    if t == 12:
        _tstruct(r)
        return
    raise ValueError("thrift compact: bad field type %d" % t)


def _tvalue(r, t):
    if t == 1:
        return True
    if t == 2:
        return False
    if t == 3:
        return r.byte()
    if t in (4, 5, 6):
        return r.zigzag()
    if t == 7:
        v = struct.unpack("<d", r.b[r.i:r.i + 8])[0]
        r.i += 8
        return v
    if t == 8:
        return r.binary()
    if t in (9, 10):
        h = r.byte()
        sz, et = h >> 4, h & 0x0f
        if sz == 15:
            sz = r.varint()
        return [_tvalue(r, et) for _ in range(sz)]
    if t == 11:
        _tskip(r, 11)
        return None
    if t == 12:
        return _tstruct(r)
    raise ValueError("thrift compact: bad field type %d" % t)


def _tstruct(r):
    """Read one struct -> {field_id: value}."""
    out = {}
    last = 0
    while True:
        h = r.byte()
        if h == 0:
            return out
        delta, t = h >> 4, h & 0x0f
        fid = last + delta if delta else r.zigzag()
        last = fid
        out[fid] = _tvalue(r, t)

def snappy_decompress(buf):
    """Raw snappy block format: varint output length, then literal/copy tags."""
    n = 0
    shift = 0
    i = 0
    while True:
        b = buf[i]
        i += 1
        n |= (b & 0x7f) << shift
        if not (b & 0x80):
            break
        shift += 7
    out = bytearray(n)
    pos = 0
    ln = len(buf)
    while i < ln and pos < n:
        tag = buf[i]
        i += 1
        kind = tag & 3
        if kind == 0:
            size = tag >> 2
            if size < 60:
                size += 1
            else:
                nb = size - 59
                size = int.from_bytes(buf[i:i + nb], "little") + 1
                i += nb
            out[pos:pos + size] = buf[i:i + size]
            pos += size
            i += size
        else:
            if kind == 1:
                length = ((tag >> 2) & 0x07) + 4
                offset = ((tag >> 5) << 8) | buf[i]
                i += 1
            elif kind == 2:
                length = (tag >> 2) + 1
                offset = int.from_bytes(buf[i:i + 2], "little")
                i += 2
            else:
                length = (tag >> 2) + 1
                offset = int.from_bytes(buf[i:i + 4], "little")
                i += 4
            if offset == 0 or offset > pos:
                raise ValueError("snappy: bad copy offset %d at out pos %d" % (offset, pos))
            start = pos - offset
            for k in range(length):
                out[pos] = out[start + k]
                pos += 1
    if pos != n:
        raise ValueError("snappy: produced %d of %d bytes" % (pos, n))
    return bytes(out)


def _decompress(codec, buf, colname):
    if codec == 0:
        return buf
    if codec == 1:
        return snappy_decompress(buf)
    raise UnsupportedEncoding(
        "column '%s' uses unsupported compression codec %s"
        % (colname, CODEC_NAMES.get(codec, codec)))

def _nbits(v):
    return int(v).bit_length()


def _unpack_lsb(data, width, count):
    """Parquet bit-packing is LSB-first across the byte stream."""
    if width == 0:
        return [0] * count
    total = count * width
    nb = (total + 7) // 8
    big = int.from_bytes(bytes(data[:nb]), "little")
    mask = (1 << width) - 1
    return [(big >> (k * width)) & mask for k in range(count)]


def _rle_decode(buf, width, count):
    """RLE / bit-packed hybrid, as used for levels and dictionary indices."""
    out = []
    i = 0
    n = len(buf)
    while len(out) < count and i < n:
        h = 0
        shift = 0
        while True:
            b = buf[i]
            i += 1
            h |= (b & 0x7f) << shift
            if not (b & 0x80):
                break
            shift += 7
        if h & 1:
            groups = h >> 1
            need = groups * width
            out.extend(_unpack_lsb(buf[i:i + need], width, groups * 8))
            i += need
        else:
            run = h >> 1
            nbytes = (width + 7) // 8
            v = int.from_bytes(bytes(buf[i:i + nbytes]), "little") if nbytes else 0
            i += nbytes
            out.extend([v] * run)
    if len(out) < count:
        out.extend([0] * (count - len(out)))
    return out[:count]

def _decode_plain(data, off, n, ptype, type_length, colname):
    out = []
    if ptype == T_BOOLEAN:
        raw = _unpack_lsb(data[off:off + (n + 7) // 8], 1, n)
        return [bool(v) for v in raw], off + (n + 7) // 8
    if ptype == T_INT32:
        end = off + 4 * n
        return list(struct.unpack("<%di" % n, bytes(data[off:end]))), end
    if ptype == T_INT64:
        end = off + 8 * n
        return list(struct.unpack("<%dq" % n, bytes(data[off:end]))), end
    if ptype == T_FLOAT:
        end = off + 4 * n
        return list(struct.unpack("<%df" % n, bytes(data[off:end]))), end
    if ptype == T_DOUBLE:
        end = off + 8 * n
        return list(struct.unpack("<%dd" % n, bytes(data[off:end]))), end
    if ptype == T_INT96:
        for _ in range(n):
            out.append(bytes(data[off:off + 12]))
            off += 12
        return out, off
    if ptype == T_BYTE_ARRAY:
        for _ in range(n):
            ln = int.from_bytes(bytes(data[off:off + 4]), "little")
            off += 4
            out.append(bytes(data[off:off + ln]))
            off += ln
        return out, off
    if ptype == T_FIXED:
        for _ in range(n):
            out.append(bytes(data[off:off + type_length]))
            off += type_length
        return out, off
    raise UnsupportedEncoding("column '%s' uses unsupported physical type %s"
                              % (colname, TYPE_NAMES.get(ptype, ptype)))

def _delta_binary_packed(data, off, colname):
    """Return (values, new_offset) for one DELTA_BINARY_PACKED block sequence."""
    r = _T(data, off)
    block_size = r.varint()
    nmini = r.varint()
    total = r.varint()
    first = r.zigzag()
    if total == 0:
        return [], r.i
    vals = [first]
    per_mini = block_size // nmini if nmini else 0
    while len(vals) < total:
        min_delta = r.zigzag()
        widths = [data[r.i + k] for k in range(nmini)]
        r.i += nmini
        for w in widths:
            nb = (per_mini * w + 7) // 8
            chunk = data[r.i:r.i + nb]
            r.i += nb
            if len(vals) >= total:
                continue
            if w == 0:
                deltas = [0] * per_mini
            else:
                deltas = _unpack_lsb(chunk, w, per_mini)
            for d in deltas:
                if len(vals) >= total:
                    break
                vals.append(vals[-1] + min_delta + d)
    return vals, r.i


def _decode_values(data, off, enc, nn, ptype, type_length, dictionary, colname):
    if nn == 0:
        return []
    if enc in (2, 8):
        if dictionary is None:
            raise UnsupportedEncoding(
                "column '%s' uses %s but no dictionary page was found"
                % (colname, ENC_NAMES.get(enc, enc)))
        width = data[off]
        idx = _rle_decode(data[off + 1:], width, nn)
        return [dictionary[i] for i in idx]
    if enc == 0:
        vals, _ = _decode_plain(data, off, nn, ptype, type_length, colname)
        return vals
    if enc == 3:
        width = data[off]
        vals = _rle_decode(data[off + 1:], width, nn)
        return [bool(v) for v in vals]
    if enc == 5:
        vals, _ = _delta_binary_packed(data, off, colname)
        return vals[:nn]
    if enc == 6:
        lens, o = _delta_binary_packed(data, off, colname)
        out = []
        for ln in lens[:nn]:
            out.append(bytes(data[o:o + ln]))
            o += ln
        return out
    if enc == 7:
        prefixes, o = _delta_binary_packed(data, off, colname)
        lens, o = _delta_binary_packed(data, o, colname)
        out = []
        prev = b""
        for i in range(nn):
            suf = bytes(data[o:o + lens[i]])
            o += lens[i]
            prev = prev[:prefixes[i]] + suf
            out.append(prev)
        return out
    raise UnsupportedEncoding(
        "column '%s' uses unsupported parquet encoding %s"
        % (colname, ENC_NAMES.get(enc, enc)))


class Node(object):
    __slots__ = ("name", "rep", "ptype", "converted", "children", "path",
                 "max_def", "max_rep", "defs", "type_length")

    def __init__(self, el, parent):
        raw = el.get(4)
        self.name = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        self.rep = el.get(3, REQUIRED)
        self.ptype = el.get(1)
        self.converted = el.get(6)
        self.type_length = el.get(2, 0)
        self.children = []
        base_def = parent.max_def if parent else 0
        base_rep = parent.max_rep if parent else 0
        bump = 1 if self.rep in (OPTIONAL, REPEATED) else 0
        self.max_def = base_def + bump
        self.max_rep = base_rep + (1 if self.rep == REPEATED else 0)
        self.path = ((parent.path if parent else ()) + ((self.name,) if parent else ()))
        self.defs = (((parent.defs if parent else ()) + (self.max_def,)) if parent
                     else (self.max_def,))


def _build_schema(elements):
    idx = [0]

    def build(parent):
        el = elements[idx[0]]
        idx[0] += 1
        node = Node(el, parent)
        for _ in range(el.get(5, 0) or 0):
            node.children.append(build(node))
        return node

    root = build(None)
    leaves = []

    def walk(n):
        if not n.children:
            leaves.append(n)
        for c in n.children:
            walk(c)

    walk(root)
    return root, leaves


_PARENT = {}

# Names used by the parquet LIST logical type for its single repeated child group.
_LIST_WRAPPERS = ("list", "bag", "array", "element", "item")


def _assemble(entries, leaf, colname):
    """entries = [(rep, def, value)] in file order -> one Python value per row."""
    if leaf.max_rep == 0:
        return [v if d >= leaf.max_def else None for _rep, d, v in entries]

    if leaf.max_rep > 1:
        raise UnsupportedEncoding(
            "column '%s' nests lists more than one level deep (max repetition %d); "
            "this reader only assembles single-level lists" % (colname, leaf.max_rep))

    chain = []
    node = leaf
    while node is not None and node.path:
        chain.append(node)
        node = _PARENT.get(id(node))
    chain.reverse()

    r = -1
    for i, nd in enumerate(chain):
        if nd.rep == REPEATED:
            r = i
    if r < 0:
        raise UnsupportedEncoding(
            "column '%s' has repetition levels but no repeated node" % colname)
    if len(chain) - 1 - r > 1:
        raise UnsupportedEncoding(
            "column '%s' is a list of structs; this reader only assembles "
            "single-level lists of scalars" % colname)

    defs = list(leaf.defs)
    list_def = defs[r - 1] if r > 0 else 0
    max_def = leaf.max_def

    rows = []
    cur = None
    open_row = False

    def flush():
        if open_row:
            rows.append(cur)

    for rep, d, v in entries:
        if rep == 0:
            flush()
            cur = None
            open_row = True
        if d >= max_def:
            if cur is None:
                cur = []
            cur.append(v)
        elif d >= list_def:
            if cur is None:
                cur = []
    flush()
    return rows


class Table(object):
    def __init__(self, columns, num_rows, schema_names):
        self.columns = columns
        self.num_rows = num_rows
        self.schema_names = schema_names

    def __repr__(self):
        return "<parquet Table rows=%d cols=%s>" % (
            self.num_rows, ",".join(self.columns))


def _read_page_header(data, off):
    r = _T(data, off)
    return _tstruct(r), r.i


def _read_chunk(data, meta, leaf, colname):
    """Decode one column chunk -> list of (rep, def, value)."""
    codec = meta.get(4, 0)
    num_values = meta.get(5, 0)
    start = meta.get(9, 0)
    dstart = meta.get(11)
    if dstart is not None and dstart < start:
        start = dstart
    size = meta.get(7, 0)
    end = start + size

    ptype = meta.get(1, leaf.ptype)
    type_length = leaf.type_length
    dictionary = None
    entries = []
    pos = start

    while len(entries) < num_values and pos < end:
        hdr, body_off = _read_page_header(data, pos)
        kind = hdr.get(1)
        csize = hdr.get(3, 0)
        body = data[body_off:body_off + csize]
        pos = body_off + csize

        if kind == 2:                                   # DICTIONARY_PAGE
            dph = hdr.get(7) or {}
            raw = _decompress(codec, body, colname)
            dictionary, _ = _decode_plain(raw, 0, dph.get(1, 0), ptype,
                                          type_length, colname)
            continue

        if kind == 0:                                   # DATA_PAGE v1
            dph = hdr.get(5) or {}
            n = dph.get(1, 0)
            enc = dph.get(2, 0)
            raw = _decompress(codec, body, colname)
            o = 0
            reps = None
            defs = None
            if leaf.max_rep > 0:
                ln = int.from_bytes(bytes(raw[o:o + 4]), "little")
                o += 4
                reps = _rle_decode(raw[o:o + ln], _nbits(leaf.max_rep), n)
                o += ln
            if leaf.max_def > 0:
                ln = int.from_bytes(bytes(raw[o:o + 4]), "little")
                o += 4
                defs = _rle_decode(raw[o:o + ln], _nbits(leaf.max_def), n)
                o += ln
            nn = n if defs is None else sum(1 for d in defs if d == leaf.max_def)
            vals = _decode_values(raw, o, enc, nn, ptype, type_length,
                                  dictionary, colname)
        elif kind == 3:                                 # DATA_PAGE v2
            v2 = hdr.get(8) or {}
            n = v2.get(1, 0)
            nulls = v2.get(2, 0)
            enc = v2.get(4, 0)
            dlen = v2.get(5, 0)
            rlen = v2.get(6, 0)
            compressed = v2.get(7, True)
            reps = None
            defs = None
            o = 0
            if leaf.max_rep > 0:
                reps = _rle_decode(body[o:o + rlen], _nbits(leaf.max_rep), n)
                o += rlen
            if leaf.max_def > 0:
                defs = _rle_decode(body[o:o + dlen], _nbits(leaf.max_def), n)
                o += dlen
            vbytes = body[o:]
            if compressed:
                vbytes = _decompress(codec, vbytes, colname)
            nn = n - nulls
            vals = _decode_values(vbytes, 0, enc, nn, ptype, type_length,
                                  dictionary, colname)
        else:
            continue                                    # index / unknown page

        vi = 0
        for i in range(n):
            d = defs[i] if defs is not None else leaf.max_def
            rp = reps[i] if reps is not None else 0
            if d == leaf.max_def:
                entries.append((rp, d, vals[vi]))
                vi += 1
            else:
                entries.append((rp, d, None))
    return entries


def _to_python(v):
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    if isinstance(v, list):
        return [_to_python(x) for x in v]
    return v


def read_parquet(path=None, data=None, columns=None):
    """Read a parquet file; columns limits decoding to the named top-level fields."""
    if data is None:
        with open(path, "rb") as f:
            data = f.read()
    if len(data) < 12 or data[-4:] != b"PAR1":
        raise ValueError("not a parquet file (missing trailing PAR1)")
    mlen = struct.unpack("<I", data[-8:-4])[0]
    meta = data[len(data) - 8 - mlen:len(data) - 8]
    fmd = _tstruct(_T(meta))
    num_rows = fmd.get(3, 0)
    schema = fmd.get(2, [])
    row_groups = fmd.get(4, []) or []

    root, leaves = _build_schema(schema)

    def link(n):
        for c in n.children:
            _PARENT[id(c)] = n
            link(c)

    link(root)

    want = set(columns) if columns else None
    top = {}
    for child in root.children:
        if want is not None and child.name not in want:
            continue
        if child.children:
            top[child.name] = dict((gc.name, []) for gc in child.children)
        else:
            top[child.name] = []

    for ci, leaf in enumerate(leaves):
        topname = leaf.path[0]
        if want is not None and topname not in want:
            continue
        sub = top[topname]
        target = sub[leaf.path[1]] if isinstance(sub, dict) else sub
        colname = ".".join(leaf.path)
        for rg in row_groups:
            chunks = rg.get(1) or []
            if ci >= len(chunks):
                continue
            cm = chunks[ci].get(3) or {}
            if not cm:
                continue
            entries = _read_chunk(data, cm, leaf, colname)
            target.extend(_assemble(entries, leaf, colname))

    flat = {}
    for name, val in top.items():
        if isinstance(val, dict):
            for k in val:
                val[k] = [_to_python(x) for x in val[k]]
            # parquet LIST<T> arrives as one repeated group; unwrap it to a plain list
            if len(val) == 1 and list(val)[0] in _LIST_WRAPPERS:
                flat[name] = val[list(val)[0]]
            else:
                flat[name] = val
        else:
            flat[name] = [_to_python(x) for x in top[name]]
    return Table(flat, num_rows, [c.name for c in root.children])


if __name__ == "__main__":
    import sys
    t = read_parquet(sys.argv[1])
    print("rows=%d cols=%d" % (t.num_rows, len(t.columns)))
    for k in t.columns:
        v = t.columns[k]
        first = v[0] if v else None
        print("  %-24s %-8s %r" % (k, type(first).__name__, str(first)[:60]))
