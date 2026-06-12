import json
from pathlib import Path
from features import FeatureExtractor
from retrieval import similarity
from engine import decide
import yaml

history_path = Path("incidents_history.json")
history = json.loads(history_path.read_text())
extractor = FeatureExtractor(history_path)
from features import extract_features_history
hist_feats = [extract_features_history(h) for h in history]

for inc_id in ["E03", "E05", "E06"]:
    incident = json.loads(Path(f"eval/{inc_id}.json").read_text())
    vec = extractor.extract(incident)
    print(f"--- {inc_id} Features ---")
    print(json.dumps(vec, indent=2))
    
    sims = []
    for h, hf in zip(history, hist_feats):
        s = similarity(vec, hf)
        sims.append((s, h["id"]))
    sims.sort(reverse=True)
    print(f"--- {inc_id} Top 3 matches ---")
    for s, hid in sims[:3]:
        print(f"{hid}: {s}")
