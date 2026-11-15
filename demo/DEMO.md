# Demo script — T08 Retention & Delete Request Simulator (Group G00, 3–5 phút)

## Chuẩn bị trước (offline, không download gì, không xóa dữ liệu thật)
```bash
python -m src.main --config configs/lab.yaml --output out/poc --run-name poc --offline --seed 42
python -m src.main --track t08 --pipeline src.baseline --output out/baseline --run-name baseline --offline --seed 42
```

## 1. Vấn đề (30 giây)
- Delete request trỏ tới **subject**, nhưng dữ liệu cá nhân nằm rải rác trên lineage graph:
  raw → backup, raw → frame → face_crop → embedding, annotation, training_export (multi-parent, multi-subject).
- Baseline handout = "xóa 1 raw theo filename" → **2 failure mode**: (1) xóa nhầm raw đang legal hold;
  (2) chỉ xóa raw nhưng giữ bản sao + embedding định danh (DR-004 còn sót 17/18 artefact).

## 2. Chạy live (1 lệnh, <0.1 giây)
```bash
python -m src.main --config configs/lab.yaml --output out/poc --run-name poc --offline --seed 42
```
Chỉ ra: exit 0, `out/poc/actions.csv` (158 hàng: 128 delete / 2 retain_legal_hold / 28 escalate),
`run_info.json` (seed 42, dry_run=true, runtime ~0.07 s), `decision.json` (**HOLD**,
`EXCEPTIONS_NEED_HUMAN_SIGNOFF`, owner "QA Lead — Group G00").

## 3. Resolver + evidence (1 phút)
Mở `out/poc/actions.csv` + `out/poc/audit_log.jsonl`:
- Cascade: DR-004 (subj_05) → 11 delete gồm backup a0072, frames, face_crops, annotations
  (scope = subject_ids ∪ BFS qua parent_ids→child).
- Exception precedence frozen: `a0043`/`a0099` → **retain_legal_hold** (LEGAL_HOLD, "hẹn xoá khi hết hold" — GDPR Đ.17(3));
  `a0075/a0079/a0083` → escalate (UNKNOWN_ORIGIN); `a0156` → escalate (acl vendor-x, NO_DELETE_PERMISSION);
  24 hàng đa chủ thể → escalate (MULTI_SUBJECT) — kể cả khi cả 2 chủ thể đều có request (a0067, a0123).
- `deletion_plan.csv` = kế hoạch dry-run trên URI `mock://` — **không dữ liệu thật nào bị xóa**.

## 4. Human-in-the-loop (1 phút)
Mở `out/poc/contact_sheet.html` bằng trình duyệt (offline, không có ảnh — artefact là mock URI):
- 20 hàng exception ưu tiên (toàn escalate), reason_code + evidence.
- Nhập tên reviewer → chọn `edit` cho 1 hàng (ví dụ DR-001::a0011 → `delete`) → Export CSV →
  lưu thành `out/poc/review_queue_reviewed.csv` → chạy lại lệnh trên → chỉ ra `audit_log.jsonl`
  có thêm dòng `override:edit` (append-only: ai / sửa gì / lý do gì, timestamp UTC).

## 5. 2 failure mode của handout — minh hoạ trên fixture (1 phút)
| Failure mode | Bằng chứng (metadata) | Baseline vs PoC |
|---|---|---|
| Xóa nhầm dữ liệu thuộc exception | `a0043` legal_hold=LH-2026-03 (subj_03/DR-002), `a0099` LH-2026-07 (subj_07/DR-005) | baseline delete cả 2 (2 false-delete); PoC retain_legal_hold 2/2 |
| Chỉ xóa raw, giữ bản sao + embedding định danh | DR-004: baseline chỉ xóa `a0071`, còn giữ backup a0072 + **3 embedding** a0075/a0079/a0083 + 13 artefact khác | baseline cascade=0; PoC cascade **120** delete toàn graph |

## 6. Reproducibility + tests (30 giây)
```bash
pytest -q   # 60 passed, 38 skipped (skip = cần overlay gt/ giảng viên)
```
- Chạy 2 lần cùng seed 42 → `actions.csv` **byte-identical** (idempotency, đã `cmp`).
- Guards: `--offline` chặn network (exit 4); GT guard chặn đọc `fixtures/*/gt/` (exit 5) — kể cả khi file gt không tồn tại.
- Audit trace: 158/158 action có dòng trong `audit_log.jsonl` (đo bằng logic của `eval_t08`).

## 7. Kết quả + điều trung thực (30 giây)
- PoC: 128 delete (8 raw + **120 cascade**) · 2 retain_legal_hold · 28 escalate · 0 delete chạm exception.
- Deletion Completeness / False-delete count / Exception handling accuracy **chưa tự chấm được** vì
  `gt/gt_actions.csv` ở overlay giảng viên — số cuối do `python -m src.evaluate --track t08` quyết định;
  nhóm không fabricate số, không chỉnh file metrics tay.
- Decision hiện tại: **HOLD** (30 exception cần người duyệt ký; dry-run only).
- Track này là **mô phỏng cơ chế trên dữ liệu mock — KHÔNG phải tuyên bố tuân thủ GDPR/Nghị định 356/2025**;
  mọi quyết định pháp lý thuộc về DPO/legal.
