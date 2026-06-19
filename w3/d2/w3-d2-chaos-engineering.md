# W3-D2: Chaos Engineering — Validate AIOps Pipeline

> **14 min read · 2959 words**  
> Part of: AIOps W3 — Reliability Engineering & Postmortem

---

## Table of Contents

1. [Definition](#1-definition)
2. [5 Core Principles](#2-5-core-principles)
3. [Fault Categories](#3-fault-categories)
   - 3.1 [Network Faults](#31-network-faults)
   - 3.2 [Resource Faults](#32-resource-faults)
   - 3.3 [Application Faults](#33-application-faults)
   - 3.4 [State Faults](#34-state-faults)
4. [Tool Landscape](#4-tool-landscape)
5. [Experiment Design Template](#5-experiment-design-template)
   - 5.1 [Writing a Hypothesis Correctly](#51-writing-a-hypothesis-correctly)
   - 5.2 [Blast Radius Escalation](#52-blast-radius-escalation)
6. [Measurement Framework for the AIOps Pipeline](#6-measurement-framework-for-the-aiops-pipeline)
   - 6.1 [Confusion Matrix per Experiment](#61-confusion-matrix-per-experiment)
   - 6.2 [RCA Accuracy](#62-rca-accuracy)
   - 6.3 [Scoreboard after N Experiments](#63-scoreboard-after-n-experiments)
   - 6.4 [External Steady-State Signal — Synthetic Probes](#64-external-steady-state-signal--synthetic-probes)
7. [Pipeline Failure Modes — Observed in Real Incidents](#7-pipeline-failure-modes--observed-in-real-incidents)
8. [Exercises](#8-exercises)
9. [Anti-Patterns](#9-anti-patterns)
10. [References](#10-references)

---

## 1. Definition

**Chaos Engineering** — an experimental discipline of deliberately injecting faults into a distributed system to uncover weaknesses *before* those faults occur naturally in production.

Distinct from 3 things it is often confused with:

| Practice | Goal | When to run |
|---|---|---|
| Unit test | Verify code matches spec | CI/CD |
| Load test | Verify system handles expected load | Pre-launch, periodic |
| Penetration test | Find security vulnerabilities | Quarterly |
| **Chaos engineering** | **Find reliability weaknesses caused by interactions between components** | **Continuously, in production-like env** |

*Source: Casey Rosenthal et al., Principles of Chaos Engineering, [principlesofchaos.org](https://principlesofchaos.org/) (2017, refined 2019).*

---

## 2. 5 Core Principles

Per [principlesofchaos.org](https://principlesofchaos.org/):

1. **Build a hypothesis around steady-state behavior** — define "system OK" using measurable metrics before injecting.
2. **Vary real-world events** — inject faults that simulate real failures: instance crash, network latency, dependency timeout.
3. **Run experiments in production** — staging cannot reproduce the scale/traffic shape of prod. Only prod-chaos catches the class of bugs caused by scale.
4. **Automate experiments to run continuously** — manual chaos = once per quarter = not trustworthy. Automated chaos = continuous verification.
5. **Minimize blast radius** — start small (1 instance, 1% traffic), expand gradually. Have a rollback fast enough.

---

## 3. Fault Categories

4 classes of faults, each with standard tools and mechanisms:

### 3.1 Network Faults

| Fault | Mechanism | Tool |
|---|---|---|
| Latency injection | `tc netem delay 500ms ± 100ms` | Pumba, Chaos Mesh, Toxiproxy |
| Packet loss | `tc netem loss 30%` | Pumba `netem`, Chaos Mesh `NetworkChaos` |
| Bandwidth throttle | `tc tbf rate 1mbit` | Pumba |
| Partition (split-brain) | `iptables -A INPUT -s X -j DROP` | Chaos Mesh `partition`, Pumba |
| DNS slow/fail | Override resolver | Toxiproxy, Chaos Mesh `DNSChaos` |

### 3.2 Resource Faults

| Fault | Mechanism | Tool |
|---|---|---|
| CPU stress | `stress-ng --cpu 4 --cpu-load 90` | Pumba `stress`, Chaos Mesh `StressChaos` |
| Memory fill | `stress-ng --vm 1 --vm-bytes 80%` | Chaos Mesh, Litmus `pod-memory-hog` |
| Disk I/O saturation | `dd if=/dev/zero of=/tmp/file bs=1M` | Chaos Mesh `IOChaos` |
| Disk fill | Fill volume to 95% | Litmus `disk-fill` |
| File descriptor exhaustion | Open N fds | Custom script |

### 3.3 Application Faults

| Fault | Mechanism | Tool |
|---|---|---|
| Pod/container kill | `docker kill`, `kubectl delete pod` | Chaos Monkey, Pumba `kill`, Litmus `pod-delete` |
| Pause (SIGSTOP) | Process freeze without crash | Pumba `pause` |
| HTTP error inject | Proxy injects 5xx response | Toxiproxy, Chaos Mesh `HTTPChaos` |
| HTTP slow response | Proxy delays response | Toxiproxy |
| Exception injection | Bytecode rewrite | Byteman (JVM), Failify |

### 3.4 State Faults

| Fault | Mechanism | Tool |
|---|---|---|
| Clock skew | `libfaketime`, `chrony` manipulation | Chaos Mesh `TimeChaos` |
| Time jump (forward/backward) | `date -s` | Chaos Mesh `TimeChaos` |
| Config corruption | Replace config file | Custom |
| Cache poisoning | Inject bad data into Redis | Custom |

---

## 4. Tool Landscape

| Tool | Vendor | Scope | License | Strengths | Limits |
|---|---|---|---|---|---|
| **Chaos Monkey** | Netflix | EC2/cloud instance | Apache 2.0 | Pioneer, simple kill | Instance-only |
| **Pumba** | Alexei Ledenev | Docker | MIT | Simple CLI, no infra | Docker only, no K8s |
| **Chaos Mesh** | PingCAP / CNCF (incubating) | Kubernetes | Apache 2.0 | CRD-driven, dashboard, broad fault types | K8s only |
| **LitmusChaos** | MayaData / CNCF (incubating) | Kubernetes | Apache 2.0 | ChaosHub experiment library, CI/CD integration | K8s only |
| **Toxiproxy** | Shopify | Network proxy | MIT | Deterministic, framework-agnostic, test-friendly | Network layer only |
| **Gremlin** | Gremlin Inc | Multi-platform | Commercial | Enterprise UI, safety controls, ALFI (app-level) | Closed-source, paid |
| **AWS FIS** | AWS | AWS workloads | AWS service | Integrated with AWS console + IAM | AWS only |
| **Azure Chaos Studio** | Azure | Azure workloads | Azure service | Same Azure-only | Azure only |

**Decision tree to pick a tool:**

```
What's the env?
├── Docker only (dev/local) → Pumba
├── Kubernetes
│   ├── Need CI/CD integration → LitmusChaos
│   ├── Need dashboard + broad fault → Chaos Mesh
│   └── Simplest → Chaos Monkey for K8s
├── Deterministic network test → Toxiproxy
├── Cloud-managed
│   ├── AWS → FIS
│   └── Azure → Chaos Studio
└── Enterprise with budget → Gremlin
```

**Sources:** [Pumba](https://github.com/alexei-led/pumba) · [Chaos Mesh](https://chaos-mesh.org/) · [LitmusChaos](https://litmuschaos.io/) · [Toxiproxy](https://github.com/Shopify/toxiproxy) · [AWS FIS](https://aws.amazon.com/fis/)

---

## 5. Experiment Design Template

5 required fields per Rosenthal & Jones, *Chaos Engineering*, O'Reilly 2020:

```yaml
experiment:
  name: "Payment service network partition under load"
  hypothesis: |
    Steady-state: order_success_rate ≥ 99.5%, checkout_p99 ≤ 800ms.
    When payment-svc is partitioned from checkout-svc, retry logic
    will failover to backup payment provider within < 30s, 
    order_success_rate will drop no more than 5% within 60s.    
  blast_radius:
    target: 1 instance of payment-svc
    traffic: 10% of production traffic (canary cell)
    duration: 60 seconds
  rollback:
    automatic: true
    trigger_when: order_success_rate < 90% OR checkout_p99 > 3s
    method: iptables flush, restart sidecar
  measurement:
    metrics: [order_success_rate, checkout_p99, payment_retry_count, error_log_rate]
    capture_window: t-5min to t+10min
  abort_conditions:
    - any SLO breach beyond budget for 30s
    - alert tier-1 fires
```

### 5.1 Writing a Hypothesis Correctly

- ❌ **Wrong (vague):** "system should still work."
- ✅ **Right (testable):** "order_success_rate ≥ 99.5%, p99 latency ≤ 800ms during 60s partition."

### 5.2 Blast Radius Escalation

When an experiment passes, expand step by step:

| Stage | Target | Traffic |
|---|---|---|
| 1 — Dev | Single dev container | 0% (synthetic) |
| 2 — Staging | Full staging stack | 0% (load test) |
| 3 — Prod canary | 1 instance | 1-10% |
| 4 — Prod region | 1 region | 25-100% |
| 5 — Prod global | All regions | 100% (game day) |

> **Do not skip stages.** Previous stage fails → stop, fix, retry.

---

## 6. Measurement Framework for the AIOps Pipeline

The chaos goal for this lab: validate that the W1+W2 pipeline (detector, correlator, RCA) catches injected faults.

### 6.1 Confusion Matrix per Experiment

| | Pipeline reported incident | Pipeline silent |
|---|---|---|
| **Fault injected (ground truth)** | TP (detected) | FN (miss) |
| **No fault** (baseline window) | FP (false alarm) | TN (correct silence) |

**Pipeline metrics:**

```
precision = TP / (TP + FP)
recall    = TP / (TP + FN)
MTTD      = mean(alert_fire_time − fault_inject_time)
```

### 6.2 RCA Accuracy

Beyond detection, check whether RCA picks the correct service:

| | RCA pick correct root | RCA pick wrong root | RCA no output |
|---|---|---|---|
| Fault → service A | RCA_correct | RCA_wrong (e.g., picked loudest downstream) | RCA_miss |

```
rca_accuracy = RCA_correct / TP
```

### 6.3 Scoreboard after N Experiments

```
| Experiment              | Detected | MTTD | RCA correct | False alarms |
|-------------------------|----------|------|-------------|--------------|
| payment latency +500ms  | Y        | 47s  | Y           | 0            |
| db kill                 | Y        | 12s  | N (picked api)| 0          |
| cache cpu 90%           | N        | —    | —           | —            |
| network partition       | Y        | 23s  | Y           | 1            |
| ...                                                                  |
| TOTAL: 8/10 detected, precision 0.89, recall 0.80, RCA_acc 0.75     |
```

### 6.4 External Steady-State Signal — Synthetic Probes

§6.1 measures TP/FN based on "did the pipeline fire an alert". The deeper chaos question is **"can the user feel the fault"** — and the cleanest signal is an **external blackbox probe**: 1 process running outside the cluster, calling the endpoint the user uses, recording pass/fail. Probe pass-rate = canonical steady-state signal, independent of the pipeline's internal metrics.

**Why external > internal for chaos steady-state:**

| Aspect | Internal metric (Prom scrape) | External synthetic probe |
|---|---|---|
| Measures | System claims OK | User-visible OK |
| Can be fooled by | 200 with wrong body, stale cache, partial degrade | Hard — measures exactly what the user sees |
| Proves user impact | Indirectly (must infer) | Directly (probe = user proxy) |
| Catches | Service crash, slow query | Plus: DNS, TLS, ingress, LB, WAF misconfig |

Chaos principle #1 requires a signal that measures user experience — not an internal metric. A probe outside the cluster is the closest implementation to "real user".

**Minimal example — 20-line shell probe:**

```bash
#!/usr/bin/env bash
# synthetic_probe.sh — log pass/fail every 5s, use as steady-state signal
ENDPOINT="${1:-http://localhost:8080/checkout/health}"
LOG="${2:-probe.log}"
while true; do
  ts=$(date -u +%s)
  start=$(date +%s%N)
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 "$ENDPOINT")
  end=$(date +%s%N)
  latency_ms=$(( (end - start) / 1000000 ))
  if [[ "$code" == "200" && "$latency_ms" -lt 500 ]]; then
    echo "$ts pass $latency_ms" >> "$LOG"
  else
    echo "$ts fail $code $latency_ms" >> "$LOG"
  fi
  sleep 5
done
```

Steady-state = "≥ 99% pass within a 60s window". During a chaos run:

- **Before inject:** probe runs for 5 minutes → confirm steady-state.
- **During inject:** pass-rate drops → quantify user impact (no Prom needed).
- **After rollback:** pass-rate must return to ≥ 99% within 2 minutes → defines "system recovered".

**Gotcha — probe location determines what you catch:**

| Probe from where | Catches which faults | Misses |
|---|---|---|
| Same pod | Pod logic crash | Network, LB, ingress, DNS |
| Same cluster | + kube-dns, internal LB | External LB, CDN, WAN |
| Outside cluster, same region | + ingress, cert, public DNS | Inter-region routing, CDN edge |
| Multi-region external | Closest to the real user | Higher cost, FP from internet flap |

For this lab: a probe running from the host machine (outside the docker compose network) is enough. Real production needs multi-region tooling (k6 Cloud, Grafana Synthetic Monitoring, Datadog Synthetics).

**References:** Google SRE Workbook ch.5 ("black-box monitoring") · [k6.io](https://k6.io/) · [Grafana Synthetic Monitoring](https://grafana.com/products/cloud/synthetic-monitoring/)

---

## 7. Pipeline Failure Modes — Observed in Real Incidents

### 7.1 Detector Miss: Anomaly Sinks Below the Noise Floor

**Roblox October 2021 (73-hour outage):** Consul streaming feature contention did not trip the latency threshold because the Consul baseline was already variable. 3σ anomaly detection on Consul read latency: large noise floor → 3σ bound ≈ 50× normal → real anomaly only 5× → silent.

**Counter:** percentile-based anomaly on p99, not on mean. Or segmented baseline (peak vs off-peak).

*Source: [about.roblox.com/newsroom/2022/01/roblox-return-to-service-10-28-10-31-2021](https://about.roblox.com/newsroom/2022/01/roblox-return-to-service-10-28-10-31-2021)*

### 7.2 Correlator False Positive: Lumping Independent Faults into 1 Incident

When 2 unrelated faults occur within the same 5 minutes (deploy bug A + network blip B), a correlator that relies on time + service clusters them together. RCA picks 1 service as root → wrong.

**Counter:** topology-aware correlation (use the dependency graph), not just temporal.

### 7.3 RCA Wrong Root: Picks the Noisiest Service, Not the Root

Retry-storm pattern: payment-svc fails → checkout-svc retries 10× → checkout fires 10× alerts. Naive RCA: rank by alert count → picks checkout. Correct root: payment.

**Counter:** topology-aware (root upstream of leaves) + temporal-causal (root drifts before downstream) — Granger causality, cross-correlation lag analysis.

### 7.4 LLM Hallucination with High Confidence

LLM-augmented RCA (W2-D2) sometimes produces a plausible but wrong root cause with confidence 0.9+. Engineer trusts it → fixes the wrong service → 30 minutes wasted.

**Counter:** grounded confidence — only high when there is evidence linkage (metric anomaly + log signature + topology distance). Reject output if the citation is empty.

### 7.5 Monitoring Dependency Loop

**Roblox 2021:** the monitoring stack ran on Consul. Consul went down → monitoring couldn't alert → AIOps had no input → silent black-out.

**Counter:** the AIOps platform has its own observability stack that does not depend on the monitored services.

---

## 8. Exercises

### 8.1 Provided Setup

Download the **starter pack** (skeleton — not the full stack):

```bash
wget https://learning-notes-dz2.pages.dev/aiops-w3/lab/w3-d2-pack.zip
unzip w3-d2-pack.zip -d w3-d2-pack/
cd w3-d2-pack/
cat README.md   # read this first
```

**What the pack ships:**

```
README.md                         integration guide for your stack
experiments_template.yaml         10-entry YAML — fill in 2-9 yourself
synthetic_probe.sh                external steady-state probe (§6.4)
pipeline/chaos_runner_skeleton.py runner with 2 TODO functions (§8.5)
configs/prometheus_targets.yml    example scrape targets — adapt to your stack
scripts/
├── start_stack.sh                stub — wire to your docker-compose
├── capture_baseline.py           N-min Prometheus snapshot → baseline.json
├── query_pipeline.py             call /alerts + /correlate + /rca
└── score_run.py                  scoreboard from chaos_results.json
```

**What the pack does NOT ship** (build yourself or reuse from W2 Lab C):

- `docker-compose.yml` for the 10-service stack
- Source code for the 10 mock services (frontend, api-gateway, payment-svc, inventory-svc, notification-svc, checkout-svc, auth-svc, log-collector, dns-resolver, cache-svc)
- AIOps pipeline FastAPI exposing `/alerts`, `/correlate`, `/rca`
- Pumba + Toxiproxy binaries (install separately — see §4)

**Recommended path:** clone your group's W2 Lab C stack, extend it to 10 services if needed, then edit `scripts/start_stack.sh` to `docker compose up -d` against that stack.

**Target topology:**

```
frontend → api-gateway → ┬→ payment-svc → payment-db
                         ├→ inventory-svc → inventory-db
                         ├→ notification-svc → kafka
                         └→ checkout-svc → ┬→ payment-svc
                                           └→ inventory-svc
+ auth-svc, log-collector, dns-resolver, cache-svc
+ prometheus 2.50, grafana 10.4, alertmanager 0.27
+ AIOps pipeline (FastAPI on port 8000):
   - GET  /alerts?since=<ts>       → list alerts fired
   - POST /correlate {window}      → cluster
   - POST /rca {cluster}           → {root_service, confidence, evidence}
```

### 8.2 Step 1 — Capture Baseline + Start Synthetic Probe

```bash
bash scripts/start_stack.sh                     # wait for all service healthchecks OK
python scripts/capture_baseline.py --duration 300 --out baseline.json

# canonical steady-state signal — run in background through all 10 experiments (see §6.4)
nohup bash synthetic_probe.sh http://localhost:8080/checkout/health probe.log &
echo $! > probe.pid
```

`baseline.json` contains the steady-state mean + p99 for each (service, metric). `probe.log` provides an independent external signal — pass-rate must be ≥ 99% within a 60s window before starting Step 2.

### 8.3 Step 2 — Experiment Catalog

| # | Target | Fault | Expected pipeline response |
|---|---|---|---|
| 1 | payment-svc | netem delay +500ms | detect latency anomaly, RCA pick payment |
| 2 | payment-svc | netem loss 30% | detect error_rate, RCA pick payment |
| 3 | inventory-svc | pod kill every 60s | detect availability, RCA pick inventory |
| 4 | api-gateway | stress CPU 90% | detect latency cascade across all downstream |
| 5 | payment-db | memory fill 95% | detect connection pool, RCA pick payment-db |
| 6 | auth-svc (lateral) | clock skew +60s | detect cert/JWT fail, RCA pick auth |
| 7 | log-collector | disk fill 95% | detect log ingestion lag (meta-monitoring catch?) |
| 8 | frontend ↔ api-gateway | full partition 30s | detect all-downstream timeout, RCA pick edge |
| 9 | dns resolver | slow lookup +2s | detect intermittent error, RCA depends on topology |
| 10 | checkout-svc | HTTP 500 inject 20% | retry storm scenario, RCA must NOT pick checkout |

> All 10 experiments must be run. Order does not matter, but there must be a **120s cooldown between each one**.

### 8.4 Step 3 — Fill in `experiments.yaml`

Copy `experiments_template.yaml → experiments.yaml`. Field structure follows §5 (5 fields: name, hypothesis, blast_radius, rollback, measurement, ground_truth). Entries #1 + #10 are pre-filled as reference; #2–9 remain TODO. All 10 entries must be complete before running the runner.

### 8.5 Step 4 — Implement `chaos_runner.py`

Copy `pipeline/chaos_runner_skeleton.py → chaos_runner.py`. Implement the 2 functions marked TODO:

- `build_inject_cmd(exp)` — dispatcher by `fault_type`, returns a command list for `subprocess.run`. Covers the 10 fault types in §3 (latency, network_loss, availability, cpu_saturation, memory, disk_fill, time_skew, network_partition, dns_latency, cascade_retry).
- `print_scoreboard(results)` — print the confusion matrix in the format from §8.6.

### 8.6 Step 5 — Run 10 Experiments + Score

```bash
python chaos_runner.py
# → chaos_results.json + stdout scoreboard
```

**Required scoreboard format:**

```
==== Chaos Run ====
Total: 10
Detected: <N>/10
RCA correct: <N>/<detected>
False alarms in baseline windows: <N>
Precision: <float>
Recall: <float>
MTTD p50: <s>, p95: <s>

Per-experiment:
| # | name              | detected | mttd  | rca_service  | rca_correct |
|---|-------------------|----------|-------|--------------|-------------|
| 1 | payment_latency   | Y        | 28s   | payment-svc  | Y           |
| 2 | ...               | ...      | ...   | ...          | ...         |

Gaps identified:
- <experiment id>: <symptom> → <suspected root cause in pipeline>
```

**Acceptance criteria:**

- Detected ≥ 7/10 (70% recall)
- RCA correct ≥ 5/7 of those detected (≈70% RCA accuracy)
- False alarms in the 5-min baseline window ≤ 1

> If acceptance fails: log the gap in §8.7, do not tune the pipeline to force-pass (that is dishonest).

### 8.7 Step 6 — Write `chaos_report.md`

Required sections:

```markdown
# Chaos Engineering Report — <your name>

## 1. Setup
- Stack version + commit hash
- Pipeline version + commit hash
- Baseline window: <start> → <end>
- Total experiments run: 10

## 2. Results table
[paste scoreboard from §8.6]

## 3. Detailed per-experiment analysis
For EACH experiment, 80-150 words:
- Hypothesis (copy from experiments.yaml)
- Observed: detected or not, MTTD, RCA service
- Match expected? If not, reason (data evidence)

## 4. Gap analysis — top 3 pipeline weaknesses
For each gap:
- Symptom: <specific observation, which experiment, which numbers>
- Likely cause in pipeline: <detector? correlator? RCA?>
- Recommended fix: <concrete, with reference to §7 failure modes>

## 5. Hypothesis for unconfirmed gaps
[Optional but encouraged] Which gaps need more experiments to confirm?
```

### 8.8 Step 7 — `SUBMIT.md`

```markdown
# W3-D2 Submission — <your name>

## 3 things I learned about my AIOps pipeline
1. ...
2. ...
3. ...

## 1 fault I expected the pipeline to catch but it missed
- Experiment: ...
- Why I expected detection: ...
- Why the pipeline missed (hypothesis): ...

## 1 trade-off in pipeline design I want to rethink
...

## Scoreboard summary
- detected: __/10
- rca_correct: __/__
- mttd_p50: __s
- false_alarms: __
- verdict: __
```

### 8.9 Acceptance Checklist

- [ ] `experiments.yaml` has all 10 entries, each with all 5 fields (hypothesis, blast_radius, rollback, measurement, ground_truth)
- [ ] `chaos_runner.py` runs, with no hard-coded experiments
- [ ] `chaos_results.json` has all 10 entries
- [ ] `probe.log` runs throughout all 10 experiments, attached to submission (proves external steady-state signal)
- [ ] Scoreboard prints in §8.6 format
- [ ] Meets §8.6 acceptance: detected ≥ 7/10, RCA correct ≥ 5/detected, FA ≤ 1
- [ ] `chaos_report.md` has all 4 required sections (5 is optional)
- [ ] `SUBMIT.md` has all 4 sections

---

## 9. Anti-Patterns

| Anti-pattern | Consequence |
|---|---|
| Inject faults without a hypothesis | Break the system, learn nothing |
| Inject into prod before passing staging | Real outage, not chaos |
| Forget the rollback script | Fault sticks after the experiment, ops must fix |
| Measurement is only "system still alive" | Miss silent failures, partial degradation |
| Skip blast radius escalation | Stage 1 fails → stage 5 destroys prod |
| Chaos monthly, not continuous | 30 days of drift between runs → bug already in production before chaos catches it |
| Inject 1 service, not combinations | Real outages are usually multi-fault (Roblox: streaming + BoltDB) |
| Do not version experiment config | Reproducibility = 0, can't debug flaky runs |

---

## 10. References

| Source | Topic | URL |
|---|---|---|
| Rosenthal et al. | Principles of Chaos Engineering (canonical, 5 principles) | https://principlesofchaos.org/ |
| Rosenthal & Jones | *Chaos Engineering: System Resiliency in Practice*, O'Reilly 2020 | https://www.oreilly.com/library/view/chaos-engineering/9781492043860/ |
| Basiri et al. | "Chaos Engineering" IEEE Software 2016 | https://ieeexplore.ieee.org/document/7471636 |
| Netflix Tech Blog | "ChAP: Chaos Automation Platform" | https://netflixtechblog.com/chap-chaos-automation-platform-53e6d528371f |
| Roblox postmortem | Real cascading failure (Consul + BoltDB) | https://about.roblox.com/newsroom/2022/01/roblox-return-to-service-10-28-10-31-2021 |
| Pumba | Docker chaos tool | https://github.com/alexei-led/pumba |
| Chaos Mesh | K8s chaos (CNCF) | https://chaos-mesh.org/ |
| LitmusChaos | K8s chaos + CI/CD (CNCF) | https://litmuschaos.io/ |
| Toxiproxy | Network chaos | https://github.com/Shopify/toxiproxy |
| AWS Fault Injection Simulator | AWS-managed | https://aws.amazon.com/fis/ |
| Container Solutions blog | Chaos tool comparison | https://blog.container-solutions.com/comparing-chaos-engineering-tools |
| Adrian Cockcroft talk | Failure modes in microservices (re:Invent 2019) | https://www.youtube.com/watch?v=NXSXMAxJSWE |
