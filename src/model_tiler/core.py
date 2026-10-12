"""OBJ import -> reusable scene -> spatial partition -> b3dm / tileset.

Coordinates in the normalized scene are East/North/Up metres. glTF is Y-up.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import struct
import tempfile
import uuid
from dataclasses import dataclass


class Cancelled(Exception):
    pass


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def finite(values):
    if not all(math.isfinite(v) for v in values):
        raise ValueError("坐标或参数包含 NaN / Infinity")
    return values


def safe_asset(root, name):
    path = (root / name.replace("\\", "/")).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("模型依赖文件必须位于输入目录内")
    if not path.is_file():
        raise ValueError(f"缺少依赖文件: {name}")
    return path


def materials(path, encoding="utf-8-sig"):
    result, current = {}, None
    for line in path.read_text(encoding=encoding).splitlines():
        fields = shlex.split(line, comments=True, posix=True)
        if not fields:
            continue
        key, args = fields[0], fields[1:]
        if key == "newmtl":
            current = {"color": [0.65, 0.73, 0.82, 1.0]}
            result[" ".join(args)] = current
        elif current is not None:
            if key == "Kd":
                if len(args) != 3:
                    raise ValueError("Kd 需要三个颜色分量")
                current["color"][:3] = [max(0, min(1, v)) for v in finite(list(map(float, args[:3])))]
            elif key in ("d", "Tr"):
                opacity = finite([float(args[0])])[0]
                current["color"][3] = max(0, min(1, 1 - opacity if key == "Tr" else opacity))
            elif key == "map_Kd":
                if args[0].startswith("-"):
                    raise ValueError("暂不支持带选项的 map_Kd；请导出简单纹理路径")
                image = safe_asset(path.parent, " ".join(args))
                mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(image.suffix.lower())
                if not mime:
                    raise ValueError("纹理仅支持 PNG/JPEG")
                current["image"] = {"mime": mime, "data": base64.b64encode(image.read_bytes()).decode()}
            elif key in ("map_Bump", "bump", "map_d", "map_Ks", "map_Ka", "disp"):
                raise ValueError(f"暂不支持 MTL {key}；请烘焙为基础颜色纹理")
    return result


def triangulate(corners, positions):
    """Ear clipping, including concave planar polygons; retain OBJ winding."""
    if len(corners) < 3:
        raise ValueError("面至少需要 3 个顶点")
    pts = [positions[c[0]] for c in corners]
    normal = [sum((pts[i][(a+1)%3] - pts[(i+1)%len(pts)][(a+1)%3]) *
                  (pts[i][(a+2)%3] + pts[(i+1)%len(pts)][(a+2)%3]) for i in range(len(pts))) for a in range(3)]
    axis = max(range(3), key=lambda i: abs(normal[i]))
    axes = [a for a in range(3) if a != axis]
    p = [(v[axes[0]], v[axes[1]]) for v in pts]
    cross = lambda a, b, c: (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])
    area = sum(p[i][0]*p[(i+1)%len(p)][1] - p[(i+1)%len(p)][0]*p[i][1] for i in range(len(p)))
    if abs(area) < 1e-15:
        raise ValueError("退化面无法三角化")
    direction = 1 if area > 0 else -1
    remaining, result = list(range(len(p))), []
    while len(remaining) > 3:
        for j, b in enumerate(remaining):
            a, c = remaining[j-1], remaining[(j+1)%len(remaining)]
            if direction * cross(p[a], p[b], p[c]) <= 1e-15:
                continue
            if any(all(direction*cross(p[x], p[y], p[t]) >= -1e-15 for x, y in [(a,b),(b,c),(c,a)])
                   for t in remaining if t not in (a,b,c)):
                continue
            result.append([corners[a], corners[b], corners[c]])
            remaining.pop(j)
            break
        else:
            raise ValueError("多边形无法三角化：请检查重复顶点、自交或共线点")
    result.append([corners[i] for i in remaining])
    return result


def import_scene(inputs, up_axis="Z", scale=1.0, properties=None, check=lambda: None,
                 encoding="utf-8-sig", reverse_winding=False):
    if encoding not in ("utf-8-sig", "gbk") or not isinstance(reverse_winding, bool):
        raise ValueError("编码应为 UTF-8 或 GBK，翻转绕向应为布尔值")
    finite([scale])
    if scale <= 0 or up_axis not in ("Y", "Z"):
        raise ValueError("单位比例必须大于 0，上轴必须为 Y 或 Z")
    scene = {"version": 1, "objects": [], "materials": {}, "sources": []}
    extras = {}
    if properties:
        with Path(properties).open(encoding="utf-8-sig", newline="") as f:
            rows = csv.reader(f)
            header = next(rows, [])
            if not header or len(header) != len(set(header)) or any(not s for s in header):
                raise ValueError("CSV 需要唯一且非空的表头，第一列为模型名称")
            for row in rows:
                if len(row) != len(header) or row[0] in extras:
                    raise ValueError("CSV 行长度错误或名称重复")
                extras[row[0]] = dict(zip(header[1:], row[1:]))
    for input_number, input_path in enumerate(inputs):
        check()
        path = Path(input_path).resolve()
        if path.suffix.lower() == ".fbx":
            from .fbx import import_fbx
            imported = import_fbx(path,input_number,up_axis,scale,extras,check,reverse_winding)
            scene['objects'].extend(imported['objects'])
            scene['materials'].update(imported['materials'])
            scene['sources'].append(imported['source'])
            continue
        if path.suffix.lower() != ".obj":
            raise ValueError("支持 OBJ + MTL 与静态 FBX；CLM/SHP 尚未实现")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        scene["sources"].append({"name": path.name, "sha256": digest, "index": input_number})
        positions, normals, uvs, groups = [], [], [], {}
        name, material = path.stem, "default"
        scene["materials"][f"{input_number}:default"] = {"color": [0.65, 0.73, 0.82, 1.0]}
        def coord(v):
            v = finite(list(map(float, v[:3])))
            if len(v) != 3:
                raise ValueError("v/vn 必须有三个分量")
            return v if up_axis == "Z" else [v[0], -v[2], v[1]]
        def index(token, sequence):
            value = int(token)
            i = value-1 if value > 0 else len(sequence)+value
            if value == 0 or not 0 <= i < len(sequence):
                raise ValueError("OBJ 索引越界")
            return i
        for line_number, line in enumerate(path.read_text(encoding=encoding).splitlines(), 1):
            if line_number % 1000 == 0:
                check()
            fields = line.partition("#")[0].strip().split()
            if not fields:
                continue
            key, args = fields[0], fields[1:]
            try:
                if key == "v":
                    positions.append([v*scale for v in coord(args)])
                elif key == "vn":
                    normals.append(coord(args))
                elif key == "vt":
                    uv = finite(list(map(float, args[:2])))
                    if len(uv) != 2:
                        raise ValueError("vt 必须有两个分量")
                    uvs.append(uv)
                elif key in ("o", "g"):
                    name = " ".join(args) or path.stem
                elif key == "usemtl":
                    material = " ".join(args)
                elif key == "mtllib":
                    for file in shlex.split(line[len(key):].strip()):
                        scene["materials"].update({f"{input_number}:{k}": v for k,v in materials(safe_asset(path.parent, file), encoding).items()})
                elif key == "f":
                    corners = []
                    for token in args:
                        parts = token.split("/")
                        corners.append((index(parts[0], positions), index(parts[1], uvs) if len(parts)>1 and parts[1] else None,
                                        index(parts[2], normals) if len(parts)>2 and parts[2] else None))
                    obj = groups.setdefault(name, {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{input_number}:{digest}:{name}")),
                                                 "name": name, "source": path.name, "sourceIndex": input_number, "properties": extras.get(name, {}), "triangles": []})
                    for face in triangulate(corners, positions):
                        if reverse_winding:
                            face = list(reversed(face))
                        vertices = [positions[c[0]] for c in face]
                        a,b,c = vertices
                        e1,e2 = [b[i]-a[i] for i in range(3)], [c[i]-a[i] for i in range(3)]
                        norm = [e1[1]*e2[2]-e1[2]*e2[1], e1[2]*e2[0]-e1[0]*e2[2], e1[0]*e2[1]-e1[1]*e2[0]]
                        length = math.sqrt(sum(v*v for v in norm))
                        if length <= 1e-15:
                            raise ValueError("退化三角形")
                        obj["triangles"].append({"p": vertices, "n": [[v * (-1 if reverse_winding else 1) for v in normals[t[2]]] if t[2] is not None else [v/length for v in norm] for t in face],
                                                 "uv": [uvs[t[1]] if t[1] is not None else [0,0] for t in face], "material": f"{input_number}:{material}"})
                elif key in ("l", "p", "curv", "surf"):
                    raise ValueError("仅支持多边形网格，不支持线、点或曲面")
            except (ValueError, IndexError) as error:
                raise ValueError(f"{path.name}:{line_number}: {error}") from error
        if not groups:
            raise ValueError(f"{path.name}: 输入不包含多边形网格")
        for obj in groups.values():
            for t in obj["triangles"]:
                if t["material"] not in scene["materials"]:
                    raise ValueError(f"未定义材质 {t['material']}")
            scene["objects"].append(obj)
    if not scene["objects"]:
        raise ValueError("输入不包含可切片的网格")
    return scene


def bounds(triangles):
    pts = [p for _,t in triangles for p in t["p"]]
    return [[min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)]]


def box(b):
    lo, hi = b
    c = [(lo[i]+hi[i])/2 for i in range(3)]
    h = [max((hi[i]-lo[i])/2, 1e-6) for i in range(3)]
    return c + [h[0],0,0,0,h[1],0,0,0,h[2]]


def enu_transform(longitude, latitude, height):
    finite([longitude, latitude, height])
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        raise ValueError("经纬度超出范围")
    lon, lat = math.radians(longitude), math.radians(latitude)
    sl,cl,sp,cp = math.sin(lon), math.cos(lon), math.sin(lat), math.cos(lat)
    a,e2 = 6378137.0, 6.6943799901413165e-3
    n = a/math.sqrt(1-e2*sp*sp)
    return [-sl,cl,0,0, -sp*cl,-sp*sl,cp,0, cp*cl,cp*sl,sp,0,
            (n+height)*cp*cl,(n+height)*cp*sl,(n*(1-e2)+height)*sp,1]


def glb(triangles, scene, double_sided=False, *, unlit=False, alpha_mode="auto", alpha_cutoff=0.5, property_fields=None,
        draco=False, draco_level=7, draco_bits=14, include_batch_ids=True):
    objects = sorted({i for i,t in triangles})
    ids = {i: j for j,i in enumerate(objects)}
    data = bytearray()
    doc = {"asset": {"version": "2.0", "generator": "Independent model-tiler"}, "scene": 0, "scenes": [{"nodes": [0]}],
           "nodes": [{"mesh": 0}], "meshes": [{"primitives": []}], "bufferViews": [], "accessors": [], "materials": []}
    def view(blob, target=None):
        data.extend(b"\x00"*((-len(data))%4))
        value = {"buffer": 0, "byteOffset": len(data), "byteLength": len(blob)}
        if target:
            value["target"] = target
        doc["bufferViews"].append(value)
        data.extend(blob)
        return len(doc["bufferViews"])-1
    def accessor(values, typ, component=5126):
        width = {"VEC3": 3, "VEC2": 2, "SCALAR": 1}[typ]
        flat = [x for row in values for x in (row if isinstance(row, list) else [row])]
        blob = struct.pack("<"+str(len(flat))+("f" if component==5126 else "I"), *flat)
        a = {"bufferView": view(blob, 34962), "componentType": component, "count": len(values), "type": typ}
        if typ == "VEC3":
            a.update(min=[min(v[i] for v in values) for i in range(width)], max=[max(v[i] for v in values) for i in range(width)])
        doc["accessors"].append(a)
        return len(doc["accessors"])-1
    grouped = {}
    for obj,t in triangles:
        grouped.setdefault(t["material"], []).append((obj,t))
    for material, faces in grouped.items():
        mat = scene["materials"][material]
        glmat = {"name": material, "doubleSided": double_sided,
                 "pbrMetallicRoughness": {"baseColorFactor": mat["color"], "metallicFactor": 0, "roughnessFactor": 0.85}}
        if mat["color"][3] < 1:
            glmat["alphaMode"] = "BLEND"
        if alpha_mode != "auto":
            glmat["alphaMode"] = alpha_mode
            if alpha_mode == "MASK":
                glmat["alphaCutoff"] = alpha_cutoff
        if unlit:
            glmat["extensions"] = {"KHR_materials_unlit": {}}
            doc["extensionsUsed"] = ["KHR_materials_unlit"]
        if "image" in mat:
            image = mat["image"]
            doc.setdefault("images", []).append({"bufferView": view(base64.b64decode(image["data"])), "mimeType": image["mime"]})
            doc.setdefault("samplers", [{"magFilter": 9729, "minFilter": 9729, "wrapS": 10497, "wrapT": 10497}])
            doc.setdefault("textures", []).append({"source": len(doc["images"])-1, "sampler": 0})
            glmat["pbrMetallicRoughness"]["baseColorTexture"] = {"index": len(doc["textures"])-1}
        doc["materials"].append(glmat)
        positions, normals, uv, batch = [], [], [], []
        for obj,t in faces:
            positions += [[p[0],p[2],-p[1]] for p in t["p"]]
            for n in t["n"]:
                length = math.sqrt(sum(v*v for v in n))
                if length == 0:
                    raise ValueError("法向量不能为零")
                normals.append([n[0]/length,n[2]/length,-n[1]/length])
            uv += [[u[0],1-u[1]] for u in t["uv"]]
            batch += [ids[obj]]*3
        attrs = {"POSITION": accessor(positions,"VEC3"), "NORMAL": accessor(normals,"VEC3"), "TEXCOORD_0": accessor(uv,"VEC2")}
        if include_batch_ids:
            attrs['_BATCHID'] = accessor(batch,'SCALAR',5126)
        doc["meshes"][0]["primitives"].append({"attributes": attrs, "material": len(doc["materials"])-1, "mode": 4})
    doc["buffers"] = [{"byteLength": len(data)}]
    text = json.dumps(doc, separators=(",",":"), allow_nan=False).encode()
    text += b" "*((-len(text))%4)
    data.extend(b"\0"*((-len(data))%4))
    blob = struct.pack("<4sII",b"glTF",2,28+len(text)+len(data)) + struct.pack("<I4s",len(text),b"JSON") + text + struct.pack("<I4s",len(data),b"BIN\0") + data
    if draco:
        from .draco import compress_glb
        blob = compress_glb(blob,draco_level,draco_bits)
    props = [dict({k:v for k,v in scene["objects"][i]["properties"].items() if property_fields is None or k in property_fields},
                  id=scene["objects"][i]["id"], name=scene["objects"][i]["name"]) for i in objects]
    keys = sorted({k for p in props for k in p})
    return blob, {key: [p.get(key) for p in props] for key in keys}, len(objects)


def b3dm(triangles, scene, double_sided=False, **settings):
    model, props, count = glb(triangles, scene, double_sided, **settings)
    ft = json.dumps({"BATCH_LENGTH": count}, separators=(",",":")).encode()
    ft += b" "*((-(28+len(ft)))%8)
    bt = json.dumps(props, ensure_ascii=False, separators=(",",":"), allow_nan=False).encode()
    bt += b" "*((-len(bt))%8)
    body = ft+bt+model
    body += b"\0"*((-(28+len(body)))%8)
    return struct.pack("<4s6I",b"b3dm",1,28+len(body),len(ft),0,len(bt),0)+body


@dataclass
class Options:
    longitude: float = 116.39123
    latitude: float = 39.90691
    height: float = 0
    mode: str = "small"
    max_triangles: int = 50000
    max_depth: int = 8
    visible_pixels: float = 25
    double_sided: bool = False
    unlit: bool = False
    alpha_mode: str = "auto"
    alpha_cutoff: float = 0.5
    origin: str = "original"
    property_fields: list[str] | None = None
    max_tile_bytes: int = 0
    draco: bool = False
    draco_level: int = 7
    draco_bits: int = 14
    instance_min_count: int = 0
    lod_ratio: float = 0.5
    lod_levels: int = 2

    def validate(self):
        enu_transform(self.longitude, self.latitude, self.height)
        finite([self.visible_pixels])
        finite([self.alpha_cutoff])
        finite([self.lod_ratio])
        if type(self.draco) is not bool or type(self.draco_level) is not int or not 0 <= self.draco_level <= 10:
            raise ValueError("Draco 开关或压缩级别无效（0–10）")
        if type(self.draco_bits) is not int or not 8 <= self.draco_bits <= 24:
            raise ValueError("Draco 位置量化应为 8–24 位")
        if type(self.instance_min_count) is not int or self.instance_min_count < 0 or self.instance_min_count == 1:
            raise ValueError("实例复用阈值应为 0（关闭）或至少 2")
        if not 0 < self.lod_ratio < 1 or type(self.lod_levels) is not int or not 1 <= self.lod_levels <= 4:
            raise ValueError("LOD 保留比例应在 (0,1)，化简层数应为 1–4")
        if self.alpha_mode not in ("auto", "OPAQUE", "MASK", "BLEND") or not 0 <= self.alpha_cutoff <= 1:
            raise ValueError("透明模式无效，透明阈值应为 0–1")
        if self.origin not in ("original", "center"):
            raise ValueError("原点应为 original 或 center")
        if not isinstance(self.double_sided,bool) or not isinstance(self.unlit,bool):
            raise ValueError("材质开关应为布尔值")
        if self.property_fields is not None and (not isinstance(self.property_fields,list) or any(not isinstance(k,str) or not k for k in self.property_fields)):
            raise ValueError("属性字段应为字符串列表")
        if type(self.max_tile_bytes) is not int or self.max_tile_bytes < 0:
            raise ValueError("切片字节上限应为非负整数，0 表示不限制")
        if self.mode not in ("small","octree","simplify") or not isinstance(self.max_triangles,int) or not 1 <= self.max_triangles <= 1000000:
            raise ValueError("处理模式或单片三角形阈值错误")
        if not isinstance(self.max_depth,int) or not 0 <= self.max_depth <= 16 or not 0 < self.visible_pixels <= 512:
            raise ValueError("深度应为 0–16，可见像素应为 (0,512]")


def tile_scene(scene, output, options=None, progress=lambda p,m: None, cancelled=lambda: False):
    options = options or Options()
    options.validate()
    # Recenter output geometry without mutating the reusable resource or its world location.
    original_faces = [(i,t) for i,obj in enumerate(scene["objects"]) for t in obj["triangles"]]
    offset = [0,0,0]
    if original_faces and options.origin == "center":
        b = bounds(original_faces)
        offset = [(b[0][a]+b[1][a])/2 for a in range(3)]
        scene = dict(scene, objects=[dict(obj, triangles=[dict(t, p=[[p[a]-offset[a] for a in range(3)] for p in t["p"]])
                                                        for t in obj["triangles"]]) for obj in scene["objects"]])
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"输出目录已存在: {output}；请选择新目录")
    def check():
        if cancelled():
            raise Cancelled("任务已取消")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".tiling-", dir=output.parent))
    all_faces = [(i,t) for i,obj in enumerate(scene["objects"]) for t in obj["triangles"]]
    if not all_faces:
        shutil.rmtree(stage)
        raise ValueError("资源库中没有三角形")
    files, object_uris = [], {i: [] for i in range(len(scene["objects"]))}
    total, completed = len(all_faces), 0
    lod_stats = []
    settings = dict(unlit=options.unlit,alpha_mode=options.alpha_mode,alpha_cutoff=options.alpha_cutoff,
                    property_fields=options.property_fields,draco=options.draco,draco_level=options.draco_level,draco_bits=options.draco_bits)
    def make_glb(faces,model_scene,**extra):
        return glb(faces,model_scene,options.double_sided,**settings,**extra)
    def encode(faces):
        from .instances import group_instances,i3dm,cmpt
        groups,remaining = group_instances(faces,scene,options.instance_min_count,check)
        payloads = [i3dm(group,scene,options,make_glb) for group in groups]
        if remaining:
            payloads.append(b3dm(remaining,scene,options.double_sided,**settings))
        kind = 'cmpt' if len(payloads)>1 else 'i3dm' if groups else 'b3dm'
        data = cmpt(payloads) if len(payloads)>1 else payloads[0]
        return dict(data=data,format=kind,instanceGroups=len(groups),instances=sum(len(g) for g in groups),
                    storedTriangles=len(remaining)+sum(len(g[0][1]['triangles']) for g in groups))
    def content(faces,payload=None,level=0):
        nonlocal completed
        check()
        payload = payload or encode(faces)
        uri = f"tiles/{len(files):05d}.{payload['format']}"
        dest = stage / uri
        dest.parent.mkdir(exist_ok=True)
        data = payload['data']
        dest.write_bytes(data)
        files.append({"uri": uri, "bytes": len(data), "triangles": len(faces), "sha256": hashlib.sha256(data).hexdigest(),
                      'lodLevel':level,**{k:v for k,v in payload.items() if k!='data'}})
        for i in {i for i,t in faces}:
            object_uris[i].append(uri)
        completed += len(faces)
        progress(min(90,20 + int(completed/total*70)), f"已写入 {len(files)} 个切片")
        return {"uri": uri}
    def bounding_box(b):
        if not options.draco:
            return box(b)
        # Cover position quantization and float32 instance/vertex rounding.
        margin=math.dist(*b)/(2**options.draco_bits-1)+max(abs(v) for row in b for v in row)*2**-22+1e-6
        return box([[v-margin for v in b[0]],[v+margin for v in b[1]]])
    def compression_error(b):
        return (math.dist(*b)/(2**options.draco_bits-1)+max(abs(v) for row in b for v in row)*2**-22+1e-6) if options.draco else 0
    def small(faces, depth=0):
        check()
        b = bounds(faces)
        node = {"boundingVolume": {"box": bounding_box(b)}, "geometricError": compression_error(b), "refine": "ADD"}
        payload = encode(faces) if options.max_tile_bytes and len(faces)<=options.max_triangles else None
        within_budget = len(faces) <= options.max_triangles and (not options.max_tile_bytes or len(payload['data']) <= options.max_tile_bytes)
        if within_budget or depth >= options.max_depth or len(faces) == 1:
            node["content"] = content(faces,payload)
            if options.mode=='simplify':
                from .simplify import simplify_faces
                previous_count=len(faces)
                for level in range(1,options.lod_levels+1):
                    simplified,error,stats=simplify_faces(faces,scene,options.lod_ratio**level,check)
                    stats['level']=level
                    lod_stats.append(stats)
                    if len(simplified)>=previous_count:
                        continue
                    coarse=encode(simplified)
                    if options.max_tile_bytes and len(coarse['data'])>options.max_tile_bytes:
                        stats['skippedByteBudget']=True
                        continue
                    node={'boundingVolume':{'box':bounding_box(b)},'geometricError':max(error*level,node['geometricError'],1e-3),
                          'refine':'REPLACE','content':content(simplified,coarse,level),'children':[node]}
                    previous_count=len(simplified)
        else:
            # Empty hierarchy nodes need a nonzero traversal error as well.
            # Their error represents omitting their full-resolution descendants.
            node["geometricError"] = max(math.dist(*b), 1e-3)
            axis = max(range(3), key=lambda a: b[1][a]-b[0][a])
            ordered = sorted(faces, key=lambda it: sum(v[axis] for v in it[1]["p"])/3)
            middle = len(ordered)//2
            node["children"] = [small(ordered[:middle],depth+1), small(ordered[middle:],depth+1)]
            node['geometricError']=max(node['geometricError'],max(c['geometricError'] for c in node['children']))
        return node
    obj_bounds = {i: bounds([(i,t) for t in obj["triangles"]]) for i,obj in enumerate(scene["objects"])}
    def octree(object_ids, depth=0):
        check()
        faces = [(i,t) for i in object_ids for t in scene["objects"][i]["triangles"]]
        b = bounds(faces)
        side = max(b[1][a]-b[0][a] for a in range(3))
        center = [(b[0][a]+b[1][a])/2 for a in range(3)]
        ready, buckets = [], {}
        for i in object_ids:
            ob = obj_bounds[i]
            size = max(ob[1][a]-ob[0][a] for a in range(3))
            if depth >= options.max_depth or side < 1e-9 or size/side*512 >= options.visible_pixels:
                ready += [(i,t) for t in scene["objects"][i]["triangles"]]
            else:
                octant = sum((1<<a) for a in range(3) if (ob[0][a]+ob[1][a])/2 >= center[a])
                buckets.setdefault(octant, []).append(i)
        node = {"boundingVolume": {"box": bounding_box(b)}, "geometricError": max(side/512 if buckets else 0,compression_error(b)), "refine": "ADD"}
        if ready:
            payload=encode(ready) if options.max_tile_bytes and len(ready)<=options.max_triangles else None
            if len(ready) <= options.max_triangles and (not options.max_tile_bytes or len(payload['data']) <= options.max_tile_bytes):
                node["content"] = content(ready,payload)
            else:
                node.setdefault("children", []).append(small(ready))
        node.setdefault("children", []).extend(octree(ids,depth+1) for _,ids in sorted(buckets.items()))
        if not node["children"]:
            del node["children"]
        if "children" in node:
            node["geometricError"] = max(node["geometricError"], max(c["geometricError"] for c in node["children"]))
        return node
    try:
        progress(20,"开始空间切片")
        root = octree(list(obj_bounds)) if options.mode == "octree" else small(all_faces)
        transform = enu_transform(options.longitude, options.latitude, options.height)
        for a in range(3):
            transform[12+a] += sum(transform[4*j+a]*offset[j] for j in range(3))
        root["transform"] = transform
        whole_bounds = bounds(all_faces)
        # Tileset-level error is the error of displaying NOTHING. A zero value
        # makes Cesium cull the entire set before visiting even a perfect leaf.
        tileset_error = max(1e-3, math.dist(*whole_bounds),root['geometricError'])
        if options.mode=='simplify':
            # The implicit tileset parent must remain traversable while its
            # coarsest root representation is being selected. Use a conservative
            # omission error larger than the root's approximation error.
            tileset_error *= 8
        tileset = {"asset": {"version": "1.0", "generator": "Independent model-tiler"}, "geometricError": tileset_error, "root": root}
        dump(stage/"tileset.json", tileset)
        def sphere(b):
            value = box(b)
            center = [transform[12+a]+sum(transform[4*j+a]*value[j] for j in range(3)) for a in range(3)]
            return center+[math.hypot(value[3],value[7],value[11])]
        children = [{"type": "element", "id": obj["id"], "name": obj["name"], "source": obj["source"], "sourceIndex": obj.get("sourceIndex",0),
                     "boundingVolume": {"box": box(obj_bounds[i])}, "sphere": sphere(obj_bounds[i]),
                     "properties": {k:v for k,v in obj["properties"].items() if options.property_fields is None or k in options.property_fields},
                     "tiles": object_uris[i]} for i,obj in enumerate(scene["objects"])]
        scenes = [{"type":"node", "name":s["name"], "children":[c for c in children if c["sourceIndex"]==s.get("index",0)]} for s in scene["sources"]]
        for s in scenes:
            faces=[(i,t) for i,obj in enumerate(scene["objects"]) if obj.get("sourceIndex",0)==s["children"][0]["sourceIndex"] for t in obj["triangles"]]
            s["sphere"]=sphere(bounds(faces))
        dump(stage/"scenetree.json", {"schema": "model-tiler/1", "scenes": scenes})
        report = {"status": "completed", "objects": len(children), "triangles": total, "tiles": files, "options": vars(options),
                  "warnings": ["尺寸过滤模式使用构件大小控制加载层级，不做几何简化"] if options.mode == "octree" else [],
                  "oversizedTiles": [f["uri"] for f in files if f["triangles"] > options.max_triangles]}
        report["originOffset"] = offset
        report['lod']=lod_stats
        report['leafTriangles']=sum(f['triangles'] for f in files if f['lodLevel']==0)
        report['storedTriangles']=sum(f['storedTriangles'] for f in files)
        report['instancedObjects']=sum(f['instances'] for f in files if f['lodLevel']==0)
        if options.mode=='simplify':
            report['warnings'].append('化简模式的最高精度层保留全部几何；带纹理、接缝、多材质和无法安全化简的构件保留原网格，实际化简比例记录在 lod 中')
        report["oversizedByteTiles"] = [f["uri"] for f in files if options.max_tile_bytes and f["bytes"] > options.max_tile_bytes]
        if report["oversizedByteTiles"]:
            report["warnings"].append("部分切片达到深度上限或仅剩一个三角形，仍超过字节上限（纹理和属性也占空间）")
        dump(stage/"report.json", report)
        # Preview samples are derived from actual normalized geometry, never synthetic results.
        stride = max(1, math.ceil(total/50000))
        dump(stage/"preview.json", {"bounds": bounds(all_faces), "triangles": [t["p"] for _,t in all_faces[::stride]],
                                   "unlit": options.unlit,
                                   "objectIds": [scene["objects"][i]["id"] for i,t in all_faces[::stride]],
                                   "colors": [scene["materials"][t["material"]]["color"] for _,t in all_faces[::stride]], "sampled": stride > 1})
        check()
        if output.exists():
            raise FileExistsError("输出目录在执行期间被创建")
        os.rename(stage, output)
        progress(100,"切片完成")
        return report
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
