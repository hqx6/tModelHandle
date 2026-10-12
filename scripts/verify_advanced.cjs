// Run after starting the local service. NODE_PATH may point to bundled Playwright.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {spawnSync} = require('node:child_process');
let chromium;
try { ({chromium} = require('playwright')); } catch { ({chromium} = require('playwright-core')); }
const baseURL = process.env.MODEL_TILER_URL || 'http://127.0.0.1:8080';
const root = path.resolve(__dirname, '..');
const file = (name, data) => ({name, data: Buffer.from(data).toString('base64')});
const demo = ['demo.obj', 'demo.mtl', 'demo.csv'].map(name => file(name, fs.readFileSync(path.join(root, 'src/model_tiler/web', name))));

function repeated() {
  const lines = [];
  [[0,0,'instance-one'],[25,0,'instance-two'],[0,25,'instance-three'],[25,25,'instance-four']].forEach(([x,y,name], i) => {
    lines.push(`o ${name}`, `v ${x} ${y} 0`, `v ${x+8} ${y} 0`, `v ${x} ${y+8} 0`, `v ${x} ${y} 12`);
    const offset=i*4;
    for (const face of [[1,3,2], [1,2,4], [2,3,4], [3,1,4]]) lines.push('f '+face.map(v=>v+offset).join(' '));
  });
  return [file('instances.obj',lines.join('\n')),file('instances.csv','name,usage\ninstance-one,first\ninstance-two,second\ninstance-three,third\ninstance-four,fourth\n')];
}
function grid() {
  const lines=['o terrain']; const size=16;
  for(let y=0;y<=size;y++)for(let x=0;x<=size;x++)lines.push(`v ${x} ${y} 0`);
  for(let y=0;y<size;y++)for(let x=0;x<size;x++){
    const a=y*(size+1)+x+1,b=a+1,d=a+size+1,c=d+1;
    lines.push(`f ${a} ${b} ${c}`,`f ${a} ${c} ${d}`);
  }
  return [file('terrain.obj',lines.join('\n'))];
}
function fbx() {
  const result=spawnSync(path.join(root,'.venv/Scripts/python.exe'),['-c','import base64; from test.test_fbx import ASCII; print(base64.b64encode(ASCII.encode()).decode())'],{cwd:root,encoding:'utf8',env:{...process.env,PYTHONPATH:path.join(root,'src')}});
  assert.equal(result.status,0,result.stderr);
  return [{name:'authored.fbx',data:result.stdout.trim()}];
}
async function submit(request,files,options) {
  const response=await request.post(baseURL+'/api/tasks',{data:{files,options:{height:100,double_sided:true,unlit:true,...options}}});
  const created=await response.json(); assert(response.ok(),JSON.stringify(created));
  for(let i=0;i<300;i++){
    const task=await(await request.get(`${baseURL}/api/tasks/${created.id}`)).json();
    if(task.status==='completed')return task;
    assert(!['failed','cancelled','interrupted'].includes(task.status),JSON.stringify(task));
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  throw Error('Task timeout '+created.id);
}
function models(tile) {
  const magic=tile.subarray(0,4).toString();
  if(magic==='cmpt'){
    const found=[];let offset=16;
    for(let i=0;i<tile.readUInt32LE(12);i++){
      const length=tile.readUInt32LE(offset+8);found.push(...models(tile.subarray(offset,offset+length)));offset+=length;
    }
    return found;
  }
  assert(['b3dm','i3dm'].includes(magic),magic);
  let offset=magic==='b3dm'?28:32;
  for(let i=12;i<=24;i+=4)offset+=tile.readUInt32LE(i);
  const glb=tile.subarray(offset,offset+tile.readUInt32LE(offset+8));
  assert.equal(glb.subarray(0,4).toString(),'glTF');
  const doc=JSON.parse(glb.subarray(20,20+glb.readUInt32LE(12)).toString());
  return [{magic,doc}];
}
async function render(browser,task,label) {
  const page=await browser.newPage({viewport:{width:1200,height:900}}), errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`${baseURL}/cesium.html?task=${task.id}`,{waitUntil:'networkidle',timeout:60000});
  await page.waitForFunction(()=>window.tileset?.tilesLoaded&&window.tileset.statistics.numberOfCommands>0,null,{timeout:40000});
  const result=await page.evaluate(()=>{
    const triangles=window.tileset.statistics.numberOfTrianglesSelected;
    const commands=window.tileset.statistics.numberOfCommands;
    const features=new Map();
    function content(c){
      for(let i=0;c&&i<c.featuresLength;i++){
        const f=c.getFeature(i);features.set(f.getProperty('id'),Object.fromEntries(f.getPropertyIds().map(k=>[k,f.getProperty(k)])));
      }
      c?.innerContents?.forEach(content);
    }
    function visit(t){content(t.content);t.children.forEach(visit);}visit(window.tileset.root);
    const picked=new Map(),positions=new Map();
    for(let y=150;y<750;y+=12)for(let x=150;x<1050;x+=12){
      // A one-pixel ID pick must match the exact pixel used for depth picking.
      // Cesium's default 3x3 pick can select a silhouette neighbour over background depth.
      const f=window.viewer.scene.pick(new Cesium.Cartesian2(x,y),1,1);
      if(f instanceof Cesium.Cesium3DTileFeature){
        const id=f.getProperty('id');picked.set(id,f.getProperty('name'));
        if(!positions.has(id)&&window.viewer.scene.pickPositionSupported){
          const world=window.viewer.scene.pickPosition(new Cesium.Cartesian2(x,y));
          if(world){
            const local=Cesium.Matrix4.multiplyByPoint(Cesium.Matrix4.inverse(window.tileset.root.computedTransform,new Cesium.Matrix4()),world,new Cesium.Cartesian3());
            positions.set(id,{name:f.getProperty('name'),position:[local.x,local.y,local.z]});
          }
        }
      }
    }
    return {version:Cesium.VERSION,triangles,
      commands,features:[...features.values()],picked:[...picked.values()],pickedPositions:[...positions.values()]};
  });
  assert.deepEqual(errors,[]);assert(result.commands>0);assert(result.features.length>0);
  assert(result.picked.length>0,`No rendered feature picked: ${label}`);
  await page.screenshot({path:path.join(root,`verification/advanced-${label}.png`)});
  if(label==='simplify'){
    assert.equal(result.triangles,512,'Near camera must select original full-resolution leaves');
    await page.evaluate(async()=>{await window.viewer.zoomTo(window.tileset,new Cesium.HeadingPitchRange(.5,-.55,5000));});
    await page.waitForFunction(()=>window.tileset.tilesLoaded&&window.tileset.statistics.numberOfTrianglesSelected>0&&window.tileset.statistics.numberOfTrianglesSelected<512,null,{timeout:30000});
    result.farTriangles=await page.evaluate(()=>window.tileset.statistics.numberOfTrianglesSelected);
    assert(result.farTriangles<result.triangles,'Distant camera must select a real reduced LOD');
  }
  await page.close(); return result;
}
(async()=>{
  fs.mkdirSync(path.join(root,'verification'),{recursive:true});
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  const request=await browser.newContext();const results={};
  try {
    const cases=[
      ['draco',demo,{draco:true}],
      ['i3dm',repeated(),{draco:true,instance_min_count:2}],
      ['transformed',['instances.obj','instances.csv'].map(name=>file(name,fs.readFileSync(path.join(root,'examples/advanced',name)))),{draco:true,instance_min_count:2}],
      ['fbx',fbx(),{draco:true}],
      ['simplify',grid(),{draco:true,mode:'simplify',lod_ratio:.25,lod_levels:2}]
    ];
    for(const [label,files,options] of cases){
      const task=await submit(request.request,files,options);
      const encoded=[];
      for(const t of task.report.tiles){
        const response=await request.request.get(`${baseURL}/artifacts/${task.id}/output/${t.uri}`);
        assert(response.ok());encoded.push(...models(await response.body()));
      }
      assert(encoded.length>0);
      for(const {magic,doc} of encoded){
        assert(doc.extensionsRequired?.includes('KHR_draco_mesh_compression'));
        for(const primitive of doc.meshes.flatMap(m=>m.primitives)){
          assert(primitive.extensions.KHR_draco_mesh_compression);
          assert(!('bufferView' in doc.accessors[primitive.attributes.POSITION]));
          if(magic==='i3dm')assert(!('_BATCHID' in primitive.attributes));
        }
      }
      if(label==='i3dm'||label==='transformed')assert(encoded.some(m=>m.magic==='i3dm'));
      const visual=await render(browser,task,label);
      if(label==='i3dm'){
        assert.deepEqual(visual.features.map(f=>f.usage).sort(),['first','fourth','second','third']);
        assert.deepEqual(visual.picked.sort(),['instance-four','instance-one','instance-three','instance-two']);
        const offsets={'instance-one':[0,0],'instance-two':[25,0],'instance-three':[0,25],'instance-four':[25,25]};
        assert.equal(visual.pickedPositions.length,4,'Depth-based instance world positions required');
        for(const {name,position:[x,y,z]} of visual.pickedPositions){
          const [ox,oy]=offsets[name];
          assert(x>=ox-.25&&x<=ox+8.25&&y>=oy-.25&&y<=oy+8.25&&z>=-.25&&z<=12.25,
            `${name} rendered outside original ENU bounds: ${[x,y,z]}`);
        }
      }
      if(label==='simplify')assert(task.report.lod.some(level=>level.outputTriangles<level.inputTriangles),'Report must show real geometry reduction');
      if(label==='transformed'){
        assert.equal(task.report.instancedObjects,4);
        assert.equal(task.report.storedTriangles,4);
        assert.equal(visual.pickedPositions.length,4,'All rotated/scaled instances must have a depth pick');
        const tree=await(await request.request.get(`${baseURL}/artifacts/${task.id}/output/scenetree.json`)).json();
        const bounds=new Map(tree.scenes.flatMap(s=>s.children).map(o=>[o.name,o.boundingVolume.box]));
        for(const {name,position} of visual.pickedPositions){
          const box=bounds.get(name);assert(box);
          for(let a=0;a<3;a++)assert(Math.abs(position[a]-box[a])<=box[3+a*4]+.25,`${name} transform outside source bounds`);
        }
      }
      results[label]={task:task.id,report:task.report,encoded:encoded.map(m=>({magic:m.magic,extensions:m.doc.extensionsRequired})),...visual};
      fs.writeFileSync(path.join(root,'verification/advanced-result.json'),JSON.stringify(results,null,2));
      console.log(label,JSON.stringify({task:task.id,triangles:visual.triangles,picked:visual.picked,commands:visual.commands}));
    }
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
