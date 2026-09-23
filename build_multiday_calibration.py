"""Build a weighted, auditable calibration profile from multiple ATC days.

Run with --build-profiles to stream each daily CSV independently first. The
second stage only reads compact JSON/CSV derivatives, so it never attempts to
place the full multi-day dataset in memory.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TRACKING = ROOT / "ATC-tracking"
GENERATED = ROOT / "generated"
ANALYZER = ROOT / "analyze_atc.py"
MULTIDAY_PROFILE = GENERATED / "multiday_behavior_profile.json"
ZONE_CELL_METRES = 4.0
ZONE_JOIN_METRES = 7.0
MAX_ZONES = 8


def derivative_paths(dataset: Path) -> tuple[Path, Path]:
    if dataset.name == "atc-20121114.csv":
        return GENERATED / "behavior_profile.json", GENERATED / "trajectory_summary.csv"
    return (
        GENERATED / f"behavior_profile_{dataset.stem}.json",
        GENERATED / f"trajectory_summary_{dataset.stem}.csv",
    )


def endpoint_zones(endpoints: list[tuple[float, float]]) -> list[dict]:
    cells: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
    for x, y in endpoints:
        cells[(math.floor(x / ZONE_CELL_METRES), math.floor(y / ZONE_CELL_METRES))].append((x, y))
    zones: list[dict] = []
    for _, points in sorted(cells.items(), key=lambda item: len(item[1]), reverse=True):
        x, y = (sum(point[i] for point in points) / len(points) for i in (0, 1))
        if any(math.dist((x, y), (zone["x"], zone["y"])) < ZONE_JOIN_METRES for zone in zones):
            continue
        zones.append({"id": f"Z{len(zones) + 1}", "x": round(x, 2), "y": round(y, 2), "support": len(points)})
        if len(zones) == MAX_ZONES:
            break
    return zones


def closest_zone(point: tuple[float, float], zones: list[dict]) -> str | None:
    if not zones:
        return None
    distance, index = min((math.dist(point, (zone["x"], zone["y"])), index) for index, zone in enumerate(zones))
    return zones[index]["id"] if distance <= ZONE_JOIN_METRES else None


def main(build_profiles: bool) -> None:
    datasets = sorted(TRACKING.glob("atc-*.csv"))
    if not datasets:
        raise SystemExit(f"No ATC CSV files found in {TRACKING}")
    GENERATED.mkdir(exist_ok=True)

    if build_profiles:
        for position, dataset in enumerate(datasets, start=1):
            print(f"[{position}/{len(datasets)}] Profiling {dataset.name}", flush=True)
            subprocess.run([sys.executable, str(ANALYZER), "--dataset", dataset.name], cwd=ROOT, check=True)

    profiles: list[dict] = []
    endpoints: list[tuple[float, float, float, float]] = []
    for dataset in datasets:
        profile_path, trajectory_path = derivative_paths(dataset)
        if not profile_path.exists() or not trajectory_path.exists():
            raise SystemExit(f"Missing derivatives for {dataset.name}. Run with --build-profiles.")
        profile = json.loads(profile_path.read_text())
        profiles.append(profile)
        with trajectory_path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                endpoints.append((float(row["start_x_m"]), float(row["start_y_m"]), float(row["end_x_m"]), float(row["end_y_m"])))

    speed_count = sum(profile["speed_m_s"].get("sample_count", 0) for profile in profiles)
    if not speed_count:
        raise SystemExit("Profiles need refreshing with the current analyzer. Run with --build-profiles.")
    speed_sum = sum(profile["speed_m_s"]["sum"] for profile in profiles)
    speed_sum_squares = sum(profile["speed_m_s"]["sum_squares"] for profile in profiles)
    speed_histogram: Counter[str] = Counter()
    heading_histogram: Counter[str] = Counter()
    for profile in profiles:
        speed_histogram.update(profile["speed_m_s"]["histogram"])
        heading_histogram.update(profile["heading_degrees"]["histogram"])
    zones = endpoint_zones([(x1, y1) for x1, y1, _, _ in endpoints] + [(x2, y2) for _, _, x2, y2 in endpoints])
    routes: Counter[str] = Counter()
    for x1, y1, x2, y2 in endpoints:
        origin, destination = closest_zone((x1, y1), zones), closest_zone((x2, y2), zones)
        if origin and destination and origin != destination:
            routes[f"{origin} → {destination}"] += 1
    mean = speed_sum / speed_count
    variance = max(0.0, speed_sum_squares / speed_count - mean * mean)
    payload = {
        "source_days": [profile["source"] for profile in profiles],
        "day_count": len(profiles),
        "source_rows": sum(profile["source_rows"] for profile in profiles),
        "people_observed": sum(profile["people_observed"] for profile in profiles),
        "valid_trajectories": sum(profile["valid_trajectories"] for profile in profiles),
        "speed_m_s": {"sample_count": speed_count, "mean": round(mean, 3), "standard_deviation": round(math.sqrt(variance), 3), "histogram": dict(sorted(speed_histogram.items()))},
        "heading_degrees": {"histogram": {key: heading_histogram[key] for key in sorted(heading_histogram, key=lambda value: float(value[:-1]))}},
        "provisional_zones": zones,
        "top_routes": [{"route": route, "trajectories": count} for route, count in routes.most_common(16)],
        "per_day": [{"source": profile["source"], "source_rows": profile["source_rows"], "valid_trajectories": profile["valid_trajectories"], "mean_speed_m_s": profile["speed_m_s"]["mean"]} for profile in profiles],
        "notes": ["Speed and heading distributions are weighted by tracking observations across days.", "Zones are re-inferred from all cleaned trajectory endpoints and remain provisional until confirmed against the floor plan.", "Days are processed independently before aggregation; no multi-day raw CSV load is performed."],
    }
    MULTIDAY_PROFILE.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {MULTIDAY_PROFILE.name}: {len(profiles)} days, {payload['valid_trajectories']:,} valid trajectories", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Aggregate ATC daily calibration profiles.")
    parser.add_argument("--build-profiles", action="store_true", help="Regenerate individual day profiles before aggregation.")
    arguments = parser.parse_args()
    main(arguments.build_profiles)
