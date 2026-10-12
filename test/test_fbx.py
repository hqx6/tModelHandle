"""Authored FBX 7.4 fixtures exercising the real native parser, no mocks."""
from __future__ import annotations
import struct
import base64
import tempfile
import unittest
from pathlib import Path
from model_tiler.fbx import import_fbx


ASCII = '''; FBX 7.4.0 project-authored test fixture
FBXHeaderExtension: { FBXHeaderVersion: 1003
 FBXVersion: 7400
}
GlobalSettings: { Version: 1000
 Properties70: {
  P: "UpAxis", "int", "Integer", "", 2
  P: "UpAxisSign", "int", "Integer", "", 1
  P: "UnitScaleFactor", "double", "Number", "", 100
 }
}
Objects: {
 Geometry: 10, "Geometry::Quad", "Mesh" {
  Vertices: *12 { a: 0,0,0, 2,0,0, 2,1,0, 0,1,0 }
  PolygonVertexIndex: *4 { a: 0,1,2,-4 }
  LayerElementNormal: 0 {
   Version: 101
   Name: ""
   MappingInformationType: "ByPolygonVertex"
   ReferenceInformationType: "Direct"
   Normals: *12 { a: 0,0,1, 0,0,1, 0,0,1, 0,0,1 }
  }
  LayerElementUV: 0 {
   Version: 101
   Name: "UVSet"
   MappingInformationType: "ByPolygonVertex"
   ReferenceInformationType: "IndexToDirect"
   UV: *8 { a: 0,0, 1,0, 1,1, 0,1 }
   UVIndex: *4 { a: 0,1,2,3 }
  }
  LayerElementMaterial: 0 {
   Version: 101
   Name: ""
   MappingInformationType: "AllSame"
   ReferenceInformationType: "IndexToDirect"
   Materials: *1 { a: 0 }
  }
  Layer: 0 {
   Version: 100
   LayerElement: { Type: "LayerElementNormal"
    TypedIndex: 0
   }
   LayerElement: { Type: "LayerElementUV"
    TypedIndex: 0
   }
   LayerElement: { Type: "LayerElementMaterial"
    TypedIndex: 0
   }
  }
 }
 Model: 20, "Model::Parent", "Null" {
  Version: 232
  Properties70: { P: "Lcl Translation", "Lcl Translation", "", "A", 10,20,30 }
 }
 Model: 21, "Model::Quad", "Mesh" {
  Version: 232
  Properties70: {
   P: "Lcl Translation", "Lcl Translation", "", "A", 1,2,3
   P: "Lcl Scaling", "Lcl Scaling", "", "A", 2,3,1
  }
 }
 Material: 30, "Material::Red", "" {
  Version: 102
  ShadingModel: "lambert"
  Properties70: { P: "DiffuseColor", "Color", "", "A", 1,0.25,0.125 }
 }
}
Connections: {
 C: "OO", 20, 0
 C: "OO", 21, 20
 C: "OO", 10, 21
 C: "OO", 30, 21
}
'''

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/5n8AAAAASUVORK5CYII=')


def textured_ascii(filename='texture.png'):
    objects = f''' Texture: 40, "Texture::Color", "" {{
  Type: "TextureVideoClip"
  Version: 202
  TextureName: "Texture::Color"
  Media: "Video::Color"
  FileName: "{filename}"
  RelativeFilename: "{filename}"
  ModelUVTranslation: 0,0
  ModelUVScaling: 1,1
  Properties70: {{ P: "UVSet", "KString", "", "", "UVSet" }}
 }}
 Video: 41, "Video::Color", "Clip" {{
  Type: "Clip"
  Filename: "{filename}"
  RelativeFilename: "{filename}"
 }}
'''
    return ASCII.replace('\n}\nConnections:', '\n'+objects+'}\nConnections:').replace(' C: "OO", 30, 21',
                   ' C: "OO", 30, 21\n C: "OP", 40, 30, "DiffuseColor"\n C: "OO", 41, 40')


def binary_fbx():
    # Minimal authored FBX 7400 binary, including actual array properties.
    def prop(value):
        if isinstance(value, str):
            data = value.encode()
            return b'S' + struct.pack('<I', len(data)) + data
        if isinstance(value, int):
            return b'L' + struct.pack('<q', value)
        if isinstance(value, tuple):
            code, vals = value
            data = struct.pack('<'+('d' if code == 'd' else 'i')*len(vals), *vals)
            return code.encode()+struct.pack('<III', len(vals), 0, len(data))+data
        return b'D'+struct.pack('<d', value)
    def node(name, values=(), children=()):
        return name, values, children
    nodes = [node('FBXHeaderExtension', children=[node('FBXVersion', [7400])]),
             node('Objects', children=[
                 node('Geometry', [10, 'Quad\x00\x01Geometry', 'Mesh'], [
                     node('Vertices', [('d',[0,0,0, 1,0,0, 0,1,0])]),
                     node('PolygonVertexIndex', [('i',[0,1,-3])])]),
                 node('Model', [20, 'Triangle\x00\x01Model', 'Mesh'], [node('Version',[232])])]),
             node('Connections', children=[node('C',['OO',20,0]), node('C',['OO',10,20])])]
    output = bytearray(b'Kaydara FBX Binary  \x00\x1a\x00'+struct.pack('<I',7400))
    def encode(item):
        name, values, children = item
        name = name.encode()
        data = b''.join(prop(v) for v in values)
        start = len(output)
        output.extend(b'\x00'*13+name+data)
        for child in children:
            encode(child)
        if children:
            output.extend(b'\x00'*13)
        output[start:start+13] = struct.pack('<IIIB',len(output),len(values),len(data),len(name))
    for item in nodes:
        encode(item)
    output.extend(b'\x00'*13)
    return bytes(output)


class FbxTest(unittest.TestCase):
    def test_ascii_hierarchy_material_uv_units(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'quad.fbx'
            path.write_text(ASCII)
            scene = import_fbx(path, 4, 'Z', .5, {'Quad': {'code':'A'}})
        self.assertEqual(len(scene['objects']),1)
        obj = scene['objects'][0]
        self.assertEqual(obj['properties'], {'code':'A'})
        self.assertEqual(obj['sourceIndex'],4)
        self.assertEqual(len(obj['triangles']),2)
        points = {tuple(p) for t in obj['triangles'] for p in t['p']}
        self.assertEqual(points,{(5.5,11,16.5),(7.5,11,16.5),(7.5,12.5,16.5),(5.5,12.5,16.5)})
        uv = {tuple(p) for t in obj['triangles'] for p in t['uv']}
        self.assertEqual(uv,{(0,0),(1,0),(1,1),(0,1)})
        mat = scene['materials'][obj['triangles'][0]['material']]
        self.assertEqual(mat['color'],[1,.25,.125,1])

    def test_real_binary_and_reverse_axis(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'triangle.fbx'
            path.write_bytes(binary_fbx())
            scene = import_fbx(path, up_axis='Y', reverse_winding=True)
        t = scene['objects'][0]['triangles'][0]
        self.assertEqual({tuple(p) for p in t['p']},{(0,0,0),(1,0,0),(0,0,1)})
        self.assertEqual(t['n'],[[0,1,0]]*3)

    def test_reflection_has_consistent_winding(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'reflected.fbx'
            path.write_text(ASCII.replace('2,3,1','-2,3,1'))
            t = import_fbx(path)['objects'][0]['triangles'][0]
        p = np.asarray(t['p'])
        n = np.cross(p[1]-p[0],p[2]-p[0])
        self.assertGreater(float(np.dot(n,t['n'][0])),0)

    def test_cancel_and_bad_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'bad.fbx'
            path.write_bytes(b'not an fbx')
            with self.assertRaisesRegex(ValueError,'FBX 解析失败'):
                import_fbx(path)
            path.write_text(ASCII)
            def check():
                raise RuntimeError('cancel')
            with self.assertRaisesRegex(RuntimeError,'cancel'):
                import_fbx(path,check=check)

    def test_external_texture_and_containment(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'texture.fbx'
            path.write_text(textured_ascii())
            (Path(d)/'texture.png').write_bytes(PNG)
            result = import_fbx(path)
            material = result['materials'][result['objects'][0]['triangles'][0]['material']]
            self.assertEqual(base64.b64decode(material['image']['data']),PNG)
            path.write_text(textured_ascii('../outside.png'))
            with self.assertRaisesRegex(ValueError,'输入目录内'):
                import_fbx(path)

    def test_animation_rejected(self):
        curve = ''' AnimationCurve: 70, "AnimCurve::Move", "" {
  Default: 0
  KeyVer: 4008
  KeyTime: *2 { a: 0,46186158000 }
  KeyValueFloat: *2 { a: 0,1 }
  KeyAttrFlags: *1 { a: 24836 }
  KeyAttrDataFloat: *4 { a: 0,0,9.419963346924634e-30,0 }
  KeyAttrRefCount: *1 { a: 2 }
 }
'''
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'animated.fbx'
            path.write_text(ASCII.replace('\n}\nConnections:', '\n'+curve+'}\nConnections:'))
            with self.assertRaisesRegex(ValueError,'静态网格'):
                import_fbx(path)

    def test_embedded_texture(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'embedded.fbx'
            text = textured_ascii()
            # ufbx recognizes FBX ASCII video Content as base64 segments.
            marker = '  RelativeFilename: "texture.png"\n }\n}\nConnections:'
            text = text.replace(marker, '  RelativeFilename: "texture.png"\n  Content: , "'+base64.b64encode(PNG).decode()+'"\n }\n}\nConnections:')
            path.write_text(text)
            result = import_fbx(path)
            material = result['materials'][result['objects'][0]['triangles'][0]['material']]
            self.assertEqual(base64.b64decode(material['image']['data']),PNG)


if __name__ == '__main__':
    unittest.main()
