# code/tools/determinism_check.py
"""How repeatable is your model server? Sends each suite input 3 times in three ways and counts
how many inputs came back with identical text all 3 times.

  python tools/determinism_check.py --model qwen2.5:1.5b

  temperature 0, seeds 0-2      -> if every input is identical, sampling a case 5 times at
                                   temperature 0 tells you nothing new on this server
  your temperature, same seed   -> if this is not identical, a seed does not make runs repeatable
  your temperature, seeds 0-2   -> how much the answers really vary
Nothing is cached: every request is a live call.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from promptgate.cli import build_model      # noqa: E402
from promptgate.suite import load_suite     # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser(prog="determinism_check")
    p.add_argument("--model", required=True, help="a bare name means openai:<name>")
    p.add_argument("--base-url", dest="base_url")
    p.add_argument("--suite", default=os.path.join(HERE, "examples", "suite.json"))
    p.add_argument("--prompt", default=os.path.join(HERE, "examples", "prompts", "v2_baseline.txt"))
    p.add_argument("--temperature", type=float, default=0.7)
    p.add_argument("--max-tokens", dest="max_tokens", type=int, default=256)
    p.add_argument("--out")
    a = p.parse_args(argv)
    spec = a.model if a.model.split(":", 1)[0] in ("openai", "ollama", "bedrock") else f"openai:{a.model}"
    system, cases = open(a.prompt, encoding="utf-8").read(), load_suite(a.suite)["cases"]
    modes = [("temperature 0, seeds 0-2", 0.0, (0, 1, 2)),
             (f"temperature {a.temperature}, same seed", a.temperature, (0, 0, 0)),
             (f"temperature {a.temperature}, seeds 0-2", a.temperature, (0, 1, 2))]
    result = {"model": spec, "inputs": len(cases), "modes": {}}
    for label, temp, seeds in modes:
        model = build_model(spec, base_url=a.base_url, temperature=temp, max_tokens=a.max_tokens)
        identical = sum(len({model.invoke(system, c["input"], s).text for s in seeds}) == 1 for c in cases)
        result["modes"][label] = identical
        print(f"{spec:24} {label:32} identical on {identical} of {len(cases)} inputs", flush=True)
    if a.out:
        json.dump(result, open(a.out, "w", encoding="utf-8"), indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
