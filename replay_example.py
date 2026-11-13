#!/usr/bin/env python3
"""Recompute the supplied SpheraAudio L3 example entirely from recorded replies."""
import argparse
import json
from pathlib import Path
import re
import sys
import tempfile
import evaluation.client as transport
from evaluation.client import digest, MODEL
import generate_prediction


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("results/replayed_l3.jsonl"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="spheraqa-judgements-", dir=args.output.parent) as temporary:
        cache = Path(temporary)
        with (root / "examples/judgements.jsonl").open() as source:
            for line in source:
                entry = json.loads(line); key, record = entry["key"], entry["record"]
                if not re.fullmatch(r"[0-9a-f]{64}", key) or digest(record["request"]) != key:
                    raise ValueError("Recorded request hash differs")
                if record["requested_model"] != MODEL or record["reported_model"] != MODEL or record["finish_reason"] != "stop":
                    raise ValueError("Recorded reply does not match the required model and completion status")
                path = cache / (key + ".json")
                if path.exists(): raise ValueError("Duplicate recorded request")
                path.write_text(json.dumps(record, ensure_ascii=False) + "\n")
        def no_network(*args, **kwargs):
            raise RuntimeError("The offline example is missing a required recorded judgement")
        transport.urlopen = no_network
        sys.argv = ["generate_prediction.py", "--input", str(root / "examples/spheraaudio_l3_answers.jsonl"),
                    "--output", str(args.output), "--dataset", str(root / "spheraqa.jsonl"),
                    "--level", "L3", "--workers", "8", "--cache-dir", str(cache)]
        return generate_prediction.main()


if __name__ == "__main__": raise SystemExit(main())
