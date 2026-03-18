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
        recommended_box = plan.get("recommended_box", {})

        outer_size = recommended_box.get("outer_size_cm", [])
        if isinstance(outer_size, (list, tuple)) and len(outer_size) == 3:
            def _fmt_size(v):
                try:
                    f = float(v)
                    if f.is_integer():
                        return str(int(f))
                    return str(f)
                except Exception:
                    return str(v)

                # not reached
            size_text = f"{_fmt_size(outer_size[0])} x {_fmt_size(outer_size[1])} x {_fmt_size(outer_size[2])}"
        else:
            size_text = "-"

        boxes_needed = plan.get("boxes_needed") or recommended_box.get("boxes_needed") or 0
        weight = _calc_box_weight_for_plan(plan, recommended_box)

        print(f"\n{idx}. {product_name} / {size_text} / {boxes_needed}박스 / {_fmt_weight_display(weight)}")


def print_clean_final_result(final_result):
    selected_engine = final_result.get("selected_engine")
    result = final_result.get("result", {})

    if selected_engine == "fixed_box_mix_checker":
        _print_fixed_box_clean(result)
    else:
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