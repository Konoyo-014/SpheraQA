"""A configurable Chat Completions client for the specified GPT-4o release."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MODEL = "gpt-4o-2024-11-20"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def strict_json(text):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value: raise ValueError("duplicate JSON field")
            value[key] = item
        return value
    return json.loads(text, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON value")))


class JudgeClient:
    def __init__(self, cache_dir, *, timeout=180, retries=5):
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.timeout, self.retries = timeout, retries

    def complete(self, system, payload, validate):
        if "json" not in system.casefold():
            raise ValueError("JSON response mode requires an explicit JSON output instruction")
        body = {"model": MODEL, "temperature": 0, "max_tokens": 16384,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": canonical(payload)}]}
        key = digest(body)
        destination = self.cache / (key + ".json")
        if destination.exists():
            receipt = strict_json(destination.read_text())
            if receipt.get("request") != body or receipt.get("requested_model") != MODEL or receipt.get("reported_model") != MODEL or receipt.get("finish_reason") != "stop":
                raise ValueError("cached judgement does not match this request and model")
            value = strict_json(receipt["response_text"])
            validate(value)
            return value, key
        base = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not base or not api_key:
            raise RuntimeError("Set OPENAI_BASE_URL and OPENAI_API_KEY using .env.example before scoring uncached answers.")
        endpoint = base + "/chat/completions"
        error = None
        for attempt in range(self.retries):
            try:
                request = Request(endpoint, data=canonical(body).encode(), headers={
                    "Content-Type": "application/json", "Authorization": "Bearer " + api_key})
                with urlopen(request, timeout=self.timeout) as response:
                    result = strict_json(response.read().decode())
                    http_status = response.status
                if http_status != 200 or result.get("model") != MODEL:
                    raise ValueError("response did not confirm the specified model")
                choices = result.get("choices")
                if not isinstance(choices, list) or len(choices) != 1 or choices[0].get("finish_reason") != "stop":
                    raise ValueError("model response did not finish normally")
                content = choices[0].get("message", {}).get("content")
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("empty judge response")
                value = strict_json(content)
                validate(value)
                receipt = {"request": body, "requested_model": MODEL, "reported_model": result["model"],
                           "response_id": result.get("id"), "finish_reason": "stop", "http_status": http_status,
                           "response_text": content, "usage": result.get("usage"), "completed_at": time.time()}
                temporary = destination.with_suffix(".partial")
                temporary.write_text(canonical(receipt) + "\n")
                temporary.replace(destination)
                return value, key
            except HTTPError as exc:
                error = f"judge HTTP {exc.code}"
                if exc.code in {400, 401, 403, 404}: break
            except (URLError, TimeoutError, OSError, ValueError, KeyError, TypeError) as exc:
                error = f"{type(exc).__name__}: {str(exc)[:160]}"
            if attempt + 1 < self.retries:
                time.sleep(min(2 ** attempt, 16))
        raise RuntimeError(error or "judge request failed")
