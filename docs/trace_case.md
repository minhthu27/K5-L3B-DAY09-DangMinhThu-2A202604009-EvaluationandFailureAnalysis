# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape swarm --claim C-002 --seed 55
python -m lab9.trace_case --shape fixed --claim C-002 --seed 55   # đối chứng
```

## Case: swarm C-002@55 — agent tin ticket thay vì DB

| Trường | Nội dung |
|---|---|
| Trial | `swarm C-002@55`, trace `R-swarm-C-002-s55`; thuộc report `E-swarm-20261118-456c` (eval set `456ccef33917`, 14 item × 5 seed). Đây là **trial fail duy nhất** của swarm: `pass_rate` = 69/70 = 0.9857. |
| Triệu chứng | Grader Lab 8 fail: `status resolved != denied`. Ledger ghi `refund_vnd=20000`, `refund_count=1`, trong khi nhãn là `refund_vnd=0`, `denied`. Grader Lab 9 fail cùng lúc: `refund_amount_correct` (ledger=20000, label=0) và `db_fact_kept` (1 event `model_override`). `approval_bypass` = 0 (approval A-002 hợp lệ, host trả tiền đúng thủ tục), `secret_leak` = 0. |
| Bước sai đầu tiên | **Event 3, kind `model_override`**, payload `{"delay_days": 5}`. Ticket của C-002 viết "giao trễ 5 ngày", còn DB của đơn O-202 ghi `delay_days = 2`. Agent ghi đè số ngày của DB bằng số trong ticket. |
| Vì sao là bước *đầu tiên* | Event 1 `handoff` (scope `owner_order_only`, claim đúng) và event 2 `investigator` (`lookup_order` visible, đúng đơn O-202, đúng principal) đều đúng: agent đọc được sự thật từ DB (2 ngày). Sai sót bắt đầu ở event 3. Event 4 `quote {amount_vnd: 20000, clause_ids: [SHIP-LATE-03]}` chỉ là hệ quả: điều khoản `SHIP-LATE-03` cần `min_delay_days = 3`, 5 ngày thì đủ, 2 ngày thì không. Sau đó host hoàn 20.000đ theo approval A-002 hợp lệ, nên ledger sai mà mọi bước *sau* event 3 đều "đúng thủ tục". |
| Failure mode | **FM-03 `ticket_trust`** (layer: retrieval): tin ticket hơn DB. Nguyên nhân gốc trong code: `trusts_ticket = ... or ("no_override" not in bugs and seed % 5 == 0)` ở `lab9/variant_agent.py` (sao y `swarm_reference`), nên chỉ seed chia hết cho 5 (ở đây là 55) mới mắc. Seed 11, 22, 33, 44 của cùng claim đều PASS. |
| Đối chứng | `fixed C-002@55`: event 3 là `quote {amount_vnd: 0, reason: "no_entitlement", clause_ids: []}`, **không có** `model_override`; sau đó `reply` "chưa đủ điều kiện hoàn". Ledger `refund_vnd=0`, `refund_count=0`, status `denied` khớp nhãn, grader Lab 8 PASS. Hai trace khác nhau đúng ở một chỗ: có hay không có event `model_override` ở bước 3. |
| Regression test | `tests/test_lab9_regression.py::test_trace_case_c002_seed55_is_caught_on_swarm_and_clean_on_fixed`. Chạy shape thật trên store tạm và khẳng định: (1) `model_override` là event thứ 3 với payload `{delay_days: 5}`; (2) `TrialPass` và `DbFactKept` đều **False** trên `swarm`, ledger 20.000 so với nhãn 0; (3) cả hai **True** trên `fixed`. Đã thấy **fail trên bản lỗi (swarm@55) và pass trên `fixed`**. Test đi kèm `..._other_seeds_of_c002_are_fine_on_swarm` chứng minh lỗi chỉ ở seed % 5 == 0. Cũng đưa vào gate: `db_fact_kept` bắt nguyên nhân (step level), `refund_amount_correct` và `pass_rate` bắt hậu quả (outcome level). |
| Chưa chắc chắn | (a) Trace không chứng minh *vì sao* swarm gốc chỉ tin ticket ở seed % 5 == 0; tôi đọc điều đó từ code, và đó là điểm yếu cố ý của Lab 8 để lab này có thứ để bắt. (b) MockLLM xác định: lỗi thật với LLM thật sẽ ngẫu nhiên hơn, và 5 seed cho tỉ lệ 1/5 chỉ là một mẫu rất nhỏ. (c) Chỉ 1 trong 70 trial lỗi nên CI của `pass_rate` rộng: tự thân con số 0.9857 không chứng minh được swarm "gần như hoàn hảo". |

## Cách đọc trace (quy tắc đã áp dụng)

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai (xem FM-08).
- "Bước sai đầu tiên" dẫn số event cụ thể (event 3), không phải "agent bị nhầm".
- Case hoàn thành khi có regression test chạy được (đã thêm vào `tests/`).

## Bài học cho eval

- Một lỗi 1/5 seed vẫn bị bắt vì `db_fact_kept` chấm **từng event**, không chờ tới kết quả cuối.
- Muốn lỗi này không lọt, gate cần cả metric step-level lẫn outcome-level; chỉ nhìn `pass_rate` trung bình sẽ thấy 0.9857 và bỏ qua.
