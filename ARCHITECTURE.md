# L3B Architecture Record

Tài liệu thiết kế kiến trúc hệ thống Multi-Agent điều tra khiếu nại thương mại điện tử (K4 L3B - Day09 V2).

## 1. System overview

Luồng điều tra phối hợp giữa các Agent chuyên biệt theo mô hình DAG:

```text
Input (Case) ──► Coordinator ──► Entity Resolver ──► Specialists (Order, Shipment, Payment)
                                                           │
                                                           ▼
Output ◄── Verifier ◄── Policy & Conflict Resolver ◄───────┘
```
- Mọi tương tác dữ liệu nghiệp vụ đều thông qua **MCP Gateway** và ghi nhận **Observable Trace** tương ứng.

## 2. Agent ownership

Áp dụng nghiêm ngặt nguyên tắc **Least Privilege** (Quyền tối thiểu):

| Actor | Input | Trách nhiệm | Tool permission | Output/handoff |
| --- | --- | --- | --- | --- |
| `coordinator` | `case` object từ inputs | Tiếp nhận vụ việc, điều phối và giao việc cho các Specialist | *Không gọi tool dữ liệu* | `case_received`, `task_assigned` |
| `entity_agent` | `candidate_order_ids`, `customer_unique_id_hint` | Xác định `order_id` thật, loại bỏ candidate sai, truy vết khách hàng | `get_order`, `get_customer_history` | `entity_resolution`, `customer_context` $\rightarrow$ Handoff sang Specialists |
| `order_specialist` | `primary_order_id` | Lấy chi tiết món hàng, thông tin người bán | `get_order_items`, `get_sellers`, `get_product_context` | `affected_entities.item_ids`, `affected_entities.seller_ids` |
| `shipment_agent` | `primary_order_id` | Phân tích tiến độ vận chuyển, đối soát hạn giao hàng của người bán | `get_shipment_summary` | `shipment_analysis` (`seller_delay` vs `logistics_delay`) |
| `payment_agent` | `primary_order_id` | Đối soát giao dịch, tính tổng tiền đã thu, đã hoàn và còn được hoàn | `get_order_payments`, `get_refund_timeline` | `payment_analysis`, `financial_resolution` |
| `conflict_resolver`| Báo cáo từ các Specialist & khiếu nại khách hàng | Tra cứu chính sách sàn, phân xử xung đột giữa các nguồn, xác định nguyên nhân gốc rễ và trách nhiệm | `get_policy` | `assessment.primary_issue`, `data_conflicts`, `root_cause_analysis` |
| `verifier` | Bản nháp output tổng hợp | Kiểm định Schema Draft 2020-12, kiểm tra tính nhất quán logic (Invariants), chốt độ tin cậy | *Không gọi tool* | `verification_completed`, Final JSON Output |

## 3. Entity resolution và A2A protocol

- **Phân giải Candidate:** Duyệt từng `candidate_order_id` qua `get_order`. Candidate hợp lệ trả về dữ liệu đơn hàng sẽ được đưa vào `resolved_order_ids`. Các candidate ném ngoại lệ hoặc không tồn tại sẽ bị đưa vào `rejected_candidates`.
- **Confidence Threshold:** Đặt `confidence = 0.95` nếu tìm thấy đúng đơn hàng thật (`status: "resolved"`), và `0.50` nếu không tìm thấy (`status: "not_found"`).
- **Correlation theo `case_id`:** Mọi thông điệp và lệnh gọi tool bắt buộc mang `case_id` tương ứng, nghiêm cấm dùng chéo bằng chứng giữa các case.
- **Handoff:** Chuyển giao trạng thái rõ ràng giữa các Agent (`task_assigned` $\rightarrow$ `tool_result_consumed` $\rightarrow$ `handoff`).

## 4. Evidence và conflict lifecycle

- **Validation:** Mọi phản hồi từ MCP Gateway được kiểm định qua schema `mcp-evidence-response-v1.schema.json`.
- **Bảo toàn `evidence_ref`:** Tất cả các mã `evidence_ref` (dạng `ev_...`) được thu thập trực tiếp từ Gateway thật, lưu vào bộ đệm và gắn trực tiếp vào trường `evidence_refs` của output và các sự kiện `tool_result_consumed`.
- **Conflict Resolution:** Khi có mâu thuẫn giữa khiếu nại khách hàng (ví dụ báo shipper giao trễ) và dữ liệu giao vận thực tế (ví dụ tracking chứng minh giao đúng hẹn), hệ thống ưu tiên nguồn dữ liệu hệ thống (`carrier_tracking`) và ghi nhận vào `data_conflicts` với `resolution_code: "PREFER_CARRIER_TIMESTAMP"`.

## 5. Failure and efficiency policy

| Failure | Retry budget | Fallback | Trace event/code |
| --- | ---: | --- | --- |
| MCP connection drop / timeout | 3 lần | Tự động exponential backoff (1.5s, 3.0s) | Retry tại connection level |
| Entity not found / invalid candidate | 1 lần / candidate | Ghi nhận vào `rejected_candidates`, set status `not_found` | `status: "not_found"` |
| Refund timeline missing | 1 lần | Mặc định `refunded_total_brl = 0.0` | `payment_verdict: "reconciled"` |
| LLM API unavailable (402/timeout) | 2 lần | Kích hoạt Rule Engine phân tích tất định từ MCP Evidence | Fallback logic nội bộ |

- **Efficiency Policy:** Mỗi case chỉ gọi từ 3 đến 5 tool cần thiết nhất, sử dụng phiên kết nối độc lập cho từng case nhằm tránh timeout và tối ưu điểm `efficiency`.

## 6. Verification invariants

Trước khi xuất hồ sơ vụ việc (`case_finalized`), Verifier kiểm tra các bất biến:
1. `case_id` phải khớp 100% với file input.
2. `recommended_refund_brl` luôn $\le$ `refundable_total_brl`.
3. Nếu `recommended_refund_brl > 0` thì `case_status` phải là `"action_required"` và có action `"issue_refund_via_payment_method"`.
4. Nếu `late_seller_ids` có phần tử thì trong `responsible_parties` bắt buộc phải có `"party_type": "seller"`.
5. Nếu `primary_issue == "unsupported_claim"` thì `case_status` là `"no_action"` và `recommended_refund_brl = 0.0`.
6. Tất cả `evidence_refs` phải là danh sách duy nhất (unique), không trùng lặp và thuộc về chính case đó.

## 7. Reproducibility

- **Môi trường:** Python 3.11+, Windows 11 / Linux.
- **Dependencies:** `mcp>=2,<3`, `httpx2>=2,<3`, `jsonschema>=4.25,<5`, `python-dotenv>=1.1,<2`, `openai>=3.11`.
- **Lệnh thực thi:**
  ```powershell
  day09 validate-inputs
  day09 run
  day09 validate
  day09 package --output dist/submission.zip
  ```
- **Tài nguyên:** CPU thông thường (không đòi hỏi GPU bắt buộc nhờ cơ chế Hybrid Neuro-Symbolic).
