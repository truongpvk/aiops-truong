# W3-D3: Outage Reproduction, Postmortem, ADR, Cost Model

---

## 1. Definitions

| Concept | Definition |
|---|---|
| **Postmortem** | Document analyzing an incident after it has been resolved: timeline, root cause, contributing factors, action items |
| **Blameless principle** | Postmortem focuses on systemic causes, not on individuals. Reason: blame culture → fewer people report errors → bugs stay hidden longer |
| **ADR (Architecture Decision Record)** | 1-page document recording a single architectural decision: context, decision, alternatives, consequences |
| **MTTR / MTTD / MTBF** | Mean Time To Recover / Detect / Between Failures |
| **Error budget** | (1 - SLO) × total events; the quota allowed to fail |

**Sources:**
- Postmortem culture: Beyer et al., *SRE Book* Ch 15 — https://sre.google/sre-book/postmortem-culture
- ADR origin: Michael Nygard, "Documenting Architecture Decisions" (2011) — https://www.cognitect.com/blog/2011/11/15/documenting-architecture-decisions

---

## 2. Postmortem Template — Standard Google SRE Fields

```markdown
# Postmortem: <short incident name>

**Status:** complete | draft  
**Date:** YYYY-MM-DD  
**Authors:** <names>  
**Severity:** SEV1 | SEV2 | SEV3  
**Duration:** <minutes> (start UTC → end UTC)

## Summary
<2-4 sentences: what happened, who was affected, how it was fixed>

## Impact
- Users affected: <number / %>
- Revenue impact: $<estimate>
- SLO budget consumed: <%>
- External communication: <status page updates, blog post>

## Timeline (UTC)
| Time | Event |
|------|-------|
| HH:MM | Trigger event (deploy, config push, traffic surge) |
| HH:MM | First user-visible symptom |
| HH:MM | First page fired |
| HH:MM | On-call ack |
| HH:MM | Root cause identified |
| HH:MM | Mitigation applied |
| HH:MM | Full recovery |

## Root cause
<technical explanation: what broke, why, why detection delayed>

## Contributing factors
- <factor 1: e.g., insufficient canary, missing alert>
- <factor 2>

## Detection
- How was the incident detected? (user report, alert, dashboard)
- Could it have been detected earlier?

## Response
- What went well
- What went poorly
- Where we got lucky

## Action items
| Item | Owner | Due | Priority |
|------|-------|-----|----------|
| <action 1> | <name> | <date> | P0/P1/P2 |
```

### 2.1 Blameless Wording

| Blame-y (avoid) | Blameless (use) |
|---|---|
| "Alice pushed bad config" | "Config push pipeline allowed invalid YAML through" |
| "On-call was slow to respond" | "Alert routing didn't reach on-call's primary device" |
| "Engineer forgot to test" | "Pre-merge test suite didn't cover this scenario" |

> Focus: **system + process**, not **people**.

---

## 3. Root Cause Analysis: 5 Whys vs Causal Tree

### 3.1 5 Whys (Toyota, 1950s)

Linear chain — repeat "why?" 5 times:

```
Symptom: API returns 500 at 02:14 UTC
Why? → DB connection pool exhausted
Why? → One query locked the whole pool, blocking for 30s
Why? → Query missing index, full table scan
Why? → Index removed by migration last week
Why? → Migration review didn't catch the performance regression
```

**Strength:** Simple, anyone can use it.  
**Limit:** Assumes a linear cause. Real outages often have multiple branches.

### 3.2 Causal Tree

Multiple branches, each branch can have its own cause:

```
                     Outage 24h11m (GitHub 2018-10-21)
                              │
            ┌─────────────────┼─────────────────┐
            │                 │                 │
       Network blip       Orchestrator      Consistency-first
       43 seconds         failover triggered  recovery policy
            │                 │                 │
       Routine optical    Quorum logic      Engineering decision
       maintenance        deemed primary     (no data loss > faster)
       (BGP convergence   unreachable
        slow)
```

**Use when:**
- Outage has > 1 simultaneous failure mode (Roblox: Consul streaming + BoltDB)
- An architectural decision contributes (consistency vs availability trade-off)

*Source: Allspaw & Robbins, Web Operations, O'Reilly 2010, Ch 10.*

---

## 4. Failure Mode Catalog — 6 Patterns with Real Incident Citations

| Pattern | Mechanism | Real incident |
|---|---|---|
| **Cascading failure** | A fails → retry storm → B saturates → C saturates | AWS Lambda 2018, DynamoDB 2015 |
| **Split-brain** | Network partition → 2 nodes think they are primary → divergent state | GitHub MySQL 2018-10-21 |
| **Catastrophic backtracking** | Regex / parser exponential time on adversarial input | Cloudflare 2019-07-02 |
| **Capacity exhaustion at boundary** | File descriptor, connection pool, thread pool full | LinkedIn 2017 conn pool, Cloudflare 2022 fd leak |
| **Monitoring dependency loop** | AIOps stack depends on monitored service → service fails → monitoring blind | Roblox 2021-10-28 (Consul) |
| **Operator action without guardrail** | Typo / wrong-scope command takes down prod | AWS S3 2017-02-28, GitLab 2017-01-31 (db delete) |

### 4.1 Pattern Detail: Cascading Failure

```
User traffic
    ↓
[Service A] ──fail──→ retries with backoff
    ↓                       ↓
[Service B] ←──retry storm (10×)──
    ↓ (CPU saturated by retry handling)
[Service C] ←──fails to get response from B
    ↓
Whole system degraded
```

**Detection trap:** Alert count is highest at C (downstream), not at A (root). Naive RCA picks C → wrong. Requires topology-aware + causal-lag analysis.

### 4.2 Pattern Detail: Split-brain (GitHub 2018)

GitHub 2018-10-21, 22:52 UTC. Routine optical equipment maintenance caused a 43-second connectivity loss between the US East coast hub and the US East data center. The Orchestrator (MySQL failover tool) on US West saw East as unreachable → triggered failover, promoting the West replica to primary. 43s later East reconnected → 2 primaries → divergent writes.

Recovery took 24h 11m because GitHub chose consistency over speed: replaying the binary log from East to West rather than serving possibly-stale data.

*Source: https://github.blog/2018-10-30-oct21-post-incident-analysis*

### 4.3 Pattern Detail: Catastrophic Backtracking (Cloudflare 2019)

2019-07-02, 13:42 UTC. Cloudflare deployed a WAF rule globally all at once containing a regex with nested quantifiers. When given input like `xxxxx=xxxxxx`, the regex engine tried exponential combinations → CPU 100% on every edge worldwide. 27-minute outage, traffic dropped 82%.

**Counter:** Test regex against ReDoS detector before deploy; canary rollout 1% → 10% → 100% instead of global atomic push.

*Source: https://blog.cloudflare.com/details-of-the-cloudflare-outage-on-july-2-2019*

### 4.4 Pattern Detail: Monitoring Loop (Roblox 2021)

2021-10-28 → 10-31, 73 hours. Roblox enabled Consul streaming under production load. Streaming contention under high concurrent read+write → blocking writes. BoltDB freelist algorithm O(n²) at scale → write latency spike.

**Critical detail:** Roblox's monitoring stack depended on Consul for service discovery. Consul slow → monitoring queries timed out → on-call had no visibility for the first ~12 hours. Diagnosis took 60+ hours because 2 unrelated issues (streaming contention + BoltDB) overlapped.

*Source: https://about.roblox.com/newsroom/2022/01/roblox-return-to-service-10-28-10-31-2021*

---

## 5. Outage Catalog — Pick 1 to Reproduce

| # | Incident | Date | Duration | Failure mode | Difficulty | Postmortem URL |
|---|---|---|---|---|---|---|
| 1 | AWS S3 us-east-1 | 2017-02-28 | ~4h | Operator typo, insufficient blast radius | Easy | https://aws.amazon.com/message/41926/ |
| 2 | GitHub MySQL split-brain | 2018-10-21 | 24h 11m | Network partition + Orchestrator failover | Hard | https://github.blog/2018-10-30-oct21-post-incident-analysis |
| 3 | Cloudflare WAF regex | 2019-07-02 | 27 min | Catastrophic backtracking + global deploy | Medium | https://blog.cloudflare.com/details-of-the-cloudflare-outage-on-july-2-2019 |
| 4 | Roblox Consul + BoltDB | 2021-10-28 | 73h | Streaming contention + freelist + monitoring loop | Hard | https://about.roblox.com/newsroom/2022/01/roblox-return-to-service-10-28-10-31-2021 |
| 5 | Slack Jan 4 2022 | 2022-01-04 | ~6h | Provisioning system overload post-holiday | Medium | https://slack.engineering/slacks-incident-on-2-22-22/ |

Community archive: https://github.com/danluu/post-mortems

---

## 6. Reproduction Patterns

Each outage has a minimal docker-compose that can trigger the same failure mode.

### 6.1 AWS S3 2017 Reproduction (Operator Typo)

```yaml
# docker-compose.yml
services:
  billing:
    image: alpine
    command: sleep infinity
  index:
    image: alpine
    command: sleep infinity
  placement:
    image: alpine
    command: sleep infinity

# bad-command.sh
docker compose stop --remove-orphans  # ← typo: should have been "stop billing"
```

**Result:** Running the bash script → 3 services go down at once → index + placement down means object metadata cannot be served and object location cannot be decided.

**Postmortem to write:** Why could a single command wipe out 3 services? Which guardrail was missing?

### 6.2 Cloudflare 2019 Reproduction (Regex CPU)

```python
import re, time
EVIL = r'(?:(?:\"|\d|.*)+(?:.*=.*))'  # simplified evil regex
INPUT = "x=" + "x" * 30
t0 = time.time()
re.match(EVIL, INPUT)
print(f"matched in {time.time()-t0:.2f}s — should be < 0.01s")
# Real run: 8-15 seconds on commodity hardware → CPU pegged
```

Wrap it into an HTTP middleware → the server becomes unresponsive within seconds.

### 6.3 GitHub 2018 Reproduction (Simplified Split-brain)

```yaml
services:
  mysql-primary:
    image: mysql:8
    networks: [east]
  mysql-replica:
    image: mysql:8
    networks: [west]
  orchestrator:
    image: openark/orchestrator:latest
    networks: [east, west]

networks:
  east: {}
  west: {}

# Trigger:
# docker network disconnect east orchestrator   # 43s
# (orchestrator from west sees east unreachable, promotes replica)
# docker network connect east orchestrator
# Both DB now think they're primary → write conflicts
```

---

## 7. Architecture Decision Record (ADR)

### 7.1 Nygard Format (2011)

```markdown
# ADR-NNN: <short title of decision>

## Status
Proposed | Accepted | Deprecated | Superseded by ADR-XXX

## Context
<situation prompting the decision: forces at play, constraints>

## Decision
<the change we're making>

## Alternatives considered
- Alternative A — pros, cons, why rejected
- Alternative B — pros, cons, why rejected

## Consequences
- Positive consequence 1
- Negative consequence 1 (trade-off accepted)
- Risk 1, mitigation
```

### 7.2 Example ADR for an AIOps Platform

```markdown
# ADR-007: Use topology-aware RCA over count-based ranking

## Status
Accepted

## Context
RCA needs to pick the root service from N services that are firing alerts. Count-based ranking
(the service with the most alerts = root) fails on cascading failure: downstream
services retry → fire more alerts than the upstream root.

## Decision
RCA combines 3 signals:
1. Topology distance from edge (upstream-bias)
2. First-drift time (causal lag analysis via Granger causality)
3. Alert volume (tiebreaker only)

## Alternatives considered
- Count-only ranking — simple, fast, BUT fails retry storm. Rejected.
- LLM-only RCA — flexible, BUT hallucinates confident-wrong roots. Rejected as primary.
- Graph PageRank only — captures topology BUT not temporal causality. Rejected as standalone.

## Consequences
+ Catches cascading patterns missed by count-only (verified vs Roblox-style scenario)
+ Composable: each signal degrades gracefully if data is missing
− Higher compute cost (Granger causality O(n × lag_window))
− Requires topology graph kept up-to-date — adds operational burden
- Risk: signal weights need tuning per environment, not automatic.
```

---

## 8. Cost Model — Break-even for an AIOps Platform

### 8.1 Cost Side

| Component | Unit | Order of magnitude |
|---|---|---|
| Metric ingestion | $/series/month | $0.0001 (Prometheus self-host) — $0.30 (Datadog) |
| Log ingestion | $/GB | $0.30 (S3 + Athena) — $2.50 (Datadog) — $5 (Splunk) |
| Trace ingestion | $/million spans | $0.50 (Tempo) — $2 (Datadog APM) |
| Model inference compute | $/hour | $0.05 (CPU c6i.large) — $3.06 (GPU g5.xlarge) |
| Storage hot/warm/cold | $/GB/month | $0.023 (S3) — $0.10 (gp3) — $0.30 (Prometheus local) |
| SRE/AIOps engineer | $/year | $120k – $250k loaded cost |
| On-call rotation overhead | hours/engineer/month | 40–80h ≈ $5k – $15k/month opportunity cost |

### 8.2 Value Side

```
value_per_year = MTTR_reduction_hours × incident_per_year × downtime_cost_per_hour
```

**Downtime cost per hour:**

| Business type | Order of magnitude |
|---|---|
| E-commerce mid-tier | $5k – $50k/hour |
| Large e-commerce (Amazon-scale) | $200k+/hour |
| Financial trading | $1M+/hour |
| Internal SaaS | $500 – $5k/hour |
| Streaming (Netflix, etc.) | $50k – $500k/hour |

*Source: ITIC 2024 Hourly Cost of Downtime Survey, Gartner 2014 (often-cited $5,600/min baseline).*

### 8.3 Break-even Formula

```python
def is_worth_it(
    num_services: int,
    incidents_per_month: int,
    avg_incident_duration_hours: float,
    downtime_cost_per_hour: float,
    expected_mttr_reduction_pct: float = 0.4,
    aiops_monthly_cost: float = 15_000,
) -> dict:
    monthly_downtime_hours = incidents_per_month * avg_incident_duration_hours
    monthly_value = (
        monthly_downtime_hours
        * expected_mttr_reduction_pct
        * downtime_cost_per_hour
    )
    roi = monthly_value / aiops_monthly_cost
    payback_months = aiops_monthly_cost / monthly_value if monthly_value > 0 else float("inf")
    return {
        "monthly_value": monthly_value,
        "monthly_cost": aiops_monthly_cost,
        "roi": roi,
        "payback_months": payback_months,
        "verdict": "worth_it" if roi > 1.5 else "marginal" if roi > 1.0 else "not_worth_it",
    }
```

### 8.4 Break-even Examples

| Scenario | Verdict | Reason |
|---|---|---|
| 20 services, 2 incidents/mo × 1h, $10k/h, $15k AIOps | ROI 0.53 → **not_worth_it** | Too few incidents to justify |
| 100 services, 5 incidents/mo × 2h, $20k/h, $25k AIOps | ROI 3.2 → **worth_it** | Right size, real downtime cost |
| 500 services, 10 incidents/mo × 1.5h, $50k/h, $60k AIOps | ROI 5.0 → **worth_it** | Scale + cost both high |
| 10 services, 1 incident/mo × 30min, $1k/h, $10k AIOps | ROI 0.02 → **not_worth_it** | Hire a good SRE instead |

### 8.5 When NOT to Do AIOps

- < 30 services and < 3 incidents/month
- Downtime cost < $1k/hour (internal tools, hobby)
- Observability stack not yet mature (no SLO, no centralized logs) → AIOps has no clean signal to work with
- Postmortem culture not yet established → AIOps surfaces signals but no one acts on them

> **The right recipe in these cases:** Invest in good observability + SLO + on-call culture. Do NOT invest in AIOps.

---

## 9. Exercises

### 9.1 Provided Setup

```bash
wget https://learning-notes-dz2.pages.dev/aiops-w3/lab/w3-d3-pack.zip
unzip w3-d3-pack.zip -d w3-d3-pack/
cd w3-d3-pack/
```

**Pack structure:**
```
outage_catalog.yaml           # 5 outages with ready-made reproduction templates
reproduction_templates/
├── aws_s3_2017/              # operator typo + over-broad command
├── github_mysql_2018/        # MySQL primary-replica + orchestrator
├── cloudflare_regex_2019/    # FastAPI app with evil regex middleware
├── roblox_consul_2021/       # Consul + internal dependency loop demo
└── slack_2022/               # provisioning queue overload demo
pipeline/                     # AIOps pipeline (W1+W2 wired, endpoint ready)
templates/
├── postmortem_template.md    # field-by-field template
├── adr_template.md           # Nygard format
└── spec_template.md          # SPEC.md outline
scripts/
├── start_reproduction.sh     # spin up chosen reproduction stack
├── inject.sh                 # trigger failure mode
└── capture_timeline.py       # record event timeline with UTC timestamp
```

### 9.2 Step 1 — Pick an Outage

Choose 1 outage from `outage_catalog.yaml` (5 options in §5).

In `SUBMIT.md` Section 0, write:

```markdown
## Outage chosen
- ID: <1-5>
- Name: <e.g., AWS S3 2017-02-28>
- Why this one: <2-3 sentences — which pattern interests you>
- Failure mode: <pick from §4: cascading | split-brain | regex | capacity | monitoring-loop | operator>
```

### 9.3 Step 2 — Reproduce

```bash
cd reproduction_templates/<chosen_outage>/
bash ../scripts/start_reproduction.sh
# wait healthcheck
bash ../scripts/inject.sh
# capture
python ../scripts/capture_timeline.py --duration 600 --out timeline.json
```

`timeline.json` contains events captured from Prometheus + container events + pipeline output, with UTC timestamps.

### 9.4 Step 3 — Run the AIOps Pipeline on the Reproduction

```bash
curl http://localhost:8000/alerts?since=<inject_start_ts> > alerts_observed.json
curl -X POST http://localhost:8000/rca \
     -d '{"window_start": <ts>, "window_end": <ts+600>}' \
     > rca_observed.json
```

Compare against expected (per the original postmortem):
- Did the pipeline detect the incident in < N seconds? (target < 30s)
- Did the pipeline pick the correct root service?
- Are there any patterns the pipeline completely missed?

> Note at least **2 specific gaps** in `postmortem.md` Section "Detection".

### 9.5 Step 4 — Write `postmortem.md`

Follow the template in §2. Every required field must be filled. The timeline must include **at least 8 events** with UTC timestamps (taken from `timeline.json`).

Required wording check: 0 instances of blame wording — only blameless wording is accepted (§2.1).

### 9.6 Step 5 — Write `ADR.md`

1 ADR for 1 design decision of the AIOps platform, following the Nygard template in §7.1. The decision must:
- Include at least 2 alternatives with pros/cons for each
- Include at least 2 consequences (1 positive, 1 trade-off)
- Reference a gap observed in §9.4

**Example suitable ADR topics:**
- RCA: count-based vs topology-aware vs causal-lag — which one
- Alert routing: page-everyone vs tier-based on-call rotation
- Detector: single threshold vs ensemble (3σ + IF + LSTM-AE)
- Storage: hot Prometheus 2 weeks vs S3+Athena cold long-term
- LLM: GPT-style cloud API vs self-hosted Llama vs no LLM

### 9.7 Step 6 — Write `cost_model.py`

Implement the function exactly per the signature:

```python
def is_worth_it(
    num_services: int,
    incidents_per_month: int,
    avg_incident_duration_hours: float,
    downtime_cost_per_hour: float,
    expected_mttr_reduction_pct: float = 0.4,
    aiops_monthly_cost: float = 15_000,
) -> dict:
    """
    Returns:
      {
        "monthly_value": float,
        "monthly_cost": float,
        "roi": float,
        "payback_months": float,  # or float('inf')
        "verdict": "worth_it" | "marginal" | "not_worth_it"
      }
    Verdict rule:
      roi > 1.5 → worth_it
      1.0 < roi ≤ 1.5 → marginal
      roi ≤ 1.0 → not_worth_it
    """
```

Plus 3 worked example scenarios in the same file:

```python
if __name__ == "__main__":
    print(is_worth_it(num_services=20, incidents_per_month=2,
                      avg_incident_duration_hours=1, downtime_cost_per_hour=10_000,
                      aiops_monthly_cost=15_000))
    print(is_worth_it(num_services=100, incidents_per_month=5,
                      avg_incident_duration_hours=2, downtime_cost_per_hour=20_000,
                      aiops_monthly_cost=25_000))
    # 1 scenario of your own — pick an industry, defend your choice of downtime cost in a comment
```

### 9.8 Step 7 — Write `SPEC.md` (Consolidate W3)

```markdown
# AIOps Mini-Platform Spec — <your name>

## 1. Platform overview
[2-3 sentences: the stack being monitored, scope, users of the platform]

## 2. SLO definition (from W3-D1)
[paste/reference slo_spec.yaml — 3 services × SLI+SLO+budget]

## 3. Detection + Correlation + RCA stack (from W1+W2)
[1 paragraph per layer — high-level approach + ADR reference]

## 4. Reliability validation (from W3-D2)
[paste chaos_report.md scoreboard + top 3 gaps]

## 5. Operational pattern (from W3-D3)
[reproduced outage + key learning + ADR-001 reference]

## 6. Cost model (from W3-D3)
[paste cost_model.py output for the current stack + break-even point]

## 7. Open risks
[3-5 known gaps not yet fixed, each with severity + mitigation plan]
```

### 9.9 Step 8 — `SUBMIT.md`

```markdown
# W3-D3 Submission — <your name>

## Outage chosen
[section §9.2]

## 3 things I learned from this outage
1. ...
2. ...
3. ...

## 1 thing my pipeline would still miss if this outage happened for real
- Pattern: ...
- Why miss: ...
- Mitigation idea: ...

## 1 decision in my ADR I'm not fully sure about
...

## Cost model verdict for my stack
- ROI: __
- Payback: __ months
- Verdict: __
```

### 9.10 Acceptance Checklist

- [ ] Reproduction runs, `inject.sh` triggers an observable failure mode
- [ ] `timeline.json` has ≥ 8 events with UTC timestamps
- [ ] `postmortem.md` has all fields per template §2, 0 blame wording, timeline ≥ 8 events, ≥ 2 detection gaps noted
- [ ] `ADR.md` has ≥ 2 alternatives each with pros/cons, ≥ 2 consequences, references 1 gap from §9.4
- [ ] `cost_model.py` parses, `is_worth_it()` returns the correct schema, has 3 worked examples
- [ ] `SPEC.md` has all 7 sections
- [ ] `SUBMIT.md` has all 5 sections

---

## 10. Deliverable Summary

| File | Description | Spec |
|---|---|---|
| `reproduction/` | Outage reproduction stack (docker-compose + inject + timeline) | §9.3 |
| `timeline.json` | Captured events with UTC timestamps | §9.3 |
| `alerts_observed.json` + `rca_observed.json` | Pipeline output on the reproduction | §9.4 |
| `postmortem.md` | Blameless postmortem following the Google SRE template | §9.5, §2 |
| `ADR.md` | 1 architecture decision record for the AIOps platform | §9.6, §7.1 |
| `cost_model.py` | `is_worth_it()` + 3 scenarios | §9.7 |
| `SPEC.md` | Master spec consolidating W3 deliverables | §9.8 |
| `SUBMIT.md` | 5-section reflection | §9.9 |

Submission path: `aiops-<name>/w3/d3/`

---

## 11. Anti-patterns

| Anti-pattern | Consequence |
|---|---|
| Postmortem blames individuals | Blame culture → bugs hidden longer → worse outages |
| Copying the original postmortem instead of writing from your own reproduction | No learning, the document = fiction |
| ADR missing Alternatives | Not a decision record, just an announcement |
| Cost model ignoring engineer time | Underestimates cost 3-5× |
| Reproducing the outage 1:1 with prod | Burns money + scope creep. A minimal env is enough to trigger the pattern |
| Action items without owner + due date | Postmortem becomes an archive file, nobody acts on it |
| 5 Whys when the outage has > 1 cause | Misses contributing factors → incomplete fix |

---

## 12. References

| Source | Topic | URL |
|---|---|---|
| Beyer et al. SRE Book Ch 15 | Postmortem Culture (canonical Google framework) | https://sre.google/sre-book/postmortem-culture/ |
| Michael Nygard (2011) | ADR format | https://www.cognitect.com/blog/2011/11/15/documenting-architecture-decisions |
| Joel Parker Henderson | ADR templates collection | https://github.com/joelparkerhenderson/architecture-decision-record |
| danluu/post-mortems | Curated archive of public postmortems | https://github.com/danluu/post-mortems |
| GitHub Engineering | Oct 2018 MySQL split-brain analysis | https://github.blog/2018-10-30-oct21-post-incident-analysis |
| Cloudflare | July 2019 regex outage detail | https://blog.cloudflare.com/details-of-the-cloudflare-outage-on-july-2-2019 |
| Roblox | Oct 2021 73-hour Consul outage | https://about.roblox.com/newsroom/2022/01/roblox-return-to-service-10-28-10-31-2021 |
| AWS | Feb 2017 S3 us-east-1 disruption | https://aws.amazon.com/message/41926/ |
| Slack Engineering | Jan 2022 incident retrospective | https://slack.engineering/slacks-incident-on-2-22-22/ |
| Allspaw & Robbins | *Web Operations*, O'Reilly 2010 (causal tree pattern) | https://www.oreilly.com/library/view/web-operations/9781449377465/ |
| Etsy | Blameless postmortem culture | https://www.etsy.com/codeascraft/blameless-postmortems |
| ITIC 2024 | Hourly Cost of Downtime Survey | https://itic-corp.com/ |
| Atlassian | Incident response handbook | https://www.atlassian.com/incident-management |
