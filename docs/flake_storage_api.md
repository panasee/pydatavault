# Flake 照片库：motion-control 接入协议

文档版本：`storage-contract-v1-draft3`。更新日期：2026-11-11。

**状态：已确认职责划分；以下是待实现的存取协议，不是当前可调用的 HTTP API。**
存取服务端、薄 Python 客户端和旧数据库迁移尚未完成。
文中标为“目标协议”的路径、请求和响应供两边后续实现与联调使用。

当前代码提供 `/v1/health`、`/v1/profiles`、`/v1/analyze` 原型接口，以及已实现的
`GET /v1/profiles/{profile_id}/model`、`GET /v1/models/{model_id}` 训练参数读取接口；
没有 HTTP 样本上传、下载、更新或参考集接口。`FlakePhotoStore.save_photo()`
也仍会重新计算图像特征，不能作为下面纯存取接口的等价实现。
现有本地手动画区、归一化与厚度判别工具见 [照片库使用说明](flake_photo_library.md)。
draft3 增加识别参数的存取职责与目标读取路径。**目前即可使用的参数 API**、
模型字段、单一目标/多类别模式、过期检查和离线推断公式见 [训练与参数协议](flake_training.md)。

## 1. 应用边界和部署

这里的“显微镜端”指 motion-control 仓库内的显微镜应用。该仓库同时包含
独立的运动控制应用；数据库不需要导入它们的 GUI、驱动或相机 SDK。

| 所属 | 负责的工作 |
|---|---|
| 显微镜端 | 导入/采集照片、边缘识别、颜色分区、人工修改、厚度标注、背景归一化、特征提取和层数判断 |
| PyDataVault 存取服务 | 配置与样本存取、已训练参数的版本保存/读取、二进制资源管理、字段与引用一致性校验、并发版本检查、查询和备份 |
| PyDataVault 本地用户工具 | 导入照片、手动多边形划背景及 flake、背景归一化、厚度标注、按 Profile 训练及判别；不做自动边缘识别或分区 |
| 薄 Python 客户端 | HTTP 请求、资源传输、错误转换、重试和分页；不包含算法，也不直接打开 SQLite |

数据库原样保存客户端提供的处理结果。它可检查数组形状、有限数值、标签 ID、
资源哈希和版本是否一致，但不重新分区、归一化、选择厚度或评定科学准确性。
光学质量标志由处理端（显微镜端或本地用户工具）生成，数据库只能按明确的查询
条件筛选它们。本地工具是分析功能的使用入口，不把推断职责放入对外存取协议，
也不要求 motion-control 调用 PyDataVault 的判别服务。

本地工具使用独立的无 Qt 分析模块 `flake_analysis.py`；显微镜端继续维护自动找
flake 流程。两端后续可以共享无 GUI 的归一化/特征/判别代码，或各自实现，但交换
参考数据时必须满足相同的 `feature_schema` 与 `processing_key`。现有本地存储
方法仍会重算特征，纯存取重构尚待完成。

目标服务基址：`http://127.0.0.1:51237/api/v1`，端口必须可配置。
部署时实际地址由配置传入客户端，不硬编码在算法模块中。

| 服务 | 当前/建议地址 |
|---|---|
| 运动位置服务 | `127.0.0.1:51235/position` |
| 显微镜图像服务 | `127.0.0.1:51236/api/v1/camera/image` |
| PyDataVault 存取服务 | 建议 `127.0.0.1:51237/api/v1`，待实现 |

原型 `flake_api.py` 默认端口为 51237；它接受显式手动多边形进行分析并提供模型参数读取，
仍然没有新的 `/api/v1` 接口。它与未来存取服务不能同时占用该端口。
目前没有可启动目标存取服务的命令，后续实现时须补充入口和启动说明。

首版服务独立于 GUI 生命周期，绑定本机回环地址。不在请求中接收任意本地路径、
远程下载 URL 或 SQL；图片通过资源接口传输。局域网部署及认证是后续单独的范围。

## 2. 资源模型与统一约定

| 资源 | 含义 |
|---|---|
| Profile | 材料＋基底（包含氧化层厚度）＋成像配置；查询和判断必须显式指定 |
| Asset | 不可变二进制资源，如原图、区域标签图、背景掩膜、归一化数组 |
| Sample | 一张原图及其当前区域、人工厚度标签、特征、处理版本、采集元数据 |
| Reference entry | 从已确认样本区域中筛选出的参考特征；没有推断逻辑 |
| Trained model | 处理端拟合的识别参数、模式、通道、适用配置/处理版本、训练来源和诊断；存取服务不重新训练 |

- `profile_id`、`asset_id`、`sample_id` 都是服务端生成的不透明字符串。
  示例中的 `profile_12` 等只是占位值，不保证实际格式。旧 SQLite 整数 ID
  不应直接成为跨应用契约。
- JSON 使用 UTF-8；所有浮点数必须有限。日期时间使用带时区的 ISO 8601 字符串，
  服务端生成时间统一为 UTC。数组通道顺序显式固定为 RGB。
- `revision` 是单条记录的正整数版本；`dataset_revision` 是整个样本数据集的
  单调递增版本。它们与算法版本、特征格式版本各自独立。
- `null` 表示未知/未测量；层数不能用 `0` 表示未知。`layers` 为正整数，
  `thickness_nm` 为正有限数。两者可以只提供一个，不隐式换算。
- 原始资源不可变，更新通过上传新资源并修改样本引用完成。预测结果不能写入
  `ground_truth`；首版没有自动预测结果写回接口。

### 2.1 图像坐标与二进制格式

所有区域均对应原图经过 EXIF 方向校正后的完整图像：左上角原点，X 向右、Y 向下，
单位为像素。像素索引 `(x, y)` 对应数组 `[y, x]`。禁止把预览缩放、裁剪或旋转后的
坐标直接作为原图坐标上传；需要先在显微镜端映射回来。

| `kind` | 首版目标编码 | 约定 |
|---|---|---|
| `original` | PNG/JPEG，8-bit RGB 解码 | 保存原始文件字节；EXIF 校正后的宽高写入样本 |
| `label_map` | 单通道 16-bit 无损 PNG | `0` 非 flake，`1..65534` 区域 ID，`65535` 忽略 |
| `substrate_mask` | 单通道 8-bit 无损 PNG | `255` 是选定的背景参考，`0` 不参与背景参考 |
| `normalized_rgb` | `.npy`，little-endian float32、C-order、`[H,W,3]` | 无 pickle/object 数组；不为显示效果裁剪浮点值 |

区域标签图是边界的权威记录，包括孔洞。首版不同时维护另一套可独立修改的多边形。
显微镜端可将标签图转换为多边形用于编辑，再将编辑结果写回标签图。
背景参考像素必须对应 `label_map == 0`，不能与 flake 或忽略像素重叠。
归一化数组的含义和单位由 `feature_schema` 定义，不能默认所有算法都采用相同归一化。

**迁移差异：**旧原型内部采用 `-1` 忽略、`0` 背景、正整数区域；新协议使用 PNG 的
`65535` 忽略值。适配器必须显式转换，旧值 65535 如曾用作普通区域需先重新编号。
不得直接强制转换整个有符号数组而不验证 ID 对应关系。

### 2.2 处理版本和特征兼容性

每个样本需要 `processing`：

```json
{
  "feature_schema": "microscope.rgb-ratio-percent.v1",
  "processing_key": "rgb8-bg-median-region-median-erode1-v1",
  "producer": "microscope-workbench",
  "producer_version": "example-1.0",
  "parameters": {"background_statistic": "median", "region_statistic": "median", "erosion_px": 1}
}
```

- `feature_schema` 确定特征字段、单位、通道顺序及定义。上例的
  `features.normalized_rgb` 必须是 3 个非负有限数，单位为背景等于 100 的百分比。
  这是特征格式的示例，不要求数据库执行其计算。
- `processing_key` 标识影响特征可比性的处理规则，由算法维护方管理。
  相机输入阶段、背景统计、区域统计等规则改变时必须使用新 key。
- `producer_version` 记录完整软件版本，便于追溯；只有界面变化时可以保留原 key。
- 查询参考集必须同时指定 `profile_id`、`feature_schema`、`processing_key`。
  数据库不跨 key 合并或转换特征。不能复用既有 schema/key 来改变其科学定义。
- 图像分区参数可额外记录于 `parameters`；客户端负责判断哪些参数会影响
  特征可比性，并据此变更 key。服务端不会从参数自动推导算法兼容性。

## 3. 接口一览（目标协议，全部待实现）

下表路径均相对于 `/api/v1`。JSON 请求使用 `Content-Type: application/json`。

| 方法 | 路径 | 成功响应 | 用途 |
|---|---|---|---|
| GET | `/health` | 200 | 协议能力、数据集版本和资源限制 |
| GET | `/profiles` | 200 | 查询标定配置 |
| POST | `/profiles` | 201；已存在时 200 | 创建或取得相同配置 |
| POST | `/assets?kind=...&encoding=...` | 201；去重命中时 200 | 上传一个二进制资源 |
| GET | `/assets/{asset_id}` | 200，二进制 | 下载原图/掩膜/处理数组 |
| POST | `/samples` | 201 | 原子提交完整样本 |
| GET | `/samples` | 200 | 筛选、分页列出样本摘要 |
| GET | `/samples/{sample_id}` | 200 | 读取完整样本和资源引用 |
| PATCH | `/samples/{sample_id}` | 200 | 带版本检查地更新标注/处理结果 |
| GET | `/references` | 200；缓存未变时 304 | 按兼容配置批量读取参考特征 |
| GET | `/profiles/{profile_id}/model` | 200；未训练时 404 | 最新训练参数、源数据版本和过期状态 |
| GET | `/models/{model_id}` | 200；不存在时 404 | 固定版本模型，用于复现和追溯 |

首版没有物理删除接口。错误样本通过把区域标签标记为 `rejected` 移出参考集，
保留原始记录以便复核。未引用资源的清理属于服务端维护流程。

### 3.1 健康状态和能力检查

目标 `GET /health` 响应示例：

```json
{
  "service": "pydatavault-flake-storage",
  "api_version": "1",
  "contract_version": "storage-contract-v1-draft3",
  "capabilities": ["profiles", "assets", "samples", "references", "trained_model_export", "optimistic_concurrency"],
  "dataset_revision": 42,
  "limits": {"json_bytes": 4194304, "original_bytes": 33554432, "asset_bytes": 268435456, "image_pixels": 16000000, "regions_per_sample": 256}
}
```

这里的 limits 是首版建议默认值，部署可配置，客户端使用实际返回值。
归一化 float32 数组比原图文件大，不能统一套用 32 MiB 原图限制。
客户端必须检查服务名、版本和能力，不能仅凭 200 或端口号判断自己连到了正确服务。

### 3.2 标定配置

`POST /profiles`：

```json
{"material": "MoS2", "substrate": "285 nm SiO2/Si", "optical_setup": "scope-A / 50x / white / camera-mode-1", "notes": "标定照片组"}
```

响应为该对象加上 `profile_id`、`revision`、`created_at`、`updated_at`。
材料、基底和成像配置是必填字符串，去掉首尾空白后按不区分大小写的完整三元组去重。
命中已有配置返回 200，不更新其备注。首版创建后不改身份字段；不同成像条件创建新配置。

`GET /profiles` 支持 `material`、`substrate`、`optical_setup` 完整匹配筛选，
以及 `limit`、`cursor`。返回 `items`、`next_cursor`、`dataset_revision`。
客户端可以做显示搜索，但实际请求参考集时必须选定具体 `profile_id`。

### 3.3 二进制资源

上传示例：`POST /assets?kind=label_map&encoding=png`，请求体为 PNG 原始字节。
PNG/JPEG 分别使用 `image/png` / `image/jpeg`；NPY 使用 `application/octet-stream`。
不使用 base64 JSON 传输大图。可提供 `X-Content-SHA256`，供服务端与收到的字节核对。

响应示例：

```json
{
  "asset_id": "asset_labels_21",
  "kind": "label_map",
  "encoding": "png",
  "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "size_bytes": 18320,
  "download_path": "/api/v1/assets/asset_labels_21"
}
```

哈希指上传文件的完整字节，不是解码像素哈希。相同 kind、编码和字节内容去重，
文件名不作为身份。同一采集另存为不同编码不一定命中内容去重，因此还要保存采集 ID。
服务端至少校验编码、尺寸、通道、类型和声明的 kind；下载返回原字节、正确的
`Content-Type`、`Content-Length` 和 `X-Content-SHA256`。客户端可复核哈希。

资源先上传，提交样本时再绑定。上传成功不代表样本已经入库，也不会进入参考集。
服务端必须串行化“绑定引用”和“清理未引用资源”，保证不会删除已提交样本的资源。
清理策略未定义前保留未引用资源；不得给资源上传结果添加未约定的过期时间。

### 3.4 创建样本

`POST /samples` 的目标请求示例：

```json
{
  "profile_id": "profile_12",
  "assets": {
    "original": "asset_original_20",
    "label_map": "asset_labels_21",
    "substrate_mask": "asset_substrate_22",
    "normalized_rgb": "asset_normalized_23"
  },
  "image": {"width": 1920, "height": 1080, "coordinates": "exif_oriented_pixels"},
  "source": {
    "acquisition_id": "microscope-A:frame-20261111-001",
    "captured_at": "2026-11-11T09:00:00Z",
    "camera_metadata": {"exposure_us": 10000, "gain": 1.0, "gain_unit": "multiplier"}
  },
  "processing": {
    "feature_schema": "microscope.rgb-ratio-percent.v1",
    "processing_key": "rgb8-bg-median-region-median-erode1-v1",
    "producer": "microscope-workbench",
    "producer_version": "example-1.0",
    "parameters": {"background_statistic": "median", "region_statistic": "median", "erosion_px": 1}
  },
  "regions": [
    {
      "region_id": 1,
      "ground_truth": {"layers": 2, "thickness_nm": 1.4, "source": "AFM", "status": "verified"},
      "features": {"normalized_rgb": [78.3, 84.1, 91.0]},
      "quality_flags": []
    }
  ],
  "quality_flags": [],
  "notes": "厚度已人工确认"
}
```

上例假设标签图只有一个正区域 ID。响应为完整样本对象，加上 `sample_id`、
`revision: 1`、`created_at`、`updated_at`、`original_sha256` 和提交后的 `dataset_revision`。

必填与一致性规则：

- `profile_id`、前三种 assets、`image`、`source.acquisition_id`、`processing`、
  `regions` 必填；`normalized_rgb` 资源、拍摄时间、相机元数据、备注可省略。
  可选 `quality_flags` 默认空列表；未知的采集信息不能编造。
- `source.acquisition_id` 由显微镜端生成并稳定复用，建议包含采集方命名空间。
  同一 profile、采集 ID、feature_schema、processing_key 只创建一个样本；重复创建
  返回 `409 SAMPLE_EXISTS` 和已有 sample_id。相同采集经新处理 key 重算可另建样本。
- 每个正区域 ID 恰好对应一个 region；不允许重号、空区域或缺少区域标注对象。
  掩膜、标签图和处理数组尺寸必须与原图坐标尺寸一致。
- `ground_truth.status` 为 `draft`、`verified` 或 `rejected`。`draft` 可以没有厚度，
  `verified` 必须包含层数/纳米厚度至少一个以及非空 source。source 是测量或确认依据，
  不是自动预测结果。`rejected` 保留已有记录，不参与参考集。
- 尚未计算特征时允许 `features: null`，不能进入参考集。特征存在时必须符合
  声明的格式；版本不支持时拒绝，不猜测字段或静默转换。
- 原图和完整资源引用必须存在且可读。校验全部通过后，在一次事务中提交样本、
  所有区域、资源引用及 dataset_revision；失败不产生部分样本。
- camera_metadata 保存原始采集元数据。若记录台面位置，还应记录其时间及是否与
  曝光同步；两个“最新值”接口不能证明位置就是曝光时位置。

### 3.5 查询和更新样本

`GET /samples` 支持：`profile_id`、`layers`、`thickness_nm_min`、`thickness_nm_max`、
`label_status`、`feature_schema`、`processing_key`、`acquisition_id`、`limit`、`cursor`。
厚度范围端点包含边界；多个区域筛选条件必须由同一个区域同时满足，
命中任一区域即返回该照片摘要。摘要包含 ID、profile_id、revision、区域数量、
拍摄/更新时间和原图 asset_id，不嵌入图片和全部特征。空结果返回 200 和空 items。

目标列表响应格式：

```json
{"items": [], "next_cursor": null, "dataset_revision": 42}
```

`GET /samples/{sample_id}` 返回 3.4 节完整对象；资源需单独下载。

目标 `PATCH /samples/{sample_id}` 是普通 JSON 的字段替换协议，不是 JSON Patch：

```json
{
  "expected_revision": 3,
  "notes": "复测后修订",
  "regions": [
    {
      "region_id": 1,
      "ground_truth": {"layers": 3, "thickness_nm": 2.1, "source": "AFM repeat", "status": "verified"},
      "features": {"normalized_rgb": [78.3, 84.1, 91.0]},
      "quality_flags": []
    }
  ]
}
```

`expected_revision` 必填。省略的字段保持原样，提供的对象或数组整体替换。
可更新 `notes`、`regions`、`quality_flags` 及处理资源引用；身份字段 profile_id、
original、image、source.acquisition_id、feature_schema、processing_key 不可修改。

只改人工标签时可保留原 features。修改标签图或背景掩膜时，必须一起提交完整 assets、
processing 和 regions；显微镜端须重新计算所有受影响特征和 normalized_rgb，
或将尚未重算的 features/normalized_rgb 置 null。服务器只检查一致性，不代为重算。
改变 feature_schema 或 processing_key 时用 POST 创建独立处理版本，保留旧版样本。

成功返回完整对象，revision 加一，dataset_revision 推进；版本不匹配返回
`409 REVISION_CONFLICT` 和 current_revision，客户端重新获取并人工合并，不能强制覆盖。
记录 revision 是并发控制标识，不承诺保存同一处理版本的所有历史编辑。

### 3.6 批量读取参考数据与缓存

目标请求：

```http
GET /api/v1/references?profile_id=profile_12&feature_schema=microscope.rgb-ratio-percent.v1&processing_key=rgb8-bg-median-region-median-erode1-v1&label_status=verified&quality=unflagged&limit=1000
```

前三个筛选字段必填；`label_status` 首版只接受 `verified`，省略也采用 verified。
`quality` 为 `unflagged`（默认）或 `all`。unflagged 要求样本和区域的 quality_flags
都为空；all 仍不包含 draft/rejected 标签。两种情况下 features 都不能为 null。
其他过滤项：`layers`、`exclude_acquisition_id`、`exclude_original_sha256`。
排除条件用于防止待识别照片被当作自己的参考。

目标响应示例：

```json
{
  "profile_id": "profile_12",
  "feature_schema": "microscope.rgb-ratio-percent.v1",
  "processing_key": "rgb8-bg-median-region-median-erode1-v1",
  "dataset_revision": 42,
  "items": [
    {
      "sample_id": "sample_31",
      "sample_revision": 3,
      "region_id": 1,
      "acquisition_id": "microscope-A:frame-20261111-001",
      "original_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "ground_truth": {"layers": 2, "thickness_nm": 1.4, "source": "AFM", "status": "verified"},
      "features": {"normalized_rgb": [78.3, 84.1, 91.0]},
      "quality_flags": []
    }
  ],
  "next_cursor": null
}
```

此接口不包含预测层数、最近邻、通道权重或置信度；这些由显微镜端计算。
参考条目的 quality_flags 合并样本级与区域级标志。acquisition_id、原图哈希和样本版本
用于追溯与去重；独立照片数不能用区域数代替，数据库不自动判断两次采集是否独立。

所有列表使用不透明 cursor，不使用 offset。profiles/samples 默认每页 50、最多 200；
references 默认每页 1000、最多 5000。cursor 绑定筛选条件、排序和 dataset_revision。
后续页带上相同筛选和 limit；条件不符返回 `400 INVALID_CURSOR`。
期间数据集变化返回 `409 DATASET_CHANGED`，客户端丢弃未完成的分页结果并重新读取。
不能把不同 dataset_revision 的页拼成一个参考集。

首次 reference 请求返回代表**整个筛选结果**的 ETag；后续页返回相同 ETag。
客户端完整读完且缓存所有页后，才能在下一次首请求使用 `If-None-Match`。
全部筛选和数据版本未变时返回 304，无响应体。筛选条件和 limit 也属于缓存键。
收到 304 后继续使用完整缓存；缓存未完成时不得请求条件刷新。
新参考集完整读取成功后再替换旧缓存，失败不破坏可用缓存。

## 4. 幂等、错误与重试

创建配置、上传资源、创建样本的 POST 和修改样本的 PATCH 均支持并要求
`Idempotency-Key`。客户端为一次逻辑操作生成唯一 key，超时重试时保持 key、路径、
查询参数及请求体字节不变。不同操作生成新 key。查询 GET 不需要该头。

服务端以 method＋规范化路径/查询＋key 记录请求指纹和成功响应，与对应写入一起提交。
重复相同请求重放首次成功响应（包括状态码），不同请求复用 key 返回
`409 IDEMPOTENCY_CONFLICT`。首版没有 key 过期机制。对于 PATCH，幂等重放先于
expected_revision 检查，以便“已提交但响应丢失”的重试仍能成功。失败未提交的请求
不占用 key。资源哈希去重与逻辑请求幂等是不同机制。

统一错误响应示例：

```json
{
  "error": {
    "code": "REVISION_CONFLICT",
    "message": "Sample changed; reload before editing",
    "details": {"sample_id": "sample_31", "expected_revision": 3, "current_revision": 4},
    "request_id": "request_example_1"
  }
}
```

| HTTP | 典型 code | 客户端处理 |
|---|---|---|
| 400 | `INVALID_REQUEST`、`INVALID_CURSOR` | 修正请求格式/参数 |
| 404 | `NOT_FOUND` | 刷新列表或报告资源不存在 |
| 409 | `REVISION_CONFLICT` | 重载并合并，不盲目重试覆盖 |
| 409 | `DATASET_CHANGED` | 丢弃未完成参考页，重新读取 |
| 409 | `IDEMPOTENCY_CONFLICT`、`SAMPLE_EXISTS` | 检查 key 或打开已有样本 |
| 413 | `PAYLOAD_TOO_LARGE` | 检查 health 的限制 |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | 使用约定的资源编码 |
| 422 | `VALIDATION_FAILED`、`UNSUPPORTED_FEATURE_SCHEMA` | 修正维度、标签、字段或版本 |
| 503 | `STORAGE_UNAVAILABLE` | 有界退避重试，保留未提交的本地标注 |

连接失败/超时和 503 可以有限次退避重试；写操作必须复用幂等 key。
响应成功但本地未收到时，不能直接换 key 重建样本。离线编辑应保留采集 ID、
待上传资源和待提交文档，恢复连接后按原操作继续。

## 5. motion-control 接入步骤

### 建立样本

1. 配置服务基址，调用 health 确认连的是目标存取服务。
2. 查询或创建 profile，记录返回的 profile_id。
3. 显微镜端完成分区、背景选择、人工厚度标注、归一化和特征计算。
4. 上传 original、label_map、substrate_mask 和可选 normalized_rgb，得到 asset_id。
5. POST samples 提交完整引用和元数据。只有拿到 sample_id/revision 才标记“已保存”。

### 重新编辑

1. GET sample 及所需 assets，按原图坐标恢复编辑状态。
2. 标注变化后构造完整 regions；边界/背景变化时同时重算受影响特征并上传新资源。
3. PATCH 携带 expected_revision 和幂等 key；冲突时保留本地修改并提示合并。

### 自动找 flake

1. 扫描前按 profile、feature_schema、processing_key 取得同一版本的完整参考集。
2. 显微镜端缓存特征，逐帧完成分区与判断，无需逐帧访问数据库。
3. 显式保留使用的数据集版本及 sample_revision，便于追溯判断依据。
4. 在扫描任务边界或用户要求时检查更新。数据库暂时不可达时保留旧缓存，
   是否允许使用过期缓存由显微镜工作流决定并显示，不默默宣称已是最新版本。
5. 自动预测不能直接提升为 verified 标签；需要人工确认或独立测量依据。

## 6. HTTP 调用示例（目标接口实现后使用）

以下使用标准库，不假定薄客户端已经存在。它们是单次请求示例；生产客户端需
按第 4 节处理错误和重试。`sample.json` 须替换为实际处理结果及上传返回的资源 ID。

```python
import hashlib
import json
import uuid
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:51237/api/v1"

# 操作开始时生成一次，并随本地待提交任务持久化；重试复用它。
upload_key = str(uuid.uuid4())
image_bytes = Path("flake.png").read_bytes()
query = urlencode({"kind": "original", "encoding": "png"})
request = Request(
    f"{BASE}/assets?{query}", method="POST", data=image_bytes,
    headers={"Content-Type": "image/png", "Idempotency-Key": upload_key,
             "X-Content-SHA256": hashlib.sha256(image_bytes).hexdigest()},
)
with urlopen(request, timeout=60) as response:
    original_asset = json.load(response)

# 同理上传 label_map、substrate_mask，以及可选 normalized_rgb。
sample = json.loads(Path("sample.json").read_text(encoding="utf-8"))
sample["assets"]["original"] = original_asset["asset_id"]
sample_key = str(uuid.uuid4())
body = json.dumps(sample, ensure_ascii=False, allow_nan=False).encode("utf-8")
request = Request(
    f"{BASE}/samples", method="POST", data=body,
    headers={"Content-Type": "application/json", "Idempotency-Key": sample_key},
)
with urlopen(request, timeout=60) as response:
    saved = json.load(response)
print(saved["sample_id"], saved["revision"])
```

薄客户端可封装为下列调用形式，但本仓库目前没有这些客户端方法：

```python
vault.upload_sample(sample_bundle)
vault.get_sample(sample_id)
vault.update_annotations(sample_id, complete_regions, expected_revision=3)
vault.get_references(profile_id=profile_id,
                     feature_schema="microscope.rgb-ratio-percent.v1",
                     processing_key="rgb8-bg-median-region-median-erode1-v1")
```

## 7. 识别参数的接入约定

现在 motion-control 可以通过 `/v1/profiles/{整数profile_id}/model` 下载模型，在
显微镜端用相同的特征处理方式和评分规则执行判别。此读取已经实现，不依赖目标
`/api/v1` 样本 CRUD 完成。原型参数的完整结构以 [训练与参数协议](flake_training.md)
为准；纯存取协议将来迁移到不透明字符串 ID，客户端不可假定两套 ID 可直接互换。

目标 `/api/v1` 的模型读取需保持以下语义（尚待实现）：

- 模型绑定 Profile、feature_schema、processing_key，保留训练使用的源数据版本
  和 fingerprint；通过 GET 导出数值参数，不执行训练。
- 新训练创建不可变模型版本，最新模型单独查询，历史 ID 始终指向同一次训练。
- 源样本变化后返回 `stale=true`，客户端拒绝用于新扫描并要求处理端重新训练。
  目标数据集有统一 `dataset_revision`；模型还应保存具体训练源版本，不能把
  另一 Profile 的变化误判为本模型来源变化。
- 多厚度 `multiclass` 与单厚度 `single_target` 都可导出。后者只给目标相似性，
  没有负样本时不得把相似度或训练保留率解释为误识别率/准确概率。
- 分组 ID、图像哈希、训练诊断、通道顺序、特征单位、均值、协方差/逆矩阵、
  拒绝距离和分类间隔均随模型输出，避免显微镜端再猜测默认参数。
- 当前图像或分组若参与过训练，应标记 `training_sample`；预测不回写为 ground_truth。
- 参数是数据资源；训练由本地工具或显微镜分析模块负责。目标协议暂不增加
  自动训练、自动预测或 HTTP 模型发布接口。

## 8. 实施顺序与联调验收

| 工作 | 当前状态 |
|---|---|
| 独立照片 SQLite、原图/掩膜/标注保存、备份 | 原型已有，存储仍与特征计算耦合 |
| 纯存取模型与结构校验，从存储层去除算法调用 | 待实现 |
| 旧数据迁移，整数 ID/忽略值/特征版本适配 | 待实现；迁移前备份，不直接改旧记录含义 |
| 本文 `/api/v1` HTTP 服务、幂等、分页版本与缓存 | 待实现 |
| 薄客户端、显微镜分区/标注流程接入 | 待实现 |
| 显微镜自动找 flake 与厚度判断接入 | 待完成；复用该端已有轮廓编辑能力 |
| PyDataVault 本地手动选区、背景归一化与厚度判别 | 已有；与目标存取服务分开，无自动分区 |
| 本地 Profile 训练、单一目标模式、版本化参数及 `/v1` 导出 | 已实现；详见训练与参数协议 |
| `/api/v1` 模型读取、与目标存取 ID/数据版本体系的迁移 | 待实现 |

实现时先完成纯存取模型、资源与样本 CRUD，再实现参考查询和薄客户端，最后切换
显微镜工作流。旧 `/v1/analyze` 只作过渡原型，不作为新的跨应用依赖。
更改文档状态为“已实现”前至少通过以下联调：

- 原图逐字节下载一致，带 EXIF 方向的照片与所有掩膜正确对齐。
- 原图和派生资源尺寸/标签不一致时拒绝，失败提交不留下半份样本。
- 同一请求重复发送不重复建样本；写入成功但响应丢失后可安全重试。
- 两个编辑端同时修改时识别 revision 冲突，原记录不被覆盖。
- 分页期间标签变化时拒绝拼接不同版本；304 仅复用已完成的完整缓存。
- 材料、基底、成像配置、特征版本与 processing_key 筛选严格隔离。
- draft/rejected、features 缺失和要求排除的待测采集不会进入参考集。
- 导入服务端/客户端不会载入 Qt、显微镜 GUI、相机 SDK 或运动驱动。
- 数据库备份恢复后，样本及全部引用资源仍可读取。

相关代码现状：[原型 HTTP 服务](../pydatavault/flake_api.py)、
[原型存储](../pydatavault/flake_photo_store.py)、[备份](../pydatavault/backup.py)。
本文作为目标协议的唯一主入口；后续协议变化先更新版本和示例，再同步两端实现。
