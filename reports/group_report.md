# Group Report — Day 06 Data Lab (T08 · Retention & Delete Request Simulator)

> Điền đầy đủ, cụ thể, không viết chung chung. Mọi con số phải lấy từ file do script sinh ra
> (`out/*/run_info.json`, `out/*/actions.csv`, `out/*/audit_log.jsonl`) — không gõ tay, không làm tròn mất đơn vị.
> **Không fabricate con số eval**: `gt/` chỉ có ở overlay giảng viên (xem mục 05 và `reports/metric_contract.md`).

## 00 · Header

| Mục | Giá trị |
|---|---|
| Group ID | G88 |
| Challenge | T08 · Retention & Delete Request Simulator — xử lý 10 delete request trên data graph 212 artefact mock |
| Thành viên (5–6) + vai trò | Điền tên thật vào `reports/team_contributions.csv` + Bảng 2 (mục 10) khi chốt nhóm: 1. ____ (Lead/Integrator) · 2. ____ (Data & fixtures) · 3. ____ (Core algorithm) · 4. ____ (QA/metric) · 5. ____ (Review + report) · 6. ____ |
| Git URL | https://github.com/minhquanphi04-AIVinUni/K5-Day06-L2A-2A202603081-PhiMinhQuan-DataGovernanceAndPrivacy.git |
| Tag `demo-180m` (commit) | Demo freeze at minute 180 (chạy `make tag-demo` khi freeze demo) |
| Tag `final-24h` (commit) | Final submission (chạy `make tag-final` khi nộp) |
| Deadline (giờ VN) | 12:00 15/11 +7GMT |

## 01 · Problem statement

- **Ai bị ảnh hưởng / ai chịu hậu quả khi hệ thống sai?** Data subject gửi delete request (GDPR Art. 17 / Nghị định 356-2025 về bảo vệ dữ liệu cá nhân) nhưng hệ thống **xóa sót** dữ liệu dẫn xuất (embedding định danh, backup) → quyền của subject bị vi phạm ngầm; ngược lại **xóa nhầm** dữ liệu đang giữ theo nghĩa vụ pháp lý (legal hold) hoặc dữ liệu của người khác (frame đa chủ thể) → ràng buộc pháp lý bị phá, dữ liệu of someone else mất vĩnh viễn — một phép xóa **không thể hoàn tác**.
- **Điểm nghẽn cụ thể:** delete request trỏ tới **subject**, nhưng dữ liệu cá nhân nằm rải rác trên lineage graph: raw → backup, raw → frame → face_crop → embedding, annotation, và training_export có **nhiều cha/nhiều subject**. Một policy "xóa file được chỉ tên" không đủ; policy "xóa mọi thứ" lại đâm vào exception: legal hold, thiếu quyền (ACL vendor), origin lạ, artefact chứa nhiều subject.
- **Đầu vào → đầu ra:** `fixtures/t08_deletion/` (`data_graph.json` 212 artefact + `requests.json` 10 request) → `out/actions.csv` (`request_id, artefact_id, action ∈ {delete, retain_legal_hold, escalate}`) + `out/audit_log.jsonl` (mỗi action 1 dòng JSONL) + `out/deletion_plan.csv` (mock dry-run) + `decision.json`.
- **Tiêu chí GO / HOLD / REWORK (chốt trước khi chạy eval):** request trỏ tới subject không có trong graph → **REWORK** (`REQUEST_SUBJECT_NOT_IN_GRAPH`); còn ≥1 exception (retain_legal_hold + escalate) → **HOLD** (`EXCEPTIONS_NEED_HUMAN_SIGNOFF` — đúng handout §6: exceptions phải được người duyệt ký); 0 exception → **GO** (`DELETE_SCOPE_CLEAN`). Mọi decision bắt buộc `reason_code` + `owner` (QA Lead — Group G00).
- **Giả định của nhóm (cần DPO/legal xác nhận):** (1) precedence legal hold > permission > unknown origin > identity > multi-subject > delete; (2) multi-subject → escalate **kể cả khi mọi chủ thể đều có request**; (3) "hẹn xóa khi hết hold" chỉ được ghi vào reason_code/audit, việc re-run khi hold hết hạn nằm ngoài scope lab. Pipeline này là **cơ chế kỹ thuật trên dữ liệu mock, KHÔNG phải tuyên bố tuân thủ GDPR/legal**.

## 02 · Data & provenance

| Split | N | Nguồn / link | License | Synthetic? |
|---|---|---|---|---|
| graph (input) | 212 artefact · 15 subject | `fixtures/t08_deletion/data_graph.json` — sinh bởi `tools/make_fixtures.py` (giảng viên, seed 42) | CC0-1.0 (synthetic) | Có |
| requests (input) | 10 delete request (deadline +30 ngày, `identity_verified=true`) | `fixtures/t08_deletion/requests.json` — cùng nguồn | CC0-1.0 (synthetic) | Có |
| GT (chỉ overlay giảng viên) | `gt/gt_actions.csv` (expected_action cho từng cặp request×artefact) + `gt/manifest.csv` | không có trong repo học viên — **nhóm không đọc, không thấy** | CC0-1.0 | Có |

- **Schema & class mapping:** `data_graph.json`: `artefact_id, type (raw_video/backup/frame/face_crop/embedding/annotation/training_export), subject_ids, parent_ids, uri (mock://...), owner, acl, legal_hold, origin_known`. Phân bố type: raw_video 15 (1/subject, root) · backup 15 · frame 45 · face_crop 45 · embedding 45 · annotation 45 · training_export 2 (multi-parent). 10 subject có request: subj_01, 03, 04, 05, 07, 08, 09, 11, 12, 14; 5 subject "control" không có request: subj_00, 02, 06, 10, 13.
- **Exception được cài sẵn trong fixture:** 2 legal hold (`a0043` LH-2026-03 subj_03 · `a0099` LH-2026-07 subj_07) · 3 embedding `origin_known=false` (a0075/a0079/a0083, subj_05) · 1 backup ACL `vendor-x` (a0156, subj_11) · 8 artefact 2-subject (frame+annotation của 4 cặp chủ thể) + 2 training_export 9–10 subject. Tổng **30 exception** trên 158 cặp scope.
- **Seed:** 42 cho mọi run (`run_info.json` ghi lại); pipeline tất định, không dùng RNG.
- **`manifest.csv` verified (sha256):** có — `verify_manifest` chạy ở bước 01 của cả PoC và baseline; `tests/test_fixtures.py::test_manifest_hashes_match` PASS cho `t08_deletion`. GT không nằm trong input manifest.
- **Dữ liệu thật dùng (nếu có):** KHÔNG — T08.md §1: "không xóa dữ liệu thực trong lab". `dry_run: true` frozen; mọi URI là `mock://`.

## 03 · Research & design

- **References:**
  1. GDPR Art. 17(3) (https://gdpr-info.eu/art-17-gdpr/, xem 2026-11-14): quyền xóa **có ngoại lệ** — nghĩa vụ pháp lý (legal hold) vượt ưu tiên quyền xóa → động lực cho hành động `retain_legal_hold` có reason_code + hẹn xóa khi hết hold.
  2. California DROP — Delete Request and Opt-out Platform (https://oag.ca.gov/privacy/ccpa, xem 2026-11-14): một request phải lan tới mọi nơi dữ liệu nằm → động lực cho cascade theo graph thay vì xóa 1 file.
  3. Data Cards Playbook (Research Shelf, Day06 handout): mô tả lineage/derivation của dataset — động lực cho việc model hoá `parent_ids` thành graph và BFS.
- **Sơ đồ data flow:** `load()` (verify sha256 manifest + parse 2 JSON + validate schema + chặn duplicate request_id / subject lạ) → `process()` = **deletion resolver**: scope = (a) raw của subject ∪ (b) BFS cascade qua cạnh `parent_ids → child` ∪ (c) mọi artefact có subject trong `subject_ids` (frame đa chủ thể / training_export treo trên cây của subject khác vẫn bị bắt) → precedence `resolve_action()` gán action + reason_code → mọi action append vào `audit_log.jsonl` (item_id = artefact_id) → `rank()` top-B=20 exception vào review queue + contact sheet HTML offline → `review()` áp override từ `review_queue_reviewed.csv` (apply_overrides + audit) → `decide()` GO/HOLD/REWORK theo policy frozen → `actions.csv` + `deletion_plan.csv` (mock dry-run).
- **Thành phần offline:** 100% Python stdlib + pandas, không model, không network (`--offline` + offline guard). Không đọc `fixtures/*/gt/` (GT guard, exit 5 nếu cố).
- **Trade-offs đã cân nhắc:**
  - **Scope theo `subject_ids` thay vì chỉ BFS từ raw:** frame đa chủ thể (a0011 của subj_00+subj_01) treo trên cây raw của *subject khác* — BFS từ raw của subj_01 sẽ sót; vì dữ liệu cá nhân của subject phải xóa bất kể nó sinh ra từ ai → scope = subject-membership ∪ BFS.
  - **Multi-subject → escalate (conservative):** kể cả khi cả 2 chủ thể đều có request (a0067: subj_04+subj_05). Đánh đổi: nếu GT coi "đủ consent → delete" thì mất completeness ở 4 row — nhóm chọn giữ False-delete = 0 và khai báo rõ trong metric contract.
  - **Hold chỉ áp cho artefact đang mang `legal_hold`, không cascade xuống descendant** (backup/frames của subj_03 vẫn được xóa): đúng văn bản "hẹn xóa khi hết hold" — nếu legal matter cần giữ cả subtree thì DPO phải escalate thủ công; đây là giả định mở đã nêu.
  - `identity_verified=false` → escalate dù fixture hiện tại đều true (rule vẫn bật để chống naive-delete khi có request lạ).

## 04 · Baseline vs PoC

- **Baseline** (`src/baseline.py`, theo đúng handout T08 §4): "Xóa một bản raw theo filename, không tìm derived/backup" — mỗi request tìm đúng 1 `raw_video` của subject và trả `delete`, **không cascade, không exception**.
- **PoC** (`src/pipeline.py`, params frozen trong `configs/lab.yaml`): deletion resolver scope+cascade+precedence, chi tiết mục 03.
- **Cùng pool / cùng heldout / cùng seed:** xác nhận — cả hai chạy `--seed 42`, cùng 10 request, cùng graph, cùng B=20, cùng output contract `actions.csv`; eval cùng `gt/gt_actions.csv` của giảng viên.

**Bảng 1 · Baseline vs PoC (số nhóm tự đo được, không cần GT — nguồn `out/*/run_info.json`)**

> ⚠️ `reports/metrics_baseline.json`, `reports/metrics_poc.json`, `reports/table1_baseline_vs_poc.md` **chưa sinh được** trên repo học viên vì `gt/gt_actions.csv` chỉ có ở overlay giảng viên (`src.evaluate` → exit 2 "ground truth not found"). Giảng viên chạy lệnh ở mục 05; nhóm **không điền tay** các file này, không fabricate số.

| Metric (unit) | Baseline | PoC | Δ & chú thích |
|---|---|---|---|
| # rows `actions.csv` | 10 (mỗi request 1 raw) | 158 (10 request × scope) | Baseline bỏ trống 148 cặp scope → Deletion Completeness sẽ rất thấp (missing = không xóa) |
| `delete` / `retain_legal_hold` / `escalate` | 10 / 0 / 0 | 128 / 2 / 28 | Baseline = 0 trên cả 2 loại exception |
| Cascade delete (artefact dẫn xuất, không phải raw) | **0** | **120** | đúng failure mode 2 của handout: baseline "chỉ xóa raw nhưng giữ bản sao và embedding định danh" |
| Raw legal-hold bị `delete` (vi phạm exception — failure mode 1) | **2** (a0043 LH-2026-03, a0099 LH-2026-07) | **0** | PoC giữ 2/2 bằng `retain_legal_hold` + reason LEGAL_HOLD |
| Delete chạm exception khác (vendor ACL / origin lạ / multi-subject) | 0 (do chỉ xóa raw) | **0** | PoC escalate 28/28; con số cuối cùng (exception handling accuracy, false-delete) do giảng viên chấm |
| Audit trace (action có dòng trong `audit_log.jsonl`) | 10/10 | **158/158** (1.0) | đo bằng chính logic item_id ↔ artefact_id của `eval_t08` |
| Idempotency (2 lần cùng seed) | identical | **byte-identical** (verified `cmp`) | protocol T08.md §4 |
| Deletion Completeness / False-delete count | **đợi overlay giảng viên** | **đợi overlay giảng viên** | lệnh: `make eval-baseline eval-poc compare TRACK=t08` |
| Runtime / 100 items | 0.045 s / 10 hành động ≈ **0.45 s / 100** | 0.075 s / 158 hành động ≈ **0.047 s / 100** | Python 3.11.9, Windows-10-10.0.26200, Intel64 Family 6 Model 154 |

## 05 · Evaluation protocol

- **Công thức primary metric:** `Deletion Completeness` = #(row GT `expected_action=delete` mà pred `delete`) / #(toàn bộ row GT `expected_action=delete`); đơn vị = cặp (request_id, artefact_id). **False-delete count (target 0)** = exception bị delete + delete ngoài scope GT. **Exception handling accuracy** = đúng hành động trên các row exception. Chi tiết: `reports/metric_contract.md`.
- **Threshold / budget B và lúc chốt:** B=20 (`configs/tracks/t08.yaml`); precedence + `operator_acl: data-platform` + decision policy frozen trong `configs/lab.yaml` — chốt 2026-11-14, trước khi nhìn kết quả eval nào.
- **Độ bất định khi N nhỏ:** 158 cặp nhưng chỉ 30 exception → mọi slice báo kèm số tuyệt đối (2/2, 24/24, 3/3, 1/1); không nói "% cải thiện" trên nhóm nhỏ.
- **Lệnh evaluate chính xác (giảng viên chạy, cần overlay `gt/`):**
  ```bash
  python -m src.evaluate --track t08 --pred out/baseline/actions.csv --run-name baseline --out reports/metrics_baseline.json
  python -m src.evaluate --track t08 --pred out/poc/actions.csv      --run-name poc      --out reports/metrics_poc.json
  python -m src.compare reports/metrics_baseline.json reports/metrics_poc.json --out reports/table1_baseline_vs_poc.md
  ```

## 06 · Result slices

Số nhóm tự đo được (không cần GT), từ `out/poc/actions.csv` (seed 42):

| Slice | N | Baseline | PoC | Ghi chú |
|---|---|---|---|---|
| action=delete | 158 | 10 (toàn raw) | **128** (8 raw + **120 cascade**) | cascade = backup/frame/crop/embedding/annotation/training_export |
| action=retain_legal_hold | 2 | 0 | **2** (a0043, a0099) | reason_code LEGAL_HOLD, ghi "hẹn xóa khi hết hold" |
| action=escalate | 28 | 0 | **28** | MULTI_SUBJECT 24 · UNKNOWN_ORIGIN 3 · NO_DELETE_PERMISSION 1 |
| exception theo reason_code | 30 | 0/30 | 30/30 được xử lý đúng policy frozen | accuracy so với GT do giảng viên chấm |
| theo request (min/max) | 10 | 1 hàng/request | DR-006 ít nhất 14 · DR-004 nhiều nhất 18 | bảng đủ 10 request bên dưới |
| subject control (không có request) | 5 subject | 0 delete | **0 delete** | chỉ xuất hiện qua escalate khi nằm trong artefact đa chủ thể |

**Bảng theo request (PoC): delete / retain_legal_hold / escalate**

| Request | Subject | delete | retain | escalate | Tổng | Exception trong request |
|---|---|---|---|---|---|---|
| DR-001 | subj_01 | 14 | 0 | 3 | 17 | a0011, a0014, a0212 (multi-subject) |
| DR-002 | subj_03 | 13 | 1 | 1 | 15 | a0043 **LEGAL_HOLD LH-2026-03**, a0211 (multi-subject) |
| DR-003 | subj_04 | 12 | 0 | 4 | 16 | a0067, a0070, a0211, a0212 |
| DR-004 | subj_05 | 11 | 0 | 7 | 18 | a0075/a0079/a0083 **UNKNOWN_ORIGIN** + 4 multi-subject |
| DR-005 | subj_07 | 13 | 1 | 2 | 16 | a0099 **LEGAL_HOLD LH-2026-07**, a0211, a0212 |
| DR-006 | subj_08 | 12 | 0 | 2 | 14 | a0123, a0126 |
| DR-007 | subj_09 | 14 | 0 | 3 | 17 | a0123, a0126, a0211 |
| DR-008 | subj_11 | 13 | 0 | 2 | 15 | a0156 **NO_DELETE_PERMISSION (acl vendor-x)**, a0211 |
| DR-009 | subj_12 | 12 | 0 | 3 | 15 | a0179, a0182, a0211 |
| DR-010 | subj_14 | 14 | 0 | 1 | 15 | a0211 (10-subject training export) |

- **Failure mode 1 (T08.md §5 — "Xóa nhầm dữ liệu thuộc exception", minh hoạ bằng metadata):** baseline xóa `a0043` (`raw_video`, subj_03, DR-002) dù `legal_hold=LH-2026-03`, và `a0099` (subj_07, DR-005) dù `legal_hold=LH-2026-07` — 2 false-delete chắc chắn khi chấm. **Root cause:** baseline không có bước exception check — đúng kịch bản "yêu cầu xóa không được thực hiện ngay khi dữ liệu đang bị giữ theo nghĩa vụ pháp lý (GDPR Đ.17(3))". PoC: precedence đặt LEGAL_HOLD đầu tiên → `retain_legal_hold`, reason "hẹn xoá khi hết hold", row vào review queue cho người duyệt ký.
- **Failure mode 2 (T08.md §5 — "chỉ xóa raw nhưng giữ bản sao và embedding định danh", minh hoạ bằng metadata):** DR-004 (subj_05): baseline chỉ xóa `a0071` và **giữ lại 17 artefact định danh**: 1 backup (`a0072`), **3 embedding vector-db** (`a0075/a0079/a0083`), 4 frame, 3 face_crop, 4 annotation, 2 training_export. **Root cause:** không có graph traversal (BFS qua `parent_ids`) — dữ liệu cá nhân "sống sót" ở mọi tầng dẫn xuất. PoC cascade: 11 delete cho DR-004 (còn 7 escalate vì origin lạ / đa chủ thể).
- **Rủi ro còn lại của PoC (tự khai báo):** 4 row multi-subject có cả 2 chủ thể đều request (a0067, a0123 với DR-003/DR-004 và DR-006/DR-007) — PoC vẫn escalate; nếu GT coi đây là delete thì completeness mất tối đa 4/điểm-phải-xóa. Nhóm chấp nhận đánh đổi này cho False-delete = 0 (giả định mở cần DPO xác nhận).
- **Latency / compute (máy, CPU, ms/item):** PoC ≈ 0.47 ms/hành động (0.0745 s / 158), baseline ≈ 4.5 ms/hành động (0.0452 s / 10) — Intel64 Family 6 Model 154, Python 3.11.9, Windows-10-10.0.26200 (ghi trong `run_info.json`). Scale 10× graph (2k artefact) vẫn chạy trong <1 s vì resolver là O(V+E).

## 07 · Human & decision

- **Review queue:** `out/poc/review_queue.csv` — top-B=20 theo `score` giảm dần (escalate=1.0 > retain_legal_hold=0.9 > delete=0.5) → 20/20 hàng là **escalate**, `slice` = reason_code, `evidence_path` = URI mock; kèm **contact sheet offline** `out/poc/contact_sheet.html` (không có ảnh — artefact là mock URI; trang vẫn có dropdown quyết định + nút Export CSV schema REVIEW_COLUMNS — `src/common/contact_sheet.py`).
- **Cách override + audit log:** reviewer điền `reviewer`, `reviewer_decision` (approve/reject/edit/escalate/skip), `override_value` (hành động mới trong vocabulary T08), `review_note` rồi lưu thành `out/review_queue_reviewed.csv`; chạy lại `make run` → `review()` đọc file (thiếu reviewer / decision lạ → fail rõ ràng; override ngoài vocabulary → ValueError), `apply_overrides` ghi đè `action`, mỗi thay đổi append 1 dòng JSONL `override:edit` (actor/action/before/after/reason, **append-only**) vào `out/audit_log.jsonl`. Test: `tests/test_pipeline_t08.py::test_t08_review_override_changes_action_and_audits`.
- **Bảng request → artefact → exception → người duyệt → hành động & log:** chính là `out/poc/review_queue.csv` (20 exception) + `out/audit_log.jsonl` (158 dòng `resolve:*` + dry-run + provenance) + bản sao nộp kèm `reports/review_queue.csv`, `reports/decision_log.csv`.
- **Decision policy + `reason_code` + owner:** REWORK (`REQUEST_SUBJECT_NOT_IN_GRAPH`) khi có request không resolve được subject; HOLD (`EXCEPTIONS_NEED_HUMAN_SIGNOFF`) khi còn ≥1 exception; GO (`DELETE_SCOPE_CLEAN`) khi 0 exception. Owner = `QA Lead — Group G00` (config `owner`). Run hiện tại: 30 exception → **HOLD** với evidence là review queue + deletion plan — hợp lý: không thể tự động thực thi khi còn 2 legal hold và 28 escalate cần người duyệt.
- **Khi nào HOLD/REWORK thay vì tự động thực thi:** mọi run còn exception (luôn là case này trên fixture); mọi run có reviewer override nhưng vẫn còn escalate; **không bao giờ** tự thực thi ngoài dry-run trong lab (`dry_run: true` frozen, chỉ xóa mock URI).

## 08 · Reproduce & tests

```bash
# PoC (1 lệnh):
python -m src.main --config configs/lab.yaml --output out/poc --run-name poc --offline --seed 42
# Baseline:
python -m src.main --track t08 --pipeline src.baseline --output out/baseline --run-name baseline --offline --seed 42
# evaluate (cần overlay gt/ của giảng viên):
python -m src.evaluate --track t08 --pred out/poc/actions.csv --out reports/metrics_poc.json
pytest -q
```

- **Output của `pytest -q`:** **60 passed, 38 skipped** (38 skip = test cần `gt/` của overlay giảng viên, marker `requires_gt` theo `tests/conftest.py`). Nhóm thêm 11 test mới trong `tests/test_pipeline_t08.py` (E2E PoC+baseline, baseline raw-only, cascade+legal-hold+zero-false-delete, scope đa chủ thể + unit precedence, subject lạ, duplicate request_id, reviewed-file sai schema, override + audit, override ngoài vocabulary, decision HOLD) và viết lại `tests/test_cli.py` cho t08 (exit 0/2/3, determinism byte-identical, GT guard chặn cả khi file gt không tồn tại, track inferred từ folder, offline guard, evaluate thiếu GT → exit 2 rõ ràng).
- **Determinism:** chạy PoC 2 lần cùng seed 42 → `out/poc/actions.csv` **byte-identical** (verified bằng `cmp`) — protocol idempotency T08.md §4.
- **Máy / CPU / OS / Python:** Windows-10-10.0.26200 · Intel64 Family 6 Model 154 · Python 3.11.9 · git sha: chạy ngoài git repo của lab (standalone track folder), điền sau khi commit vào repo nhóm.
- **Lưu ý Windows:** không có `make` trên PATH; venv dùng `.venv/Scripts/python.exe` (Makefile/run.sh mặc định `.venv/bin/python`) → đã gọi trực tiếp `python -m src.main / -m pytest` như trên — tất cả exit 0.

## 09 · Limitations & ethics

- **Rủi ro pháp lý / PII / license:** fixture synthetic CC0 (`mock://` URI, không có dữ liệu thật, manifest khai báo source `synthetic:tools/make_fixtures.py`). Trong lab **không có phép xóa nào được thực thi thật** (`dry_run: true` frozen; `deletion_plan.csv` chỉ là kế hoạch). Nếu chạy trên hệ thống thật: phải có approval workflow thật, backup/restore, và DPO ký từng exception — pipeline này chỉ là mô phỏng cơ chế.
- **KHÔNG tuyên bố "đạt GDPR / Nghị định 356/2025":** pipeline chỉ mô phỏng *cơ chế* xử lý delete request (precedence, cascade, audit). Việc căn cứ nghĩa vụ pháp lý (legal hold hết hạn, ngoại lệ Art. 17(3)) là việc của DPO/legal; nhóm chỉ cung cấp reason_code + evidence để người duyệt quyết.
- **Điều chưa biết:** exception handling accuracy / completeness thật phụ thuộc `gt_actions.csv` (overlay) — đặc biệt 4 row multi-subject both-consented (a0067, a0123) mà nhóm chủ động escalate; nếu GT kỳ vọng delete thì đây là "miss" có chủ đích, đã khai báo trong metric contract. Baseline chắc chắn có ≥2 false-delete (xóa raw đang legal hold) và completeness thấp (thiếu 148/158 hàng scope) — nhưng con số cuối do giảng viên chấm, nhóm không đoán.
- **Scale 10×:** resolver O(V+E) trên graph 212 node chạy 0.075 s; với 10k artefact cần thêm: transactional delete (batch + rollback), idempotency key theo (request, artefact), và hàng đợi retry khi hệ thống nguồn từ chối. Review queue nên paging vì 20 hàng chỉ là budget demo.
- **Kế hoạch cải tiến:** (1) dry-run diff trước/sau (snapshot mock state) để reviewer xem chính xác cái gì mất; (2) policy "đủ consent multi-subject → delete có điều kiện" khi có tách-subject (vd face_crop tách được theo subject); (3) SLA dashboard theo deadline +30 ngày (mốc +24h của handout) + notification stub; (4) resume job bị ngắt bằng checkpoint theo request_id.

## 10 · Team contribution

**Bảng 2 · Individual contribution evidence** (đối chiếu với `git shortlog -sne --all` và `reports/team_contributions.csv`)

> Điền tên/email thật sau khi mọi thành viên commit; quy tắc rubric: mỗi người ≥1 commit thực chất (commit chỉ đổi khoảng trắng không tính).

| Học viên | Git author/email | Module / vai trò | Commit SHA / diff chứng minh | Trình bày? |
|---|---|---|---|---|
| Member 1 | (điền) | Integration / CLI · wiring config | | |
| Member 2 | (điền) | Data + GT protocol · fixtures/provenance | | |
| Member 3 | (điền) | Core algorithm (`src/pipeline.py`, `src/baseline.py`) | | |
| Member 4 | (điền) | Metric + tests (`tests/test_pipeline_t08.py`) | | |
| Member 5 | (điền) | Review queue + report + contact sheet | | |
| Member 6 (nếu có) | (điền) | Research / ablation / debate | | |

## 11 · References

| URL | Ngày xem | 1–2 dòng ý nghĩa (không chép nguyên văn) |
|---|---|---|
| https://gdpr-info.eu/art-17-gdpr/ (GDPR Art. 17 + 17(3)) | 2026-11-14 | Quyền xóa có ngoại lệ bắt buộc (nghĩa vụ pháp lý…) → cơ sở cho `retain_legal_hold` + hẹn xóa khi hết hold thay vì từ chối im lặng. |
| https://oag.ca.gov/privacy/ccpa (California DROP) | 2026-11-14 | Một delete request phải lan tới mọi broker/ nơi lưu dữ liệu → tham chiếu cho cascade theo graph thay vì xóa 1 file. |
| Data Cards Playbook (Research Shelf — Day06 handout) | 2026-11-14 | Mô tả lineage/derivation dataset → động lực model hoá `parent_ids` thành graph có hướng để BFS cascade. |

---

**Bảng 3 · Checklist chốt khi nộp**

- [x] Link repo trong README; input mẫu không chứa PII thật (synthetic CC0, mock URI, dry-run).
- [x] Đúng 1 lệnh run (`python -m src.main ...` exit 0), 1 lệnh evaluate, 1 lệnh tests; không cần download khi chạy (offline guard, không model ngoài).
- [x] Có baseline/PoC outputs + 2 failure mode bắt buộc phân tích bằng metadata + reviewer evidence (review queue, contact sheet) + decision log + audit_log.jsonl append-only.
- [x] Có license/source provenance (manifest sha256 verified), seed 42, offline environment, eval N (158 cặp) ghi rõ trong metric contract.
- [ ] Mỗi thành viên có ≥1 commit thực chất và được nêu minh bạch trong bảng trên (cần tên/email thật của nhóm).
