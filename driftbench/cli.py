"""driftbench: run multi-turn drift conversations against models and grade the last reply."""
import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

from .checks import run_check
from .judge import judge
from .llm import chat

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
                ok, notes = grade(case, reply, args.judge)
            except Exception as e:  # keep going, one broken call shouldn't kill the run
                reply, ok, notes = "", False, [f"error: {e}"]
            rows.append({"model": model, "id": case["id"], "axis": case["axis"], "title": case["title"],
                         "passed": ok, "reply": reply, "notes": notes,
                         "seconds": round(time.time() - t0, 1)})
            print(f"  {'PASS' if ok else 'FAIL'}  {case['id']:6} {case['title']}")
        with open(out_dir / f"{safe_name(model)}.jsonl", "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    report(out_dir, args.judge)


def report(out_dir, judge_model=None):
    out_dir = Path(out_dir)
    runs = {}
    for p in sorted(out_dir.glob("*.jsonl")):
        rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
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
