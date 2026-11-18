"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, load_spec, per_item
from lab9.graders import GRADERS

SAFETY = {"secret_leak", "approval_bypass"}   # aggregated as counts, not means


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    problems: list[str] = []
    metrics, rows = pr.get("metrics") or {}, pr.get("rows") or []
    manifest = pr.get("manifest") or {}

    # 1. NaN / inf: `nan < min` is False, so a naive threshold comparison waves it through.
    for k, v in metrics.items():
        if isinstance(v, float) and not math.isfinite(v):
            problems.append(f"metric {k} is {v} (NaN/inf is not a result)")

    # 2. Row count and duplicates: dropped failing rows, or the same good row repeated.
    want_rows = int(manifest.get("items", 0)) * len(manifest.get("seeds") or [])
    if len(rows) != want_rows:
        problems.append(f"{len(rows)} rows but manifest says {manifest.get('items')} items x "
                        f"{len(manifest.get('seeds') or [])} seeds = {want_rows} (dropped or duplicated rows)")
    keys = [(r.get("item_id"), r.get("seed")) for r in rows]
    dup = sorted({k for k in keys if keys.count(k) > 1}, key=str)
    if dup:
        problems.append(f"duplicate (item, seed) rows: {dup[:3]}")

    # 3. Same (item, seed) set as the baseline: cherry-picked seeds / items.
    base_keys = {(r.get("item_id"), r.get("seed")) for r in base.get("rows") or []}
    if set(keys) != base_keys:
        missing, extra = sorted(base_keys - set(keys), key=str), sorted(set(keys) - base_keys, key=str)
        problems.append(f"(item, seed) set differs from the baseline: {len(missing)} missing {missing[:2]}, "
                        f"{len(extra)} extra {extra[:2]}")
    if spec.get("seeds") and sorted(manifest.get("seeds") or []) != sorted(spec["seeds"]):
        problems.append(f"manifest seeds {manifest.get('seeds')} differ from the spec's {spec['seeds']}")

    # 4. Crashed graders: a failure list, or a graded metric whose row value is None/absent.
    if pr.get("evaluator_failures"):
        problems.append(f"{len(pr['evaluator_failures'])} evaluator failure(s): {pr['evaluator_failures'][0]}")
    graded = [m["id"] for m in spec.get("metrics", []) if m["id"] in GRADERS]
    for mid in graded:
        bad = sum(1 for r in rows if r.get(mid) is None)
        if bad:
            problems.append(f"{bad} row(s) have no value for graded metric {mid} (crashed grader drops out of the mean)")

    # 5. Reported metrics must be what the rows say. A hand-edited metrics block is not evidence.
    for mid in graded:
        vals = [r[mid] for r in rows if r.get(mid) is not None]
        if not vals or mid not in metrics or not isinstance(metrics[mid], (int, float)):
            continue
        again = float(sum(vals)) if mid in SAFETY else sum(vals) / len(vals)
        if abs(again - metrics[mid]) > 1e-3:
            problems.append(f"metric {mid} reports {metrics[mid]} but its rows give {again:.4f}")

    # 5b. Reliability metrics (pass_5, pass_at_k, pass_hat_k) are functions of the pass_rate rows too.
    by_item: dict[str, list[bool]] = {}
    for r in rows:
        if r.get("pass_rate") is not None:
            by_item.setdefault(r.get("item_id"), []).append(bool(r["pass_rate"]))
    for name, v in metrics.items():
        m = re.fullmatch(r"pass_(at|hat)_(\d+)", name)
        if not by_item or not isinstance(v, (int, float)) or not (m or name == "pass_5"):
            continue
        try:
            if name == "pass_5":
                again = sum(all(x) for x in by_item.values()) / len(by_item)
            else:
                fn = stats.pass_at_k if m.group(1) == "at" else stats.pass_hat_k
                k = int(m.group(2))
                again = sum(fn(len(x), sum(x), k) for x in by_item.values()) / len(by_item)
        except ValueError:
            continue          # fewer than k runs per item: the report itself sets it to None
        if abs(again - v) > 1e-3:
            problems.append(f"metric {name} reports {v} but its rows give {again:.4f}")

    # 6. Slice tags belong to the item, not to the PR: relabelling hides a regression from the slice gate.
    tags = lambda rep: {r.get("item_id"): sorted(r.get("slices") or []) for r in rep.get("rows") or []}
    tp, tb = tags(pr), tags(base)
    moved = sorted(i for i in tp.keys() & tb.keys() if tp[i] != tb[i])
    if moved:
        problems.append(f"slice tags differ from the baseline for {moved[:3]} (relabelled items)")

    # 7. Ratchet: same eval-set version => thresholds only get stricter.
    if base_spec is not None and base_spec.get("eval_set_version") == spec.get("eval_set_version"):
        problems += _ratchet(spec, base_spec)
    return problems


def _loosened(name: str, new: dict, old: dict) -> list[str]:
    out = []
    nt, ot = new.get("threshold", new), old.get("threshold", old)
    if "max" in ot and nt.get("max", math.inf) > ot["max"]:
        out.append(f"{name}: max raised {ot['max']} -> {nt.get('max')}")
    if "min" in ot and nt.get("min", -math.inf) < ot["min"]:
        out.append(f"{name}: min lowered {ot['min']} -> {nt.get('min')}")
    if "max_increase" in ot and nt.get("max_increase", math.inf) > ot["max_increase"]:
        out.append(f"{name}: max_increase raised {ot['max_increase']} -> {nt.get('max_increase')}")
    if ot.get("no_significant_drop") and not nt.get("no_significant_drop"):
        out.append(f"{name}: no_significant_drop removed")
    if old.get("blocking") and not new.get("blocking"):
        out.append(f"{name}: no longer blocking")
    return out


def _ratchet(spec: dict, base_spec: dict) -> list[str]:
    problems = []
    now = {m["id"]: m for m in spec.get("metrics", [])}
    for m in base_spec.get("metrics", []):
        if m["id"] not in now:
            problems.append(f"ratchet: metric {m['id']} disappeared from the spec")
        else:
            problems += [f"ratchet: {p}" for p in _loosened(f"metric {m['id']}", now[m["id"]], m)]
    now_s = {(s["name"], s["metric"]): s for s in spec.get("slices") or []}
    for s in base_spec.get("slices") or []:
        key = (s["name"], s["metric"])
        if key not in now_s:
            problems.append(f"ratchet: slice {s['name']}/{s['metric']} disappeared from the spec")
        else:
            problems += [f"ratchet: {p}" for p in _loosened(f"slice {s['name']}/{s['metric']}", now_s[key], s)]
    if base_spec.get("pass_k") and set(base_spec["pass_k"]) - set(spec.get("pass_k") or []):
        problems.append("ratchet: pass_k lost values the base spec reports")
    return problems


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for sl in spec.get("slices") or []:
        name, metric = sl["name"], sl["metric"]
        a, b = per_item(pr, metric, slice_name=name), per_item(base, metric, slice_name=name)
        tag = f"slice:{name}/{metric}"
        verdict, note = "ok", ""
        if not a:
            verdict, note = "fail", "no rows in this slice"          # an empty slice must not pass silently
        else:
            val = sum(a.values()) / len(a)
            note = f"n={len(a)} mean {val:.3f}"
            if "min" in sl and val < sl["min"]:
                verdict = "fail"
            keys = sorted(set(a) & set(b))
            if sl.get("no_significant_drop") and keys:
                d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                                   n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
                note += f" Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
                if hi < 0:
                    verdict = "fail"
        if verdict != "ok" and sl.get("blocking"):
            blocked.append(tag)
        label = "ok" if verdict == "ok" else ("BLOCK" if sl.get("blocking") else "warn")
        lines.append(f"  {tag:<34} {note:<52} {json.dumps({k: sl[k] for k in ('min',) if k in sl}):<14} {label}")
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
