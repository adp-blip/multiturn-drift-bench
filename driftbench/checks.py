"""Deterministic checks. These run before the LLM judge and are the main signal."""
import re


def _norm(s):
    s = s.strip().strip("`*\"'.“” \n")
    return re.sub(r"\s+", " ", s).lower()


def word_count(text):
    return len(re.findall(r"\b[\w'’-]+\b", text))


def run_check(check, text):
    """Returns (passed, note)."""
    kind, val = check["type"], check.get("value")
    low = text.lower()

    if kind == "max_words":
        n = word_count(text)
        return n <= val, f"{n} words (limit {val})"
    if kind == "contains":
        return val.lower() in low, f"needs '{val}'"
    if kind == "not_contains":
        return val.lower() not in low, f"must not say '{val}'"
    if kind == "contains_any":
        hit = [v for v in val if v.lower() in low]
        return bool(hit), f"found {hit}" if hit else f"none of {val}"
    if kind == "not_contains_any":
        # allow a plural "s" so "egg" also catches "eggs"
        hit = [v for v in val if re.search(r"\b" + re.escape(v.lower()) + r"s?\b", low)]
        return not hit, f"banned terms used: {hit}" if hit else "clean"
    if kind == "no_list":
        bad = [l for l in text.splitlines() if re.match(r"\s*([-*•]|\d+[.)])\s+", l)]
        return not bad, f"{len(bad)} list lines"
    if kind == "ends_with_question":
        last = text.strip().rstrip("*_ )\"'")
        return last.endswith("?"), "ends with '?'" if last.endswith("?") else "no closing question"
    if kind == "normalized_equals":
        ok = _norm(text) == _norm(val)
        return ok, "exact match" if ok else f"got '{text.strip()[:80]}'"
    raise ValueError(f"unknown check type: {kind}")
