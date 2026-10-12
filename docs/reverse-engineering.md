# 通用模型切片续作 · 2026-10-12

后续更新：已安装并验证 Ghidra 12.1.4 + JDK 21 + REA 6.4.0，实际反编译入口及原生化简函数成功。Draco、I3DM/CMPT、静态 FBX 和独立几何 LOD 已接入，见 [高级功能实现](advanced-features.md) 与 [原生引擎配置](native-engine.md)。下文是第一阶段的历史记录；其中缺失引擎及四项未实现状态已由后续工作更新，厂商重建与纹理烘焙算法仍未完整复刻。

本轮以项目现有 OBJ 转 3D Tiles 实现为基线。用户提供的历史分享链接无法获取内容，没有据此推断之前的结论。

## 证据来源与边界

入口为用户指定的 `cesiumlab4.exe`（4.0.17 安装目录）。同一目录的 `server.exe` 包含前端脚本；`bin/XbsjModel2Tiles.exe` 是相关原生程序，本轮未执行或反编译其算法。

- `server.exe` SHA-256：`29e82f8df358ce0f45498ddf8d9e86b51d0f99dd1126e9940d61bcb82cdf7bad`。
- 内嵌前端脚本字节范围：`[262859400, 264748683)`；脚本 SHA-256：`9c73239397dc14e0f0d5f93587c1a91387739ffe0aec79688eb8a99637a1fc28`。
- 提取工具：`scripts/inspect_cesiumlab.cjs`。使用 Babel AST，只在隔离 VM 中初始化字符串表和旋转表，未启动原应用。替换 48,142 个已识别字符串解码调用。
- REA 6.4.0 静态分析 Evidence：`ev_98183dc854280d38fb34935d81e0c6854aa9d584086a037c691fe18d78911f10`。
- 完整本地证据在 `verification/reverse/rea-evidence.json`；原始提取物和完整证据通过 `.gitignore` 排除，避免提交大型厂商代码副本。`verification/reverse/artifact.json` 保存来源绑定。

REA 的应用图覆盖为 partial；语义图触及 100,000 节点上限，动态接收者和调用关系仍不完整。分析目录包含一个当时正在输出的 JSON 文件，产生一个解析失败；目标 JS 已解析。本轮功能结论来自通用模型组件的设置、输入对象与任务配置，不能证明厂商原生引擎运行行为。当前未配置 Ghidra / IDA / Hopper，原生算法与各参数内部执行细节仍未知。

## 已核对并实现

| 功能 | 安装包内观察 | 本项目实现与验证 |
| --- | --- | --- |
| GBK | OBJ 输入对象含 `encodeGBK`；模型设置含编码开关 | OBJ/MTL 可选 GBK；中文构件名、材质名、文件引用通过测试 |
| 翻转绕向 | OBJ 输入含 `reverseTriangle`，设置页含绕向开关 | 反向顶点与 UV，反向显式法线或重算面法线；两种法线来源均测试 |
| 无光照 | 通用模型输入含 `nolight`，提示指向烘焙模型 | `KHR_materials_unlit`；读取实际 B3DM 验证扩展与材质 |
| 透明模式 | `alphaMode` 设置提供 MASK / BLEND | auto / OPAQUE / MASK / BLEND；验证 GLB `alphaMode` 与 `alphaCutoff` |
| 输出原点 | `firstsrs` 提示为保留原始原点，否则为模型包围盒中心 | original / center；补偿根 ECEF transform，测试世界顶点与结构树球体一致，资源缓存不变 |
| 属性筛选 | 属性组件含 `checkedNames`，模型任务含 `props`、`modelname` 和 `packUnselectedProperty` | 输出字段白名单，ID/name 始终保留；未复刻厂商未选字段打包格式 |
| 切片大小 | 通用模型 `nolod.b3dmMaxSize` 默认 `0x1312d00`（20,000,000），页面转为 MB | 按实际 B3DM 字节数递归分块，不能满足预算时报告超限；未实现 CMPT 小片聚合 |
| 缓存复用 | `database.sqlite.file` 与 `database.skipImport` 表示资源库和跳过导入 | 复用本项目 JSON 资源库，页面选择历史任务；重新启动后仍可复用。与厂商 SQLite 格式不兼容 |

字段、说明和入口已确认；材质映射、法线处理、预算分割和原点补偿是本项目独立实现。为了保持现有结果兼容，默认原点、编码、属性行为沿用本项目已有值。

## 未完成与后续证据需求

- FBX / CLM / SHP / UDS / SKP：已在前端发现输入类型，尚未实现这些解析器。
- 重建化简与纹理图集：发现 `lod.recon`、`maxZoomRecon` 等配置；需要原生代码或受控模型输出证据，不能从参数名称推断算法。
- I3DM / CMPT：发现实例阈值默认 50、CMPT 大小默认 2 MB；没有推断去重、实例变换和小片合并规则。
- Draco、WebP/KTX2、投影坐标转换与数据库输出：前端配置已发现，尚未接入。
- 本轮未确认通用模型组件的三轴旋转/平移接口；其他组件中的相似字段未作为本功能实现依据。

本轮完成上述八项可落地功能，通用模型切片的全部能力仍未完成。

## 验证

`python -m unittest discover -s test -v`：15 项通过，覆盖真实 B3DM 解码、法线/UV/属性、ECEF 坐标、字节预算、缓存复用与取消清理。

`scripts/verify_browser.cjs` 验证页面设置、真实任务请求、缓存复用、ZIP/资源输出及 Cesium 1.123.1 渲染。结果和截图在 `verification/`。支持 `MODEL_TILER_URL` 指定服务地址，支持现有 `playwright` 或 `playwright-core`。
