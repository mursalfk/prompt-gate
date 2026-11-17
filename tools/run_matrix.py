# code/tools/run_matrix.py
"""Run every prompt version against one or more models, gate each change, and write a summary.

  python tools/run_matrix.py run --models qwen2.5:1.5b,llama3.2:3b,gemma3:1b
  python tools/run_matrix.py summary

`run` records every model answer in <out>/cache/<model>.jsonl, so a second run costs nothing and
the recordings can be committed and replayed in CI. `summary` makes no model calls: it reads the
reports, gates every prompt change against that model's baseline, gates every model swap on the
baseline prompt, and writes <out>/summary.md and <out>/summary.json.

Prompt files are examples/prompts/*.txt. --baseline names the one every change is compared to.
--first names an earlier prompt the baseline replaced; the summary gates first -> baseline too.
"""
import argparse
import glob
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from promptgate.cli import build_model          # noqa: E402
from promptgate.gate import compare, wilson     # noqa: E402
from promptgate.models import CachedModel       # noqa: E402
from promptgate.runner import run_suite         # noqa: E402
from promptgate.suite import load_suite         # noqa: E402

BASELINE = "v2_baseline"
FIRST = "v1_first_try"


def slug(name):
    return re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()


def spec_for(name):
    return name if name == "fake-support" or name.split(":", 1)[0] in ("openai", "ollama", "bedrock") else f"openai:{name}"


def prompt_files(folder, only=None):
    files = sorted(glob.glob(os.path.join(folder, "*.txt")))
    if only:
        files = [f for f in files if os.path.splitext(os.path.basename(f))[0] in only]
    return files


def cmd_run(a):
    suite = load_suite(a.suite)
    cache_dir = a.cache_dir or os.path.join(a.out, "cache")
    os.makedirs(cache_dir, exist_ok=True)
    only = [p.strip() for p in a.only.split(",")] if a.only else None
    options = {"fence_ok": True} if a.fence_ok else None
    for name in [m.strip() for m in a.models.split(",") if m.strip()]:
        spec = spec_for(name)
        inner = build_model(spec, base_url=a.base_url, temperature=a.temperature, max_tokens=a.max_tokens)
        model = CachedModel(inner, os.path.join(cache_dir, slug(spec) + ".jsonl"), a.cache_mode)
        os.makedirs(os.path.join(a.out, slug(spec)), exist_ok=True)
        for path in prompt_files(a.prompts, only):
            stem = os.path.splitext(os.path.basename(path))[0]
            t0, before = time.time(), model.calls
            report = run_suite(suite, open(path, encoding="utf-8").read(), model, samples=a.samples, grader_options=options)
            report["prompt_file"] = os.path.basename(path)
            report["settings"] = {"temperature": a.temperature, "max_tokens": a.max_tokens, "samples": a.samples}
            with open(os.path.join(a.out, slug(spec), stem + ".json"), "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
            t = report["totals"]
            print(f"{spec:28} {stem:14} pass {t['passes']}/{t['runs']} ({t['pass_rate']:.1%})  "
                  f"in {t['input_tokens_avg']} out {t['output_tokens_avg']}  "
                  f"{model.calls - before} new calls, {time.time() - t0:.0f}s", flush=True)
    return 0


def load_reports(out):
    reports = {}
    for path in sorted(glob.glob(os.path.join(out, "*", "*.json"))):
        if os.path.basename(os.path.dirname(path)) == "cache":
            continue
        rep = json.load(open(path, encoding="utf-8"))
        if not isinstance(rep, dict) or "cases" not in rep:      # e.g. the determinism checks
            continue
        reports.setdefault(rep["model"], {})[os.path.splitext(os.path.basename(path))[0]] = rep
    return reports


def pct(x):
    return f"{x:.1%}"


def interval(rep):
    lo, hi = wilson(rep["totals"]["passes"], rep["totals"]["runs"])
    return f"{pct(lo)} to {pct(hi)}"


def growth(new, old):
    return "n/a" if not old else f"{new / old - 1:+.0%}"


def why(res):
    text = "; ".join(res.failures) or "no rule fired"
    if res.improved:
        text += f" (improved: {', '.join(res.improved)})"
    return text


def verdict(res):
    return "PASS" if res.passed else "BLOCKED"


def cell(case):
    return "n/a" if case is None else f"{case['passes']}/{case['samples']}" + (" (must pass)" if case["must_pass"] else "")


def cmd_summary(a):
    reports = load_reports(a.out)
    if not reports:
        print(f"no reports in {a.out}; run `run` first", file=sys.stderr)
        return 2
    base_name, first_name = a.baseline, a.first
    md = ["# Prompt gate results", ""]
    data = {"baseline": base_name, "first": first_name, "fixes": [], "prompt_changes": [], "model_swaps": [],
            "baseline_cases": {}, "baseline_failed_checks": {}}
    for model, by_prompt in reports.items():
        if base_name not in by_prompt:
            continue
        base = by_prompt[base_name]
        bt = base["totals"]
        md += [f"## {model}", "",
               f"Baseline `{base_name}`: {bt['passes']}/{bt['runs']} passed ({pct(bt['pass_rate'])}, 95% range "
               f"{interval(base)}), {bt['input_tokens_avg']} input and {bt['output_tokens_avg']} output tokens on average.",
               ""]
        if first_name in by_prompt:
            first = by_prompt[first_name]
            ft, res = first["totals"], compare(first, base)
            md += [f"From `{first_name}` ({ft['passes']}/{ft['runs']}, {pct(ft['pass_rate'])}) to `{base_name}`: "
                   f"**{verdict(res)}**. {why(res)}", ""]
            data["fixes"].append({"model": model, "passed": res.passed, "failures": res.failures,
                                  "regressed": res.regressed, "improved": res.improved, "metrics": res.metrics})
        md += ["| change | passed | pass rate | 95% range | input tokens | output tokens | verdict | why |",
               "|---|---|---|---|---|---|---|---|"]
        for stem, cand in by_prompt.items():
            if stem in (base_name, first_name):
                continue
            res, ct = compare(base, cand), cand["totals"]
            md.append(f"| `{stem}` | {ct['passes']}/{ct['runs']} | {pct(ct['pass_rate'])} | {interval(cand)} "
                      f"| {ct['input_tokens_avg']} ({growth(ct['input_tokens_avg'], bt['input_tokens_avg'])}) "
                      f"| {ct['output_tokens_avg']} ({growth(ct['output_tokens_avg'], bt['output_tokens_avg'])}) "
                      f"| {verdict(res)} | {why(res)} |")
            data["prompt_changes"].append({"model": model, "change": stem, "passed": res.passed,
                                           "failures": res.failures, "regressed": res.regressed,
                                           "improved": res.improved, "metrics": res.metrics,
                                           "failed_checks": cand.get("failed_checks", {})})
        md.append("")
        checks = base.get("failed_checks", {})
        if checks:
            md += ["Checks that failed most on the baseline: "
                   + ", ".join(f"{k} ({v})" for k, v in list(checks.items())[:5]), ""]
        data["baseline_cases"][model] = {cid: f"{c['passes']}/{c['samples']}" for cid, c in base["cases"].items()}
        data["baseline_failed_checks"][model] = checks

    models = [m for m in reports if base_name in reports[m]]
    for title, stem in ((f"Baseline prompt `{base_name}`, case by case", base_name),
                        (f"First try `{first_name}`, case by case", first_name)):
        have = [m for m in models if stem in reports[m]]
        if not have:
            continue
        md += [f"## {title}", "", "| case | " + " | ".join(have) + " |", "|---|" + "---|" * len(have)]
        for cid in reports[have[0]][stem]["cases"]:
            md.append(f"| {cid} | " + " | ".join(cell(reports[m][stem]["cases"].get(cid)) for m in have) + " |")
        md.append("| **all** | " + " | ".join(
            f"**{reports[m][stem]['totals']['passes']}/{reports[m][stem]['totals']['runs']}**" for m in have) + " |")
        md.append("")
    if len(models) > 1:
        md += ["## Model swaps on the baseline prompt", "",
               "Same prompt, same suite. `from` is the model you run today, `to` is the model you want to move to.",
               "", "| from | to | pass rate | verdict | why |", "|---|---|---|---|---|"]
        for a_m in models:
            for b_m in models:
                if a_m == b_m:
                    continue
                base, cand = reports[a_m][base_name], reports[b_m][base_name]
                res = compare(base, cand)
                md.append(f"| {a_m} | {b_m} | {pct(base['totals']['pass_rate'])} to {pct(cand['totals']['pass_rate'])} "
                          f"| {verdict(res)} | {why(res)} |")
                data["model_swaps"].append({"from": a_m, "to": b_m, "passed": res.passed, "failures": res.failures,
                                            "regressed": res.regressed, "improved": res.improved,
                                            "metrics": res.metrics})
        md.append("")
    open(os.path.join(a.out, "summary.md"), "w", encoding="utf-8").write("\n".join(md))
    json.dump(data, open(os.path.join(a.out, "summary.json"), "w", encoding="utf-8"), indent=2)
    print("\n".join(md))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="run_matrix")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--models", required=True, help="comma-separated; a bare name means openai:<name>")
    r.add_argument("--base-url", dest="base_url", help="default http://localhost:11434/v1 (Ollama)")
    r.add_argument("--suite", default=os.path.join(HERE, "examples", "suite.json"))
    r.add_argument("--prompts", default=os.path.join(HERE, "examples", "prompts"))
    r.add_argument("--only", help="comma-separated prompt names (without .txt) to run; default all")
    r.add_argument("--samples", type=int, default=5)
    r.add_argument("--temperature", type=float, default=0.7)
    r.add_argument("--max-tokens", dest="max_tokens", type=int, default=256)
    r.add_argument("--cache-mode", default="auto", choices=CachedModel.MODES)
    r.add_argument("--cache-dir", dest="cache_dir", help="where recordings live (default <out>/cache)")
    r.add_argument("--fence-ok", dest="fence_ok", action="store_true",
                   help="JSON graders accept a reply wrapped in a ```json code fence")
    r.add_argument("--out", default=os.path.join(HERE, "results"))
    r.set_defaults(fn=cmd_run)
    s = sub.add_parser("summary")
    s.add_argument("--out", default=os.path.join(HERE, "results"))
    s.add_argument("--baseline", default=BASELINE, help="prompt name every change is compared to")
    s.add_argument("--first", default=FIRST, help="earlier prompt that the baseline replaced (optional)")
    s.set_defaults(fn=cmd_summary)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
