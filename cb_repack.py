# -*- coding: utf-8 -*-
"""
将修改后的 JSON 写回 .mcworld 存档
"""

import os
import shutil
import tempfile
import base64
import zipfile

import bnbt
import cb_extract
import mcleveldb
import mcleveldb_write


def apply_json_to_db(db_dir, data: dict):
    """
    将 JSON 中的修改应用到数据库。
    返回 (修改的命令条数, 涉及的区块数)。
    """
    commands = data.get("commands", {})
    names = data.get("names", {})
    meta = data.get("_meta", [])
    if not meta:
        raise ValueError("JSON 中缺少 _meta 字段")

    kv = mcleveldb.load_full_database(db_dir)

    # 按 chunk_key 分组
    by_chunk = {}
    for m in meta:
        cb_key = m["cb_key"]
        if cb_key not in commands:
            continue
        new_cmd = commands[cb_key]
        new_name = names.get(cb_key, None)
        chunk_key_hex = m["chunk_key_hex"]
        by_chunk.setdefault(chunk_key_hex, []).append(
            (m["entry_index"], new_cmd, new_name)
        )

    updates = {}
    total_cmd_changes = 0
    total_name_changes = 0

    for chunk_key_hex, changes in by_chunk.items():
        chunk_key = bytes.fromhex(chunk_key_hex)
        old_value = kv.get(chunk_key)
        if old_value is None:
            continue

        try:
            entries, _ = bnbt.parse_all(old_value)
        except Exception:
            continue

        changes.sort(key=lambda t: t[0])
        chunk_changed = False
        for idx, new_cmd, new_name in changes:
            if idx < 0 or idx >= len(entries):
                continue
            _name, nb = entries[idx]
            entry_changed = False

            old_cmd = bnbt.compound_get_value(nb, "Command", "")
            if old_cmd != new_cmd:
                bnbt.compound_set(nb, "Command", bnbt.make_string(new_cmd))
                entry_changed = True
                total_cmd_changes += 1

            if new_name is not None:
                old_name = bnbt.compound_get_value(nb, "CustomName", "")
                if old_name != new_name:
                    bnbt.compound_set(nb, "CustomName", bnbt.make_string(new_name))
                    entry_changed = True
                    total_name_changes += 1

            if entry_changed:
                chunk_changed = True

        if chunk_changed:
            new_value = bnbt.write_all([(name, nb) for name, nb in entries])
            updates[chunk_key] = new_value

    if updates:
        mcleveldb_write.append_updates_to_log(db_dir, updates)

    return total_cmd_changes, total_name_changes, len(updates)


def repack_mcworld_from_json(json_data: dict, output_mcworld_path):
    """
    从 JSON 的 _world_data 解码原始 mcworld，应用修改，输出新 mcworld。
    不需要外部 mcworld 文件。
    返回 (命令修改条数, 悬浮文字修改条数, 涉及区块数)。
    """
    world_data_b64 = json_data.get("_world_data", "")
    if not world_data_b64:
        raise ValueError("JSON 中缺少 _world_data 字段，无法重建存档")

    raw_mcworld = base64.b64decode(world_data_b64)

    tmp_dir = tempfile.mkdtemp(prefix="mcworld_repack_")
    try:
        # 解压原始 mcworld 到临时目录
        import io
        with zipfile.ZipFile(io.BytesIO(raw_mcworld), "r") as zf:
            zf.extractall(tmp_dir)

        db_dir = os.path.join(tmp_dir, "db")
        if not os.path.isdir(db_dir):
            for root, dirs, _ in os.walk(tmp_dir):
                if "db" in dirs:
                    db_dir = os.path.join(root, "db")
                    break

        cmd_changes, name_changes, chunk_changes = apply_json_to_db(db_dir, json_data)

        extract_root = os.path.dirname(db_dir)
        cb_extract.pack_mcworld(extract_root, output_mcworld_path)

        return cmd_changes, name_changes, chunk_changes
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
