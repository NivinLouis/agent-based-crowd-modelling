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

    def __init__(self, map_path: Path, map_metadata: dict, profile: dict, seed: int = 42):
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
        self.rows, self.cols = self.free.shape
        self.cell_m = self.resolution * self.scale
        self.profile = profile
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
        return row, col

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
            agents.append({"id": identifier + 1, "position": np.array([x, y], dtype=float), "velocity": np.zeros(2), "speed": self.sampled_speed(), "destination": destination, "radius": self.random.uniform(0.22, 0.30)})
        return agents

    def run(self, agents_count: int, duration_seconds: int, scenario: str) -> tuple[list[dict], dict]:
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
                    people.append([agent["id"], round(float(x), 3), round(float(y), 3), round(float(np.linalg.norm(velocity)), 3), round(float(math.atan2(velocity[1], velocity[0])) if np.linalg.norm(velocity) else 0.0, 4)])
                frames.append({"t": float(step * dt), "people": people})
            for index, agent in enumerate(agents):
                position, velocity = agent["position"], agent["velocity"]
                desired = self.direction_to_destination(position[0], position[1], agent["destination"]) * agent["speed"]
                force = (desired - velocity) / 0.5
                for other in agents:
                    if other is agent:
                        continue
                    delta = position - other["position"]
                    distance = float(np.linalg.norm(delta))
                    if 0.001 < distance < 3.0:
                        unit = delta / distance
                        force += 2.2 * math.exp(((agent["radius"] + other["radius"]) - distance) / 0.28) * unit
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
                target = self.zones[agent["destination"]]
                if math.dist(proposal, (target["x"], target["y"])) < 0.75:
                    arrived += 1
        return frames, {"agents": len(agents), "duration_seconds": duration_seconds, "scenario": scenario, "arrivals_proxy": arrived}
