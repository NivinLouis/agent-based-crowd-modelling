# ATC Crowd Safety Explorer

An interactive, research-oriented decision-support prototype for studying crowd movement and testing **what-if safety scenarios**. The application replays real pedestrian tracks from the ATC Shopping Center dataset, calibrates an Agent-Based Model (ABM) from multi-day observations, and runs map-constrained Social Force simulations.

> **Important:** this is a normal-flow modelling and scenario-comparison tool. It is not a certified evacuation model, does not predict real stampedes, and must not be used as the sole basis for operational safety decisions.

## What it does

- Replays ATC trajectories for a selected **dataset day, start time, time window, and sampling interval**.
- Shows the observed crowd over the ATC occupancy map with local-density cells, mean-flow vectors, people count, speed, and a congestion-watch indicator.
- Seeds a simulation directly from the selected ATC snapshot: each observed person becomes an ABM agent at the observed location with trajectory-derived speed, heading, and facing angle.
- Learns speed, endpoint-zone, and route distributions from multiple available ATC days without loading every CSV into memory at once.
- Infers short-term route intent using a strictly causal five-second trajectory history.
- Runs normal-flow, entrance-surge, counter-flow, and restricted-exit scenarios; synthetic zone spawning supports capacity experiments up to 1,000 agents.
- Constrains agents to the walkable area derived from the ATC occupancy grid so they do not travel through walls or outside the represented facility.
- Produces reproducible calibration and held-out validation reports against later ATC observations.

## System architecture

```text
ATC 3D range sensors
        │
        ▼
Daily person-tracking CSV files ──► Numerical trajectory adapter
                                      │
                   ATC occupancy map ├──► Walkable-area mask
                                      │
                                      ▼
                   Multi-day behaviour and route calibration
                                      │
                                      ▼
          History / heading / facing-aware Social Force ABM
                                      │
                                      ▼
     Flask dashboard: replay • scenario simulation • safety analytics
                                      │
                                      ▼
            Calibration and held-out validation reports
```

### ATC state representation

ATC is already a sensor-derived tracking dataset rather than raw camera video. Each headerless row is converted to the following numerical state:

```text
s(i, t) = [time, person_id, x_m, y_m, z_m,
           speed_m_s, motion_angle_rad, facing_angle_rad]
```

The source coordinate and speed values are transformed from millimetres and millimetres/second to metres and metres/second. This state interface is intentionally compatible with a future CV pipeline that performs detection, multi-object tracking, floor-plane calibration, and heading/facing estimation.

## Technology

| Layer | Tools / approach |
| --- | --- |
| Application | Python, Flask, JavaScript, HTML5/CSS3 |
| Data processing | NumPy, streaming CSV access, JSON derivatives |
| Simulation | Map-constrained Social Force ABM, local-neighbour interactions |
| Spatial model | ATC ROS occupancy grid (`.pgm` + `.yaml`) and derived walkable mask |
| Analytics | Local density, mean flow, congestion-watch proxy, route distributions |
| Evaluation | Development-day calibration and leave-one-day-out trajectory forecasting |

## Repository layout

```text
.
├── app.py                         # Flask API and ATC data access
├── social_force.py                # Social Force simulation engine
├── walkable_mask.py               # Occupancy-map walkability extraction
├── analyze_atc.py                 # Per-day behaviour profiling
├── build_multiday_calibration.py  # Streaming multi-day profile aggregation
├── calibrate_social_force.py      # Development-day parameter selection
├── validation_runner.py           # Held-out forecast validation
├── create_validation_report.py    # Presentable validation report generator
├── static/                        # Dashboard UI
├── ATC-map/                       # Required occupancy grid and metadata
├── ATC-tracking/                  # User-supplied daily ATC CSVs (ignored by Git)
└── generated/                     # Local profiles, indexes, masks, and reports (ignored)
```

## Quick start

### 1. Requirements

- Python 3.10 or newer
- The ATC occupancy-map files in `ATC-map/`
- One or more uncompressed ATC daily CSV files in `ATC-tracking/`

Install the small Python dependency set:

```bash
python3 -m pip install -r requirements.txt
```

### 2. Add the ATC data

The raw tracking data is intentionally not committed to this repository. Place the uncompressed files in this layout:

```text
ATC-tracking/
├── atc-20121024.csv
├── atc-20121111.csv
├── atc-20121114.csv
└── ...
```

The source has no header and uses this column order:

```text
time, person_id, x_mm, y_mm, z_mm,
velocity_mm_s, motion_angle_rad, facing_angle_rad
```

Download data and review its research-use terms from the [ATC pedestrian tracking dataset page](https://dil.atr.jp/crest2010_HRI/ATC_dataset/). If you use the dataset in research, cite the dataset's requested reference paper.

### 3. Start the dashboard

```bash
python3 app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000). On its first use for a day, the application creates a small local time index in `generated/`; later reads reuse it.

## Using the dashboard

### Observed replay

1. Choose a **Tracking day**.
2. Select **Observed replay**, a start time, window, and sampling interval.
3. Select **Load interval**, then **Play**.

The map presents tracked pedestrians, density cells, and flow direction. The congestion-watch card is a transparent screening signal that combines local density with slower or opposing movement; it is not a physical-pressure metric.

### Simulation and capacity testing

1. Select **Social Force ABM**.
2. Choose **Observed crowd snapshot** to initialize agents from real ATC positions, or **Synthetic zone spawn** for a counterfactual capacity test.
3. Select a scenario and simulation duration, then run the simulation.

For large runs above 250 agents, the engine uses a local-density interaction approximation to preserve dashboard responsiveness. Compare scenario trends and thresholds; do not interpret this approximation as a certified evacuation prediction.

## Model details

### Initialization and intent

For observed-snapshot runs, the selected ATC time supplies agent identifier, position, instantaneous speed, motion angle, and facing angle. The system then examines only the preceding five seconds of each person’s track to smooth heading and speed. A likely destination is chosen from routes observed on other days, weighted by heading alignment and soft origin context. The future trajectory is never used to seed the forecast.

### Social Force dynamics

Agents are accelerated toward an inferred destination while responding to nearby pedestrians and the occupancy-map boundary. Facing angle influences interaction weighting, and simulated facing direction turns gradually toward the motion direction. The walkable mask repairs positions that would cross walls or leave the observed ATC footprint.

### Multi-day calibration

Build/refresh daily profiles and aggregate them:

```bash
python3 build_multiday_calibration.py --build-profiles
```

This writes `generated/multiday_behavior_profile.json`. Subsequent aggregation of existing derivatives can be run without `--build-profiles`:

```bash
python3 build_multiday_calibration.py
```

To profile one specific day only:

```bash
python3 analyze_atc.py --dataset atc-20121114.csv
```

## Calibration and validation

### Calibration

Select Social Force parameters on a **development day**, separate from the final test day:

```bash
python3 calibrate_social_force.py \
  --development-day atc-20121111.csv \
  --horizon 10 \
  --snapshots 3 \
  --history-seconds 5
```

The script evaluates candidate relaxation, repulsion-strength, and repulsion-range settings and writes JSON/Markdown results in `generated/`.

### Held-out validation

Run normal-flow validation on a day excluded from its calibration profile:

```bash
python3 validation_runner.py \
  --test-day atc-20121114.csv \
  --horizon 10 \
  --snapshots 6 \
  --history-seconds 5
```

The runner seeds both Social Force and the constant-velocity baseline from the same observed snapshots, matches people by persistent ATC ID at the forecast horizon, and reports:

- **Final displacement error (FDE):** mean position error in metres.
- **Speed MAE:** mean absolute speed error in m/s.
- **Density L1 error:** mismatch in two-metre spatial-cell occupancy, normalized by observed people.

The included local validation artefact for held-out `atc-20121114.csv` (six snapshots, five-second causal history, ten-second horizon) reported:

| Metric | Social Force | Constant velocity |
| --- | ---: | ---: |
| Final displacement error | 3.505 m | 4.986 m |
| Density L1 error | 1.364 | 1.805 |
| FDE improvement | **29.71%** | baseline |

These results demonstrate short-horizon normal-flow reproduction for this evaluation configuration. They do **not** validate emergency, panic, crush-pressure, or stampede forecasting.

To create the presentable Word report after running the validations:

```bash
python3 create_validation_report.py
```

## API endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /api/datasets` | Lists locally available ATC daily CSVs. |
| `GET /api/metadata?dataset=...` | Reports time coverage and dataset metadata. |
| `GET /api/map` | Serves the map rendered for the dashboard. |
| `GET /api/profile?dataset=...` | Returns the observed behaviour profile and routes. |
| `GET /api/window?...` | Streams an observed time interval into replay frames. |
| `GET /api/simulate?...` | Executes an observed-seed or synthetic Social Force scenario. |

## Limitations and responsible use

- ATC tracks come from 3D range sensors; this repository does **not** run object detection on raw video.
- The available dataset represents ordinary shopping-centre movement, not labelled emergency or stampede events.
- Inferred endpoint zones should be checked against the supplied ATC floor-plan material before designing scenarios.
- Model performance is contextual: recalibration and site-specific validation are required before applying the approach to a different venue.
- Capacity scenarios are exploratory comparisons, not engineering certification or an evacuation plan.

## License and attribution

The repository currently does not include a separate code licence file. The ATC data has its own research-use licence and citation requirement; do not redistribute the tracking CSVs, raw sensor data, or any other restricted dataset asset through this repository.
