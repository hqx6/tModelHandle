"""Loopback-only task service, bounded uploads, serial background jobs."""
import base64
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path, PurePosixPath
import threading
from urllib.parse import urlparse
import uuid

from .core import Cancelled, Options, dump, import_scene, tile_scene


class TaskStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True,exist_ok=True)
        self.tasks, self.events = {}, {}
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=1)
        for file in self.root.glob("*/task.json"):
            task = json.loads(file.read_text())
            if task["status"] in ("queued","running"):
                task.update(status="interrupted",message="服务重启中断了任务；请重新提交",progress=0)
                dump(file,task)
            self.tasks[task["id"]] = task

    def update(self, id, **values):
        with self.lock:
            self.tasks[id].update(values)
            # Atomic state updates avoid half-written task records on restart.
            temporary = self.root/id/"task.tmp"
            dump(temporary,self.tasks[id])
            temporary.replace(self.root/id/"task.json")

    def create(self, request):
        if not isinstance(request,dict):
            raise ValueError("任务请求必须是 JSON 对象")
        options = Options(**request.get("options",{}))
        options.validate()
        inputs = request.get("files",[])
        if not isinstance(inputs,list) or not inputs or len(inputs)>256:
            raise ValueError("请选择模型及依赖文件（最多 256 个文件）")
        decoded, names = [], set()
        for file in inputs:
            if not isinstance(file,dict):
                raise ValueError("文件描述必须是对象")
            name = file["name"].replace("\\","/")
            p = PurePosixPath(name)
            if p.is_absolute() or any(x in ("..", "") for x in p.parts) or not p.parts or ":" in name or name in names:
                raise ValueError("文件路径无效或重复")
            if p.suffix.lower() not in (".obj",".mtl",".png",".jpg",".jpeg",".csv"):
                raise ValueError("上传仅支持 OBJ/MTL/PNG/JPEG/CSV")
            names.add(name)
            decoded.append((name,base64.b64decode(file["data"],validate=True)))
        if not any(name.lower().endswith(".obj") for name,_ in decoded):
            raise ValueError("至少选择一个 OBJ 文件")
        csvs = [name for name,_ in decoded if name.lower().endswith(".csv")]
        if len(csvs)>1:
            raise ValueError("每个任务最多一个 CSV 属性表")
        up_axis, scale = request.get("upAxis","Z"), float(request.get("scale",1))
        if up_axis not in ("Y","Z") or not 0 < scale < 1e9:
            raise ValueError("模型单位比例或上轴无效")
        id = uuid.uuid4().hex
        folder = self.root/id
        source = folder/"input"
        source.mkdir(parents=True)
        for name,data in decoded:
            target = source/name
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(data)
        event = threading.Event()
        task = {"id":id,"status":"queued","progress":0,"message":"等待处理","options":vars(options)}
        with self.lock:
            self.tasks[id],self.events[id] = task,event
        self.update(id)
        def run():
            def check():
                if event.is_set():
                    raise Cancelled("任务已取消")
            try:
                check()
                self.update(id,status="running",progress=5,message="正在导入模型")
                scene = import_scene([source/name for name,_ in decoded if name.lower().endswith(".obj")],up_axis,scale,
                                     source/csvs[0] if csvs else None,check)
                dump(folder/"resource.json",scene)
                report = tile_scene(scene,folder/"output",options,lambda p,m:self.update(id,progress=p,message=m),event.is_set)
                import zipfile
                with zipfile.ZipFile(folder/"output.zip","w",zipfile.ZIP_DEFLATED) as archive:
                    for file in (folder/"output").rglob("*"):
                        if file.is_file() and file.name != "preview.json":
                            archive.write(file,file.relative_to(folder/"output"))
                self.update(id,status="completed",progress=100,message="切片完成",report=report,tileset=f"/artifacts/{id}/output/tileset.json")
            except Cancelled:
                self.update(id,status="cancelled",message="任务已取消")
            except Exception as error:
                self.update(id,status="failed",message=str(error))
        self.pool.submit(run)
        return dict(task)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,store,**kwargs):
        self.store = store
        super().__init__(*args,directory=str(Path(__file__).parent/"web"),**kwargs)

    def response(self,status,value):
        data = json.dumps(value,ensure_ascii=False,allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/tasks":
            with self.store.lock:
                tasks = [dict(t) for t in self.store.tasks.values()]
            self.response(200,tasks)
        elif path.startswith("/api/tasks/"):
            id = path.rsplit("/",1)[-1]
            with self.store.lock:
                task = dict(self.store.tasks[id]) if id in self.store.tasks else None
            self.response(200 if task else 404,task or {"error":"任务不存在"})
        elif path.startswith("/artifacts/"):
            from urllib.parse import unquote
            target = (self.store.root/unquote(path[len("/artifacts/"):])).resolve()
            if not target.is_relative_to(self.store.root) or not target.is_file():
                self.response(404,{"error":"文件不存在"})
                return
            self.send_response(200)
            self.send_header("Content-Type",mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            self.send_header("Content-Length",str(target.stat().st_size))
            self.end_headers()
            with target.open("rb") as f:
                import shutil
                shutil.copyfileobj(f,self.wfile)
        else:
            super().do_GET()

    def do_POST(self):
        # Prevent cross-origin task creation from unrelated pages on the user's browser.
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{self.headers.get('Host')}":
            self.response(403,{"error":"不允许跨来源请求"})
            return
        try:
            path = urlparse(self.path).path
            if path == "/api/tasks":
                length = int(self.headers.get("Content-Length","0"))
                if not 0 < length <= 64*1024*1024:
                    self.response(413,{"error":"单次上传需小于 64 MB（含 Base64 编码）；更大模型请使用 CLI"})
                    return
                request = json.loads(self.rfile.read(length))
                self.response(202,self.store.create(request))
            elif path.startswith("/api/tasks/") and path.endswith("/cancel"):
                id = path.split("/")[3]
                with self.store.lock:
                    task = self.store.tasks.get(id)
                    if not task:
                        self.response(404,{"error":"任务不存在"})
                        return
                    if task["status"] not in ("queued","running"):
                        self.response(409,{"error":"任务已结束"})
                        return
                    self.store.events[id].set()
                self.response(202,{"id":id,"message":"已请求取消"})
            else:
                self.response(404,{"error":"接口不存在"})
        except (ValueError,KeyError,TypeError) as error:
            self.response(400,{"error":str(error)})


def serve(root,port):
    store = TaskStore(root)
    server = ThreadingHTTPServer(("127.0.0.1",port),partial(Handler,store=store))
    print(f"通用模型切片: http://127.0.0.1:{port}",flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        for event in store.events.values():
            event.set()
        store.pool.shutdown(wait=True,cancel_futures=True)
