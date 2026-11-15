# Metric contract (khai báo bắt buộc — chốt ở phút 30, trước khi chạy eval)

> Các metric chuẩn đã được cài trong `src/metrics/tracks.py::eval_t08`. File này là nơi nhóm **cam kết** cách dùng chúng.
> Không đổi testset, policy hay tham số sau khi đã thấy kết quả eval.

| Mục | Khai báo của nhóm |
|---|---|
| Track | T08 · Retention & Delete Request Simulator (`fixtures/t08_deletion/`) |
| Primary metric (tên đúng như evaluator in ra) | **Deletion Completeness** (cài sẵn trong `src/metrics/tracks.py::eval_t08`) |
| Tăng tốt hay giảm tốt? | higher (Deletion Completeness); kèm **False-delete count** — *lower, mục tiêu bắt buộc = 0* |
| Đơn vị đánh giá (image / region / pair / release / batch / case …) | **cặp (request_id, artefact_id)** — mỗi delete request × mỗi artefact trong scope của nó |
| Công thức + numerator / denominator | Deletion Completeness = #(row GT `expected_action=delete` mà pred cũng `delete`) / #(toàn bộ row GT `expected_action=delete`). False-delete = #(row GT exception mà pred `delete`) + #(pred `delete` ở cặp `(request_id, artefact_id)` không có trong GT) |
| Cách ghép prediction ↔ GT (IoU? id? tolerance?) | join theo **2 khóa** `request_id` + `artefact_id`; `action` được strip/lower-case trước khi so; thiếu row = chưa xóa (tệ nhất cho completeness); duplicate khóa → evaluator raise |
| Candidate pool cố định | 10 request trong `requests.json` × scope do **graph** quyết định (không phụ thuộc GT): mọi artefact có subject trong `subject_ids` ∪ mọi descendant BFS qua cạnh `parent_ids → child` từ raw của subject. Với fixture này = **158 cặp** (chính là số row PoC) |
| Review budget B / K | **B = 20** hàng rủi ro nhất (toàn bộ exception) vào `review_queue.csv` (`configs/lab.yaml`, không đổi giữa baseline và PoC) |
| Seed | 42 (pipeline tất định, không dùng RNG — chạy 2 lần cho `actions.csv` byte-identical, đã verify bằng `cmp`) |
| Threshold policy (chọn trên dev như thế nào) | Không dùng nhãn. Policy precedence frozen theo T08.md §8: **legal hold > permission > unknown origin > identity chưa verify > multi-subject > delete** (`src/pipeline.py::resolve_action`): (1) `legal_hold` truthy → `retain_legal_hold`; (2) ACL không chứa `operator_acl` (`data-platform`) → `escalate`; (3) `origin_known=false` → `escalate`; (4) `identity_verified=false` → `escalate`; (5) >1 subject → `escalate`; (6) còn lại → `delete`. Tham số frozen trong `configs/lab.yaml`: `operator_acl: data-platform`, `require_identity_verified: true`, `dry_run: true`, `budget: 20`, decision `go_max_exceptions: 0`. Baseline theo handout §4: xóa mỗi request đúng 1 raw theo filename, không cascade, không exception |
| Quyết định multi-subject (giả định mở, khai báo rõ) | artefact đa chủ thể → **escalate ngay cả khi mọi chủ thể trong đó đều có request** (vd `a0067` subj_04+subj_05, cả hai đều có DR) — frame của 2 người không tách được, xoá vẫn ảnh hưởng người chưa consent; ưu tiên giữ False-delete = 0 hơn tối đa hoá completeness. Nếu GT coi "đủ consent → delete" thì nhóm chấp nhận mất điểm completeness ở 4 row này chứ **không** đổi policy sau khi thấy eval |
| Eval split (không dùng để tune) | toàn bộ 10 request (evaluator không có split); GT `gt/gt_actions.csv` **chỉ có ở overlay giảng viên** — nhóm không đọc, không thấy |
| Chỉ số lỗi xấu (miss / false alarm / false reject) | **False-delete count (target 0)** — xóa nhầm dữ liệu exception là lỗi nghiêm trọng nhất của track vì một phép xóa không thể hoàn tác |
| Slices sẽ báo cáo (≥2) | (1) theo `expected_action` (delete / retain_legal_hold / escalate) như evaluator groupby; (2) theo `reason_code` exception của PoC (LEGAL_HOLD / NO_DELETE_PERMISSION / UNKNOWN_ORIGIN / MULTI_SUBJECT); (3) theo `request_id` (bảng 10 request trong group_report mục 06) |
| Cách báo độ bất định khi N nhỏ (Wilson CI / bootstrap) | N = 158 cặp nhưng exceptions chỉ có 30 → **luôn báo số tuyệt đối kèm tỷ lệ** (vd "2/2 legal-hold được giữ", "24/24 multi-subject escalate"), không nói % trên nhóm nhỏ hơn 10 |
| Ngày giờ chốt + người chốt | 2026-11-14 · QA Lead — Group G00 (trước khi nhìn bất kỳ kết quả eval nào; repo học viên không có `gt/`) |

Nếu baseline = 0 thì ghi rõ "baseline = 0" thay vì phần trăm cải thiện.

**Lưu ý trung thực:**
1. `gt/gt_actions.csv` là **instructor-only** (chỉ có ở overlay giảng viên, kèm `gt/manifest.csv` riêng). Nhóm **không tự tính được** Deletion Completeness / Exception handling accuracy trước khi nộp và **không fabricate con số eval nào** trong report. Mọi con số nhóm đưa ra là số tự đo được **không cần nhãn**, từ `out/*/run_info.json` + `out/*/actions.csv`: số action theo loại, cascade, exception theo `reason_code`, audit completeness (đo bằng chính logic item_id ↔ artefact_id của `eval_t08`), determinism. Điểm chính thức do giảng viên chạy `python -m src.evaluate --track t08 ...` quyết định; nhóm không chỉnh file metrics tay.
2. Track này mô phỏng **cơ chế xử lý delete request trên dữ liệu mock** (URI `mock://...`, `dry_run: true`, không dữ liệu thật nào bị xóa — T08.md §1). Pipeline/report này **KHÔNG phải tuyên bố tuân thủ GDPR Art. 17 hay Nghị định 356/2025**: đây chỉ là gate kỹ thuật + quy trình human-in-the-loop; mọi quyết định pháp lý (giữ theo nghĩa vụ pháp lý, hết hold, hẹn xóa) thuộc về DPO/legal.
