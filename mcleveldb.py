# -*- coding: utf-8 -*-
"""
存档 LevelDB 读取器
"""

import os
import struct
import zlib
import glob

MASK_DELTA = 0xa282ead8

def unmask_crc(masked):
    rot = (masked - MASK_DELTA) & 0xFFFFFFFF
    unrot = ((rot >> 17) | ((rot << 15) & 0xFFFFFFFF)) & 0xFFFFFFFF
    return unrot


# ---------------- CRC32C ----------------
_CRC32C_TABLE = None

def _build_crc32c_table():
    global _CRC32C_TABLE
    poly = 0x82F63B78
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            if c & 1:
                c = (c >> 1) ^ poly
            else:
                c >>= 1
        table.append(c)
    _CRC32C_TABLE = table

def crc32c(data: bytes, crc: int = 0) -> int:
    if _CRC32C_TABLE is None:
        _build_crc32c_table()
    crc ^= 0xFFFFFFFF
    for b in data:
        crc = _CRC32C_TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return (crc ^ 0xFFFFFFFF) & 0xFFFFFFFF


# ---------------- varint ----------------
def read_varint32(data, pos):
    result = 0
    shift = 0
    while True:
        if pos >= len(data):
            raise ValueError("varint 读取越界")
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, pos

def read_varint64(data, pos):
    return read_varint32(data, pos)  # Python int 无限精度

def write_varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


# ---------------- Log format (WAL / MANIFEST) ----------------
BLOCK_SIZE = 32768
HEADER_SIZE = 7  # checksum(4) + length(2) + type(1)

# Record types
RECORD_ZERO   = 0  # padding
RECORD_FULL   = 1
RECORD_FIRST  = 2
RECORD_MIDDLE = 3
RECORD_LAST   = 4

def read_log_records(data: bytes):
    """解析 log 格式文件，返回还原后的完整 record 列表 (bytes)。"""
    records = []
    pos = 0
    n = len(data)
    cur = bytearray()
    in_fragment = False
    while pos + HEADER_SIZE <= n:
        block_start = pos - (pos % BLOCK_SIZE)
        block_end = block_start + BLOCK_SIZE
        if pos + HEADER_SIZE > block_end:
            pos = block_end
            continue
        checksum = struct.unpack_from("<I", data, pos)[0]
        length = struct.unpack_from("<H", data, pos + 4)[0]
        rtype = data[pos + 6]
        body_start = pos + HEADER_SIZE
        body_end = body_start + length
        if rtype == RECORD_ZERO:
            pos = block_end
            in_fragment = False
            cur = bytearray()
            continue
        if body_end > n:
            break
        body = data[body_start:body_end]
        if rtype == RECORD_FULL:
            records.append(bytes(body))
            in_fragment = False
            cur = bytearray()
        elif rtype == RECORD_FIRST:
            cur = bytearray(body)
            in_fragment = True
        elif rtype == RECORD_MIDDLE:
            if in_fragment:
                cur += body
        elif rtype == RECORD_LAST:
            if in_fragment:
                cur += body
                records.append(bytes(cur))
            in_fragment = False
            cur = bytearray()
        pos = body_end
    return records


# ---------------- SSTable ----------------
FOOTER_LEN = 48  # 标准_level_db footer
TABLE_MAGIC = 0xdb4775248b80fb57

# 基岩版可能使用不同的 footer 长度
FOOTER_LEN_V5 = 53  # 新版 LevelDB footer（带 max_compressed_size）

TYPE_DELETION = 0
TYPE_VALUE = 1


def _decode_block_contents(raw_block: bytes):
    """block trailer: 1 byte compression type + 4 byte crc32c

    基岩版 LevelDB 使用的压缩类型：
      0 = 无压缩
      1 = zlib (标准, 带 header)
      2 = zlib (变体)
      4 = raw deflate (无 zlib header) — Mojang LevelDB fork 新增
      5 = raw deflate (变体)
    其他未知类型: 尝试 raw deflate → 标准 zlib → 原样返回
    """
    if len(raw_block) < 5:
        return b""
    payload = raw_block[:-5]
    ctype = raw_block[-5]

    if ctype == 0:
        # 无压缩
        return payload

    if ctype in (4, 5):
        # Raw deflate (无 zlib header) — Mojang LevelDB 新格式
        try:
            return zlib.decompressobj(-15).decompress(payload)
        except Exception:
            try:
                return zlib.decompress(payload)
            except Exception:
                return payload

    if ctype in (1, 2):
        # 标准 zlib (带 header)
        try:
            return zlib.decompress(payload)
        except zlib.error:
            try:
                return zlib.decompressobj(-15).decompress(payload)
            except Exception:
                return payload

    # 未知压缩类型: 尝试 raw deflate → 标准 zlib → 原样返回
    try:
        return zlib.decompressobj(-15).decompress(payload)
    except Exception:
        try:
            return zlib.decompress(payload)
        except Exception:
            return payload


def _parse_block_entries(block: bytes):
    """解析 data block 内容为 (key, value) 列表。"""
    if len(block) < 4:
        return []
    num_restarts = struct.unpack_from("<I", block, len(block) - 4)[0]
    restarts_offset = len(block) - 4 - 4 * num_restarts
    if restarts_offset < 0:
        restarts_offset = 0
    entries_data = block[:restarts_offset]
    pos = 0
    entries = []
    last_key = b""
    n = len(entries_data)
    while pos < n:
        try:
            shared, pos = read_varint32(entries_data, pos)
            nonshared, pos = read_varint32(entries_data, pos)
            vlen, pos = read_varint32(entries_data, pos)
        except Exception:
            break
        if pos + nonshared + vlen > n:
            break
        key_delta = entries_data[pos:pos + nonshared]
        pos += nonshared
        value = entries_data[pos:pos + vlen]
        pos += vlen
        if shared > len(last_key):
            shared = len(last_key)
        key = last_key[:shared] + key_delta
        last_key = key
        entries.append((key, value))
    return entries


def parse_handle(data, pos):
    off, pos = read_varint64(data, pos)
    size, pos = read_varint64(data, pos)
    return (off, size), pos


def read_block_raw(data: bytes, handle):
    off, size = handle
    raw = data[off: off + size + 5]
    return _decode_block_contents(raw)


def _parse_footer(data: bytes):
    """解析 SSTable footer，返回 (metaindex_handle, index_handle)。
    兼容标准 48 字节 footer 和新版 53 字节 footer。
    """
    n = len(data)
    # 尝试标准 footer (48 bytes)
    for flen in (FOOTER_LEN, FOOTER_LEN_V5):
        if n < flen:
            continue
        footer = data[-flen:]
        pos = 0
        try:
            metaindex_handle, pos = parse_handle(footer, pos)
            index_handle, pos = parse_handle(footer, pos)
            # 验证 magic number（仅对标准 footer）
            if flen == FOOTER_LEN:
                magic = struct.unpack_from("<Q", footer, FOOTER_LEN - 8)[0]
                if magic == TABLE_MAGIC:
                    return metaindex_handle, index_handle
                # magic 不匹配也继续尝试，某些基岩版可能魔数不同
            return metaindex_handle, index_handle
        except Exception:
            continue
    return None, None


def parse_sstable(data: bytes):
    """解析一个 sstable 文件，返回 ({key: value_or_None}, {key: seq})。
    value为None表示删除标记。"""
    if len(data) < FOOTER_LEN:
        return {}, {}
    metaindex_handle, index_handle = _parse_footer(data)
    if index_handle is None:
        return {}, {}

    try:
        index_block = read_block_raw(data, index_handle)
    except Exception:
        return {}, {}
    index_entries = _parse_block_entries(index_block)

    result = {}
    seqs = {}
    for _ikey, ivalue in index_entries:
        try:
            handle, _p = parse_handle(ivalue, 0)
            block = read_block_raw(data, handle)
        except Exception:
            continue
        entries = _parse_block_entries(block)
        for key, value in entries:
            if len(key) < 8:
                continue
            user_key = key[:-8]
            tag = struct.unpack_from("<Q", key, len(key) - 8)[0]
            seq = tag >> 8
            vtype = tag & 0xFF
            prev = result.get(user_key)
            if prev is None or seq > seqs.get(user_key, 0):
                if vtype == TYPE_DELETION:
                    result[user_key] = None
                else:
                    result[user_key] = value
                seqs[user_key] = seq
    return result, seqs


# ---------------- MANIFEST / CURRENT ----------------

def get_current_manifest(db_dir):
    cur_path = os.path.join(db_dir, "CURRENT")
    with open(cur_path, "r", encoding="utf-8") as f:
        name = f.read().strip()
    return os.path.join(db_dir, name), name


def list_sst_files(db_dir):
    files = []
    for ext in ("*.ldb", "*.sst"):
        files.extend(glob.glob(os.path.join(db_dir, ext)))
    return sorted(files)


def load_full_database(db_dir):
    """读取整个 db 目录，返回最终的 key->value 字典（bytes->bytes，已处理删除）。
    策略：
      1. 解析所有 .ldb/.sst 文件（按文件编号/mtime 从旧到新）。
      2. 回放所有 .log 文件中的 WriteBatch。
    """
    sst_files = list_sst_files(db_dir)

    def sort_key(path):
        base = os.path.basename(path)
        num = "".join(ch for ch in base if ch.isdigit())
        try:
            n = int(num) if num else 0
        except ValueError:
            n = 0
        return (n, os.path.getmtime(path))

    sst_files.sort(key=sort_key)

    merged = {}
    for path in sst_files:
        with open(path, "rb") as f:
            data = f.read()
        try:
            kv, _seqs = parse_sstable(data)
        except Exception:
            continue
        for k, v in kv.items():
            merged[k] = v

    log_files = sorted(
        glob.glob(os.path.join(db_dir, "*.log")),
        key=lambda p: sort_key(p)
    )
    for path in log_files:
        with open(path, "rb") as f:
            data = f.read()
        records = read_log_records(data)
        for rec in records:
            _apply_write_batch(rec, merged)

    final = {k: v for k, v in merged.items() if v is not None}
    return final


# ---------------- WriteBatch 解析 (用于 log record) ----------------

def _apply_write_batch(rec: bytes, target: dict):
    """WriteBatch 格式: 8字节sequence + 4字节count + 一串 (tag + key/value)"""
    if len(rec) < 12:
        return
    pos = 8  # skip sequence
    count = struct.unpack_from("<I", rec, pos)[0]
    pos += 4
    n = len(rec)
    for _ in range(count):
        if pos >= n:
            break
        tag = rec[pos]
        pos += 1
        if tag == 1:  # kTypeValue
            try:
                klen, pos = read_varint32(rec, pos)
                key = rec[pos:pos + klen]
                pos += klen
                vlen, pos = read_varint32(rec, pos)
                value = rec[pos:pos + vlen]
                pos += vlen
                target[key] = value
            except Exception:
                break
        elif tag == 0:  # kTypeDeletion
            try:
                klen, pos = read_varint32(rec, pos)
                key = rec[pos:pos + klen]
                pos += klen
                target[key] = None
            except Exception:
                break
        else:
            break
