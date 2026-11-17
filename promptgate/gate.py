# code/promptgate/gate.py
"""Compare a candidate report to a baseline report and decide: ship or block.

Five rules. Each one fails the gate on its own.
 1. must-pass cases: a case marked must_pass needs pass rate >= must_pass_min in the candidate.
 2. regressed cases: a case whose pass rate fell by >= case_drop vs baseline counts as regressed;
    more than max_regressed_cases blocks.
 3. overall quality: the candidate's 95% Wilson interval sits entirely below the baseline's interval
    (upper bound below the baseline's lower bound, minus tolerance), i.e. worse by more than noise.
 4. token creep: average input or output tokens grew by more than max_token_growth.
 5. cost creep: cost per 1k runs grew by more than max_cost_growth (only when prices were given).
"""
import math
from dataclasses import dataclass, field

DEFAULTS = {
    "must_pass_min": 1.0,
    "case_drop": 0.5,
    "max_regressed_cases": 0,
    "tolerance": 0.0,
    "max_token_growth": 0.25,
    "max_cost_growth": 0.25,
    "allow_suite_mismatch": False,
}


def wilson(passes, n, z=1.96):
    """Wilson score interval for a pass rate. Behaves sensibly at 0%, 100% and small n."""
    if n == 0:
        return 0.0, 1.0
    p = passes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


@dataclass
class GateResult:
    passed: bool
    failures: list = field(default_factory=list)
    improved: list = field(default_factory=list)
    regressed: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


def _rate(c):
    return c["passes"] / c["samples"] if c["samples"] else 0.0


def compare(baseline, candidate, **overrides):
    cfg = {**DEFAULTS, **overrides}
    failures, improved, regressed = [], [], []
    bc, cc = baseline["cases"], candidate["cases"]

    if set(bc) != set(cc):
        only_b, only_c = sorted(set(bc) - set(cc)), sorted(set(cc) - set(bc))
        msg = f"suite mismatch (only in baseline: {only_b}, only in candidate: {only_c})"
        if not cfg["allow_suite_mismatch"]:
            failures.append(msg)
    shared = sorted(set(bc) & set(cc))

    for cid in shared:
        b, c = _rate(bc[cid]), _rate(cc[cid])
        if cc[cid]["must_pass"] and c < cfg["must_pass_min"]:
            # name the failing check only; the full reason and the failing answers are in the report itself
            why = f" ({cc[cid]['failures'][0].split(': ', 1)[0]})" if cc[cid]["failures"] else ""
            failures.append(f"must-pass case {cid} at {c:.0%}, needs {cfg['must_pass_min']:.0%} "
                            f"(baseline was {b:.0%}){why}")
        if b - c >= cfg["case_drop"]:
            regressed.append(cid)
        elif c - b >= cfg["case_drop"]:
            improved.append(cid)
    if len(regressed) > cfg["max_regressed_cases"]:
        failures.append(f"{len(regressed)} regressed case(s): {', '.join(regressed)}")

    bt, ct = baseline["totals"], candidate["totals"]
    lo, hi = wilson(ct["passes"], ct["runs"])
    b_lo, b_hi = wilson(bt["passes"], bt["runs"])
    if hi < b_lo - cfg["tolerance"]:
        failures.append(
            f"overall pass rate {ct['pass_rate']:.1%} (95% interval {lo:.1%} to {hi:.1%}) is "
            f"significantly below baseline {bt['pass_rate']:.1%} (95% interval {b_lo:.1%} to {b_hi:.1%})")

    for key, label in (("input_tokens_avg", "input tokens"), ("output_tokens_avg", "output tokens")):
        if bt[key] > 0 and ct[key] > bt[key] * (1 + cfg["max_token_growth"]):
            failures.append(f"{label} grew {ct[key] / bt[key] - 1:.0%} ({bt[key]} to {ct[key]}), "
                            f"limit {cfg['max_token_growth']:.0%}")
    if bt["cost_per_1k_runs"] > 0 and ct["cost_per_1k_runs"] > bt["cost_per_1k_runs"] * (1 + cfg["max_cost_growth"]):
        failures.append(f"cost per 1k runs grew {ct['cost_per_1k_runs'] / bt['cost_per_1k_runs'] - 1:.0%}, "
                        f"limit {cfg['max_cost_growth']:.0%}")

    return GateResult(
        passed=not failures, failures=failures, improved=improved, regressed=regressed,
        metrics={
            "baseline_pass_rate": bt["pass_rate"], "candidate_pass_rate": ct["pass_rate"],
            "baseline_interval": [round(b_lo, 4), round(b_hi, 4)],
            "candidate_interval": [round(lo, 4), round(hi, 4)],
            "input_tokens": [bt["input_tokens_avg"], ct["input_tokens_avg"]],
            "output_tokens": [bt["output_tokens_avg"], ct["output_tokens_avg"]],
            "cost_per_1k_runs": [bt["cost_per_1k_runs"], ct["cost_per_1k_runs"]],
        })


def to_markdown(result, baseline, candidate):
    """A summary you can paste into a pull request comment."""
    m = result.metrics
    lines = [f"## Prompt gate: {'PASS' if result.passed else 'BLOCKED'}", "",
             "| metric | baseline | candidate |", "|---|---|---|",
             f"| model | {baseline.get('model', '?')} | {candidate.get('model', '?')} |",
             f"| pass rate | {m['baseline_pass_rate']:.1%} "
             f"(95%: {m['baseline_interval'][0]:.1%} to {m['baseline_interval'][1]:.1%}) "
             f"| {m['candidate_pass_rate']:.1%} "
             f"(95%: {m['candidate_interval'][0]:.1%} to {m['candidate_interval'][1]:.1%}) |",
             f"| input tokens (avg) | {m['input_tokens'][0]} | {m['input_tokens'][1]} |",
             f"| output tokens (avg) | {m['output_tokens'][0]} | {m['output_tokens'][1]} |",
             f"| cost per 1k runs | {m['cost_per_1k_runs'][0]} | {m['cost_per_1k_runs'][1]} |",
             f"| prompt | `{baseline['prompt_sha']}` | `{candidate['prompt_sha']}` |", ""]
    if result.failures:
        lines += ["**Why it was blocked**", ""] + [f"- {f}" for f in result.failures] + [""]
    if result.regressed:
        lines.append("Regressed: " + ", ".join(result.regressed))
    if result.improved:
        lines.append("Improved: " + ", ".join(result.improved))
    return "\n".join(lines)
