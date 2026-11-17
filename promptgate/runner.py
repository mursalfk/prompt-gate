# code/promptgate/runner.py
"""Run a suite against one system prompt and produce a report dict (JSON-friendly)."""
import hashlib

from .graders import grade_detail


def run_suite(suite, system_prompt, model, samples=3, prices=None, grader_options=None):
    """prices: optional {"input_per_1k": float, "output_per_1k": float}. Left out, cost is 0.
    grader_options: merged into every grader, e.g. {"fence_ok": True}.

    A sample passes only if every grader on the case passes. Each case runs `samples` times
    (or the case's own "samples") because model output is not identical from run to run.
    """
    prices = prices or {}
    cases, failed_checks = {}, {}
    total_pass = total_runs = 0
    in_tok = out_tok = lat = 0.0
    for case in suite["cases"]:
        n_case = case.get("samples", samples)
        passes = 0
        failures = []
        failed_outputs = []
        c_in = c_out = c_lat = 0.0
        for s in range(n_case):
            r = model.invoke(system_prompt, case["input"], s)
            detail = grade_detail(case["graders"], r, grader_options)
            reasons = [why for _, ok, why in detail if not ok]
            ok = not reasons
            passes += ok
            for name, good, _ in detail:
                if not good:
                    failed_checks[name] = failed_checks.get(name, 0) + 1
            if not ok and len(failures) < 3:
                failures.extend(reasons[:1])
            if not ok and len(failed_outputs) < 2:
                failed_outputs.append(r.text[:300])      # keep a little evidence for whoever reads the report
            c_in += r.input_tokens
            c_out += r.output_tokens
            c_lat += r.latency_ms
        cases[case["id"]] = {
            "passes": passes,
            "samples": n_case,
            "must_pass": bool(case.get("must_pass", False)),
            "tags": case.get("tags", []),
            "failures": failures,
            "failed_outputs": failed_outputs,
            "input_tokens": round(c_in / n_case, 1),
            "output_tokens": round(c_out / n_case, 1),
            "latency_ms": round(c_lat / n_case, 1),
        }
        total_pass += passes
        total_runs += n_case
        in_tok += c_in
        out_tok += c_out
        lat += c_lat
    n = total_runs or 1
    avg_in, avg_out = in_tok / n, out_tok / n
    cost_1k = 1000 * (avg_in / 1000 * prices.get("input_per_1k", 0) + avg_out / 1000 * prices.get("output_per_1k", 0))
    return {
        "model": getattr(model, "name", "unknown"),
        "prompt_sha": hashlib.sha256(system_prompt.encode()).hexdigest()[:12],
        "samples": samples,
        "grader_options": grader_options or {},
        "cases": cases,
        # how many samples failed each check, most common first: the fastest way to see WHAT broke
        "failed_checks": dict(sorted(failed_checks.items(), key=lambda kv: -kv[1])),
        "totals": {
            "passes": total_pass,
            "runs": total_runs,
            "pass_rate": round(total_pass / n, 4),
            "input_tokens_avg": round(avg_in, 1),
            "output_tokens_avg": round(avg_out, 1),
            "latency_ms_avg": round(lat / n, 1),
            "cost_per_1k_runs": round(cost_1k, 4),
        },
    }
