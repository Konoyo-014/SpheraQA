# SpheraQA

[简体中文](README_zh.md)

SpheraQA is a benchmark for spatial-audio understanding. It contains **22,197 questions and 7,686 first-order Ambisonics (FOA) recordings**, covering spatial perception, relations between sound sources, and reasoning about changes in position and orientation.

| Level | Tasks | Questions |
| --- | --- | ---: |
| L1 — Perception | Direction, distance, coordinates, source count, motion and trajectories | 18,223 |
| L2 — Relations | Relative position, distance comparison and navigation | 2,149 |
| L3 — Reasoning | Source identification, rotations, distance changes, source selection and geometric reasoning | 1,825 |
| Total | | 22,197 |

The complete evaluation set is stored in `spheraqa.jsonl`. Each question includes its audio path, reference answer and scoring specification. [DATASET.md](DATASET.md) describes the recording conditions, question families and data fields.

## Get the dataset

The complete dataset is hosted on [ModelScope](https://modelscope.cn/datasets/Konoyo/SpheraQA). The repository contains the audio preparation, prompt export and evaluation code. Use Python 3.10 or newer for the ModelScope downloader. From this repository's root, download the question bank, media manifest and audio archives, then extract the recordings:

```bash
python3 -m pip install "modelscope>=1.40.3"
modelscope download Konoyo/SpheraQA --repo-type dataset --local-dir . --include spheraqa.jsonl media.jsonl audio_parts.json "audio/parts/*"
python3 prepare_audio.py
```

The 47 independent archives in `audio/parts/` total **19.95 GB**. They contain **26.61 hours of four-channel WAV audio**, occupying **24.52 GB after extraction**. `audio_parts.json` records each archive's exact size and SHA-256; `media.jsonl` provides the same information for each recording. The extraction script checks both. To verify an existing extraction, run `python3 prepare_audio.py --verify-only`.

All audio uses 16 kHz sampling and ACN/SN3D channel order **W, Y, Z, X**. Use the complete waveform with all four channels. Recordings for the controlled tasks include isolated reference sounds before the main mixture; those sounds are part of the model input.

## Evaluate a model

Use Python 3.9 or newer. The supplied scripts use the Python standard library.

Export the questions and audio paths for inference:

```bash
python3 make_prompts.py --output prompts.jsonl
```

For each row, give your model the `question` text and the complete waveform at `audio_path`. Audio paths are relative to the repository root. Preserve `id` to associate the answer with its question. The exported file contains the model inputs; the reference answers and scoring fields remain in the question bank.

Save one original model response per line in `model_answers.jsonl`:

```json
{"id":"spheraqa_...","predicted_answer":"The model's original answer","capped":false}
```

Use the exact question ID from `prompts.jsonl`. Set `capped` to `true` if generation reached its output limit.

Copy `.env.example` to `.env` and enter your Chat Completions API key and base URL. Load the configuration, score the answers, and compute accuracy:

```bash
set -a
source .env
set +a

python3 generate_prediction.py --input model_answers.jsonl --output scored.jsonl --workers 8
python3 compute_acc.py --input scored.jsonl --show-subcategory --output summary.json
```

The judge model is **`gpt-4o-2024-11-20`**. Every supplied L3 answer is processed by this model. L1 and L2 use numerical and categorical rules, with the same model extracting free-text answers when needed. Numerical comparisons are performed by the scoring code.

To evaluate one level, pass the same `--level L1`, `--level L2` or `--level L3` option to `make_prompts.py` and `generate_prediction.py`. The summary script reads the selected level from the scoring file. Continue an interrupted scoring run with `generate_prediction.py --resume` and the same input and output paths.

## Scoring

Accuracy is the proportion of questions for which every required answer component is correct. Azimuth uses circular error with a 20° tolerance; elevation allows 10°, distance allows 1 m, and shortest angular separation allows 20°. Trajectories must cover all requested times, with a 0.051-second allowance for rounded timestamps. Explicit answer times and time ranges must match the question.

Missing answers and generation-limit responses receive zero. API failures remain unresolved and must be retried before computing accuracy. The scoring file records each decision and the GPT replies used for it.

## Reference result

| Model | Level | Correct / total | Accuracy |
| --- | --- | ---: | ---: |
| SpheraAudio | L3 | 436 / 1,825 | 23.89% |

[examples/](examples/) contains the complete responses, per-question scores, recorded GPT replies and [model settings](examples/model_metadata.json) for this result. It uses the SpheraAudio joint-SFT checkpoint at step 1633, with greedy generation and complete FOA recordings. All 1,825 responses finished without reaching the generation limit.

Recompute these scores from the recorded replies without an API key or network access:

```bash
python3 replay_example.py --output results/replayed_l3.jsonl
python3 compute_acc.py --input results/replayed_l3.jsonl --show-subcategory
```

The code is distributed under [Apache-2.0](LICENSE). Recording credits and source links for the FSD50K audio used in the controlled scenes are provided in [ATTRIBUTION.md](ATTRIBUTION.md).
