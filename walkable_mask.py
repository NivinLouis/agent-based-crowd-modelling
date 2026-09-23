"""Derive a conservative walkable-area mask from observed ATC trajectories."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np


def mask_path_for(generated: Path, dataset: Path) -> Path:
    return generated / f"walkable_mask_{dataset.stem}.npy"


def build_walkable_mask(dataset: Path, generated: Path, map_metadata: dict, stride: int = 20) -> Path:
    """Rasterize sampled pedestrian positions onto the simulator's 0.5 m grid.

    The supplied occupancy grid marks broad unknown/outside areas as free. A
    data-derived footprint prevents simulated people entering those areas while
    retaining real corridors used by the tracked pedestrians.
    """
    output = mask_path_for(generated, dataset)
    if output.exists() and output.stat().st_mtime >= dataset.stat().st_mtime:
        return output
    scale = 10
    cell_m = float(map_metadata["resolution"]) * scale
    origin_x, origin_y = map_metadata["origin"][:2]
    rows, cols = int(map_metadata["height"]) // scale, int(map_metadata["width"]) // scale
    mask = np.zeros((rows, cols), dtype=bool)
    with dataset.open("rb") as handle:
        for number, line in enumerate(handle):
            if number % stride:
                continue
            fields = line.split(b",")
            if len(fields) < 4:
                continue
            try:
                x, y = float(fields[2]) / 1000, float(fields[3]) / 1000
            except ValueError:
                continue
            col = int((x - origin_x) / cell_m)
            row = rows - 1 - int((y - origin_y) / cell_m)
            if 0 <= row < rows and 0 <= col < cols:
                mask[row, col] = True
    # A 1 m buffer supports natural avoidance while keeping agents inside the
    # empirical tracking footprint.
    expanded = mask.copy()
    for dr in range(-2, 3):
        for dc in range(-2, 3):
            if dr == dc == 0:
                continue
            source_rows = slice(max(0, -dr), min(rows, rows - dr))
            source_cols = slice(max(0, -dc), min(cols, cols - dc))
            target_rows = slice(max(0, dr), min(rows, rows + dr))
            target_cols = slice(max(0, dc), min(cols, cols + dc))
            expanded[target_rows, target_cols] |= mask[source_rows, source_cols]
    generated.mkdir(exist_ok=True)
    np.save(output, expanded)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build a trajectory-derived ATC walkable mask.")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--generated", type=Path, default=Path("generated"))
    parser.add_argument("--origin-x", type=float, default=-60)
    parser.add_argument("--origin-y", type=float, default=-40)
    parser.add_argument("--width", type=int, default=2800)
    parser.add_argument("--height", type=int, default=1200)
    parser.add_argument("--resolution", type=float, default=0.05)
    arguments = parser.parse_args()
    metadata = {"origin": [arguments.origin_x, arguments.origin_y], "width": arguments.width, "height": arguments.height, "resolution": arguments.resolution}
    print(build_walkable_mask(arguments.dataset, arguments.generated, metadata))
