import argparse
import json
from pathlib import Path

from .core import Options, dump, import_scene, tile_scene


def main():
    parser = argparse.ArgumentParser(description="通用模型切片：OBJ / FBX → 3D Tiles")
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
    convert.add_argument("--mode", choices=["small", "octree", "simplify"], default="small")
    convert.add_argument("--draco", action="store_true", help="Draco 网格压缩（需要 advanced 依赖）")
    convert.add_argument("--draco-level", type=int, default=7)
    convert.add_argument("--draco-bits", type=int, default=14)
    convert.add_argument("--instance-min-count", type=int, default=0, help="重复构件达到此数量时使用 I3DM；0 为关闭")
    convert.add_argument("--lod-ratio", type=float, default=0.5, help="每级粗糙层的三角形保留比例")
    convert.add_argument("--lod-levels", type=int, default=2)
    convert.add_argument("--max-triangles", type=int, default=50000)
    convert.add_argument("--max-depth", type=int, default=8)
    convert.add_argument("--visible-pixels", type=float, default=25)
    convert.add_argument("--double-sided", action="store_true")
    convert.add_argument("--unlit", action="store_true", help="烘焙模型使用无光照材质")
    convert.add_argument("--alpha-mode", choices=["auto","OPAQUE","MASK","BLEND"], default="auto")
    convert.add_argument("--alpha-cutoff", type=float, default=0.5)
    convert.add_argument("--encoding", choices=["utf-8-sig","gbk"], default="utf-8-sig")
    convert.add_argument("--reverse-winding", action="store_true")
    convert.add_argument("--origin", choices=["original","center"], default="original")
    convert.add_argument("--property-fields", nargs="*", default=None, help="只输出指定 CSV 字段；不传字段则只保留 id/name")
    convert.add_argument("--max-tile-bytes", type=int, default=0, help="实际 B3DM 字节上限，0 为不限制")
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
        options = Options(longitude=args.longitude,latitude=args.latitude,height=args.height,mode=args.mode,
                          max_triangles=args.max_triangles,max_depth=args.max_depth,visible_pixels=args.visible_pixels,
                          double_sided=args.double_sided,unlit=args.unlit,alpha_mode=args.alpha_mode,alpha_cutoff=args.alpha_cutoff,
                          origin=args.origin,property_fields=args.property_fields,max_tile_bytes=args.max_tile_bytes,
                          draco=args.draco,draco_level=args.draco_level,draco_bits=args.draco_bits,
                          instance_min_count=args.instance_min_count,lod_ratio=args.lod_ratio,lod_levels=args.lod_levels)
        options.validate()
        if args.skip_import:
            if not args.resource:
                raise ValueError("跳过导入需要 --resource")
            scene = json.loads(args.resource.read_text(encoding="utf-8"))
            if scene.get("version") != 1:
                raise ValueError("不支持的资源库版本")
        else:
            if not args.inputs:
                raise ValueError("请指定输入 OBJ 或 FBX 文件")
            if args.resource and args.resource.exists():
                raise FileExistsError("资源缓存已存在，请用 --skip-import 或选择新路径")
            scene = import_scene(args.inputs,args.up_axis,args.scale,args.properties,
                                 encoding=args.encoding,reverse_winding=args.reverse_winding)
            if args.resource:
                args.resource.parent.mkdir(parents=True,exist_ok=True)
                dump(args.resource,scene)
        report = tile_scene(scene,args.output,options,lambda p,m: print(f"{p:3}% {m}"))
        print(f"{report['objects']} 个构件 / {report['triangles']} 个三角形 / {len(report['tiles'])} 个切片")
        print(args.output.resolve()/"tileset.json")
    except (ValueError,OSError,KeyError) as error:
        parser.exit(1,f"错误: {error}\n")


if __name__ == "__main__":
    main()
