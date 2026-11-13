#!/usr/bin/env python3
"""Extract and score model answers against the SpheraQA question bank."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import threading

from evaluation.client import JudgeClient, MODEL, strict_json, digest
from evaluation.core import SemanticScorer, RUBRIC, TRAJECTORY_RUBRIC
from evaluation.prompts import EXTRACTION_SYSTEM, SEMANTIC_SYSTEM
from evaluation.reasoning import final_answer, validate_extraction, preliminary_score, complete_score, semantic_request
from evaluation.temporal import check_time_binding

LOCAL = threading.local()


def rows(path):
    with Path(path).open() as stream:
        for number, line in enumerate(stream, 1):
            if line.strip():
                row = strict_json(line)
                if not isinstance(row, dict): raise ValueError(f"line {number} is not a JSON object")
                yield row


def prediction_fingerprint(prediction):
    return digest({"present": prediction is not None,
                   "raw": prediction["predicted_answer"] if prediction else "",
                   "capped": prediction.get("capped", False) if prediction else False})


def scoring_fingerprint():
    root = Path(__file__).resolve().parent
    files = [root / "generate_prediction.py", *sorted((root / "evaluation").glob("*.py"))]
    return digest({str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files})


def semantic_validation(payload, result):
    if not isinstance(result, dict) or type(result.get("match")) is not bool:
        raise ValueError("semantic match must be boolean")
    evidence = result.get("evidence")
    if not isinstance(evidence, str) or (evidence and evidence not in payload["candidate_evidence"]):
        raise ValueError("semantic evidence is absent from the answer")
    if result["match"] and not evidence: raise ValueError("positive semantic match has no evidence")
    if not isinstance(result.get("reason"), str): raise ValueError("semantic reason is missing")


def score_question(question, prediction, client):
    raw = prediction["predicted_answer"]
    capped = prediction.get("capped", False)
    if type(capped) is not bool: raise ValueError("capped must be boolean")
    receipts = []
    if question["level"] == "L3":
        components = question["scoring"]["components"]
        source_components = [spec for spec in components if spec["kind"] in {"source", "source_set"}]
        extracted_components = [spec for spec in components if spec["kind"] not in {"source", "source_set"}]
        public = {"item_id": question["id"], "template_key": question["task"], "question": question["question"],
                  "components": [{key: value for key, value in spec.items()
                                  if key in {"component_id", "kind", "quantity", "description", "allowed_values"}}
                                 for spec in extracted_components]}
        candidate = final_answer(raw)
        payload = {"item_id": question["id"], "question": question["question"],
                   "candidate": candidate, "components": public["components"]}
        # Numerical/category claims are extracted without reference answers.
        # Source identity is judged directly from the complete original answer.
        if extracted_components:
            extraction, receipt = client.complete(EXTRACTION_SYSTEM, payload,
                lambda value: validate_extraction(public, candidate, value))
            receipts.append(receipt)
        else:
            extraction = {"answer_status": "answered" if candidate else "no_final_answer",
                          "contradiction": False, "contradiction_evidence": "", "components": []}
        reference = {"item_id": question["id"], "components": extracted_components}
        preliminary = preliminary_score(public, reference, candidate, extraction)
        for spec in source_components:
            preliminary["semantic_requests"].append(semantic_request(public, spec, {"value": candidate, "evidence": candidate}))
            preliminary["components"].append({"component_id": spec["component_id"], "kind": spec["kind"],
                "correct": None, "reason": "pending_raw_source_match", "candidate_value": candidate,
                "evidence": candidate, "source_matching_input": "complete_final_answer"})
        order = {spec["component_id"]: index for index, spec in enumerate(components)}
        preliminary["components"].sort(key=lambda value: order[value["component_id"]])
        preliminary["contract_sha256"] = digest({"item_id": question["id"], "components": components})
        semantic = {}
        for request in preliminary["semantic_requests"]:
            answer, receipt = client.complete(SEMANTIC_SYSTEM, request,
                lambda value, request=request: semantic_validation(request, value))
            semantic[request["check_id"]] = answer
            receipts.append(receipt)
        decision = complete_score(preliminary, semantic)
        if capped:
            decision["correct"] = False
            decision["score"] = 0
            decision["reason"] = "generation_limit_reached"
        return {"score": int(decision["correct"]), "extraction": extraction,
                "decision": decision, "judge_model": MODEL, "judge_receipts": receipts}
    if not hasattr(LOCAL, "scorer"): LOCAL.scorer = SemanticScorer()
    scorer = LOCAL.scorer
    public = {"item_id": question["id"], "category": question["task"],
              "score_question": question["question"], "query_schema": question["scoring"]["query_schema"]}
    route = scorer.route(public, raw, capped)
    extraction = None
    if route["kind"] == "cpu":
        claim = route["claim"]
    else:
        payload = route["payload"]
        rubric = TRAJECTORY_RUBRIC if question["task"] == "trajectory" else RUBRIC
        extraction, receipt = client.complete(rubric, payload, lambda value: scorer.validate(payload, value))
        receipts.append(receipt)
        if route["kind"] == "llm_binding":
            claim = route["claim"] if extraction["status"] == "complete" and extraction["source_matches_query"] else {
                "ok": False, "value": None, "reason": "source_binding_not_satisfied"}
        else:
            claim = scorer.claim(payload, extraction)
    decision = scorer.core.score_claim(question["task"], claim, question["scoring"]["reference_claim"])
    time_error = check_time_binding(question, raw)
    if time_error: decision = {"success": 0, "reason": time_error}
    return {"score": int(decision["success"]), "claim": claim, "extraction": extraction,
            "decision": decision, "judge_model": MODEL if receipts else None, "judge_receipts": receipts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", "-i", type=Path, required=True)
    parser.add_argument("--output", "-o", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).with_name("spheraqa.jsonl"))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--level", choices=("L1", "L2", "L3", "all"), default="all")
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.workers < 1: parser.error("workers must be positive")
    dataset = list(rows(args.dataset)); all_ids = {q["id"] for q in dataset}
    if len(all_ids) != len(dataset): raise ValueError("duplicate dataset ID")
    selected = [q for q in dataset if args.level == "all" or q["level"] == args.level]
    if not selected: raise ValueError("selected question set is empty")
    predictions = {}
    for row in rows(args.input):
        identity = row.get("id", row.get("question_id", row.get("item_id")))
        if identity not in all_ids or identity in predictions: raise ValueError("duplicate or unknown prediction ID")
        answer = row.get("predicted_answer", row.get("response"))
        if not isinstance(answer, str): raise ValueError("predicted_answer must contain the raw model response as text")
        predictions[identity] = {"predicted_answer": answer, "capped": row.get("capped", False)}
    dataset_sha = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
    scoring_sha = scoring_fingerprint()
    client = JudgeClient(args.cache_dir or Path(str(args.output) + ".calls"))
    done = {}
    if args.resume and args.output.exists():
        for result in rows(args.output):
            if result.get("dataset_sha256") != dataset_sha or result.get("scope") != args.level or result.get("scoring_sha256") != scoring_sha:
                raise ValueError("resume requires the same dataset, scoring code and selected level")
            identity = result["id"]
            if result.get("status") in {"scored", "missing_prediction"}:
                if result["prediction_sha256"] != prediction_fingerprint(predictions.get(identity)):
                    raise ValueError("prediction text, presence or capped status changed during resume")
                done[identity] = result
    elif args.output.exists():
        raise ValueError("output already exists; use --resume or a new path")
    selected_ids = {q["id"] for q in selected}
    if set(done) - selected_ids: raise ValueError("resume output contains an unselected question")

    def work(question):
        identity = question["id"]
        prediction = predictions.get(identity)
        raw = prediction["predicted_answer"] if prediction else ""
        result = {"id": identity, "level": question["level"], "task": question["task"],
                  "split": question["split"], "dataset_sha256": dataset_sha, "scope": args.level,
                  "scoring_sha256": scoring_sha, "prediction_sha256": prediction_fingerprint(prediction)}
        if prediction is None:
            return {**result, "status": "missing_prediction", "score": 0, "judge_receipts": []}
        try:
            return {**result, "status": "scored", **score_question(question, prediction, client)}
        except Exception as exc:
            return {**result, "status": "unresolved", "score": None,
                    "error": f"{type(exc).__name__}: {str(exc)[:200]}"}

    args.output.parent.mkdir(parents=True, exist_ok=True)
    failed = 0
    # Rewrite only the already verified completed records, then append work.
    # This replaces earlier unresolved attempts without duplicating item IDs.
    with args.output.open("w") as output:
        for q in selected:
            if q["id"] in done:
                output.write(json.dumps(done[q["id"]], ensure_ascii=False, allow_nan=False) + "\n")
        output.flush()
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            jobs = [pool.submit(work, q) for q in selected if q["id"] not in done]
            completed = len(done)
            for job in as_completed(jobs):
                result = job.result(); completed += 1
                failed += result["status"] == "unresolved"
                output.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n"); output.flush()
                if completed % 25 == 0 or completed == len(selected):
                    print(json.dumps({"completed": completed, "total": len(selected), "unresolved": failed}), flush=True)
    return 1 if failed else 0


if __name__ == "__main__": raise SystemExit(main())
