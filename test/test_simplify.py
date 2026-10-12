import copy
import math
import unittest

from model_tiler.simplify import simplify_faces


def grid(size=16, offset=0, material="plain"):
    triangles = []
    for y in range(size):
        for x in range(size):
            corners = [[x+offset, y, 0], [x+1+offset, y, 0],
                       [x+1+offset, y+1, 0], [x+offset, y+1, 0]]
            for ids in ([0, 1, 2], [0, 2, 3]):
                triangles.append({"p": [corners[i] for i in ids],
                                  "n": [[0, 0, 1]]*3,
                                  "uv": [[0, 0]]*3, "material": material})
    return triangles


class SimplificationTests(unittest.TestCase):
    def setUp(self):
        self.scene = {"materials": {"plain": {}, "other": {}, "texture": {"image": {"data": "x"}}}}

    def test_real_reduction_normals_feature_coverage_and_component_bounds(self):
        source = [(7, t) for t in grid()] + [(7, t) for t in grid(1, 100)] + [(11, t) for t in grid(8, 200)]
        original = copy.deepcopy(source)
        reduced, error, stats = simplify_faces(source, self.scene, .25)
        self.assertEqual(source, original)
        self.assertLess(len(reduced), len(source)*.65)
        self.assertEqual({i for i, _ in reduced}, {7, 11})
        self.assertTrue(any(t["p"][0][0] >= 100 and t["p"][0][0] < 102 for _, t in reduced))
        self.assertAlmostEqual(error, math.sqrt(16**2+16**2))
        self.assertEqual(stats["inputTriangles"], len(source))
        self.assertEqual(stats["outputTriangles"], len(reduced))
        self.assertEqual(stats["achievedRatio"], len(reduced)/len(source))
        self.assertEqual(stats["simplifiedComponents"], 2)
        for _, t in reduced:
            self.assertEqual(t["material"], "plain")
            for n in t["n"]:
                self.assertTrue(all(math.isfinite(v) for v in n))
                self.assertAlmostEqual(math.hypot(*n), 1)
                self.assertGreater(n[2], .99)

    def test_preserves_material_texture_uv_and_normal_seams(self):
        cases = []
        triangles = grid(4)
        triangles[0]["material"] = "other"
        cases.append((triangles, "material-boundaries"))
        cases.append((grid(4, material="texture"), "texture-or-uv"))
        triangles = grid(4)
        triangles[0]["uv"][0] = [.5, .5]
        cases.append((triangles, "texture-or-uv"))
        triangles = grid(4)
        triangles[0]["n"] = [[0, 1, 0]]*3
        cases.append((triangles, "normal-seams"))
        for triangles, reason in cases:
            with self.subTest(reason=reason):
                source = [(3, t) for t in triangles]
                result, error, stats = simplify_faces(source, self.scene, .1)
                self.assertEqual(result, source)
                self.assertEqual(error, 0)
                self.assertEqual(stats["preservedReasons"], {reason: 1})

    def test_curved_mesh_is_decimated_and_keeps_outward_normals(self):
        points = [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]]
        indices = [[0, 2, 4], [2, 1, 4], [1, 3, 4], [3, 0, 4],
                   [2, 0, 5], [1, 2, 5], [3, 1, 5], [0, 3, 5]]
        for _ in range(3):
            mids, new = {}, []
            def midpoint(a, b):
                key = tuple(sorted((a, b)))
                if key not in mids:
                    p = [(x+y)/2 for x, y in zip(points[a], points[b])]
                    length = math.hypot(*p)
                    mids[key] = len(points)
                    points.append([v/length for v in p])
                return mids[key]
            for a, b, c in indices:
                ab, bc, ca = midpoint(a, b), midpoint(b, c), midpoint(c, a)
                new.extend([[a, ab, ca], [ab, b, bc], [ca, bc, c], [ab, bc, ca]])
            indices = new
        source = [(5, {"p": [points[i] for i in face], "n": [points[i] for i in face],
                       "uv": [[0, 0]]*3, "material": "plain"}) for face in indices]
        reduced, error, stats = simplify_faces(source, self.scene, .25)
        self.assertLess(len(reduced), len(source)*.5)
        self.assertEqual(stats["simplifiedComponents"], 1)
        self.assertGreater(error, 0)
        for _, triangle in reduced:
            for p, n in zip(triangle["p"], triangle["n"]):
                self.assertGreater(sum(x*y for x, y in zip(p, n)), .7)
                self.assertAlmostEqual(math.hypot(*n), 1)

    def test_ratio_validation_and_cancellation(self):
        for ratio in (0, -1, 1.01, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                simplify_faces([], self.scene, ratio)
        def cancelled():
            raise RuntimeError("cancelled")
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            simplify_faces([(0, t) for t in grid()], self.scene, .5, cancelled)

    def test_aggressive_ratio_retains_every_small_component(self):
        source = [(0, t) for t in grid()] + [(1, t) for t in grid(1, 100)]
        reduced, _, _ = simplify_faces(source, self.scene, .00001)
        self.assertTrue(any(i == 0 for i, _ in reduced))
        self.assertEqual(sum(i == 1 for i, _ in reduced), 2)


if __name__ == "__main__":
    unittest.main()
