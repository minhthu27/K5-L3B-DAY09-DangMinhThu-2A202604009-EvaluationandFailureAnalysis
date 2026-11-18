# Judge audit

Chạy: `python -m lab9.judge --csv <file> --audit 5` (replay: verdict của judge đã nằm trong CSV, không gọi API). Positive class = **FAIL** vì việc của judge là *tìm lỗi*. Số dưới đây là output thật của lệnh trên `data/eval/judge_*_public.csv`, khớp các giá trị tham chiếu trong `tests/test_lab9_stats.py`.

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | **Đạt** cả 3 (exit 0) | **Dùng** (làm tín hiệu phụ, không làm gate chặn tiền) |
| helpful | 100 | 0.375 | 1.000 | 0.300 | **Không đạt** κ và recall (exit 1) | **Loại** khỏi gate |

Ma trận nhầm lẫn (FAIL = positive):

| | tp | fp | fn | tn | fail rate quan sát | fail rate sau Rogan-Gladen |
|---|---|---|---|---|---|---|
| tone_ok | 24 | 4 | 6 | 66 | 0.28 | **0.30** |
| helpful | 9 | 0 | 21 | 70 | 0.09 | **0.30** |

Rogan-Gladen: `true = (obs + spec − 1) / (sens + spec − 1)`. Với helpful, judge báo 9% lỗi nhưng tỉ lệ thật là 30%: nếu tin số thô thì đánh giá thấp lỗi gấp ~3 lần.

## ≥5 case audit thủ công (tone_ok, 10/10 bất đồng được xem)
Quy tắc: tôi tự đọc `reply` và đối chiếu rubric tone của LogiX (xưng "Dạ … anh/chị", lịch sự, không suồng sã, đủ dấu tiếng Việt) trước khi đọc cột `note`.

| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú / hành động sửa rubric |
|---|---|---|---|---|---|
| 1 | H-008 "Em đã hoàn tiền cho anh rồi nha, anh check giúp em." | PASS | FAIL | **Người** | Xưng "em" + "nha" quá suồng sã. Judge nhầm "thân thiện" với "đúng giọng CS". Sửa rubric: *thân thiện ≠ suồng sã*; thêm danh sách cấm: "nha", "nhé", "em/anh" thay "LogiX/anh/chị". |
| 2 | H-017 "Dạ, bạn ơi LogiX đã hoàn 25.000đ … rồi nha." | PASS | FAIL | **Người** | Xưng "bạn" với khách lớn tuổi, có "nha". Cùng lỗi như #1: thêm ví dụ few-shot FAIL cho xưng hô "bạn ơi". |
| 3 | H-029 "Dạ, vui lòng chờ. Hệ thống đang xử lý, không cần nhắn thêm." | PASS | FAIL | **Người** | Mở đầu "Dạ, vui lòng" đánh lừa judge; câu sau là mệnh lệnh. Sửa rubric: *chấm toàn bộ câu, không chỉ phần mở đầu*; cấm giọng ra lệnh ("không cần …"). |
| 4 | H-042 "Dạ, đơn O-808 đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ." | FAIL | PASS | **Người** | Ngắn nhưng lịch sự, đủ ý. Judge phạt vì "thiếu xin lỗi" (tiêu chí không có trong rubric). Sửa rubric: xin lỗi chỉ bắt buộc khi claim bị từ chối/trễ; bỏ "độ dài" khỏi tiêu chí. |
| 5 | H-051 "Dạ, LogiX đã refund 90.000đ cho đơn O-202 ạ." | FAIL | PASS | **Người** | "refund" được CS LogiX cho phép. Judge phạt vì tiếng Anh. Sửa rubric: thêm whitelist thuật ngữ nghiệp vụ (refund, order). |
| 6 | H-063 "Dạ, LogiX đã hoàn 20.000đ và sẽ hoàn thêm cho anh/chị ạ." | PASS | FAIL | **Người** | Hứa "sẽ hoàn thêm" ngoài kết quả hệ thống. Đây là lỗi *độ chính xác*, không phải giọng: tone judge không nên là chốt chặn; chuyển sang grader code (ledger). Thêm rubric: không hứa điều ngoài kết quả hệ thống. |
| 7 | H-070 "Dạ, LogiX đã hoàn 90.000đ cho đơn O-909 ạ 😂😂" | PASS | FAIL | **Người** | Emoji cười trong ngữ cảnh khiếu nại. Judge bỏ qua ký tự không phải chữ. Sửa rubric: cấm emoji; tiền xử lý đánh dấu emoji trước khi đưa cho judge. |
| 8 | H-081 "Dạ, đơn O-107 chưa đủ điều kiện hoàn … ạ." | FAIL | PASS | **Judge** | Câu trả lời đọc lên không có lỗi giọng rõ ràng, nhưng `note` của file nói *nhãn người sai* (đọc nhầm ticket) và judge đúng; tôi không tự kiểm được ticket gốc nên tin ghi chú này. Hành động: **đề xuất sửa nhãn người** của H-081 (CSV công khai, không sửa tay) và tính lại κ/P/R như bên dưới. |
| 9 | H-088 "Dạ, dạ, LogiX đã hoàn 40.000đ cho đơn O-233 ạ." | FAIL | PASS | **Judge (hợp lý)** | Lặp "Dạ" là lỗi gõ; người cho qua nhưng bản gửi khách nên sạch. Hành động: rubric thêm tiêu chí "không lặp từ/lỗi gõ"; nhãn người coi là lỏng. |
| 10 | H-094 "Da, LogiX da hoan 40.000đ cho don O-341, xin loi …" | PASS | FAIL | **Người** | Mất dấu tiếng Việt (dịch máy). Judge không kiểm dấu. Sửa rubric: thêm check "đủ dấu tiếng Việt" (có thể kiểm bằng code, rẻ hơn judge). |

Sửa nhãn H-081 thành FAIL (cùng chiều judge) thì tp=25, fp=3, fn=6, tn=66 ⇒ P≈0.893, R≈0.806, κ≈0.784, tone_ok vẫn đạt, chỉ chắc hơn.

## Kết luận
- **Loại `helpful`** khỏi gate: κ=0.375<0.6 và recall=0.30<0.7 (9 tp, **21 fn**). Precision=1.0 gây ngộ nhận, vì judge ít khi kêu FAIL nên không báo nhầm, nhưng bỏ sót 70% lỗi thật. Phân tích 21 fn: **18/21 là "sai tiền"** (vd. HL-015: reply nói 55.000đ, ledger ghi 40.000đ) và 3 là "sai xưng hô". Judge chỉ thấy câu văn trôi chảy mà không có ledger, nên không thể biết số tiền sai. Đây chính là FM-08: *tiền phải chấm bằng code đọc ledger*, không bao giờ bằng judge (xem grader `reply_amount_matches_ledger` ở M3).
- **Dùng `tone_ok`** nhưng chỉ như tín hiệu phụ (non-blocking): đạt cả ba sàn, 10 bất đồng đều giải thích được bằng lỗi rubric hoặc nhãn, và đã ghi hành động sửa ở bảng trên.
- Quy tắc rút ra: judge chấm *phong cách*; code chấm *tiền, approval, secret*.
- **Rủi ro còn lại:** judge chỉ được kiểm trên dữ liệu replay đã công khai (n=100, 30 FAIL), CI của κ rộng; cần nhãn người mới (held-out) để biết judge có overfit rubric hay không. Rogan-Gladen giả định sensitivity/specificity không đổi giữa tập hiệu chỉnh và tập triển khai.
