# SpheraQA

[English](README.md)

SpheraQA 是空间音频理解评测数据集，包含 **22,197 道题和 7,686 段一阶 Ambisonics（FOA）录音**，评测模型的空间感知、声源关系判断，以及位置和朝向变化的推理能力。

| 层级 | 任务内容 | 题数 |
| --- | --- | ---: |
| L1：空间感知 | 方向、距离、坐标、声源数量、运动和轨迹 | 18,223 |
| L2：空间关系 | 相对位置、距离比较和转向导航 | 2,149 |
| L3：空间推理 | 声源识别、旋转、距离变化、声源选择和几何推理 | 1,825 |
| 合计 | | 22,197 |

全部评测题保存在 `spheraqa.jsonl` 中。每题包含音频路径、参考答案和评分规则。[数据说明](DATASET.md)介绍了录音条件、题型及各字段的含义。

## 获取数据集

完整数据集保存在 [ModelScope](https://modelscope.cn/datasets/Konoyo/SpheraQA)。本仓库提供音频准备、题目导出和评分程序。ModelScope 下载工具需要 Python 3.10 或更新版本。在本仓库根目录下载题库、音频清单和压缩包，再解压录音：

```bash
python3 -m pip install "modelscope>=1.40.3"
modelscope download Konoyo/SpheraQA --repo-type dataset --local-dir . --include spheraqa.jsonl media.jsonl audio_parts.json "audio/parts/*"
python3 prepare_audio.py
```

`audio/parts/` 中的 47 个压缩包共约 **19.95 GB**，可以独立解压，包含 **26.61 小时的四通道 WAV 录音**，解压后占用约 **24.52 GB**。`audio_parts.json` 保存各压缩包的准确大小和 SHA-256，`media.jsonl` 保存各录音的对应信息，解压程序会逐一检查。已经解压的录音可用 `python3 prepare_audio.py --verify-only` 再次核对。

所有录音的采样率均为 16 kHz，采用 ACN/SN3D，通道依次为 **W、Y、Z、X**。模型需要读取完整的四通道波形。控制场景在主混合录音之前包含独立声源参考片段，这些片段也是模型输入的一部分。

## 评测模型

使用 Python 3.9 或更新版本。所附脚本只依赖 Python 标准库。

先导出模型需要的问题和音频路径：

```bash
python3 make_prompts.py --output prompts.jsonl
```

逐行读取 `prompts.jsonl`，把 `question` 中的问题文本和 `audio_path` 指向的完整波形交给模型。音频路径相对于仓库根目录；保留 `id`，用于对应问题与回答。导出文件提供模型输入，参考答案和评分字段保留在题库中。

将模型的原始回答写入 `model_answers.jsonl`，每行保存一个 JSON 对象：

```json
{"id":"spheraqa_...","predicted_answer":"模型的原始回答","capped":false}
```

`id` 必须与导出问题的 ID 完全相同。如果生成达到输出长度上限，将 `capped` 设为 `true`。

把 `.env.example` 复制为 `.env`，填写 Chat Completions API 的密钥和服务地址，然后加载配置、评分并统计准确率：

```bash
set -a
source .env
set +a

python3 generate_prediction.py --input model_answers.jsonl --output scored.jsonl --workers 8
python3 compute_acc.py --input scored.jsonl --show-subcategory --output summary.json
```

判分模型为 **`gpt-4o-2024-11-20`**，每条已提供的 L3 回答都会经过该模型。L1、L2 使用数值和类别规则；需要理解自由文本时，也由同一模型抽取回答内容。数值误差由评分程序计算。

只评测一个层级时，在 `make_prompts.py` 和 `generate_prediction.py` 中使用相同的 `--level L1`、`--level L2` 或 `--level L3` 参数。统计程序会从评分文件读取层级。评分中断后，使用相同的输入、输出路径，并在 `generate_prediction.py` 命令中加入 `--resume` 即可继续。

## 评分规则

准确率按全部必要答案成分均正确的题目比例计算。方位角使用圆周误差，允许误差为 20°；仰角为 10°，距离为 1 m，最短方位夹角为 20°。轨迹回答必须覆盖每个指定时刻，时间匹配允许 0.051 秒误差，以容纳时间戳的小数舍入。回答中明确写出的时刻和时间范围也必须符合题目要求。

缺失回答和达到生成长度上限的回答计零分。API 失败会保留为未完成状态，需要重试后才能统计准确率。逐题评分文件保存判定结果及实际使用的 GPT 回复。

## 参考结果

| 模型 | 层级 | 正确题数 / 总题数 | 准确率 |
| --- | --- | ---: | ---: |
| SpheraAudio | L3 | 436 / 1,825 | 23.89% |

[examples/](examples/)提供这次评测的完整回答、逐题分数、实际 GPT 回复和[模型设置](examples/model_metadata.json)。模型使用 SpheraAudio joint-SFT 第 1633 步权重，以贪心方式生成，并读取完整 FOA 录音。1,825 条回答均正常结束，没有达到生成长度上限。

以下命令使用保存的回复重新计算分数，无需 API 密钥或网络连接：

```bash
python3 replay_example.py --output results/replayed_l3.jsonl
python3 compute_acc.py --input results/replayed_l3.jsonl --show-subcategory
```

代码采用 [Apache-2.0](LICENSE)。控制场景使用的 FSD50K 录音，其作者署名和原始链接见 [ATTRIBUTION.md](ATTRIBUTION.md)。
