"""Tune Social Force parameters on a development ATC day, not the final test day."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

import app
from social_force import SocialForceSimulation
from validation_runner import evaluate_snapshot, training_profile
from walkable_mask import build_walkable_mask

ROOT = Path(__file__).resolve().parent
GENERATED = ROOT / "generated"


def main(development_day: str, horizon: int, snapshots: int, history_seconds: float = 5.0) -> Path:
    dataset = app.resolve_dataset(development_day)
    index = app.get_index(dataset)
    profile = training_profile(dataset)
    mask = build_walkable_mask(dataset, GENERATED, app.get_map())
    first, last = float(index["first_time"]), float(index["last_time"])
    times = [round(float(value), 3) for value in np.linspace(first + 1800, last - 1800 - horizon, snapshots)]
    candidates = [
        {"relaxation_time": relaxation, "repulsion_strength": strength, "repulsion_range": spread, "facing_aware": True}
        for relaxation in (1.5, 4.0, 10.0)
        for strength in (0.0, 0.2, 0.6)
        for spread in (0.12,)
    ]
    results = []
    for parameters in candidates:
        simulator = SocialForceSimulation(app.MAP_PGM, app.get_map(), profile, mask, seed=42, **parameters)
        snapshots_result = [evaluate_snapshot(simulator, dataset, index, timestamp, horizon, history_seconds) for timestamp in times]
        usable = [result for result in snapshots_result if result["matched"]]
        social_fde = float(np.mean([result["social_fde_m"] for result in usable]))
        baseline_fde = float(np.mean([result["constant_velocity_fde_m"] for result in usable]))
        social_density = float(np.mean([result["social_density_l1"] for result in usable]))
        baseline_density = float(np.mean([result["constant_velocity_density_l1"] for result in usable]))
        # Prioritize trajectory displacement, with a smaller crowd-density term.
        objective = social_fde / baseline_fde + 0.25 * social_density / max(baseline_density, 0.001)
        results.append({"parameters": parameters, "objective": objective, "social_fde_m": social_fde, "baseline_fde_m": baseline_fde, "social_density_l1": social_density, "baseline_density_l1": baseline_density, "matched_snapshots": len(usable)})
    results.sort(key=lambda item: item["objective"])
    payload = {"development_day": dataset.name, "horizon_seconds": horizon, "seed_history_seconds": history_seconds, "snapshots": snapshots, "selection_metric": "social_fde / baseline_fde + 0.25 * social_density / baseline_density", "best": results[0], "candidates": results}
    path = GENERATED / f"calibration_{dataset.stem}.json"
    report = GENERATED / f"calibration_{dataset.stem}.md"
    path.write_text(json.dumps(payload, indent=2))
    report.write_text(
        f"# Social Force parameter calibration — {dataset.stem}\n\n"
        f"Development snapshots: {snapshots}; causal seed history: {history_seconds:g} seconds; horizon: {horizon} seconds.\n\n"
        "## Selected parameters\n\n"
        f"- Relaxation time: {results[0]['parameters']['relaxation_time']} s\n"
        f"- Repulsion strength: {results[0]['parameters']['repulsion_strength']}\n"
        f"- Repulsion range: {results[0]['parameters']['repulsion_range']} m\n"
        f"- Development Social Force FDE: {results[0]['social_fde_m']:.3f} m\n"
        f"- Development constant-velocity FDE: {results[0]['baseline_fde_m']:.3f} m\n"
    )
    print(path)
    print(report)
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tune Social Force parameters on a development ATC day.")
    parser.add_argument("--development-day", default="atc-20121111.csv")
    parser.add_argument("--horizon", type=int, default=10)
    parser.add_argument("--snapshots", type=int, default=3)
    parser.add_argument("--history-seconds", type=float, default=5.0)
    options = parser.parse_args()
    main(options.development_day, options.horizon, options.snapshots, options.history_seconds)
