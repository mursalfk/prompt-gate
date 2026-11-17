# code/promptgate/models.py
"""Model adapters. Every adapter exposes: name (str) and invoke(system, user, sample) -> ModelResult.

- FakeModel:       deterministic stand-in for tests and demos. No network.
- BedrockConverse: Amazon Bedrock Converse API through an injected boto3 client.
- OllamaModel:     a local Ollama server over its native HTTP API.
- OpenAICompatModel: any server that speaks the OpenAI chat-completions API
                   (Ollama at /v1, llama.cpp server, LM Studio, vLLM).
- CachedModel:     wraps any adapter with a response cache (record / replay / auto).

Real adapters pass the sample number as the random seed, so sample 0 and sample 1 of the
same case are separate draws. Do not rely on a seed to make a run repeatable: servers differ,
and the same seed can still give different text. The recording in CachedModel is what repeats.
"""
import hashlib
import json
import os
import random
import time
import urllib.request
from dataclasses import dataclass, asdict


@dataclass
class ModelResult:
    text: str
    stop_reason: str = "end_turn"
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0

    def to_dict(self):
        return asdict(self)


class CacheMiss(Exception):
    """Raised in replay mode when a response was never recorded."""


def estimate_tokens(text):
    """Rough estimate used only by FakeModel. Real adapters report real usage."""
    return max(1, int(len(text.split()) * 1.3))


class FakeModel:
    """Deterministic stand-in. `fn(system, user) -> str` plays the model.

    `flake` is the chance that a sample degrades to a useless answer, seeded from the
    request so runs are reproducible. It exists to give the statistics something to chew on.
    """

    def __init__(self, fn, flake=0.0, name="fake"):
        self.fn = fn
        self.flake = flake
        self.name = name

    def invoke(self, system, user, sample=0):
        seed = hashlib.sha256(f"{system}\n{user}\n{sample}".encode()).hexdigest()
        rng = random.Random(seed)
        text = "I am not sure." if rng.random() < self.flake else self.fn(system, user)
        return ModelResult(
            text=text,
            stop_reason="end_turn",
            input_tokens=estimate_tokens(system + " " + user),
            output_tokens=estimate_tokens(text),
            latency_ms=round(40 + rng.random() * 20, 1),
        )


class BedrockConverse:
    """Amazon Bedrock Converse. Pass a boto3 'bedrock-runtime' client so tests can stub it."""

    def __init__(self, client, model_id, max_tokens=512, temperature=0.0):
        self.client = client
        self.model_id = model_id
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.name = f"bedrock:{model_id}"
        self.params = {"temperature": temperature, "max_tokens": max_tokens}

    def invoke(self, system, user, sample=0):
        t0 = time.time()
        resp = self.client.converse(
            modelId=self.model_id,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={"maxTokens": self.max_tokens, "temperature": self.temperature},
        )
        blocks = resp["output"]["message"]["content"]
        text = "".join(b.get("text", "") for b in blocks)
        usage = resp.get("usage", {})
        return ModelResult(
            text=text,
            stop_reason=resp.get("stopReason", ""),
            input_tokens=usage.get("inputTokens", 0),
            output_tokens=usage.get("outputTokens", 0),
            latency_ms=float(resp.get("metrics", {}).get("latencyMs", (time.time() - t0) * 1000)),
        )


class OllamaModel:
    """A local Ollama server (native /api/chat). Free to run on your own machine."""

    def __init__(self, model, base_url="http://localhost:11434", temperature=0.0, max_tokens=256, timeout=300):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.name = f"ollama:{model}"
        self.params = {"temperature": temperature, "max_tokens": max_tokens}

    def invoke(self, system, user, sample=0):
        body = json.dumps({
            "model": self.model,
            "stream": False,
            "options": {"temperature": self.temperature, "seed": sample, "num_predict": self.max_tokens},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }).encode()
        req = urllib.request.Request(self.base_url + "/api/chat", data=body,
                                     headers={"Content-Type": "application/json"})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read())
        return ModelResult(
            text=data["message"]["content"],
            stop_reason={"stop": "end_turn", "length": "max_tokens"}.get(data.get("done_reason", "stop"),
                                                                        data.get("done_reason", "")),
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
            latency_ms=round((time.time() - t0) * 1000, 1),
        )


class OpenAICompatModel:
    """Any server that speaks the OpenAI chat-completions API.

    Tested here against llama.cpp's server. Ollama serves the same API at
    http://localhost:11434/v1, as do LM Studio and vLLM. `api_key` is only sent when given.
    """

    def __init__(self, model, base_url="http://localhost:11434/v1", temperature=0.0, max_tokens=256,
                 timeout=300, api_key=None):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.api_key = api_key
        self.name = f"openai:{model}"
        self.params = {"temperature": temperature, "max_tokens": max_tokens}

    def invoke(self, system, user, sample=0):
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "seed": sample,
            "stream": False,
        }).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.base_url + "/chat/completions", data=body, headers=headers)
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read())
        choice = data["choices"][0]
        usage = data.get("usage") or {}
        finish = choice.get("finish_reason") or "stop"
        return ModelResult(
            text=choice["message"].get("content") or "",
            stop_reason={"stop": "end_turn", "length": "max_tokens"}.get(finish, finish),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            latency_ms=round((time.time() - t0) * 1000, 1),
        )


class CachedModel:
    """Response cache keyed by (model, system, user, sample) plus the adapter's sampling
    settings (temperature, max tokens) when it has any. A recording made at one temperature
    is never replayed for another.

    replay: hit -> stored result, miss -> CacheMiss (CI costs nothing and never calls a model)
    record: always call the inner model and store the result
    auto:   hit -> stored result, miss -> call inner model and store it
    """

    MODES = ("replay", "record", "auto")

    def __init__(self, inner, path, mode="auto"):
        if mode not in self.MODES:
            raise ValueError(f"mode must be one of {self.MODES}")
        self.inner = inner
        self.path = path
        self.mode = mode
        self.name = inner.name
        self.hits = 0
        self.calls = 0
        self._store = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        row = json.loads(line)
                        self._store[row["key"]] = row["result"]

    def key(self, system, user, sample):
        parts = [self.inner.name, system, user, sample]
        params = getattr(self.inner, "params", None)
        if params:
            parts.append(params)
        raw = json.dumps(parts, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def invoke(self, system, user, sample=0):
        k = self.key(system, user, sample)
        if self.mode != "record" and k in self._store:
            self.hits += 1
            return ModelResult(**self._store[k])
        if self.mode == "replay":
            raise CacheMiss("no recorded response for this prompt and input; record it first")
        result = self.inner.invoke(system, user, sample)
        self.calls += 1
        self._store[k] = result.to_dict()
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"key": k, "result": self._store[k]}) + "\n")
        return result
