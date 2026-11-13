#!/usr/bin/env python3
"""Summarize a complete SpheraQA scoring file, preserving every denominator."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
from generate_prediction import rows, scoring_fingerprint


def metric(values):
    correct = sum(row["score"] for row in values)
    return {"correct": correct, "total": len(values), "accuracy_percent": 100 * correct / len(values) if values else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", "-i", type=Path, required=True)
    parser.add_argument("--output", "-o", type=Path)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).with_name("spheraqa.jsonl"))
    parser.add_argument("--show-subcategory", "-s", action="store_true")
    args = parser.parse_args()
    results = list(rows(args.input))
    if not results: raise ValueError("empty result file")
    scope = results[0]["scope"]
    if scope not in {"all", "L1", "L2", "L3"}: raise ValueError("invalid result scope")
    source = {q["id"]: q for q in rows(args.dataset) if scope == "all" or q["level"] == scope}
    dataset_sha = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
    scoring_sha = scoring_fingerprint()
    ids = [r["id"] for r in results]
    if len(ids) != len(set(ids)) or set(ids) != set(source): raise ValueError("results must cover every selected question exactly once")
    for result in results:
        q = source[result["id"]]
        if result["scope"] != scope or result["dataset_sha256"] != dataset_sha: raise ValueError("dataset or scope mismatch")
        if result.get("scoring_sha256") != scoring_sha: raise ValueError("results use a different scoring implementation")
        if result["status"] not in {"scored", "missing_prediction"} or type(result["score"]) is not int or result["score"] not in {0, 1}:
            raise ValueError("unresolved judgements remain; resume scoring before computing accuracy")
        if any(result[key] != q[key] for key in ("level", "task", "split")): raise ValueError("result metadata differs from the question bank")
    summary = {"dataset_sha256": dataset_sha, "scope": scope, "overall": metric(results)}
    for field in ("level", "task"):
        groups = defaultdict(list)
        for row in results: groups[row[field]].append(row)
        summary["by_" + field] = {key: metric(values) for key, values in sorted(groups.items())}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary if not args.show_subcategory else {k:v for k,v in summary.items() if k != "by_task"}, ensure_ascii=False, indent=2))
    if args.show_subcategory:
        print("task\tcorrect\ttotal\taccuracy_percent")
        for task, score in summary["by_task"].items():
            print(f"{task}\t{score['correct']}\t{score['total']}\t{score['accuracy_percent']:.4f}")


if __name__ == "__main__": main()
