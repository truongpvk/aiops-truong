# Evidence-Driven Remediation Engine

This repository contains the implementation for an AIOps Evidence-Driven Remediation Engine. The engine is designed to parse raw telemetry (logs, traces, and metrics) from a live incident, match it against a corpus of historical incidents, and autonomously recommend a remediation action utilizing Expected Value (EV) decision making.

## Architecture

The pipeline is split into three core layers:

1. **Feature Extraction (`features.py`):**
   Parses raw JSON incident files. It extracts high-signal features including affected services, template-matched log counts, and topology-agnostic trace anomaly vectors (comprising `max_error_rate` and `max_dev_ratio`). It also features advanced heuristics for resolving the likely `root_service` and detecting ambiguous scenarios where logs and traces drastically conflict.

2. **Retrieval and Voting (`retrieval.py`):**
   Compares the live incident features against the historical corpus (`incidents_history.json`). It calculates a composite similarity score incorporating Jaccard and Cosine distance across all four telemetry dimensions. Out-of-Distribution (OOD) incidents are immediately escalated. The top-k candidates undergo an outcome-weighted voting procedure, assigning penalties/rewards based on historical action outcomes.

3. **Decision & Safety (`decision.py`):**
   Evaluates candidate actions using Expected Value (`ev = (P_success * Gain) - ((1 - P_success) * Loss)`), cross-referencing against an action catalog (`actions.yaml`) for blast radius constraints. If no action yields a high EV or clears the heuristic safety gates, the pipeline defaults to `page_oncall`.

## Usage

You can invoke the engine via the CLI entrypoint:

```bash
python engine.py decide \
  --incident eval/E01.json \
  --history incidents_history.json \
  --actions actions.yaml \
  >> audit.jsonl
```

## Evaluation

To automatically test the engine against the 8 provided evaluation incidents and grade the output:

```bash
# Clear previous runs
rm audit.jsonl

# Generate audit log for E01-E08
for i in {01..08}; do
  python engine.py decide --incident "eval/E$i.json" >> audit.jsonl
done

# Run the grading script
python grade.py --audit audit.jsonl --expected eval/expected.json
```
