# run_all_tests.py
# 모든 테스트 케이스를 한 번에 실행하는 배치 테스트 파일

import json
from test_orders import TEST_ORDERS, get_test_order
from router import route_packing, stringify_result


def evaluate_case(case_name, final_result):
    expected = TEST_ORDERS[case_name].get("expected", {})
    selected_engine = final_result.get("selected_engine")
    result = final_result.get("result", {})

    pass_reasons = []
    fail_reasons = []

    # 1) 엔진 기대값 체크
    expected_engine = expected.get("selected_engine")
    if expected_engine:
        if "_or_" in expected_engine:
            allowed = expected_engine.split("_or_")
            if selected_engine in allowed:
                pass_reasons.append(f"selected_engine OK: {selected_engine}")
            else:
                fail_reasons.append(
                    f"expected selected_engine in {allowed}, actual={selected_engine}"
                )
        else:
            if selected_engine == expected_engine:
                pass_reasons.append(f"selected_engine OK: {selected_engine}")
            else:
                fail_reasons.append(
                    f"expected selected_engine={expected_engine}, actual={selected_engine}"
                )

    # 2) 박스 수 기대값 체크
    if selected_engine == "fixed_box_mix_checker" and isinstance(result, dict):
        boxes = result.get("boxes", [])
        actual_box_count = len(boxes)

        if "boxes" in expected:
            expected_boxes = expected["boxes"]
            if actual_box_count == expected_boxes:
                pass_reasons.append(f"boxes OK: {actual_box_count}")
            else:
                fail_reasons.append(
                    f"expected boxes={expected_boxes}, actual={actual_box_count}"
                )

        if "boxes_min" in expected:
            expected_boxes_min = expected["boxes_min"]
            if actual_box_count >= expected_boxes_min:
                pass_reasons.append(f"boxes_min OK: {actual_box_count}")
            else:
                fail_reasons.append(
                    f"expected boxes>={expected_boxes_min}, actual={actual_box_count}"
                )

    # 3) 체크포인트
    check_point = expected.get("check_point")
    if check_point:
        result_text = stringify_result(result)
        if check_point in result_text:
            pass_reasons.append(f"check_point OK: {check_point}")
        else:
            pass_reasons.append(f"check_point 참고: {check_point}")

    is_pass = len(fail_reasons) == 0
    return {
        "is_pass": is_pass,
        "pass_reasons": pass_reasons,
        "fail_reasons": fail_reasons,
        "selected_engine": selected_engine,
    }


def run_all_tests():
    total = 0
    passed = 0
    failed = 0

    print("=" * 100)
    print("[ALL TESTS START]")
    print("=" * 100)

    for case_name, case_info in TEST_ORDERS.items():
        total += 1
        description = case_info.get("description", "")
        orders = get_test_order(case_name)

        print("\n" + "-" * 100)
        print(f"[TEST CASE] {case_name}")
        print(f"[DESCRIPTION] {description}")

        try:
            final_result = route_packing(order_items=orders, debug=False)
            eval_result = evaluate_case(case_name, final_result)

            print(f"[SELECTED ENGINE] {eval_result['selected_engine']}")

            if eval_result["is_pass"]:
                passed += 1
                print("[RESULT] PASS")
                for reason in eval_result["pass_reasons"]:
                    print(f"- {reason}")
            else:
                failed += 1
                print("[RESULT] FAIL")
                for reason in eval_result["fail_reasons"]:
                    print(f"- {reason}")

        except Exception as e:
            failed += 1
            print("[RESULT] FAIL")
            print(f"- 실행 중 오류 발생: {e}")

    print("\n" + "=" * 100)
    print("[ALL TESTS SUMMARY]")
    print("=" * 100)
    print(f"총 테스트: {total}")
    print(f"PASS: {passed}")
    print(f"FAIL: {failed}")


if __name__ == "__main__":
    run_all_tests()