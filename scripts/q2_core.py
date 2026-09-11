"""Q2 forecast, day-ahead optimization and causal physical execution."""
import time

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix


def forecast(history_load, history_pv, days=7):
    if len(history_load) == 0 or len(history_load) != len(history_pv):
        raise ValueError("Forecast accepts nonempty past-only histories")
    return np.mean(history_load[-days:], axis=0), np.mean(history_pv[-days:], axis=0)


def margin(past_residuals, config, n=144):
    if not len(past_residuals):
        return np.zeros(n)
    errors = np.asarray(past_residuals[-config["residual_days"]:])
    width = config["residual_bucket_slots"]
    assert n % width == 0
    quantiles = [max(0., float(np.quantile(errors[:, t:t+width], config["candidate_quantile"], method="linear")))
                 for t in range(0, n, width)]
    return np.repeat(quantiles, width)


def plan_audit(price, load, pv, flow, initial, target, config):
    eta, ed = config["charge_efficiency"], config["discharge_efficiency"]
    cap = config["power_kw"] * config["step_hours"]
    previous = np.r_[initial, flow["e"][:-1]]
    maximum = lambda x: float(np.max(np.abs(x)))
    priority = np.minimum(np.maximum(pv-load, 0), np.minimum(cap, np.maximum(0, (config["energy_max"]-previous)/eta)))
    values = {
        "balance": maximum(flow["g"]+pv+flow["d"]-load-flow["c"]-flow["s"]),
        "state": maximum(flow["e"]-previous-eta*flow["c"]+flow["d"]/ed),
        "terminal": abs(float(flow["e"][-1]-target)),
        "bounds": float(max(0., config["energy_min"]-min(flow["e"]), max(flow["e"])-config["energy_max"])),
        "power": float(max(0., max(flow["c"])-cap, max(flow["d"])-cap)),
        "negative": float(max(0., -min(min(flow[k]) for k in ["g", "c", "d", "s"]))),
        "pv_priority": maximum(np.maximum(pv-load, 0)-flow["s"]-priority),
        "mutual_count": int(np.sum((flow["c"]>1e-6) & (flow["d"]>1e-6))),
    }
    values["pass"] = all(v <= 1e-6 for v in values.values())
    return values


def solve_once(price, load, pv, initial, target, config, strict=False, fixed=None):
    n = len(price)
    g, c, d, s, e, z, w = [np.arange(i*n, (i+1)*n) for i in range(7)]
    cap = config["power_kw"] * config["step_hours"]
    eta, ed = config["charge_efficiency"], config["discharge_efficiency"]
    emin, emax = config["energy_min"], config["energy_max"]
    surplus = np.maximum(pv-load, 0)
    lb = np.zeros(7*n); ub = np.full(7*n, np.inf)
    ub[c] = cap; ub[d] = np.minimum(cap, np.maximum(load-pv, 0)); ub[s] = surplus
    lb[e], ub[e] = emin, emax
    lb[e[-1]] = ub[e[-1]] = target
    ub[z] = ub[w] = 1
    objective = np.zeros(7*n); objective[g] = price
    ri, ci, va, lower, upper = [], [], [], [], []
    def add(items, low, high):
        for index, value in items.items():
            ri.append(len(lower)); ci.append(int(index)); va.append(value)
        lower.append(low); upper.append(high)
    for t in range(n):
        add({g[t]:1, c[t]:-1, d[t]:1, s[t]:-1}, load[t]-pv[t], load[t]-pv[t])
        row = {e[t]:1, c[t]:-eta, d[t]:1/ed}
        if t: row[e[t-1]] = -1
        add(row, initial if t == 0 else 0, initial if t == 0 else 0)
        if strict:
            add({c[t]:1, z[t]:-cap}, -np.inf, 0)
            add({d[t]:1, z[t]:cap}, -np.inf, cap)
            if surplus[t] > 0:
                limit = min(cap, surplus[t])
                add({s[t]:-1, w[t]:limit}, limit-surplus[t], np.inf)
                add({e[t]:1, g[t]:-eta, w[t]:-(emax-emin)}, emin, np.inf)
            else: ub[w[t]] = 0
    integer = np.zeros(7*n, dtype=int)
    if strict and fixed is None: integer[z] = integer[w] = 1
    if fixed:
        for index, value in fixed.items(): lb[index] = ub[index] = value
    matrix = coo_matrix((va, (ri, ci)), shape=(len(lower), 7*n)).tocsc()
    begin = time.perf_counter()
    result = milp(objective, integrality=integer, bounds=Bounds(lb, ub),
                  constraints=LinearConstraint(matrix, np.array(lower), np.array(upper)),
                  options={"time_limit":30, "mip_rel_gap":1e-9})
    if result.status != 0:
        raise RuntimeError(f"Solver status {result.status}: {result.message}")
    flow = {key: result.x[index] for key, index in zip(["g", "c", "d", "s", "e"], [g,c,d,s,e])}
    info = {"objective": float(result.fun), "status": int(result.status), "seconds": time.perf_counter()-begin,
            "formulation": "MILP" if strict else "LP",
            "mip_gap": float(result.mip_gap) if getattr(result, "mip_gap", None) is not None else None}
    return flow, info


def plan(price, load, pv, initial, config):
    target = config["planned_terminal_energy"]
    flow, info = solve_once(price, load, pv, initial, target, config)
    check = plan_audit(price, load, pv, flow, initial, target, config)
    lower_bound = info["objective"]
    lp_check = check.copy()
    if not check["pass"]:
        flow, info = solve_once(price, load, pv, initial, target, config, strict=True)
        check = plan_audit(price, load, pv, flow, initial, target, config)
    assert check["pass"], check
    info.update({"audit": check, "lp_audit": lp_check, "lp_lower_bound": lower_bound,
                 "gap_to_lp_yuan": info["objective"]-lower_bound})
    return flow, info


def step(ordinary, load, pv, energy, config):
    """Only current measurements and current energy enter this function."""
    if min(ordinary, load, pv) < -1e-6:
        raise ValueError("Negative physical input")
    ordinary, load, pv = max(ordinary,0.), max(load,0.), max(pv,0.)
    eta, ed = config["charge_efficiency"], config["discharge_efficiency"]
    cap = config["power_kw"] * config["step_hours"]
    pv_load = min(load, pv)
    grid_load = min(ordinary, load-pv_load)
    shortage = max(0., load-pv_load-grid_load)
    if shortage > 0:
        discharge = min(shortage, cap, max(0., energy-config["energy_min"])*ed)
        emergency = shortage-discharge
        pv_charge = grid_charge = 0.
    else:
        available = min(cap, max(0., config["energy_max"]-energy)/eta)
        pv_charge = min(pv-pv_load, available)
        grid_charge = min(ordinary-grid_load, max(0., available-pv_charge))
        discharge = emergency = 0.
    charge = pv_charge+grid_charge
    pv_spill = max(0., pv-pv_load-pv_charge)
    grid_spill = max(0., ordinary-grid_load-grid_charge)
    return {"plan_kwh": ordinary, "load_kwh": load, "pv_kwh": pv,
            "e_start": energy, "e_end": energy+eta*charge-discharge/ed,
            "charge_kwh": charge, "discharge_kwh": discharge,
            "pv_load_kwh": pv_load, "grid_load_kwh": grid_load,
            "pv_charge_kwh": pv_charge, "grid_charge_kwh": grid_charge,
            "pv_spill_kwh": pv_spill, "paid_grid_spill_kwh": grid_spill,
            "emergency_kwh": emergency}


def execute(price, ordinary, load, pv, initial, config):
    rows = []; energy = float(initial)
    for t, (p,g,l,v) in enumerate(zip(price, ordinary, load, pv)):
        row = step(float(g), float(l), float(v), energy, config)
        row.update({"slot": t, "price": float(p), "plan_cost": float(p)*row["plan_kwh"],
                    "emergency_cost": config["emergency_price_multiple"]*float(p)*row["emergency_kwh"]})
        row["total_cost"] = row["plan_cost"]+row["emergency_cost"]
        rows.append(row); energy = row["e_end"]
    return rows, energy
