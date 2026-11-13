"""Preserve explicit answer times and time ranges during spatial comparison."""
import math
import re
from .rules import NUM, TIME_TOLERANCE_S, answer_block, json_value, Invalid, protect_quotes, unwrap_trajectory


def seconds(value):
    if type(value) in {int, float} and math.isfinite(value): return float(value)
    if isinstance(value, str):
        match = re.fullmatch(r"\s*(" + NUM + r")\s*(ms|milliseconds?|s|seconds?)?\s*", value, re.I)
        if match:
            return float(match.group(1)) / (1000 if (match.group(2) or "").lower().startswith("m") else 1)
    raise ValueError("invalid explicit answer time")


def check_time_binding(question, raw):
    category = question["task"]
    if category not in {"json_coords", "trajectory"}: return None
    try: text = answer_block(raw)
    except Invalid: return None
    try: data = json_value(text)
    except Invalid: data = None
    if category == "json_coords":
        for _ in range(8):
            if not isinstance(data, dict) or len(data) != 1:
                break
            key, value = next(iter(data.items()))
            if key not in {"answer", "result", "data", "response", "coordinates", "position"} or not isinstance(value, dict):
                break
            data = value
    if category == "json_coords" and isinstance(data, dict):
        provided = []
        for key in ("time_s", "timestamp", "time", "t"):
            if key in data:
                try: provided.append(seconds(data[key]))
                except ValueError: return "invalid_explicit_answer_time"
        if provided:
            masked, _ = protect_quotes(question["question"])
            matches = re.findall(r"\b(?:at\s+(?:exactly\s+)?(?:t\s*=\s*)?|t\s*=\s*)(" + NUM + r")\s*s(?:econds?)?\b", masked, re.I)
            requested = {float(value) for value in matches}
            if len(requested) == 1:
                target = next(iter(requested))
                if any(abs(value - target) > TIME_TOLERANCE_S for value in provided):
                    return "answer_names_a_different_time"
            if max(provided) - min(provided) > TIME_TOLERANCE_S:
                return "conflicting_explicit_answer_times"
    if category == "trajectory":
        try:
            data = unwrap_trajectory(data)
        except Invalid:
            return "invalid_trajectory_wrapper"
        lower, upper = [], []
        if isinstance(data, dict):
            try:
                lower = [seconds(data[key]) for key in ("start_s", "start_time", "start_time_s") if key in data]
                upper = [seconds(data[key]) for key in ("end_s", "end_time", "end_time_s") if key in data]
                for key in ("time_range_s", "time_window_seconds"):
                    if key in data:
                        value = data[key]
                        if not isinstance(value, list) or len(value) != 2: return "invalid_explicit_trajectory_range"
                        lower.append(seconds(value[0])); upper.append(seconds(value[1]))
            except ValueError: return "invalid_explicit_trajectory_range"
        else:
            masked, _ = protect_quotes(text)
            for start, end in re.findall(r"\b(?:from|between)\s+(" + NUM + r")\s*s?(?:econds?)?\s+(?:to|and)\s+(" + NUM + r")\s*s(?:econds?)?\b", masked, re.I):
                lower.append(float(start)); upper.append(float(end))
        reference = question["scoring"]["reference_claim"]["value"]
        times = [p.get("time_s", 0.) for p in reference.get("required_points", reference["points"])]
        if lower and max(lower) > min(times) + TIME_TOLERANCE_S: return "trajectory_starts_after_a_required_time"
        if upper and min(upper) < max(times) - TIME_TOLERANCE_S: return "trajectory_ends_before_a_required_time"
    return None
