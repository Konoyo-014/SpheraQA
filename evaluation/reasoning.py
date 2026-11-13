"""Score every requested L3 component after extracting the model answer."""
import hashlib

import json

import math

import re

import unicodedata

class PendingJudgement(ValueError):
    pass

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()

def final_answer(raw):
    """Strip explicit private reasoning, but require no new answer syntax."""
    if not isinstance(raw, str):
        raise PendingJudgement("Model response is not text")
    if "</think>" in raw.lower():
        end = [m.end() for m in re.finditer(r"</think\s*>", raw, re.I)]
        return raw[end[-1]:].strip() if end else ""
    if re.search(r"<think\s*>", raw, re.I):
        return ""
    return raw.strip()

def normalized_source(text):
    return "".join(c.casefold() for c in unicodedata.normalize("NFKC", text)
                   if not c.isspace() and unicodedata.category(c)[0] not in {"P", "S"})

def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)

def numeric_evidence(spec, claim, candidate):
    """Require the stated number and a compatible explicit unit in its excerpt."""
    evidence = claim["evidence"]
    quantity = spec.get("quantity", "")
    angle = quantity in {"azimuth_deg", "elevation_deg", "angular_separation_deg"}
    pattern = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
    unit_pattern = re.compile(r"^\s*(°|%|degrees?|deg\b|radians?|rad\b|kilometers?|kilometres?|km\b|centimeters?|centimetres?|cm\b|millimeters?|millimetres?|mm\b|meters?|metres?|m\b|milliseconds?|ms\b|seconds?|sec\b|s\b|kilograms?|kg\b|hertz|hz\b)", re.I)
    candidates = []
    start = 0
    while True:
        start = candidate.find(evidence, start)
        if start < 0: break
        for match in re.finditer(pattern, evidence.replace("−", "-")):
            number = float(match.group())
            suffix = candidate[start + match.end():]
            found = unit_pattern.match(suffix)
            unit = found.group(1).lower() if found else ""
            factor = 1.
            if unit:
                if angle:
                    if unit.startswith("rad"): factor = 180 / math.pi
                    elif unit not in {"°", "deg", "degree", "degrees"}: continue
                elif quantity == "distance_m":
                    if unit.startswith(("centim", "cm")): factor = .01
                    elif unit.startswith(("millim", "mm")): factor = .001
                    elif unit.startswith(("kilom", "km")): factor = 1000.
                    elif unit not in {"m", "meter", "meters", "metre", "metres"}: continue
                elif quantity == "time_s":
                    if unit.startswith(("millisecond", "ms")): factor = .001
                    elif unit not in {"s", "sec", "second", "seconds"}: continue
                elif quantity == "absorption_coefficient":
                    if unit == "%": factor = .01
                    else: continue
            candidates.append(number * factor)
            if angle and re.search(r"\b(?:right|below)\b", suffix[:45], re.I):
                candidates.append(-number * factor)
        start += max(1, len(evidence))
    values = list(claim["value"].values()) if isinstance(claim["value"], dict) else [claim["value"]]
    if any(not any(math.isclose(value, number, rel_tol=1e-4, abs_tol=1e-4) for number in candidates) for value in values):
        raise PendingJudgement("Numeric value or unit is not supported by the quoted answer")


def validate_extraction(public, candidate, extraction):
    if not isinstance(extraction, dict):
        raise PendingJudgement("Extraction is not an object")
    if extraction.get("answer_status") not in {"answered", "abstained", "no_final_answer"}:
        raise PendingJudgement("Invalid answer_status")
    if type(extraction.get("contradiction")) is not bool:
        raise PendingJudgement("contradiction must be boolean")
    evidence = extraction.get("contradiction_evidence")
    if not isinstance(evidence, str) or (evidence and evidence not in candidate):
        raise PendingJudgement("Invalid contradiction evidence")
    if extraction["contradiction"] and not evidence:
        raise PendingJudgement("Contradiction needs candidate evidence")
    components = extraction.get("components")
    expected = public["components"]
    if not isinstance(components, list) or len(components) != len(expected):
        raise PendingJudgement("Incomplete extraction schema")
    if [c.get("component_id") for c in components] != [c["component_id"] for c in expected]:
        raise PendingJudgement("Component IDs are missing, duplicated, or out of order")
    for spec, claim in zip(expected, components):
        status = claim.get("status")
        if status not in {"single", "missing", "ambiguous", "contradictory"}:
            raise PendingJudgement("Invalid component status")
        evidence = claim.get("evidence")
        if not isinstance(evidence, str) or (evidence and evidence not in candidate):
            raise PendingJudgement("Component evidence not present in candidate")
        if not isinstance(claim.get("subject_text", ""), str):
            raise PendingJudgement("subject_text must be text")
        if claim.get("subject_text") and claim["subject_text"] not in candidate:
            if spec["kind"] in {"numeric", "enum"} and claim["subject_text"] in public["question"]:
                # A query name copied into an explicit-evidence metadata field is
                # not a candidate assertion. Keep all extracted values/evidence;
                # restore the already defined implicit-target representation.
                extraction.setdefault("schema_repairs",[]).append({
                    "component_id":claim["component_id"],"field":"subject_text",
                    "previous_value":claim["subject_text"],"new_value":"",
                    "reason":"Question-only source name is absent from candidate; preserve implicit query target, no identity supplied."})
                claim["subject_text"]=""
            else:
                raise PendingJudgement("Explicit subject must occur verbatim in candidate")
        if status != "single":
            if claim.get("value") is not None:
                raise PendingJudgement("Uncommitted component value must be null")
            continue
        if not evidence:
            raise PendingJudgement("A committed claim requires verbatim evidence")
        value, kind = claim.get("value"), spec["kind"]
        if kind == "numeric":
            if isinstance(value, dict):
                if set(value) != {"lower", "upper"} or not all(finite_number(v) for v in value.values()) or value["lower"] > value["upper"]:
                    raise PendingJudgement("Invalid numeric interval")
            elif not finite_number(value):
                raise PendingJudgement("Invalid numeric value")
            numeric_evidence(spec, claim, candidate)
        elif kind == "enum":
            if value not in spec["allowed_values"]:
                raise PendingJudgement("Invalid enum value")
        elif kind in {"source", "semantic"}:
            if not isinstance(value, str) or not value.strip():
                raise PendingJudgement("Invalid textual claim")
            # Source-name normalization is not used for scoring. The separate
            # source matcher receives the complete original final answer.
            if kind == "semantic" and value not in candidate:
                raise PendingJudgement("Semantic assertion must be a verbatim excerpt")
        elif kind == "source_set":
            if not isinstance(value, list) or not value or not all(isinstance(x,str) and x.strip() for x in value):
                raise PendingJudgement("Invalid source set")
        else:
            raise PendingJudgement("Unknown contract kind")
    return extraction

def circular_error(a, b):
    return abs((a-b+180.0) % 360.0-180.0)

def numeric_error(value, expected, circular=False):
    if not isinstance(value, dict):
        return circular_error(value, expected) if circular else abs(value-expected)
    lower, upper = value["lower"], value["upper"]
    if not circular:
        return max(abs(lower-expected),abs(upper-expected))
    if upper-lower >= 360:
        return 180.0
    # The maximum circular error can occur inside an interval at the antipode.
    opposite = expected + 180
    k = math.ceil((lower-opposite)/360)
    if lower <= opposite + 360*k <= upper:
        return 180.0
    return max(circular_error(lower,expected),circular_error(upper,expected))

def valid_domain(value, quantity):
    values = list(value.values()) if isinstance(value,dict) else [value]
    if quantity == "elevation_deg":
        return all(-90 <= x <= 90 for x in values)
    if quantity in {"distance_m", "time_s"}:
        return all(x >= 0 for x in values)
    if quantity == "angular_separation_deg":
        return all(0 <= x <= 180 for x in values)
    if quantity == "absorption_coefficient":
        return all(0 <= x <= 1 for x in values)
    return True  # Any finite azimuth has an equivalent wrapped representation.

def semantic_request(public, spec, claim):
    return {"check_id":spec["component_id"],"kind":spec["kind"],
            "question":public["question"],"requested_component":spec["description"],
            "reference_proposition_or_identity":spec["expected"],
            "candidate_value":claim["value"],"candidate_evidence":claim["evidence"],
            "reference_visible":True,"geometry_reference_visible":False}

def preliminary_score(public, reference, candidate, extraction):
    validate_extraction(public,candidate,extraction)
    if public["item_id"] != reference["item_id"]:
        raise PendingJudgement("Public/private contract identity mismatch")
    components, requests = [], []
    for spec, claim in zip(reference["components"],extraction["components"]):
        result = {"component_id":spec["component_id"],"kind":spec["kind"],"correct":False,
                  "reason":claim["status"],"candidate_value":claim["value"],"evidence":claim["evidence"]}
        if spec["kind"] in {"source", "source_set"}:
            # Read the actual selection in context, including comparisons,
            # corrections and collective answers such as "both sources".
            # Never pass an extractor-supplied question name as the answer.
            requests.append(semantic_request(public, spec, {"value": candidate, "evidence": candidate}))
            result.update(correct=None, reason="pending_raw_source_match",
                          candidate_value=candidate, evidence=candidate,
                          source_matching_input="complete_final_answer")
            components.append(result)
            continue
        if claim["status"] != "single":
            components.append(result);continue
        value, expected, kind = claim["value"],spec["expected"],spec["kind"]
        if kind == "numeric":
            error = numeric_error(value,expected,spec["circular"])
            valid = valid_domain(value,spec["quantity"])
            result.update(correct=valid and error <= spec["tolerance"]+1e-12,
                          error=error,tolerance=spec["tolerance"],reason="numeric_comparison" if valid else "invalid_physical_domain")
            subject = claim.get("subject_text", "")
            if result["correct"] and subject and spec.get("bound_source") and normalized_source(subject) != normalized_source(spec["bound_source"]):
                binding_id = spec["component_id"] + "__binding"
                requests.append({"check_id":binding_id,"kind":"source","question":public["question"],
                                 "requested_component":"Identity binding of " + spec["description"],
                                 "reference_proposition_or_identity":spec["bound_source"],
                                 "candidate_value":subject,"candidate_evidence":candidate,
                                 "reference_visible":True,"geometry_reference_visible":False})
                result.update(correct=None,reason="pending_source_binding",binding_check_id=binding_id)
        elif kind == "enum":
            result.update(correct=value == expected,reason="exact_category")
        else:
            requests.append(semantic_request(public,spec,claim))
            result.update(correct=None,reason="pending_semantic_match")
        components.append(result)
    return {"item_id":public["item_id"],"template_key":public["template_key"],
            "answer_status":extraction["answer_status"],"contradiction":extraction["contradiction"],
            "contradiction_evidence":extraction["contradiction_evidence"],"components":components,
            "semantic_requests":requests,"candidate_final_sha256":hashlib.sha256(candidate.encode()).hexdigest(),
            "extraction_sha256":digest(extraction),"contract_sha256":digest(reference)}

def complete_score(preliminary, semantic_results):
    value = json.loads(json.dumps(preliminary))
    checks = {c["check_id"]:c for c in value["semantic_requests"]}
    already_incorrect = (value["answer_status"] != "answered" or value["contradiction"]
                         or any(c["correct"] is False for c in value["components"]))
    missing=set(checks)-set(semantic_results)
    if set(semantic_results)-set(checks) or (missing and not already_incorrect):
        raise PendingJudgement("Semantic results are missing or have unexpected IDs")
    value["semantic_checks_not_needed_for_decided_whole_answer"] = sorted(missing)
    for component in value["components"]:
        cid = component.get("binding_check_id",component["component_id"])
        if cid not in checks:
            continue
        if cid not in semantic_results:
            component.update(correct=None,reason="not_adjudicated_whole_answer_already_incorrect")
            continue
        result = semantic_results[cid]
        if not isinstance(result,dict) or type(result.get("match")) is not bool:
            raise PendingJudgement("Semantic match must be boolean")
        evidence = result.get("evidence")
        if not isinstance(evidence,str) or (evidence and evidence not in checks[cid]["candidate_evidence"]):
            raise PendingJudgement("Invalid semantic evidence")
        if result["match"] and not evidence:
            raise PendingJudgement("Positive semantic match needs evidence")
        if not isinstance(result.get("reason"),str):
            raise PendingJudgement("Semantic reason must be text")
        component.update(correct=result["match"],reason="reference_visible_semantic_match",semantic_result=result)
    value["correct"] = (value["answer_status"] == "answered" and not value["contradiction"]
                        and all(c["correct"] is True for c in value["components"]))
    value["score"] = int(value["correct"])
    value["denominator"] = 1
    value["metric"] = "whole_answer_accuracy"
    return value
