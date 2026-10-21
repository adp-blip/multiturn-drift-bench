"""driftbench: run multi-turn drift conversations against models and grade the last reply."""
import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

from .checks import run_check
from .judge import judge
from .llm import LAST_USAGE, chat

AXES = ["instruction_retention", "inference_memory", "versioned_editing", "self_coherence"]
DEFAULT_MODELS = ["groq:openai/gpt-oss-20b", "groq:qwen/qwen3.8-27b", "groq:allam-2-7b"]
DEFAULT_JUDGE = "groq:openai/gpt-oss-120b"


def load_cases(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def grade(case, reply, judge_model):
    notes, ok = [], True
    for c in case.get("checks", []):
        passed, note = run_check(c, reply)
        notes.append(f"{c['type']}: {'ok' if passed else 'FAIL'} ({note})")
        ok &= passed
    if case.get("rubric") and judge_model:
        passed, reason = judge(judge_model, case["turns"], reply, case["rubric"])
        notes.append(f"judge: {'ok' if passed else 'FAIL'} ({reason})")
        ok &= passed
    return ok, notes


def _timing(u):
    if not u:
        return {}
    return {"queue_s": round(u.get("queue_time", 0), 3), "total_s": round(u.get("total_time", 0), 3),
            "completion_tokens": u.get("completion_tokens", 0),
            "tok_per_s": round(u["completion_tokens"] / u["completion_time"], 1) if u.get("completion_time") else None}


def _pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))] if xs else None


def safe_name(model):
    return model.split(":", 1)[-1].replace("/", "_")


def run(args):
    cases = load_cases(args.data)
    if args.only:
        cases = [c for c in cases if c["id"] in args.only]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    for model in args.models:
        rows = []
        print(f"\n== {model} ({len(cases)} cases)")
        for case in cases:
            t0 = time.time()
            try:
                reply = chat(model, case["turns"])
                timing = _timing(LAST_USAGE)  # grab it before the judge call overwrites it
                ok, notes = grade(case, reply, args.judge)
            except Exception as e:  # keep going, one broken call shouldn't kill the run
                reply, ok, notes, timing = "", False, [f"error: {e}"], {}
            rows.append({"model": model, "id": case["id"], "axis": case["axis"], "title": case["title"],
                         "passed": ok, "reply": reply, "notes": notes, "timing": timing,
                         "seconds": round(time.time() - t0, 1)})
            print(f"  {'PASS' if ok else 'FAIL'}  {case['id']:6} {case['title']}")
        path = out_dir / f"{safe_name(model)}.jsonl"
        if args.only and path.exists():
            # partial re-run: replace just these cases, keep the rest of the earlier results
            fresh = {r["id"]: r for r in rows}
            old = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
            rows = [fresh.pop(r["id"], r) for r in old] + list(fresh.values())
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    report(out_dir, args.judge)


def report(out_dir, judge_model=DEFAULT_JUDGE):
    out_dir = Path(out_dir)
    runs, skipped = {}, []
    for p in sorted(out_dir.glob("*.jsonl")):
        rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        # API errors (e.g. rate limits) say nothing about the model, so they're left out of the scores
        errored = [r for r in rows if any(n.startswith("error:") for n in r["notes"])]
        if errored:
            skipped.append((rows[0].get("model", p.stem), [r["id"] for r in errored]))
        rows = [r for r in rows if r not in errored]
        runs[rows[0].get("model", p.stem) if rows else p.stem] = rows

    lines = ["# Results", ""]
    if judge_model:
        lines += [f"Judge model: `{judge_model}` (only used on cases with a rubric)", ""]
    lines += ["| model | " + " | ".join(a.replace("_", " ") for a in AXES) + " | overall |",
              "|---" * (len(AXES) + 2) + "|"]
    for model, rows in runs.items():
        by_axis = defaultdict(list)
        for r in rows:
            by_axis[r["axis"]].append(r["passed"])
        cells = [f"{sum(v)}/{len(v)}" for v in (by_axis[a] for a in AXES)]
        total = sum(r["passed"] for r in rows)
        lines.append(f"| `{model}` | " + " | ".join(cells) + f" | **{total}/{len(rows)}** |")

    if skipped:
        lines += ["", "Not scored (API error during the run, re-run pending): "
                  + "; ".join(f"`{m}`: {', '.join(ids)}" for m, ids in skipped)]
    has_timing = any(r.get("timing") for rows in runs.values() for r in rows)
    lines += [] if not has_timing else ["", "## Responsiveness", "",
              "Groq server-side timings for the model's final reply (judge calls excluded). `total` is time spent "
              "processing the request; `queue` is time waiting in Groq's queue before that.", "",
              "| model | median total (s) | p95 total (s) | median queue (s) | median output tok/s | median output tokens |",
              "|---|---|---|---|---|---|"]
    for model, rows in runs.items() if has_timing else []:
        t = [r["timing"] for r in rows if r.get("timing")]
        if not t:
            continue
        tps = [x["tok_per_s"] for x in t if x.get("tok_per_s")]
        lines.append(f"| `{model}` | {_pct([x['total_s'] for x in t], .5):.2f} | {_pct([x['total_s'] for x in t], .95):.2f} | "
                     f"{_pct([x['queue_s'] for x in t], .5):.2f} | {_pct(tps, .5) or '-'} | "
                     f"{_pct([x['completion_tokens'] for x in t], .5)} |")

    lines += ["", "## Failures", ""]
    for model, rows in runs.items():
        fails = [r for r in rows if not r["passed"]]
        if not fails:
            continue
        lines.append(f"### {model}")
        for r in fails:
            snippet = r["reply"].replace("\n", " ")[:220]
            lines.append(f"- **{r['id']}** ({r['title']}): " + "; ".join(n for n in r["notes"] if "FAIL" in n or "error" in n))
            lines.append(f"  > {snippet}")
        lines.append("")
    (out_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nwrote {out_dir / 'summary.md'}")


def main():
    ap = argparse.ArgumentParser(prog="driftbench")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the bench")
    r.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    r.add_argument("--judge", default=DEFAULT_JUDGE, help="judge model, '' to skip rubric cases")
    r.add_argument("--data", default="data/conversations.jsonl")
    r.add_argument("--out", default="results")
    r.add_argument("--only", nargs="*", help="case ids to run")
    s = sub.add_parser("report", help="rebuild summary.md from saved results")
    s.add_argument("--out", default="results")
    args = ap.parse_args()
    run(args) if args.cmd == "run" else report(args.out)


if __name__ == "__main__":
    main()
