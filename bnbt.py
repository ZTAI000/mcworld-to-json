# -*- coding: utf-8 -*-
"""
基岩版NBT编解码器
"""

import struct

# NBT 标签类型
TAG_End = 0
TAG_Byte = 1
TAG_Short = 2
TAG_Int = 3
TAG_Long = 4
TAG_Float = 5
TAG_Double = 6
TAG_ByteArray = 7
TAG_String = 8
TAG_List = 9
TAG_Compound = 10
TAG_IntArray = 11
TAG_LongArray = 12


class NBTBase:
    """所有 NBT 值的基类包装，tag 表示类型，value 表示 Python 值。"""
    __slots__ = ("tag", "value")

    def __init__(self, tag, value):
        self.tag = tag
        self.value = value

    def __repr__(self):
        return f"NBT(tag={self.tag}, value={self.value!r})"


class Reader:
    def __init__(self, data, pos=0):
        self.data = data
        self.pos = pos

    def remaining(self):
        return len(self.data) - self.pos

    def read(self, n):
        if n < 0:
            raise ValueError("NBT 读取长度不能为负")
        if self.pos + n > len(self.data):
            raise ValueError("NBT 数据越界读取")
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b

    def u8(self):
        if self.pos + 1 > len(self.data):
            raise ValueError("NBT 数据越界读取")
        v = self.data[self.pos]
        self.pos += 1
        return v

    def i8(self):
        v = struct.unpack_from("<b", self.data, self.pos)[0]
        self.pos += 1
        return v

    def i16(self):
        v = struct.unpack_from("<h", self.data, self.pos)[0]
        self.pos += 2
        return v

    def u16(self):
        v = struct.unpack_from("<H", self.data, self.pos)[0]
        self.pos += 2
        return v

    def i32(self):
        v = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return v

    def i64(self):
        v = struct.unpack_from("<q", self.data, self.pos)[0]
        self.pos += 8
        return v

    def f32(self):
        v = struct.unpack_from("<f", self.data, self.pos)[0]
        self.pos += 4
        return v

    def f64(self):
        v = struct.unpack_from("<d", self.data, self.pos)[0]
        self.pos += 8
        return v

    def read_string(self):
        # 基岩版字符串: 2字节小端长度前缀(unsigned short) + UTF-8 字节
        length = self.u16()
        raw = self.read(length)
        return raw.decode("utf-8", errors="surrogateescape")


class Writer:
    def __init__(self):
        self.buf = bytearray()

    def write(self, b):
        self.buf += b

    def u8(self, v):
        self.buf += struct.pack("<B", v & 0xFF)

    def i8(self, v):
        self.buf += struct.pack("<b", v)

    def i16(self, v):
        self.buf += struct.pack("<h", v)

    def u16(self, v):
        self.buf += struct.pack("<H", v)

    def i32(self, v):
        self.buf += struct.pack("<i", v)

    def i64(self, v):
        self.buf += struct.pack("<q", v)

    def f32(self, v):
        self.buf += struct.pack("<f", v)

    def f64(self, v):
        self.buf += struct.pack("<d", v)

    def write_string(self, s):
        raw = s.encode("utf-8", errors="surrogateescape")
        self.buf += struct.pack("<H", len(raw))
        self.buf += raw

    def getvalue(self):
        return bytes(self.buf)


def _read_payload(r: Reader, tag: int):
    if tag == TAG_End:
        return None
    if tag == TAG_Byte:
        return r.i8()
    if tag == TAG_Short:
        return r.i16()
    if tag == TAG_Int:
        return r.i32()
    if tag == TAG_Long:
        return r.i64()
    if tag == TAG_Float:
        return r.f32()
    if tag == TAG_Double:
        return r.f64()
    if tag == TAG_ByteArray:
        n = r.i32()
        raw = r.read(n)
        return list(struct.unpack(f"<{n}b", raw)) if n else []
    if tag == TAG_String:
        return r.read_string()
    if tag == TAG_List:
        sub_tag = r.u8()
        n = r.i32()
        items = []
        for _ in range(n):
            items.append(NBTBase(sub_tag, _read_payload(r, sub_tag)))
        return {"item_tag": sub_tag, "items": items}
    if tag == TAG_Compound:
        d = {}
        order = []
        while True:
            sub_tag = r.u8()
            if sub_tag == TAG_End:
                break
            name = r.read_string()
            val = _read_payload(r, sub_tag)
            d[name] = NBTBase(sub_tag, val)
            order.append(name)
        return {"fields": d, "order": order}
    if tag == TAG_IntArray:
        n = r.i32()
        raw = r.read(n * 4)
        return list(struct.unpack(f"<{n}i", raw)) if n else []
    if tag == TAG_LongArray:
        n = r.i32()
        raw = r.read(n * 8)
        return list(struct.unpack(f"<{n}q", raw)) if n else []
    raise ValueError(f"未知 NBT 标签类型: {tag}")


def _write_payload(w: Writer, tag: int, value):
    if tag == TAG_End:
        return
    if tag == TAG_Byte:
        w.i8(value)
    elif tag == TAG_Short:
        w.i16(value)
    elif tag == TAG_Int:
        w.i32(value)
    elif tag == TAG_Long:
        w.i64(value)
    elif tag == TAG_Float:
        w.f32(value)
    elif tag == TAG_Double:
        w.f64(value)
    elif tag == TAG_ByteArray:
        n = len(value)
        w.i32(n)
        if n:
            w.write(struct.pack(f"<{n}b", *value))
    elif tag == TAG_String:
        w.write_string(value)
    elif tag == TAG_List:
        sub_tag = value["item_tag"]
        items = value["items"]
        w.u8(sub_tag)
        w.i32(len(items))
        for it in items:
            _write_payload(w, sub_tag, it.value)
    elif tag == TAG_Compound:
        fields = value["fields"]
        order = value["order"]
        for name in order:
            nb = fields[name]
            w.u8(nb.tag)
            w.write_string(name)
            _write_payload(w, nb.tag, nb.value)
        w.u8(TAG_End)
    elif tag == TAG_IntArray:
        n = len(value)
        w.i32(n)
        if n:
            w.write(struct.pack(f"<{n}i", *value))
    elif tag == TAG_LongArray:
        n = len(value)
        w.i32(n)
        if n:
            w.write(struct.pack(f"<{n}q", *value))
    else:
        raise ValueError(f"未知 NBT 标签类型: {tag}")


def parse_one(data: bytes, pos: int = 0):
    """从 data[pos:] 解析一个完整的 NBT 顶层标签 (name, NBTBase, 新pos)。"""
    r = Reader(data, pos)
    tag = r.u8()
    if tag == TAG_End:
        return None, None, r.pos
    name = r.read_string()
    value = _read_payload(r, tag)
    return name, NBTBase(tag, value), r.pos


def parse_all(data: bytes):
    """解析 data 中所有连续排列的顶层 NBT 标签（基岩版区块中常见多个紧邻的复合标签）。
    容错：如果某条解析失败，返回已成功解析的部分。
    """
    pos = 0
    results = []
    n = len(data)
    while pos < n:
        try:
            name, nb, newpos = parse_one(data, pos)
            if nb is None:
                break
            results.append((name, nb))
            if newpos <= pos:
                break  # 防止死循环
            pos = newpos
        except Exception:
            break
    return results, pos


def write_one(name: str, nb: NBTBase) -> bytes:
    w = Writer()
    w.u8(nb.tag)
    w.write_string(name)
    _write_payload(w, nb.tag, nb.value)
    return w.getvalue()


def write_all(entries) -> bytes:
    """entries: [(name, NBTBase), ...]"""
    out = bytearray()
    for name, nb in entries:
        out += write_one(name, nb)
    return bytes(out)


# ---------- 便捷访问/构造 Compound 的辅助函数 ----------

def compound_get(nb: NBTBase, key: str):
    """从 Compound 类型的 NBTBase 中取字段的 NBTBase，不存在返回 None。"""
    assert nb.tag == TAG_Compound
    return nb.value["fields"].get(key)


def compound_get_value(nb: NBTBase, key: str, default=None):
    f = compound_get(nb, key)
    return f.value if f is not None else default


def compound_set(nb: NBTBase, key: str, value_nb: NBTBase):
    """设置/新增 Compound 的字段，保持顺序。"""
    assert nb.tag == TAG_Compound
    fields = nb.value["fields"]
    order = nb.value["order"]
    if key not in fields:
        order.append(key)
    fields[key] = value_nb


def make_string(s: str) -> NBTBase:
    return NBTBase(TAG_String, s)


def new_compound() -> NBTBase:
    return NBTBase(TAG_Compound, {"fields": {}, "order": []})
