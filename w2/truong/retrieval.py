import math
import json
from features import extract_features_history

def cos_sim(v1: dict, v2: dict) -> float:
    keys = set(v1.keys()) | set(v2.keys())
    dot = sum(v1.get(k, 0) * v2.get(k, 0) for k in keys)
    mag1 = math.sqrt(sum(v**2 for v in v1.values()))
    mag2 = math.sqrt(sum(v**2 for v in v2.values()))
    if mag1 == 0 or mag2 == 0: return 0.0
    return dot / (mag1 * mag2)

def jaccard_list(l1: list, l2: list) -> float:
    s1, s2 = set(l1), set(l2)
    if not s1 and not s2: return 1.0
    return len(s1 & s2) / len(s1 | s2)

def extract_trace_vector(t_sigs):
    vec = {}
    for edge, t in t_sigs.items():
        vec[f"{edge}_err"] = t.get("error_rate", 0) * 10.0
        vec[f"{edge}_dev"] = max(0.0, t.get("p99_deviation_ratio", 1.0) - 2.0)
    return vec

def similarity(a: dict, b: dict) -> float:
    sim_svc = jaccard_list(a.get("affected_services", []), b.get("affected_services", []))
    sim_logs = cos_sim(a.get("log_signatures", {}), b.get("log_signatures", {}))
    
    t_a = extract_trace_vector(a.get("trace_signatures", {}))
    t_b = extract_trace_vector(b.get("trace_signatures", {}))
    sim_traces = cos_sim(t_a, t_b)
    
    m_a = a.get("metric_signatures", {})
    m_b = b.get("metric_signatures", {})
    sim_metrics = cos_sim(m_a, m_b)
    
    return 0.1 * sim_svc + 0.4 * sim_logs + 0.4 * sim_traces + 0.1 * sim_metrics

def parse_history_action_with_catalog(s: str, catalog: list) -> dict:
    parts = s.split(":")
    name = parts[0]
    raw_params = parts[1:]
    
    catalog_entry = next((a for a in catalog if a["name"] == name), None)
    params_dict = {}
    if catalog_entry:
        param_names = catalog_entry.get("params", [])
        for i, pname in enumerate(param_names):
            if i < len(raw_params):
                params_dict[pname] = raw_params[i]
    return {"name": name, "params": params_dict}

def retrieve_and_vote(query: dict, history: list, actions_catalog: list, top_k: int = 5, ood_threshold: float = 0.25) -> dict:
    if query.get("is_ambiguous"):
        return {
            "is_ood": True,
            "max_sim": 0.0,
            "candidates": [],
            "evidence": [{"reason": "Conflicting evidence between logs and traces"}]
        }
        
    scored_history = []
    for h in history:
        h_feat = extract_features_history(h)
        sim = similarity(query, h_feat)
        scored_history.append((sim, h))
        
    scored_history.sort(key=lambda x: x[0], reverse=True)
    top_candidates = scored_history[:top_k]
    
    max_sim = top_candidates[0][0] if top_candidates else 0.0
    
    if max_sim < ood_threshold:
        return {
            "is_ood": True,
            "max_sim": max_sim,
            "candidates": [],
            "evidence": [{"reason": "max_sim below OOD threshold", "max_sim": max_sim}]
        }
        
    votes = {}
    evidence = []
    
    # Calculate Max Possible Vote for Confidence normalization
    # Max possible vote is if all top_k were "success" and had similarity = 1.0 (or just sum of sims)
    max_possible_vote = sum(sim for sim, _ in top_candidates)
    if max_possible_vote == 0: max_possible_vote = 1.0
    
    for sim, h in top_candidates:
        outcome = h.get("outcome", "unknown")
        if outcome == "success":
            multiplier = 1.0
        elif outcome == "partial":
            multiplier = 0.5
        elif outcome == "failed":
            multiplier = -1.0
        else:
            multiplier = 0.0
            
        weight = sim * multiplier
        
        for action_str in h.get("actions_taken", []):
            action_dict = parse_history_action_with_catalog(action_str, actions_catalog)
            if action_dict["name"] == "rollback_service" and "target_version" in action_dict["params"]:
                action_dict["params"]["target_version"] = "previous"
                
            if "service" in action_dict.get("params", {}):
                query_root = query.get("root_service")
                if query_root:
                    action_dict["params"]["service"] = query_root
                
            action_key = json.dumps(action_dict, sort_keys=True)
            if action_key not in votes:
                votes[action_key] = 0.0
            votes[action_key] += weight
            
        evidence.append({
            "incident_id": h["id"],
            "similarity": round(sim, 3),
            "outcome": outcome,
            "actions_taken": h.get("actions_taken", []),
            "weight_contributed": round(weight, 3)
        })
        
    for k in votes:
        votes[k] = max(0.0, votes[k])
        
    ranked_actions = []
    for k, v in votes.items():
        # confidence is ratio of received vote vs max possible vote
        confidence = min(1.0, v / max_possible_vote)
        ranked_actions.append({
            "action": json.loads(k),
            "confidence": round(confidence, 3),
            "raw_score": v
        })
        
    ranked_actions.sort(key=lambda x: x["confidence"], reverse=True)
    
    return {
        "is_ood": False,
        "max_sim": round(max_sim, 3),
        "candidates": ranked_actions,
        "evidence": evidence
    }
