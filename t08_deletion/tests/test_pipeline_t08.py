"""Group tests — T08 pipeline (src/pipeline.py, src/baseline.py).

Success path: full 5-step run on the real t08 fixture (PoC + baseline).
Sanity: cascade tới mọi artefact dẫn xuất; legal hold CHƯA BAO GIỜ bị delete;
        zero false-delete ngoài scope request.
Failure/edge: request trỏ subject lạ; reviewed-file sai schema; override hành
        động ngoài vocabulary; duplicate request_id.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from src.baseline import run as run_baseline
from src.common.context import RunContext
from src.common.io import set_seed
from src.common.review import AuditLog, load_review_decisions
from src.pipeline import descendants, load, process, rank, request_scope, resolve_action, review, run as run_poc
from src.metrics.tracks import TRACKS

ROOT = Path(__file__).resolve().parents[1]
FX = ROOT / "fixtures" / "t08_deletion"
OWNER = "QA Lead — Group G00"
SPEC = TRACKS["t08"]


def make_ctx(tmp_path: Path, out_name: str = "out", pipeline_cfg: dict | None = None) -> RunContext:
    cfg = {"track": "t08", "owner": OWNER, "group_id": "G00", "strict_provenance": True,
           "params": {} if pipeline_cfg is None else pipeline_cfg}
    return RunContext(track="t08", input_dir=FX, output_dir=tmp_path / out_name, config=cfg,
                      seed=42, rng=set_seed(42), run_name="test", offline=True,
                      audit=AuditLog(tmp_path / out_name / "audit_log.jsonl"))


def load_graph() -> dict[str, dict]:
    g = json.loads((FX / "data_graph.json").read_text(encoding="utf-8"))
    return {a["artefact_id"]: a for a in g["artefacts"]}


# ---------------------------------------------------------------- success: full E2E
@pytest.mark.parametrize("runner", [run_poc, run_baseline], ids=["poc", "baseline"])
def test_t08_end_to_end(tmp_path, runner):
    ctx = make_ctx(tmp_path)
    runner(ctx)
    pred = pd.read_csv(tmp_path / "out" / "actions.csv")
    assert list(pred["request_id"].unique()) == [f"DR-{i:03d}" for i in range(1, 11)]
    assert {"request_id", "artefact_id", "action"} <= set(pred.columns)
    assert not pred.duplicated(["request_id", "artefact_id"]).any()      # eval _unique contract
    assert set(pred["action"]) <= {"delete", "retain_legal_hold", "escalate"}
    # 5-step contract artifacts
    q = pd.read_csv(tmp_path / "out" / "review_queue.csv")
    assert list(q["rank"]) == list(range(1, len(q) + 1))
    assert (tmp_path / "out" / "contact_sheet.html").exists()
    dec = json.loads((tmp_path / "out" / "decision.json").read_text(encoding="utf-8"))
    assert dec["owner"] == OWNER and dec["reason_code"] and dec["status"] in {"GO", "HOLD", "REWORK"}
    audit = (tmp_path / "out" / "audit_log.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert audit and all(json.loads(l)["actor"] for l in audit)
    # audit trace completeness precondition: mỗi artefact được action đều có 1 dòng audit
    logged = {json.loads(l)["item_id"] for l in audit}
    assert set(pred["artefact_id"]) <= logged


def test_t08_baseline_deletes_raw_only(tmp_path):
    """Handout §4 baseline: xóa 1 raw theo filename, không tìm derived/backup —
    và vì bỏ qua exception nên nó xóa nhầm cả 2 raw đang legal hold (failure mode 1)."""
    ctx = make_ctx(tmp_path)
    run_baseline(ctx)
    pred = pd.read_csv(tmp_path / "out" / "actions.csv")
    assert len(pred) == 10                                  # mỗi request đúng 1 dòng
    arts = load_graph()
    assert all(arts[r.artefact_id]["type"] == "raw_video" for r in pred.itertuples())
    assert pred["action"].eq("delete").all()
    held = {"a0043", "a0099"}
    assert held <= set(pred["artefact_id"])                 # legal hold bị xóa oan — failure mode 1
    assert set(pred["artefact_type"]) == {"raw_video"}      # không cascade


# ---------------------------------------------------------------- sanity: cascade + exceptions
def test_t08_cascade_legal_hold_and_no_false_delete(tmp_path):
    ctx = make_ctx(tmp_path)
    run_poc(ctx)
    pred = pd.read_csv(tmp_path / "out" / "actions.csv")
    arts = load_graph()
    reqs = json.loads((FX / "requests.json").read_text(encoding="utf-8"))
    children: dict[str, list[str]] = {}
    for a in arts.values():
        for p in a["parent_ids"]:
            children.setdefault(p, []).append(a["artefact_id"])

    # (1) cascade: mọi descendant của raw bị delete đều có action (delete khi không
    #     vướng exception, escalate khi đa chủ thể / origin lạ), không có dòng thiếu
    for r in pred[(pred.action == "delete") & (pred.artefact_type == "raw_video")].itertuples():
        for d in descendants([r.artefact_id], children):
            row = pred[(pred.request_id == r.request_id) & (pred.artefact_id == d)]
            assert len(row) == 1, (r.request_id, d)
            a = arts[d]
            clean = len(a["subject_ids"]) == 1 and a["origin_known"] and a["acl"] == ["data-platform"]
            assert row["action"].iloc[0] == ("delete" if clean else "escalate"), (r.request_id, d)

    # (2) legal hold: CHƯA BAO GIỜ delete; đúng 2 retain_legal_hold với reason LEGAL_HOLD
    held = {aid for aid, a in arts.items() if a["legal_hold"]}
    assert held == {"a0043", "a0099"}
    assert pred.loc[pred.artefact_id.isin(held) & (pred.action == "delete")].empty
    assert pred[pred.action == "retain_legal_hold"]["reason_code"].eq("LEGAL_HOLD").all()
    assert set(pred[pred.action == "retain_legal_hold"]["artefact_id"]) == held

    # (3) exception precedence trên 3 họ case của handout §4
    assert set(pred[pred.reason_code == "MULTI_SUBJECT"]["artefact_id"]) == {
        aid for aid, a in arts.items() if len(a["subject_ids"]) > 1
        and any(s in {r["subject_id"] for r in reqs} for s in a["subject_ids"])}
    assert set(pred[pred.reason_code == "UNKNOWN_ORIGIN"]["artefact_id"]) == {"a0075", "a0079", "a0083"}
    assert set(pred[pred.reason_code == "NO_DELETE_PERMISSION"]["artefact_id"]) == {"a0156"}

    # (4) zero false-delete ngoài scope: không delete nào chạm subject không có request
    requested = {r["subject_id"] for r in reqs}
    control = {"subj_00", "subj_02", "subj_06", "subj_10", "subj_13"}
    dels = pred[pred.action == "delete"]
    assert not any(control & set(arts[r.artefact_id]["subject_ids"]) for r in dels.itertuples())
    # ...và mọi delete đều nằm trong scope của đúng request của nó
    for r in dels.itertuples():
        assert set(arts[r.artefact_id]["subject_ids"]) & requested
    # mỗi request phủ đủ mọi artefact của subject của mình
    rows = set(zip(pred.request_id, pred.artefact_id))
    for req in reqs:
        for aid, a in arts.items():
            if req["subject_id"] in a["subject_ids"]:
                assert (req["request_id"], aid) in rows, (req["request_id"], aid)

    # (5) audit trace: 100% action có dòng audit (item_id = artefact_id)
    audit = [json.loads(l) for l in (tmp_path / "out" / "audit_log.jsonl").read_text(encoding="utf-8").splitlines()]
    logged = {e["item_id"] for e in audit}
    assert set(pred["artefact_id"]) <= logged
    assert any(e["action"] == "dry_run_simulation" for e in audit)


def test_t08_scope_includes_cross_tree_multi_subject_artefacts(tmp_path):
    """Frame/annotation đa chủ thể và training_export treo trên cây lineage của
    subject khác vẫn phải nằm trong scope (dữ liệu cá nhân của subject)."""
    ctx = make_ctx(tmp_path)
    data = load(ctx)
    arts, children = data["artefacts"], None
    children = {"a0001": ["a0011"], "a0011": ["a0014"]}          # a0011/a0014 (subj_00+subj_01) treo trên raw của subj_00
    scope = request_scope({"subject_id": "subj_01", "identity_verified": True}, arts, children)
    assert "a0011" in scope and "a0014" in scope                 # qua subject_ids
    assert descendants(["a0012"], {}) == set()                   # BFS rỗng thì không thêm gì
    # unit precedence: mọi nhánh của resolve_action
    art = arts[arts.artefact_id == "a0001"].iloc[0]
    req = {"request_id": "DR-001", "subject_id": "subj_01", "identity_verified": True}
    assert resolve_action(art, req, {"operator_acl": "data-platform", "require_identity_verified": True})[1] == "IN_SCOPE_NO_EXCEPTION"
    held = arts[arts.artefact_id == "a0043"].iloc[0]
    assert resolve_action(held, req, {"operator_acl": "data-platform", "require_identity_verified": True})[1] == "LEGAL_HOLD"
    noacl = arts[arts.artefact_id == "a0156"].iloc[0]
    assert resolve_action(noacl, req, {"operator_acl": "data-platform", "require_identity_verified": True})[1] == "NO_DELETE_PERMISSION"
    unkn = arts[arts.artefact_id == "a0075"].iloc[0]
    assert resolve_action(unkn, req, {"operator_acl": "data-platform", "require_identity_verified": True})[1] == "UNKNOWN_ORIGIN"
    multi = arts[arts.artefact_id == "a0011"].iloc[0]
    assert resolve_action(multi, req, {"operator_acl": "data-platform", "require_identity_verified": True})[1] == "MULTI_SUBJECT"


# ---------------------------------------------------------------- failure / edge
def _make_fixture(tmp_path: Path, requests_payload: list[dict]) -> Path:
    d = tmp_path / "fixture"
    d.mkdir()
    shutil.copy(FX / "data_graph.json", d / "data_graph.json")
    (d / "requests.json").write_text(json.dumps(requests_payload), encoding="utf-8")
    return d


def test_t08_unknown_subject_fails_loudly(tmp_path):
    """Request trỏ tới subject không có trong graph → ValueError (exit 2 qua src.main)."""
    ctx = make_ctx(tmp_path, pipeline_cfg={})
    bad = _make_fixture(tmp_path, [{"request_id": "DR-999", "subject_id": "subj_zz",
                                    "deadline": "2026-12-01", "identity_verified": True}])
    from src.common.review import AuditLog
    ctx.input_dir = bad
    ctx.audit = AuditLog(ctx.output_dir / "audit_log.jsonl")
    with pytest.raises(ValueError, match="subj_zz"):
        run_poc(ctx)


def test_t08_duplicate_request_id_fails_loudly(tmp_path):
    from src.common.review import AuditLog
    ctx = make_ctx(tmp_path)
    dup = [{"request_id": "DR-001", "subject_id": "subj_01", "deadline": "2026-12-01", "identity_verified": True}] * 2
    ctx.input_dir = _make_fixture(tmp_path, dup)
    ctx.audit = AuditLog(ctx.output_dir / "audit_log.jsonl")
    with pytest.raises(ValueError, match="duplicate request_id"):
        run_poc(ctx)


def test_t08_reviewed_file_rejects_bad_rows(tmp_path):
    p = tmp_path / "reviewed.csv"
    base = {"rank": [1], "item_id": ["DR-001::a0011"], "score": [1.0], "reason": [""], "suggested_action": [""],
            "slice": [""], "evidence_path": [""], "override_value": [""], "review_note": [""], "reviewed_at": [""]}
    # unknown decision value
    pd.DataFrame(base | {"reviewer": ["an"], "reviewer_decision": ["maybe"]}).to_csv(p, index=False)
    with pytest.raises(ValueError, match="reviewer_decision"):
        load_review_decisions(p)
    # decision without an owner
    pd.DataFrame(base | {"reviewer": [""], "reviewer_decision": ["approve"]}).to_csv(p, index=False)
    with pytest.raises(ValueError, match="reviewer"):
        load_review_decisions(p)


def test_t08_review_override_changes_action_and_audits(tmp_path):
    ctx = make_ctx(tmp_path)
    data = load(ctx)
    scored = process(ctx, data)
    queue = rank(ctx, scored)
    top_id = queue.iloc[0]["item_id"]                      # 'DR-001::a0011' (escalate MULTI_SUBJECT)
    reviewed = queue.head(1).copy()
    reviewed["reviewer"], reviewed["reviewer_decision"] = "tester", "edit"
    reviewed["override_value"] = "delete"                  # human quyết định xóa bỏ multi-subject frame
    reviewed["review_note"] = "frame đã tách được theo subject, DPO duyệt xóa"
    reviewed.to_csv(ctx.output_dir / "review_queue_reviewed.csv", index=False)
    final = review(ctx, scored, queue)
    rid, aid = top_id.split("::")
    row = final[(final.request_id == rid) & (final.artefact_id == aid)].iloc[0]
    assert row["action"] == "delete"
    assert ctx.stats.get("n_overridden") == 1
    log = [json.loads(l) for l in (ctx.output_dir / "audit_log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(e["action"] == "override:edit" and e["actor"] == "tester" for e in log)


def test_t08_review_override_unknown_action_rejected(tmp_path):
    ctx = make_ctx(tmp_path)
    data = load(ctx)
    scored = process(ctx, data)
    queue = rank(ctx, scored)
    reviewed = queue.head(1).copy()
    reviewed["reviewer"], reviewed["reviewer_decision"] = "tester", "edit"
    reviewed["override_value"] = "purge_everything"        # ngoài vocabulary của T08
    reviewed.to_csv(ctx.output_dir / "review_queue_reviewed.csv", index=False)
    with pytest.raises(ValueError, match="unknown actions"):
        review(ctx, scored, queue)


def test_t08_decision_hold_when_exceptions_exist(tmp_path):
    ctx = make_ctx(tmp_path)
    run_poc(ctx)
    dec = json.loads((ctx.output_dir / "decision.json").read_text(encoding="utf-8"))
    assert dec["status"] == "HOLD" and dec["reason_code"] == "EXCEPTIONS_NEED_HUMAN_SIGNOFF"
    assert dec["metrics"]["dry_run"] is True               # không bao giờ thực thi thật trong lab
