"""Local ATC trajectory replay and crowd-state API."""

from __future__ import annotations

import csv
import json
import math
import os
import struct
import time
from collections import defaultdict
from pathlib import Path
from typing import Iterator

from flask import Flask, jsonify, request, send_from_directory
from PIL import Image
from social_force import SocialForceSimulation
from walkable_mask import build_walkable_mask

ROOT = Path(__file__).resolve().parent
TRACKING_DIR = ROOT / "ATC-tracking"
DEFAULT_DATASET = "atc-20121114.csv"
MAP_DIR = ROOT / "ATC-map"
MAP_YAML = MAP_DIR / "localization_grid.yaml"
MAP_PGM = MAP_DIR / "localization_grid.pgm"
GENERATED = ROOT / "generated"
MAP_PNG = GENERATED / "atc_occupancy_map.png"
INDEX_EVERY_SECONDS = 60
CALIBRATION_PATH = GENERATED / "calibration_atc-20121111.json"

app = Flask(__name__, static_folder="static", static_url_path="/static")
_indexes: dict[str, dict] = {}
_map: dict | None = None


def available_datasets() -> list[Path]:
    return sorted(TRACKING_DIR.glob("atc-*.csv"))


def resolve_dataset(name: str | None) -> Path:
    dataset = Path(name or DEFAULT_DATASET).name
    candidate = TRACKING_DIR / dataset
    if not candidate.is_file() or not candidate.name.startswith("atc-"):
        raise ValueError("Unknown tracking dataset.")
    return candidate


def index_path_for(dataset: Path) -> Path:
    return GENERATED / f"time_index_{dataset.stem}.json"


def profile_path_for(dataset: Path) -> Path:
    if dataset.name == DEFAULT_DATASET:
        return GENERATED / "behavior_profile.json"
    return GENERATED / f"behavior_profile_{dataset.stem}.json"


def calibrated_social_force_parameters() -> dict:
    if not CALIBRATION_PATH.exists():
        return {}
    try:
        return json.loads(CALIBRATION_PATH.read_text())["best"]["parameters"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}


def read_map_metadata() -> dict:
    """Parse the tiny ROS map YAML without adding a YAML dependency."""
    values: dict[str, object] = {}
    for raw in MAP_YAML.read_text().splitlines():
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        value = value.strip()
        if key == "origin":
            values[key] = [float(item.strip()) for item in value.strip("[]").split(",")]
        elif key == "resolution":
            values[key] = float(value)
    with Image.open(MAP_PGM) as image:
        width, height = image.size
    return {
        "resolution": values["resolution"],
        "origin": values["origin"],
        "width": width,
        "height": height,
    }


def ensure_map_png() -> None:
    GENERATED.mkdir(exist_ok=True)
    if MAP_PNG.exists() and MAP_PNG.stat().st_mtime >= MAP_PGM.stat().st_mtime:
        return
    with Image.open(MAP_PGM) as image:
        image.convert("L").save(MAP_PNG, "PNG", optimize=True)


def cache_is_valid(candidate: dict, dataset: Path) -> bool:
    stat = dataset.stat()
    return (
        candidate.get("source") == str(dataset.relative_to(ROOT))
        and candidate.get("csv_size") == stat.st_size
        and candidate.get("csv_mtime_ns") == stat.st_mtime_ns
    )


def build_time_index(dataset: Path) -> dict:
    """Create byte offsets at one-minute intervals for fast window reads."""
    if not dataset.exists():
        raise FileNotFoundError(f"Missing ATC data: {dataset}")

    started = time.monotonic()
    checkpoints: list[list[float | int]] = []
    first_time: float | None = None
    last_time: float | None = None
    next_checkpoint: float | None = None
    rows = 0

    with dataset.open("rb") as handle:
        while True:
            offset = handle.tell()
            line = handle.readline()
            if not line:
                break
            try:
                stamp = float(line.split(b",", 1)[0])
            except (ValueError, IndexError):
                continue
            if first_time is None:
                first_time = stamp
                next_checkpoint = math.floor(stamp / INDEX_EVERY_SECONDS) * INDEX_EVERY_SECONDS
            if stamp >= next_checkpoint:  # type: ignore[operator]
                checkpoints.append([round(stamp, 3), offset])
                next_checkpoint += INDEX_EVERY_SECONDS  # type: ignore[operator]
            last_time = stamp
            rows += 1

    stat = dataset.stat()
    payload = {
        "source": str(dataset.relative_to(ROOT)),
        "csv_size": stat.st_size,
        "csv_mtime_ns": stat.st_mtime_ns,
        "created_seconds": round(time.monotonic() - started, 2),
        "first_time": first_time,
        "last_time": last_time,
        "row_count": rows,
        "checkpoints": checkpoints,
    }
    index_path_for(dataset).write_text(json.dumps(payload))
    return payload


def get_index(dataset: Path) -> dict:
    key = dataset.name
    if key in _indexes:
        return _indexes[key]
    GENERATED.mkdir(exist_ok=True)
    index_path = index_path_for(dataset)
    if index_path.exists():
        candidate = json.loads(index_path.read_text())
        if cache_is_valid(candidate, dataset):
            _indexes[key] = candidate
            return candidate
    _indexes[key] = build_time_index(dataset)
    return _indexes[key]


def get_map() -> dict:
    global _map
    if _map is None:
        _map = read_map_metadata()
    return _map


def nearest_offset(target: float, checkpoints: list[list[float | int]]) -> int:
    low, high = 0, len(checkpoints) - 1
    answer = 0
    while low <= high:
        middle = (low + high) // 2
        if float(checkpoints[middle][0]) <= target:
            answer = middle
            low = middle + 1
        else:
            high = middle - 1
    return int(checkpoints[answer][1])


def stream_rows(dataset: Path, start: float, end: float, index: dict) -> Iterator[list[str]]:
    offset = nearest_offset(start, index["checkpoints"])
    with dataset.open("rb") as raw:
        raw.seek(offset)
        text = (line.decode("utf-8", errors="ignore") for line in raw)
        for row in csv.reader(text):
            if len(row) != 8:
                continue
            try:
                stamp = float(row[0])
            except ValueError:
                continue
            if stamp < start:
                continue
            if stamp > end:
                return
            yield row


def frame_key(stamp: float, sample_seconds: float) -> int:
    return int(math.floor(stamp / sample_seconds) * sample_seconds * 1000)


def observed_snapshot(dataset: Path, index: dict, timestamp: float) -> list[dict]:
    """Return the latest tracked state for every person in a short time slice."""
    states: dict[int, dict] = {}
    for row in stream_rows(dataset, timestamp, timestamp + 0.25, index):
        person_id = int(row[1])
        states[person_id] = {
            "id": person_id,
            "x": float(row[2]) / 1000,
            "y": float(row[3]) / 1000,
            "speed": float(row[5]) / 1000,
            "heading": float(row[6]),
        }
    return list(states.values())


@app.get("/")
def home():
    return send_from_directory(ROOT / "static", "index.html")


@app.get("/api/metadata")
def metadata():
    try:
        dataset = resolve_dataset(request.args.get("dataset"))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    index = get_index(dataset)
    map_info = get_map()
    return jsonify(
        {
            "source": dataset.name,
            "first_time": index["first_time"],
            "last_time": index["last_time"],
            "duration_seconds": round(index["last_time"] - index["first_time"], 3),
            "row_count": index["row_count"],
            "checkpoint_count": len(index["checkpoints"]),
            "index_build_seconds": index.get("created_seconds"),
            "map": map_info,
        }
    )


@app.get("/api/datasets")
def datasets():
    items = []
    for dataset in available_datasets():
        items.append({"id": dataset.name, "label": dataset.stem.removeprefix("atc-"), "size_bytes": dataset.stat().st_size})
    return jsonify({"default": DEFAULT_DATASET, "datasets": items})


@app.get("/api/map")
def map_image():
    ensure_map_png()
    return send_from_directory(GENERATED, MAP_PNG.name, mimetype="image/png")


@app.get("/api/profile")
def behavior_profile():
    try:
        dataset = resolve_dataset(request.args.get("dataset"))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    profile_path = profile_path_for(dataset)
    if not profile_path.exists():
        return jsonify({"error": "Behaviour profile has not been generated. Run: python3 analyze_atc.py"}), 404
    return jsonify(json.loads(profile_path.read_text()))


@app.get("/api/window")
def window():
    try:
        dataset = resolve_dataset(request.args.get("dataset"))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    index = get_index(dataset)
    earliest, latest = float(index["first_time"]), float(index["last_time"])
    try:
        start = float(request.args.get("start", earliest))
        duration = min(max(float(request.args.get("duration", 300)), 10), 900)
        sample = min(max(float(request.args.get("sample", 1)), 0.5), 10)
    except ValueError:
        return jsonify({"error": "start, duration, and sample must be numeric."}), 400
    start = min(max(start, earliest), latest - 1)
    end = min(start + duration, latest)

    frames: dict[int, dict[int, list[float | int]]] = defaultdict(dict)
    raw_points = 0
    for row in stream_rows(dataset, start, end, index):
        stamp = float(row[0])
        bucket = frame_key(stamp, sample)
        person_id = int(row[1])
        # Store the most recent position per person in each sampling interval.
        frames[bucket][person_id] = [
            person_id,
            round(float(row[2]) / 1000, 3),
            round(float(row[3]) / 1000, 3),
            round(float(row[5]) / 1000, 3),
            round(float(row[6]), 4),
        ]
        raw_points += 1

    output_frames = [
        {"t": bucket / 1000, "people": list(people.values())}
        for bucket, people in sorted(frames.items())
    ]
    return jsonify(
        {
            "start": start,
            "end": end,
            "sample_seconds": sample,
            "raw_points_read": raw_points,
            "frames": output_frames,
        }
    )


@app.get("/api/simulate")
def simulate():
    try:
        dataset = resolve_dataset(request.args.get("dataset"))
        agents = min(max(int(request.args.get("agents", 120)), 20), 300)
        duration = min(max(int(request.args.get("duration", 120)), 20), 180)
        scenario = request.args.get("scenario", "normal")
        initialization = request.args.get("initialization", "observed")
        if scenario not in {"normal", "surge", "counterflow", "restricted_exit"}:
            raise ValueError("Unknown simulation scenario.")
        if initialization not in {"observed", "synthetic"}:
            raise ValueError("Unknown simulation initialization.")
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    profile_path = profile_path_for(dataset)
    if not profile_path.exists():
        return jsonify({"error": "This day needs a calibration profile before simulation."}), 409
    profile = json.loads(profile_path.read_text())
    walkable_mask = build_walkable_mask(dataset, GENERATED, get_map())
    parameters = calibrated_social_force_parameters()
    simulator = SocialForceSimulation(MAP_PGM, get_map(), profile, walkable_mask, seed=42, **parameters)
    index = get_index(dataset)
    earliest, latest = float(index["first_time"]), float(index["last_time"])
    start = min(max(float(request.args.get("start", earliest)), earliest), latest - 1)
    seeds = observed_snapshot(dataset, index, start) if initialization == "observed" else None
    if initialization == "observed" and not seeds:
        return jsonify({"error": "No tracked people were found at the selected time."}), 422
    frames, summary = simulator.run(agents, duration, scenario, seeds)
    summary["seed_time"] = start if seeds is not None else None
    summary["parameters"] = parameters
    return jsonify({"frames": frames, "sample_seconds": 1, "simulation": summary})


if __name__ == "__main__":
    # Build/reuse the index before the first request so the UI can report readiness.
    get_index(resolve_dataset(DEFAULT_DATASET))
    ensure_map_png()
    app.run(host="127.0.0.1", port=5000, debug=False)
