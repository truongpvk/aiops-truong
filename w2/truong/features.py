import json
import re
from pathlib import Path
from collections import defaultdict

def tokenize(text: str) -> set:
    text = re.sub(r'\d+', '', text)
    text = re.sub(r'[^\w\s]', ' ', text)
    return set(text.lower().split())

def jaccard(s1: set, s2: set) -> float:
    if not s1 and not s2: return 1.0
    return len(s1 & s2) / len(s1 | s2)

class FeatureExtractor:
    def __init__(self, history_path: Path):
        self.known_log_templates = {}
        if history_path.exists():
            history = json.loads(history_path.read_text())
            for inc in history:
                for sig in inc.get("log_signatures", []):
                    self.known_log_templates[sig] = tokenize(sig)

    def match_log_template(self, msg: str) -> str:
        msg_tokens = tokenize(msg)
        best_match = None
        best_score = 0.0
        for template, template_tokens in self.known_log_templates.items():
            score = jaccard(msg_tokens, template_tokens)
            if score > best_score:
                best_score = score
                best_match = template
        
        if best_score > 0.3 and best_match:
            return best_match
        
        # Fallback to normalized text as template
        text = re.sub(r'\d+', '<NUM>', msg)
        text = re.sub(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '<UUID>', text)
        return text

    def extract(self, incident: dict) -> dict:
        affected_services = set()
        
        # 1. Trigger Alert
        trigger = incident.get("trigger_alert", {})
        if trigger.get("service"):
            affected_services.add(trigger["service"])
            
        # 2. Logs
        log_counts = defaultdict(int)
        for log in incident.get("logs", []):
            if log.get("level") in ["ERROR", "FATAL"]:
                affected_services.add(log["svc"])
            template = self.match_log_template(log["msg"])
            log_counts[template] += 1
            
        # 3. Traces
        trace_stats = defaultdict(lambda: {"count": 0, "error_count": 0, "max_p99": 0, "max_p50": 0})
        for t in incident.get("traces", []):
            edge = f'{t["from"]}->{t["to"]}'
            trace_stats[edge]["count"] += t.get("count", 0)
            trace_stats[edge]["error_count"] += t.get("error_count", 0)
            trace_stats[edge]["max_p99"] = max(trace_stats[edge]["max_p99"], t.get("p99_ms", 0))
            trace_stats[edge]["max_p50"] = max(trace_stats[edge]["max_p50"], t.get("p50_ms", 0))
            
        trace_features = {}
        for edge, stats in trace_stats.items():
            err_rate = stats["error_count"] / stats["count"] if stats["count"] > 0 else 0
            dev_ratio = stats["max_p99"] / stats["max_p50"] if stats["max_p50"] > 0 else 1.0
            
            if err_rate > 0.1 or dev_ratio > 2.0:
                parts = edge.split("->")
                affected_services.add(parts[0])
                affected_services.add(parts[1])
                
            trace_features[edge] = {
                "error_rate": err_rate,
                "p99_deviation_ratio": dev_ratio
            }
            
        # 4. Metrics
        metric_features = {}
        samples = incident.get("metrics_window", {}).get("samples", {})
        for metric_name, points in samples.items():
            if not points: continue
            vals = [p[1] for p in points]
            delta = max(vals) - min(vals)
            metric_features[metric_name] = delta
            
        svc_scores = defaultdict(float)
        log_scores = defaultdict(float)
        for log in incident.get("logs", []):
            if log.get("level") in ["ERROR", "FATAL", "WARN"]:
                log_scores[log["svc"]] += 1.0
                svc_scores[log["svc"]] += 1.0
                
        trace_scores = defaultdict(float)
        for t in incident.get("traces", []):
            err_rate = t.get("error_count", 0) / max(1, t.get("count", 0))
            if err_rate > 0.05:
                trace_scores[t["from"]] += err_rate * 1000.0
                trace_scores[t["to"]] += err_rate * 1000.0
                svc_scores[t["from"]] += err_rate * 1000.0
                svc_scores[t["to"]] += err_rate * 1000.0
                
        root_service = None
        if svc_scores:
            root_service = max(svc_scores.items(), key=lambda x: x[1])[0]
            
        logs_root = max(log_scores.items(), key=lambda x: x[1])[0] if log_scores else None
        traces_root = max(trace_scores.items(), key=lambda x: x[1])[0] if trace_scores else None
        
        is_ambiguous = False
        if logs_root and traces_root and logs_root != traces_root:
            if trace_scores[logs_root] < trace_scores[traces_root] * 0.5:
                if log_scores[logs_root] > 10 and trace_scores[traces_root] > 50:
                    is_ambiguous = True
            
        return {
            "affected_services": list(affected_services),
            "root_service": root_service,
            "is_ambiguous": is_ambiguous,
            "log_signatures": dict(log_counts),
            "trace_signatures": trace_features,
            "metric_signatures": metric_features
        }

def extract_features_history(history_inc: dict) -> dict:
    affected = history_inc.get("affected_services", [])
    
    log_sigs = {}
    for sig in history_inc.get("log_signatures", []):
        log_sigs[sig] = 1
        
    trace_sigs = {}
    for t in history_inc.get("trace_signatures", []):
        edge = f'{t["from"]}->{t["to"]}'
        trace_sigs[edge] = {
            "error_rate": t.get("error_rate", 0),
            "p99_deviation_ratio": t.get("p99_deviation_ratio", 1.0)
        }
        
    metric_sigs = {}
    for m in history_inc.get("metric_signatures", []):
        delta_str = m.get("delta", "0 -> 0")
        parts = delta_str.replace("->", "|").split("|")
        try:
            delta = abs(float(parts[1].strip()) - float(parts[0].strip()))
        except:
            delta = 0.0
        metric_name = f"{m['service']}.{m['metric']}"
        metric_sigs[metric_name] = delta
        
    return {
        "affected_services": affected,
        "log_signatures": log_sigs,
        "trace_signatures": trace_sigs,
        "metric_signatures": metric_sigs
    }
