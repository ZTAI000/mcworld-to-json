# -*- coding: utf-8 -*-
"""
从基岩版.mcworld存档中提取命令方块文本的核心逻辑
"""

import os
import struct
import shutil
import tempfile
import zipfile
import json
import base64

import mcleveldb
import bnbt

TAG_BLOCK_ENTITY = 0x31

COMMAND_BLOCK_IDS = {
    "CommandBlock",
    "CommandBlockMinecart",
    "minecraft:command_block",
    "minecraft:chain_command_block",
    "minecraft:repeating_command_block",
    "minecraft:command_block_minecart",
}

DIM_NAMES = {0: "overworld", 1: "nether", 2: "the_end"}
DIM_NAME_TO_ID = {v: k for k, v in DIM_NAMES.items()}

# NetEase 维度编码映射
NETEASE_DIM_MAP = {
    0x2f: 0,   # NetEase overworld
}


def unpack_mcworld(mcworld_path, dest_dir):
    """解压 .mcworld (zip) 到 dest_dir，返回 db 目录路径。"""
    with zipfile.ZipFile(mcworld_path, "r") as zf:
        zf.extractall(dest_dir)
    db_dir = os.path.join(dest_dir, "db")
    if not os.path.isdir(db_dir):
        for root, dirs, _files in os.walk(dest_dir):
            if "db" in dirs:
                db_dir = os.path.join(root, "db")
                break
    if not os.path.isdir(db_dir):
        raise FileNotFoundError("存档中未找到 db 目录")
    return db_dir


def pack_mcworld(src_dir, output_path):
    """将 src_dir 打包为 .mcworld (zip)。"""
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(src_dir):
            for fname in files:
                fpath = os.path.join(root, fname)
                arcname = os.path.relpath(fpath, src_dir)
                zf.write(fpath, arcname)


def parse_chunk_key(key: bytes):
    """解析 LevelDB key，返回 (chunk_x, chunk_z, dimension, tag) 或 None。"""
    n = len(key)
    if n < 8:
        return None
    cx, cz = struct.unpack_from("<ii", key, 0)

    if n == 9:
        # 旧版主世界: cx(4) + cz(4) + tag(1)
        return cx, cz, 0, key[8]

    if n == 10:
        # 新版: cx(4) + cz(4) + dim(1) + tag(1)
        raw_dim = key[8]
        dim = NETEASE_DIM_MAP.get(raw_dim, raw_dim)
        return cx, cz, dim, key[9]

    if n == 13:
        # 旧版下界/末地: cx(4) + cz(4) + dim(4) + tag(1)
        dim = struct.unpack_from("<i", key, 8)[0]
        return cx, cz, dim, key[12]

    return None


def make_chunk_key(cx, cz, dim, tag, key_fmt="auto"):
    """根据格式构造 LevelDB key。"""
    if key_fmt == "auto":
        if dim == 0:
            return struct.pack("<ii", cx, cz) + bytes([tag])
        else:
            return struct.pack("<iii", cx, cz, dim) + bytes([tag])
    elif key_fmt == "9byte":
        return struct.pack("<ii", cx, cz) + bytes([tag])
    elif key_fmt == "10byte":
        raw_dim = 0 if dim == 0 else dim
        return struct.pack("<ii", cx, cz) + bytes([raw_dim, tag])
    elif key_fmt == "13byte":
        return struct.pack("<iii", cx, cz, dim) + bytes([tag])
    return struct.pack("<ii", cx, cz) + bytes([tag])


def is_command_block_compound(nb) -> bool:
    if nb.tag != bnbt.TAG_Compound:
        return False
    id_val = bnbt.compound_get_value(nb, "id", "")
    if id_val in COMMAND_BLOCK_IDS:
        return True
    if id_val and ":" in id_val:
        short_name = id_val.split(":")[-1]
        for cb_id in COMMAND_BLOCK_IDS:
            if short_name == cb_id.split(":")[-1]:
                return True
    return False


def extract_from_db(db_dir):
    """
    从数据库中提取所有命令方块。
    返回 (commands, names, meta) 三元组。
    """
    kv = mcleveldb.load_full_database(db_dir)

    commands = {}
    names = {}
    meta = []
    cmd_index = 0

    for key in sorted(kv.keys()):
        info = parse_chunk_key(key)
        if info is None:
            continue
        cx, cz, dim, tag = info
        if tag != TAG_BLOCK_ENTITY:
            continue

        value = kv[key]
        if value is None:
            continue

        try:
            entries, _ = bnbt.parse_all(value)
        except Exception:
            continue

        for i, (name, nb) in enumerate(entries):
            if not is_command_block_compound(nb):
                continue

            cmd_index += 1
            cb_key = f"cb_{cmd_index}"
            cmd_text = bnbt.compound_get_value(nb, "Command", "") or ""
            custom_name = bnbt.compound_get_value(nb, "CustomName", "") or ""
            x = bnbt.compound_get_value(nb, "x", 0)
            y = bnbt.compound_get_value(nb, "y", 0)
            z = bnbt.compound_get_value(nb, "z", 0)

            commands[cb_key] = cmd_text
            names[cb_key] = custom_name

            # 确定 key 格式
            klen = len(key)
            if klen == 9:
                key_fmt = "9byte"
            elif klen == 10:
                key_fmt = "10byte"
            elif klen == 13:
                key_fmt = "13byte"
            else:
                key_fmt = "9byte"

            meta.append({
                "cb_key": cb_key,
                "pos": f"{x},{y},{z}",
                "chunk_key_hex": key.hex(),
                "entry_index": i,
                "chunk_x": cx,
                "chunk_z": cz,
                "dimension": dim,
                "key_fmt": key_fmt,
            })

    return commands, names, meta


def build_output_json(mcworld_path, db_dir=None, tmp_dir=None):
    """
    构建完整的输出 JSON（含 _world_data 自包含数据）。
    如果 db_dir 为 None，则自动解包 mcworld_path。
    """
    own_tmp = False
    if db_dir is None:
        own_tmp = True
        tmp_dir = tempfile.mkdtemp(prefix="mcworld_extract_")
        db_dir = unpack_mcworld(mcworld_path, tmp_dir)

    try:
        commands, names, meta = extract_from_db(db_dir)

        world_name = None
        base = os.path.dirname(db_dir)
        cand = os.path.join(base, "levelname.txt")
        if os.path.isfile(cand):
            with open(cand, "r", encoding="utf-8", errors="ignore") as f:
                world_name = f.read().strip()

        # 读取原始 .mcworld 的 base64 编码
        world_data_b64 = ""
        if mcworld_path and os.path.isfile(mcworld_path):
            with open(mcworld_path, "rb") as f:
                raw = f.read()
            world_data_b64 = base64.b64encode(raw).decode("ascii")

        output = {
            "world_data": {
                "source_file": os.path.basename(mcworld_path) if mcworld_path else "",
                "world_name": world_name,
                "command_block_count": len(commands),
            },
            "commands": commands,
            "names": names,
            "_meta": meta,
            "_world_data": world_data_b64,
        }
        return output
    finally:
        if own_tmp:
            shutil.rmtree(tmp_dir, ignore_errors=True)
