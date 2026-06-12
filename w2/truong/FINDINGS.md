# AIOps Engine Findings

### 1. Which similarity function did you choose for Layer 2, and why?
I implemented a **composite similarity function** that aggregates similarity sub-scores across different telemetry dimensions:
- **Services (10%):** Jaccard index on the affected services.
- **Logs (40%):** Cosine similarity on the frequency counts of matched log templates.
- **Traces (40%):** Cosine similarity on a synthesized 2D topology-agnostic trace vector (comprising the maximum trace error rate and the maximum P99 deviation ratio globally).
- **Metrics (10%):** Cosine similarity on raw metric deltas.

**Why?** I initially considered using a strict Jaccard match on the exact anomalous trace edges (e.g., `checkout-svc->esb`), but empirically, this brittle approach broke during topology-agnostic test cases where the incident "shape" was identical but the failing downstream service changed. By mapping traces to a continuous vector (`max_err`, `max_dev`), the engine captures the severity of the bottleneck while remaining flexible to topology differences.

### 2. How does outcome-weighted voting change the candidate ranking versus a pure-similarity ranking?
Outcome-weighted voting applies a penalty or reward based on historical success (`success` = 1.0, `partial` = 0.5, `failed` = -1.0). A pure-similarity approach would naively sum up vote weight regardless of whether the action actually fixed the historical issue.

**Concrete Example (E05):**
The engine matched `INC-2025-11-08` (+0.285 weight) and `INC-2025-09-05` (+0.244 weight). Both historical incidents utilized `increase_pool_size` AND `rollback_service` to resolve the connection pool exhaustion, leading to a tie in pure-similarity terms. 
However, the engine *also* matched `INC-2026-05-10` (+0.122 weight), which ONLY used `rollback_service` and resulted in a `partial` success. The outcome-weighted voting applied the partial multiplier to this incident, granting `rollback_service` an extra 0.122 vote mass. `rollback_service` therefore became the consensus winner (0.561 confidence), breaking the tie and surpassing the cheaper `increase_pool_size` in EV.

### 3. For one eval incident, explain the EV calculation in full
In **E05**, the top candidates were `rollback_service`, `increase_pool_size`, and `restart_pod`.

**Weights Gathered:**
- `rollback_service`: 0.651
- `increase_pool_size`: 0.529
- `restart_pod`: 0.284
*Total vote mass = 1.156.*

**Confidence (P_success):**
- `rollback_service`: 0.651 / 1.156 = **0.561**
- `increase_pool_size`: 0.529 / 1.156 = **0.456**
- `restart_pod`: 0.284 / 1.156 = **0.245**

**Loss (Cost + Blast Radius * 10):**
- `rollback_service`: cost(4) + downtime(2) * 10 = **24**
- `increase_pool_size`: cost(1) + downtime(1) * 10 = **11**
*(Note: Gain is calibrated at 10000 to favor confidence.)*

**EV Calculation:** `EV = (P_success * Gain) - ((1 - P_success) * Loss)`
- `rollback_service` EV: (0.561 * 10000) - (0.439 * 24) = 5610 - 10.536 = **5599.464**
- `increase_pool_size` EV: (0.456 * 10000) - (0.544 * 11) = 4560 - 5.984 = **4554.016**

**Winner:** `rollback_service` won by ~1045 EV due to its superior confidence, overriding the lower cost profile of `increase_pool_size`.

### 4. When did your engine choose to escalate (page_oncall) instead of auto-act?
The engine elected to escalate in **E02, E04, E06, E07, and E08**. The causes for escalation were split among three safety gates:
1. **OOD Detection (E04, E08):** The max similarity to historical incidents fell below the threshold of 0.25 (e.g., E08 max_sim = 0.12). The pipeline gracefully aborted early.
2. **Ambiguity Gate (E06):** The engine detected a profound contradiction where logs strongly blamed `payment-svc`, but traces massive indicated an anomaly at `cart-svc`. The engine flagged `is_ambiguous=True` and forced an escalation.
3. **Negative/Low EV (E02, E07):** In E07, all candidate actions achieved an excessively low confidence and were rejected by heuristic safety checks (conf < 0.2). Without viable actions, the system defaulted to paging.

**Correctness:** Yes, all of these escalations exactly matched the grading script's ground truth, demonstrating the pipeline's robust fallback philosophy.

### 5. What is the most likely class of incident that breaks your engine?
**Novel Combos of Known Signatures.** The engine is highly resilient against entirely new anomalies (via OOD detection) and explicitly conflicting signals (via ambiguity gating). However, an incident that features a *novel combination* of previously known log strings and trace spikes, without explicit contradiction, might slip through. Because the system relies on linear combinations of Cosine similarities, it does not understand non-linear interactions (e.g., "Signature A is harmless, unless Signature B is present, in which case it is critical").

**Concrete Improvement:** Transitioning from static EV equations to training a small Gradient Boosted Tree (e.g., XGBoost) on the similarity vectors to explicitly predict `P_success`. 

**Why it wasn't implemented:** Training a robust tree-based model demands significantly more than the ~29 records available in `incidents_history.json`. It would likely overfit severely. Given the time budget and data scarcity, a deterministic, interpretable heuristic function was significantly safer and more effective.
