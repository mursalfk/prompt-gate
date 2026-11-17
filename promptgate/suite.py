# code/promptgate/suite.py
"""Load and validate a test suite (plain JSON, no dependencies).

{
  "name": "...",
  "all_cases": [ <grader>, ... ],        optional: checks added to every case
  "cases": [
    {"id": "...", "input": "...", "graders": [ <grader>, ... ],
     "must_pass": false,                 optional: one failed sample blocks the gate
     "skip_all_cases": false,            optional: this case uses only its own graders
     "samples": 10,                      optional: overrides --samples for this case
     "tags": ["..."]}                    optional
  ]
}
"""
import json

from .graders import GRADERS

REQUIRED_FIELDS = {
    "contains": ["value"], "not_contains": ["value"], "regex": ["value"], "not_regex": ["value"],
    "json": [], "json_keys": ["keys"], "json_field_in": ["field", "values"],
    "json_field_max_words": ["field", "value"],
    "max_words": ["value"], "min_words": ["value"], "stop_reason": [],
}


class SuiteError(ValueError):
    pass


def _check_graders(where, graders):
    for g in graders:
        t = g.get("type")
        if t not in GRADERS:
            raise SuiteError(f"{where}: unknown grader type {t!r}")
        for f in REQUIRED_FIELDS[t]:
            if f not in g:
                raise SuiteError(f"{where}: grader {t!r} needs field {f!r}")


def validate(suite):
    """Checks the suite and returns it with `all_cases` graders appended to each case."""
    if not isinstance(suite, dict) or not isinstance(suite.get("cases"), list) or not suite["cases"]:
        raise SuiteError("suite needs a non-empty 'cases' list")
    shared = suite.get("all_cases", [])
    if not isinstance(shared, list):
        raise SuiteError("'all_cases' must be a list of graders")
    _check_graders("all_cases", shared)
    seen, cases = set(), []
    for c in suite["cases"]:
        cid = c.get("id")
        if not cid:
            raise SuiteError("every case needs an 'id'")
        if cid in seen:
            raise SuiteError(f"duplicate case id: {cid}")
        seen.add(cid)
        if not isinstance(c.get("input"), str) or not c["input"].strip():
            raise SuiteError(f"{cid}: 'input' must be a non-empty string")
        own = c.get("graders") or []
        _check_graders(cid, own)
        graders = own if c.get("skip_all_cases") else own + shared
        if not graders:
            raise SuiteError(f"{cid}: needs at least one grader")
        if "samples" in c and (not isinstance(c["samples"], int) or c["samples"] < 1):
            raise SuiteError(f"{cid}: 'samples' must be a positive whole number")
        cases.append({**c, "graders": graders})
    return {**suite, "cases": cases}


def load_suite(path):
    with open(path, encoding="utf-8") as f:
        return validate(json.load(f))
