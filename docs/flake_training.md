# Flake 标定训练与识别参数 API

状态：**本地训练、模型持久化、GUI 识别和下述 `/v1` 参数读取接口已实现。**
通用照片/掩膜上传下载的 `/api/v1` 存取协议仍按 [存取接口文档](flake_storage_api.md)
另行实现。本文中的模型是可复现的 JSON 数值参数，不是 pickle 或可执行模型文件。

## 1. 用户流程

1. 在 **Tools → Flake Photo Library…** 选择材料、基底和成像配置组成的 Profile。
   同一种材料在不同氧化层厚度、物镜/NA、相机或光源条件下应使用不同 Profile。
2. 逐张导入标定照片，画背景和厚度均匀的区域，填写已知层数/nm 和标签来源，然后
   **Save as labeled reference photo**。同一照片可以包含多个厚度。
3. 同一块物理 flake 的重复拍摄，在 **Flake / training group** 填写相同 ID。
   未填写时，以解码后的图像哈希视为独立组，并产生
   `ungrouped_photos_assumed_independent` 提示；程序无法自行知道两张照片是否来自同一 flake。
4. 每个厚度至少提供 **3 个可用独立组**。这是当前小模型的最低工程门槛，
   不代表足以验证实验精度。存在背景/区域质量标志的数据不会用于训练。
5. **Training channels** 默认 Auto，也可指定 R、G、B、RG、RB、GB 或 RGB。
   点击 **Train / update profile**，只训练已保存的数据，不包含当前未保存的标签/选区。
   训练在后台线程运行，成功后创建新的参数版本。失败不覆盖旧版本。
6. 导入待测照片、手动画区，点击 **Estimate thickness** 使用当前参数。
   左侧显示模式、通道、版本、独立组数及训练诊断；**Export trained parameters…**
   可导出与 API 相同的模型对象。

训练成功后，参考照片新增或 revision 变化会使该 Profile 的旧模型过期；重新训练
后才能继续用它识别。另一材料/Profile 的样本修改不会使当前模型过期。
旧模型仍可读取用于追溯，但不会自动回退到旧版本。

## 2. 多厚度与单一目标厚度

### 多厚度 `multiclass`

根据已有类别分类。优先以整数层数作为类别；只提供 nm 时，以精确标注的 nm 数值
作为离散类别，不自动分箱、插值或换算层数。nm-only 的重复测量如需归为同类，
应先由用户确定一致的标定类别。层数类别中附带的 nm 输出为该类标注的中位数。

Auto 比较 7 种通道组合，用按独立组留一的诊断选择组合：先比较宏平均分类正确率，
相同则优先较少通道，再比较带正误符号的分类间隔中位数。
每个候选都从训练折拟合参数，同一组在各厚度类别中同时被留出。
论文中某材料的推荐通道不作为硬编码规则。

### 单一厚度 `single_target`

只标定一种厚度时，训练仍然可用：学习其颜色分布与接受范围。
Auto 保留 RGB，也允许用户按已知实验条件手动选通道。只有正样本无法检验
哪个通道能最好地排除其他厚度，因此不会伪造自动通道优劣或分类准确率。

- 范围内：`target_like`，`is_target_like=true`，候选标签在 `candidate_layers` /
  `candidate_thickness_nm`，正式 `layers` / `thickness_nm` 为 null。
- 范围外：`out_of_distribution`，`is_target_like=false`，不猜测另一种厚度。
- 质量不合格或训练内样本：`is_target_like=null`，需要复查。

`target_like` 表示与目标参考相似，**不能证明已排除所有其他厚度**。仅正样本
无法测出误接受率，所以 `false_positive_rate` 始终为 null。

## 3. 参数的拟合方法

输入为现有处理版本 `substrate-ratio-v1` 的 `normalized_rgb`：

```text
x[c] = median(100 * flake_pixel[c] / median(selected_substrate[c]))
```

通道顺序为 RGB，背景等于 100，单位为百分比；不要传入 0–1 RGB、BGR、灰度、
显示预览或 `1 - ratio` 对比度。区域内部的采样/边界排除与
[照片处理说明](flake_photo_library.md) 相同。

同一照片、同一厚度的区域先平均；同组照片再平均。最后每个独立组等权参与拟合。
这样同一张照片画更多区域，或同一 flake 多拍几张，不会增加它在模型中的权重。
所选 p 个通道的类别均值为 μ，组间样本协方差为 S（分母 n−1）：

```text
Σ = 0.5 * S + 0.5 * diag(diag(S)) + I * (1 percentage point)^2
precision = inverse(Σ)
log_determinant = log(det(Σ))
```

保留部分通道相关性，并用对角收缩及噪声下限稳定小样本矩阵。每类接受距离上限：

```text
distance_limit = clip(1.1 * quantile(留组预测的真实类别距离, 0.95), 2.0, 4.0)
```

每次留出一个组，使用其他组的参数计算该组距离；最终均值/协方差使用全部可用组。
这些收缩系数、噪声下限、距离上下限及间隔阈值是本实现的工程默认值，
不是文献提供的通用物理常数，也不是置信概率。

训练报告包含各通道组合的结果、混淆矩阵、各类正样本保留率、接受覆盖率。
**同一数据用于选择通道、估计距离阈值和报告诊断，因此报告不是独立测试成绩。**
`validation.independent_test=false`；正样本保留率或多类别宏平均正确率低于 0.8
时，`decision.requires_review=true`，模型只提供待复核候选。
独立于训练的新 flake/批次仍需用于实验验证。

## 4. API：已实现

启动命令：

```bash
python -m pydatavault.flake_api --db /path/to/flake_photo_library.db --port 51237
```

仅本机 `127.0.0.1`。这些 HTTP 接口读取参数，不触发训练，不重新拟合，也不写库。
训练由 GUI 或 Python `FlakeAnalysisService.train(profile_id, channels="auto")` 执行。

| 方法与路径 | 用途 |
|---|---|
| `GET /v1/health` | `capabilities` 包含 `trained_model_export`，并返回 `model_schema` |
| `GET /v1/profiles` | 读取材料/基底/成像配置，获得当前整数 `profile_id` |
| `GET /v1/profiles/{profile_id}/model` | 读取该配置的最新参数及当前过期状态 |
| `GET /v1/models/{model_id}` | 按 ID 读取某次训练参数，包括历史版本 |
| `POST /v1/analyze` | 手动多边形分析；优先使用已训练参数，结果包含模型 ID/版本/模式/通道 |

两种模型读取接口成功均返回 `{"api_version": 1, "model": {...}}`。
不存在的模型/尚未训练的配置返回 404；SQLite 暂不可用返回 503。
模型过期时 GET 仍为 200，并设置 `model.stale=true`；`/v1/analyze` 则返回
409 与 `{"code":"model_stale","error":"..."}`，不会静默改用旧基线。
从未训练过的配置仍使用旧参考基线，响应 `inference_method="reference_baseline"`；
有模型时为 `trained_model`。要获得单一目标模式必须先训练。

### 4.1 `model` 字段

| 字段 | 约定 |
|---|---|
| `model_schema` | `pydatavault.flake-gaussian.v1` |
| `algorithm` / `algorithm_version` | `regularized_gaussian` / 整数 `1` |
| `model_id` / `revision` / `created_at` | 当前原型为整数 ID；revision 在每个 Profile 内递增，时间为 UTC |
| `profile` | `profile_id`、`material`、`substrate`、`optical_setup`，必须与待测条件匹配 |
| `processing_version` | `substrate-ratio-v1` |
| `feature_schema` | `microscope.rgb-ratio-percent.v1` |
| `processing_key` | `rgb8-bg-median-region-median-erode1-v1` |
| `feature_units` | `substrate_equals_100_percent` |
| `channels` | 如 `["R","B"]`；数组/矩阵均按此顺序排列 |
| `mode` | `multiclass` 或 `single_target` |
| `classes` | 类别参数列表，详见下表 |
| `decision` | 评分规则、最小间隔、正则化参数和 `requires_review` |
| `training` | 数据指纹、源照片 revision、图像哈希、分组、数量、通道选择、诊断及验证信息 |
| `stale` / `current_data_fingerprint` | 读取时检查的动态状态；其余训练参数及来源不会原地改写 |
| `literature` | 方法参考 DOI；不表示其作者验证过本程序或当前训练数据 |

`classes[]`：

| 字段 | 约定 |
|---|---|
| `class_id` | 如 `layers:2` 或 `nm:8.5`，仅作为该模型中的类别标识 |
| `layers` / `thickness_nm` | 已知类别标签，可有一个为 null，不隐式换算 |
| `mean` | p 元向量，归一化 RGB 百分比 |
| `covariance` / `precision` | p×p 矩阵，precision 为 covariance 的逆 |
| `log_determinant` | covariance 行列式的自然对数 |
| `distance_limit` | 此类别的 Mahalanobis RMS 接受距离上限 |
| `training_groups` / `reference_photos` | 独立组数 / 该类使用的不同图像数，前者用于最低样本要求 |

`training.data_fingerprint` 是当前 Profile 和全部源照片 ID/revision/哈希/处理版本
的规范 JSON SHA-256；`source_photos` 包括未通过质量筛选的照片。任何源 revision
变化都要求重新训练。`photo_hashes` / `groups` 则只包含实际参与训练的数据。
分组格式为 `user:<用户填写的ID>` 或 `image:<解码图像哈希>`。
`training.channels_requested` 保留 Auto/手动选择；`channel_selection` 为
`grouped_leave_one_out`、`positive_only_keep_channels` 或 `manual`。

### 4.2 motion-control 本地判别的复现规则

客户端需要先校验协议版本、Profile、特征格式/处理版本/单位，并拒绝 `stale=true`。
不能只按材料名称选模型，也不能从模型 JSON 自动推断输入图片的处理流程。

对每区的 RGB 特征按 `channels` 选出 x。对每个类别计算：

```text
δ = x - mean
q = max(0, δᵀ * precision * δ)
distance = sqrt(q / p)
score = 0.5 * (q + log_determinant)
```

类别先验相等；省略了在同一模型各类间相同的高斯常数项。取 score 最小的类别，
完全相同时按 `classes` 列表顺序。多类 `margin = 第二小score - 最小score`；
单类 margin 为 null。score/margin 不应转换成“已校准准确率”。

状态按以下优先级确定：

1. 当前图像哈希或明确的 flake 分组已参与训练：`training_sample`，不作为独立识别。
2. 背景/区域存在质量标志，或 `decision.requires_review=true`：`needs_review`。
3. 候选 distance 大于其 distance_limit：`out_of_distribution`。
4. 多类 margin 小于 `decision.minimum_margin`（当前为 0.5）：`ambiguous`。
5. 其余多类为 `match`；单类为 `target_like`。

只有 `match` 的 `layers` / `thickness_nm` 填写正式匹配标签；其他状态均为 null，
候选标签仍可查看。单类 `target_like` 只能用于候选筛选，不应自动当作人工真值。
HTTP `/v1/analyze` 可额外传入 `training_group`，值为用户原始 ID，不带 `user:`。

Python 客户端示例（motion-control 可以复用此无 GUI 模块，或按以上公式实现）：

```python
import json
from urllib.request import urlopen
from pydatavault.flake_training import predict_with_model, training_group
from pydatavault.flake_imaging import image_digest

profile_id = 1  # 通过 /v1/profiles 按实际材料、基底、成像配置选择
with urlopen(f"http://127.0.0.1:51237/v1/profiles/{profile_id}/model", timeout=10) as response:
    envelope = json.load(response)
assert envelope["api_version"] == 1
model = envelope["model"]
assert model["profile"]["profile_id"] == profile_id
if model["stale"]:
    raise RuntimeError("Reference data changed; retraining is required")

# rgb: EXIF 方向校正后的 uint8 RGB；features: 同一处理版本的区域特征列表。
# each feature 至少包含 region_id、normalized_rgb、warnings。
results = predict_with_model(
    features, model, quality_warnings=background_warnings,
    image_hash=image_digest(rgb), group=training_group(known_flake_id, image_digest(rgb)),
)
```

读取后可以缓存整个模型用于一次扫描，记录 `model_id/revision/data_fingerprint`。
每次新扫描重新读取最新模型及过期标志；离线 JSON 无法知道之后数据库是否变化。
图像哈希使用 `flake_imaging.image_digest`：对 ASCII 形式的 RGB shape 元组和
连续 C-order uint8 RGB 字节按顺序计算 SHA-256，不是原始压缩图片文件的哈希。

## 5. 存储、版本和文献依据

模型保存在原照片 SQLite 的 `flake_models` 表中，数据库 schema 从 1 无损升级为 2。
每次训练新增一行，旧模型保留；训练结束提交时再次检查数据指纹，若训练期间有
其他编辑者修改参考数据，则拒绝发布该次模型。SQLite 备份会同时包含模型。
仅模型拟合方式改变时升级模型算法版本；改变图像/特征含义则必须另改 processing key。

- [Wang 等，Nanotechnology 2012](https://doi.org/10.1088/0957-4484/23/49/495713)：
  通道适用性与材料/基底有关。因此保留三通道，并按 Profile 学习或手动指定通道。
- [Sterbentz 等，Scientific Reports 2021](https://pmc.ncbi.nlm.nih.gov/articles/PMC7970966/)：
  采用三维颜色分布、均值和协方差。本实现借鉴这一表示，但直接使用人工标签的
  区域与采集组拟合，不复现其自动分区/GMM 聚类，也不继承论文准确率。
- [Mondal 等，ACS Nano 2022](https://doi.org/10.1021/acsnano.2c04833)：
  普通 RGB 的相机处理可能影响强度比；多亮度斜率和 RAW 比值是其研究方向。
  本版本仍是 8-bit RGB 单图比值标定，不实现 RAW/多亮度斜率模式，不能声称已消除
  任意 gamma、光谱或相机设置差异。
