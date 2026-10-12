"""Conservative, component-preserving QEM geometric LODs.

This is an independent implementation, not CesiumLab's reconstruction algorithm.
Textured/UV meshes, normal seams and material boundaries are retained unchanged.
The error is a safe component AABB diagonal bound, not a measured QEM error.
"""
from __future__ import annotations

from collections import defaultdict
import math
import threading


# fast-simplification's native implementation stores its current mesh globally.
_ENGINE_LOCK = threading.Lock()


def simplify_faces(faces, scene, ratio, check=lambda: None):
    """Return (faces, geometric_error, stats); ratio is fraction to retain.

    Every object/material and disconnected component stays represented. Open
    boundaries are fixed, and normals on simplified meshes are area weighted.
    Cancellation is checked before/after each native component operation.
    """
    if not math.isfinite(ratio) or not 0 < ratio <= 1:
        raise ValueError("LOD 保留比例必须大于 0 且不超过 1")
    try:
        import numpy as np
        import fast_simplification
    except (ImportError, TypeError) as exc:
        raise ValueError("几何化简需要 Python 3.10+、numpy 和 fast-simplification；请安装项目依赖") from exc

    check()
    groups = defaultdict(list)
    for object_index, triangle in faces:
        groups[object_index].append(triangle)
    output, error = [], 0.0
    stats = {"algorithm": "quadric-edge-collapse", "requestedRatio": ratio,
             "inputTriangles": len(faces), "outputTriangles": 0,
             "simplifiedComponents": 0, "preservedComponents": 0,
             "preservedReasons": {}, "errorMethod": "component-aabb-diagonal-bound"}

    def preserve(object_index, triangles, reason):
        output.extend((object_index, t) for t in triangles)
        stats["preservedComponents"] += 1
        stats["preservedReasons"][reason] = stats["preservedReasons"].get(reason, 0) + 1

    for object_index, triangles in groups.items():
        check()
        materials = {t["material"] for t in triangles}
        if len(materials) != 1:
            preserve(object_index, triangles, "material-boundaries")
            continue
        material = next(iter(materials))
        if scene["materials"][material].get("image") or any(
                any(abs(v) > 1e-12 for v in uv) for t in triangles for uv in t["uv"]):
            preserve(object_index, triangles, "texture-or-uv")
            continue

        points, indices, attributes, point_ids = [], [], {}, {}
        seam = False
        for t in triangles:
            vertex_ids = []
            for p, n, uv in zip(t["p"], t["n"], t["uv"]):
                key = tuple(p)
                old = attributes.get(key)
                if old is not None and any(abs(a-b) > 1e-5 for a, b in zip(old, n)):
                    seam = True
                attributes[key] = n
                if key not in point_ids:
                    point_ids[key] = len(points)
                    points.append(p)
                vertex_ids.append(point_ids[key])
            indices.append(vertex_ids)
        if seam:
            preserve(object_index, triangles, "normal-seams")
            continue

        # Split connected components before decimation: small isolated details
        # must not vanish when the global target count is low.
        parent = list(range(len(points)))
        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for a, b, c in indices:
            parent[find(b)] = find(a)
            parent[find(c)] = find(a)
        components = defaultdict(list)
        for i, (a, b, c) in enumerate(indices):
            components[find(a)].append(i)
        for component in components.values():
            check()
            original = [triangles[i] for i in component]
            count = len(component)
            target = max(4, math.ceil(count * ratio))
            if target >= count:
                preserve(object_index, original, "minimum-component-size")
                continue
            ids = sorted({v for i in component for v in indices[i]})
            local_ids = {v: i for i, v in enumerate(ids)}
            xyz = np.asarray([points[v] for v in ids], dtype=np.float64)
            idx = np.asarray([[local_ids[v] for v in indices[i]] for i in component], dtype=np.int32)
            with _ENGINE_LOCK:
                reduced, reduced_idx = fast_simplification.simplify(
                    xyz, idx, target_count=target, preserve_border=True)
            check()
            if len(reduced_idx) == 0 or len(reduced_idx) >= count or not np.isfinite(reduced).all():
                preserve(object_index, original, "no-safe-reduction")
                continue
            # QEM can put a new vertex just outside a convex surface. Constrain
            # it to the original AABB so tile bounds remain valid and the
            # diagonal is a symmetric surface-distance upper bound.
            low, high = xyz.min(axis=0), xyz.max(axis=0)
            reduced = np.clip(reduced, low, high)
            normals = np.zeros_like(reduced)
            face_normals = []
            for a, b, c in reduced_idx:
                n = np.cross(reduced[b]-reduced[a], reduced[c]-reduced[a])
                length = float(np.linalg.norm(n))
                if not math.isfinite(length) or length <= 1e-20:
                    break
                face_normals.append(n / length)
                normals[[a, b, c]] += n
            if len(face_normals) != len(reduced_idx):
                preserve(object_index, original, "degenerate-output")
                continue
            lengths = np.linalg.norm(normals, axis=1)
            component_output = []
            for i, face in enumerate(reduced_idx):
                component_output.append((object_index, {
                    "p": reduced[face].tolist(),
                    "n": [(normals[v]/lengths[v] if lengths[v] > 1e-20 else face_normals[i]).tolist() for v in face],
                    "uv": [[0.0, 0.0] for _ in range(3)], "material": material}))
            output.extend(component_output)
            stats["simplifiedComponents"] += 1
            error = max(error, float(np.linalg.norm(high-low)))
    stats["outputTriangles"] = len(output)
    stats["achievedRatio"] = len(output) / len(faces) if faces else 1.0
    return output, error, stats
