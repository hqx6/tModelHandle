import base64
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest

from model_tiler.core import Cancelled, Options, b3dm, enu_transform, import_scene, tile_scene
from model_tiler.server import TaskStore

DEMO = Path(__file__).resolve().parents[1]/"src/model_tiler/web"


def decode_tile(data):
    magic,version,length,ftj,ftb,btj,btb = struct.unpack_from("<4s6I",data)
    assert (magic,version,length)==(b"b3dm",1,len(data))
    assert length%8==0
    ft=json.loads(data[28:28+ftj]);start=28+ftj+ftb
    bt=json.loads(data[start:start+btj]);start+=btj+btb
    assert start%8==0
    gmagic,gversion,glen=struct.unpack_from("<4sII",data,start)
    assert (gmagic,gversion)==(b"glTF",2)
    size,typ=struct.unpack_from("<I4s",data,start+12)
    assert typ==b"JSON"
    doc=json.loads(data[start+20:start+20+size]);binary=data[start+28+size:start+glen]
    def values(index):
        a=doc["accessors"][index];v=doc["bufferViews"][a["bufferView"]]
        width={"VEC3":3,"VEC2":2,"SCALAR":1}[a["type"]]
        fmt="f" if a["componentType"]==5126 else "I"
        return struct.unpack_from("<"+str(a["count"]*width)+fmt,binary,v.get("byteOffset",0))
    return ft,bt,doc,values


class TilerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.scene=import_scene([DEMO/"demo.obj"],properties=DEMO/"demo.csv")
    def tearDown(self):
        self.temp.cleanup()

    def test_geometry_metadata_and_binary_layout(self):
        report=tile_scene(self.scene,self.root/"out",Options(max_triangles=5))
        self.assertEqual(report["triangles"],24)
        self.assertEqual(sum(t["triangles"] for t in report["tiles"]),24)
        decoded_positions=[]
        for tile in report["tiles"]:
            ft,bt,doc,values=decode_tile((self.root/"out"/tile["uri"]).read_bytes())
            self.assertEqual(len(bt["name"]),ft["BATCH_LENGTH"])
            self.assertEqual(len(bt["用途"]),ft["BATCH_LENGTH"])
            for primitive in doc["meshes"][0]["primitives"]:
                attributes=primitive["attributes"]
                self.assertTrue(all(0<=i<ft["BATCH_LENGTH"] for i in values(attributes["_BATCHID"])))
                raw=values(attributes["POSITION"])
                decoded_positions += [tuple(round(v,5) for v in (raw[i],-raw[i+2],raw[i+1])) for i in range(0,len(raw),3)]
                norms=values(attributes["NORMAL"])
                for i in range(0,len(norms),3):
                    self.assertAlmostEqual(math.hypot(*norms[i:i+3]),1,places=6)
        expected=[tuple(p) for obj in self.scene["objects"] for t in obj["triangles"] for p in t["p"]]
        self.assertCountEqual(decoded_positions,expected)
        tileset=json.loads((self.root/"out/tileset.json").read_text())
        self.assertGreater(tileset["geometricError"],0)
        def inspect(node):
            b=node["boundingVolume"]["box"]
            if "children" in node and "content" not in node:
                self.assertGreater(node["geometricError"],0)
            if "content" in node:
                ft,bt,doc,values=decode_tile((self.root/"out"/node["content"]["uri"]).read_bytes())
                for p in doc["meshes"][0]["primitives"]:
                    v=values(p["attributes"]["POSITION"])
                    for i in range(0,len(v),3):
                        enu=[v[i],-v[i+2],v[i+1]]
                        for a,half in enumerate([b[3],b[7],b[11]]):
                            self.assertLessEqual(abs(enu[a]-b[a]),half+1e-5)
            for child in node.get("children",[]):
                inspect(child)
        inspect(tileset["root"])

    def test_ecef_anchor_and_orientation(self):
        m=enu_transform(0,0,10)
        self.assertEqual(m[12:15],[6378147.0,0,0])
        self.assertEqual(m[:3],[0,1,0])
        self.assertEqual(m[4:7],[0,0,1])
        self.assertEqual(m[8:11],[1,0,0])
        with self.assertRaises(ValueError): enu_transform(200,0,0)

    def test_concave_negative_indices_y_up_and_scale(self):
        file=self.root/"concave.obj"
        file.write_text("o shape\nv 0 0 0\nv 2 0 0\nv 2 2 0\nv 1 1 0\nv 0 2 0\nf -5 -4 -3 -2 -1\n")
        s=import_scene([file],"Y",2)
        tris=s["objects"][0]["triangles"]
        self.assertEqual(len(tris),3)
        area=0
        for t in tris:
            a,b,c=t["p"]
            area+=abs((b[0]-a[0])*(c[2]-a[2])-(b[2]-a[2])*(c[0]-a[0]))/2
        self.assertEqual(area,12)

    def test_octree_add_without_loss_or_duplicate_features(self):
        path=self.root/"mixed.obj"
        path.write_text("o large\nv 0 0 0\nv 100 0 0\nv 0 100 0\nf 1 2 3\no tiny\nv 98 98 0\nv 98.1 98 0\nv 98 98.1 0\nf 4 5 6\n")
        s=import_scene([path]);tile_scene(s,self.root/"oct",Options(mode="octree",visible_pixels=25))
        tileset=json.loads((self.root/"oct/tileset.json").read_text())
        self.assertEqual(tileset["root"]["refine"],"ADD")
        self.assertTrue(tileset["root"]["children"])
        ids=[]
        for f in (self.root/"oct/tiles").glob("*.b3dm"):
            ids+=decode_tile(f.read_bytes())[1]["id"]
        self.assertCountEqual(ids,[o["id"] for o in s["objects"]])

    def test_texture_embedded(self):
        # Valid 1x1 PNG; test independently verifies the glTF image range and MIME.
        png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=')
        (self.root/"pixel.png").write_bytes(png)
        (self.root/"m.mtl").write_text('newmtl m\nKd 1 1 1\nmap_Kd pixel.png\n')
        (self.root/"m.obj").write_text('mtllib m.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 0 1\nusemtl m\nf 1/1 2/2 3/3\n')
        s=import_scene([self.root/"m.obj"])
        ft,bt,d,v=decode_tile(b3dm([(0,t) for t in s["objects"][0]["triangles"]],s))
        self.assertEqual(d["images"][0]["mimeType"],"image/png")
        self.assertEqual(d["bufferViews"][d["images"][0]["bufferView"]]["byteLength"],len(png))
        self.assertEqual(v(d["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"]),(0,1,1,1,0,0))

    def test_cache_roundtrip_and_output_not_overwritten(self):
        scene=json.loads(json.dumps(self.scene))
        tile_scene(scene,self.root/"out")
        before=(self.root/"out/tileset.json").read_bytes()
        with self.assertRaises(FileExistsError): tile_scene(scene,self.root/"out")
        self.assertEqual((self.root/"out/tileset.json").read_bytes(),before)

    def test_cancel_removes_partial_output(self):
        with self.assertRaises(Cancelled):
            tile_scene(self.scene,self.root/"out",cancelled=lambda:True)
        self.assertFalse((self.root/"out").exists())
        self.assertFalse(list(self.root.glob(".tiling-*")))

    def test_invalid_geometry_and_path_escape(self):
        for text in ['v nan 0 0\n','v 0 0 0\nf 1 2 3\n','mtllib ../other.mtl\n']:
            (self.root/"bad.obj").write_text(text)
            with self.assertRaises(ValueError): import_scene([self.root/"bad.obj"])

    def test_task_upload_validation(self):
        store=TaskStore(self.root/"tasks")
        try:
            for name in ['../outside.obj','/outside.obj','bad.exe','C:/foo.obj']:
                with self.assertRaises(ValueError):
                    store.create({"files":[{"name":name,"data":""}]})
        finally:store.pool.shutdown()

    def test_gbk_and_reversed_winding_with_explicit_normals(self):
        (self.root/'材质.mtl').write_text('newmtl 墙\nKd 1 0 0\n',encoding='gbk')
        path=self.root/'gbk.obj'
        path.write_text('mtllib 材质.mtl\no 主楼\nv 0 0 0\nv 1 0 0\nv 0 1 0\nvn 0 0 1\nusemtl 墙\nf 1//1 2//1 3//1\n',encoding='gbk')
        s=import_scene([path],encoding='gbk',reverse_winding=True)
        self.assertEqual(s['objects'][0]['name'],'主楼')
        t=s['objects'][0]['triangles'][0]
        self.assertEqual(t['p'],[[0,1,0],[1,0,0],[0,0,0]])
        self.assertEqual(t['n'],[[0,0,-1]]*3)
        implicit=self.root/'implicit.obj'
        implicit.write_text('v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
        self.assertEqual(import_scene([implicit],reverse_winding=True)['objects'][0]['triangles'][0]['n'],[[0,0,-1]]*3)

    def test_unlit_alpha_and_property_selection_in_actual_b3dm(self):
        for mode in ('OPAQUE','MASK','BLEND'):
            tile_scene(self.scene,self.root/mode,Options(unlit=True,alpha_mode=mode,alpha_cutoff=.3,property_fields=[]))
            ft,bt,doc,values=decode_tile(next((self.root/mode/'tiles').glob('*.b3dm')).read_bytes())
            self.assertEqual(set(bt),{'id','name'})
            self.assertEqual(doc['extensionsUsed'],['KHR_materials_unlit'])
            for m in doc['materials']:
                self.assertEqual(m['alphaMode'],mode)
                self.assertIn('KHR_materials_unlit',m['extensions'])
                if mode=='MASK': self.assertEqual(m['alphaCutoff'],.3)
            tree=json.loads((self.root/mode/'scenetree.json').read_text())
            self.assertTrue(all(c['properties']=={} for s in tree['scenes'] for c in s['children']))
        tile_scene(self.scene,self.root/'selected',Options(property_fields=['用途']))
        self.assertEqual(set(decode_tile(next((self.root/'selected/tiles').glob('*.b3dm')).read_bytes())[1]),{'id','name','用途'})
        self.assertIn('用途',self.scene['objects'][0]['properties'])

    def test_center_origin_preserves_world_position_and_resource(self):
        before=json.dumps(self.scene)
        tile_scene(self.scene,self.root/'original')
        report=tile_scene(self.scene,self.root/'center',Options(origin='center'))
        self.assertNotEqual(report['originOffset'],[0,0,0])
        self.assertEqual(before,json.dumps(self.scene))
        def world_points(folder):
            m=json.loads((folder/'tileset.json').read_text())['root']['transform']
            points=[]
            for file in (folder/'tiles').glob('*.b3dm'):
                _,_,doc,values=decode_tile(file.read_bytes())
                for primitive in doc['meshes'][0]['primitives']:
                    v=values(primitive['attributes']['POSITION'])
                    for i in range(0,len(v),3):
                        enu=[v[i],-v[i+2],v[i+1]]
                        points.append([m[12+a]+sum(m[4*j+a]*enu[j] for j in range(3)) for a in range(3)])
            return points
        for a,b in zip(world_points(self.root/'original'),world_points(self.root/'center')):
            for x,y in zip(a,b): self.assertAlmostEqual(x,y,places=6)
        a=json.loads((self.root/'original/scenetree.json').read_text(encoding='utf-8'))
        b=json.loads((self.root/'center/scenetree.json').read_text(encoding='utf-8'))
        for x,y in zip(a['scenes'][0]['children'],b['scenes'][0]['children']):
            for p,q in zip(x['sphere'],y['sphere']): self.assertAlmostEqual(p,q,places=6)

    def test_actual_byte_budget_and_unavoidable_overflow(self):
        report=tile_scene(self.scene,self.root/'budget',Options(max_tile_bytes=4500,max_depth=16))
        self.assertGreater(len(report['tiles']),1)
        self.assertEqual(sum(t['triangles'] for t in report['tiles']),24)
        self.assertTrue(all(t['bytes']<=4500 for t in report['tiles']))
        report=tile_scene(self.scene,self.root/'overflow',Options(max_tile_bytes=1,max_depth=2))
        self.assertEqual(len(report['oversizedByteTiles']),len(report['tiles']))
        self.assertTrue(report['warnings'])

    def test_task_resource_reuse_without_upload_and_restart(self):
        store=TaskStore(self.root/'tasks')
        files=[{'name':name,'data':base64.b64encode((DEMO/name).read_bytes()).decode()} for name in ('demo.obj','demo.mtl','demo.csv')]
        first=store.create({'files':files})
        store.pool.shutdown()
        self.assertEqual(store.tasks[first['id']]['status'],'completed')
        before=(store.root/first['id']/'resource.json').read_bytes()
        store=TaskStore(self.root/'tasks')
        try:
            with self.assertRaises(ValueError): store.create({'resourceTask':'../outside'})
            with self.assertRaises(ValueError): store.create({'resourceTask':first['id'],'files':files})
            second=store.create({'resourceTask':first['id'],'options':{'mode':'octree','unlit':True,'property_fields':[],'origin':'center'}})
        finally: store.pool.shutdown()
        result=store.tasks[second['id']]
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(result['report']['triangles'],24)
        self.assertEqual((store.root/second['id']/'resource.json').read_bytes(),before)

    def test_new_option_validation(self):
        for settings in ({'alpha_mode':'bad'},{'alpha_cutoff':float('nan')},{'origin':'bad'},{'property_fields':'用途'},
                         {'max_tile_bytes':-1},{'max_tile_bytes':1.5},{'unlit':'false'}):
            with self.assertRaises(ValueError): Options(**settings).validate()


if __name__=='__main__':unittest.main()
