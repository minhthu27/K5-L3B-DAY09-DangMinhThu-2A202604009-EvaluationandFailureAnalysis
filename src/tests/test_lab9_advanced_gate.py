"""M5-M7 on the committed reports: slice gate, integrity/ratchet, traps, fm_map, eval set v2.
Run: python -m pytest -q -m student -o addopts= -k advanced   (needs reports/baseline_v2.json)"""
import copy
import json
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.student
SPEC_V2 = Path("config/metric_spec_v2.yaml")
BASE_V2 = Path("reports/baseline_v2.json")
SLICE_REG = Path("reports/eval_slice_regression.json")

needs_reports = pytest.mark.skipif(not (BASE_V2.is_file() and SLICE_REG.is_file()), reason="v2 reports not generated")


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def spec():
    return yaml.safe_load(SPEC_V2.read_text(encoding="utf-8"))


def test_fm_map_covers_every_mechanism_with_a_real_code():
    import re

    mapping = yaml.safe_load(Path("config/fm_map.yaml").read_text(encoding="utf-8"))
    mechanisms = {m for c in load("data/eval/adversarial_v2.json")["claims"] for m in c["mechanisms"]}
    assert mechanisms <= set(mapping)
    assert all(re.fullmatch(r"FM-\d{2}", str(mapping[m])) for m in mechanisms)


def test_eval_set_v2_is_clean():
    from lab9 import evalset

    assert evalset.check(evalset.ITEMS_V2, evalset.LABELS_V2) == []


def test_spec_v2_has_reliability_and_slice_gates(spec):
    by_id = {m["id"]: m for m in spec["metrics"]}
    assert by_id["pass_hat_5"]["blocking"] and by_id["pass_hat_5"]["threshold"]["min"] >= 0.8
    assert by_id["reply_amount_matches_ledger"]["blocking"]
    slices = {s["name"]: s for s in spec["slices"]}
    assert slices["money_movement"]["blocking"] and slices["adversarial"]["blocking"]


@needs_reports
def test_baseline_merges_against_itself_and_aa_floor_is_met(spec):
    from lab9 import gate

    base = load(BASE_V2)
    blocked, _ = gate.check(base, base, spec, spec)
    assert blocked == []
    assert base["metrics"]["pass_hat_5"] >= 0.8 + 0.05      # floor 0.85 must sit under the baseline with room


@needs_reports
def test_slice_regression_is_blocked_by_the_money_movement_slice(spec):
    from lab9 import gate

    blocked, _ = gate.check(load(SLICE_REG), load(BASE_V2), spec)
    assert "slice:money_movement/pass_rate" in blocked


@needs_reports
def test_every_public_trap_is_blocked(spec):
    from lab9 import gate, traps

    base, pr = load(BASE_V2), load(SLICE_REG)
    for name, (rep, loose) in traps.build(pr, spec).items():
        blocked, _ = gate.check(rep, base, loose or spec, spec if loose else None)
        assert blocked, f"trap {name} leaked"


@needs_reports
@pytest.mark.parametrize("name", ["hand_edited_metrics", "relabelled_slices", "duplicated_rows", "failures_listed"])
def test_extra_traps_are_blocked_by_integrity(spec, name):
    from lab9 import gate

    base, rep = load(BASE_V2), copy.deepcopy(load(SLICE_REG))
    if name == "hand_edited_metrics":
        rep["metrics"].update(pass_rate=1.0, refund_amount_correct=1.0, pass_hat_5=1.0)
    elif name == "relabelled_slices":
        for r in rep["rows"]:
            if "money_movement" in r["slices"]:
                r["slices"] = []
    elif name == "duplicated_rows":
        good = next(r for r in rep["rows"] if r["pass_rate"])
        rep["rows"] = [copy.deepcopy(good) if r["pass_rate"] is False else r for r in rep["rows"]]
    else:
        rep["evaluator_failures"] = ["grader crashed"]
    blocked, _ = gate.check(rep, base, spec)
    assert blocked == ["integrity"]


def test_ratchet_rejects_each_kind_of_loosening(spec):
    from lab9 import gate

    def loosen(fn):
        s = copy.deepcopy(spec)
        fn(s)
        return gate._ratchet(s, spec)

    pr = next(m for m in spec["metrics"] if m["id"] == "pass_rate")
    assert gate._ratchet(spec, spec) == []
    assert loosen(lambda s: s["metrics"][[m["id"] for m in s["metrics"]].index("pass_rate")]["threshold"].update(min=0.5))
    assert loosen(lambda s: s["metrics"][[m["id"] for m in s["metrics"]].index("pass_rate")]["threshold"].pop("no_significant_drop"))
    assert loosen(lambda s: s["metrics"][[m["id"] for m in s["metrics"]].index("pass_hat_5")].update(blocking=False))
    assert loosen(lambda s: s.update(metrics=[m for m in s["metrics"] if m["id"] != "pass_hat_5"]))
    assert loosen(lambda s: s["slices"].pop())
    assert loosen(lambda s: s["slices"][0].update(min=0.1))
    assert pr["threshold"]["min"] == 0.95           # sanity: the spec under test is the strict one


def test_ratchet_is_skipped_for_a_new_eval_set_version():
    from lab9 import gate

    base = {"manifest": {"items": 1, "seeds": [1]}, "rows": [{"item_id": "a", "seed": 1, "pass_rate": True, "slices": []}],
            "metrics": {"pass_rate": 1.0}, "evaluator_failures": []}
    old = {"eval_set_version": "v1", "metrics": [{"id": "pass_rate", "kind": "capability", "blocking": True, "threshold": {"min": 0.9}}]}
    new = {"eval_set_version": "v2", "metrics": [{"id": "pass_rate", "kind": "capability", "blocking": True, "threshold": {"min": 0.5}}]}
    assert gate.integrity_check(base, base, new, old) == []
