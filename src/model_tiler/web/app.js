import {Preview} from './preview.js';
const $ = id => document.getElementById(id);
const preview = new Preview($('canvas'));
let selectedFiles = [], currentTask = null, timer, loadedTask;
function filesChanged(files,folder=false){
  $('resource-task').value='';
  selectedFiles = [...files].filter(f=>/\.(obj|fbx|mtl|png|jpe?g|csv)$/i.test(f.name));
  const root = folder ? selectedFiles[0]?.webkitRelativePath.split('/')[0]+'/' : '';
  selectedFiles = selectedFiles.map(file=>({file,name:folder?file.webkitRelativePath.slice(root.length):file.name}));
  $('file-list').replaceChildren(...selectedFiles.map(({file,name})=>{
    const div=document.createElement('div');div.textContent=`${name} · ${(file.size/1024).toFixed(1)} KB`;return div;
  }));
  if(!selectedFiles.length)$('file-list').textContent='尚未选择文件';
}
$('files').onchange=e=>filesChanged(e.target.files);
$('folder').onchange=e=>filesChanged(e.target.files,true);
document.querySelectorAll('[name=mode]').forEach(r=>r.onchange=()=>{
  $('pixel-label').hidden=r.value!=='octree';$('lod-options').hidden=r.value!=='simplify';
});
$('draco').onchange=()=>{$('draco-options').hidden=!$('draco').checked;};
$('alpha-mode').onchange=()=>{$('alpha-cutoff-label').hidden=$('alpha-mode').value!=='MASK';};
$('property-mode').onchange=()=>{$('property-fields-label').hidden=$('property-mode').value!=='selected';};
async function request(url,options){
  const response=await fetch(url,options), body=await response.json();
  if(!response.ok)throw new Error(body.error||'请求失败');return body;
}
const base64 = file => new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(new Error('文件读取失败'));reader.readAsDataURL(file);});
$('form').onsubmit=async e=>{
  e.preventDefault();$('error').textContent='';
  try{
    const resourceTask=$('resource-task').value;
    if(!resourceTask&&!selectedFiles.some(f=>/\.(obj|fbx)$/i.test(f.name)))throw new Error('请选择 OBJ 或 FBX 模型，并一并选择依赖文件。');
    if(!resourceTask&&selectedFiles.reduce((sum,f)=>sum+f.file.size,0)>47*1024*1024)throw new Error('页面上传上限约 47 MB 原始数据，更大模型请使用 CLI。');
    $('submit').disabled=true;
    const options={longitude:+$('longitude').value,latitude:+$('latitude').value,height:+$('height').value,
      mode:document.querySelector('[name=mode]:checked').value,max_triangles:+$('max-triangles').value,
      max_depth:+$('max-depth').value,visible_pixels:+$('visible-pixels').value,double_sided:$('double-sided').checked,
      unlit:$('unlit').checked,alpha_mode:$('alpha-mode').value,alpha_cutoff:+$('alpha-cutoff').value,origin:$('origin').value,
      draco:$('draco').checked,draco_level:+$('draco-level').value,draco_bits:+$('draco-bits').value,
      instance_min_count:+$('instance-min-count').value,lod_ratio:+$('lod-ratio').value,lod_levels:+$('lod-levels').value,
      max_tile_bytes:Math.round(+$('max-tile-mb').value*1000000),property_fields:$('property-mode').value==='all'?null:
        $('property-mode').value==='none'?[]:$('property-fields').value.split(/[,，]/).map(s=>s.trim()).filter(Boolean)};
    const files=resourceTask?[]:await Promise.all(selectedFiles.map(async ({file,name})=>({name,data:await base64(file)})));
    const payload={files,options,upAxis:$('up-axis').value,scale:+$('scale').value,encoding:$('encoding').value,reverseWinding:$('reverse-winding').checked};
    if(resourceTask)payload.resourceTask=resourceTask;
    const task=await request('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    loadedTask=null;follow(task.id);
  }catch(error){$('error').textContent=error.message;$('submit').disabled=false;}
};
function follow(id){clearTimeout(timer);currentTask=id;$('task').hidden=false;poll();}
async function poll(){
  try{
    const task=await request(`/api/tasks/${currentTask}`);
    const active=['queued','running'].includes(task.status);
    $('task-title').textContent={queued:'等待处理',running:'正在切片',completed:'切片完成',failed:'处理失败',cancelled:'已取消',interrupted:'已中断'}[task.status];
    $('percent').textContent=`${task.progress}%`;$('progress').value=task.progress;$('task-message').textContent=task.message;
    $('cancel').hidden=!active;$('submit').disabled=active;
    if(task.status==='completed'&&loadedTask!==task.id){await loadOutput(task);loadedTask=task.id;}
    if(active)timer=setTimeout(poll,700);else await history();
  }catch(error){$('error').textContent=error.message;$('submit').disabled=false;}
}
$('cancel').onclick=async()=>{try{await request(`/api/tasks/${currentTask}/cancel`,{method:'POST'});$('task-message').textContent='已请求取消';}catch(e){$('error').textContent=e.message;}};
async function loadOutput(task){
  const root=`/artifacts/${task.id}/output/`;
  const [geometry,tree]=await Promise.all([request(root+'preview.json'),request(root+'scenetree.json')]);
  preview.load(geometry);$('empty').hidden=true;$('viewport-label').hidden=false;
  $('preview-mode').textContent=geometry.sampled?'本地几何预览 · 已采样':'本地几何预览';
  $('tree').replaceChildren();$('properties').textContent='';
  for(const scene of tree.scenes){
    const label=document.createElement('small');label.textContent=scene.name;$('tree').append(label);
    for(const object of scene.children){
      const b=document.createElement('button');b.textContent='◇ '+object.name;
      b.onclick=()=>{preview.select(object.id);$('tree').querySelectorAll('button').forEach(x=>x.classList.toggle('active',x===b));$('properties').textContent=JSON.stringify({id:object.id,...object.properties},null,2);};
      $('tree').append(b);
    }
  }
  $('summary').replaceChildren(...[['构件',task.report.objects],['三角形',task.report.triangles],['切片',task.report.tiles.length]].map(([label,value])=>{const d=document.createElement('div');d.className='metric';const strong=document.createElement('strong');strong.textContent=value;const small=document.createElement('small');small.textContent=label;d.append(strong,small);return d;}));
  $('downloads').replaceChildren(...['tileset.json','scenetree.json','report.json'].map(name=>{const a=document.createElement('a');a.href=root+name;a.download=name;a.textContent='↓ '+name;return a;}));
  const globe=document.createElement('a');globe.href=`/cesium.html?task=${task.id}`;globe.target='_blank';globe.textContent='↗ Cesium 地球预览';$('downloads').append(globe);
  const zip=document.createElement('a');zip.href=`/artifacts/${task.id}/output.zip`;zip.download='model-tiles.zip';zip.textContent='↓ 完整成果 ZIP';$('downloads').prepend(zip);
  const resource=document.createElement('a');resource.href=`/artifacts/${task.id}/resource.json`;resource.download='resource.json';resource.textContent='↓ 资源库缓存';$('downloads').append(resource);
  if(task.report.warnings?.length){const note=document.createElement('p');note.className='note';note.textContent=task.report.warnings.join('；');$('downloads').append(note);}
  const code=document.createElement('pre');code.textContent=`Cesium.Cesium3DTileset.fromUrl(\n  location.origin + '${task.tileset}'\n)`;$('downloads').append(code);
}
async function history(){
  const tasks=await request('/api/tasks');
  const selected=$('resource-task').value;
  const initial=document.createElement('option');initial.value='';initial.textContent='导入当前文件';
  $('resource-task').replaceChildren(initial,...tasks.filter(t=>t.resourceAvailable||t.status==='completed').map(t=>{const o=document.createElement('option');o.value=t.id;o.textContent=`${t.id.slice(0,8)} · 已导入资源`;return o;}));
  $('resource-task').value=selected;
  $('history').replaceChildren(...tasks.reverse().slice(0,10).map(t=>{const b=document.createElement('button');b.textContent=`${t.id.slice(0,8)} · ${t.status}`;b.onclick=()=>follow(t.id);return b;}));
}
$('demo').onclick=async()=>{
  const obj=await (await fetch('demo.obj')).text(),mtl=await (await fetch('demo.mtl')).text(),csv=await(await fetch('demo.csv')).text();
  filesChanged([new File([obj],'demo.obj'),new File([mtl],'demo.mtl'),new File([csv],'demo.csv')]);
  $('form').requestSubmit();
};
history().catch(e=>{$('error').textContent=e.message;});
