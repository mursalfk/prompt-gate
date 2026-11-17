# code/test_promptgate.py
"""Offline tests. No AWS account, no network beyond localhost. Run: python test_promptgate.py"""
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from promptgate.cli import build_model
from promptgate.gate import compare, to_markdown, wilson
from promptgate.graders import grade, grade_detail, strip_fence
from promptgate.models import (BedrockConverse, CachedModel, CacheMiss, FakeModel, ModelResult, OllamaModel,
                               OpenAICompatModel)
from promptgate.runner import run_suite
from promptgate.suite import SuiteError, load_suite, validate
from examples.support_bot import make_model

# The scripted bot in examples/support_bot.py was written against the first suite and prompts, which now
# live in round1/. They are the fixed fixture for these tests. The real suite is checked separately below.
SUITE = os.path.join(HERE, "round1", "suite.json")
PROMPTS = os.path.join(HERE, "round1", "prompts")
REAL_SUITE = os.path.join(HERE, "examples", "suite.json")
REAL_PROMPTS = os.path.join(HERE, "examples", "prompts")
checks = 0


def ok(cond, msg=""):
    global checks
    assert cond, msg
    checks += 1


def R(text, stop="end_turn"):
    return ModelResult(text=text, stop_reason=stop)


def test_wilson():
    ok(wilson(0, 0) == (0.0, 1.0))
    lo, hi = wilson(50, 50)
    ok(hi == 1.0 and 0.92 < lo < 0.95, (lo, hi))
    lo, hi = wilson(0, 50)
    ok(lo == 0.0 and 0.05 < hi < 0.08, (lo, hi))
    ok(wilson(10, 100)[1] - wilson(10, 100)[0] > wilson(100, 1000)[1] - wilson(100, 1000)[0])


def test_graders():
    cases = [
        ({"type": "contains", "value": "30 Days"}, R("within 30 days"), True),
        ({"type": "contains", "value": "30 Days", "ci": False}, R("within 30 days"), False),
        ({"type": "not_contains", "value": "INT-"}, R("see INT-1"), False),
        ({"type": "regex", "value": r"\d{2} days"}, R("in 30 days"), True),
        ({"type": "not_regex", "value": r"INT-\d+"}, R("nothing here"), True),
        ({"type": "json"}, R('{"a": 1}'), True),
        ({"type": "json"}, R("not json"), False),
        ({"type": "json_keys", "keys": ["a", "b"]}, R('{"a": 1}'), False),
        ({"type": "json_keys", "keys": ["a"]}, R("[1]"), False),
        ({"type": "json_field_in", "field": "p", "values": ["high"]}, R('{"p": "high"}'), True),
        ({"type": "json_field_in", "field": "p", "values": ["high"]}, R('{"p": "low"}'), False),
        ({"type": "max_words", "value": 3}, R("one two three four"), False),
        ({"type": "min_words", "value": 2}, R("one two"), True),
        ({"type": "stop_reason"}, R("cut off", "max_tokens"), False),
        ({"type": "stop_reason", "value": "max_tokens"}, R("cut off", "max_tokens"), True),
    ]
    for spec, result, want in cases:
        ok(grade([spec], result)[0] is want, (spec, result.text))
    passed, reasons = grade([{"type": "contains", "value": "x"}, {"type": "json"}], R("nope"))
    ok(not passed and len(reasons) == 2)


def test_code_fences_and_field_limits():
    fenced = R('```json\n{"answer": "hi there", "priority": "low"}\n```')
    keys = {"type": "json_keys", "keys": ["answer", "priority"]}
    ok(strip_fence(fenced.text) == '{"answer": "hi there", "priority": "low"}')
    ok(strip_fence("``` {\"a\": 1} ```") == '{"a": 1}' and strip_fence('{"a": 1}') == '{"a": 1}')
    ok(not grade([keys], fenced)[0], "strict by default: a fenced reply is not valid JSON")
    ok(grade([{**keys, "fence_ok": True}], fenced)[0], "the grader can be told to accept a fence")
    ok(grade([keys], fenced, {"fence_ok": True})[0], "or the whole run can")
    ok(not grade([{**keys, "fence_ok": False}], fenced, {"fence_ok": True})[0], "a grader's own setting wins")
    limit = {"type": "json_field_max_words", "field": "answer", "value": 3}
    ok(grade([limit], R('{"answer": "one two three", "priority": "a b c d e f"}'))[0], "only the field is counted")
    ok(not grade([limit], R('{"answer": "one two three four"}'))[0])
    ok(not grade([limit], R("plain text that is far too long"))[0], "broken JSON does not dodge the limit")
    ok(grade([limit], R("short text"))[0])
    ok(grade([limit], fenced, {"fence_ok": True})[0])
    named = {"type": "regex", "value": "x", "label": "has an x"}
    passed, reasons = grade([named], R("nope"))
    ok(not passed and reasons[0].startswith("has an x: "), reasons)
    ok(grade_detail([named, {"type": "contains", "value": "no"}], R("nope")) ==
       [("has an x", False, "has an x: no match for /x/"), ("contains no", True, "")])


def test_suite_validation():
    ok(len(load_suite(SUITE)["cases"]) == 14)
    real = load_suite(REAL_SUITE)
    ok(len(real["cases"]) == 16 and sum(c.get("samples", 5) for c in real["cases"]) == 95)
    shared = len(json.load(open(REAL_SUITE))["all_cases"])
    by_id = {c["id"]: c for c in real["cases"]}
    ok(len(by_id["refund_window"]["graders"]) == 1 + shared, "all_cases checks are added to an ordinary case")
    ok(len(by_id["injection_debug"]["graders"]) == 1, "a skip_all_cases case keeps only its own checks")
    ok(all(c.get("skip_all_cases") for c in real["cases"] if c.get("must_pass")), "must-pass cases carry only their own checks")
    only_shared = validate({"all_cases": [{"type": "json"}], "cases": [{"id": "a", "input": "x"}]})
    ok(only_shared["cases"][0]["graders"] == [{"type": "json"}], "a case may rely on all_cases alone")
    bad = [
        ({}, "cases"),
        ({"cases": [{"input": "x", "graders": [{"type": "json"}]}]}, "id"),
        ({"cases": [{"id": "a", "input": "", "graders": [{"type": "json"}]}]}, "input"),
        ({"cases": [{"id": "a", "input": "x", "graders": []}]}, "grader"),
        ({"cases": [{"id": "a", "input": "x", "graders": [{"type": "magic"}]}]}, "unknown"),
        ({"cases": [{"id": "a", "input": "x", "graders": [{"type": "contains"}]}]}, "field"),
        ({"cases": [{"id": "a", "input": "x", "graders": [{"type": "json"}]},
                    {"id": "a", "input": "y", "graders": [{"type": "json"}]}]}, "duplicate"),
        ({"all_cases": [{"type": "magic"}], "cases": [{"id": "a", "input": "x"}]}, "all_cases"),
        ({"all_cases": [{"type": "json"}], "cases": [{"id": "a", "input": "x", "skip_all_cases": True}]}, "grader"),
        ({"cases": [{"id": "a", "input": "x", "samples": 0, "graders": [{"type": "json"}]}]}, "samples"),
    ]
    for suite, word in bad:
        try:
            validate(suite)
            ok(False, f"should reject: {word}")
        except SuiteError as e:
            ok(word in str(e), (word, str(e)))


class Counting:
    name = "counting"

    def __init__(self):
        self.n = 0

    def invoke(self, system, user, sample=0):
        self.n += 1
        return ModelResult(text=f"{system}|{user}|{sample}", input_tokens=10, output_tokens=5)


def test_cache():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "c.jsonl")
        inner = Counting()
        m = CachedModel(inner, path, "auto")
        a = m.invoke("sys", "hi", 0)
        b = m.invoke("sys", "hi", 0)
        ok(a.text == b.text and inner.n == 1 and m.hits == 1)
        m.invoke("sys2", "hi", 0)
        ok(inner.n == 2, "a changed prompt must miss")
        m.invoke("sys", "hi", 1)
        ok(inner.n == 3, "a different sample must miss")
        replay = CachedModel(Counting(), path, "replay")
        ok(replay.invoke("sys", "hi", 0).text == a.text, "survives a restart")
        try:
            replay.invoke("sys-changed", "hi", 0)
            ok(False, "replay miss must raise")
        except CacheMiss:
            ok(True)
        ok(replay.inner.n == 0, "replay never calls the model")
        rec = CachedModel(inner, path, "record")
        before = inner.n
        rec.invoke("sys", "hi", 0)
        ok(inner.n == before + 1, "record always calls")
        try:
            CachedModel(inner, path, "nope")
            ok(False)
        except ValueError:
            ok(True)
        hot, cold = Counting(), Counting()
        hot.params, cold.params = {"temperature": 0.7}, {"temperature": 0.0}
        p2 = os.path.join(d, "params.jsonl")
        CachedModel(hot, p2, "auto").invoke("sys", "hi", 0)
        CachedModel(cold, p2, "auto").invoke("sys", "hi", 0)
        ok(cold.n == 1, "an answer recorded at one temperature is not replayed for another")
        again = Counting()
        again.params = {"temperature": 0.7}
        CachedModel(again, p2, "auto").invoke("sys", "hi", 0)
        ok(again.n == 0, "the same settings replay")


def test_runner():
    suite = load_suite(SUITE)
    model = FakeModel(lambda s, u: "x", flake=0.0, name="plain")
    rep = run_suite(suite, "p", model, samples=2, prices={"input_per_1k": 1.0, "output_per_1k": 2.0})
    ok(rep["totals"]["runs"] == 28 and rep["samples"] == 2)
    ok(rep["cases"]["injection_direct"]["passes"] == 2, "a plain 'x' leaks nothing")
    ok(rep["cases"]["refund_window"]["passes"] == 0 and rep["cases"]["refund_window"]["failures"])
    ok(rep["cases"]["refund_window"]["failed_outputs"] == ["x", "x"], "a failing case keeps a little evidence")
    ok(rep["cases"]["injection_direct"]["failed_outputs"] == [])
    t = rep["totals"]
    expect = round(1000 * (t["input_tokens_avg"] / 1000 * 1.0 + t["output_tokens_avg"] / 1000 * 2.0), 4)
    ok(abs(t["cost_per_1k_runs"] - expect) < 0.2, (t["cost_per_1k_runs"], expect))  # averages are rounded to 1 decimal
    ok(run_suite(suite, "p", model, samples=1)["totals"]["cost_per_1k_runs"] == 0.0, "no prices, no invented cost")
    a = run_suite(suite, "p", make_model(), samples=3)
    b = run_suite(suite, "p", make_model(), samples=3)
    ok(a == b, "runs are reproducible")
    ok(rep["failed_checks"] and list(rep["failed_checks"].values()) == sorted(rep["failed_checks"].values(), reverse=True),
       "the report tallies which checks failed, most common first")
    small = validate({"all_cases": [{"type": "json", "label": "valid JSON"}],
                      "cases": [{"id": "a", "input": "x", "samples": 4, "graders": [{"type": "contains", "value": "x"}]},
                                {"id": "b", "input": "y"}]})
    fenced = FakeModel(lambda s, u: '```json\n{"x": 1}\n```', flake=0.0, name="fenced")
    strict = run_suite(small, "p", fenced, samples=2)
    ok(strict["cases"]["a"]["samples"] == 4 and strict["cases"]["b"]["samples"] == 2 and strict["totals"]["runs"] == 6,
       "a case can ask for more samples than the rest")
    ok(strict["totals"]["passes"] == 0 and strict["failed_checks"] == {"valid JSON": 6})
    loose = run_suite(small, "p", fenced, samples=2, grader_options={"fence_ok": True})
    ok(loose["totals"]["passes"] == 6 and loose["failed_checks"] == {} and loose["grader_options"] == {"fence_ok": True})


def report(prompt, samples=5):
    return run_suite(load_suite(SUITE), open(os.path.join(PROMPTS, prompt)).read(), make_model(), samples=samples)


def test_gate_scenarios():
    base = report("v1.txt")
    ok(base["totals"]["pass_rate"] > 0.9)
    tidy = compare(base, report("v2_tidy.txt"))
    ok(not tidy.passed)
    ok(any("must-pass case injection_direct" in f for f in tidy.failures))
    ok(any("significantly below" in f for f in tidy.failures))
    ok("password_reset" in tidy.regressed)
    reword = compare(base, report("v3_reword.txt"))
    ok(reword.passed, reword.failures)
    bloated = compare(base, report("v4_bloated.txt"))
    ok(not bloated.passed and len(bloated.failures) == 1 and "input tokens grew" in bloated.failures[0])
    ok(compare(base, report("v4_bloated.txt"), max_token_growth=5.0).passed, "limits are configurable")


def synth(passes_by_case, samples=5, in_tok=100.0, out_tok=20.0, cost=0.0, must=()):
    cases = {cid: {"passes": p, "samples": samples, "must_pass": cid in must, "tags": [], "failures": [],
                   "input_tokens": in_tok, "output_tokens": out_tok, "latency_ms": 1.0}
             for cid, p in passes_by_case.items()}
    tp, tr = sum(passes_by_case.values()), samples * len(passes_by_case)
    return {"model": "m", "prompt_sha": "x", "samples": samples, "cases": cases,
            "totals": {"passes": tp, "runs": tr, "pass_rate": tp / tr, "input_tokens_avg": in_tok,
                       "output_tokens_avg": out_tok, "latency_ms_avg": 1.0, "cost_per_1k_runs": cost}}


def test_gate_edges():
    base = synth({"a": 5, "b": 5, "c": 5, "d": 5})
    ok(compare(base, synth({"a": 5, "b": 5, "c": 5, "d": 4})).passed, "one flaky sample is noise")
    ok(not compare(base, synth({"a": 5, "b": 5, "c": 5, "d": 1})).passed, "a collapsed case is a regression")
    ok(compare(base, synth({"a": 5, "b": 5, "c": 5, "d": 1}), max_regressed_cases=1, tolerance=1.0).passed)
    ok(not compare(base, synth({"a": 5, "b": 5, "c": 5, "d": 4}, must=("d",))).passed, "must-pass needs 100%")
    ok(compare(base, synth({"a": 5, "b": 5, "c": 5, "d": 4}, must=("d",)), must_pass_min=0.8).passed)
    mismatch = synth({"a": 5, "b": 5, "c": 5})
    ok(not compare(base, mismatch).passed and compare(base, mismatch, allow_suite_mismatch=True).passed)
    ok(not compare(synth({"a": 5}, cost=1.0), synth({"a": 5}, cost=2.0)).passed, "cost creep blocks")
    ok(compare(synth({"a": 5}, cost=0.0), synth({"a": 5}, cost=9.0)).passed, "no prices, no cost rule")
    better = compare(synth({"a": 1, "b": 5}), synth({"a": 5, "b": 5}))
    ok(better.passed and better.improved == ["a"])
    old, new = synth({"a": 5, "b": 5}), synth({"a": 5, "b": 0}, must=("b",))
    old["model"], new["model"] = "openai:model-a", "openai:model-b"
    swap = compare(old, new)
    md = to_markdown(swap, old, new)
    ok("| model | openai:model-a | openai:model-b |" in md, "a model swap is visible in the verdict")
    ok("Regressed: b" in md and "(baseline was 100%)" in md)


def cli(*args):
    p = subprocess.run([sys.executable, "-m", "promptgate", *args], cwd=HERE, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def test_cli():
    with tempfile.TemporaryDirectory() as d:
        out = {v: os.path.join(d, v + ".json") for v in ("v1", "v2_tidy", "v3_reword")}
        for v, f in out.items():
            name = "v1.txt" if v == "v1" else f"{v}.txt"
            code, so, _ = cli("run", "--suite", "round1/suite.json", "--prompt", f"round1/prompts/{name}",
                              "--model", "fake-support", "--samples", "5", "--out", f)
            ok(code == 0 and "pass" in so)
        ok(cli("gate", "--baseline", out["v1"], "--candidate", out["v3_reword"])[0] == 0)
        code, so, _ = cli("gate", "--baseline", out["v1"], "--candidate", out["v2_tidy"], "--markdown",
                          os.path.join(d, "g.md"))
        ok(code == 1 and "BLOCKED" in so and "BLOCKED" in open(os.path.join(d, "g.md")).read())
        code, _, err = cli("run", "--suite", "round1/suite.json", "--prompt", "round1/prompts/v2_tidy.txt",
                           "--model", "fake-support", "--cache", os.path.join(d, "empty.jsonl"),
                           "--cache-mode", "replay", "--out", os.path.join(d, "x.json"))
        ok(code == 2 and "cache miss" in err)
        bad = os.path.join(d, "bad.json")
        open(bad, "w").write("{}")
        code, _, err = cli("run", "--suite", bad, "--prompt", "round1/prompts/v1.txt", "--model", "fake-support",
                           "--out", os.path.join(d, "y.json"))
        ok(code == 2 and "suite error" in err)


def test_bedrock_adapter_with_stubber():
    try:
        import boto3
        from botocore.stub import Stubber
    except ImportError:          # boto3 is only needed for the Bedrock adapter, so the suite runs without it
        return "skipped"
    client = boto3.client("bedrock-runtime", region_name="us-east-1")
    stub = Stubber(client)
    resp = {"output": {"message": {"role": "assistant", "content": [{"text": '{"answer": "hi"}'}]}},
            "stopReason": "max_tokens", "usage": {"inputTokens": 12, "outputTokens": 7, "totalTokens": 19},
            "metrics": {"latencyMs": 321}}
    stub.add_response("converse", resp, {
        "modelId": "amazon.nova-micro-v1:0",
        "system": [{"text": "be brief"}],
        "messages": [{"role": "user", "content": [{"text": "hello"}]}],
        "inferenceConfig": {"maxTokens": 256, "temperature": 0.0}})
    with stub:
        m = BedrockConverse(client, "amazon.nova-micro-v1:0", max_tokens=256)
        r = m.invoke("be brief", "hello")
        stub.assert_no_pending_responses()
    ok(r.text == '{"answer": "hi"}' and r.stop_reason == "max_tokens")
    ok((r.input_tokens, r.output_tokens, r.latency_ms) == (12, 7, 321.0))
    passed, reasons = grade([{"type": "stop_reason"}], r)
    ok(not passed, "a truncated answer must not pass as complete")


def test_ollama_adapter_against_local_server():
    seen = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["path"] = self.path
            seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            data = json.dumps({"message": {"content": "pong"}, "done_reason": "length",
                               "prompt_eval_count": 9, "eval_count": 3}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        m = OllamaModel("llama3.2", base_url=f"http://127.0.0.1:{srv.server_port}", temperature=0.7, max_tokens=99)
        r = m.invoke("sys", "ping", sample=3)
    finally:
        srv.shutdown()
    ok(seen["path"] == "/api/chat" and seen["body"]["model"] == "llama3.2" and seen["body"]["stream"] is False)
    ok(seen["body"]["options"] == {"temperature": 0.7, "seed": 3, "num_predict": 99}, "the sample number is the seed")
    ok([x["role"] for x in seen["body"]["messages"]] == ["system", "user"])
    ok(r.text == "pong" and r.stop_reason == "max_tokens" and (r.input_tokens, r.output_tokens) == (9, 3))


def test_openai_compatible_adapter_against_local_server():
    seen = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
            data = json.dumps({"choices": [{"message": {"role": "assistant", "content": "pong"},
                                            "finish_reason": "length"}],
                               "usage": {"prompt_tokens": 11, "completion_tokens": 4}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/v1"
    try:
        r = OpenAICompatModel("qwen2.5:1.5b", base_url=url, temperature=0.7, max_tokens=64).invoke("sys", "ping", 2)
        OpenAICompatModel("m", base_url=url + "/", api_key="k").invoke("sys", "ping")
    finally:
        srv.shutdown()
    b = seen[0]["body"]
    ok(seen[0]["path"] == "/v1/chat/completions" and b["model"] == "qwen2.5:1.5b" and b["stream"] is False)
    ok((b["temperature"], b["max_tokens"], b["seed"]) == (0.7, 64, 2), "the sample number is the seed")
    ok([x["role"] for x in b["messages"]] == ["system", "user"])
    ok(r.text == "pong" and r.stop_reason == "max_tokens" and (r.input_tokens, r.output_tokens) == (11, 4))
    ok(seen[0]["auth"] is None and seen[1]["auth"] == "Bearer k", "a key is sent only when you give one")
    ok(seen[1]["path"] == "/v1/chat/completions", "a trailing slash in the base URL is harmless")


def test_model_specs():
    m = build_model("openai:qwen2.5:1.5b", base_url="http://x/v1", temperature=0.2, max_tokens=50)
    ok(isinstance(m, OpenAICompatModel) and m.name == "openai:qwen2.5:1.5b" and m.base_url == "http://x/v1")
    ok(m.params == {"temperature": 0.2, "max_tokens": 50})
    ok(build_model("openai:m").base_url == "http://localhost:11434/v1", "Ollama's OpenAI-compatible URL is the default")
    o = build_model("ollama:llama3.2:3b")
    ok(isinstance(o, OllamaModel) and o.model == "llama3.2:3b" and o.base_url == "http://localhost:11434")
    for bad in ("gpt", "openai:", "nope:x"):
        try:
            build_model(bad)
            ok(False, bad)
        except SystemExit:
            ok(True)


def test_matrix_tool():
    with tempfile.TemporaryDirectory() as d:
        tool = os.path.join(HERE, "tools", "run_matrix.py")
        fixture = ["--suite", SUITE, "--prompts", PROMPTS]
        p = subprocess.run([sys.executable, tool, "run", "--models", "fake-support", "--out", d, *fixture],
                           capture_output=True, text=True)
        ok(p.returncode == 0 and p.stdout.count("70 new calls") == 4, p.stderr)
        p = subprocess.run([sys.executable, tool, "run", "--models", "fake-support", "--out", d, "--cache-mode", "replay",
                            *fixture], capture_output=True, text=True)
        ok(p.returncode == 0 and p.stdout.count(" 0 new calls") == 4, "a second run replays the recordings")
        p = subprocess.run([sys.executable, tool, "run", "--models", "fake-support", "--out", os.path.join(d, "one"),
                            "--cache-dir", os.path.join(d, "cache"), "--cache-mode", "replay", "--only", "v1", *fixture],
                           capture_output=True, text=True)
        ok(p.returncode == 0 and p.stdout.count(" 0 new calls") == 1, "--only and --cache-dir: " + p.stderr)
        p = subprocess.run([sys.executable, tool, "summary", "--out", d, "--baseline", "v1"], capture_output=True, text=True)
        md = open(os.path.join(d, "summary.md")).read()
        ok(p.returncode == 0 and "| `v2_tidy` |" in md and "| `v3_reword` |" in md and "| `v4_bloated` |" in md)
        data = json.load(open(os.path.join(d, "summary.json")))
        verdicts = {x["change"]: x["passed"] for x in data["prompt_changes"]}
        ok(verdicts == {"v2_tidy": False, "v3_reword": True, "v4_bloated": False}, verdicts)
        ok(len(data["baseline_cases"]["fake-support"]) == 14)


def test_run_all_with_the_real_suite_and_prompts():
    """The one-command runner, end to end, with the scripted stand-in model (no server, no network).
    It proves the real suite and the six real prompts load, grade and summarise without errors."""
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "results")
        p = subprocess.run([sys.executable, os.path.join(HERE, "tools", "run_all.py"), "--models", "fake-support",
                            "--out", out], capture_output=True, text=True)
        ok(p.returncode == 0, p.stdout[-400:] + p.stderr[-400:])
        names = sorted(os.listdir(REAL_PROMPTS))
        ok(len(names) == 6 and names[0] == "v1_first_try.txt" and names[1] == "v2_baseline.txt", names)
        for folder in (out, out + "-fence-ok"):
            md = open(os.path.join(folder, "summary.md")).read()
            ok("v2_baseline" in md and "| `v6_bloated` |" in md, folder)
            data = json.load(open(os.path.join(folder, "summary.json")))
            ok(len(data["baseline_cases"]["fake-support"]) == 16 and len(data["prompt_changes"]) == 4)
        ok(os.path.exists(os.path.join(out, "cache", "fake-support.jsonl")))
        p = subprocess.run([sys.executable, os.path.join(HERE, "tools", "run_all.py"), "--models", "qwen2.5:1.5b",
                            "--base-url", "http://127.0.0.1:9/v1", "--out", out], capture_output=True, text=True)
        ok(p.returncode == 2 and "Start Ollama" in p.stderr, "no server: a clear message and exit code 2")


if __name__ == "__main__":
    skipped = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            if fn() == "skipped":
                skipped.append(name)
                print(f"skip {name}  (needs boto3: pip install boto3)")
            else:
                print(f"ok  {name}")
    print(f"{checks} checks passed offline" + (f", {len(skipped)} test skipped" if skipped else ""))