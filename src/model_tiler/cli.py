import argparse
import json
from pathlib import Path

from .core import Options, dump, import_scene, tile_scene


def main():
    parser = argparse.ArgumentParser(description="通用模型切片：OBJ → 3D Tiles")
    sub = parser.add_subparsers(dest="command", required=True)
    convert = sub.add_parser("convert")
    convert.add_argument("inputs", nargs="*", type=Path)
    convert.add_argument("--output", required=True, type=Path)
    convert.add_argument("--longitude", type=float, default=116.39123)
    convert.add_argument("--latitude", type=float, default=39.90691)
    convert.add_argument("--height", type=float, default=0)
    convert.add_argument("--up-axis", choices=["Y", "Z"], default="Z")
    convert.add_argument("--scale", type=float, default=1)
    convert.add_argument("--properties", type=Path)
    convert.add_argument("--mode", choices=["small", "octree"], default="small")
    convert.add_argument("--max-triangles", type=int, default=50000)
    convert.add_argument("--max-depth", type=int, default=8)
    convert.add_argument("--visible-pixels", type=float, default=25)
    convert.add_argument("--double-sided", action="store_true")
    convert.add_argument("--resource", type=Path, help="归一化资源缓存 JSON（不覆盖已有文件）")
    convert.add_argument("--skip-import", action="store_true")
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--workspace", type=Path, default=Path("workspace"))
    args = parser.parse_args()
    if args.command == "serve":
        from .server import serve as run
        run(args.workspace, args.port)
        return
    try:
        if args.skip_import:
            if not args.resource:
                raise ValueError("跳过导入需要 --resource")
            scene = json.loads(args.resource.read_text(encoding="utf-8"))
            if scene.get("version") != 1:
                raise ValueError("不支持的资源库版本")
        else:
            if not args.inputs:
                raise ValueError("请指定输入 OBJ 文件")
            if args.resource and args.resource.exists():
                raise FileExistsError("资源缓存已存在，请用 --skip-import 或选择新路径")
            scene = import_scene(args.inputs,args.up_axis,args.scale,args.properties)
            if args.resource:
                args.resource.parent.mkdir(parents=True,exist_ok=True)
                dump(args.resource,scene)
        options = Options(args.longitude,args.latitude,args.height,args.mode,args.max_triangles,args.max_depth,args.visible_pixels,args.double_sided)
        report = tile_scene(scene,args.output,options,lambda p,m: print(f"{p:3}% {m}"))
        print(f"{report['objects']} 个构件 / {report['triangles']} 个三角形 / {len(report['tiles'])} 个切片")
        print(args.output.resolve()/"tileset.json")
    except (ValueError,OSError,KeyError) as error:
        parser.exit(1,f"错误: {error}\n")


if __name__ == "__main__":
    main()
