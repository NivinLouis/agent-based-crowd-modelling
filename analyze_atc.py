"""Create a compact behavioural calibration profile from the ATC tracking CSV.

The original file is chronologically ordered, but people are interleaved. This
script therefore retains only one lightweight state record per person while it
streams the source once. The generated JSON is consumed by the dashboard and
is also suitable as input parameters for the simulator phase.
"""

from __future__ import annotations

import csv
import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TRACKING_DIR = ROOT / "ATC-tracking"
SOURCE = TRACKING_DIR / "atc-20121114.csv"
OUTPUT_DIR = ROOT / "generated"
PROFILE = OUTPUT_DIR / "behavior_profile.json"
TRAJECTORIES = OUTPUT_DIR / "trajectory_summary.csv"

SPEED_BIN = 0.1
HEADING_BINS = 16
MIN_TRAJECTORY_SECONDS = 30
MIN_TRAJECTORY_SAMPLES = 20
ZONE_CELL_METRES = 4.0
ZONE_JOIN_METRES = 7.0
MAX_ZONES = 6


def speed_bin(speed: float) -> str:
    lower = math.floor(speed / SPEED_BIN) * SPEED_BIN
    return f"{lower:.1f}-{lower + SPEED_BIN:.1f}"


def heading_bin(angle: float) -> str:
    degrees = (math.degrees(angle) + 360) % 360
    index = int((degrees + 180 / HEADING_BINS) // (360 / HEADING_BINS)) % HEADING_BINS
    centre = index * (360 / HEADING_BINS)
    return f"{centre:.0f}°"


def weighted_zone_candidates(endpoints: list[tuple[float, float]]) -> list[dict]:
    """Merge popular 4 m endpoint cells into interpretable provisional zones."""
    cells: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
    for x, y in endpoints:
        cells[(math.floor(x / ZONE_CELL_METRES), math.floor(y / ZONE_CELL_METRES))].append((x, y))
    ordered = sorted(cells.items(), key=lambda item: len(item[1]), reverse=True)
    selected: list[dict] = []
    for (cell_x, cell_y), points in ordered:
        x = sum(point[0] for point in points) / len(points)
        y = sum(point[1] for point in points) / len(points)
        if any(math.dist((x, y), (zone["x"], zone["y"])) < ZONE_JOIN_METRES for zone in selected):
            continue
        selected.append({"x": round(x, 2), "y": round(y, 2), "support": len(points)})
        if len(selected) == MAX_ZONES:
            break
    return selected


def closest_zone(point: tuple[float, float], zones: list[dict]) -> str | None:
    if not zones:
        return None
    distance, index = min((math.dist(point, (zone["x"], zone["y"])), index) for index, zone in enumerate(zones))
    return f"Z{index + 1}" if distance <= ZONE_JOIN_METRES else None


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"Missing source file: {SOURCE}")
    OUTPUT_DIR.mkdir(exist_ok=True)
    # Preserve the dashboard's reference-day filenames while keeping profiles
    # for every additional day as separate, non-destructive artifacts.
    if SOURCE.name == "atc-20121114.csv":
        profile_path, trajectories_path = PROFILE, TRAJECTORIES
    else:
        profile_path = OUTPUT_DIR / f"behavior_profile_{SOURCE.stem}.json"
        trajectories_path = OUTPUT_DIR / f"trajectory_summary_{SOURCE.stem}.csv"
    people: dict[int, dict] = {}
    speeds: Counter[str] = Counter()
    headings: Counter[str] = Counter()
    total_speed = total_speed_sq = speed_count = 0
    total_rows = 0

    with SOURCE.open(newline="") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if len(row) != 8:
                continue
            try:
                stamp, person_id = float(row[0]), int(row[1])
                x, y, speed, heading = float(row[2]) / 1000, float(row[3]) / 1000, float(row[5]) / 1000, float(row[6])
            except ValueError:
                continue
            total_rows += 1
            if 0 <= speed <= 4.0:
                speeds[speed_bin(speed)] += 1
                total_speed += speed
                total_speed_sq += speed * speed
                speed_count += 1
            headings[heading_bin(heading)] += 1
            current = people.get(person_id)
            if current is None:
                people[person_id] = {"id": person_id, "first_t": stamp, "last_t": stamp, "first_x": x, "first_y": y, "last_x": x, "last_y": y, "samples": 1, "path": 0.0}
                continue
            delta_t = stamp - current["last_t"]
            step = math.dist((x, y), (current["last_x"], current["last_y"]))
            # Ignore discontinuities caused by a person ID being re-acquired.
            if 0.01 <= delta_t <= 1.0 and step <= 4.0:
                current["path"] += step
            current.update({"last_t": stamp, "last_x": x, "last_y": y})
            current["samples"] += 1

    valid = [record for record in people.values() if record["samples"] >= MIN_TRAJECTORY_SAMPLES and record["last_t"] - record["first_t"] >= MIN_TRAJECTORY_SECONDS]
    starts = [(record["first_x"], record["first_y"]) for record in valid]
    ends = [(record["last_x"], record["last_y"]) for record in valid]
    zones = weighted_zone_candidates(starts + ends)
    routes: Counter[str] = Counter()
    for record in valid:
        origin = closest_zone((record["first_x"], record["first_y"]), zones)
        destination = closest_zone((record["last_x"], record["last_y"]), zones)
        if origin and destination and origin != destination:
            routes[f"{origin} → {destination}"] += 1

    with trajectories_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["person_id", "start_time", "end_time", "duration_s", "start_x_m", "start_y_m", "end_x_m", "end_y_m", "path_length_m", "displacement_m", "samples"])
        for record in valid:
            duration = record["last_t"] - record["first_t"]
            displacement = math.dist((record["first_x"], record["first_y"]), (record["last_x"], record["last_y"]))
            writer.writerow([record["id"], f"{record['first_t']:.3f}", f"{record['last_t']:.3f}", f"{duration:.2f}", f"{record['first_x']:.3f}", f"{record['first_y']:.3f}", f"{record['last_x']:.3f}", f"{record['last_y']:.3f}", f"{record['path']:.3f}", f"{displacement:.3f}", record["samples"]])

    mean_speed = total_speed / speed_count
    variance = max(0.0, total_speed_sq / speed_count - mean_speed * mean_speed)
    profile = {
        "source": SOURCE.name,
        "source_rows": total_rows,
        "people_observed": len(people),
        "valid_trajectories": len(valid),
        "trajectory_filter": {"minimum_seconds": MIN_TRAJECTORY_SECONDS, "minimum_samples": MIN_TRAJECTORY_SAMPLES},
        "speed_m_s": {"sample_count": speed_count, "sum": round(total_speed, 6), "sum_squares": round(total_speed_sq, 6), "mean": round(mean_speed, 3), "standard_deviation": round(math.sqrt(variance), 3), "histogram": dict(sorted(speeds.items()))},
        "heading_degrees": {"histogram": {key: headings[key] for key in sorted(headings, key=lambda value: float(value[:-1]))}},
        "provisional_zones": [{"id": f"Z{index + 1}", **zone} for index, zone in enumerate(zones)],
        "top_routes": [{"route": route, "trajectories": count} for route, count in routes.most_common(12)],
        "notes": ["Zones are inferred from popular trajectory endpoint cells, not confirmed door locations.", "Validate or relabel zones against the supplied entry/exit floor-plan slide before using them in safety scenarios."],
    }
    profile_path.write_text(json.dumps(profile, indent=2))
    print(f"Wrote {profile_path.name}: {len(valid):,} usable trajectories from {total_rows:,} observations")
    print(f"Wrote {trajectories_path.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create a behavioural calibration profile for one ATC tracking day.")
    parser.add_argument("--dataset", default=SOURCE.name, help="CSV filename inside ATC-tracking")
    args = parser.parse_args()
    requested = TRACKING_DIR / Path(args.dataset).name
    if not requested.exists():
        available = ", ".join(path.name for path in sorted(TRACKING_DIR.glob("atc-*.csv")))
        raise SystemExit(f"Dataset not found: {requested.name}. Available: {available}")
    SOURCE = requested
    main()
