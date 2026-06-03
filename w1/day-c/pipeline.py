import json
import time
from collections import deque

def compute_rolling_mean(window):
    return sum(window) / len(window) if window else 0.0

def compute_rate_of_change(current, previous):
    if previous == 0:
        return 0.0
    return ((current - previous) / previous) * 100.0

def run_pipeline():
    # Mock data stream representing CPU usage metrics
    mock_queue = [
        {"ts": 1705314000, "metric": "cpu_usage", "value": 45.2, "host": "srv-1"},
        {"ts": 1705314015, "metric": "cpu_usage", "value": 46.5, "host": "srv-1"},
        {"ts": 1705314030, "metric": "cpu_usage", "value": 50.1, "host": "srv-1"},
        {"ts": 1705314045, "metric": "cpu_usage", "value": 85.0, "host": "srv-1"}, # Spike
        {"ts": 1705314060, "metric": "cpu_usage", "value": 88.2, "host": "srv-1"},
        {"ts": 1705314075, "metric": "cpu_usage", "value": 47.0, "host": "srv-1"},
    ]

    window_size = 3
    window = deque(maxlen=window_size)
    previous_value = None
    
    features = []

    print("Starting mock streaming pipeline...")
    for event in mock_queue:
        val = event["value"]
        window.append(val)
        
        rolling_mean = compute_rolling_mean(window)
        roc = compute_rate_of_change(val, previous_value) if previous_value is not None else 0.0
        
        feature_event = {
            "ts": event["ts"],
            "metric": event["metric"],
            "host": event["host"],
            "value": val,
            "feature_rolling_mean_3": round(rolling_mean, 2),
            "feature_rate_of_change_pct": round(roc, 2)
        }
        features.append(feature_event)
        previous_value = val
        
        print(f"Processed event: {feature_event}")
        time.sleep(0.5)  # Simulate streaming delay
        
    output_file = "features.json"
    with open(output_file, "w") as f:
        json.dump(features, f, indent=2)
    
    print(f"\nPipeline finished. Features saved to {output_file}")

if __name__ == "__main__":
    run_pipeline()
