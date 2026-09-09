"""Factory-floor simulator (paper Table III): 50 heterogeneous machines across
5 production lines, 8 sensors per machine, 60 samples/min per sensor.

Each machine has a latent health trajectory with stochastic degradation and
occasional fault injection; sensors emit fixed-point readings (milli-units)
so they can be fed directly to the telemetry circuit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np

LINES = ["CNC", "Assembly", "QC", "Conveyor", "Press"]
MACHINE_TYPES = {"CNC": "cnc_mill", "Assembly": "robot_arm", "QC": "vision_station",
                 "Conveyor": "conveyor", "Press": "hydraulic_press"}
SENSORS = ["vibration_x", "vibration_y", "vibration_z", "temperature",
           "spindle_current", "acoustic", "pressure", "cycle_time"]

# nominal (mean, std) per sensor in milli-units for a healthy machine
NOMINAL = {
    "vibration_x": (1200, 150), "vibration_y": (1100, 140), "vibration_z": (900, 120),
    "temperature": (42_000, 800), "spindle_current": (8_500, 400), "acoustic": (65_000, 1_500),
    "pressure": (150_000, 3_000), "cycle_time": (12_000, 300),
}
# declared circuit bounds [lo, hi] per sensor (milli-units)
BOUNDS = {
    "vibration_x": (0, 6_000), "vibration_y": (0, 6_000), "vibration_z": (0, 6_000),
    "temperature": (0, 95_000), "spindle_current": (0, 20_000), "acoustic": (0, 110_000),
    "pressure": (0, 250_000), "cycle_time": (0, 60_000),
}


@dataclass
class Machine:
    machine_id: str
    line: str
    mtype: str
    health: float = 1.0                 # latent [0,1]
    degradation: float = 0.0
    cycles: int = 0
    fault: bool = False
    fault_started: int = -1
    history: List[Tuple[int, float]] = field(default_factory=list)


class FactorySimulator:
    def __init__(self, n_machines: int = 50, seed: int = 42, samples_per_min: int = 60,
                 accel: float = 1.0):
        """`accel` scales wear and fault rates (1.0 = realistic multi-day dynamics;
        use e.g. 50 to see maintenance events inside a short demo run)."""
        self.rng = np.random.default_rng(seed)
        self.samples_per_min = samples_per_min
        self.accel = accel
        self.machines: Dict[str, Machine] = {}
        per_line = n_machines // len(LINES)
        k = 0
        for line in LINES:
            for i in range(per_line + (1 if k < n_machines % len(LINES) else 0)):
                mid = f"{line.lower()}-{i + 1:02d}"
                self.machines[mid] = Machine(mid, line, MACHINE_TYPES[line],
                                             degradation=float(self.rng.uniform(2e-5, 8e-5)))
            k += 1
        self.minute = 0

    # ---- dynamics ---------------------------------------------------------------
    def _step_health(self, m: Machine) -> None:
        m.health -= self.accel * m.degradation * (1 + 4 * (1 - m.health))   # accelerating wear
        if not m.fault and self.rng.random() < 2.5e-4 * self.accel:         # ~1 fault / 2.8 days / machine
            m.fault, m.fault_started = True, self.minute
        if m.fault:
            m.health -= 0.004 * self.accel
        m.health = float(np.clip(m.health, 0.0, 1.0))
        m.cycles += int(self.rng.poisson(5))

    def repair(self, machine_id: str) -> None:
        m = self.machines[machine_id]
        m.health, m.fault, m.fault_started = float(self.rng.uniform(0.92, 1.0)), False, -1

    # ---- telemetry --------------------------------------------------------------
    def telemetry_window(self, m: Machine) -> Dict[str, np.ndarray]:
        """One 60 s window: 60 samples for each of 8 sensors (int milli-units)."""
        stress = (1 - m.health) ** 1.5
        out = {}
        for s in SENSORS:
            mu, sd = NOMINAL[s]
            shift = {"vibration_x": 3.0, "vibration_y": 3.0, "vibration_z": 3.0, "temperature": 0.6,
                     "spindle_current": 0.9, "acoustic": 0.35, "pressure": -0.25, "cycle_time": 0.9}[s]
            mean = mu * (1 + shift * stress)
            x = self.rng.normal(mean, sd * (1 + 2 * stress), self.samples_per_min)
            lo, hi = BOUNDS[s]
            out[s] = np.clip(np.round(x), lo, hi).astype(np.int64)
        return out

    def step(self) -> Dict[str, Dict[str, np.ndarray]]:
        """Advance one minute; return telemetry windows for all machines."""
        self.minute += 1
        windows = {}
        for m in self.machines.values():
            self._step_health(m)
            m.history.append((self.minute, m.health))
            windows[m.machine_id] = self.telemetry_window(m)
        return windows

    def true_health_score(self, machine_id: str) -> int:
        return int(round(100 * self.machines[machine_id].health))
