"""Verified rigid/uniform-scale instances and 3D Tiles 1.0 I3DM/CMPT payloads."""
from __future__ import annotations

import hashlib
import json
import math
import struct


def _dot(a,b):
    return sum(x*y for x,y in zip(a,b))


def _cross(a,b):
    return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]


def _unit(v):
    length=math.hypot(*v)
    return [x/length for x in v] if length>1e-15 else None


def _canonical(obj,materials):
    """A proper orthonormal frame; verify candidates against original world vertices."""
    first=obj['triangles'][0]['p']
    origin=first[0]
    edge=[first[1][a]-origin[a] for a in range(3)]
    scale=math.hypot(*edge)
    right=_unit(edge)
    forward=_unit(_cross(edge,[first[2][a]-origin[a] for a in range(3)]))
    if right is None or forward is None:
        return None
    up=_cross(forward,right)
    axes=[right,up,forward]
    local=[]
    for t in obj['triangles']:
        p=[[_dot([v[a]-origin[a] for a in range(3)],axis)/scale for axis in axes] for v in t['p']]
        n=[[_dot(v,axis) for axis in axes] for v in t['n']]
        local.append(dict(t,p=p,n=n))
    def rounded(v):
        value=round(v,8)
        return 0.0 if value==0 else value
    signature=[{'p':[[rounded(v) for v in p] for p in t['p']],
                'n':[[rounded(v) for v in n] for n in t['n']],
                'uv':t['uv'],'material':materials[t['material']]} for t in local]
    key=hashlib.sha256(json.dumps(signature,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    return {'key':key,'triangles':local,'position':origin,'right':right,'up':up,'scale':scale}


def _matches(template,instance,obj,materials):
    axes=[instance['right'],instance['up'],_cross(instance['right'],instance['up'])]
    for a,b in zip(template['triangles'],obj['triangles']):
        if a['uv'] != b['uv'] or materials[a['material']] != materials[b['material']]:
            return False
        for p,q in zip(a['p'],b['p']):
            rebuilt=[instance['position'][i]+instance['scale']*sum(p[j]*axes[j][i] for j in range(3)) for i in range(3)]
            if math.dist(rebuilt,q)>1e-6:
                return False
        for n,q in zip(a['n'],b['n']):
            rebuilt=[sum(n[j]*axes[j][i] for j in range(3)) for i in range(3)]
            if math.dist(rebuilt,q)>1e-6:
                return False
    return True


def group_instances(faces,scene,minimum,check=lambda:None):
    """Only whole objects with matching ordered geometry/material/UV/normal data."""
    if minimum==0:
        return [],faces
    by_object={}
    for i,t in faces:
        by_object.setdefault(i,[]).append(t)
    candidates={}
    for i,triangles in by_object.items():
        check()
        if len(triangles)!=len(scene['objects'][i]['triangles']):
            continue
        candidate=_canonical(dict(scene['objects'][i],triangles=triangles),scene['materials'])
        if candidate is None:
            continue
        groups=candidates.setdefault(candidate['key'],[])
        for group in groups:
            if _matches(group[0][1],candidate,dict(scene['objects'][i],triangles=triangles),scene['materials']):
                group.append((i,candidate));break
        else:
            groups.append([(i,candidate)])
    selected=[g for buckets in candidates.values() for g in buckets if len(g)>=minimum]
    ids={i for group in selected for i,_ in group}
    return selected,[(i,t) for i,t in faces if i not in ids]


def _json_bytes(value,offset=0):
    data=json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
    return data+b' '*((-(offset+len(data)))%8)


def i3dm(group,scene,options,make_glb):
    first=scene['objects'][group[0][0]]
    prototype=dict(first,triangles=group[0][1]['triangles'])
    template=dict(scene,objects=[prototype])
    model,_,_=make_glb([(0,t) for t in prototype['triangles']],template,include_batch_ids=False)
    positions=[candidate['position'] for _,candidate in group]
    center=[(min(p[a] for p in positions)+max(p[a] for p in positions))/2 for a in range(3)]
    binary=bytearray()
    ft={'INSTANCES_LENGTH':len(group),'RTC_CENTER':center}
    def attribute(name,values,width,integer=False):
        ft[name]={'byteOffset':len(binary)}
        if integer: ft[name]['componentType']='UNSIGNED_INT'
        flat=[v for row in values for v in (row if width>1 else [row])]
        binary.extend(struct.pack('<'+str(len(flat))+('I' if integer else 'f'),*flat))
    attribute('POSITION',[[p[a]-center[a] for a in range(3)] for p in positions],3)
    attribute('NORMAL_RIGHT',[c['right'] for _,c in group],3)
    attribute('NORMAL_UP',[c['up'] for _,c in group],3)
    attribute('SCALE',[c['scale'] for _,c in group],1)
    attribute('BATCH_ID',list(range(len(group))),1,True)
    binary.extend(b'\0'*((-len(binary))%8))
    properties=[dict({k:v for k,v in scene['objects'][i]['properties'].items()
                     if options.property_fields is None or k in options.property_fields},
                    id=scene['objects'][i]['id'],name=scene['objects'][i]['name']) for i,_ in group]
    bt={key:[p.get(key) for p in properties] for key in sorted({key for p in properties for key in p})}
    ftj=_json_bytes(ft,32)
    btj=_json_bytes(bt)
    body=ftj+binary+btj+model
    body+=b'\0'*((-(32+len(body)))%8)
    return struct.pack('<4s7I',b'i3dm',1,32+len(body),len(ftj),len(binary),len(btj),0,1)+body


def cmpt(tiles):
    body=b''.join(tiles)
    return struct.pack('<4s3I',b'cmpt',1,16+len(body),len(tiles))+body
