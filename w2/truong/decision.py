def select_action(candidates_info: dict, actions_catalog: list) -> dict:
    if candidates_info.get("is_ood"):
        return {
            "selected_action": "page_oncall",
            "params": {"team": "platform-team"},
            "confidence": 1.0,
            "evidence": candidates_info.get("evidence", [])
        }
        
    candidates = candidates_info.get("candidates", [])
    if not candidates:
        return {
            "selected_action": "page_oncall",
            "params": {"team": "platform-team"},
            "confidence": 0.0,
            "evidence": candidates_info.get("evidence", []) + [{"reason": "No valid candidates"}]
        }
        
    catalog_map = {a["name"]: a for a in actions_catalog}
    
    best_action = None
    best_ev = -float('inf')
    best_conf = 0.0
    
    evaluated_candidates = []
    
    for c in candidates:
        action = c["action"]
        name = action["name"]
        conf = c["confidence"]
        
        if name == "page_oncall":
            continue
            
        cat = catalog_map.get(name, {})
        cost = cat.get("cost_min", 0) + cat.get("downtime_min", 0) * 2
        blast = cat.get("blast_radius_services", 0)
        
        # Blast Radius Gate
        if conf < 0.5 and blast >= 2:
            evaluated_candidates.append({"action": name, "status": "rejected_blast_radius", "conf": conf, "blast": blast})
            continue
            
        if conf < 0.3:
            evaluated_candidates.append({"action": name, "status": "rejected_low_conf", "conf": conf})
            continue
            
        loss = cost + blast * 10
        gain = 10000 # Make confidence dominant
        
        ev = (conf * gain) - ((1.0 - conf) * loss)
        evaluated_candidates.append({"action": name, "status": "evaluated", "ev": ev, "conf": conf, "loss": loss})
        
        if ev > best_ev:
            best_ev = ev
            best_action = action
            best_conf = conf
            
    evidence = candidates_info.get("evidence", []) + [{"ev_evaluations": evaluated_candidates}]
            
    if best_action and best_ev > 0:
        return {
            "selected_action": best_action["name"],
            "params": best_action.get("params", {}),
            "confidence": best_conf,
            "evidence": evidence
        }
        
    # Fallback
    return {
        "selected_action": "page_oncall",
        "params": {"team": "platform-team"},
        "confidence": 1.0,
        "evidence": evidence + [{"reason": "All candidates had negative EV or were rejected."}]
    }
