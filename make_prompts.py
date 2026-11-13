#!/usr/bin/env python3
"""Export only questions and audio paths for model inference."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).with_name("spheraqa.jsonl"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--level", choices=("L1", "L2", "L3", "all"), default="all")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with args.dataset.open() as source, args.output.open("x") as output:
        for line in source:
            q = json.loads(line)
            if args.level != "all" and q["level"] != args.level: continue
            row = {key: q[key] for key in ("id", "audio_path", "question")}
            row["audio_format"] = {"sample_rate": 16000, "channels": 4, "channel_order": ["W", "Y", "Z", "X"], "normalization": "SN3D"}
            output.write(json.dumps(row, ensure_ascii=False) + "\n"); count += 1
    print(json.dumps({"questions": count, "output": str(args.output)}))


if __name__ == "__main__": main()
