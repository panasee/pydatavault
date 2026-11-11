# Flake 照片库：手动选区与厚度识别

PyDataVault 提供本地小工具：用户导入照片，用多边形圈出硅片背景和各个 flake
区域，然后进行背景归一化并与已标定照片比较。这里不做自动边缘识别或颜色分区。

这与对外数据存取服务是两个职责。motion-control 的自动找 flake 流程继续使用
显微镜端算法；后续接入请参考 [存取接口协议](flake_storage_api.md)。该文档的
`/api/v1` 存取服务和薄客户端仍待实现。本文描述已经可用的本地工具，以及保留的
`/v1` 手动选区分析原型，不能把后者当作存取服务。

## 用户操作

1. 打开 **Tools → Flake Photo Library…**。新建或选择 Profile：材料、基底
   （包含氧化层厚度，例如 `285 nm SiO2/Si`）、成像配置（显微镜、物镜、光源/
   滤光片和相机模式）。仅在同一 Profile 内比较。材料由用户选择，不从图像猜测。
2. **Import photo…** 导入 8-bit PNG/JPEG/TIFF/BMP。初始没有任何选区。
3. **Draw substrate polygon** 圈出干净的硅片/氧化层背景，避开 flake、污点、
   反光和阴影。单击添加顶点，双击或 Enter 闭合；Backspace 删除最后一个顶点，
   Esc 取消当前多边形。可以添加多个背景多边形，它们的并集作为参考。
4. **Draw a new flake region** 圈出一个厚度均匀的 flake 区域。不同厚度分别画区，
   不要求圈出整块 flake。背景与 flake、不同 flake 区域不能重叠。
5. **3. Estimate thickness** 显示每区的层数/厚度候选、状态和归一化 RGB 百分比。
   识别未知样本时不需要填写已知层数，也不需要保存照片。没有足够参考数据时会
   明确提示；不会强行给出确定层数。悬停结果可查看参考照片数量、距离和质量信息。
6. 如果这是一张已测量厚度的标定照片，在表中填写 **Known layers** 和/或
   **Known nm**，以及 **Label source**（例如 AFM 或 Raman），再点击
   **Save as labeled reference photo**。所有保留区域都需要真实标签及来源。
   未知区域可删除后再保存。预测值不会自动成为参考标签。
7. 保存多张参考照片后，点击 **Train / update profile** 拟合该配置的识别参数。
   可自动选择 RGB 通道组合，也支持只训练一个目标厚度；各厚度至少 3 个独立组。
   同一 flake 的重复拍摄应填写同一 **Flake / training group**。
   完整训练、单目标结果含义和参数 API 见 [训练说明](flake_training.md)。

### 修改与重开

- 单击图像中的区域或表格行选中它，然后 **Redraw selected region** 重新画区。
  重画会清除此区域的已知厚度标签，避免新区域错误继承旧标签。
- **Remove selected regions** 删除选中区域，支持多选；**Clear substrate reference**
  清空背景后可重画。**Undo edit** 恢复选区和标签，最多保留 8 步，并限制内存占用
  （大图至少保留一步）。任何修改都会清除旧预测，需要重新识别。
- 滚轮缩放，**Select / pan** 拖动视野，**Fit photo** 适配窗口。
  可以切换原图、区域叠加或归一化预览。青色为背景，黄色为用户区域边界，白色为选中区。
- 双击左侧已保存照片重新编辑；再次保存更新同一条记录及其 revision。
  并发编辑冲突会拒绝覆盖，保留本地修改。原始文件可用 **Export saved original…** 导出。

照片的原始字节完整保留。处理使用 EXIF 方向校正后的 RGB 图像；所有坐标均为该图像
的全分辨率像素坐标，左上角原点、X 向右、Y 向下。单个多边形需要 3–512 个顶点，
不可自交或越界；界面要求每区至少 9 像素，每张图最多 256 个 flake 区域。
照片限制为 1600 万像素、32 MiB，不接受透明或高位深/RAW 图像。
预览缩小和显示亮度不改变实际测量值。

## 背景归一化

处理版本仍为 `substrate-ratio-v1`，三个通道分别计算：

```text
background_rgb[c] = median(raw_rgb[用户圈选的硅片背景, c])
normalized_rgb[y, x, c] = 100 * raw_rgb[y, x, c] / background_rgb[c]
```

选定背景的每通道中位数为 100%。实际特征和存储图像使用浮点数，不截断大于 100%
的值。界面归一化预览把背景映射到 RGB 160，仅方便观察，不参与判别。

这一方法在乘性增益模型下抵消整体亮度和 RGB 通道增益变化，可补偿这类白平衡/
颜色偏差。**它不是完整的相机色彩标定**：不能恢复过曝/欠曝信息，也不能通用地
消除 gamma、色调映射、黑电平偏移、照明光谱变化、通道混色或空间不均匀照明。
应保持采集条件尽量稳定，把不可比的成像条件放入不同 Profile。

背景中位数任一通道接近黑色（≤5）或饱和（≥250）时拒绝归一化。
背景不均匀、局部截断、区域太小或颜色混杂会产生质量标志。
带质量标志的参考区域/照片可保存供检查，但不参与判别。
flake 特征取区域内部的中位数 RGB 比值；内部像素充足时排除一像素边界，减少混色。
这只是对用户掩膜取内部像素，不是对照片运行边缘检测。

## 训练后判别与未训练基线

训练后的配置使用持久化的通道选择、类别均值、正则化协方差和拒绝阈值。
每个 Profile 独立训练；`multiclass` 区分已标定厚度，`single_target` 只判断是否像
唯一的目标厚度。`target_like` 保留候选标签而不填写正式层数；不能证明已排除
其他厚度。参考数据改变后模型标记为过期，识别要求重新训练，不静默回退。
使用训练内照片或同一分组时返回 `training_sample`，不会当作独立验证。
详情及完整状态规则见 [训练与参数协议](flake_training.md)。

**只有从未训练过的配置**使用以下旧参考基线。该基线为最近类别中心匹配。
同一照片内同一厚度的多个区域先取平均，
每张照片等权参与统计；大量划区不会增加独立照片数。当前查询照片的内容哈希会从
参考集中排除。同一次采集的不同处理导出仍应由用户避免重复计为独立照片。

类别优先按层数分组；只有 nm 标签时按标注的厚度值分组，不自动把 nm 换算为层数。
类别各通道标准差下限为 1 个百分点，标准化 RMS 距离大于 3 拒绝匹配，
第一与第二候选距离差小于 0.75 视为歧义。这些是启发式阈值，不是物理公差或置信概率。
只匹配已有标定类别，不外推未见过的层数，也不连续插值 nm 厚度。

| 状态 | 含义 |
|---|---|
| `match` | 未训练基线：候选至少有 3 张其他参考照片，至少 2 类，距离/间隔合格且无质量标志；训练模型使用各自拟合的规则 |
| `no_reference` | 当前 Profile 没有可用参考数据 |
| `insufficient_reference` | 候选照片不足 3 张或只有一个厚度类别 |
| `ambiguous` | 多种厚度的颜色相近，不能可靠区分 |
| `out_of_distribution` | 颜色超出当前标定范围 |
| `needs_review` | 背景或待测区域有质量问题，需要复查 |
| `target_like` | 仅训练模型：与单一目标相似，其他厚度未被排除 |
| `training_sample` | 仅训练模型：照片或分组参与过训练，不能当作独立识别 |

只有 `match` 填写返回值的 `layers` / `thickness_nm`；其他状态返回 null，并可能提供
`candidate_layers` / `candidate_thickness_nm` 供查看。nm-only 类别的层数始终为 null。
光学颜色可能不唯一，未覆盖的厚度也可能误匹配；实际精度需用独立测量厚度的显微镜
照片验证，不能把当前合成图测试当作实验精度评估。

## 本地 Python 分析接口

`flake_analysis` 与 `flake_photo_store` 不依赖 Qt 或实验室配置。显式传入数据库路径
即可使用；不传路径时读取 `VAULT_DB_PATH/.labdb/flake_photo_library.db`。

```python
from pathlib import Path
from pydatavault.flake_analysis import FlakeAnalysisService
from pydatavault.flake_imaging import decode_image, masks_from_polygons

service = FlakeAnalysisService(r"D:\lab\.labdb\flake_photo_library.db")
profiles = service.store.list_profiles()
profile_id = profiles[0]["profile_id"]  # 按实际材料、基底和成像配置选择
rgb = decode_image(Path("unknown.png").read_bytes())

# 示例顶点，必须替换为用户在当前照片中选择的坐标。
labels, background = masks_from_polygons(
    rgb.shape[:2],
    substrate_polygons=[[[10, 10], [80, 10], [80, 40], [10, 40]]],
    flake_polygons=[[[100, 100], [160, 100], [150, 160], [110, 150]]],
)
result = service.analyze(rgb, profile_id, labels=labels, substrate_mask=background)
for region in result["regions"]:
    print(region["region_id"], region["status"], region["layers"])
```

`analyze(image, profile_id, *, substrate_mask, labels, training_group_id=None)` 接受原图字节或 `H×W×3 uint8`
RGB 数组。两个掩膜必须显式提供，缺失时抛出 `ValueError`，不会自动猜测。
`labels` 为整型数组，正数为区域 ID、0 为未选区域、-1 为忽略；`substrate_mask`
为同尺寸布尔数组。`masks_from_polygons` 按 flake 多边形顺序分配 ID 1、2……。
返回图像尺寸 `[width, height]`、Profile、处理版本、背景 RGB、质量标志，以及每区
面积、像素质心、包围框 `[x0, y0, x1_exclusive, y1_exclusive]`、颜色特征和判别结果。
结果还包含 `inference_method`、`model_id`、`model_revision`、`model_mode`、`channels`。
`service.train(profile_id, channels="auto")` 训练并保存新版本；
`service.store.get_model(profile_id)` 读取最新参数，不会触发训练。

本地 `FlakePhotoStore` 提供 `ensure_profile`、`save_photo`、`list_photos`、`load_photo`。
目前 `save_photo` 仍从原图和掩膜重新计算特征；更新必须携带 `expected_revision`。
它不是未来允许显微镜端上传任意版本特征的纯存取接口。

## 保留的本机 HTTP 分析原型

```bash
python -m pydatavault.flake_api --db /path/to/flake_photo_library.db --port 51237
# 安装后的入口：pydatavault-flake-api
```

默认端口已为 **51237**，避开显微镜图像服务 51236。仅绑定 `127.0.0.1`，
独立于 GUI，一次处理一个请求；不提供远程认证/CORS 或 HTTP 写库能力。
JSON 请求体上限为 46 MiB，不接收客户端本地文件路径。

| 方法与路径 | 用途 |
|---|---|
| `GET /v1/health` | API 与处理版本 |
| `GET /v1/profiles` | 配置 ID、材料、基底、成像配置与照片数 |
| `GET /v1/profiles/{profile_id}/model` | 最新训练参数及过期标志，未训练时 404 |
| `GET /v1/models/{model_id}` | 固定 ID 的训练参数，含历史版本 |
| `POST /v1/analyze` | 读取参考样本，对显式多边形进行归一化和判别 |

```python
import base64
import json
from pathlib import Path
from urllib.request import Request, urlopen

payload = {
    "profile_id": 1,
    "image_base64": base64.b64encode(Path("unknown.png").read_bytes()).decode("ascii"),
    "substrate_polygons": [[[10, 10], [80, 10], [80, 40], [10, 40]]],
    "flake_polygons": [[[100, 100], [160, 100], [150, 160], [110, 150]]],
}
request = Request("http://127.0.0.1:51237/v1/analyze",
                  data=json.dumps(payload).encode("utf-8"),
                  headers={"Content-Type": "application/json"})
with urlopen(request, timeout=120) as response:
    result = json.load(response)
```

响应在 Python 接口结果上增加 `api_version: 1`。缺少背景/flake 多边形、越界、自交
或区域重叠返回 400。旧自动分区参数 `options` 和 `substrate_rect` 已移除，提交时
明确返回 400，不会静默忽略。缺少/超限请求体返回 413，未知路径 404，临时 SQLite
错误 503。这是早期原型的输入变更；motion-control 新集成以独立存取协议为准。
已训练模型过期时分析返回 409、`code=model_stale`。参数下载接口已实现，
其字段、评分公式和 motion-control 离线复现示例见 [参数 API](flake_training.md)。

## 保存、备份与后续集成

独立的 `.labdb/flake_photo_library.db` 保存原图字节、压缩的区域/背景/归一化数组、
特征、真实标签、标签来源、处理版本、时间戳和 revision。保存为一个事务，同一
Profile 下重复图像内容被拒绝。revision 用于并发校验，不是所有历史版本的归档。
备份使用 SQLite 快照并包含 WAL 中已提交数据及训练模型版本。旧点采样标定库继续独立存在。

模块边界：`flake_photo_widget` 负责用户手动画区，`flake_analysis` 负责本地查询
和判别，`flake_imaging` 提供归一化与颜色特征，`flake_training` 负责训练和可移植模型推断。
数据库最终的对外接口只负责存取（包括已训练参数）；
自动找 flake、自动边缘识别和分区由 motion-control 实现。本地小工具不会向存取
协议增加必须调用的 `/predict` 或 `/analyze` 服务。未来若两边共享归一化/判别代码，
应使用无 Qt 依赖的处理模块，并明确相同 `feature_schema` 和 `processing_key`；
仅算法名称相同并不保证特征兼容。
