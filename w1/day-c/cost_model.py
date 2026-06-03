import pandas as pd

def estimate_cost():
    # Scale Tiers
    tiers = [
        {"Tier": "Small", "Services": 10, "Log_GB_per_day": 50, "Metric_events_per_sec": 100000},
        {"Tier": "Medium", "Services": 100, "Log_GB_per_day": 500, "Metric_events_per_sec": 1000000},
        {"Tier": "Large", "Services": 1000, "Log_GB_per_day": 5000, "Metric_events_per_sec": 10000000},
    ]

    # Cost assumptions (Build / Self-host)
    # Log: ~$9 per GB/day per month (Based on Loki+S3 tiering: 500GB/day -> $4500/mo)
    # Metric: ~$0.002 per event/sec per month (1M events/sec -> $2000/mo)
    # Compute/Kafka: Base cost scaling with log volume (~$7.4 per GB/day log volume)
    
    # Cost assumptions (Buy / Datadog)
    # SaaS usually costs ~3x-4x of infrastructure for small/medium, scaling up.
    # We will use an estimation based on the content.md ($30K-$50K for medium)

    results = []

    for t in tiers:
        log_cost_build = t["Log_GB_per_day"] * 9
        metric_cost_build = t["Metric_events_per_sec"] * 0.002
        compute_cost_build = t["Log_GB_per_day"] * 7.4
        
        total_build = log_cost_build + metric_cost_build + compute_cost_build
        
        # Estimate Datadog as 3.5x for Medium, 4x for Large, etc.
        if t["Tier"] == "Small":
            total_buy = total_build * 3.0
        elif t["Tier"] == "Medium":
            total_buy = 40000  # Based on the text $30-50k
        else:
            total_buy = total_build * 4.5
            
        results.append({
            "Tier": t["Tier"],
            "Services": t["Services"],
            "Log(GB/day)": t["Log_GB_per_day"],
            "Metric(events/sec)": t["Metric_events_per_sec"],
            "Build_Storage_Log($)": round(log_cost_build),
            "Build_Storage_Metric($)": round(metric_cost_build),
            "Build_Compute($)": round(compute_cost_build),
            "Total_Build($)": round(total_build),
            "Total_Buy_SaaS($)": round(total_buy)
        })

    df = pd.DataFrame(results)
    print("Cost Estimation Breakdown (Monthly):\n")
    print(df.to_markdown(index=False))
    
    return df

if __name__ == "__main__":
    estimate_cost()
