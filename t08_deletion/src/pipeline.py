"""PoC pipeline — T08 Retention & Delete Request Simulator (group G00).

Ý tưởng: một delete request (DR-xxx) trỏ tới một *subject*, không trỏ tới một file.
Xoá đúng nghĩa vụ của Art.17 / Nghị định 356/2025 (mô phỏng) nghĩa là phải chạm tới
TOÀN BỘ dữ liệu định danh được subject đó: raw → backup, frame → face_crop →
embedding, annotation, training_export. Vì vậy bộ giải (resolver) làm 2 việc:

  1. SCOPE  — toàn bộ artefact có subject trong `subject_ids` (dữ liệu cá nhân
     nằm rải rác trên nhiều cây lineage: frame đa chủ thể, training_export đa
     cha) **cộng với** mọi descendant tìm được bằng BFS qua cạnh parent→child
     từ các raw của subject (cascade theo graph edges).
  2. PRECEDENCE — mỗi artefact trong scope được xét theo thứ tự ưu tiên
     frozen (T08.md §8): legal hold > permission > unknown origin > identity
     chưa verify > multi-subject > delete. Hành động nào cũng có reason_code.

Mọi quyết định delete/retain_legal_hold/escalate đều được append vào
`out/audit_log.jsonl` (AuditLog, append-only, item_id = artefact_id) để chấm
audit completeness. `dry_run: true` — chỉ simulate, không xoá dữ liệu thật
(fixture là mock URI `mock://...`).

Lab contract (5 bước, chạy bởi `python -m src.main ...`):
    01 load      verify manifest sha256 + parse data_graph.json + requests.json
    02 process   resolver: scope + cascade BFS + precedence → actions
    03 rank      top-B review queue: exception/risky actions trước
    04 review    apply override từ review_queue_reviewed.csv + audit log
    05 decide    GO / HOLD / REWORK (exception policy frozen) + reason_code + owner

Output: out/actions.csv (request_id, artefact_id, action ∈ {delete,
retain_legal_hold, escalate}) — không đọc fixtures/*/gt (GT guard).
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

# Defaults; mọi giá trị đều có thể override (và đã frozen) trong configs/lab.yaml.
DEFAULTS = {
    "budget": 20,
    "dry_run": True,                  # chỉ simulate trên artefact mock
    "operator_acl": "data-platform",  # operator xóa phải có ACL này trên artefact
    "require_identity_verified": True,
    "decision": {"go_max_exceptions": 0, "rework_if_unresolved_requests": True},
}

ACTIONS = {"delete", "retain_legal_hold", "escalate"}


# ---------------------------------------------------------------- 01 load
def load(ctx: RunContext) -> dict:
    manifest = ctx.input_dir / "manifest.csv"
    if manifest.exists():
        problems = verify_manifest(manifest)
        if problems:
            ctx.audit.log(actor="pipeline", action="provenance_problems", item_id=str(manifest), after=problems[:20])
            if ctx.config.get("strict_provenance", True):
                raise ValueError(f"provenance check failed ({len(problems)} problems), first: {problems[:3]}")
        data_manifest = pd.read_csv(manifest)
    else:
        data_manifest = None

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
    if len(arts) == 0 or len(reqs) == 0:
        raise ValueError("data_graph.json / requests.json are empty — nothing to resolve")
    if reqs["request_id"].duplicated().any():
        dup = reqs.loc[reqs["request_id"].duplicated(), "request_id"].tolist()
        raise ValueError(f"requests.json has duplicate request_id {dup}")
    subjects = {s for ids in arts["subject_ids"] for s in ids}
    unknown = sorted(set(reqs["subject_id"]) - subjects)
    if unknown:
        raise ValueError(f"requests reference subjects not present in data_graph.json: {unknown}")
    if ctx.audit:
        ctx.audit.log(actor="pipeline", action="load", item_id=str(ctx.input_dir),
                      after={"n_artefacts": len(arts), "n_requests": len(reqs)})
    return {"artefacts": arts, "requests": reqs, "manifest": data_manifest, "graph_note": graph.get("note", "")}


# ---------------------------------------------------------------- 02 process (resolver)
def build_children(arts: pd.DataFrame) -> dict[str, list[str]]:
    """parent_ids -> children (cạnh của lineage graph, dùng cho BFS cascade)."""
    ch: dict[str, list[str]] = {}
    for aid, parents in zip(arts["artefact_id"], arts["parent_ids"]):
        for p in parents or []:
            ch.setdefault(p, []).append(aid)
    return ch


def descendants(roots: list[str], children: dict[str, list[str]]) -> set[str]:
    """Mọi node đạt được từ `roots` bằng BFS qua cạnh parent→child (không gồm roots)."""
    seen: set[str] = set()
    stack = list(roots)
    while stack:
        x = stack.pop()
        for c in children.get(x, []):
            if c not in seen:
                seen.add(c)
                stack.append(c)
    return seen


def request_scope(req: dict, arts: pd.DataFrame, children: dict[str, list[str]]) -> list[str]:
    """Scope xoá của 1 request = (a) raw của subject, (b) cascade BFS qua graph edges,
    (c) mọi artefact khác có subject trong `subject_ids` (frame đa chủ thể,
    training_export đa cha nằm trên cây lineage của subject khác)."""
    subj = req["subject_id"]
    mine = arts[arts["subject_ids"].map(lambda ids: subj in (ids or []))]
    raws = sorted(mine.loc[mine["type"] == "raw_video", "artefact_id"])
    scope = descendants(raws, children)          # (b) cascade
    scope |= set(mine["artefact_id"])            # (a)+(c) mọi dữ liệu của subject
    return sorted(scope | set(raws))


def _held(v) -> bool:
    """legal_hold thực sự đang bật? (pandas str-dtype để null thành NaN — NaN là truthy!)"""
    return v is not None and not (isinstance(v, float) and pd.isna(v)) and str(v).strip().lower() not in ("", "none", "nan")


def resolve_action(art: pd.Series, req: dict, params: dict) -> tuple[str, str, str]:
    """Precedence frozen (T08.md §8): legal hold > permission > unknown origin >
    identity > multi-subject > delete. Trả về (action, reason_code, reason)."""
    acl = list(art["acl"] or [])
    if _held(art["legal_hold"]):
        return ("retain_legal_hold", "LEGAL_HOLD",
                f"artefact đang giữ legal_hold={art['legal_hold']} (nghĩa vụ pháp lý, GDPR Đ.17(3)); "
                f"hẹn xoá khi hết hold, không xoá ngay")
    if params["operator_acl"] not in acl:
        return ("escalate", "NO_DELETE_PERMISSION",
                f"acl={acl} không cấp quyền xóa cho operator '{params['operator_acl']}'")
    if not art["origin_known"]:
        return ("escalate", "UNKNOWN_ORIGIN",
                "origin_known=false: không rõ nguồn gốc dữ liệu, không tự ý xoá")
    if params["require_identity_verified"] and not req.get("identity_verified", False):
        return ("escalate", "IDENTITY_NOT_VERIFIED",
                "request chưa được xác minh danh tính (identity_verified=false)")
    if len(art["subject_ids"] or []) > 1:
        return ("escalate", "MULTI_SUBJECT",
                f"artefact chứa dữ liệu của {len(art['subject_ids'])} chủ thể {sorted(art['subject_ids'])}; "
                "xoá sẽ ảnh hưởng subject không có trong request — cần người duyệt")
    return ("delete", "IN_SCOPE_NO_EXCEPTION", "thuộc scope của request, không vướng exception")


def process(ctx: RunContext, data: dict) -> pd.DataFrame:
    p = {**DEFAULTS, **(ctx.config.get("params") or {})}
    p["decision"] = {**DEFAULTS["decision"], **(p.get("decision") or {})}
    arts, reqs = data["artefacts"], data["requests"]
    art_by_id = {r["artefact_id"]: r for r in arts.to_dict(orient="records")}
    children = build_children(arts)
    rows = []
    cascade_deleted = 0          # delete trên artefact dẫn xuất (không phải raw trực tiếp)
    raws_deleted = 0
    unresolved = 0               # request không có artefact nào trong scope
    exc_by_code: dict[str, int] = {}
    for req in reqs.to_dict(orient="records"):
        scope = request_scope(req, arts, children)
        if not scope:
            unresolved += 1
            ctx.audit.log(actor="pipeline", action="request_unresolved", item_id=req["request_id"],
                          reason=f"subject {req['subject_id']} has no artefact in data_graph")
            continue
        for aid in scope:
            art = art_by_id[aid]
            action, code, reason = resolve_action(art, req, p)
            if action == "delete":
                if art["type"] == "raw_video":
                    raws_deleted += 1
                else:
                    cascade_deleted += 1
            else:
                exc_by_code[code] = exc_by_code.get(code, 0) + 1
            rows.append({
                "request_id": req["request_id"], "artefact_id": aid, "action": action,
                "item_id": f"{req['request_id']}::{aid}",       # id duy nhất cho review queue
                "score": 1.0 if action == "escalate" else (0.9 if action == "retain_legal_hold" else 0.5),
                "reason_code": code, "reason": reason,
                "artefact_type": art["type"], "uri": art["uri"],
                "subject_ids": ";".join(sorted(art["subject_ids"] or [])),
                "legal_hold": art["legal_hold"] if _held(art["legal_hold"]) else "",
                "subject_id": req["subject_id"],
            })
    out = pd.DataFrame(rows).sort_values(["request_id", "artefact_id"], kind="mergesort").reset_index(drop=True)
    dup = out[out.duplicated(["request_id", "artefact_id"], keep=False)]
    if len(dup):
        raise ValueError(f"resolver produced duplicate (request_id, artefact_id) rows: {dup[['request_id', 'artefact_id']].head().values.tolist()}")
    bad = set(out["action"]) - ACTIONS
    if bad:
        raise ValueError(f"resolver produced unknown actions {sorted(bad)}; allowed={sorted(ACTIONS)}")

    ctx.stats["n_requests"] = int(len(reqs))
    ctx.stats["n_unresolved_requests"] = unresolved
    ctx.stats["n_actions"] = len(out)
    ctx.stats["n_delete"] = int((out["action"] == "delete").sum())
    ctx.stats["n_retain_legal_hold"] = int((out["action"] == "retain_legal_hold").sum())
    ctx.stats["n_escalate"] = int((out["action"] == "escalate").sum())
    ctx.stats["n_delete_raw"] = raws_deleted
    ctx.stats["n_delete_cascade"] = cascade_deleted
    ctx.stats["n_exceptions"] = int((out["action"] != "delete").sum())
    ctx.stats["exceptions_by_reason_code"] = exc_by_code
    ctx.stats["dry_run"] = bool(p["dry_run"])
    if p["dry_run"]:
        ctx.audit.log(actor="pipeline", action="dry_run_simulation", item_id="all",
                      after={"note": data["graph_note"] or "mock artefacts — nothing real is deleted",
                             "n_would_delete": int((out["action"] == "delete").sum())})
    # evidence: deletion plan (mock) — bước 5 của handout "Delete mock + evidence"
    out[out["action"] == "delete"][["request_id", "artefact_id", "action", "artefact_type", "uri"]].to_csv(
        ctx.out("deletion_plan.csv"), index=False)
    # audit: mỗi hành động 1 dòng JSONL (item_id = artefact_id để chấm audit completeness)
    for r in out.to_dict(orient="records"):
        ctx.audit.log(actor="pipeline", action=f"resolve:{r['action']}", item_id=r["artefact_id"],
                      after=r["action"], reason=f"{r['reason_code']}: {r['reason']}",
                      request_id=r["request_id"], subject_id=r["subject_id"])
    return out


# ---------------------------------------------------------------- 03 rank
def rank(ctx: RunContext, scored: pd.DataFrame) -> pd.DataFrame:
    p = {**DEFAULTS, **(ctx.config.get("params") or {})}
    if "score" not in scored.columns:
        raise ValueError("rank(): process() output needs a numeric `score` column (higher = review first)")
    q = make_review_queue(scored, id_col="item_id", score_col="score", budget=int(p["budget"]),
                          reason_col="reason", action_col="action", slice_col="reason_code",
                          evidence_col="uri")
    write_review_queue(q, ctx.out("review_queue.csv"))
    # contact sheet offline (không có ảnh — artefact là mock URI; trang HTML vẫn có
    # dropdown quyết định + nút Export CSV cho reviewer)
    render_contact_sheet(q, ctx.out("contact_sheet.html"), image_col=None,
                         fields=("rank", "item_id", "score", "reason", "suggested_action", "slice"),
                         title=f"t08 Delete Request Simulator — review queue ({ctx.run_name})")
    return q


# ---------------------------------------------------------------- 04 review
def review(ctx: RunContext, scored: pd.DataFrame, queue: pd.DataFrame) -> pd.DataFrame:
    """Nếu tồn tại out/review_queue_reviewed.csv: reviewer có thể `edit` hành động
    (vd: quyết định xóa/giữ khác sau khi xem bằng chứng). Mọi thay đổi vào audit log."""
    reviewed_path = ctx.output_dir / "review_queue_reviewed.csv"
    if not reviewed_path.exists():
        return scored
    reviewed = load_review_decisions(reviewed_path)
    ctx.stats["n_reviewed"] = len(reviewed)
    final = apply_overrides(scored, reviewed, id_col="item_id", value_col="action", audit=ctx.audit)
    changed = (final["action"] != scored["action"]).sum()
    ctx.stats["n_overridden"] = int(changed)
    bad = set(final["action"]) - ACTIONS
    if bad:
        raise ValueError(f"reviewer override produced unknown actions {sorted(bad)}; allowed={sorted(ACTIONS)}")
    return final


# ---------------------------------------------------------------- 05 decide
def decide(ctx: RunContext, final: pd.DataFrame) -> Decision:
    p = {**DEFAULTS, **(ctx.config.get("params") or {})}
    d = {**DEFAULTS["decision"], **(p.get("decision") or {})}
    owner = ctx.config.get("owner") or "TODO-owner"
    n_exc = int((final["action"] != "delete").sum())
    n_del = int((final["action"] == "delete").sum())
    evidence = [str(ctx.out("review_queue.csv")), str(ctx.out("deletion_plan.csv"))]
    metrics = {"n_requests": ctx.stats.get("n_requests"), "n_actions": len(final),
               "n_delete": n_del, "n_exceptions": n_exc,
               "exceptions_by_reason_code": ctx.stats.get("exceptions_by_reason_code", {}),
               "dry_run": ctx.stats.get("dry_run", True)}
    if d["rework_if_unresolved_requests"] and ctx.stats.get("n_unresolved_requests", 0) > 0:
        return Decision(status="REWORK", reason_code="REQUEST_SUBJECT_NOT_IN_GRAPH",
                        reason=f"{ctx.stats['n_unresolved_requests']} request trỏ tới subject không có trong data_graph — không thể xử lý, cần làm lại dữ liệu đầu vào.",
                        owner=owner, scope=ctx.track, evidence=evidence, metrics=metrics)
    if n_exc <= int(d["go_max_exceptions"]):
        return Decision(status="GO", reason_code="DELETE_SCOPE_CLEAN",
                        reason=f"{n_del} artefact sẽ xoá (dry-run), không có exception nào — thực thi kế hoạch xoá.",
                        owner=owner, scope=ctx.track, evidence=evidence, metrics=metrics)
    return Decision(status="HOLD", reason_code="EXCEPTIONS_NEED_HUMAN_SIGNOFF",
                    reason=f"{n_exc} exception (retain_legal_hold/escalate) cần người duyệt ký duyệt trước khi thực thi; xoá đúng phần không vướng exception.",
                    owner=owner, scope=ctx.track, evidence=evidence, metrics=metrics)


def run(ctx: RunContext) -> None:
    spec = get_track(ctx.track)
    data = load(ctx)                                  # 01 load
    scored = process(ctx, data)                       # 02 process
    ctx.stats["n_items"] = len(scored)
    queue = rank(ctx, scored)                         # 03 rank
    final = review(ctx, scored, queue)                # 04 review
    cols = spec.pred_columns + [c for c in final.columns if c not in spec.pred_columns]
    final[cols].to_csv(ctx.out(spec.pred_file), index=False)
    write_decision(decide(ctx, final), ctx.out("decision.json"), ctx.out("decision_log.csv"))
