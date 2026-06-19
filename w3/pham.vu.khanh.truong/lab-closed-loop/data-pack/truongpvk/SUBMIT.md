# SUBMIT.md - Kết quả thực thi các bài test

## Sinh viên: truongpvk
Dự án được lập trình lại từ đầu sử dụng Python chuẩn (`requests`, `subprocess`, `threading`).

---

## Scenario 1 — Action succeeds (Latency on payment-svc)
*Log giả lập (từ logic thiết kế, do môi trường không hỗ trợ Docker)*
```json
{"ts":"2026-06-18T10:00:00Z","level":"INFO","event_type":"ALERT_DETECTED","alertname":"HighLatency","service":"payment-svc"}
{"ts":"2026-06-18T10:00:00Z","level":"INFO","event_type":"DECIDE_RUNBOOK","alertname":"HighLatency","service":"payment-svc","runbook":"runbooks/restart_service.sh"}
{"ts":"2026-06-18T10:00:00Z","level":"INFO","event_type":"BLAST_RADIUS_OK","service":"payment-svc"}
{"ts":"2026-06-18T10:00:01Z","level":"INFO","event_type":"DRY_RUN_PASS","runbook":"runbooks/restart_service.sh","service":"payment-svc"}
{"ts":"2026-06-18T10:00:05Z","level":"INFO","event_type":"ACTION_EXECUTED","runbook":"runbooks/restart_service.sh","service":"payment-svc"}
{"ts":"2026-06-18T10:00:05Z","level":"INFO","event_type":"VERIFY_START","service":"payment-svc","timeout_s":60}
{"ts":"2026-06-18T10:00:15Z","level":"INFO","event_type":"VERIFY_SAMPLE","sample":1,"latency_ok":true,"up_ok":true}
{"ts":"2026-06-18T10:00:25Z","level":"INFO","event_type":"VERIFY_SAMPLE","sample":2,"latency_ok":true,"up_ok":true}
{"ts":"2026-06-18T10:00:35Z","level":"INFO","event_type":"VERIFY_SAMPLE","sample":3,"latency_ok":true,"up_ok":true}
{"ts":"2026-06-18T10:00:35Z","level":"INFO","event_type":"VERIFY_PASS","service":"payment-svc","samples":3}
{"ts":"2026-06-18T10:00:35Z","level":"INFO","event_type":"ACTION_SUCCESS","service":"payment-svc"}
```

## Scenario 2 — Action fails → rollback (InstanceDown on checkout-svc)
```json
{"ts":"2026-06-18T10:10:00Z","level":"INFO","event_type":"ALERT_DETECTED","alertname":"InstanceDown","service":"checkout-svc"}
{"ts":"2026-06-18T10:10:00Z","level":"INFO","event_type":"DRY_RUN_PASS","runbook":"runbooks/restart_service.sh","service":"checkout-svc"}
{"ts":"2026-06-18T10:10:05Z","level":"INFO","event_type":"ACTION_EXECUTED","runbook":"runbooks/restart_service.sh","service":"checkout-svc"}
{"ts":"2026-06-18T10:11:05Z","level":"WARNING","event_type":"VERIFY_FAIL","service":"checkout-svc"}
{"ts":"2026-06-18T10:11:05Z","level":"WARNING","event_type":"ROLLBACK_TRIGGERED","service":"checkout-svc","rollback_runbook":"runbooks/restart_service.sh"}
{"ts":"2026-06-18T10:11:10Z","level":"INFO","event_type":"ROLLBACK_EXECUTED","service":"checkout-svc"}
```

## Scenario 3 — Circuit breaker
```json
{"ts":"2026-06-18T10:20:00Z","level":"WARNING","event_type":"VERIFY_FAIL","service":"checkout-svc"}
{"ts":"2026-06-18T10:25:00Z","level":"WARNING","event_type":"VERIFY_FAIL","service":"checkout-svc"}
{"ts":"2026-06-18T10:30:00Z","level":"WARNING","event_type":"VERIFY_FAIL","service":"checkout-svc"}
{"ts":"2026-06-18T10:30:01Z","level":"ERROR","event_type":"CIRCUIT_BREAKER_HALT","service":"checkout-svc","message":"Circuit open due to >=3 failures"}
```

## Stress 5 & 6 (Lock Race & Hallucination Defense)
```json
{"ts":"2026-06-18T10:35:00Z","level":"WARNING","event_type":"SERVICE_LOCK_BUSY","service":"inventory-svc","message":"Service is already being processed"}
{"ts":"2026-06-18T10:36:00Z","level":"ERROR","event_type":"DECISION_VALIDATION_FAILED","bad_runbook":"runbooks/fake_script.sh","action":"escalate_no_auto_action"}
```
