import base64
import json
from pathlib import Path
import struct
import unittest

import DracoPy
import numpy as np

from model_tiler.core import glb, import_scene
from model_tiler.draco import _read_glb, compress_glb


class DracoTests(unittest.TestCase):
    def setUp(self):
        demo = Path(__file__).resolve().parents[1] / "src/model_tiler/web"
        self.scene = import_scene([demo / "demo.obj"], properties=demo / "demo.csv")
        self.faces = [(i, t) for i, obj in enumerate(self.scene["objects"]) for t in obj["triangles"]]

    def decode(self, blob):
        doc, binary = _read_glb(blob)
        meshes = []
        for primitive in doc["meshes"][0]["primitives"]:
            ext = primitive["extensions"]["KHR_draco_mesh_compression"]
            view = doc["bufferViews"][ext["bufferView"]]
            data = binary[view["byteOffset"]:view["byteOffset"] + view["byteLength"]]
            self.assertEqual(data[:5], b"DRACO")
            meshes.append((primitive, ext, DracoPy.decode(data)))
        return doc, binary, meshes

    def test_real_decode_counts_bounds_ids_and_face_association(self):
        original, _, _ = glb(self.faces, self.scene, unlit=True)
        result = compress_glb(original)
        doc, binary, meshes = self.decode(result)
        self.assertIn("KHR_draco_mesh_compression", doc["extensionsRequired"])
        self.assertIn("KHR_materials_unlit", doc["extensionsUsed"])
        actual = []
        for p, ext, decoded in meshes:
            for name, index in p["attributes"].items():
                accessor = doc["accessors"][index]
                attr = decoded.get_attribute_by_unique_id(ext["attributes"][name])["data"]
                self.assertEqual(accessor["count"], len(attr))
                self.assertNotIn("bufferView", accessor)
                if name == "POSITION":
                    np.testing.assert_allclose(accessor["min"], attr.min(axis=0))
                    np.testing.assert_allclose(accessor["max"], attr.max(axis=0))
            ids = decoded.get_attribute_by_unique_id(ext["attributes"]["_BATCHID"])["data"].ravel()
            self.assertEqual(doc["accessors"][p["attributes"]["_BATCHID"]]["componentType"], 5126)
            self.assertEqual(doc["accessors"][p["indices"]]["count"], decoded.faces.size)
            for face in decoded.faces:
                self.assertTrue(np.all(ids[face] == ids[face[0]]))
                for index in face:
                    x, y, z = decoded.points[index]
                    actual.append((int(ids[index]), tuple(round(float(v), 2) for v in (x, -z, y))))
        expected = [(i, tuple(round(v, 2) for v in p)) for i, t in self.faces for p in t["p"]]
        self.assertCountEqual(actual, expected)
        # Embedded binary holds only Draco streams, no uncompressed geometry.
        self.assertEqual(len(doc["bufferViews"]), len(meshes))

    def test_embedded_image_material_and_lossless_uv_normals(self):
        image = b"image fixture bytes"
        first = self.scene["materials"][self.faces[0][1]["material"]]
        first["image"] = {"mime": "image/png", "data": base64.b64encode(image).decode()}
        for _, triangle in self.faces:
            triangle["uv"] = [[.125, .25], [.375, .5], [.625, .75]]
        original, _, _ = glb(self.faces, self.scene)
        before, _ = _read_glb(original)
        doc, binary, meshes = self.decode(compress_glb(original))
        self.assertEqual(doc["materials"], before["materials"])
        v = doc["bufferViews"][doc["images"][0]["bufferView"]]
        self.assertEqual(binary[v["byteOffset"]:v["byteOffset"] + v["byteLength"]], image)
        actual = []
        for _, ext, decoded in meshes:
            np.testing.assert_allclose(np.linalg.norm(decoded.normals, axis=1), 1, atol=1e-6)
            self.assertTrue(np.isfinite(decoded.tex_coord).all())
            ids = decoded.get_attribute_by_unique_id(ext["attributes"]["_BATCHID"])["data"].ravel()
            for face in decoded.faces:
                for index in face:
                    actual.append((int(ids[index]), tuple(np.round(decoded.points[index], 2)),
                                   tuple(decoded.normals[index]), tuple(decoded.tex_coord[index])))
        expected = [(i, tuple(round(v, 2) for v in (p[0], p[2], -p[1])),
                     (n[0], n[2], -n[1]), (u[0], 1-u[1]))
                    for i, triangle in self.faces for p, n, u in zip(triangle["p"], triangle["n"], triangle["uv"])]
        self.assertCountEqual(actual, expected)

    def test_meaningful_geometry_reduction(self):
        material = next(iter(self.scene["materials"]))
        faces = []
        for x in range(20):
            for y in range(20):
                for corners in (((x,y,0),(x+1,y,0),(x+1,y+1,0)), ((x,y,0),(x+1,y+1,0),(x,y+1,0))):
                    faces.append((0, {"p": corners, "n": [[0,0,1]]*3, "uv": [[0,0]]*3, "material": material}))
        original, _, _ = glb(faces, self.scene)
        result = compress_glb(original)
        self.assertLess(len(result), len(original) / 4)
        _, _, meshes = self.decode(result)
        self.assertEqual(sum(len(m.faces) for _, _, m in meshes), 800)

    def test_invalid_parameters_and_input(self):
        original, _, _ = glb(self.faces, self.scene)
        for settings in ({"compression_level": 11}, {"quantization_bits": 0}):
            with self.assertRaises(ValueError):
                compress_glb(original, **settings)
        with self.assertRaises(ValueError):
            compress_glb(b"bad")
        with self.assertRaises(ValueError):
            compress_glb(compress_glb(original))


if __name__ == "__main__":
    unittest.main()
