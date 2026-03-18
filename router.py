# router.py
# 역할:
# 1. 주문 데이터를 받는다.
# 2. fixed_box_mix_checker를 먼저 시도한다.
# 3. 적재 가능하면 그 결과를 사용한다.
# 4. 불가능하면 run_packing_engine으로 넘긴다.
# 5. 마지막엔 사람이 보기 쉬운 요약을 출력한다.
# 6. final_plans를 못 찾으면 result 구조를 진단해서 보여준다.

import os
import sys
import json
import inspect
import importlib
from pprint import pprint
from test_orders import TEST_ORDERS, get_test_order


DEFAULT_FIXED_BOX_CODE = "BX010"
DEFAULT_FIXED_BOX_NAME = "72x48x40"
DEFAULT_FIXED_BOX_OUTER_SIZE_CM = [72.0, 48.0, 40.0]

DEBUG = False
SHOW_RAW_RESULT = False


SAMPLE_ORDER_ITEMS = [
    {"product_name": "비에녹스200u", "qty": 75},
    {"product_name": "리체스 딥 리도(C)", "qty": 225},
    {"product_name": "엘라스티 D 플러스(1syr)", "qty": 25},
]


def load_order_items_from_json(json_path):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("JSON 파일은 반드시 리스트 형태여야 합니다. 예: [{...}, {...}]")

    return [normalize_order_item(x) for x in data]


def normalize_order_item(item):
    if not isinstance(item, dict):
        raise ValueError(f"주문 항목이 dict 형태가 아닙니다: {item}")

    product_name = (
        item.get("product_name")
        or item.get("name")
        or item.get("상품명")
        or item.get("product")
    )

    qty = (
        item.get("qty")
        if item.get("qty") is not None
        else item.get("quantity")
        if item.get("quantity") is not None
        else item.get("수량")
    )

    if not product_name:
        raise ValueError(f"상품명이 없습니다: {item}")

    if qty is None:
        raise ValueError(f"수량이 없습니다: {item}")

    try:
        qty = int(qty)
    except Exception:
        raise ValueError(f"수량이 숫자가 아닙니다: {item}")

    return {
        "product_name": str(product_name).strip(),
        "qty": qty,
    }


def normalize_order_items(order_items):
    return [normalize_order_item(x) for x in order_items]


def import_module_safely(module_name):
    return importlib.import_module(module_name)


def find_candidate_function(module, candidate_names):
    for name in candidate_names:
        func = getattr(module, name, None)
        if callable(func):
            return func
    return None


def stringify_result(result):
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return str(result)


def result_looks_like_fit(result):
    """
    fixed_box_mix_checker 결과가
    '전량 1박스 적재 가능'인지 판정
    """

    if not isinstance(result, dict):
        text = stringify_result(result).lower()
        return "전량 1박스 적재 가능" in text or "1박스 적재 가능" in text

    # 1) 가장 확실한 구조 기반 판정
    pack_failed = bool(result.get("pack_failed", False))
    boxes = result.get("boxes", [])
    no_fit = result.get("no_fit", [])
    unresolved = result.get("unresolved", [])
    invalid_specs = result.get("invalid_specs", [])
    not_found = result.get("not_found", [])

    if (
        pack_failed is False
        and isinstance(boxes, list)
        and len(boxes) == 1
        and not no_fit
        and not unresolved
        and not invalid_specs
        and not not_found
    ):
        return True

    # 2) formatted_text 기준 판정
    formatted_text = str(result.get("formatted_text", "")).lower()
    if "전량 1박스 적재 가능" in formatted_text:
        return True

    # 3) 마지막 fallback: 전체 문자열 검색
    text = stringify_result(result).lower()

    positive_keywords = [
        "전량 1박스 적재 가능",
        "1박스 적재 가능",
        "적재 가능",
        "can fit",
        "all fit",
        "passed",
    ]
    negative_keywords = [
        "전량 1박스 적재 불가",
        "적재 불가",
        "cannot fit",
        "no fit",
        "failed",
    ]

    has_positive = any(k in text for k in positive_keywords)
    has_negative = any(k in text for k in negative_keywords)

    return has_positive and not has_negative

def call_function_flexibly(func, payload):
    sig = inspect.signature(func)
    params = sig.parameters

    if len(params) == 0:
        return func()

    candidate_payload = {
        "order_items": payload.get("order_items"),
        "items": payload.get("order_items"),
        "orders": payload.get("order_items"),
        "products": payload.get("order_items"),
        "product_list": payload.get("order_items"),

        "base_box_code": payload.get("base_box_code"),
        "box_code": payload.get("box_code"),
        "fixed_box_code": payload.get("fixed_box_code"),

        "base_box_name": payload.get("base_box_name"),
        "box_name": payload.get("box_name"),

        "box_query": payload.get("box_query"),
        "outer_size_cm": payload.get("outer_size_cm"),
        "target_outer_size_cm": payload.get("outer_size_cm"),

        "debug": payload.get("debug"),
        "verbose": payload.get("debug"),
    }

    kwargs = {}
    positional_args = []

    for i, (param_name, param) in enumerate(params.items()):
        if param.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            if param_name in candidate_payload and candidate_payload[param_name] is not None:
                kwargs[param_name] = candidate_payload[param_name]
            else:
                if i == 0 and payload.get("order_items") is not None:
                    positional_args.append(payload.get("order_items"))

        elif param.kind == inspect.Parameter.KEYWORD_ONLY:
            if param_name in candidate_payload and candidate_payload[param_name] is not None:
                kwargs[param_name] = candidate_payload[param_name]

    try:
        if positional_args:
            return func(*positional_args, **kwargs)
        return func(**kwargs)
    except TypeError:
        return func(payload.get("order_items"))


def try_call_with_payload_variants(func, payload_variants):
    last_error = None
    for payload in payload_variants:
        try:
            return call_function_flexibly(func, payload)
        except Exception as e:
            last_error = e
    if last_error:
        raise last_error
    raise RuntimeError("payload_variants가 비어 있습니다.")


def find_key_deep(obj, target_key, max_depth=8):
    found = []

    def _walk(x, path, depth):
        if depth > max_depth:
            return

        if isinstance(x, dict):
            for k, v in x.items():
                new_path = f"{path}.{k}" if path else k
                if k == target_key:
                    found.append((new_path, v))
                _walk(v, new_path, depth + 1)

        elif isinstance(x, list):
            for i, v in enumerate(x):
                new_path = f"{path}[{i}]"
                _walk(v, new_path, depth + 1)

    _walk(obj, "", 0)
    return found


def extract_actual_engine_result(result):
    if not isinstance(result, dict):
        return result

    priority_keys = ["final_plans", "no_box_fit", "unresolved", "invalid_specs"]
    deep_found = {k: find_key_deep(result, k) for k in priority_keys}

    # final_plans가 발견되면 그 final_plans를 가진 가장 가까운 dict를 찾음
    if deep_found["final_plans"]:
        candidate_paths = [p for p, _ in deep_found["final_plans"]]
        shortest = sorted(candidate_paths, key=len)[0]

        # path의 마지막 key를 제거해서 parent dict path를 얻음
        parent_path = shortest.rsplit(".", 1)[0] if "." in shortest else ""

        parent = get_by_path(result, parent_path)
        if isinstance(parent, dict):
            return parent

    # 기존 방식 fallback
    current = result
    for _ in range(6):
        if not isinstance(current, dict):
            break

        if any(k in current for k in priority_keys):
            return current

        next_current = None
        for key in ["result", "data", "output", "payload", "final_result"]:
            if key in current and isinstance(current[key], dict):
                next_current = current[key]
                break

        if next_current is None:
            break

        current = next_current

    return result


def get_by_path(obj, path):
    if path == "" or path is None:
        return obj

    current = obj
    tokens = []
    temp = path.replace("[", ".[").split(".")

    for t in temp:
        if t == "":
            continue
        tokens.append(t)

    for token in tokens:
        if token.startswith("[") and token.endswith("]"):
            idx = int(token[1:-1])
            if not isinstance(current, list):
                return None
            if idx >= len(current):
                return None
            current = current[idx]
        else:
            if not isinstance(current, dict):
                return None
            if token not in current:
                return None
            current = current[token]

    return current


def calc_estimated_box_weight_kg(box_info, qty):
    """
    최종 표시용 예상중량
    - 상품중량 합 + 박스중량
    - 보수적으로 0.1kg 추가
    - 소수점 첫째 자리까지 표시
    """
    try:
        unit_weight = float(box_info.get("unit_weight_kg", 0))
        box_weight = float(box_info.get("box_weight_kg", 0))
        adjusted = (unit_weight * qty + box_weight) + 0.1
        return round(adjusted, 1)
    except Exception:
        return None

def print_result_structure_diagnosis(result):
    print("\n[진단]")
    if isinstance(result, dict):
        print("top-level keys:", list(result.keys()))
    else:
        print("result type:", type(result).__name__)

    for key in ["final_plans", "no_box_fit", "unresolved", "invalid_specs", "result", "data", "output", "payload"]:
        found = find_key_deep(result, key)
        if found:
            print(f"- '{key}' 발견 위치:")
            for path, value in found[:5]:
                value_type = type(value).__name__
                extra = ""
                if isinstance(value, list):
                    extra = f" (len={len(value)})"
                elif isinstance(value, dict):
                    extra = f" (keys={list(value.keys())[:10]})"
                print(f"  · {path} -> {value_type}{extra}")


def summarize_fixed_box_result(result):
    print("\n[최종 요약]")
    print("선택 엔진: fixed_box_mix_checker")

    if isinstance(result, str):
        print(result)
        return

    if not isinstance(result, dict):
        print(stringify_result(result))
        return

    # fixed_box_mix_checker는 formatted_text를 이미 잘 만들어주고 있음
    formatted_text = result.get("formatted_text")
    if formatted_text:
        print(formatted_text)
        return

    # formatted_text가 없을 때만 수동 요약
    selected_box = result.get("selected_box", {})
    boxes = result.get("boxes", [])
    capacity_reference = result.get("capacity_reference", [])
    pack_failed = bool(result.get("pack_failed", False))
    pack_failed_reason = result.get("pack_failed_reason", "")
    no_fit = result.get("no_fit", [])
    unresolved = result.get("unresolved", [])
    invalid_specs = result.get("invalid_specs", [])
    not_found = result.get("not_found", [])

    if selected_box:
        box_name = selected_box.get("박스명", "-")
        box_code = selected_box.get("박스코드", "-")
        w = selected_box.get("외경가로(cm)", "-")
        d = selected_box.get("외경세로(cm)", "-")
        h = selected_box.get("외경높이(cm)", "-")
        print(f"기준박스: {box_name} ({box_code}) / {w} x {d} x {h} cm")

    if pack_failed:
        print(f"판정: 합포 계산 실패 / {pack_failed_reason}")
    else:
        if len(boxes) == 1:
            print("판정: 전량 1박스 적재 가능")
        else:
            print(f"판정: 전량 1박스 적재 불가 / 총 {len(boxes)}박스 필요")
        print(f"총 박스수: {len(boxes)}박스")

    if capacity_reference:
        print("\n[capacity_reference]")
        for idx, row in enumerate(capacity_reference, start=1):
            product_name = row.get("product_name", "-")
            calc_qty = row.get("calc_qty", "-")
            calc_unit_type = row.get("calc_unit_type", "item")
            pack_qty = row.get("package_pack_qty", 1)
            max_units = row.get("max_units_per_box", "-")
            fill_ratio = float(row.get("fill_ratio", 0) or 0)

            if str(calc_unit_type).lower() == "package":
                qty_text = f"{calc_qty}package({int(calc_qty) * int(pack_qty)}ea)"
                cap_text = f"{max_units}package"
            else:
                qty_text = f"{calc_qty}ea"
                cap_text = f"{max_units}ea"

            print(
                f"{idx}. {product_name} / {qty_text} / 박스당최대 {cap_text} / 점유율 {round(fill_ratio * 100, 2)}%"
            )

    if boxes:
        print("\n[box_summary]")
        for box in boxes:
            box_no = box.get("box_no", "-")
            used_fill_ratio = float(box.get("used_fill_ratio", 0) or 0)
            gross_weight_est = box.get("gross_weight_est", "-")
            print(f"BOX {box_no} / 점유율={round(used_fill_ratio * 100, 2)}% / 예상총중량={gross_weight_est} kg")

            for item in box.get("items", []):
                product_name = item.get("product_name", "-")
                qty = item.get("qty", "-")
                calc_unit_type = item.get("calc_unit_type", "item")
                pack_qty = item.get("package_pack_qty", 1)
                fill_ratio = float(item.get("fill_ratio", 0) or 0)

                if str(calc_unit_type).lower() == "package":
                    qty_text = f"{qty}package({int(qty) * int(pack_qty)}ea)"
                else:
                    qty_text = f"{qty}ea"

                print(f"- {product_name} / {qty_text} / 점유율 {round(fill_ratio * 100, 2)}%")

    if no_fit:
        print(f"\n[no_fit] {no_fit}")
    if unresolved:
        print(f"\n[unresolved] {unresolved}")
    if invalid_specs:
        print(f"\n[invalid_specs] {invalid_specs}")
    if not_found:
        print(f"\n[not_found] {not_found}")

def summarize_run_engine_result(result):
    print("\n[최종 요약]")
    print("선택 엔진: run_packing_engine")

    actual = extract_actual_engine_result(result)

    final_plans = []
    no_box_fit = []
    unresolved = []
    invalid_specs = []
    not_found = []

    if isinstance(actual, dict):
        final_plans = actual.get("final_plans", [])
        no_box_fit = actual.get("no_box_fit", [])
        unresolved = actual.get("unresolved", [])
        invalid_specs = actual.get("invalid_specs", [])

    # 바깥 result에도 not_found / unresolved 등이 있을 수 있어서 추가 탐색
    found_not_found = find_key_deep(result, "not_found")
    for _, value in found_not_found:
        if isinstance(value, list):
            not_found.extend(value)

    found_unresolved = find_key_deep(result, "unresolved")
    for _, value in found_unresolved:
        if isinstance(value, list):
            unresolved.extend(value)

    found_invalid_specs = find_key_deep(result, "invalid_specs")
    for _, value in found_invalid_specs:
        if isinstance(value, list):
            invalid_specs.extend(value)

    found_no_box_fit = find_key_deep(result, "no_box_fit")
    for _, value in found_no_box_fit:
        if isinstance(value, list):
            no_box_fit.extend(value)

    # 중복 제거
    def _dedupe_dict_list(items):
        seen = set()
        deduped = []
        for item in items:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            if key not in seen:
                seen.add(key)
                deduped.append(item)
        return deduped

    not_found = _dedupe_dict_list(not_found)
    unresolved = _dedupe_dict_list(unresolved)
    invalid_specs = _dedupe_dict_list(invalid_specs)
    no_box_fit = _dedupe_dict_list(no_box_fit)

    # 1) 정상 final_plans가 있으면 기존처럼 출력
    if final_plans:
        total_boxes = 0

        for idx, plan in enumerate(final_plans, start=1):
            product_name = plan.get("product_name", "-")
            qty = plan.get("original_qty", plan.get("qty", "-"))
            calc_unit_type = plan.get("calc_unit_type", "")
            package_pack_qty = plan.get("package_pack_qty", "")

            recommended_box = plan.get("recommended_box", {})
            selected_box_name = (
                plan.get("selected_box_name")
                or recommended_box.get("box_name")
                or "-"
            )
            selected_box_code = (
                plan.get("selected_box_code")
                or recommended_box.get("box_code")
                or "-"
            )

            boxes_needed = (
                plan.get("boxes_needed")
                or recommended_box.get("boxes_needed")
                or 0
            )

            trim_info = recommended_box.get("first_box_trim_info", {})
            trimmed_outer_height = trim_info.get("trimmed_outer_height_cm")

            qty_for_weight = plan.get("qty")
            if qty_for_weight is None:
                qty_for_weight = qty if isinstance(qty, int) else 0

            estimated_weight = None
            if recommended_box:
                estimated_weight = calc_estimated_box_weight_kg(recommended_box, qty_for_weight)

            try:
                total_boxes += int(boxes_needed)
            except Exception:
                pass

            print(f"\n{idx}. {product_name}")
            print(f"- 수량: {qty}")
            if calc_unit_type:
                print(f"- 계산단위: {calc_unit_type}")
            if package_pack_qty not in ["", None]:
                print(f"- 패키지입수: {package_pack_qty}")
            print(f"- 추천박스: {selected_box_name} ({selected_box_code})")
            print(f"- 필요박스수: {boxes_needed}")

            if trimmed_outer_height is not None:
                print(f"- 제단후 높이(외경): {trimmed_outer_height} cm")

            if estimated_weight is not None:
                print(f"- 예상중량: {estimated_weight} kg")

        print(f"\n총 박스수: {total_boxes}박스")

        if no_box_fit:
            print("\n[no_box_fit]")
            for item in no_box_fit:
                print(f"- {item}")
        if unresolved:
            print("\n[unresolved]")
            for item in unresolved:
                print(f"- {item}")
        if invalid_specs:
            print("\n[invalid_specs]")
            for item in invalid_specs:
                print(f"- {item}")
        if not_found:
            print("\n[not_found]")
            for item in not_found:
                print(f"- {item}")

        return

    # 2) final_plans는 없지만 예외 정보가 있으면 예외 중심으로 출력
    if not_found or unresolved or invalid_specs or no_box_fit:
        print("미등록 또는 처리 불가 상품이 있습니다.")

        if not_found:
            print("\n[not_found]")
            for item in not_found:
                if isinstance(item, dict):
                    product_name = item.get("product_name", "상품명없음")
                    qty = item.get("qty", "-")
                    reason = item.get("reason", "미등록 상품")
                    print(f"- {product_name} / {qty} / {reason}")
                else:
                    print(f"- {item}")

        if unresolved:
            print("\n[unresolved]")
            for item in unresolved:
                if isinstance(item, dict):
                    product_name = item.get("product_name", "상품명없음")
                    qty = item.get("qty", "-")
                    reason = item.get("reason", "-")
                    print(f"- {product_name} / {qty} / {reason}")
                else:
                    print(f"- {item}")

        if invalid_specs:
            print("\n[invalid_specs]")
            for item in invalid_specs:
                if isinstance(item, dict):
                    product_name = item.get("product_name", "상품명없음")
                    qty = item.get("qty", "-")
                    reason = item.get("reason", "-")
                    print(f"- {product_name} / {qty} / {reason}")
                else:
                    print(f"- {item}")

        if no_box_fit:
            print("\n[no_box_fit]")
            for item in no_box_fit:
                if isinstance(item, dict):
                    product_name = item.get("product_name", "상품명없음")
                    qty = item.get("qty", "-")
                    reason = item.get("reason", "-")
                    print(f"- {product_name} / {qty} / {reason}")
                else:
                    print(f"- {item}")
        return

    # 3) 진짜 아무 정보도 없을 때만 진단 출력
    print("final_plans가 없습니다.")
    print_result_structure_diagnosis(result)
    if SHOW_RAW_RESULT:
        print("\n[RAW RESULT]")
        print(stringify_result(result))

def print_final_summary(selected_engine, result):
    if selected_engine == "fixed_box_mix_checker":
        summarize_fixed_box_result(result)
    else:
        summarize_run_engine_result(result)

def evaluate_test_case(case_name, final_result):
    """
    test_orders.py의 expected 와 실제 결과를 비교해서
    PASS / FAIL 간단 판정
    """
    if case_name not in TEST_ORDERS:
        return

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

    # 3) 체크포인트 문자열 검사
    check_point = expected.get("check_point")
    if check_point:
        result_text = stringify_result(result)
        if check_point in result_text:
            pass_reasons.append(f"check_point OK: {check_point}")
        else:
            # 일부 케이스는 사람이 보는 문구용이라 실패 대신 참고로만 처리
            pass_reasons.append(f"check_point 참고: {check_point}")

    # 출력
    print("\n[TEST RESULT]")
    if fail_reasons:
        print("FAIL")
        for reason in fail_reasons:
            print(f"- {reason}")
    else:
        print("PASS")
        for reason in pass_reasons:
            print(f"- {reason}")

def run_fixed_box_mix_checker(order_items, debug=False):
    module = import_module_safely("fixed_box_mix_checker")

    candidate_names = [
        "run_fixed_box_mix_check",
        "fixed_box_mix_check",
        "check_fixed_box_mix",
        "run_checker",
        "run",
        "main",
    ]

    func = find_candidate_function(module, candidate_names)
    if func is None:
        raise AttributeError("fixed_box_mix_checker.py 안에서 실행 함수를 찾지 못했습니다.")

    payload_variants = [
        {"order_items": order_items, "box_query": DEFAULT_FIXED_BOX_NAME, "debug": debug},
        {"order_items": order_items, "outer_size_cm": DEFAULT_FIXED_BOX_OUTER_SIZE_CM, "debug": debug},
        {"order_items": order_items, "box_code": DEFAULT_FIXED_BOX_CODE, "debug": debug},
        {
            "order_items": order_items,
            "base_box_code": DEFAULT_FIXED_BOX_CODE,
            "base_box_name": DEFAULT_FIXED_BOX_NAME,
            "fixed_box_code": DEFAULT_FIXED_BOX_CODE,
            "box_query": DEFAULT_FIXED_BOX_NAME,
            "outer_size_cm": DEFAULT_FIXED_BOX_OUTER_SIZE_CM,
            "debug": debug,
        },
    ]

    return try_call_with_payload_variants(func, payload_variants)


def run_main_packing_engine(order_items, debug=False):
    module = import_module_safely("run_packing_engine")

    candidate_names = [
        "run_packing_engine",
        "run_engine",
        "packing_engine_run",
        "run",
        "main",
    ]

    func = find_candidate_function(module, candidate_names)
    if func is None:
        raise AttributeError("run_packing_engine.py 안에서 실행 함수를 찾지 못했습니다.")

    payload = {
        "order_items": order_items,
        "debug": debug,
    }

    return call_function_flexibly(func, payload)


def route_packing(order_items, debug=False):
    order_items = normalize_order_items(order_items)

    print("\n" + "=" * 90)
    print("[ROUTER] 주문 라우팅 시작")
    print("=" * 90)
    pprint(order_items, sort_dicts=False)

    fixed_result = None
    fixed_ok = False

    print("\n[STEP 1] fixed_box_mix_checker 시도")
    try:
        fixed_result = run_fixed_box_mix_checker(order_items=order_items, debug=debug)
        fixed_ok = result_looks_like_fit(fixed_result)
    except Exception as e:
        print(f"[fixed_box_mix_checker 오류] {e}")

    if fixed_ok:
        print("[ROUTER] fixed_box_mix_checker 결과 채택")
        return {
            "selected_engine": "fixed_box_mix_checker",
            "result": fixed_result,
        }

    print("[STEP 2] run_packing_engine fallback 실행")
    engine_result = run_main_packing_engine(order_items=order_items, debug=debug)

    print("[ROUTER] run_packing_engine 결과 채택")
    return {
        "selected_engine": "run_packing_engine",
        "result": engine_result,
    }


def main():
    case_name = None

    if len(sys.argv) >= 2:
        arg1 = sys.argv[1]

        # 1) 테스트 케이스 이름으로 실행
        if arg1 in TEST_ORDERS:
            case_name = arg1
            order_items = get_test_order(arg1)
            print(f"[TEST CASE] {arg1}")
            print(f"[DESCRIPTION] {TEST_ORDERS[arg1].get('description', '')}")

        # 2) JSON 파일로 실행
        elif os.path.exists(arg1) and arg1.lower().endswith(".json"):
            json_path = arg1
            order_items = load_order_items_from_json(json_path)

        # 3) 둘 다 아니면 안내 후 종료
        else:
            print(f"알 수 없는 입력입니다: {arg1}")
            print("사용 방법:")
            print("1) python router.py")
            print("2) python router.py sample_order.json")
            print("3) python router.py case_01_fixed_box_1box_ok")
            print("\n사용 가능한 테스트 케이스:")
            for test_case_name, case_info in TEST_ORDERS.items():
                print(f"- {test_case_name} / {case_info.get('description', '')}")
            return
    else:
        order_items = SAMPLE_ORDER_ITEMS

    final_result = route_packing(order_items=order_items, debug=DEBUG)

    print("\n" + "=" * 90)
    print("[FINAL ROUTER RESULT]")
    print("=" * 90)
    print(f"선택 엔진: {final_result['selected_engine']}")

    print_final_summary(
        selected_engine=final_result["selected_engine"],
        result=final_result["result"],
    )

    if case_name:
        evaluate_test_case(case_name, final_result)


if __name__ == "__main__":
    main()