"""
cost_model.py — AIOps Platform Break-even Cost Model
W3-D3 Exercise §9.7

Implements the is_worth_it() function to evaluate whether investing in an AIOps
platform is justified based on incident frequency, downtime cost, and expected
MTTR reduction.
"""


def is_worth_it(
    num_services: int,
    incidents_per_month: int,
    avg_incident_duration_hours: float,
    downtime_cost_per_hour: float,
    expected_mttr_reduction_pct: float = 0.4,
    aiops_monthly_cost: float = 15_000,
) -> dict:
    """
    Evaluate whether an AIOps platform investment is justified.

    Args:
        num_services: Number of services being monitored.
        incidents_per_month: Average number of incidents per month.
        avg_incident_duration_hours: Average duration of each incident in hours.
        downtime_cost_per_hour: Cost of downtime per hour in USD.
        expected_mttr_reduction_pct: Expected reduction in MTTR (0.0 to 1.0).
            Default 0.4 = 40% reduction.
        aiops_monthly_cost: Monthly cost of running the AIOps platform in USD.
            Default $15,000.

    Returns:
        dict with keys:
            monthly_value (float): Monthly value saved by AIOps.
            monthly_cost (float): Monthly cost of AIOps platform.
            roi (float): Return on investment ratio (value / cost).
            payback_months (float): Months to recoup investment (or inf).
            verdict (str): "worth_it" | "marginal" | "not_worth_it"

    Verdict rule:
        roi > 1.5  → "worth_it"
        1.0 < roi <= 1.5 → "marginal"
        roi <= 1.0 → "not_worth_it"
    """
    monthly_downtime_hours = incidents_per_month * avg_incident_duration_hours
    monthly_value = (
        monthly_downtime_hours
        * expected_mttr_reduction_pct
        * downtime_cost_per_hour
    )
    roi = monthly_value / aiops_monthly_cost
    payback_months = (
        aiops_monthly_cost / monthly_value if monthly_value > 0 else float("inf")
    )

    if roi > 1.5:
        verdict = "worth_it"
    elif roi > 1.0:
        verdict = "marginal"
    else:
        verdict = "not_worth_it"

    return {
        "monthly_value": monthly_value,
        "monthly_cost": aiops_monthly_cost,
        "roi": roi,
        "payback_months": payback_months,
        "verdict": verdict,
    }


if __name__ == "__main__":
    # --- Scenario 1 (from §8.4): Small stack, few incidents ---
    # 20 services, 2 incidents/month, 1h each, $10k/h downtime, $15k AIOps cost
    # Expected: ROI 0.53 → not_worth_it
    result1 = is_worth_it(
        num_services=20,
        incidents_per_month=2,
        avg_incident_duration_hours=1,
        downtime_cost_per_hour=10_000,
        aiops_monthly_cost=15_000,
    )
    print("Scenario 1 (Small stack, few incidents):")
    print(f"  {result1}")
    print()

    # --- Scenario 2 (from §8.4): Right-sized stack ---
    # 100 services, 5 incidents/month, 2h each, $20k/h downtime, $25k AIOps cost
    # Expected: ROI 3.2 → worth_it
    result2 = is_worth_it(
        num_services=100,
        incidents_per_month=5,
        avg_incident_duration_hours=2,
        downtime_cost_per_hour=20_000,
        aiops_monthly_cost=25_000,
    )
    print("Scenario 2 (Right-sized stack):")
    print(f"  {result2}")
    print()

    # --- Scenario 3 (Custom): E-commerce platform during peak season ---
    # Industry: Mid-tier e-commerce (Southeast Asia market)
    # Justification: E-commerce platforms in SEA typically handle 50-200 services
    # during peak events (sale seasons like 6.6, 7.7, 11.11). Downtime during
    # flash sales costs $30k-$80k/hour due to lost GMV + marketing spend waste +
    # customer churn. We use $40k/h as a conservative mid-tier estimate.
    # Incidents spike to ~8/month during peak season due to traffic surges,
    # third-party payment gateway issues, and inventory sync failures.
    # AIOps cost is $35k/month including: Prometheus/Grafana infra ($8k),
    # log pipeline ($7k), model compute ($5k), 1 SRE engineer time ($15k).
    result3 = is_worth_it(
        num_services=150,
        incidents_per_month=8,
        avg_incident_duration_hours=1.5,
        downtime_cost_per_hour=40_000,
        aiops_monthly_cost=35_000,
    )
    print("Scenario 3 (E-commerce peak season — SEA market):")
    print(f"  {result3}")
    print()

    # --- Summary ---
    print("=" * 60)
    print("Summary:")
    for i, r in enumerate([result1, result2, result3], 1):
        print(
            f"  Scenario {i}: ROI={r['roi']:.2f}, "
            f"Payback={r['payback_months']:.2f} months, "
            f"Verdict={r['verdict']}"
        )
