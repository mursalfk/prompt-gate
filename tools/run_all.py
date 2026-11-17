# code/tools/run_all.py
"""One command that produces every number in the article on your own machine.

  ollama pull qwen2.5:1.5b
  ollama pull llama3.2:3b
  ollama pull gemma3:1b
  python tools/run_all.py

What it does, in order:
  1. checks that the model server answers (Ollama by default, http://localhost:11434/v1)
  2. runs every prompt in examples/prompts against every model: 6 prompts x 16 cases x 5 samples
     (95 calls each). Every answer is recorded under results/cache/, so running it again is free.
  3. for each model, checks how repeatable the server is (tools/determinism_check.py)
  4. regrades the same recordings with JSON code fences allowed, into results-fence-ok/ (no new calls)
  5. writes results/summary.md and results-fence-ok/summary.md

Send results/summary.md, results-fence-ok/summary.md and results/determinism/ back, or commit the
whole results/ folder: it is also what the GitHub Actions workflow replays.
"""
import argparse
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from tools import determinism_check, run_matrix      # noqa: E402

MODELS = "qwen2.5:1.5b,llama3.2:3b,gemma3:1b"


def server_ok(base_url):
    try:
        urllib.request.urlopen(base_url.rstrip("/") + "/models", timeout=5).read()
        return True
    except Exception as e:                                          # noqa: BLE001
        print(f"cannot reach {base_url}: {e}", file=sys.stderr)
        return False


def main(argv=None):
    p = argparse.ArgumentParser(prog="run_all")
    p.add_argument("--models", default=MODELS)
    p.add_argument("--base-url", dest="base_url", default="http://localhost:11434/v1")
    p.add_argument("--out", default=os.path.join(HERE, "results"))
    p.add_argument("--suite", default=os.path.join(HERE, "examples", "suite.json"))
    p.add_argument("--prompts", default=os.path.join(HERE, "examples", "prompts"))
    p.add_argument("--baseline-prompt", dest="baseline_prompt", default=None,
                   help="prompt file for the determinism check (default: <prompts>/v2_baseline.txt)")
    p.add_argument("--samples", type=int, default=5)
    p.add_argument("--temperature", type=float, default=0.7)
    p.add_argument("--max-tokens", dest="max_tokens", type=int, default=256)
    p.add_argument("--skip-determinism", dest="skip_determinism", action="store_true")
    a = p.parse_args(argv)

    models = [m.strip() for m in a.models.split(",") if m.strip()]
    fake = all(m == "fake-support" for m in models)
    if not fake and not server_ok(a.base_url):
        print("Start Ollama (it runs a server on that address) and pull the models, then run this again.",
              file=sys.stderr)
        return 2

    common = ["--suite", a.suite, "--prompts", a.prompts, "--samples", str(a.samples),
              "--temperature", str(a.temperature), "--max-tokens", str(a.max_tokens), "--base-url", a.base_url]
    cache_dir = os.path.join(a.out, "cache")
    fence_out = a.out.rstrip("/\\") + "-fence-ok"

    print(f"\n[1/4] running every prompt on {', '.join(models)}", flush=True)
    rc = run_matrix.main(["run", "--models", a.models, "--out", a.out, "--cache-dir", cache_dir, *common])
    if rc:
        return rc

    if not a.skip_determinism:
        print("\n[2/4] repeatability checks (live calls, not recorded)", flush=True)
        os.makedirs(os.path.join(a.out, "determinism"), exist_ok=True)
        prompt = a.baseline_prompt or os.path.join(a.prompts, "v2_baseline.txt")
        for m in models:
            if m == "fake-support":
                continue
            rc = determinism_check.main(["--model", m, "--base-url", a.base_url, "--suite", a.suite,
                                         "--prompt", prompt, "--temperature", str(a.temperature),
                                         "--max-tokens", str(a.max_tokens),
                                         "--out", os.path.join(a.out, "determinism", run_matrix.slug(run_matrix.spec_for(m)) + ".json")])
            if rc:
                return rc

    print("\n[3/4] regrading the same recordings with code fences allowed (no new calls)", flush=True)
    rc = run_matrix.main(["run", "--models", a.models, "--out", fence_out, "--cache-dir", cache_dir,
                          "--cache-mode", "replay", "--fence-ok", *common])
    if rc:
        return rc

    print("\n[4/4] summaries", flush=True)
    for out in (a.out, fence_out):
        rc = run_matrix.main(["summary", "--out", out])
        if rc:
            return rc
    print(f"\nDone. Read {os.path.join(a.out, 'summary.md')} and {os.path.join(fence_out, 'summary.md')}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
