"""Small, reproducible Social Force crowd simulator calibrated from ATC profiles."""

from __future__ import annotations

import math
import random
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image


class SocialForceSimulation:
    """Continuous pedestrian movement over a map-derived 0.5 m navigation grid."""

    def __init__(self, map_path: Path, map_metadata: dict, profile: dict, walkable_mask_path: Path | None = None, seed: int = 42, relaxation_time: float = 0.5, repulsion_strength: float = 2.2, repulsion_range: float = 0.28, facing_aware: bool = True):
        self.random = random.Random(seed)
        self.resolution = float(map_metadata["resolution"])
        self.origin_x, self.origin_y = map_metadata["origin"][:2]
        self.scale = 10  # 10 source pixels = 0.5 m navigation cells.
        pixels = np.asarray(Image.open(map_path).convert("L"))
        rows = pixels.shape[0] // self.scale
        cols = pixels.shape[1] // self.scale
        cropped = pixels[: rows * self.scale, : cols * self.scale]
        # The ATC map uses 0 for occupied structure and 127 for free space.
        self.free = (cropped.reshape(rows, self.scale, cols, self.scale).mean(axis=(1, 3)) > 100)
        if walkable_mask_path and walkable_mask_path.exists():
            observed_free = np.load(walkable_mask_path)
            if observed_free.shape == self.free.shape:
                self.free &= observed_free
        self.rows, self.cols = self.free.shape
        self.cell_m = self.resolution * self.scale
        self.profile = profile
        self.relaxation_time = relaxation_time
        self.repulsion_strength = repulsion_strength
        self.repulsion_range = repulsion_range
        self.facing_aware = facing_aware
        self.zones = {zone["id"]: zone for zone in profile["provisional_zones"]}
        self.routes = self._routes()
        self.fields: dict[str, np.ndarray] = {}

    def _routes(self) -> list[tuple[str, str, int]]:
        routes = []
        for item in self.profile.get("top_routes", []):
            try:
                origin, destination = [part.strip() for part in item["route"].split("→")]
                if origin in self.zones and destination in self.zones:
                    routes.append((origin, destination, int(item["trajectories"])))
            except (KeyError, ValueError):
                continue
        if not routes and len(self.zones) > 1:
            names = list(self.zones)
            routes = [(names[0], names[1], 1)]
        return routes

    def to_cell(self, x: float, y: float) -> tuple[int, int]:
        col = int((x - self.origin_x) / self.cell_m)
        row = self.rows - 1 - int((y - self.origin_y) / self.cell_m)
        return max(0, min(self.rows - 1, row)), max(0, min(self.cols - 1, col))

    def to_world(self, row: int, col: int) -> tuple[float, float]:
        x = self.origin_x + (col + 0.5) * self.cell_m
        y = self.origin_y + (self.rows - row - 0.5) * self.cell_m
        return x, y

    def nearest_free(self, row: int, col: int) -> tuple[int, int]:
        if self.free[row, col]:
            return row, col
        for radius in range(1, 18):
            for dr in range(-radius, radius + 1):
                for dc in (-radius, radius):
                    rr, cc = row + dr, col + dc
                    if 0 <= rr < self.rows and 0 <= cc < self.cols and self.free[rr, cc]:
                        return rr, cc
            for dc in range(-radius + 1, radius):
                for dr in (-radius, radius):
                    rr, cc = row + dr, col + dc
                    if 0 <= rr < self.rows and 0 <= cc < self.cols and self.free[rr, cc]:
                        return rr, cc
        # Some inferred endpoint-zone centres land in an untracked sliver of
        # the map. Fall back to the nearest empirically walkable cell instead
        # of permitting an agent to spawn outside the building footprint.
        candidates = np.argwhere(self.free)
        if len(candidates):
            distances = (candidates[:, 0] - row) ** 2 + (candidates[:, 1] - col) ** 2
            nearest = candidates[int(np.argmin(distances))]
            return int(nearest[0]), int(nearest[1])
        raise RuntimeError("No walkable cells are available in the simulation map.")

    def distance_field(self, destination: str) -> np.ndarray:
        if destination in self.fields:
            return self.fields[destination]
        field = np.full((self.rows, self.cols), np.inf, dtype=np.float32)
        zone = self.zones[destination]
        row, col = self.nearest_free(*self.to_cell(zone["x"], zone["y"]))
        field[row, col] = 0
        queue = deque([(row, col)])
        while queue:
            r, c = queue.popleft()
            next_value = field[r, c] + 1
            for rr, cc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                if 0 <= rr < self.rows and 0 <= cc < self.cols and self.free[rr, cc] and next_value < field[rr, cc]:
                    field[rr, cc] = next_value
                    queue.append((rr, cc))
        self.fields[destination] = field
        return field

    def direction_to_destination(self, x: float, y: float, destination: str) -> np.ndarray:
        row, col = self.nearest_free(*self.to_cell(x, y))
        field = self.distance_field(destination)
        candidates = [(field[row, col], row, col)]
        for rr, cc in ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1)):
            if 0 <= rr < self.rows and 0 <= cc < self.cols and self.free[rr, cc]:
                candidates.append((field[rr, cc], rr, cc))
        _, target_row, target_col = min(candidates)
        tx, ty = self.to_world(target_row, target_col)
        vector = np.array([tx - x, ty - y], dtype=float)
        norm = float(np.linalg.norm(vector))
        if norm < 0.05:
            zone = self.zones[destination]
            vector = np.array([zone["x"] - x, zone["y"] - y], dtype=float)
            norm = float(np.linalg.norm(vector))
        return vector / norm if norm else np.zeros(2)

    def sampled_speed(self) -> float:
        model = self.profile["speed_m_s"]
        value = self.random.gauss(float(model["mean"]), float(model["standard_deviation"]))
        return max(0.45, min(1.8, value))

    def make_agents(self, count: int, scenario: str) -> list[dict]:
        routes, weights = zip(*[(route[:2], route[2]) for route in self.routes])
        agents = []
        for identifier in range(count):
            origin, destination = self.random.choices(routes, weights=weights, k=1)[0]
            if scenario == "counterflow" and identifier % 2:
                origin, destination = destination, origin
            if scenario == "restricted_exit":
                destination = max(self.routes, key=lambda item: item[2])[1]
            zone = self.zones[origin]
            angle = self.random.random() * math.tau
            radius = self.random.uniform(0, 0.8)
            x, y = zone["x"] + radius * math.cos(angle), zone["y"] + radius * math.sin(angle)
            row, col = self.nearest_free(*self.to_cell(x, y))
            x, y = self.to_world(row, col)
            agents.append({"id": identifier + 1, "position": np.array([x, y], dtype=float), "velocity": np.zeros(2), "speed": self.sampled_speed(), "destination": destination, "radius": self.random.uniform(0.22, 0.30), "facing": angle, "arrived": False})
        return agents

    def inferred_destination(self, position: np.ndarray, heading: float) -> str:
        """Choose a calibrated destination consistent with the observed heading."""
        heading_vector = np.array([math.cos(heading), math.sin(heading)])
        choices, weights = [], []
        for _, destination, route_weight in self.routes:
            target = self.zones[destination]
            vector = np.array([target["x"] - position[0], target["y"] - position[1]])
            distance = float(np.linalg.norm(vector))
            if distance < 1.0:
                continue
            alignment = max(0.05, (1 + float(np.dot(heading_vector, vector / distance))) / 2)
            choices.append(destination)
            weights.append(route_weight * alignment)
        return self.random.choices(choices, weights=weights, k=1)[0] if choices else self.routes[0][1]

    def make_observed_agents(self, seeds: list[dict], scenario: str) -> list[dict]:
        agents = []
        for seed in seeds:
            position = np.array([seed["x"], seed["y"]], dtype=float)
            row, col = self.to_cell(position[0], position[1])
            if not self.free[row, col]:
                row, col = self.nearest_free(row, col)
                position = np.array(self.to_world(row, col), dtype=float)
            heading = seed["heading"]
            destination = self.inferred_destination(position, heading)
            if scenario == "counterflow":
                heading = (heading + math.pi) % math.tau
                destination = self.inferred_destination(position, heading)
            velocity = np.array([math.cos(heading), math.sin(heading)], dtype=float) * min(seed["speed"], 1.8)
            # Preserve the observed instantaneous speed in forecast mode. The
            # calibrated distribution remains for synthetic arrivals only.
            agents.append({"id": seed["id"], "position": position, "velocity": velocity, "speed": max(0.0, min(seed["speed"], 1.8)), "destination": destination, "radius": self.random.uniform(0.22, 0.30), "facing": seed.get("facing", heading), "arrived": False})
        return agents

    def run(self, agents_count: int, duration_seconds: int, scenario: str, seeds: list[dict] | None = None) -> tuple[list[dict], dict]:
        if seeds is not None:
            agents = self.make_observed_agents(seeds, scenario)
        else:
            if scenario == "surge":
                agents_count = min(500, round(agents_count * 1.6))
            agents = self.make_agents(agents_count, scenario)
        dt, sample_every = 0.1, 10
        frames, arrived = [], 0
        for step in range(int(duration_seconds / dt) + 1):
            if step % sample_every == 0:
                people = []
                for agent in agents:
                    x, y = agent["position"]
                    velocity = agent["velocity"]
                    # Preserve enough coordinate precision to avoid visually
                    # rounding an in-bounds agent across a 0.5 m grid edge.
                    people.append([agent["id"], round(float(x), 5), round(float(y), 5), round(float(np.linalg.norm(velocity)), 3), round(float(math.atan2(velocity[1], velocity[0])) if np.linalg.norm(velocity) else 0.0, 4)])
                frames.append({"t": float(step * dt), "people": people})
            for index, agent in enumerate(agents):
                position, velocity = agent["position"], agent["velocity"]
                if agent["arrived"]:
                    continue
                desired = self.direction_to_destination(position[0], position[1], agent["destination"]) * agent["speed"]
                force = (desired - velocity) / self.relaxation_time
                for other in agents:
                    if other is agent:
                        continue
                    delta = position - other["position"]
                    distance = float(np.linalg.norm(delta))
                    if 0.001 < distance < 3.0:
                        unit = delta / distance
                        direction_to_other = -unit
                        facing_vector = np.array([math.cos(agent["facing"]), math.sin(agent["facing"])])
                        ahead = max(0.0, float(np.dot(facing_vector, direction_to_other)))
                        # Agents retain rear collision avoidance but react more
                        # strongly to pedestrians within their forward view.
                        perception = 0.45 + 0.55 * ahead if self.facing_aware else 1.0
                        force += perception * self.repulsion_strength * math.exp(((agent["radius"] + other["radius"]) - distance) / self.repulsion_range) * unit
                new_velocity = velocity + force * dt
                magnitude = float(np.linalg.norm(new_velocity))
                if magnitude > 2.0:
                    new_velocity *= 2.0 / magnitude
                proposal = position + new_velocity * dt
                row, col = self.to_cell(proposal[0], proposal[1])
                if not self.free[row, col]:
                    new_velocity *= 0.05
                    proposal = position
                agent["velocity"], agent["position"] = new_velocity, proposal
                if magnitude > 0.05:
                    target_facing = math.atan2(new_velocity[1], new_velocity[0])
                    turn = (target_facing - agent["facing"] + math.pi) % math.tau - math.pi
                    agent["facing"] += max(-0.35, min(0.35, turn))
                target = self.zones[agent["destination"]]
                if math.dist(proposal, (target["x"], target["y"])) < 0.75:
                    agent["arrived"] = True
                    agent["velocity"] = np.zeros(2)
                    arrived += 1
        return frames, {"agents": len(agents), "duration_seconds": duration_seconds, "scenario": scenario, "agents_reached_destination": arrived, "initialization": "observed_snapshot" if seeds is not None else "synthetic"}
