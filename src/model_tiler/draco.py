"""Required KHR_draco_mesh_compression for the tiler's self-contained GLBs.

Geometry is replaced, not copied alongside a compressed fallback. DracoPy's
decoder supplies actual attribute IDs and post-deduplication accessor counts.
"""
import json
import struct


EXTENSION = "KHR_draco_mesh_compression"


def _read_glb(blob):
    if len(blob) < 28 or struct.unpack_from("<4sII", blob) != (b"glTF", 2, len(blob)):
        raise ValueError("Draco 输入必须是完整的 glTF 2.0 GLB")
    doc, binary, offset = None, b"", 12
    while offset < len(blob):
        size, kind = struct.unpack_from("<I4s", blob, offset)
        chunk = blob[offset + 8:offset + 8 + size]
        if len(chunk) != size:
            raise ValueError("GLB 数据块长度无效")
        if kind == b"JSON":
            doc = json.loads(chunk)
        elif kind == b"BIN\0":
            binary = chunk
        offset += size + 8
    if doc is None or any("uri" in b for b in doc.get("buffers", [])):
        raise ValueError("Draco 仅支持内嵌二进制 GLB")
    return doc, binary


def compress_glb(blob, compression_level=7, quantization_bits=14):
    """Compress triangle primitives; preserve materials, images and exact IDs.

    Position quantization uses ``quantization_bits``; UVs, normals and generic
    attributes remain lossless, including feature IDs (never quantized).
    """
    try:
        import numpy as np
        import DracoPy
    except ImportError as exc:
        raise ValueError("Draco 压缩需要 numpy 和 DracoPy，请安装项目依赖") from exc
    if not isinstance(compression_level, int) or not 0 <= compression_level <= 10:
        raise ValueError("Draco compression_level 必须在 0–10 之间")
    if not isinstance(quantization_bits, int) or not 1 <= quantization_bits <= 30:
        raise ValueError("Draco quantization_bits 必须在 1–30 之间")
    doc, binary = _read_glb(blob)
    if EXTENSION in doc.get("extensionsUsed", []):
        raise ValueError("GLB 已包含 Draco 压缩")
    accessors, views = doc.get("accessors", []), doc.get("bufferViews", [])
    payloads = [binary[v.get("byteOffset", 0):v.get("byteOffset", 0) + v["byteLength"]] for v in views]
    dtypes = {5120: "i1", 5121: "u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
    widths = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}

    def values(index):
        a = accessors[index]
        if "sparse" in a or a["type"] not in widths or "bufferView" not in a:
            raise ValueError("Draco 暂不支持稀疏或矩阵访问器")
        v = views[a["bufferView"]]
        if v.get("buffer", 0) != 0:
            raise ValueError("Draco 仅支持 GLB 的内嵌 buffer 0")
        dtype, width = np.dtype(dtypes[a["componentType"]]), widths[a["type"]]
        stride = v.get("byteStride", dtype.itemsize * width)
        return np.ndarray((a["count"], width), dtype=dtype,
                          buffer=payloads[a["bufferView"]], offset=a.get("byteOffset", 0),
                          strides=(stride, dtype.itemsize)).copy()

    seen, compressed = set(), False
    for mesh in doc.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            if primitive.get("mode", 4) != 4 or primitive.get("targets"):
                raise ValueError("Draco 当前仅支持无 morph targets 的 TRIANGLES")
            attrs = primitive["attributes"]
            arrays = {name: values(index) for name, index in attrs.items()}
            if "POSITION" not in arrays or arrays["POSITION"].shape[1] != 3:
                raise ValueError("Draco 网格缺少 VEC3 POSITION")
            count = len(arrays["POSITION"])
            if not count or any(len(a) != count or not np.isfinite(a).all() for a in arrays.values()):
                raise ValueError("Draco 属性数量不一致、为空或含非有限值")
            indices = values(primitive["indices"]).reshape(-1) if "indices" in primitive else np.arange(count, dtype=np.uint32)
            if len(indices) % 3 or indices.min() < 0 or indices.max() >= count:
                raise ValueError("Draco 三角形索引无效")
            generic = {name: a for name, a in arrays.items() if name not in ("POSITION", "NORMAL", "TEXCOORD_0")}
            for name in generic:
                if name in ("_BATCHID", "_FEATURE_ID_0"):
                    a = generic[name]
                    if (a < 0).any() or (a > 4294967295).any() or (a != np.floor(a)).any():
                        raise ValueError("Draco feature ID 必须是非负 32 位整数")
                    # Generic FLOAT attributes are stored losslessly by Draco.
                    # Keep FLOAT batch accessors: glTF reserves UINT32 for indices.
                    if a.dtype == np.uint32:
                        if (a > 16777216).any():
                            raise ValueError("32 位 feature ID 超过 glTF FLOAT 的精确表示范围")
                        generic[name] = a.astype(np.float32)
            encoded = DracoPy.encode(arrays["POSITION"].astype(float), indices.reshape(-1, 3).astype(np.uint32),
                                     normals=arrays.get("NORMAL", None).astype(float) if "NORMAL" in arrays else None,
                                     tex_coord=arrays.get("TEXCOORD_0", None).astype(float) if "TEXCOORD_0" in arrays else None,
                                     generic_attributes=generic or None, compression_level=compression_level,
                                     quantization_bits=quantization_bits)
            decoded = DracoPy.decode(encoded)
            ids = {}
            for name, index in attrs.items():
                # Shared accessors may acquire different counts when deduplicated.
                # Clone each compressed accessor to keep other references valid.
                a = dict(accessors[index])
                attr = decoded.get_attribute_by_type({"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 3}[name]) if name in ("POSITION", "NORMAL", "TEXCOORD_0") else decoded.get_attribute_by_name(name)
                if attr is None:
                    raise ValueError("Draco 编码器未保留属性 " + name)
                ids[name] = attr["unique_id"]
                for field in ("bufferView", "byteOffset", "sparse"):
                    a.pop(field, None)
                a["count"] = len(attr["data"])
                if name in ("_BATCHID", "_FEATURE_ID_0") and a["componentType"] == 5125:
                    a["componentType"] = 5126
                    a.pop("normalized", None)
                if name == "POSITION" or "min" in a or "max" in a:
                    a["min"] = attr["data"].min(axis=0).tolist()
                    a["max"] = attr["data"].max(axis=0).tolist()
                attrs[name] = len(accessors)
                accessors.append(a)
                seen.add(index)
            if "indices" in primitive:
                seen.add(primitive["indices"])
            primitive["indices"] = len(accessors)
            accessors.append({"componentType": 5125, "count": int(decoded.faces.size), "type": "SCALAR"})
            primitive.setdefault("extensions", {})[EXTENSION] = {"bufferView": len(views), "attributes": ids}
            views.append({"buffer": 0, "byteLength": len(encoded)})
            payloads.append(encoded)
            compressed = True
    if not compressed:
        raise ValueError("GLB 没有可压缩的三角形网格")
    # Original geometry accessors no longer referenced by primitives. Keep other
    # accessor references (animations/skins) and image bufferViews intact.
    referenced = set()
    for mesh in doc.get("meshes", []):
        for p in mesh["primitives"]:
            referenced.update(p["attributes"].values())
            referenced.add(p["indices"])
    for skin in doc.get("skins", []):
        if "inverseBindMatrices" in skin:
            referenced.add(skin["inverseBindMatrices"])
    for animation in doc.get("animations", []):
        for sampler in animation.get("samplers", []):
            referenced.update((sampler["input"], sampler["output"]))
    removed = seen - referenced
    accessor_map = {old: new for new, old in enumerate(i for i in range(len(accessors)) if i not in removed)}
    for mesh in doc.get("meshes", []):
        for p in mesh["primitives"]:
            p["attributes"] = {name: accessor_map[index] for name, index in p["attributes"].items()}
            p["indices"] = accessor_map[p["indices"]]
    for skin in doc.get("skins", []):
        if "inverseBindMatrices" in skin:
            skin["inverseBindMatrices"] = accessor_map[skin["inverseBindMatrices"]]
    for animation in doc.get("animations", []):
        for sampler in animation.get("samplers", []):
            for key in ("input", "output"):
                sampler[key] = accessor_map[sampler[key]]
    doc["accessors"] = [a for i, a in enumerate(accessors) if i not in removed]
    used = set()

    def scan(value, remap=None):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "bufferView" and isinstance(child, int):
                    if remap is None:
                        used.add(child)
                    else:
                        value[key] = remap[child]
                else:
                    scan(child, remap)
        elif isinstance(value, list):
            for child in value:
                scan(child, remap)

    scan(doc)
    data, retained, remap = bytearray(), [], {}
    for index in sorted(used):
        data.extend(b"\0" * (-len(data) % 4))
        remap[index] = len(retained)
        v = dict(views[index], buffer=0, byteOffset=len(data))
        retained.append(v)
        data.extend(payloads[index])
    scan(doc, remap)
    doc["bufferViews"] = retained
    doc["buffers"] = [{"byteLength": len(data)}]
    for key in ("extensionsUsed", "extensionsRequired"):
        doc.setdefault(key, []).append(EXTENSION)
    text = json.dumps(doc, separators=(",", ":"), allow_nan=False).encode()
    text += b" " * (-len(text) % 4)
    data.extend(b"\0" * (-len(data) % 4))
    return (struct.pack("<4sII", b"glTF", 2, 28 + len(text) + len(data)) +
            struct.pack("<I4s", len(text), b"JSON") + text +
            struct.pack("<I4s", len(data), b"BIN\0") + data)
