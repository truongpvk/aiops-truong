"""
verify_checklist.py — Acceptance checklist verification for W3-D3
Checks all requirements from §9.10
"""
import json
import re
import os

PASS = "[PASS]"
FAIL = "[FAIL]"

def check(name, condition, detail=""):
    status = PASS if condition else FAIL
    msg = f"  {status}: {name}" + (f" -- {detail}" if detail else "")
    print(msg)
    return condition

results = []
print("=" * 60)
print("W3-D3 Acceptance Checklist Verification")
print("=" * 60)

# 1. timeline.json
print("\n[1] timeline.json")
with open("timeline.json", "r") as f:
    tl = json.load(f)
events = tl.get("events", [])
results.append(check("Has >= 8 events", len(events) >= 8, f"found {len(events)}"))
utc_count = sum(1 for e in events if "timestamp" in e and e["timestamp"].endswith("Z"))
results.append(check("All events have UTC timestamps", utc_count == len(events), f"{utc_count}/{len(events)}"))

# 2. postmortem.md
print("\n[2] postmortem.md")
with open("postmortem.md", "r", encoding="utf-8") as f:
    pm = f.read()
required_fields = ["Summary", "Impact", "Timeline", "Root cause", "Contributing factors", "Detection", "Response", "Action items"]
for field in required_fields:
    results.append(check(f"Has field: {field}", f"## {field}" in pm))
# Count timeline events in postmortem
pm_timeline_rows = len(re.findall(r"\| \d{2}:\d{2}:\d{2} \|", pm))
results.append(check("Timeline has >= 8 events", pm_timeline_rows >= 8, f"found {pm_timeline_rows}"))
# Check blameless wording
blame_words = ["Alice", "Bob", "engineer forgot", "was slow to respond", "pushed bad"]
blame_found = [w for w in blame_words if w.lower() in pm.lower()]
results.append(check("0 blame wording", len(blame_found) == 0, f"blame words found: {blame_found}" if blame_found else "clean"))
# Detection gaps
gap_count = pm.lower().count("gap")
results.append(check("Has >= 2 detection gaps noted", gap_count >= 2, f"found {gap_count} mentions"))

# 3. ADR.md
print("\n[3] ADR.md")
with open("ADR.md", "r", encoding="utf-8") as f:
    adr = f.read()
results.append(check("Has Status section", "## Status" in adr))
results.append(check("Has Context section", "## Context" in adr))
results.append(check("Has Decision section", "## Decision" in adr))
results.append(check("Has Alternatives section", "## Alternatives" in adr or "### Alternative" in adr))
alt_count = adr.count("### Alternative")
results.append(check("Has >= 2 alternatives", alt_count >= 2, f"found {alt_count}"))
results.append(check("Has Consequences section", "## Consequences" in adr))
# Check pros/cons
pros_count = adr.lower().count("**pros:**") + adr.lower().count("**pros**")
cons_count = adr.lower().count("**cons:**") + adr.lower().count("**cons**")
results.append(check("Each alternative has pros", pros_count >= 2, f"found {pros_count}"))
results.append(check("Each alternative has cons", cons_count >= 2, f"found {cons_count}"))
results.append(check("References gap from §9.4", "GAP" in adr or "gap" in adr.lower()))

# 4. cost_model.py
print("\n[4] cost_model.py")
with open("cost_model.py", "r", encoding="utf-8") as f:
    cm = f.read()
results.append(check("Has is_worth_it function", "def is_worth_it(" in cm))
results.append(check("Has __main__ block", 'if __name__ == "__main__"' in cm))
# Count scenarios
scenario_count = cm.count("is_worth_it(")  - 1  # subtract the def line
results.append(check("Has >= 3 worked examples", scenario_count >= 3, f"found {scenario_count}"))
# Test function returns correct schema
exec_globals = {}
exec(compile(open("cost_model.py").read(), "cost_model.py", "exec"), exec_globals)
fn = exec_globals["is_worth_it"]
result = fn(20, 2, 1, 10000)
required_keys = {"monthly_value", "monthly_cost", "roi", "payback_months", "verdict"}
results.append(check("Returns correct schema keys", set(result.keys()) == required_keys, f"keys: {set(result.keys())}"))
results.append(check("Verdict is valid enum", result["verdict"] in ["worth_it", "marginal", "not_worth_it"], f"verdict: {result['verdict']}"))

# 5. SPEC.md
print("\n[5] SPEC.md")
with open("SPEC.md", "r", encoding="utf-8") as f:
    spec = f.read()
spec_sections = [
    "## 1. Platform overview",
    "## 2. SLO definition",
    "## 3. Detection",
    "## 4. Reliability validation",
    "## 5. Operational pattern",
    "## 6. Cost model",
    "## 7. Open risks",
]
for section in spec_sections:
    results.append(check(f"Has section: {section}", section in spec))

# 6. SUBMIT.md
print("\n[6] SUBMIT.md")
with open("SUBMIT.md", "r", encoding="utf-8") as f:
    sub = f.read()
submit_sections = [
    "## Outage chosen",
    "## 3 things I learned",
    "## 1 thing my pipeline would still miss",
    "## 1 decision in my ADR",
    "## Cost model verdict",
]
for section in submit_sections:
    results.append(check(f"Has section: {section}", section in sub))

# Summary
print("\n" + "=" * 60)
passed = sum(results)
total = len(results)
print(f"TOTAL: {passed}/{total} checks passed")
if passed == total:
    print("ALL CHECKS PASSED -- Ready for submission!")
else:
    print(f"{total - passed} checks FAILED -- review above")
