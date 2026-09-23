"""Predictive-maintenance model used by the FlexFactory agent.

A lightweight feature-based classifier (logistic regression on window
statistics, trained on the simulator) stands in for the LSTM/transformer
runtime; it exposes the same `health_score()` API so the agent, the circuit
public inputs and the twin registry stay unchanged when a stronger model is
plugged in.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np

from .simulator import SENSORS, NOMINAL


def features(window: Dict[str, np.ndarray]) -> np.ndarray:
    f: List[float] = []
    for s in SENSORS:
        mu, sd = NOMINAL[s]
        x = window[s].astype(float)
        f += [(x.mean() - mu) / sd, x.std() / sd, (x.max() - mu) / sd]
    return np.array(f)


class HealthModel:
    def __init__(self):
        self.w = None
        self.b = 0.0
        self.mu = None
        self.sd = None

    def fit(self, X: np.ndarray, health: np.ndarray, epochs: int = 300, lr: float = 0.05) -> "HealthModel":
        """Regress latent health from window features (ridge-regularised)."""
        self.mu, self.sd = X.mean(0), X.std(0) + 1e-9
        Z = (X - self.mu) / self.sd
        w = np.zeros(Z.shape[1]); b = float(health.mean())
        for _ in range(epochs):
            pred = Z @ w + b
            g = pred - health
            w -= lr * (Z.T @ g / len(Z) + 1e-3 * w)
            b -= lr * g.mean()
        self.w, self.b = w, b
        return self

    def health_score(self, window: Dict[str, np.ndarray]) -> int:
        z = (features(window) - self.mu) / self.sd
        h = float(np.clip(z @ self.w + self.b, 0.0, 1.0))
        return int(round(100 * h))

    def needs_maintenance(self, window: Dict[str, np.ndarray], threshold: int = 40) -> bool:
        return self.health_score(window) < threshold


def train_from_simulator(minutes: int = 240, seed: int = 7) -> HealthModel:
    from .simulator import FactorySimulator
    sim = FactorySimulator(seed=seed)
    X, y = [], []
    for _ in range(minutes):
        wins = sim.step()
        for mid, w in wins.items():
            X.append(features(w)); y.append(sim.machines[mid].health)
        # inject extra degraded samples so the regressor sees the low-health regime
        for mid in list(sim.machines)[:10]:
            sim.machines[mid].health = max(0.0, sim.machines[mid].health - 0.01)
    return HealthModel().fit(np.array(X), np.array(y))
