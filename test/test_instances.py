import copy
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest

from model_tiler.core import Options, tile_scene
from model_tiler.instances import group_instances
from test_tiler import decode_tile


def repeated_scene():
    points=[[0,0,0],[2,0,0],[0,3,0],[0,0,4]]
    prototype=[]
    for indices in ([0,1,2],[0,3,1],[0,2,3],[1,3,2]):
        p=[points[i] for i in indices]
        a=[p[1][i]-p[0][i] for i in range(3)];b=[p[2][i]-p[0][i] for i in range(3)]
        n=[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]
        n=[v/math.hypot(*n) for v in n]
        prototype.append({'p':p,'n':[n]*3,'uv':[[0,0]]*3,'material':'m'})
    objects=[]
    for i,(angle,scale,position) in enumerate(((0,1,[0,0,0]),(math.pi/2,2,[10,0,3]),(.4,.5,[0,10,5]))):
        def rotate(p):
            c,s=math.cos(angle),math.sin(angle)
            return [p[0]*c-p[1]*s,p[0]*s+p[1]*c,p[2]]
        triangles=[dict(t,p=[[v*scale+position[a] for a,v in enumerate(rotate(p))] for p in t['p']],
                        n=[rotate(n) for n in t['n']]) for t in prototype]
        objects.append(dict(id=str(i),name=f'part{i}',source='fixture.obj',sourceIndex=0,properties={'usage':str(i)},triangles=triangles))
    return dict(version=1,objects=objects,materials={'m':{'color':[1,0,0,1]}},sources=[{'name':'fixture.obj','index':0}])


def parse_i3dm(data):
    magic,version,size,fj,fb,bj,bb,fmt=struct.unpack_from('<4s7I',data)
    assert (magic,version,size,fmt)==(b'i3dm',1,len(data),1)
    assert size%8==0
    ft=json.loads(data[32:32+fj]);binary=data[32+fj:32+fj+fb]
    offset=32+fj+fb
    bt=json.loads(data[offset:offset+bj]);offset+=bj+bb
    assert offset%8==0
    glb=data[offset:offset+struct.unpack_from('<I',data,offset+8)[0]]
    json_size=struct.unpack_from('<I',glb,12)[0];doc=json.loads(glb[20:20+json_size]);gb=glb[28+json_size:]
    return ft,binary,bt,doc,gb


class InstanceTests(unittest.TestCase):
    def test_rotated_scaled_instances_binary_and_reconstruction(self):
        scene=repeated_scene();before=copy.deepcopy(scene)
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'tiles'
            report=tile_scene(scene,output,Options(instance_min_count=2,origin='center'))
            self.assertEqual(report['instancedObjects'],3)
            self.assertEqual(report['tiles'][0]['format'],'i3dm')
            self.assertEqual(report['storedTriangles'],4)
            self.assertEqual(report['leafTriangles'],12)
            ft,fb,bt,doc,gb=parse_i3dm((output/report['tiles'][0]['uri']).read_bytes())
            self.assertEqual(ft['INSTANCES_LENGTH'],3)
            self.assertEqual(bt['id'],['0','1','2'])
            template=[]
            for primitive in doc['meshes'][0]['primitives']:
                self.assertNotIn('_BATCHID',primitive['attributes'])
                accessor=doc['accessors'][primitive['attributes']['POSITION']];view=doc['bufferViews'][accessor['bufferView']]
                raw=struct.unpack_from('<'+str(accessor['count']*3)+'f',gb,view.get('byteOffset',0))
                template.extend([[raw[i],-raw[i+2],raw[i+1]] for i in range(0,len(raw),3)])
            def vectors(key,width):
                return struct.unpack_from('<'+str(3*width)+'f',fb,ft[key]['byteOffset'])
            positions=vectors('POSITION',3);right=vectors('NORMAL_RIGHT',3);up=vectors('NORMAL_UP',3);scale=vectors('SCALE',1)
            for i,obj in enumerate(scene['objects']):
                x=right[i*3:i*3+3];y=up[i*3:i*3+3]
                z=[x[1]*y[2]-x[2]*y[1],x[2]*y[0]-x[0]*y[2],x[0]*y[1]-x[1]*y[0]]
                expected=[p for t in obj['triangles'] for p in t['p']]
                for p,q in zip(template,expected):
                    rebuilt=[ft['RTC_CENTER'][a]+positions[i*3+a]+scale[i]*(p[0]*x[a]+p[1]*y[a]+p[2]*z[a])+report['originOffset'][a] for a in range(3)]
                    for a,b in zip(rebuilt,q):self.assertAlmostEqual(a,b,places=5)
        self.assertEqual(scene,before)

    def test_mixed_content_cmpt_and_material_mismatch(self):
        scene=repeated_scene()
        obj=copy.deepcopy(scene['objects'][0]);obj.update(id='different',name='different')
        for t in obj['triangles']:t['uv']=[[.2,.2]]*3
        scene['objects'].append(obj)
        with tempfile.TemporaryDirectory() as folder:
            report=tile_scene(scene,Path(folder)/'out',Options(instance_min_count=2))
            tile=report['tiles'][0];self.assertEqual(tile['format'],'cmpt')
            data=(Path(folder)/'out'/tile['uri']).read_bytes()
            self.assertEqual(struct.unpack_from('<4s3I',data),(b'cmpt',1,len(data),2))
            size=struct.unpack_from('<I',data,24)[0]
            self.assertEqual(parse_i3dm(data[16:16+size])[2]['id'],['0','1','2'])
            self.assertEqual(decode_tile(data[16+size:])[1]['id'],['different'])

    def test_partial_objects_threshold_and_rounding_do_not_lose_geometry(self):
        scene=repeated_scene()
        faces=[(i,t) for i,obj in enumerate(scene['objects']) for t in obj['triangles']]
        groups,left=group_instances(faces[:-1],scene,2)
        self.assertEqual(len(groups),1)
        self.assertEqual(len(left),3)
        groups,left=group_instances(faces,scene,4)
        self.assertEqual(groups,[]);self.assertEqual(len(left),12)
        with tempfile.TemporaryDirectory() as folder:
            report=tile_scene(scene,Path(folder)/'out',Options(instance_min_count=2,max_triangles=2))
            self.assertEqual(sum(t['triangles'] for t in report['tiles']),12)
            self.assertTrue(all(t['format']=='b3dm' for t in report['tiles']))

    def test_new_option_validation(self):
        for kwargs in ({'draco':'yes'},{'draco_level':11},{'draco_bits':0},{'instance_min_count':1},
                       {'instance_min_count':-1},{'lod_ratio':0},{'lod_ratio':1},{'lod_levels':5}):
            with self.assertRaises(ValueError):Options(**kwargs).validate()


if __name__=='__main__':unittest.main()
