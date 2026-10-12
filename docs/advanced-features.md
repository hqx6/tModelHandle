# Draco、I3DM、FBX 与几何 LOD

这四项功能已接入转换核心、CLI 和页面，并通过真实解码和 Cesium 1.123.1 渲染验证。原生切片程序的接口与化简函数也进行了静态分析；本项目使用独立实现，没有调用厂商 DLL。

## 使用

本机项目 `.venv` 为 Python 3.12，已安装 `.[advanced]`：NumPy 2.5.4、DracoPy 2.2.0、ufbx 0.0.5 和 fast-simplification 0.2.0。其他机器需要 Python 3.10+ 并安装该依赖组。基础 OBJ 功能仍可无第三方依赖运行。

页面勾选“Draco 几何压缩”，设置实例复用阈值（0 关闭，至少 2），或选择“几何化简”。FBX 与 OBJ 共用上轴、单位比例和 CSV 属性设置。缓存复用可重新选择压缩、实例和 LOD 参数。

```powershell
# 旋转/缩放实例 + Draco，独立的 ID 与属性
.\.venv\Scripts\python.exe -m model_tiler.cli convert examples/advanced/instances.obj --properties examples/advanced/instances.csv --instance-min-count 2 --draco --output workspace/my-instances

# 静态 ASCII 或 binary FBX；厘米模型需要 --scale 0.01
.\.venv\Scripts\python.exe -m model_tiler.cli convert examples/advanced/sample.fbx --draco --up-axis Z --scale 1 --output workspace/my-fbx

# 两层粗糙网格 + 完整精度叶层
.\.venv\Scripts\python.exe -m model_tiler.cli convert examples/advanced/terrain.obj --mode simplify --lod-ratio 0.25 --lod-levels 2 --draco --output workspace/my-lod
```

实际示例成果分别保存在 `examples/advanced/instances-tiles`、`fbx-tiles` 和 `lod-tiles`，输入样本均为项目自制。输出目录不覆盖已有数据。

## 实现范围

| 功能 | 已实现 | 明确边界 |
| --- | --- | --- |
| Draco | 真正编码网格，GLB 必需扩展 `KHR_draco_mesh_compression`；移除原始几何载荷；保留材质、图片、构件 ID、UV 和法线 | 位置采用 8–24 位量化，压缩级别 0–10。包围盒和瓦片误差覆盖量化及 float32 舍入误差。极小网格因格式开销未必更小 |
| I3DM | 完整重复构件共享一个 GLB；平移、旋转、均匀缩放和独立 Batch Table 属性；混合普通网格/多个模板写 CMPT | 核对有序顶点、法线、UV、材质，候选需回算到原始坐标并通过 1 微米容差。不匹配、非均匀变形或跨片拆开的对象保留 B3DM；不合并不同三角形顺序的模板 |
| FBX | ufbx 实际解析 ASCII 与二进制；应用父子层级和几何变换、非均匀缩放法线、镜像绕向、逐角 UV、基础颜色材质、内嵌/目录内 PNG/JPEG | 单位及上轴显式指定。动画、蒙皮、形变、缓存、曲面、分层/程序纹理和其他贴图通道会明确拒绝，不静默丢弃 |
| 几何 LOD | QEM 边折叠，按对象和连通分量分别处理；保留小构件、开放边界；粗糙层使用 REPLACE；最高精度层保留全部源三角形 | 暂保留带纹理、非零 UV、法线接缝、多材质等对象。没有实现厂商的表面重建、纹理烘焙或图集算法；本实现与其算法等价性尚未建立 |

化简顶点限制在原组件 AABB 内，误差使用组件包围盒对角线的保守上界。`lod_ratio` 是目标，不保证精确达到；边界、最小组件大小等约束可能阻止进一步化简。`report.lod` 包含实际比例、保留原因及预算跳过记录。

三角形和实际字节预算同时生效，I3DM 的阈值按同片完整构件数量判断。`report.triangles` 为输入逻辑三角形，`leafTriangles` 为完整精度层逻辑三角形，`storedTriangles` 为所有层实际存储网格（实例模板只计一次）。每片列出格式、LOD 层数、实例数、字节数及 SHA-256。

本地预览显示原始归一化几何，使用 Cesium 预览检查压缩、实例与 LOD 的实际显示效果。规范来源：[Draco glTF 扩展](https://github.com/KhronosGroup/glTF/blob/main/extensions/2.0/Khronos/KHR_draco_mesh_compression/README.md)、[I3DM](https://github.com/CesiumGS/3d-tiles/tree/main/specification/TileFormats/Instanced3DModel)、[CMPT](https://github.com/CesiumGS/3d-tiles/tree/main/specification/TileFormats/Composite)。解析和化简接口依据 [ufbx 网格文档](https://ufbx.github.io/elements/meshes/)、[fast-simplification 文档](https://pyvista.github.io/fast-simplification/)。

## 原生证据

使用已配置的 Ghidra 12.1.4 / REA 6.4.0 静态检查用户安装目录里的原生模块，未执行这些程序。

- `XbsjModel2Tiles.exe` 概览：889 个函数、545 个已定义字符串。Evidence `ev_e37fb925771c924c50aa92b4835a5944a830945865182765b386ce7099380603`，文件 `verification/model2tiles-overview.json`。
- 对该程序的限定字符串搜索发现 `DracoAttributeQuantization` 构造函数导入名称；这只确立接口存在，不能证明所有路径都进行 Draco 压缩。完整 Evidence 在 `verification/model2tiles-native.json`。
- `XbsjMeshSimply.dll` SHA-256：`497e9e5b724516cd4bf5b368521f3ba6af12f3465aba54659672597ff3db8872`。函数名称搜索发现 `NaniteSimplify`、`snapAndSimply`、`snapAndSimply2`、`testAlphaWrapSimply` 等入口。Evidence `ev_76787b74b025ef47845bad8cfcc126c3b9915d17e8b16f32d207a717309419b2`。
- 深入读取 `NaniteSimplify`（`0x18009d6c0`）得到函数体、反编译伪代码、引用及调用关系，观察到 `ProxyMesh` 列表和 `SimplifyOption` 边界，引用字符串含 `ReCluster`。Evidence `ev_3a65ce8c5820849a291caab4b04326b7d6f60f95a75aca9cfbaaf5f37ad02f74`，文件 `verification/mesh-simplify-function.json`。

这仍是局部静态覆盖，内部未命名函数、动态调用和重建/纹理烘焙路径尚未全部解析。名称、反编译签名及引用不作为本项目 QEM 与厂商算法等价的证明。大型本地分析 snapshot 和完整函数数据通过 `.gitignore` 排除；仓库保留 `verification/mesh-simplify-summary.json`，可依据同一 Evidence ID 对照本地完整记录。

## 验证

38 项测试全部通过，涵盖真实 Draco 解码及字段关联、ASCII/binary FBX、内嵌/外部纹理、反射/缩放、I3DM/CMPT 布局及世界位置回算、混合格式、缓存复用、取消、完整精度 LOD 和误差层级。记录：`verification/unittest.txt`。

浏览器脚本 `scripts/verify_advanced.cjs` 实际加载生成的内容、渲染并拾取构件，还核对实例深度坐标落在源构件包围盒内：

- Draco OBJ：24 个三角形，两构件属性正常拾取。
- I3DM + Draco：4 个实例共用 4 个存储三角形，4 份属性和实例位置可拾取。
- FBX + Draco：自制 FBX 的 2 个三角形及构件正常显示。
- 几何 LOD + Draco：网格近处 512 个三角形，远处选中 64 个；实际存储层级为 512 / 128 / 64。

`verification/advanced-result.json` 和 `advanced-*.png` 保存实际报告、坐标与截图。`scripts/verify_browser.cjs` 另外验证页面开关提交、历史资源复用与下载。
