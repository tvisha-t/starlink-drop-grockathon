"""Command-line check of the core engine: simulate a preset flood (or tornado) and plan terminals.

Usage (from the starlink-drop folder):
    python3 -m backend.run_core_test
    python3 -m backend.run_core_test south_dekalb_ef3       # the tornado preset
    python3 -m backend.run_core_test path/to/tracts.csv     # use a different tracts file
"""
import sys
import time

from .data import load_all
from .optimize import Optimizer
from .scenario import DEFAULT_PRESET, PRESETS
from .sim import Simulator


def main():
    t0 = time.time()
    preset = next((a for a in sys.argv[1:] if a in PRESETS), DEFAULT_PRESET)
    paths = [a for a in sys.argv[1:] if a not in PRESETS]
    data = load_all(paths[0] if paths else None)
    print(f"Loaded {len(data.tracts)} tracts ({data.tracts['pop'].sum():,} people), "
          f"{len(data.towers)} towers, {len(data.sites)} sites, {len(data.eocs)} EOCs "
          f"in {time.time() - t0:.1f}s")

    t0 = time.time()
    sim_engine = Simulator(data)
    opt = Optimizer(data)
    print(f"Built coverage matrices and hubs in {time.time() - t0:.1f}s")

    sc = PRESETS[preset]
    t0 = time.time()
    sim = sim_engine.simulate(sc)
    print(f"\nScenario: {sc['name']}  (simulated in {time.time() - t0:.2f}s)")
    print(f"  Towers structurally damaged (avg across draws): {sim['towers_damaged_share']:.1%}")
    for h, people, dark in zip(sim["hours"], sim["people_out"], sim["towers_dark_share"]):
        print(f"  hour {h:>2}: {people:>9,.0f} people without service   {dark:6.1%} of towers dark")

    for alpha, label in [(0.0, "Most people"), (2.0, "Most vulnerable")]:
        t0 = time.time()
        plan = opt.plan(sim, sc, alpha=alpha, hour=12, check_exact=(alpha == 2.0))
        m, b, x = plan["metrics"], plan["baseline_metrics"], plan["vs_baseline"]
        nb, nx = plan["nearest_metrics"], plan["vs_nearest"]
        print(f"\nPlan: {label} (alpha={alpha}), {plan['k']} terminals, "
              f"{plan['pinned_hospitals']} hospital(s) pinned  [{time.time() - t0:.2f}s]")
        print(f"  reached: {m['people_reached']:,.0f} people ({m['share_reached']:.0%} of those without service), "
              f"{m['vulnerable_reached']:,.0f} vulnerable")
        print(f"  closest-to-damage baseline: {nb['people_reached']:,.0f} people, "
              f"{nb['vulnerable_reached']:,.0f} vulnerable"
              + (f"  -> {nx['people_x']:.1f}x people, {(nx['vulnerable_x'] or 0):.1f}x vulnerable" if nx['people_x'] else ""))
        print(f"  one-per-EOC baseline:       {b['people_reached']:,.0f} people, "
              f"{b['vulnerable_reached']:,.0f} vulnerable")
        if "optimality" in plan:
            print(f"  greedy reaches {plan['optimality']['greedy_share_of_optimal']:.1%} of the exact optimum")
        for s in plan["sites"][:5]:
            print(f"   {s['rank']:>2}. {s['name'][:40]:<40} {s['type']:<17} +{s['people_reached']:,} people")


if __name__ == "__main__":
    main()
