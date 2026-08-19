#!/usr/bin/env python3
"""Day-one capability and cost probe.

Run this BEFORE writing a harvester, and before looping over anything.

It answers, in one pass, every question the spec left open:

  * What is my actual credit balance, and what does one call cost?
  * Does cost scale with granularity? With analytic type? (the two cost levers)
  * What is my plan's AOI ceiling -- 10 mi^2 like Basic, 50 like Premium, or
    something else? The "Hackathon" plan is documented nowhere.
  * Which `filter_type` do the analysis types actually accept? The spec says
    3, the quickstart README says 2 or 4. Nobody knows. One cheap call does.
  * Are /satellite and /streetview reachable, or Premium-locked?
  * Does `map_data` carry a per-tile uncertainty field? FortyGuard's pipeline
    page claims probabilistic outputs are "carried through analytics rather
    than dropped at the model boundary" -- it is not in the public schema,
    but if it is in the payload it is worth rendering, and nobody else will
    think to look.

The design exploits one fact from the handbook: **failed and rejected tasks
are free.** Credits are deducted only on success. So probing the AOI ceiling
by deliberately overshooting costs nothing when it is rejected -- and the
one call that succeeds is deliberately made as cheap as possible (single
hour, coarsest granularity).

Usage:
    cp .env.example .env      # put your key in FORTYGUARD_API_KEY
    pip install -r requirements.txt
    python probe.py                     # full probe
    python probe.py --skip-aoi-ceiling  # if you want to avoid the large call
    python probe.py --date 2025-07-22
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from nightwatch.client import NightwatchClient, FortyGuardError, Rejected  # noqa: E402
from nightwatch import geo  # noqa: E402

# A small AOI in central Chicago (the Loop) -- dense masonry, good baseline.
PROBE_LON, PROBE_LAT = -87.6298, 41.8781

REPORT_PATH = Path("probe_report.json")


# --------------------------------------------------------------------------
# credit accounting
# --------------------------------------------------------------------------
def find_numbers(obj: Any, want: tuple, path: str = "") -> dict:
    """Recursively pull numeric fields whose key mentions credits/usage.

    The shape of the usage response is not documented, so we go looking
    rather than guessing a key name.
    """
    found = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if isinstance(v, (int, float)) and any(w in k.lower() for w in want):
                found[p] = v
            else:
                found.update(find_numbers(v, want, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:5]):
            found.update(find_numbers(v, want, f"{path}[{i}]"))
    return found


CREDIT_WORDS = ("credit", "balance", "remaining", "used", "usage", "quota", "limit")


class Ledger:
    """Tracks credit balance across the probe so each test's cost is isolated."""

    def __init__(self, client: NightwatchClient):
        self.client = client
        self.raw_first: Any = None
        self.last: Optional[float] = None
        self.key: Optional[str] = None
        self._warned = False
        self.usage_error: Optional[str] = None

    def snapshot(self) -> Optional[float]:
        try:
            raw = self.client.usage()
        except Exception as e:
            # Report once, not once per test -- snapshot() runs twice around
            # every call, so an unguarded print buries the actual results.
            self.usage_error = str(e)
            if not self._warned:
                print(f"    ! usage lookup failed, credit deltas unavailable: {e}")
                print("      (the probe continues; costs will show '?')")
                self._warned = True
            return None
        if self.raw_first is None:
            self.raw_first = raw
        nums = find_numbers(raw, CREDIT_WORDS)
        if not nums:
            return None
        if self.key is None:
            # prefer a key that looks like a remaining balance
            for k in nums:
                if any(w in k.lower() for w in ("remain", "balance", "available")):
                    self.key = k
                    break
            else:
                self.key = sorted(nums)[0]
        return float(nums.get(self.key, 0))

    def cost_of(self, fn, label: str) -> dict:
        """Run fn(), returning result plus measured credit delta and latency."""
        before = self.snapshot()
        t0 = time.monotonic()
        ok, result, err = True, None, None
        try:
            result = fn()
        except Exception as e:  # noqa: BLE001 - we want every failure recorded
            ok, err = False, f"{type(e).__name__}: {e}"
        latency = time.monotonic() - t0
        after = self.snapshot()

        delta = None
        if before is not None and after is not None:
            delta = before - after

        print(
            f"    {'OK ' if ok else 'ERR'} {label}: "
            f"{latency:6.1f}s"
            + (f"  credits={delta:g}" if delta is not None else "  credits=?")
            + ("" if ok else f"\n        {err}")
        )
        return {
            "label": label,
            "ok": ok,
            "error": err,
            "latency_s": round(latency, 2),
            "credit_delta": delta,
            "credits_before": before,
            "credits_after": after,
            "result": result,
        }


# --------------------------------------------------------------------------
# inspection helpers
# --------------------------------------------------------------------------
def describe_map_data(result: Any) -> dict:
    """What did we actually get back? Schema reality-check."""
    if not isinstance(result, dict):
        return {"note": f"result is {type(result).__name__}, not a dict"}

    md = result.get("map_data") or {}
    feats = md.get("features") or []
    out = {
        "top_level_keys": sorted(result.keys()),
        "n_features": len(feats),
        "stats_data_keys": sorted((result.get("stats_data") or {}).keys()),
    }
    if feats:
        props = feats[0].get("properties", {})
        out["first_feature_property_keys"] = sorted(props.keys())
        out["first_feature_properties"] = props
        # the thing nobody else will look for
        uncertainty_like = [
            k for k in props
            if any(w in k.lower() for w in
                   ("uncert", "conf", "std", "sigma", "var", "error", "ci"))
        ]
        out["uncertainty_like_fields"] = uncertainty_like
    return out


def summarise_values(result: Any) -> dict:
    md = (result or {}).get("map_data") or {}
    vals = []
    for f in md.get("features") or []:
        v = f.get("properties", {}).get("value")
        if isinstance(v, (int, float)):
            vals.append(float(v))
    if not vals:
        return {}
    vals.sort()
    return {
        "n": len(vals),
        "min": vals[0],
        "max": vals[-1],
        "median": vals[len(vals) // 2],
        "units_from_stats": ((result or {}).get("stats_data") or {}).get("units"),
    }


# --------------------------------------------------------------------------
# the probe
# --------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2025-07-22",
                    help="a past summer date (handbook floor is 2021-01-01)")
    ap.add_argument("--skip-aoi-ceiling", action="store_true",
                    help="skip the large-AOI test (it makes one real call)")
    ap.add_argument("--skip-premium", action="store_true",
                    help="skip /satellite and /streetview probes")
    args = ap.parse_args()

    load_dotenv()
    client = NightwatchClient(use_cache=False)  # probe must measure real calls
    ledger = Ledger(client)

    report: dict = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "probe_date": args.date,
        "using_official_client": client.using_official,
        "tests": {},
    }

    print("=" * 72)
    print("NIGHTWATCH DAY-ONE PROBE")
    print("=" * 72)
    print(f"official fortyguard client vendored: {client.using_official}")

    # --- 0. balance -------------------------------------------------------
    print("\n[0] credit balance")
    start_balance = ledger.snapshot()
    print(f"    balance field: {ledger.key}")
    print(f"    value: {start_balance}")
    report["usage_raw"] = ledger.raw_first
    report["balance_field"] = ledger.key
    report["balance_start"] = start_balance
    if start_balance is None:
        print("    ! could not identify a credit field. Costs below will show '?'.")
        print("      Inspect probe_report.json -> usage_raw and set it manually.")

    tiny = geo.feature_collection(geo.square_aoi(PROBE_LON, PROBE_LAT, 0.6))
    small = geo.feature_collection(geo.square_aoi(PROBE_LON, PROBE_LAT, 4.0))

    # --- 1. baseline tcm --------------------------------------------------
    print("\n[1] baseline tcm, 0.6 mi^2, granularity 100, single day")
    t = ledger.cost_of(
        lambda: client.heatmap(tiny, args.date, filter_type=3,
                               granularity=100, analytic_type="tcm"),
        "tcm g100 filter3",
    )
    report["tests"]["tcm_g100"] = _strip(t)
    if t["ok"]:
        report["schema_tcm"] = describe_map_data(t["result"])
        print(f"        features: {report['schema_tcm'].get('n_features')}")
        print(f"        property keys: {report['schema_tcm'].get('first_feature_property_keys')}")
        unc = report["schema_tcm"].get("uncertainty_like_fields")
        if unc:
            print(f"        *** UNCERTAINTY-LIKE FIELDS FOUND: {unc} ***")
            print("            Render these. Almost nobody else will.")

    # --- 2. granularity cost lever ---------------------------------------
    print("\n[2] granularity cost lever (same AOI, 60 vs 100)")
    t60 = ledger.cost_of(
        lambda: client.heatmap(tiny, args.date, filter_type=3,
                               granularity=60, analytic_type="tcm"),
        "tcm g60 filter3",
    )
    report["tests"]["tcm_g60"] = _strip(t60)
    if t["credit_delta"] and t60["credit_delta"]:
        ratio = t60["credit_delta"] / t["credit_delta"]
        report["granularity_cost_ratio_60_over_100"] = ratio
        print(f"    -> 60 m costs {ratio:.2f}x what 100 m costs")

    # --- 3. filter_type question for analytics ---------------------------
    print("\n[3] which filter_type do the analytics accept?")
    print("    (rejections are free -- this is the cheap way to settle it)")
    for ft, kwargs in [
        (3, {}),
        (2, {"start_time": "00:00", "end_time": "23:00"}),
    ]:
        r = ledger.cost_of(
            lambda ft=ft, kwargs=kwargs: client.heatmap(
                tiny, args.date, filter_type=ft, granularity=100,
                analytic_type="time_of_measure", **kwargs),
            f"time_of_measure filter_type={ft}",
        )
        report["tests"][f"tom_filter{ft}"] = _strip(r)
        if r["ok"]:
            report[f"tom_filter{ft}_values"] = summarise_values(r["result"])
            report[f"schema_time_of_measure"] = describe_map_data(r["result"])
            print(f"        values: {report[f'tom_filter{ft}_values']}")

    # --- 4. persistence + threshold --------------------------------------
    print("\n[4] persistence with threshold/direction")
    rp = ledger.cost_of(
        lambda: client.heatmap(tiny, args.date, filter_type=3, granularity=100,
                               analytic_type="persistence",
                               threshold=26, direction="above"),
        "persistence t=26 above",
    )
    report["tests"]["persistence"] = _strip(rp)
    if rp["ok"]:
        report["persistence_values"] = summarise_values(rp["result"])
        print(f"        values: {report['persistence_values']}")

    # --- 5. AOI ceiling ---------------------------------------------------
    if not args.skip_aoi_ceiling:
        print("\n[5] AOI ceiling (cheapest possible large calls: 1 hour, g100)")
        print("    a rejection here is free; a success costs one cheap call")
        ceiling = None
        for area in (9.0, 12.0, 30.0, 48.0, 60.0):
            aoi = geo.feature_collection(geo.square_aoi(PROBE_LON, PROBE_LAT, area))
            r = ledger.cost_of(
                lambda aoi=aoi: client.heatmap(
                    aoi, args.date, filter_type=1, start_time="14:00",
                    granularity=100, analytic_type="tcm"),
                f"aoi {area:g} mi^2",
            )
            report["tests"][f"aoi_{area:g}"] = _strip(r, keep_result=False)
            if r["ok"]:
                ceiling = area
            else:
                print(f"    -> rejected at {area:g} mi^2; ceiling is between "
                      f"{ceiling if ceiling else '<9'} and {area:g}")
                break
        report["aoi_ceiling_confirmed_at_least"] = ceiling
        if ceiling and ceiling >= 48:
            print("    -> Premium-like ceiling (~50 mi^2). Plan bigger transects.")
        elif ceiling and ceiling <= 12:
            print("    -> Basic-like ceiling (10 mi^2). Transect strategy stands.")

    # --- 6. env_params parameter cap (tier tell) -------------------------
    print("\n[6] env_params parameter cap (3 = Basic/Startup, all = Premium)")
    four = ["wet_bulb_temperature_celsius", "heat_index_celsius",
            "relative_humidity_percent", "solar_irradiance"]
    r3 = ledger.cost_of(
        lambda: client.env_params(PROBE_LAT, PROBE_LON, 32.0, args.date,
                                  filter_type=3, start_time=None,
                                  analysis=four[:3]),
        "env_params 3 params",
    )
    report["tests"]["env_params_3"] = _strip(r3)
    r4 = ledger.cost_of(
        lambda: client.env_params(PROBE_LAT, PROBE_LON, 32.0, args.date,
                                  filter_type=3, start_time=None,
                                  analysis=four),
        "env_params 4 params",
    )
    report["tests"]["env_params_4"] = _strip(r4)
    report["env_params_cap_is_3"] = bool(r3["ok"] and not r4["ok"])
    if report["env_params_cap_is_3"]:
        print("    -> capped at 3 parameters. Budget your picks per call.")
    elif r4["ok"]:
        print("    -> 4+ parameters accepted. Premium-like access.")

    # --- 7. premium endpoints --------------------------------------------
    if not args.skip_premium:
        print("\n[7] segmentation endpoints (Premium in the public docs)")
        rs = ledger.cost_of(
            lambda: client.satellite(PROBE_LAT, PROBE_LON, args.date,
                                     start_time="14:00", granularity=80),
            "satellite",
        )
        report["tests"]["satellite"] = _strip(rs, keep_result=False)
        if rs["ok"] and isinstance(rs["result"], dict):
            seg = (rs["result"].get("segmentation") or {})
            report["satellite_segment_classes"] = list(
                (seg.get("segments") or {}).keys())
            report["satellite_image_year"] = rs["result"].get("image_year")
            print(f"        classes: {report['satellite_segment_classes']}")
            print(f"        image_year: {report['satellite_image_year']}")

        rv = ledger.cost_of(
            lambda: client.streetview(PROBE_LAT, PROBE_LON,
                                      horizontal_angle=0, vertical_angle=15),
            "streetview",
        )
        report["tests"]["streetview"] = _strip(rv, keep_result=False)
        if rv["ok"] and isinstance(rv["result"], dict):
            front = rv["result"].get("front") or {}
            report["streetview_segment_classes"] = list(
                (front.get("segments") or {}).keys())
            report["streetview_image_date"] = front.get("image_date")
            print(f"        classes: {report['streetview_segment_classes']}")

    # --- summary ----------------------------------------------------------
    end_balance = ledger.snapshot()
    report["balance_end"] = end_balance
    if start_balance is not None and end_balance is not None:
        report["probe_total_cost"] = start_balance - end_balance

    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    _summary(report)

    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nfull report -> {REPORT_PATH.resolve()}")
    print("Paste the summary block into the README. It is the answer to "
          "'why did you scope it this way'.")


def _strip(t: dict, keep_result: bool = True) -> dict:
    """Drop bulky payloads from the saved report."""
    out = {k: v for k, v in t.items() if k != "result"}
    if keep_result and isinstance(t.get("result"), dict):
        out["result_keys"] = sorted(t["result"].keys())
    return out


def _summary(report: dict) -> None:
    b0, b1 = report.get("balance_start"), report.get("balance_end")
    if b0 is not None:
        print(f"  balance:            {b0:g} -> {b1:g}"
              f"   (probe cost {report.get('probe_total_cost', '?')})")
    per_call = report.get("tests", {}).get("tcm_g100", {}).get("credit_delta")
    if per_call:
        print(f"  cost / tcm call:    {per_call:g} credits at g100")
        if b1:
            print(f"  calls affordable:   ~{int(b1 / per_call):,} more at that rate")
            print(f"  AOIs affordable:    ~{int(b1 / per_call / 3):,} "
                  f"(3 layers each)")
    ratio = report.get("granularity_cost_ratio_60_over_100")
    if ratio:
        print(f"  g60 vs g100:        {ratio:.2f}x")
    print(f"  AOI ceiling >=:     {report.get('aoi_ceiling_confirmed_at_least')} mi^2")
    print(f"  env_params cap 3:   {report.get('env_params_cap_is_3')}")
    for name in ("satellite", "streetview"):
        t = report.get("tests", {}).get(name)
        if t:
            print(f"  {name:18s}{'available' if t['ok'] else 'BLOCKED - ' + str(t['error'])[:60]}")
    for ft in (3, 2):
        t = report.get("tests", {}).get(f"tom_filter{ft}")
        if t:
            print(f"  time_of_measure ft={ft}: {'accepted' if t['ok'] else 'rejected'}")
    unc = (report.get("schema_tcm") or {}).get("uncertainty_like_fields")
    print(f"  uncertainty fields: {unc if unc else 'none found'}")


if __name__ == "__main__":
    main()
