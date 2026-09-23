# ATC Crowd Safety Explorer

Local proof of concept for replaying the ATC pedestrian tracking dataset on the
provided occupancy map. It is designed as the first phase of an agent-based
crowd-safety project: observe real trajectories, calculate local density and
flow, then use the resulting behaviour distributions to calibrate simulations.

## Run

```bash
python3 -m pip install -r requirements.txt
python3 app.py
```

Open `http://127.0.0.1:5000` in a browser. The first run builds a small,
local time index for `ATC-tracking/atc-20121114.csv`; later starts reuse it.

## Tracking data layout

Keep daily ATC CSVs in `ATC-tracking/`, for example:

```text
ATC-tracking/
  atc-20121024.csv
  atc-20121114.csv
  atc-20121125.csv
```

The dashboard currently replays `atc-20121114.csv`. To create a calibration
profile for another available day, run:

```bash
python3 analyze_atc.py --dataset atc-20121125.csv
```

Multi-day aggregation should combine per-day profiles rather than loading all
CSV files into memory at once.

Create a weighted calibration profile across every CSV currently in
`ATC-tracking/` with:

```bash
python3 build_multiday_calibration.py --build-profiles
```

This first creates or refreshes a separate profile for each day, then writes
`generated/multiday_behavior_profile.json`. Re-run without
`--build-profiles` to aggregate existing derivatives only.

## Validation

Run a leave-one-day-out validation (the test day is excluded from calibration):

```bash
python3 validation_runner.py --test-day atc-20121114.csv --horizon 10 --snapshots 6
```

This writes JSON and Markdown reports in `generated/`, comparing Social Force
forecasts initialized from real ATC snapshots against constant-velocity
extrapolation and later ATC observations.

## Current capabilities

- reads a selected time interval from the 1 GB ATC CSV without loading it all
  into memory;
- converts the ROS occupancy grid from PGM to a browser-ready map image;
- replays tracked pedestrians, density cells, and flow vectors on the map;
- calculates current people count, mean speed, peak local density, and a
  transparent congestion watch indicator.

## Calibration profile

Create the observed-behaviour profile before beginning simulation work:

```bash
python3 analyze_atc.py
```

This streams the source once and writes `generated/behavior_profile.json` and
`generated/trajectory_summary.csv`. The profile includes speed and heading
distributions, filtered trajectory summaries, provisional endpoint zones, and
the most frequent zone-to-zone routes. Endpoint zones are analytical
suggestions only; verify them against the supplied floor-plan slide before
using them as simulated entrances or exits.

## Dataset fields

The source CSV has no header. Its columns are:
`time, person_id, x_mm, y_mm, z_mm, velocity_mm_s, motion_angle_rad, facing_angle_rad`.

The web application converts position and velocity to metres and metres per
second. It uses `localization_grid.yaml` to align trajectory coordinates with
the occupancy map.

## Scope and next steps

The current risk display is an exploratory proxy: it highlights local density
combined with low speed or opposing movement. It does not claim to measure
physical crowd pressure or predict a real stampede. The next implementation
phase is trajectory cleaning, behavioural distribution extraction, and a
calibrated simulation module.
