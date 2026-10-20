"""UserPromptSubmit hook: remind Claude which project skills fit the prompt.

Reads the hook payload on stdin, matches the prompt against the situations
in CLAUDE.md's skill-trigger table, and prints a short reminder as
additionalContext. Silent when nothing matches. Never blocks the prompt.
"""
import json
import re
import sys

# (pattern, reminder) — patterns are matched case-insensitively against the
# prompt. Keep reminders to one line each; CLAUDE.md holds the full table.
TRIGGERS = [
    (r"\b(still off|not the point|the point (is|isn'?t|was)|that'?s (wrong|not (it|right|what))|"
     r"misunderst|you misread|not what i (meant|asked|want)|i meant)\b",
     "The user is correcting intent: use `domain-modeling` (capture the resolved meaning in "
     "GLOSSARY.md / an ADR this turn) and run `retro` at wrap-up."),
    (r"\b(bug|broken|crash\w*|traceback|exception|error|fails?|failing|failed|"
     r"doesn'?t work|not working|stuck|hangs?|regress\w*)\b",
     "Possible bug: `anthropic-skills:diagnosing-bugs` to find the cause, then `tdd` "
     "(failing test that reproduces it before the fix)."),
    (r"\b(kept original|keep(s|ing)? the original|skip(ped|s)?|savings|grow(s|th|n)?|grew|"
     r"convers\w+|re-?queue\w*|min(imum)? savings|don'?t grow|bigger|smaller)\b",
     "Touches keep/skip/replace rules: follow `encode-policy` (uniform library, ADR-0001)."),
    (r"\b(setting|settings page|toggle|option)\b",
     "If a setting is added/removed/changed: follow `optimizarr-setting`; prefer automatic "
     "behaviour over a new knob."),
    (r"\b(preset|crf|vmaf|xpsnr|film ?grain|bitrate cap|encoder speed|benchmark|"
     r"faster|slower|quality level)\b",
     "Encoder-setting question: measure with `encode-experiment` (numbers from "
     "`library-stats`) rather than guessing."),
    (r"\b(how many|what percent|median|average|on (my|this) library|stats?)\b",
     "Needs real numbers: query the live DB read-only with `library-stats`."),
    (r"\b(refactor\w*|clean ?up|messy|tangled|hard to change|architecture|restructure|"
     r"split (up )?(encoder|the file))\b",
     "Design work: `codebase-design` vocabulary; `improve-codebase-architecture` for a "
     "ranked review."),
    (r"\b(should we|what if we|i'?m thinking (of|about)|i have an idea|idea for|plan for|"
     r"new feature|design (a|the)|how should (we|it))\b",
     "Underspecified idea: `grill-with-docs` before building (skip if it's already concrete)."),
    (r"\b(prototype|try (it )?out|experiment with|mock ?up|feel right|see what it looks like)\b",
     "Cheap trial: `prototype` (throwaway branch, verdict in the commit)."),
    (r"\b(commit|push|pull request|\bpr\b|merge)\b",
     "Shipping: follow `optimizarr-ship` (tests green, house commit style, Windows OpenSSH push)."),
]


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    prompt = str(payload.get("prompt") or "")
    hits = [msg for pat, msg in TRIGGERS if re.search(pat, prompt, re.IGNORECASE)]
    if not hits:
        return 0
    text = "Skill triggers (CLAUDE.md):\n" + "\n".join(f"- {m}" for m in hits)
    json.dump({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                      "additionalContext": text}}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
