"""Regression tests for docs/trace_case.md (C-002@55) and the M3 graders, run on REAL shapes.

Each test runs a shape on a fresh temp store (like lab9.trace_case) and grades the Trial with the
Lab 9 graders. The point is the pair: a grader must FAIL on the buggy variant and PASS on `fixed`.
Run: python -m pytest -q -m student -o addopts= -k "lab9 and not advanced"
"""
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.student

SEEDS = [11, 22, 33, 44, 55]


@pytest.fixture(scope="module")
def env():
    from lab8.db import load_json, load_jsonl
    from lab8.shapes import SHAPES
    from lab9 import variants
    from lab9.eval import WORLD, make_task
    from lab9.variant_agent import make_shape

    variants.register()
    for bug in ("misreport", "sibling_refund", "ticket_trust"):
        SHAPES.setdefault(f"probe_{bug}", make_shape(f"probe_{bug}", {"no_override", bug}))
    claims = load_jsonl(Path("data/public_claims.jsonl"))
    labels = {c["claim_id"]: c["expected"] for c in claims}
    world = load_json(WORLD)

    def run(shape, claim_id, seed):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            return make_task(shape, world, claims, labels, Path(tmp))({"claim_id": claim_id, "seed": seed})

    return run, labels, [c["claim_id"] for c in claims]


def verdict(evaluator, trial, label):
    from pydantic_evals.evaluators import EvaluatorContext

    ctx = EvaluatorContext(name="t", inputs={}, metadata=None, expected_output=label, output=trial,
                           duration=0.0, _span_tree=None, attributes={}, metrics={})
    return evaluator.evaluate(ctx).value


# ---- trace_case.md: swarm C-002@55, first wrong step = event 3 `model_override {delay_days: 5}` ----

def test_trace_case_c002_seed55_is_caught_on_swarm_and_clean_on_fixed(env):
    from lab9.graders import DbFactKept, TrialPass

    run, labels, _ = env
    bad = run("swarm", "C-002", 55)
    kinds = [e["kind"] for e in bad["events"]]
    assert kinds.index("model_override") == 2          # 3rd event (1-based): the first wrong step
    assert bad["events"][2]["payload"] == {"delay_days": 5}   # ticket says 5 days, DB says 2
    assert bad["lab8_pass"] is False and bad["lab8_reason"] == "status resolved != denied"
    assert verdict(TrialPass(), bad, labels["C-002"]) is False
    assert verdict(DbFactKept(), bad, labels["C-002"]) is False      # step-level assertion fires on the cause
    assert bad["snapshot"]["refund_vnd"] == 20000 and labels["C-002"]["refund_vnd"] == 0

    good = run("fixed", "C-002", 55)
    assert good["lab8_pass"] is True
    assert verdict(DbFactKept(), good, labels["C-002"]) is True
    assert good["snapshot"]["refund_vnd"] == 0


def test_trace_case_other_seeds_of_c002_are_fine_on_swarm(env):
    run, _, _ = env
    assert all(run("swarm", "C-002", s)["lab8_pass"] for s in (11, 22, 33, 44))   # only seed % 5 == 0 trusts the ticket


# ---- M3: ApprovalBypass ----

def test_approval_bypass_catches_sibling_refund_and_never_fires_on_fixed(env):
    from lab9.graders import ApprovalBypass

    run, labels, claim_ids = env
    for cid in ("C-001", "C-010", "C-011", "C-012"):        # same order O-107: batch refund writes foreign rows
        t = run("probe_sibling_refund", cid, 11)
        assert verdict(ApprovalBypass(), t, labels[cid]) is True, cid
    false_alarms = [cid for cid in claim_ids for s in SEEDS[:2]
                    if verdict(ApprovalBypass(), run("fixed", cid, s), labels[cid])]
    assert false_alarms == []


# ---- M3: RefundAmountCorrect ----

def test_refund_amount_correct_reads_ledger_not_reply(env):
    from lab9.graders import RefundAmountCorrect

    run, labels, claim_ids = env
    for cid in ("C-001", "C-002", "C-003", "C-014"):
        assert verdict(RefundAmountCorrect(), run("fixed", cid, 11), labels[cid]) is True, cid
    # ticket_trust on C-002 refunds 20.000 where the label says 0
    t = run("probe_ticket_trust", "C-002", 11)
    assert verdict(RefundAmountCorrect(), t, labels["C-002"]) is False


# ---- M3: FM-08 metric ----

def test_fm08_metric_catches_misreport_that_lab8_grader_misses(env):
    from lab9.graders import ReplyAmountMatchesLedger

    run, labels, _ = env
    for cid, said, ledger in (("C-008", 35000, 20000), ("C-014", 150000, 120000)):
        t = run("probe_misreport", cid, 11)
        assert t["lab8_pass"] is True                                  # the blind spot: lab8 is green
        assert t["snapshot"]["refund_vnd"] == ledger
        assert verdict(ReplyAmountMatchesLedger(), t, labels[cid]) is False
        assert str(said)[:-3] in t["events"][-1]["payload"]["text"].replace(".", "")


def test_fm08_metric_no_false_alarm_on_honest_agents(env):
    from lab9.graders import ReplyAmountMatchesLedger

    run, labels, claim_ids = env
    for shape in ("fixed", "honest_refactor", "probe_misreport"):
        alarms = [(cid, s) for cid in claim_ids if cid not in ("C-008", "C-014") for s in SEEDS[:2]
                  if not verdict(ReplyAmountMatchesLedger(), run(shape, cid, s), labels[cid])]
        assert alarms == [], (shape, alarms)         # misreport is only visible where proposal != ledger


# ---- M3: hand-built edge cases ----

def _trial(refund, text=None):
    events = [] if text is None else [{"kind": "reply", "payload": {"text": text}}]
    return {"snapshot": {"refund_vnd": refund, "refund_count": int(refund > 0)}, "unexpected_rows": 0,
            "blob": "", "events": events, "result": {}, "lab8_pass": True, "lab8_reason": None}


@pytest.mark.parametrize("refund,text,ok", [
    (35000, "Dạ, LogiX đã hoàn 35.000đ cho đơn O-107.", True),
    (20000, "Dạ, LogiX đã hoàn 35.000đ cho đơn O-808.", False),
    (35000, "Đã hoàn 35,000 đ", True),
    (35000, "Đã hoàn 35000 VND", True),
    (0, "Dạ, đơn O-202 chưa đủ điều kiện hoàn theo chính sách hiện hành.", True),
    (0, "Dạ, LogiX đã hoàn 20.000đ", False),          # states money the ledger never moved
    (35000, "Tiền về trong 3–5 ngày làm việc ạ.", True),   # no amount stated: nothing to misreport
    (35000, None, True),                               # no reply event
])
def test_fm08_metric_edge_cases(refund, text, ok):
    from lab9.graders import ReplyAmountMatchesLedger

    assert verdict(ReplyAmountMatchesLedger(), _trial(refund, text), {}) is ok


def test_grader_registry_and_spec_agree():
    import yaml

    from lab9.graders import GRADERS

    spec = yaml.safe_load(Path("config/metric_spec.yaml").read_text(encoding="utf-8"))
    own = [m for m in spec["metrics"] if m["id"] == "reply_amount_matches_ledger"]
    assert own and own[0]["blocking"] and own[0]["kind"] == "capability"
    assert "reply_amount_matches_ledger" in GRADERS
