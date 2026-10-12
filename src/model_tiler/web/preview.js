// Lightweight local inspection of the imported mesh; no globe or external services.
export class Preview {
  constructor(canvas){
    this.canvas=canvas;this.gl=canvas.getContext('webgl');this.yaw=.65;this.pitch=.65;this.zoom=1;
    if(!this.gl){canvas.insertAdjacentText('afterend','浏览器不支持 WebGL，请使用输出文件检查模型。');return;}
    const gl=this.gl;
    const shader=(type,source)=>{const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw new Error(gl.getShaderInfoLog(s));return s;};
    this.program=gl.createProgram();
    gl.attachShader(this.program,shader(gl.VERTEX_SHADER,`attribute vec3 p; attribute vec3 color; uniform float yaw,pitch,zoom,aspect; varying vec3 c;
      void main(){float a=cos(yaw),b=sin(yaw),s=sin(pitch),t=cos(pitch);vec3 q=vec3(a*p.x-b*p.y,b*p.x+a*p.y,p.z);float dep=t*q.y+s*q.z;
      gl_Position=vec4(q.x*zoom/aspect,(t*q.z-s*q.y)*zoom,-dep*.2,1.);c=color;}`));
    gl.attachShader(this.program,shader(gl.FRAGMENT_SHADER,`precision mediump float; varying vec3 c; void main(){gl_FragColor=vec4(c,1.);}`));
    gl.linkProgram(this.program);if(!gl.getProgramParameter(this.program,gl.LINK_STATUS))throw new Error(gl.getProgramInfoLog(this.program));
    this.buffer=gl.createBuffer();this.count=0;
    let drag=null;
    canvas.onpointerdown=e=>{drag=[e.clientX,e.clientY];canvas.setPointerCapture(e.pointerId);};
    canvas.onpointermove=e=>{if(drag){this.yaw+=(e.clientX-drag[0])*.008;this.pitch=Math.max(-1.5,Math.min(1.5,this.pitch+(e.clientY-drag[1])*.008));drag=[e.clientX,e.clientY];this.render();}};
    canvas.onpointerup=canvas.onpointercancel=()=>drag=null;
    canvas.onwheel=e=>{e.preventDefault();this.zoom=Math.max(.1,Math.min(8,this.zoom*Math.exp(-e.deltaY*.001)));this.render();};
    canvas.ondblclick=()=>{this.yaw=.65;this.pitch=.65;this.zoom=1;this.render();};
    new ResizeObserver(()=>this.render()).observe(canvas);
  }
  load(data){this.data=data;this.zoom=1;this.select(null);}
  select(id){
    if(!this.gl||!this.data)return;
    const {bounds,triangles,objectIds,colors}=this.data;
    const center=bounds[0].map((v,i)=>(v+bounds[1][i])/2);
    const scale=1.2/Math.max(...bounds[0].map((v,i)=>bounds[1][i]-v),.001);
    const values=[];
    triangles.forEach((tri,i)=>{
      const u=tri[1].map((v,j)=>v-tri[0][j]),v=tri[2].map((x,j)=>x-tri[0][j]);
      const n=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]];
      const l=Math.hypot(...n)||1, shade=this.data.unlit?1:.4+.6*Math.abs((n[0]*.2+n[1]*.4+n[2]*.8)/l);
      const col=objectIds[i]===id?[.3,.95,.8]:colors[i].slice(0,3).map(x=>x*(id?.35:1)*shade);
      tri.forEach(p=>values.push(...p.map((v,j)=>(v-center[j])*scale),...col));
    });
    this.gl.bindBuffer(this.gl.ARRAY_BUFFER,this.buffer);this.gl.bufferData(this.gl.ARRAY_BUFFER,new Float32Array(values),this.gl.STATIC_DRAW);this.count=values.length/6;this.render();
  }
  render(){
    if(!this.gl)return;const gl=this.gl,c=this.canvas;
    c.width=c.clientWidth*devicePixelRatio;c.height=c.clientHeight*devicePixelRatio;gl.viewport(0,0,c.width,c.height);
    gl.clearColor(.07,.105,.15,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.enable(gl.DEPTH_TEST);gl.useProgram(this.program);
    for(const [name,value]of Object.entries({yaw:this.yaw,pitch:this.pitch,zoom:this.zoom,aspect:c.width/c.height}))gl.uniform1f(gl.getUniformLocation(this.program,name),value);
    gl.bindBuffer(gl.ARRAY_BUFFER,this.buffer);
    for(const [name,offset]of [['p',0],['color',12]]){const a=gl.getAttribLocation(this.program,name);gl.enableVertexAttribArray(a);gl.vertexAttribPointer(a,3,gl.FLOAT,false,24,offset);}
    gl.drawArrays(gl.TRIANGLES,0,this.count);
  }
}
