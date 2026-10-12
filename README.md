# 通用模型切片

将 OBJ / 静态 FBX 模型转换为带地理定位和构件属性的 3D Tiles。处理流程是 **导入 → 归一化资源库 → 分块 / 尺寸过滤 / 几何 LOD → 实例优化与压缩 → 地理定位 → 3D Tiles + 构件结构树**。

## 启动

Python 3.10+。基础 OBJ 转换与本地页面无需第三方 Python 依赖；FBX、Draco 和几何化简需要 `advanced` 依赖。本机已配置 Python 3.12 项目环境：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[advanced]"
.\.venv\Scripts\python.exe -m model_tiler.cli serve --port 8080
```

其他机器可用 Python 3.10+ 建立虚拟环境，安装 `.[advanced]` 后启动。仅使用基础 OBJ 功能时：

```sh
PYTHONPATH=src python3 -m model_tiler.cli serve --port 8080
```

打开 <http://127.0.0.1:8080>，点击“加载演示模型”，或选择自己的 OBJ / FBX、MTL、纹理与 CSV。有纹理子目录时使用“选择完整文件夹”。页面提供进度、取消、历史任务、几何旋转、构件选中、属性与结果链接。

本地几何预览离线可用；“Cesium 地球预览”使用固定版本 Cesium 1.123.1 的公共 CDN，需要联网，无需 ion token。地球预览实际读取 B3DM / I3DM / CMPT（含 Draco），并可点击构件查看属性。

## 命令行转换

```sh
PYTHONPATH=src python3 -m model_tiler.cli convert \
  src/model_tiler/web/demo.obj \
  --properties src/model_tiler/web/demo.csv \
  --longitude 116.39123 --latitude 39.90691 --height 0 \
  --up-axis Z --scale 1 \
  --mode small --max-triangles 50000 \
  --resource workspace/model-resource.json \
  --output workspace/my-tiles
```

多个 OBJ / FBX 可以混合写在 `convert` 后。默认坐标单位为米，Z 向上；Y 向上模型使用 `--up-axis Y`。FBX 会应用层级和几何变换，上轴与单位由用户显式指定，例如厘米单位用 `--scale 0.01`。输入原点放到指定经纬度/椭球高度。当前仅支持本地 ENU 空间参考，不转换 EPSG 投影坐标。

### 新增模型设置

页面与 CLI 均可设置以下参数：

| 功能 | CLI 参数 | 行为 |
| --- | --- | --- |
| GBK 编码 | `--encoding gbk` | OBJ 与 MTL 使用 GBK；CSV 仍使用 UTF-8 |
| 翻转绕向 | `--reverse-winding` | 交换顶点、UV 顺序，同时翻转已有法线或重新生成面法线 |
| 无光照 | `--unlit` | GLB 写入 `KHR_materials_unlit`，适合烘焙模型 |
| 透明模式 | `--alpha-mode MASK --alpha-cutoff 0.5` | 支持 auto / OPAQUE / MASK / BLEND，auto 依据 MTL 透明度 |
| 输出原点 | `--origin center` | 几何以包围盒中心为原点，并补偿根 transform，世界位置保持一致 |
| 属性筛选 | `--property-fields 用途 楼层` | B3DM 与结构树只输出指定字段；ID/名称始终保留。省略参数输出全部，空列表仅输出 ID/名称 |
| 单片字节预算 | `--max-tile-bytes 20000000` | 按实际 B3DM / I3DM / CMPT（含纹理与属性）大小继续分块；0 表示不限制 |
| Draco | `--draco --draco-level 7 --draco-bits 14` | 实际压缩 GLB 网格，位置有量化误差，构件 ID / UV / 法线无损保留 |
| I3DM 实例 | `--instance-min-count 50` | 同片重复完整构件达到阈值时共享几何；0 关闭，最小 2 |
| 几何 LOD | `--mode simplify --lod-ratio 0.5 --lod-levels 2` | QEM 边折叠生成粗糙层，使用 REPLACE，最高精度层保留原网格 |

字节预算与三角形上限同时生效。达到最大深度或只剩一个三角形时保留完整几何，并在 `oversizedByteTiles` 和 `warnings` 中记录超限。纹理在各切片重复嵌入，减小阈值可能增加总存储量；当前没有纹理图集。`origin` 默认仍为 `original`，保持已有项目行为。

页面“复用资源库”可选择历史已导入任务，无需重新上传文件。复用时采用缓存内的编码、上轴、比例和绕向处理结果，材质、原点、属性与分块参数可重新设置。成果区可下载 `resource.json`，用于 CLI 离线重复切片。被筛掉的属性仍保留在资源缓存中，后续可以恢复输出。

新增功能的安装包证据、对应关系和未完成范围见 [逆向进度](docs/reverse-engineering.md)。这些是独立实现，尚未证明与原生切片引擎算法等价。

已有资源库可以跳过导入，改用八叉树尺寸过滤：

```sh
PYTHONPATH=src python3 -m model_tiler.cli convert \
  --skip-import --resource workspace/model-resource.json \
  --mode octree --visible-pixels 25 --max-depth 8 \
  --output workspace/my-octree-tiles
```

输出目录和资源缓存不会覆盖已有文件。页面任务存储在 `workspace/<任务ID>/`；服务重启将未完成任务标记为 `interrupted`。可用该任务的 `resource.json` 从 CLI 重新切片。

## 输入与输出

- OBJ：UTF-8 / GBK，支持正/负索引、对象/组、法线、UV、凸/凹多边形耳切三角化。
- MTL：`Kd`、`d` / `Tr`、`map_Kd` PNG/JPEG，材质与纹理嵌入 GLB。暂不支持法线贴图、贴图变换选项、动画；遇到这些贴图指令会明确报错。纹理透明通道不会自动推断 MASK/BLEND，可显式指定透明模式。
- CSV：UTF-8，第一列按构件名称关联，其余列作为 Batch Table 属性。每个构件带独立 `id`、`name`。
- FBX：ASCII / 二进制静态多边形网格，层级和几何变换、基础颜色材质、PNG/JPEG 外部或内嵌纹理；动画、蒙皮、形变、曲面和未支持纹理通道会明确报错。
- 输出：`tileset.json`、`tiles/*.b3dm` / `*.i3dm` / `*.cmpt`、`scenetree.json`、`report.json`、本地预览用 `preview.json`。结构树的 `sphere` 是 ECEF 世界坐标；`boundingVolume.box` 是根 transform 下的 ENU 坐标。

小场景按三角形重心排序递归分块，保留全部几何；跨分块的三角形完整保留且各块包围体覆盖实际几何。八叉树按构件 AABB 尺寸 / 当前块尺寸 × 512 与像素阈值比较，较小构件下移到子层，使用 `refine: ADD`，每个构件只分配一次。到深度上限后保留全部数据；超限切片记录在报告中。

本版本输出 **3D Tiles 1.0 / B3DM / I3DM / CMPT**，支持 Draco 和独立几何 LOD 实现。实例优化核对完整对象的有序几何、材质、UV 和法线，支持平移、旋转及均匀缩放；非均匀变形、不同三角形顺序等不匹配对象保留普通网格。混合内容写入 CMPT。

化简分别处理构件和连通分量，保留小构件及开放边界；带纹理、非零 UV、硬法线接缝、多材质对象暂保留原网格。`report.lod` 给出实际比例、保留原因和保守误差界。尚未复刻厂商体素/表面重建和纹理烘焙算法，也未实现 CLM/UDS/SHP、纹理图集或数据库输出。小场景阈值采用逻辑三角形数；报告 `storedTriangles` 统计实际存储网格，`leafTriangles` 统计完整精度层。

模型在内存中归一化，大型 BIM 应扩展为磁盘资源库后处理。页面原始文件上传约 47 MB 上限；更大输入使用 CLI。四项高级功能的原生证据、验证结果及限制见 [高级功能实现](docs/advanced-features.md)。

## 接入 Cesium 项目

将输出目录通过 HTTP 提供，再加载：

```js
const tileset = await Cesium.Cesium3DTileset.fromUrl('/models/tileset.json');
viewer.scene.primitives.add(tileset);
await viewer.zoomTo(tileset);
// 点击拾取到 Cesium3DTileFeature 后：
// feature.getProperty('id'); feature.getProperty('name');
```

B3DM 的布局与 Batch Table 遵循 [CesiumGS 的格式规范](https://github.com/CesiumGS/3d-tiles/tree/main/specification/TileFormats/Batched3DModel)。

## 测试与验证

```sh
PYTHONPATH=src python3 -m unittest discover -s test -v
# 服务运行时，使用已安装的 Playwright 和 Chrome：
node scripts/verify_browser.cjs
node scripts/verify_advanced.cjs
```

验证结果在 `verification/`。`examples/demo-tiles/` 和 `examples/demo-octree/` 是实际转换成果，`examples/demo-resource.json` 是可重复使用的资源库。

![模型工坊实际验证截图](verification/model-workshop.png)
