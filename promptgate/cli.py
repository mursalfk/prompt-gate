# code/promptgate/cli.py
"""Command line.

  python -m promptgate run  --suite S.json --prompt P.txt --model openai:qwen2.5:1.5b --out report.json
  python -m promptgate gate --baseline base.json --candidate cand.json

--model is one of:
  openai:<name>   any OpenAI-compatible server (Ollama /v1, llama.cpp server, LM Studio, vLLM); see --base-url
  ollama:<name>   Ollama's native API on localhost:11434
  bedrock:<id>    Amazon Bedrock Converse (needs AWS credentials, billed per call)
  fake-support    the scripted model used only by this project's own tests
Exit codes: 0 pass, 1 blocked, 2 usage or cache error.
"""
import argparse
import json
import os
import sys

from .gate import compare, to_markdown, DEFAULTS
from .models import CachedModel, CacheMiss, OllamaModel, OpenAICompatModel
from .runner import run_suite
from .suite import load_suite, SuiteError


def build_model(spec, region="us-east-1", base_url=None, temperature=0.7, max_tokens=256):
    if spec == "fake-support":
        from examples.support_bot import make_model
        return make_model()
    kind, _, name = spec.partition(":")
    if kind == "bedrock" and name:
        import boto3
        from .models import BedrockConverse
        return BedrockConverse(boto3.client("bedrock-runtime", region_name=region), name,
                               max_tokens=max_tokens, temperature=temperature)
    if kind == "ollama" and name:
        return OllamaModel(name, base_url=base_url or "http://localhost:11434",
                           temperature=temperature, max_tokens=max_tokens)
    if kind == "openai" and name:
        return OpenAICompatModel(name, base_url=base_url or "http://localhost:11434/v1",
                                 temperature=temperature, max_tokens=max_tokens,
                                 api_key=os.environ.get("PROMPTGATE_API_KEY"))
    raise SystemExit(f"unknown --model {spec!r} (use openai:<name>, ollama:<name>, bedrock:<id> or fake-support)")


def cmd_run(a):
    try:
        suite = load_suite(a.suite)
    except (SuiteError, OSError, ValueError) as e:
        print(f"suite error: {e}", file=sys.stderr)
        return 2
    system = open(a.prompt, encoding="utf-8").read()
    model = build_model(a.model, a.region, a.base_url, a.temperature, a.max_tokens)
    if a.cache:
        model = CachedModel(model, a.cache, a.cache_mode)
    prices = {"input_per_1k": a.price_in, "output_per_1k": a.price_out}
    try:
        report = run_suite(suite, system, model, samples=a.samples, prices=prices,
                           grader_options={"fence_ok": True} if a.fence_ok else None)
    except CacheMiss as e:
        print(f"cache miss: {e}", file=sys.stderr)
        return 2
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    t = report["totals"]
    print(f"{report['model']}  prompt {report['prompt_sha']}  pass {t['passes']}/{t['runs']} "
          f"({t['pass_rate']:.1%})  in {t['input_tokens_avg']} out {t['output_tokens_avg']} tokens")
    return 0


def cmd_gate(a):
    base, cand = json.load(open(a.baseline, encoding="utf-8")), json.load(open(a.candidate, encoding="utf-8"))
    overrides = {k: getattr(a, k) for k in DEFAULTS if getattr(a, k, None) is not None}
    result = compare(base, cand, **overrides)
    md = to_markdown(result, base, cand)
    print(md)
    if a.markdown:
        open(a.markdown, "w", encoding="utf-8").write(md)
    return 0 if result.passed else 1


def main(argv=None):
    p = argparse.ArgumentParser(prog="promptgate")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--suite", required=True)
    r.add_argument("--prompt", required=True)
    r.add_argument("--model", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--samples", type=int, default=3)
    r.add_argument("--cache")
    r.add_argument("--cache-mode", default="auto", choices=CachedModel.MODES)
    r.add_argument("--region", default="us-east-1")
    r.add_argument("--base-url", dest="base_url", help="server URL for openai: and ollama: models")
    r.add_argument("--temperature", type=float, default=0.7, help="test at the temperature you ship (default 0.7)")
    r.add_argument("--max-tokens", dest="max_tokens", type=int, default=256)
    r.add_argument("--fence-ok", dest="fence_ok", action="store_true",
                   help="JSON graders accept a reply wrapped in a ```json code fence")
    r.add_argument("--price-in", type=float, default=0.0, help="price per 1k input tokens")
    r.add_argument("--price-out", type=float, default=0.0, help="price per 1k output tokens")
    r.set_defaults(fn=cmd_run)
    g = sub.add_parser("gate")
    g.add_argument("--baseline", required=True)
    g.add_argument("--candidate", required=True)
    g.add_argument("--markdown")
    g.add_argument("--must-pass-min", dest="must_pass_min", type=float)
    g.add_argument("--case-drop", dest="case_drop", type=float)
    g.add_argument("--max-regressed-cases", dest="max_regressed_cases", type=int)
    g.add_argument("--tolerance", type=float)
    g.add_argument("--max-token-growth", dest="max_token_growth", type=float)
    g.add_argument("--max-cost-growth", dest="max_cost_growth", type=float)
    g.set_defaults(fn=cmd_gate)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
