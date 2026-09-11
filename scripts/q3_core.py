"""Core optimizer and causal evaluator for Question 3 rolling plans."""
from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from q2_core import execute, plan_audit


@dataclass
class Choice:
    plan: np.ndarray
    source: str
    score: float
    scenario_mean: float
    scenario_cvar: float
    adjustment_fee: float


def adjustment_fee(price: np.ndarray, new: np.ndarray, old: np.ndarray | None) -> float:
    if old is None:
        return 0.0
    return float(np.sum(0.5 * price * np.abs(new - old)))


def solve_plan(price, load, pv, initial, target, config, previous=None, strict=False):
    """Plan ordinary purchases; changes are priced against the previous effective plan."""
    price, load, pv = map(lambda x: np.asarray(x, dtype=float), (price, load, pv))
    n = len(price)
    if not (len(load) == len(pv) == n and n > 0):
        raise ValueError("price/load/pv lengths must match")
    previous = None if previous is None else np.asarray(previous, dtype=float)
    if previous is not None and len(previous) != n:
        raise ValueError("previous plan length mismatch")

    groups = {name: np.arange(i * n, (i + 1) * n)
              for i, name in enumerate(["g", "c", "d", "s", "e", "z", "w", "up", "down"])}
    g, c, d, s, e, z, w, up, down = (groups[k] for k in groups)
    size = 9 * n
    cap = config["power_kw"] * config["step_hours"]
    eta, ed = config["charge_efficiency"], config["discharge_efficiency"]
    emin, emax = config["energy_min"], config["energy_max"]
    surplus = np.maximum(pv - load, 0.0)
    lb = np.zeros(size)
    ub = np.full(size, np.inf)
    ub[c] = cap
    ub[d] = np.minimum(cap, np.maximum(load - pv, 0.0))
    ub[s] = surplus
    lb[e], ub[e] = emin, emax
    lb[e[-1]] = ub[e[-1]] = target
    ub[z] = ub[w] = 1.0
    if previous is None:
        ub[up] = ub[down] = 0.0

    objective = np.zeros(size)
    objective[g] = price
    if previous is not None:
        objective[up] = objective[down] = 0.5 * price

    ri, ci, va, lower, upper = [], [], [], [], []

    def add(items, low, high):
        row = len(lower)
        for index, value in items.items():
            ri.append(row)
            ci.append(int(index))
            va.append(value)
        lower.append(low)
        upper.append(high)

    for t in range(n):
        add({g[t]: 1, c[t]: -1, d[t]: 1, s[t]: -1}, load[t] - pv[t], load[t] - pv[t])
        state = {e[t]: 1, c[t]: -eta, d[t]: 1 / ed}
        if t:
            state[e[t - 1]] = -1
        add(state, initial if t == 0 else 0, initial if t == 0 else 0)
        if previous is not None:
            add({g[t]: 1, up[t]: -1, down[t]: 1}, previous[t], previous[t])
        if strict:
            add({c[t]: 1, z[t]: -cap}, -np.inf, 0)
            add({d[t]: 1, z[t]: cap}, -np.inf, cap)
            if surplus[t] > 0:
                limit = min(cap, surplus[t])
                add({s[t]: -1, w[t]: limit}, limit - surplus[t], np.inf)
                add({e[t]: 1, g[t]: -eta, w[t]: -(emax - emin)}, emin, np.inf)
            else:
                ub[w[t]] = 0

    integer = np.zeros(size, dtype=int)
    if strict:
        integer[z] = integer[w] = 1
    matrix = coo_matrix((va, (ri, ci)), shape=(len(lower), size)).tocsc()
    started = time.perf_counter()
    result = milp(objective, integrality=integer, bounds=Bounds(lb, ub),
                  constraints=LinearConstraint(matrix, np.asarray(lower), np.asarray(upper)),
                  options={"time_limit": 30, "mip_rel_gap": 1e-9})
    if result.status != 0:
        raise RuntimeError(f"Solver status {result.status}: {result.message}")
    flow = {key: result.x[groups[key]] for key in ["g", "c", "d", "s", "e"]}
    check = plan_audit(price, load, pv, flow, initial, target, config)
    if previous is not None:
        check["adjustment_balance"] = float(np.max(np.abs(
            flow["g"] - previous - result.x[up] + result.x[down])))
        check["pass"] = check["pass"] and check["adjustment_balance"] <= 1e-6
    info = {"objective": float(result.fun), "seconds": time.perf_counter() - started,
            "formulation": "MILP" if strict else "LP", "audit": check}
    return flow, info


def plan_with_audit(price, load, pv, initial, target, config, previous=None):
    flow, info = solve_plan(price, load, pv, initial, target, config, previous, strict=False)
    lp = info
    if not info["audit"]["pass"]:
        flow, info = solve_plan(price, load, pv, initial, target, config, previous, strict=True)
        info["lp_audit"] = lp["audit"]
        info["lp_lower_bound"] = lp["objective"]
    if not info["audit"]["pass"]:
        raise AssertionError(info["audit"])
    return flow, info


def evaluate_plan(price, ordinary, load, pv, initial, config, previous=None):
    rows, end = execute(price, ordinary, load, pv, initial, config)
    fee = adjustment_fee(np.asarray(price), np.asarray(ordinary), previous)
    cash = sum(r["total_cost"] for r in rows) + fee
    inventory_value = config["terminal_value_multiple"] * float(np.mean(price)) * (initial - end)
    return cash + inventory_value, cash, fee, rows, end


def choose_candidate(price, candidates, scenario_loads, scenario_pvs, initial, config,
                     previous=None, risk_weight=0.0, alpha=0.9):
    """Choose a fixed ordinary-purchase plan by causal execution across scenarios."""
    unique = []
    for source, plan in candidates:
        plan = np.maximum(0.0, np.asarray(plan, dtype=float))
        if not any(np.max(np.abs(plan - p)) < 1e-7 for _, p in unique):
            unique.append((source, plan))
    scored = []
    for source, plan in unique:
        values = []
        fee = adjustment_fee(np.asarray(price), plan, previous)
        for load, pv in zip(scenario_loads, scenario_pvs):
            score, _, _, _, _ = evaluate_plan(price, plan, load, pv, initial, config, previous)
            values.append(score)
        values = np.sort(np.asarray(values))
        mean = float(np.mean(values))
        tail = values[max(0, int(np.ceil(alpha * len(values))) - 1):]
        cvar = float(np.mean(tail))
        score = mean + risk_weight * (cvar - mean)
        scored.append(Choice(plan, source, score, mean, cvar, fee))
    return min(scored, key=lambda x: (x.score, x.adjustment_fee)), scored
