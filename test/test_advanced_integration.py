import base64
import json
from pathlib import Path
import tempfile
import unittest

from model_tiler.core import Options, import_scene, tile_scene
from model_tiler.server import TaskStore
from test_fbx import ASCII
from test_simplify import grid
from test_tiler import DEMO, decode_tile


class AdvancedIntegrationTests(unittest.TestCase):
    def test_mixed_obj_fbx_csv_and_resource_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder)
            fbx=folder/'mesh.fbx';fbx.write_text(ASCII,encoding='utf-8')
            csv=folder/'props.csv';csv.write_text('name,usage\nQuad,fbx mesh\n主楼,obj mesh\n',encoding='utf-8')
            scene=import_scene([DEMO/'demo.obj',fbx],properties=csv)
            self.assertEqual(len(scene['sources']),2)
            obj=next(o for o in scene['objects'] if o['name']=='Quad')
            self.assertEqual(obj['sourceIndex'],1)
            self.assertEqual(obj['properties']['usage'],'fbx mesh')
            self.assertEqual(scene['sources'][1]['format'],'fbx')
            report=tile_scene(scene,folder/'out',Options(draco=True))
            self.assertEqual(report['triangles'],26)
            tileset=json.loads((folder/'out/tileset.json').read_text('utf-8'))
            self.assertGreater(tileset['root']['geometricError'],0)
            tree=json.loads((folder/'out/scenetree.json').read_text('utf-8'))
            self.assertEqual(len(tree['scenes']),2)
            self.assertEqual(tree['scenes'][1]['children'][0]['id'],obj['id'])

    def test_full_resolution_lod_leaves_metadata_and_monotone_errors(self):
        scene=dict(version=1,objects=[dict(id='terrain',name='terrain',source='grid.obj',sourceIndex=0,
                   properties={'usage':'terrain'},triangles=grid()+grid(1,100))],
                   materials={'plain':{'color':[.5,.5,.5,1]}},sources=[{'name':'grid.obj','index':0}])
        original=json.dumps(scene)
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder)/'out'
            report=tile_scene(scene,folder,Options(mode='simplify',lod_ratio=.25,lod_levels=2))
            self.assertEqual(report['triangles'],514)
            self.assertEqual(report['leafTriangles'],514)
            self.assertTrue(any(s['outputTriangles']<s['inputTriangles'] for s in report['lod']))
            self.assertEqual(len(report['tiles']),3)
            counts={}
            for tile in report['tiles']:
                ft,bt,doc,_=decode_tile((folder/tile['uri']).read_bytes())
                self.assertEqual(bt['id'],['terrain'])
                self.assertEqual(bt['usage'],['terrain'])
                counts[tile['lodLevel']]=tile['triangles']
            self.assertEqual(counts[0],514)
            self.assertLess(counts[2],counts[1]);self.assertLess(counts[1],counts[0])
            tileset=json.loads((folder/'tileset.json').read_text('utf-8'))
            self.assertGreater(tileset['geometricError'],tileset['root']['geometricError'])
            node=tileset['root']
            while node.get('children'):
                self.assertEqual(node['refine'],'REPLACE')
                child=node['children'][0]
                self.assertGreaterEqual(node['geometricError'],child['geometricError'])
                node=child
            self.assertEqual(node['geometricError'],0)
        self.assertEqual(json.dumps(scene),original)

    def test_fbx_upload_and_advanced_cache_reuse(self):
        with tempfile.TemporaryDirectory() as folder:
            store=TaskStore(Path(folder)/'tasks')
            first=store.create({'files':[{'name':'mesh.fbx','data':base64.b64encode(ASCII.encode()).decode()}],
                                'options':{'draco':True}})
            store.pool.shutdown()
            self.assertEqual(store.tasks[first['id']]['status'],'completed',store.tasks[first['id']])
            store=TaskStore(Path(folder)/'tasks')
            try:
                next_task=store.create({'resourceTask':first['id'],'options':{'draco':True,'mode':'simplify','instance_min_count':2}})
            finally:
                store.pool.shutdown()
            self.assertEqual(store.tasks[next_task['id']]['status'],'completed',store.tasks[next_task['id']])
            self.assertEqual(store.tasks[next_task['id']]['report']['triangles'],2)


if __name__=='__main__':unittest.main()
