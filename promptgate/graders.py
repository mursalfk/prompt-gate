# code/promptgate/graders.py
"""Deterministic graders. A grader spec is a dict like {"type": "contains", "value": "30 days"}.

No model judges another model here. Every check is a plain function you can read, so a
failure always has a reason you can act on.

Two optional fields work on every grader:
  "label"     a short human name, used in failure messages and in the report's failed_checks tally
  "fence_ok"  JSON graders only: accept a reply wrapped in a Markdown code fence (```json ... ```).
              Leave it off if your app parses the reply as it comes.
"""
import json
import re

_FENCE = re.compile(r"^```[a-zA-Z0-9]*\s*(.*?)\s*```$", re.S)


def strip_fence(text):
    """'```json {..} ```' -> '{..}'. Text without a fence is returned unchanged."""
    t = text.strip()
    m = _FENCE.match(t)
    return m.group(1) if m else t


def _load(spec, result):
    text = result.text.strip()
    if spec.get("fence_ok"):
        text = strip_fence(text)
    return json.loads(text)


def _text(result, spec):
    t = result.text
    return t.lower() if spec.get("ci", True) else t


def _val(spec):
    v = spec["value"]
    return v.lower() if spec.get("ci", True) else v


def g_contains(spec, result):
    ok = _val(spec) in _text(result, spec)
    return ok, "" if ok else f"missing {spec['value']!r}"


def g_not_contains(spec, result):
    ok = _val(spec) not in _text(result, spec)
    return ok, "" if ok else f"contains forbidden {spec['value']!r}"


def g_regex(spec, result):
    ok = re.search(spec["value"], result.text) is not None
    return ok, "" if ok else f"no match for /{spec['value']}/"


def g_not_regex(spec, result):
    ok = re.search(spec["value"], result.text) is None
    return ok, "" if ok else f"matched forbidden /{spec['value']}/"


def g_json(spec, result):
    try:
        _load(spec, result)
        return True, ""
    except ValueError:
        return False, "output is not valid JSON"


def g_json_keys(spec, result):
    try:
        data = _load(spec, result)
    except ValueError:
        return False, "output is not valid JSON"
    if not isinstance(data, dict):
        return False, "JSON is not an object"
    missing = [k for k in spec["keys"] if k not in data]
    return (not missing), ("" if not missing else f"missing keys {missing}")


def g_json_field_in(spec, result):
    try:
        data = _load(spec, result)
    except ValueError:
        return False, "output is not valid JSON"
    val = data.get(spec["field"]) if isinstance(data, dict) else None
    ok = val in spec["values"]
    return ok, "" if ok else f"{spec['field']}={val!r} not in {spec['values']}"


def g_json_field_max_words(spec, result):
    """Word limit on one JSON field. If the reply is not JSON, the whole reply is counted, so a
    model cannot dodge the limit by breaking the format."""
    try:
        data = _load(spec, result)
        val = data.get(spec["field"]) if isinstance(data, dict) else None
    except ValueError:
        val = None
    text = val if isinstance(val, str) else result.text
    n = len(text.split())
    ok = n <= spec["value"]
    return ok, "" if ok else f"{n} words, limit {spec['value']}"


def g_max_words(spec, result):
    n = len(result.text.split())
    ok = n <= spec["value"]
    return ok, "" if ok else f"{n} words, limit {spec['value']}"


def g_min_words(spec, result):
    n = len(result.text.split())
    ok = n >= spec["value"]
    return ok, "" if ok else f"{n} words, need at least {spec['value']}"


def g_stop_reason(spec, result):
    want = spec.get("value", "end_turn")
    ok = result.stop_reason == want
    return ok, "" if ok else f"stop reason {result.stop_reason!r}, wanted {want!r}"


GRADERS = {
    "contains": g_contains,
    "not_contains": g_not_contains,
    "regex": g_regex,
    "not_regex": g_not_regex,
    "json": g_json,
    "json_keys": g_json_keys,
    "json_field_in": g_json_field_in,
    "json_field_max_words": g_json_field_max_words,
    "max_words": g_max_words,
    "min_words": g_min_words,
    "stop_reason": g_stop_reason,
}


def label(spec):
    """Short name for a grader, for messages and tallies."""
    if spec.get("label"):
        return spec["label"]
    v = spec.get("value", spec.get("keys", spec.get("values", "")))
    return f"{spec['type']} {v}".strip()


def grade_detail(specs, result, options=None):
    """Run every grader on one result. Returns [(label, passed, reason)] in suite order.

    `options` are merged into every spec first (the spec wins), e.g. {"fence_ok": True}.
    """
    out = []
    for spec in specs:
        s = {**options, **spec} if options else spec
        ok, why = GRADERS[s["type"]](s, result)
        out.append((label(s), ok, "" if ok else f"{label(s)}: {why}"))
    return out


def grade(specs, result, options=None):
    """Returns (passed, [failure reasons])."""
    reasons = [why for _, ok, why in grade_detail(specs, result, options) if not ok]
    return (not reasons), reasons
