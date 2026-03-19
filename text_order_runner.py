# text_order_runner.py
# 실사용용 텍스트 주문 입력 실행기
# 사용 예:
#   python text_order_runner.py
#
# 실행 흐름:
# 1) 주문 여러 줄 입력
# 2) 패킹리스트 필요 여부 입력 (yes / no)
# 3) 결과 출력

import re
from router import route_packing, print_final_summary
from sulu_med_exporter import build_router_display_payload


def parse_order_text(text: str):
    order_items = []
    lines = text.splitlines()

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        # 앞 번호/기호 제거
        line = re.sub(r"^\s*(\d+\s*[\.\)]\s*|[-•]\s*)", "", line)

        # 뒤에서 수량 찾기
        m = re.search(r"(.+?)\s+(\d+)\s*(개|ea)?\s*$", line, flags=re.IGNORECASE)
        if not m:
            raise ValueError(f"주문 줄 해석 실패: {raw_line}")

        product_name = m.group(1).strip()
        qty = int(m.group(2))

        if not product_name:
            raise ValueError(f"상품명 해석 실패: {raw_line}")

        order_items.append({
            "product_name": product_name,
            "qty": qty,
        })

    if not order_items:
        raise ValueError("주문이 비어 있습니다.")

    return order_items


def read_multiline_input():
    print("=" * 90)
    print("[주문 텍스트 입력]")
    print("=" * 90)
    print("아래처럼 주문을 여러 줄로 붙여넣으세요.")
    print("예)")
    print("비에녹스200u 75")
    print("리체스 딥 리도(C) 225")
    print("엘라스티 D 플러스(1syr) 25")
    print("")
    print("입력이 끝나면 빈 줄을 2번 누르세요.")
    print("-" * 90)

    lines = []
    blank_count = 0

    while True:
        line = input()

        if line.strip() == "":
            blank_count += 1
        else:
            blank_count = 0

        if blank_count >= 2:
            break

        lines.append(line)

    return "\n".join(lines).strip()


def read_packing_list_mode():
    print("\n" + "=" * 90)
    print("[패킹리스트 필요 여부]")
    print("=" * 90)
    print("yes / no 로 입력하세요. (한글도 가능: 예 / 아니오 / 필요 / 불필요)")

    while True:
        value = input("입력: ").strip().lower()

        if value in ["yes", "y", "예", "필요", "패킹리스트 yes", "패킹리스트 필요"]:
            return True

        if value in ["no", "n", "아니오", "불필요", "패킹리스트 no", "패킹리스트 불필요"]:
            return False

        print("잘못된 입력입니다. yes 또는 no 로 다시 입력해주세요.")


def _fmt_weight_display(value):
    try:
        adjusted = float(value) + 0.1
        return f"{adjusted:.1f} kg"
    except Exception:
        return "-"


def _fmt_precise_weight_display(value):
    try:
        num = float(value)
    except Exception:
        return "-"

    return f"{num:.3f}".rstrip("0").rstrip(".") + " kg"


def _build_plan_size_text(plan):
    outer_size = plan.get("outer_size_cm", [])
    if not isinstance(outer_size, (list, tuple)) or len(outer_size) != 3:
        return "-"

    trimmed_outer_height = None
    box_lines = plan.get("box_lines", []) or []
    if box_lines:
        trimmed_outer_height = box_lines[0].get("trimmed_outer_height_cm")

    dims = [outer_size[0], outer_size[1], outer_size[2]]
    if trimmed_outer_height not in (None, ""):
        dims[2] = trimmed_outer_height

    def _fmt_size(v):
        try:
            f = float(v)
            if f.is_integer():
                return str(int(f))
            return str(f)
        except Exception:
            return str(v)

    return f"{_fmt_size(dims[0])} x {_fmt_size(dims[1])} x {_fmt_size(dims[2])}"


def _print_fixed_box_clean(result):
    selected_box = result.get("selected_box", {})
    boxes = result.get("boxes", [])

    outer_w = selected_box.get("외경가로(cm)", "-")
    outer_d = selected_box.get("외경세로(cm)", "-")
    outer_h = selected_box.get("외경높이(cm)", "-")

    def _fmt_size(v):
        try:
            f = float(v)
            if f.is_integer():
                return str(int(f))
            return str(f)
        except Exception:
            return str(v)

    print("\n" + "=" * 90)
    print("[최종 결과]")
    print("=" * 90)
    print(f"총 박스수: {len(boxes)}박스")

    for idx, box in enumerate(boxes, start=1):
        gross_weight = box.get("gross_weight_est", "-")
        print(
            f"\n{idx}. "
            f"{_fmt_size(outer_w)} x {_fmt_size(outer_d)} x {_fmt_size(outer_h)} "
            f"/ 1박스 / {_fmt_weight_display(gross_weight)}"
        )


def _calc_box_weight_for_plan(plan, recommended_box):
    try:
        qty = plan.get("qty")
        if qty is None:
            qty = plan.get("original_qty", 0)

        unit_weight = float(recommended_box.get("unit_weight_kg", 0))
        box_weight = float(recommended_box.get("box_weight_kg", 0))

        # 표시용 규칙: +0.1kg, 소수점 첫째 자리
        return round(unit_weight * float(qty) + box_weight + 0.1, 1)
    except Exception:
        return None


def _print_run_engine_clean(result):
    actual = result
    if isinstance(result, dict) and "final_result" in result and isinstance(result["final_result"], dict):
        actual = result["final_result"]

    final_plans = actual.get("final_plans", []) if isinstance(actual, dict) else []
    unresolved = actual.get("unresolved", []) if isinstance(actual, dict) else []
    invalid_specs = actual.get("invalid_specs", []) if isinstance(actual, dict) else []
    no_box_fit = actual.get("no_box_fit", []) if isinstance(actual, dict) else []

    not_found = []
    if isinstance(result, dict):
        value = result.get("not_found")
        if isinstance(value, list):
            not_found.extend(value)

    if not final_plans:
        print("\n" + "=" * 90)
        print("[최종 결과]")
        print("=" * 90)

        if not_found or unresolved or invalid_specs or no_box_fit:
            print("처리 불가 항목이 있습니다.")

            if not_found:
                print("\n[not_found]")
                for item in not_found:
                    if isinstance(item, dict):
                        print(f"- {item.get('product_name', '상품명없음')} / {item.get('qty', '-')} / {item.get('reason', '-')}")
                    else:
                        print(f"- {item}")

            if unresolved:
                print("\n[unresolved]")
                for item in unresolved:
                    if isinstance(item, dict):
                        print(f"- {item.get('product_name', '상품명없음')} / {item.get('qty', '-')} / {item.get('reason', '-')}")
                    else:
                        print(f"- {item}")

            if invalid_specs:
                print("\n[invalid_specs]")
                for item in invalid_specs:
                    if isinstance(item, dict):
                        print(f"- {item.get('product_name', '상품명없음')} / {item.get('qty', '-')} / {item.get('reason', '-')}")
                    else:
                        print(f"- {item}")

            if no_box_fit:
                print("\n[no_box_fit]")
                for item in no_box_fit:
                    if isinstance(item, dict):
                        print(f"- {item.get('product_name', '상품명없음')} / {item.get('qty', '-')} / {item.get('reason', '-')}")
                    else:
                        print(f"- {item}")
        else:
            print("결과를 찾지 못했습니다.")
        return

    print("\n" + "=" * 90)
    print("[최종 결과]")
    print("=" * 90)

    total_boxes = 0
    for plan in final_plans:
        recommended_box = plan.get("recommended_box", {})
        boxes_needed = plan.get("boxes_needed") or recommended_box.get("boxes_needed") or 0
        try:
            total_boxes += int(boxes_needed)
        except Exception:
            pass

    print(f"총 박스수: {total_boxes}박스")

    for idx, plan in enumerate(final_plans, start=1):
        product_name = plan.get("product_name", "-")
        boxes_needed = plan.get("boxes_needed") or 0
        size_text = _build_plan_size_text(plan)

        box_lines = plan.get("box_lines", []) or []
        gross_weight = None
        if box_lines:
            gross_weight = box_lines[0].get("gross_weight_est")

        print(
            f"\n{idx}. {product_name} / {size_text} / {boxes_needed}박스 / "
            f"{_fmt_precise_weight_display(gross_weight)}"
        )


def _format_box_range(group):
    start = int(group.get("box_no_start", 0) or 0)
    end = int(group.get("box_no_end", 0) or 0)

    if start and end and start != end:
        return f"{start}~{end}"
    if start:
        return str(start)
    return "-"


def _print_router_grouped_clean(final_result):
    payload = build_router_display_payload(final_result)
    groups = payload.get("groups", []) or []
    issues = payload.get("issues", []) or []

    print("\n" + "=" * 90)
    print("[최종 결과]")
    print("=" * 90)

    total_boxes = sum(int(group.get("box_count", 0) or 0) for group in groups)
    print(f"총 박스수: {total_boxes}박스")

    for idx, group in enumerate(groups, start=1):
        product_names = ", ".join(
            str(item.get("product_name", "") or "").strip()
            for item in group.get("item_rows", []) or []
            if str(item.get("product_name", "") or "").strip()
        )
        box_range = _format_box_range(group)
        box_size = str(group.get("box_size_cm", "") or "").strip() or "-"
        box_count = int(group.get("box_count", 0) or 0)
        total_weight = group.get("total_weight_kg", "-")

        print(
            f"\n{idx}. {box_range} / {product_names or '-'} / "
            f"{box_size} / {box_count}박스 / {_fmt_precise_weight_display(total_weight)}"
        )

    if issues:
        suppress_from_not_found = set()
        for _, issue_key, product_name, qty, reason in issues:
            if issue_key in {"ambiguous", "unresolved"}:
                suppress_from_not_found.add((product_name, qty, reason))

        issue_groups = {}
        for _, issue_key, product_name, qty, reason in issues:
            if issue_key == "not_found" and (product_name, qty, reason) in suppress_from_not_found:
                continue
            issue_groups.setdefault(issue_key, []).append((product_name, qty, reason))

        for issue_key, rows in issue_groups.items():
            print(f"\n[{issue_key}]")
            for product_name, qty, reason in rows:
                label = product_name or "상품명없음"
                print(f"- {label} / {qty or '-'} / {reason or '-'}")


def print_clean_final_result(final_result):
    selected_engine = final_result.get("selected_engine")
    result = final_result.get("result", {})

    if selected_engine == "fixed_box_mix_checker":
        _print_fixed_box_clean(result)
    else:
        try:
            _print_router_grouped_clean(final_result)
        except Exception:
            _print_run_engine_clean(result)


def main():
    try:
        raw_text = read_multiline_input()
        order_items = parse_order_text(raw_text)
        need_packing_list = read_packing_list_mode()

        print("\n" + "=" * 90)
        print("[파싱된 주문]")
        print("=" * 90)
        for idx, item in enumerate(order_items, start=1):
            print(f"{idx}. {item['product_name']} / {item['qty']}")

        final_result = route_packing(order_items=order_items, debug=False)

        if need_packing_list:
            print("\n" + "=" * 90)
            print("[패킹리스트 출력]")
            print("=" * 90)
            print(f"선택 엔진: {final_result['selected_engine']}")
            print_final_summary(
                selected_engine=final_result["selected_engine"],
                result=final_result["result"],
            )
        else:
            print_clean_final_result(final_result)

    except Exception as e:
        print("\n[ERROR]")
        print(str(e))


if __name__ == "__main__":
    main()
