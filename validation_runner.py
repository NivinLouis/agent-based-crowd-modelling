"""Validate Social Force forecasts against held-out ATC observations.

The runner uses a leave-one-day-out split: calibration information is built
from every day except the requested test day. At each test snapshot it compares
the Social Force forecast with a constant-velocity baseline against later ATC
tracks with the same person IDs.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np

import app
from build_multiday_calibration import closest_zone, derivative_paths, endpoint_zones
from social_force import SocialForceSimulation
from walkable_mask import build_walkable_mask

ROOT = Path(__file__).resolve().parent
GENERATED = ROOT / "generated"


def training_profile(test_dataset: Path) -> dict:
    """Aggregate calibration artefacts from every day except the held-out day."""
    profiles, endpoints = [], []
    for dataset in app.available_datasets():
        if dataset == test_dataset:
            continue
        profile_path, trajectory_path = derivative_paths(dataset)
        if not profile_path.exists() or not trajectory_path.exists():
            raise FileNotFoundError(f"Missing calibration derivatives for {dataset.name}")
        profile = json.loads(profile_path.read_text())
        profiles.append(profile)
        import csv
        with trajectory_path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                endpoints.append((float(row["start_x_m"]), float(row["start_y_m"]), float(row["end_x_m"]), float(row["end_y_m"])))
    speed_count = sum(profile["speed_m_s"]["sample_count"] for profile in profiles)
    total_speed = sum(profile["speed_m_s"]["sum"] for profile in profiles)
    total_squared = sum(profile["speed_m_s"]["sum_squares"] for profile in profiles)
    mean = total_speed / speed_count
    variance = max(0.0, total_squared / speed_count - mean * mean)
    zones = endpoint_zones([(x1, y1) for x1, y1, _, _ in endpoints] + [(x2, y2) for _, _, x2, y2 in endpoints])
    routes: Counter[str] = Counter()
    for x1, y1, x2, y2 in endpoints:
        origin, destination = closest_zone((x1, y1), zones), closest_zone((x2, y2), zones)
        if origin and destination and origin != destination:
            routes[f"{origin} → {destination}"] += 1
    return {
        "source_days": [profile["source"] for profile in profiles],
        "speed_m_s": {"mean": mean, "standard_deviation": math.sqrt(variance)},
        "provisional_zones": zones,
        "top_routes": [{"route": route, "trajectories": count} for route, count in routes.most_common(16)],
    }


def snapshot_by_id(dataset: Path, index: dict, timestamp: float) -> dict[int, dict]:
    return {state["id"]: state for state in app.observed_snapshot(dataset, index, timestamp)}


def density_l1(predicted: dict[int, tuple[float, float]], actual: dict[int, dict], cell_m: float = 2.0) -> float:
    pred_cells, actual_cells = Counter(), Counter()
    for x, y in predicted.values():
        pred_cells[(math.floor(x / cell_m), math.floor(y / cell_m))] += 1
    for state in actual.values():
        actual_cells[(math.floor(state["x"] / cell_m), math.floor(state["y"] / cell_m))] += 1
    keys = set(pred_cells) | set(actual_cells)
    return sum(abs(pred_cells[key] - actual_cells[key]) for key in keys) / max(1, len(actual))


def evaluate_snapshot(simulator: SocialForceSimulation, dataset: Path, index: dict, timestamp: float, horizon: int) -> dict:
    initial = snapshot_by_id(dataset, index, timestamp)
    if not initial:
        return {"time": timestamp, "agents": 0, "matched": 0}
    frames, _ = simulator.run(len(initial), horizon, "normal", list(initial.values()))
    simulated = {person[0]: (person[1], person[2], person[3]) for person in frames[-1]["people"]}
    actual = snapshot_by_id(dataset, index, timestamp + horizon)
    shared = sorted(set(simulated) & set(actual) & set(initial))
    if not shared:
        return {"time": timestamp, "agents": len(initial), "matched": 0}
    social_errors, baseline_errors, speed_errors = [], [], []
    social_xy, baseline_xy, actual_shared = {}, {}, {}
    for identifier in shared:
        seed, truth = initial[identifier], actual[identifier]
        sx, sy, simulated_speed = simulated[identifier]
        bx = seed["x"] + seed["speed"] * math.cos(seed["heading"]) * horizon
        by = seed["y"] + seed["speed"] * math.sin(seed["heading"]) * horizon
        social_errors.append(math.dist((sx, sy), (truth["x"], truth["y"])))
        baseline_errors.append(math.dist((bx, by), (truth["x"], truth["y"])))
        speed_errors.append(abs(simulated_speed - truth["speed"]))
        social_xy[identifier], baseline_xy[identifier], actual_shared[identifier] = (sx, sy), (bx, by), truth
    return {
        "time": timestamp,
        "agents": len(initial),
        "matched": len(shared),
        "social_fde_m": float(np.mean(social_errors)),
        "constant_velocity_fde_m": float(np.mean(baseline_errors)),
        "social_speed_mae_m_s": float(np.mean(speed_errors)),
        "social_density_l1": density_l1(social_xy, actual_shared),
        "constant_velocity_density_l1": density_l1(baseline_xy, actual_shared),
    }


def main(test_name: str, horizon: int, snapshots: int, parameters: dict | None = None) -> tuple[Path, Path]:
    dataset = app.resolve_dataset(test_name)
    index = app.get_index(dataset)
    profile = training_profile(dataset)
    walkable_mask = build_walkable_mask(dataset, GENERATED, app.get_map())
    parameters = parameters or {}
    simulator = SocialForceSimulation(app.MAP_PGM, app.get_map(), profile, walkable_mask, seed=42, **parameters)
    first, last = float(index["first_time"]), float(index["last_time"])
    # Keep snapshots away from the day edges so all forecast horizons exist.
    times = np.linspace(first + 1800, last - 1800 - horizon, snapshots)
    results = [evaluate_snapshot(simulator, dataset, index, round(float(timestamp), 3), horizon) for timestamp in times]
    usable = [result for result in results if result["matched"]]
    if not usable:
        raise RuntimeError("No matched ATC trajectories were available for validation.")
    aggregate = {key: float(np.mean([result[key] for result in usable])) for key in ("social_fde_m", "constant_velocity_fde_m", "social_speed_mae_m_s", "social_density_l1", "constant_velocity_density_l1")}
    aggregate["fde_improvement_pct"] = round(100 * (aggregate["constant_velocity_fde_m"] - aggregate["social_fde_m"]) / aggregate["constant_velocity_fde_m"], 2)
    payload = {
        "method": "leave-one-day-out, observed-snapshot forecast",
        "test_day": dataset.name,
        "training_days": profile["source_days"],
        "horizon_seconds": horizon,
        "social_force_parameters": parameters,
        "snapshots_requested": snapshots,
        "snapshots_with_matches": len(usable),
        "aggregate": aggregate,
        "snapshots": results,
        "limitations": ["Destinations are inferred from held-out-day-excluded route and heading patterns; later ATC positions are only used for scoring.", "This validates normal movement prediction, not stampede or emergency-event prediction.", "The constant-velocity baseline intentionally has no wall or pedestrian-interaction model."],
    }
    variant = "facing" if parameters.get("facing_aware", True) else "nofacing"
    json_path = GENERATED / f"validation_{dataset.stem}_h{horizon}_{variant}.json"
    markdown_path = GENERATED / f"validation_{dataset.stem}_h{horizon}_{variant}.md"
    json_path.write_text(json.dumps(payload, indent=2))
    markdown_path.write_text(
        f"# Social Force validation report — {dataset.stem}\n\n"
        f"- Method: leave-one-day-out observed-snapshot forecast\n- Held-out test day: `{dataset.name}`\n- Training days: {len(profile['source_days'])}\n- Forecast horizon: {horizon} seconds\n- Usable snapshots: {len(usable)} of {snapshots}\n\n"
        "## Aggregate results\n\n"
        f"| Metric | Social Force | Constant velocity |\n|---|---:|---:|\n"
        f"| Final displacement error (m) | {aggregate['social_fde_m']:.3f} | {aggregate['constant_velocity_fde_m']:.3f} |\n"
        f"| Density L1 error (people / observed person) | {aggregate['social_density_l1']:.3f} | {aggregate['constant_velocity_density_l1']:.3f} |\n\n"
        f"Social Force speed MAE: **{aggregate['social_speed_mae_m_s']:.3f} m/s**.\n\n"
        f"FDE change relative to constant velocity: **{aggregate['fde_improvement_pct']:.2f}%**.\n\n"
        "## Interpretation\n\n"
        "This is a normal-operations forecast validation. It does not establish real-world stampede prediction performance. Individual trajectory accuracy is limited by unobserved destination intent; density and flow measures are therefore equally important.\n"
    )
    return json_path, markdown_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate Social Force forecasts against a held-out ATC day.")
    parser.add_argument("--test-day", default="atc-20121114.csv")
    parser.add_argument("--horizon", type=int, default=10)
    parser.add_argument("--snapshots", type=int, default=6)
    parser.add_argument("--relaxation-time", type=float, default=0.5)
    parser.add_argument("--repulsion-strength", type=float, default=2.2)
    parser.add_argument("--repulsion-range", type=float, default=0.28)
    parser.add_argument("--no-facing", action="store_true", help="Disable facing-angle-based interaction weighting.")
    options = parser.parse_args()
    parameters = {"relaxation_time": options.relaxation_time, "repulsion_strength": options.repulsion_strength, "repulsion_range": options.repulsion_range, "facing_aware": not options.no_facing}
    json_report, markdown_report = main(options.test_day, options.horizon, options.snapshots, parameters)
    print(json_report)
    print(markdown_report)
