"""Static ASCII/binary FBX importer backed by the upstream ufbx bindings.

Positions retain FBX source units: `scale` is metres per source unit, exactly
as for OBJ. `up_axis` explicitly selects the source axis, overriding metadata.
Hierarchical and geometric transforms are baked before this axis conversion.
Returns objects/materials/source for merging into the normalized scene.
"""
from __future__ import annotations

import base64
import hashlib
import math
from pathlib import Path
import uuid


def _image(texture, root):
    if texture.layers or not texture.has_file:
        raise ValueError("FBX 基础颜色纹理必须为单张 PNG/JPEG 文件；请烘焙程序或分层纹理")
    data = texture.content or (texture.video.content if texture.video else b"")
    if not data:
        name = texture.relative_filename or texture.filename
        image = (root / name.replace("\\", "/")).resolve()
        if not image.is_relative_to(root):
            raise ValueError("FBX 纹理必须位于输入目录内")
        if not image.is_file():
            raise ValueError(f"缺少 FBX 纹理: {name}")
        data = image.read_bytes()
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    else:
        raise ValueError("FBX 纹理仅支持 PNG/JPEG")
    return {"mime": mime, "data": base64.b64encode(data).decode("ascii")}


def import_fbx(path, source_index=0, up_axis="Z", scale=1.0, extras=None,
               check=lambda: None, reverse_winding=False):
    """Import static polygon nodes, preserving per-corner normals/UV/material.

    `extras` maps node names to CSV properties. IDs incorporate native node IDs
    so repeated names do not merge. Animations and deformers are rejected.
    """
    try:
        import numpy as np
        import ufbx
    except ImportError as error:
        raise ValueError("FBX 导入需要 ufbx 和 numpy；请安装项目依赖") from error
    from .core import triangulate

    if up_axis not in ("Y", "Z") or not math.isfinite(scale) or scale <= 0:
        raise ValueError("单位比例必须大于 0，上轴必须为 Y 或 Z")
    path = Path(path).resolve()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    check()
    try:
        scene = ufbx.load_file(str(path), load_external_files=False,
                               generate_missing_normals=True, strict=True)
    except Exception as error:
        raise ValueError(f"FBX 解析失败: {path.name}: {error}") from error
    result = {"objects": [], "materials": {}, "source": {
        "name": path.name, "sha256": digest, "index": source_index,
        "format": "fbx", "coordinatePolicy": "explicit-up-axis-and-scale"}}
    textures = {}
    default = f"{source_index}:fbx:default"
    result["materials"][default] = {"color": [.65, .73, .82, 1.0]}
    axis = np.eye(3) if up_axis == "Z" else np.array([[1,0,0],[0,0,-1],[0,1,0]])
    try:
        if scene.anim_curves or scene.skin_deformers or scene.blend_deformers or scene.cache_deformers:
            raise ValueError("FBX 当前仅支持静态网格；请先烘焙动画、蒙皮、形变或缓存")
        if scene.nurbs_curves or scene.nurbs_surfaces or scene.line_curves or scene.procedural_geometries:
            raise ValueError("FBX 曲线/曲面必须先转换为多边形网格")
        material_views = scene.materials
        # ufbx 0.0.5 nested map wrappers borrow their parent's storage. Keep
        # parents alive explicitly; chained mat.pbr.base_color can dangle.
        retained_views = []
        for mat in material_views:
            check()
            key = f"{source_index}:fbx:{mat.element_id}"
            pbr, fbx = mat.pbr, mat.fbx
            retained_views.extend((mat, pbr, fbx))
            base_color, diffuse_color = pbr.base_color, fbx.diffuse_color
            color_map = base_color if base_color.has_value else diffuse_color
            color = list(color_map.value_vec4[:3]) if color_map.has_value else [.65,.73,.82]
            base_factor, diffuse_factor = pbr.base_factor, fbx.diffuse_factor
            factor_map = base_factor if base_factor.has_value else diffuse_factor
            factor = factor_map.value_real if factor_map.has_value else 1.0
            opacity_map, transparency = pbr.opacity, fbx.transparency_factor
            opacity = opacity_map.value_real if opacity_map.has_value else 1.0
            if not opacity_map.has_value and transparency.has_value:
                opacity = 1 - transparency.value_real
            if not all(math.isfinite(v) for v in color+[factor, opacity]):
                raise ValueError(f"FBX 材质 {mat.name} 包含 NaN / Infinity")
            color = [float(max(0,min(1,c*factor))) for c in color] + [float(max(0,min(1,opacity)))]
            result["materials"][key] = {"color": color}
            tex = color_map.texture if color_map.texture_enabled else None
            if tex:
                result["materials"][key]["image"] = _image(tex, path.parent)
                textures[key] = tex
            # This scene schema supports base color only; reject other texture
            # channels rather than silently drop normal/displacement/alpha maps.
            for channel in mat.textures:
                if not tex or channel.texture.element_id != tex.element_id:
                    raise ValueError(f"FBX 材质 {mat.name} 包含未支持的纹理通道 {channel.material_prop}；请烘焙基础颜色")
        node_views = scene.nodes
        for node in node_views:
            check()
            mesh = node.mesh
            if not mesh:
                continue
            if mesh.num_line_faces or mesh.num_point_faces:
                raise ValueError("FBX 包含线/点面；请转换为三角网格")
            matrix = node.geometry_to_world
            linear = np.asarray([matrix.c0, matrix.c1, matrix.c2], dtype=float).T
            translation = np.asarray(matrix.c3, dtype=float)
            if abs(np.linalg.det(linear)) < 1e-15:
                raise ValueError(f"FBX 节点 {node.name} 的变换不可逆")
            normal_matrix = axis @ np.linalg.inv(linear).T
            points = [((axis @ (linear @ np.asarray(p) + translation))*scale).tolist()
                      for p in mesh.vertex_position.values]
            obj = {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source_index}:{digest}:fbx:{node.element_id}")),
                   "name": node.name or path.stem, "source": path.name, "sourceIndex": source_index,
                   "properties": dict((extras or {}).get(node.name, {})), "triangles": []}
            flip = (np.linalg.det(linear) < 0) != bool(reverse_winding)
            pos_attr, norm_attr = mesh.vertex_position, mesh.vertex_normal
            for face_index, face in enumerate(mesh.faces):
                if face_index % 1000 == 0:
                    check()
                if face.num_indices < 3:
                    raise ValueError("FBX 包含空面或非多边形面")
                material_index = mesh.face_material[face_index] if mesh.face_material else 0
                node_materials = node.materials
                if node_materials:
                    assigned_material = node_materials[material_index]
                    key = f"{source_index}:fbx:{assigned_material.element_id}"
                else:
                    key = default
                tex = textures.get(key)
                uv_attr = mesh.vertex_uv
                if tex and tex.uv_set:
                    matching = [s for s in mesh.uv_sets if s.name == tex.uv_set]
                    if not matching:
                        raise ValueError(f"FBX 纹理 UV 集不存在: {tex.uv_set}")
                    uv_attr = matching[0].vertex_uv
                if tex and not uv_attr.exists:
                    raise ValueError(f"FBX 节点 {node.name} 的纹理缺少 UV")
                corners = [(pos_attr.indices[i], i) for i in range(face.index_begin, face.index_begin+face.num_indices)]
                for triangle in triangulate(corners, points):
                    if flip:
                        triangle.reverse()
                    p = [points[c[0]] for c in triangle]
                    flat = np.cross(np.asarray(p[1])-p[0], np.asarray(p[2])-p[0])
                    if np.linalg.norm(flat) <= 1e-15:
                        raise ValueError("FBX 包含退化三角形")
                    normals, uvs = [], []
                    for _, corner in triangle:
                        normal = normal_matrix @ np.asarray(norm_attr.values[norm_attr.indices[corner]]) if norm_attr.exists else flat
                        if reverse_winding and norm_attr.exists:
                            normal = -normal
                        length = float(np.linalg.norm(normal))
                        if length <= 1e-15:
                            raise ValueError("FBX 包含零长度法线")
                        normals.append((normal/length).tolist())
                        uv = list(uv_attr.values[uv_attr.indices[corner]]) if uv_attr.exists else [0.,0.]
                        if tex and tex.has_uv_transform:
                            uv = list(ufbx.transform_position(tex.uv_to_texture, ufbx.Vec3(*uv, 0))[:2])
                        uvs.append(uv)
                    values = [v for row in p+normals+uvs for v in row]
                    if not all(math.isfinite(v) for v in values):
                        raise ValueError("FBX 包含 NaN / Infinity")
                    obj["triangles"].append({"p": p, "n": normals, "uv": uvs, "material": key})
            if obj["triangles"]:
                result["objects"].append(obj)
        if not result["objects"]:
            raise ValueError("FBX 输入不包含可切片的多边形网格")
        return result
    finally:
        # Wrappers keep the native scene alive; let reference counting release
        # the scene after its children, instead of freeing borrowed views early.
        textures.clear()
