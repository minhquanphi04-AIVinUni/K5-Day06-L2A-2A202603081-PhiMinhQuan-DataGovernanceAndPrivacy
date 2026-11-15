"""T08 *baseline* (group-owned): xóa 1 bản raw theo filename, không tìm derived/backup.

Đúng mức handout đặt ra cho T08 (§4): "Baseline: Xóa một bản raw theo filename,
không tìm derived/backup". Với mỗi delete request, baseline chỉ tìm ĐÚNG MỘT artefact
`raw_video` của subject (khớp filename `*<subject_id>*`) và trả action=delete cho nó.
Không cascade, không backup, không exception (legal hold / permission / multi-subject
đều bị bỏ qua) — chính vì thế baseline đâm trúng cả 2 failure mode bắt buộc phân tích
của handout §5 (xóa nhầm dữ liệu đang legal hold; chỉ xóa raw nhưng giữ bản sao +
embedding định danh). PoC (src/pipeline.py) phải thắng baseline trên cùng pool/seed.

    python -m src.main --track t08 --pipeline src.baseline --output out/baseline --run-name baseline --offline --seed 42
"""
from __future__ import annotations

import json
import pandas as pd

from .common.contact_sheet import render_contact_sheet
from .common.context import RunContext
from .common.decision import Decision, write_decision
from .common.manifest import verify_manifest
from .common.review import apply_overrides, load_review_decisions, make_review_queue, write_review_queue
from .metrics.tracks import get_track

REQUIRED_ART_COLS = ["artefact_id", "type", "subject_ids", "parent_ids", "uri", "owner", "acl",
                     "legal_hold", "origin_known"]
REQUIRED_REQ_COLS = ["request_id", "subject_id", "deadline", "identity_verified"]

# Baseline giữ nguyên hình dạng 5 bước nhưng tham số tối giản (frozen trong configs/lab.yaml).
DEFAULTS = {
    "budget": 20,
    "dry_run": True,
    "decision": {},   # baseline luôn HOLD BASELINE_ONLY
}


def load(ctx: RunContext) -> dict:
    manifest = ctx.input_dir / "manifest.csv"
    if manifest.exists():
        problems = verify_manifest(manifest)
        if problems:
            if ctx.config.get("strict_provenance", True):
                raise ValueError(f"provenance check failed ({len(problems)} problems), first: {problems[:3]}")
    graph = json.loads((ctx.input_dir / "data_graph.json").read_text(encoding="utf-8"))
    requests = json.loads((ctx.input_dir / "requests.json").read_text(encoding="utf-8"))
    arts = pd.DataFrame(graph.get("artefacts", []))
    reqs = pd.DataFrame(requests)
    arts["legal_hold"] = arts["legal_hold"].where(arts["legal_hold"].notna(), None)  # null -> None (NaN là truthy!)
    missing = [c for c in REQUIRED_ART_COLS if c not in arts.columns]
    if missing:
        raise ValueError(f"data_graph.json missing required artefact fields {missing}")
    missing = [c for c in REQUIRED_REQ_COLS if c not in reqs.columns]
    if missing:
        raise ValueError(f"requests.json missing required request fields {missing}")
    ctx.audit.log(actor="baseline", action="load", item_id=str(ctx.input_dir),
                  after={"n_artefacts": len(arts), "n_requests": len(reqs)})
    return {"artefacts": arts, "requests": reqs}


def process(ctx: RunContext, data: dict) -> pd.DataFrame:
    p = {**DEFAULTS, **(ctx.config.get("params") or {})}
    arts, reqs = data["artefacts"], data["requests"]
    rows = []
    for req in reqs.to_dict(orient="records"):
        subj = req["subject_id"]
        mine = arts[arts["subject_ids"].map(lambda ids: subj in (ids or []))]
        raws = mine[mine["type"] == "raw_video"]
        if len(raws) == 0:
            # baseline không biết làm gì hơn: bỏ qua (request sẽ bị thiếu prediction)
            ctx.audit.log(actor="baseline", action="request_skipped", item_id=req["request_id"],
                          reason=f"no raw_video found for subject {subj}")
            continue
        art = raws.iloc[0]
        # KHÔNG exception, KHÔNG cascade: xóa raw dù legal_hold / acl / multi-subject.
        rows.append({
            "request_id": req["request_id"], "artefact_id": art["artefact_id"], "action": "delete",
            "item_id": f"{req['request_id']}::{art['artefact_id']}", "score": 1.0,
            "reason_code": "BASELINE_RAW_ONLY",
            "reason": f"baseline: xóa raw khớp filename '{art.get('filename', '')}' — không tìm derived/backup, không xét exception",
            "artefact_type": art["type"], "uri": art["uri"],
            "subject_ids": ";".join(sorted(art["subject_ids"] or [])),
            "legal_hold": str(art["legal_hold"]) if art["legal_hold"] is not None and not pd.isna(art["legal_hold"]) else "", "subject_id": subj,
        })
        if p["dry_run"]:
            ctx.audit.log(actor="baseline", action="dry_run_simulation", item_id=art["artefact_id"],
                          after="delete", reason="baseline raw-only (mock artefact, không xoá thật)")
        ctx.audit.log(actor="baseline", action="resolve:delete", item_id=art["artefact_id"],
                      after="delete", reason="BASELINE_RAW_ONLY: xóa 1 raw, bỏ qua derived/backup/exception",
                      request_id=req["request_id"], subject_id=subj)
    out = pd.DataFrame(rows).sort_values(["request_id", "artefact_id"], kind="mergesort").reset_index(drop=True)
    ctx.stats["n_requests"] = int(len(reqs))
    ctx.stats["n_actions"] = len(out)
    ctx.stats["n_delete"] = int((out["action"] == "delete").sum())
    ctx.stats["n_retain_legal_hold"] = 0        # baseline không biết legal hold
    ctx.stats["n_escalate"] = 0                 # baseline không biết escalate
    ctx.stats["n_delete_cascade"] = 0           # baseline không cascade
    return out


def rank(ctx: RunContext, scored: pd.DataFrame) -> pd.DataFrame:
    p = {**DEFAULTS, **(ctx.config.get("params") or {})}
    if "score" not in scored.columns:
        raise ValueError("rank(): process() output needs a numeric `score` column (higher = review first)")
    q = make_review_queue(scored, id_col="item_id", score_col="score", budget=int(p["budget"]),
                          reason_col="reason", action_col="action", slice_col="artefact_type",
                          evidence_col="uri")
    write_review_queue(q, ctx.out("review_queue.csv"))
    render_contact_sheet(q, ctx.out("contact_sheet.html"), image_col=None,
                         fields=("rank", "item_id", "score", "reason", "suggested_action", "slice"),
                         title=f"t08 baseline (raw-only, no cascade) — review queue ({ctx.run_name})")
    return q


def review(ctx: RunContext, scored: pd.DataFrame, queue: pd.DataFrame) -> pd.DataFrame:
    reviewed_path = ctx.output_dir / "review_queue_reviewed.csv"
    if reviewed_path.exists():
        reviewed = load_review_decisions(reviewed_path)
        ctx.stats["n_reviewed"] = len(reviewed)
        return apply_overrides(scored, reviewed, id_col="item_id", value_col="action", audit=ctx.audit)
    return scored


def decide(ctx: RunContext, final: pd.DataFrame) -> Decision:
    owner = ctx.config.get("owner") or "TODO-owner"
    return Decision(status="HOLD", reason_code="BASELINE_ONLY",
                    reason=f"Baseline xóa mỗi request đúng 1 raw theo filename (n={len(final)}), bỏ qua "
                           f"derived/backup và mọi exception — chỉ dùng để đối chiếu, không bao giờ thực thi.",
                    owner=owner, scope=ctx.track,
                    evidence=[str(ctx.out("review_queue.csv"))],
                    metrics={"n_actions": len(final), "n_delete": ctx.stats.get("n_delete", 0)})


def run(ctx: RunContext) -> None:
    spec = get_track(ctx.track)
    data = load(ctx)
    scored = process(ctx, data)
    ctx.stats["n_items"] = len(scored)
    queue = rank(ctx, scored)
    final = review(ctx, scored, queue)
    cols = spec.pred_columns + [c for c in final.columns if c not in spec.pred_columns]
    final[cols].to_csv(ctx.out(spec.pred_file), index=False)
    write_decision(decide(ctx, final), ctx.out("decision.json"), ctx.out("decision_log.csv"))
