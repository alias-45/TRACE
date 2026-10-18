"""Independent held-out synthetic benchmark for TRACE.

This is a self-contained offline test. It does NOT connect to or modify FalkorDB.
The six held-out incidents and their labels are separate from csv/eval_labels.csv.
Labels are specified before running the methods. This is a small sanity benchmark,
not evidence of production accuracy. Do not tune rules against these cases and then
continue calling them held-out.

Run from project root:
    python heldout_benchmark.py
"""
from itertools import combinations

# Each incident has flat-log attributes and explicit graph-service connections.
# Deliberate decoys: H003/H004 share a low-criticality service; H005/H006 share a device.
INCIDENTS = {
    "H001": {"device": "PAY-LAP-01", "owner": "U-H1", "services": {"Vendor Payments"}},
    "H002": {"device": "PAY-SRV-02", "owner": "U-H2", "services": {"Vendor Payments"}},
    "H003": {"device": "HR-LAP-03", "owner": "U-H3", "services": {"Internal Wiki"}},
    "H004": {"device": "HR-LAP-04", "owner": "U-H4", "services": {"Internal Wiki"}},
    "H005": {"device": "SHARED-KIOSK-9", "owner": "U-H5", "services": {"Badge Portal"}},
    "H006": {"device": "SHARED-KIOSK-9", "owner": "U-H6", "services": {"Badge Portal"}},
}
SERVICE_CRITICALITY = {
    "Vendor Payments": "high",
    "Internal Wiki": "low",
    "Badge Portal": "low",
}
# Hand-labelled before evaluating any method. Pair is linked only when the scenario
# defines a meaningful investigation relationship, not merely a shared entity.
TRUTH = {
    frozenset(("H001", "H002")),  # independent alerts converge on high-criticality payment service
    frozenset(("H005", "H006")),  # explicit same-device sequence; useful correlation, not causal proof
}
UNIVERSE = {frozenset(p) for p in combinations(INCIDENTS, 2)}


def score(found):
    found = set(found) & UNIVERSE
    tp = len(found & TRUTH)
    fp = len(found - TRUTH)
    fn = len(TRUTH - found)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"pairs_found": len(found), "TP": tp, "FP": fp, "FN": fn,
            "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
            "predicted_pairs": sorted("-".join(sorted(p)) for p in found)}


def main():
    flat_device = set()
    flat_device_owner = set()
    graph_any_service = set()
    graph_critical_service = set()
    ids = list(INCIDENTS)
    for a, b in combinations(ids, 2):
        x, y = INCIDENTS[a], INCIDENTS[b]
        pair = frozenset((a, b))
        if x["device"] == y["device"]:
            flat_device.add(pair)
        if x["device"] == y["device"] or x["owner"] == y["owner"]:
            flat_device_owner.add(pair)
        shared = x["services"] & y["services"]
        if shared:
            graph_any_service.add(pair)
            if any(SERVICE_CRITICALITY.get(s, "low").lower() in {"high", "critical"} for s in shared):
                graph_critical_service.add(pair)
    methods = [
        ("Flat log: same device", flat_device),
        ("Flat log: same device or owner", flat_device_owner),
        ("Graph proxy: any shared service", graph_any_service),
        ("Graph proxy: shared high/critical service", graph_critical_service),
    ]
    print("TRACE HELD-OUT SYNTHETIC BENCHMARK (offline; no database changes)")
    print(f"Incidents: {len(INCIDENTS)} | candidate pairs: {len(UNIVERSE)} | labelled positives: {len(TRUTH)} | negatives: {len(UNIVERSE)-len(TRUTH)}")
    print("Ground truth: " + ", ".join(sorted("-".join(sorted(p)) for p in TRUTH)))
    for label, found in methods:
        print(f"\n{label}\n{score(found)}")
    print("\nLIMITATIONS: six hand-designed synthetic incidents; labels and features are scenario-authored; graph proxy is not the production FalkorDB query; no LLM is run here. This validates plumbing and decoy handling only, not real-world generalization.")

if __name__ == "__main__":
    main()
