# code/tools/show_answers.py
"""Print every recorded answer next to the verdict the graders gave it. No model is called.

  python tools/show_answers.py --model qwen2.5:1.5b --prompt examples/prompts/v1.txt
  python tools/show_answers.py --model qwen2.5:1.5b --prompt examples/prompts/v1.txt --case refund_late

Use it to check your graders: read each answer, decide for yourself whether it is right, and
compare with the PASS or FAIL printed beside it. A grader that disagrees with you is a bug in
the grader, and fixing it costs nothing because the answers are replayed from the recording.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from promptgate.cli import build_model          # noqa: E402
from promptgate.graders import grade            # noqa: E402
from promptgate.models import CachedModel       # noqa: E402
from promptgate.suite import load_suite         # noqa: E402
from tools.run_matrix import slug, spec_for     # noqa: E402


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # model text can hold any character; a Windows console may not
    p = argparse.ArgumentParser(description="Show recorded answers with grader verdicts.")
    p.add_argument("--model", required=True)
    p.add_argument("--prompt", required=True)
    p.add_argument("--suite", default=os.path.join(HERE, "examples", "suite.json"))
    p.add_argument("--out", default=os.path.join(HERE, "results"))
    p.add_argument("--samples", type=int, default=5)
    p.add_argument("--temperature", type=float, default=0.7)
    p.add_argument("--max-tokens", type=int, default=256)
    p.add_argument("--cache-dir", dest="cache_dir", help="where recordings live (default <out>/cache)")
    p.add_argument("--fence-ok", dest="fence_ok", action="store_true",
                   help="JSON graders accept a reply wrapped in a ```json code fence")
    p.add_argument("--case", help="only this case id")
    p.add_argument("--only", choices=["pass", "fail"], help="only passing or only failing answers")
    a = p.parse_args(argv)

    spec = spec_for(a.model)
    inner = build_model(spec, temperature=a.temperature, max_tokens=a.max_tokens)
    cache_dir = a.cache_dir or os.path.join(a.out, "cache")
    model = CachedModel(inner, os.path.join(cache_dir, slug(spec) + ".jsonl"), "replay")
    options = {"fence_ok": True} if a.fence_ok else None
    system = open(a.prompt, encoding="utf-8").read()
    for case in load_suite(a.suite)["cases"]:
        if a.case and case["id"] != a.case:
            continue
        print(f"\n## {case['id']}   input: {case['input']}")
        for s in range(case.get("samples", a.samples)):
            r = model.invoke(system, case["input"], s)
            ok, reasons = grade(case["graders"], r, options)
            if a.only and (a.only == "pass") != ok:
                continue
            text = " ".join(r.text.split())
            print(f"  [{'PASS' if ok else 'FAIL'}] {text}")
            for why in reasons:
                print(f"         -> {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
