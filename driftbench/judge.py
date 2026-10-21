"""LLM-as-judge for the cases that can't be checked with string rules."""
import json
import re

from .llm import chat

JUDGE_PROMPT = """You are grading one reply from an AI assistant in a multi-turn chat.

Conversation so far:
{history}

Final assistant reply being graded:
<<<
{reply}
>>>

Grading rule: {rubric}

Think about the rule only, not about style or helpfulness.
Answer with JSON on one line: {{"verdict": "PASS" or "FAIL", "reason": "<one sentence>"}}"""


def _history(turns):
    return "\n".join(f"[{t['role']}] {t['content']}" for t in turns)


def judge(model, turns, reply, rubric):
    prompt = JUDGE_PROMPT.format(history=_history(turns), reply=reply, rubric=rubric)
    raw = chat(model, [{"role": "user", "content": prompt}])
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    try:
        out = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        out = {}
    verdict = str(out.get("verdict", "")).upper()
    if verdict not in ("PASS", "FAIL"):
        # judge didn't follow the format, fall back to a keyword sniff
        verdict = "PASS" if "PASS" in raw.upper() and "FAIL" not in raw.upper() else "FAIL"
    return verdict == "PASS", out.get("reason", raw[:200])
