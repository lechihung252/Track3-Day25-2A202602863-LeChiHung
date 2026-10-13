"""Reproduce the experiments in reports/REPORT.md (sections 2, 3 and 8).

    python scripts/run_experiments.py            # everything
    python scripts/run_experiments.py --only 1   # one experiment: 1 | 2 | 3 | params | cache

Every run overrides the `client:` / `cache:` block in memory only; configs/gameday.yaml is never touched.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))  # run without pip install

import argparse
import copy
import dataclasses
from typing import Any

from gameday_lab import client as client_mod
from gameday_lab.factory import build_client
from gameday_lab.sim.config import load_config
from gameday_lab.sim.engine import evaluate_checks, run_scenario

TAPES = "configs/scenarios.yaml"
CACHE_TAPES = "configs/scenarios_cache.yaml"
COLS = ["availability", "latency_p95_s", "amplification", "retry_ratio", "cost_per_good_turn", "recovery_s",
        "primary_share_after_restore", "pass"]
CACHE_COLS = ["availability", "faq_availability", "cache_hit_ratio", "stale_served", "wrong_answers",
              "wrong_answers_by_reason", "outdated_answers", "cost_per_good_turn", "pass"]
NO_BUDGET = {"retry_budget_ratio": 100, "retry_budget_max_tokens": 1e9, "retry_budget_min_per_s": 1e6}
BAD_BREAKER = {"min_calls": 1_000_000_000}


def run(scen_path: str, key: str, client_over: dict[str, Any] | None = None,
        breaker_over: dict[str, Any] | None = None, cache_over: dict[str, Any] | None = None,
        client: str = "resilient") -> dict[str, Any]:
    cfg = load_config("configs/gameday.yaml", scen_path)
    settings = copy.deepcopy(cfg.client)
    settings.update(client_over or {})
    settings["breaker"].update(breaker_over or {})
    cfg = dataclasses.replace(cfg, client=settings, cache={**cfg.cache, **(cache_over or {})})
    m = run_scenario(cfg, key, build_client(client, cfg)).metrics()
    m["pass"] = all(evaluate_checks(m, cfg.scenarios[key].checks).values())
    return m


def show(label: str, m: dict[str, Any], cols: list[str] = COLS) -> None:
    print(f"  {label:36s} " + " ".join(f"{c}={m.get(c)}" for c in cols))


def exp1() -> None:
    print("== Exp 1: retry budget as the last line of defence (max_attempts_per_provider=5)")
    for key in ("G2_overload_storm", "G3_brownout"):
        show(f"{key[:2]} default config", run(TAPES, key))
        show(f"{key[:2]} broken breaker, budget ON", run(TAPES, key, {"max_attempts_per_provider": 5}, BAD_BREAKER))
        show(f"{key[:2]} broken breaker, budget OFF",
             run(TAPES, key, {"max_attempts_per_provider": 5, **NO_BUDGET}, BAD_BREAKER))
        show(f"{key[:2]} working breaker, budget OFF", run(TAPES, key, {"max_attempts_per_provider": 5, **NO_BUDGET}))


def exp2() -> None:
    print("== Exp 2: attempt_timeout_s 8 vs 30")
    for key in ("G6_provider_outage", "G4_slow_region"):
        for t in (8.0, 30.0):
            show(f"{key[:2]} timeout={t}", run(TAPES, key, {"attempt_timeout_s": t}),
                 COLS + ["give_up_reasons", "good_turns", "turns"])


def exp3() -> None:
    print("== Exp 3: ladder() without the tool-calling filter")
    original = client_mod.ResilientClient.ladder
    client_mod.ResilientClient.ladder = lambda self, task: list(self.order)  # type: ignore[method-assign]
    try:
        show("G6 no tool filter", run(TAPES, "G6_provider_outage"),
             COLS + ["incompatible_routes", "give_up_reasons", "good_turns", "turns"])
    finally:
        client_mod.ResilientClient.ladder = original  # type: ignore[method-assign]


def params() -> None:
    print("== Parameter sweeps (one knob at a time, the rest as in configs/gameday.yaml)")
    for v in (4.0, 8.0, 15.0):
        for key in ("G2_overload_storm", "G6_provider_outage"):
            show(f"{key[:2]} attempt_timeout_s={v}", run(TAPES, key, {"attempt_timeout_s": v}))
    for v in (1, 2, 4):
        for key in ("G2_overload_storm", "G3_brownout"):
            show(f"{key[:2]} max_attempts_per_provider={v}", run(TAPES, key, {"max_attempts_per_provider": v}))
    for v in (0.05, 0.25, 2.0):
        show(f"G2 backoff_base_s={v}", run(TAPES, "G2_overload_storm", {"backoff_base_s": v}))
    for v in (0.05, 0.1, 0.5):
        show(f"G3 retry_budget_ratio={v}", run(TAPES, "G3_brownout", {"retry_budget_ratio": v}))
    for w, n in ((5, 3), (20, 10), (100, 50)):
        show(f"G3 window_size={w} min_calls={n}", run(TAPES, "G3_brownout", None, {"window_size": w, "min_calls": n}))
    for v in (0.2, 0.5, 0.9):
        show(f"G3 failure_rate_threshold={v}", run(TAPES, "G3_brownout", None, {"failure_rate_threshold": v}))
    for v in (3.0, 5.0, 1e9):
        show(f"G4 slow_call_s={v}", run(TAPES, "G4_slow_region", None, {"slow_call_s": v}))
    for v in (5.0, 10.0, 60.0):
        show(f"G2 open_duration_s={v}", run(TAPES, "G2_overload_storm", None, {"open_duration_s": v}))
    for v in (1, 2, 20):
        show(f"G2 half_open_max_probes={v}", run(TAPES, "G2_overload_storm", None,
                                                 {"half_open_max_probes": v, "half_open_successes": min(v, 2)}))


def cache() -> None:
    print("== Cache (C1): one knob at a time")
    for v in (0.80, 0.92, 0.97):
        show(f"similarity_threshold={v}", run(CACHE_TAPES, "C1_faq_outage", client="cached", cache_over={
            "similarity_threshold": v}), CACHE_COLS)
    for v in (30, 60, 300):
        show(f"ttl_s={v}", run(CACHE_TAPES, "C1_faq_outage", client="cached", cache_over={"ttl_s": v}), CACHE_COLS)
    for v in (0, 60, 300):
        show(f"max_stale_s={v}", run(CACHE_TAPES, "C1_faq_outage", client="cached", cache_over={"max_stale_s": v}),
             CACHE_COLS)


def main() -> None:
    experiments = {"1": exp1, "2": exp2, "3": exp3, "params": params, "cache": cache}
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=list(experiments), default=None)
    args = ap.parse_args()
    for name, fn in experiments.items():
        if args.only in (None, name):
            fn()


if __name__ == "__main__":
    main()
