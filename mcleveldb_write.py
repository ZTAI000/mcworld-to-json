# -*- coding: utf-8 -*-
"""
基岩版 LevelDB 安全写入模块
"""

import os
import re
import struct
import glob

import mcleveldb

BLOCK_SIZE = mcleveldb.BLOCK_SIZE
HEADER_SIZE = mcleveldb.HEADER_SIZE
RECORD_FULL = mcleveldb.RECORD_FULL
RECORD_FIRST = mcleveldb.RECORD_FIRST
RECORD_MIDDLE = mcleveldb.RECORD_MIDDLE
RECORD_LAST = mcleveldb.RECORD_LAST


def mask_crc(crc: int) -> int:
    """LevelDB CRC32C 掩码。"""
    rot = ((crc >> 15) | ((crc << 17) & 0xFFFFFFFF)) & 0xFFFFFFFF
    return (rot + mcleveldb.MASK_DELTA) & 0xFFFFFFFF


def _find_max_sequence(db_dir):
    max_seq = 0
    for path in glob.glob(os.path.join(db_dir, "*.ldb")) + glob.glob(os.path.join(db_dir, "*.sst")):
        try:
            with open(path, "rb") as f:
                data = f.read()
            _kv, seqs = mcleveldb.parse_sstable(data)
            for s in seqs:
                if s > max_seq:
                    max_seq = s
        except Exception:
            pass
    for path in glob.glob(os.path.join(db_dir, "*.log")):
        try:
            with open(path, "rb") as f:
                data = f.read()
            records = mcleveldb.read_log_records(data)
            for rec in records:
                if len(rec) >= 8:
                    seq = struct.unpack_from("<Q", rec, 0)[0]
                    if seq > max_seq:
                        max_seq = seq
        except Exception:
            pass
    return max_seq


def _build_write_batch(kv_pairs, seq):
    """构建 WriteBatch: seq(8) + count(4) + entries。"""
    buf = bytearray()
    buf += struct.pack("<Q", seq)
    buf += struct.pack("<I", len(kv_pairs))
    for key, value in kv_pairs:
        if value is None:
            buf += bytes([0])  # kTypeDeletion
            buf += mcleveldb.write_varint(len(key))
            buf += key
        else:
            buf += bytes([1])  # kTypeValue
            buf += mcleveldb.write_varint(len(key))
            buf += key
            buf += mcleveldb.write_varint(len(value))
            buf += value
    return bytes(buf)


def _wrap_as_log_blocks(record: bytes) -> bytes:
    """将 WriteBatch 记录包装为 LevelDB log block 格式。"""
    out = bytearray()
    total = len(record)
    pos = 0
    first_fragment = True

    while pos < total or first_fragment:
        block_pos = len(out) % BLOCK_SIZE
        avail = BLOCK_SIZE - block_pos
        if avail < HEADER_SIZE:
            out += b'\x00' * avail
            block_pos = 0
            avail = BLOCK_SIZE

        fragment_len = min(avail - HEADER_SIZE, total - pos)
        if fragment_len <= 0 and not first_fragment:
            break

        if first_fragment and fragment_len >= total:
            rtype = RECORD_FULL
        elif first_fragment:
            rtype = RECORD_FIRST
        elif pos + fragment_len >= total:
            rtype = RECORD_LAST
        else:
            rtype = RECORD_MIDDLE

        fragment = record[pos:pos + fragment_len]

        crc_data = bytes([rtype]) + fragment
        crc = mcleveldb.crc32c(crc_data)
        masked = mask_crc(crc)

        header = struct.pack("<IHB", masked, len(fragment), rtype)
        out += header
        out += fragment

        first_fragment = False
        pos += fragment_len
        if pos >= total:
            break

    return bytes(out)


def _find_log_files(db_dir):
    """返回按编号排序的 .log 文件列表。"""
    logs = []
    for path in glob.glob(os.path.join(db_dir, "*.log")):
        base = os.path.basename(path)
        m = re.match(r"(\d+)\.log", base)
        if m:
            logs.append((int(m.group(1)), path))
    logs.sort()
    return logs


def append_updates_to_log(db_dir, kv_updates: dict, seq_offset=1_000_000):
    """
    将修改追加到现有 .log 文件末尾（同文件编号，不更新 MANIFEST）。
    如果没有 .log 文件，创建新的。
    """
    logs = _find_log_files(db_dir)
    max_seq = _find_max_sequence(db_dir)
    new_seq = max_seq + seq_offset

    kv_pairs = list(kv_updates.items())
    record = _build_write_batch(kv_pairs, new_seq)
    log_bytes = _wrap_as_log_blocks(record)

    if not logs:
        max_num = 0
        for path in glob.glob(os.path.join(db_dir, "*")):
            base = os.path.basename(path)
            m = re.findall(r"(\d+)", base)
            if m:
                for s in m:
                    n = int(s)
                    if n > max_num:
                        max_num = n
        filename = f"{max_num + 1:06d}.log"
        path = os.path.join(db_dir, filename)
        with open(path, "wb") as f:
            f.write(log_bytes)
        return path

    # 追加到最后一个 .log 文件
    _num, log_path = logs[-1]
    file_size = os.path.getsize(log_path)

    # 填充到下一个 BLOCK_SIZE 边界
    padding = (BLOCK_SIZE - (file_size % BLOCK_SIZE)) % BLOCK_SIZE

    with open(log_path, "ab") as f:
        if padding > 0:
            f.write(b'\x00' * padding)
        f.write(log_bytes)

    return log_path
