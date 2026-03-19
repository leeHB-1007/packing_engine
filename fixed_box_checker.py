from __future__ import annotations

from math import ceil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from master_loader import (
    load_master_workbook,
    prepare_boxes_for_engine,
    prepare_packages_for_engine,
    prepare_products_for_engine,
)
from matcher import RawOrderLine, match_order_lines
from repack_engine import (
    _calc_best_orientation_fit,
    _calc_trim_info,
    build_repack_candidates,
)


DEFAULT_MASTER = Path("data/packing_engine_normalized_masters_ko_json_schema_fixed.xlsx")


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"y", "yes", "true", "1", "필요", "예", "있음"}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(round(float(value)))
    except Exception:
        return default


def _to_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _fmt_qty(value: Any) -> str:
    num = _to_float(value, None)
    if num is None:
        return "-"
    if abs(num - round(num)) < 1e-9:
        return f"{int(round(num)):,}"
    return f"{num:,.2f}".rstrip("0").rstrip(".")


def _fmt_weight(value: Any) -> str:
    """
    최종 표시용 중량 포맷
    - 보수적으로 0.1kg 추가
    - 소수점 첫째 자리까지 표시
    """
    try:
        if value is None or value == "":
            return "-"
        adjusted = float(value) + 0.1
        return f"{adjusted:.1f}"
    except Exception:
        return "-"

def _fmt_cm(value: Any) -> str:
    num = _to_float(value, None)
    if num is None:
        return "-"
    return f"{num:,.1f}".rstrip("0").rstrip(".")


def _norm_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\n", " ").replace("\r", " ")
    text = " ".join(text.split())
    return text.strip()


def _norm_key(value: Any) -> str:
    return _norm_text(value).replace(" ", "").lower()


def _unit_label(calc_unit: Any) -> str:
    text = str(calc_unit or "").strip().lower()
    if text in {"item", "ea", "each", "unit", "개"}:
        return "ea"
    if text in {"package", "pkg", "pack"}:
        return "package"
    if not text:
        return "ea"
    return str(calc_unit)


def _build_display_qty(calc_qty: Any, calc_unit: Any, input_qty: Any = None) -> str:
    calc_unit_norm = _unit_label(calc_unit)
    main_text = f"{_fmt_qty(calc_qty)}{calc_unit_norm}"

    input_num = _to_float(input_qty, None)
    calc_num = _to_float(calc_qty, None)

    if calc_unit_norm != "ea" and input_num is not None and calc_num is not None:
        if abs(input_num - calc_num) > 1e-9:
            return f"{main_text}({_fmt_qty(input_qty)}ea)"

    return main_text


def _normalize_order_lines(order_lines: List[Any]) -> List[RawOrderLine]:
    if not isinstance(order_lines, list) or not order_lines:
        raise ValueError("order_lines는 1개 이상 들어있는 list 형태여야 합니다.")

    normalized: List[RawOrderLine] = []

    for idx, item in enumerate(order_lines, start=1):
        if isinstance(item, RawOrderLine):
            product_name = str(
                getattr(item, "item_text", getattr(item, "raw_text", ""))
            ).strip()
            qty = int(item.qty)

        elif isinstance(item, dict):
            product_name = (
                item.get("product_name")
                or item.get("name")
                or item.get("상품명")
                or item.get("input_name")
            )
            qty = item.get("qty", item.get("수량"))

            if product_name is None or qty is None:
                raise ValueError(f"{idx}번째 주문 dict에 product_name/상품명 또는 qty/수량이 없습니다.")

            product_name = str(product_name).strip()
            qty = int(qty)

        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            product_name = str(item[0]).strip()
            qty = int(item[1])

        else:
            raise ValueError(
                f"{idx}번째 주문 형식이 올바르지 않습니다. "
                f"dict, tuple, list, RawOrderLine 중 하나여야 합니다."
            )

        if not product_name:
            raise ValueError(f"{idx}번째 주문의 상품명이 비어 있습니다.")

        if qty <= 0:
            raise ValueError(f"{idx}번째 주문의 수량은 1 이상이어야 합니다. / 상품명={product_name}")

        normalized.append(RawOrderLine(product_name, qty))

    return normalized


def _build_box_lookup(prepared_boxes_df) -> List[dict]:
    results = []

    for _, row in prepared_boxes_df.iterrows():
        results.append(
            {
                "박스코드": _norm_text(row.get("박스코드")),
                "박스명": _norm_text(row.get("박스명")),
                "외경가로(cm)": float(row.get("외경가로(cm)", 0) or 0),
                "외경세로(cm)": float(row.get("외경세로(cm)", 0) or 0),
                "외경높이(cm)": float(row.get("외경높이(cm)", 0) or 0),
                "내경가로(cm)": float(row.get("내경가로(cm)", 0) or 0),
                "내경세로(cm)": float(row.get("내경세로(cm)", 0) or 0),
                "내경높이(cm)": float(row.get("내경높이(cm)", 0) or 0),
                "박스중량(kg)": float(row.get("박스중량(kg)", 0) or 0),
                "최대허용중량(kg)": float(row.get("최대허용중량(kg)", 0) or 0),
                "박스정렬우선순위": float(row.get("박스정렬우선순위", 999999) or 999999),
            }
        )

    return results


def _same_outer_dims(box: dict, outer_size_cm: Tuple[float, float, float], tol: float = 0.1) -> bool:
    return (
        abs(float(box["외경가로(cm)"]) - float(outer_size_cm[0])) <= tol
        and abs(float(box["외경세로(cm)"]) - float(outer_size_cm[1])) <= tol
        and abs(float(box["외경높이(cm)"]) - float(outer_size_cm[2])) <= tol
    )


def _find_fixed_box(
    prepared_boxes_df,
    box_query: str | None = None,
    outer_size_cm: Tuple[float, float, float] | None = None,
) -> dict:
    boxes = _build_box_lookup(prepared_boxes_df)

    if not boxes:
        raise ValueError("사용 가능한 박스 데이터가 없습니다.")

    if outer_size_cm is not None:
        matches = [box for box in boxes if _same_outer_dims(box, outer_size_cm)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return sorted(matches, key=lambda x: x["박스정렬우선순위"])[0]

    query = _norm_key(box_query)
    if query:
        exact_matches = []
        contains_matches = []

        for box in boxes:
            code_key = _norm_key(box["박스코드"])
            name_key = _norm_key(box["박스명"])

            if query in {code_key, name_key, name_key.replace("박스", "")}:
                exact_matches.append(box)
                continue

            if query in code_key or query in name_key:
                contains_matches.append(box)

        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            return sorted(exact_matches, key=lambda x: x["박스정렬우선순위"])[0]
        if len(contains_matches) == 1:
            return contains_matches[0]
        if len(contains_matches) > 1:
            return sorted(contains_matches, key=lambda x: x["박스정렬우선순위"])[0]

    raise ValueError("지정한 박스를 찾지 못했습니다. box_query 또는 outer_size_cm를 다시 확인해주세요.")


def _build_forced_remainder_result(matched_orders: List[Any]) -> Dict[str, List[dict]]:
    remainders = []
    not_found = []

    for row in matched_orders:
        if getattr(row, "matched", False) and getattr(row, "matched_name", None):
            remainders.append(
                {
                    "product_name": row.matched_name,
                    "qty": int(row.qty),
                    "reason": "FIXED_BOX_CHECK",
                }
            )
        else:
            not_found.append(
                {
                    "product_name": getattr(row, "item_text", getattr(row, "raw_input", None)),
                    "qty": getattr(row, "qty", None),
                    "reason": getattr(row, "message", None) or "PRODUCT_NOT_FOUND",
                }
            )

    return {
        "remainders": remainders,
        "not_found": not_found,
    }

def _apply_policy_max_units(item: dict, physical_max_units: int, selected_box: dict | None = None) -> int:
    """
    회사 정책 cap 적용

    우선순위
    1. 72x48x40 + 특정 제품군(엘라스티/셀로좀/리플렌젠/리체스/드메이) -> 300 고정
    2. SPECIAL_XXX -> XXX cap
    3. 그 외 -> physical max
    """
    packing_policy_code = _norm_text(item.get("packing_policy_code", "")).upper()
    product_name = _norm_text(item.get("product_name", ""))

    final_max_units = int(physical_max_units)

    # -------------------------------------------------
    # 회사 고정 규칙:
    # 72x48x40 박스에서는 특정 제품군 300ea 기준
    # -------------------------------------------------
    if selected_box is not None:
        box_l = float(selected_box.get("외경가로(cm)", 0) or 0)
        box_w = float(selected_box.get("외경세로(cm)", 0) or 0)
        box_h = float(selected_box.get("외경높이(cm)", 0) or 0)

        is_72_48_40 = (
            abs(box_l - 72.0) <= 0.1
            and abs(box_w - 48.0) <= 0.1
            and abs(box_h - 40.0) <= 0.1
        )

        is_preferred_300_product = any(
            keyword in product_name
            for keyword in ["엘라스티", "셀로좀", "리플렌젠", "리체스", "드메이"]
        )

        if is_72_48_40 and is_preferred_300_product:
            return 300

    # -------------------------------------------------
    # 일반 SPECIAL_XXX 정책
    # -------------------------------------------------
    if packing_policy_code.startswith("SPECIAL_"):
        try:
            policy_limit = int(packing_policy_code.replace("SPECIAL_", "").strip())
            if policy_limit > 0:
                final_max_units = min(final_max_units, policy_limit)
        except Exception:
            pass

    return max(0, int(final_max_units))

def _calc_units_by_weight_local(box: dict, item: dict, rules: dict) -> int:
    box_weight = float(box["박스중량(kg)"])
    box_limit = float(box["최대허용중량(kg)"])
    global_limit = float(rules.get("BOX_MAX_WEIGHT_KG", 30))
    unit_weight = float(item["unit_weight_kg"])

    effective_limit = min(box_limit, global_limit)

    if unit_weight <= 0 or effective_limit <= box_weight:
        return 0

    return max(0, int((effective_limit - box_weight) // unit_weight))


def _calc_mixed_layer_capacity_split_by_width(
    floor_l: float,
    floor_w: float,
    item_l: float,
    item_w: float,
) -> int:
    """
    바닥면을 width 방향 strip으로 나눠서
    정방향(item_l x item_w) / 회전방향(item_w x item_l) 혼합 적재 최대치 계산
    """
    if min(floor_l, floor_w, item_l, item_w) <= 0:
        return 0

    best = 0

    max_normal_rows = int(floor_w // item_w)
    max_rot_rows = int(floor_w // item_l)

    normal_per_row = int(floor_l // item_l)
    rot_per_row = int(floor_l // item_w)

    for normal_rows in range(max_normal_rows + 1):
        used_w = normal_rows * item_w
        remain_w = floor_w - used_w
        rot_rows = int(remain_w // item_l)
        total = (normal_rows * normal_per_row) + (rot_rows * rot_per_row)
        if total > best:
            best = total

    for rot_rows in range(max_rot_rows + 1):
        used_w = rot_rows * item_l
        remain_w = floor_w - used_w
        normal_rows = int(remain_w // item_w)
        total = (rot_rows * rot_per_row) + (normal_rows * normal_per_row)
        if total > best:
            best = total

    return best


def _calc_mixed_layer_capacity_split_by_length(
    floor_l: float,
    floor_w: float,
    item_l: float,
    item_w: float,
) -> int:
    """
    바닥면을 length 방향 strip으로 나눠서
    정방향(item_l x item_w) / 회전방향(item_w x item_l) 혼합 적재 최대치 계산
    """
    if min(floor_l, floor_w, item_l, item_w) <= 0:
        return 0

    best = 0

    max_normal_cols = int(floor_l // item_l)
    max_rot_cols = int(floor_l // item_w)

    normal_per_col = int(floor_w // item_w)
    rot_per_col = int(floor_w // item_l)

    for normal_cols in range(max_normal_cols + 1):
        used_l = normal_cols * item_l
        remain_l = floor_l - used_l
        rot_cols = int(remain_l // item_w)
        total = (normal_cols * normal_per_col) + (rot_cols * rot_per_col)
        if total > best:
            best = total

    for rot_cols in range(max_rot_cols + 1):
        used_l = rot_cols * item_w
        remain_l = floor_l - used_l
        normal_cols = int(remain_l // item_l)
        total = (rot_cols * rot_per_col) + (normal_cols * normal_per_col)
        if total > best:
            best = total

    return best


def _calc_mixed_layer_capacity(
    floor_l: float,
    floor_w: float,
    item_l: float,
    item_w: float,
) -> int:
    """
    한 층 바닥면에서 90도 혼합 회전 허용 기준 최대 적재량
    """
    best = 0

    # 순수 단일 회전 적재
    best = max(
        best,
        int(floor_l // item_l) * int(floor_w // item_w),
        int(floor_l // item_w) * int(floor_w // item_l),
    )

    # width strip 혼합
    best = max(
        best,
        _calc_mixed_layer_capacity_split_by_width(floor_l, floor_w, item_l, item_w),
    )

    # length strip 혼합
    best = max(
        best,
        _calc_mixed_layer_capacity_split_by_length(floor_l, floor_w, item_l, item_w),
    )

    return best


def _calc_best_mixed_rotation_fit(
    box: dict,
    item: dict,
    qty: int,
    rules: dict,
) -> dict:
    """
    박스 한 층 바닥면에서 혼합 회전을 허용했을 때 최대 적재량 계산
    - 세워지는 높이축은 1개로 고정
    - 같은 층 바닥면에서만 90도 혼합 회전 허용
    """
    box_l = float(box["내경가로(cm)"])
    box_w = float(box["내경세로(cm)"])
    box_h = float(box["내경높이(cm)"])

    dims = [
        float(item["length_cm"]),
        float(item["width_cm"]),
        float(item["height_cm"]),
    ]

    units_by_weight = _calc_units_by_weight_local(box, item, rules)

    best_result = {
        "global_best_units_by_space": 0,
        "global_best_max_units_per_box": 0,
        "global_best_orientation": None,
        "layer_capacity": 0,
        "layers_per_box": 0,
        "chosen_height": 0.0,
    }

    for height_idx in range(3):
        item_h = dims[height_idx]
        footprint = [dims[i] for i in range(3) if i != height_idx]

        if len(footprint) != 2:
            continue

        item_l, item_w = footprint[0], footprint[1]

        if min(item_l, item_w, item_h) <= 0:
            continue

        layers_per_box = int(box_h // item_h)
        if layers_per_box <= 0:
            continue

        layer_capacity = _calc_mixed_layer_capacity(
            floor_l=box_l,
            floor_w=box_w,
            item_l=item_l,
            item_w=item_w,
        )

        if layer_capacity <= 0:
            continue

        units_by_space = int(layer_capacity * layers_per_box)
        max_units = min(int(units_by_space), int(units_by_weight))

        if max_units > int(best_result["global_best_max_units_per_box"]):
            best_result = {
                "global_best_units_by_space": int(units_by_space),
                "global_best_max_units_per_box": int(max_units),
                "global_best_orientation": (item_l, item_w, item_h),
                "layer_capacity": int(layer_capacity),
                "layers_per_box": int(layers_per_box),
                "chosen_height": float(item_h),
            }

    return best_result

def _evaluate_on_fixed_box(
    repack_result: Dict[str, List[dict]],
    selected_box: dict,
    rules: dict,
) -> Dict[str, Any]:
    fit_results = []
    no_fit = []

    for item in repack_result.get("candidates", []):
        qty = int(item["qty"])

        uniform_fit = _calc_best_orientation_fit(selected_box, item, qty, rules)
        mixed_fit = _calc_best_mixed_rotation_fit(selected_box, item, qty, rules)

        recommended_max_units = int(uniform_fit["recommended_max_units_per_box"])
        recommended_orientation = uniform_fit["best_orientation"]

        uniform_physical_max_units = int(uniform_fit["global_best_max_units_per_box"])
        uniform_best_orientation = uniform_fit["global_best_orientation"]

        mixed_physical_max_units = int(mixed_fit["global_best_max_units_per_box"])
        mixed_best_orientation = mixed_fit["global_best_orientation"]

        if mixed_physical_max_units > uniform_physical_max_units:
            physical_max_units = mixed_physical_max_units
            physical_best_orientation = mixed_best_orientation
            mixed_rotation_applied = True
            layer_capacity_for_trim = int(mixed_fit.get("layer_capacity", 0))
        else:
            physical_max_units = uniform_physical_max_units
            physical_best_orientation = uniform_best_orientation
            mixed_rotation_applied = False
            layer_capacity_for_trim = 0

        final_max_units = _apply_policy_max_units(item, physical_max_units, selected_box)

        if final_max_units <= 0 or physical_best_orientation is None:
            no_fit.append(
                {
                    "product_name": item["product_name"],
                    "qty": qty,
                    "original_qty": item.get("original_qty", qty),
                    "calc_unit_type": item.get("calc_unit_type", "item"),
                    "package_pack_qty": item.get("package_pack_qty", 1),
                    "reason": "FIXED_BOX_NO_FIT",
                }
            )
            continue

        boxes_needed = int(ceil(qty / final_max_units))
        first_box_qty = min(qty, final_max_units)

        trim_info = _calc_trim_info(
            inner_size_cm=(
                float(selected_box["내경가로(cm)"]),
                float(selected_box["내경세로(cm)"]),
                float(selected_box["내경높이(cm)"]),
            ),
            outer_size_cm=(
                float(selected_box["외경가로(cm)"]),
                float(selected_box["외경세로(cm)"]),
                float(selected_box["외경높이(cm)"]),
            ),
            orientation=physical_best_orientation,
            item_qty=first_box_qty,
            keep_height_cm=2.0,
        )

        gross_weight_est = float(selected_box["박스중량(kg)"]) + (first_box_qty * float(item["unit_weight_kg"]))

        fit_results.append(
            {
                "product_name": item["product_name"],
                "original_qty": item.get("original_qty", qty),
                "calc_qty": qty,
                "calc_unit_type": item.get("calc_unit_type", "item"),
                "package_pack_qty": item.get("package_pack_qty", 1),
                "spec_source": item.get("spec_source", ""),
                "packing_policy_code": item.get("packing_policy_code", ""),
                "selected_box_code": selected_box["박스코드"],
                "selected_box_name": selected_box["박스명"],
                "outer_size_cm": (
                    float(selected_box["외경가로(cm)"]),
                    float(selected_box["외경세로(cm)"]),
                    float(selected_box["외경높이(cm)"]),
                ),
                "inner_size_cm": (
                    float(selected_box["내경가로(cm)"]),
                    float(selected_box["내경세로(cm)"]),
                    float(selected_box["내경높이(cm)"]),
                ),
                "can_fit": True,
                "boxes_needed": boxes_needed,
                "max_units_per_box": final_max_units,
                "physical_max_units_per_box": physical_max_units,
                "recommended_max_units_per_box": recommended_max_units,
                "global_best_max_units_per_box": final_max_units,
                "best_orientation": recommended_orientation,
                "global_best_orientation": physical_best_orientation,
                "mixed_rotation_applied": mixed_rotation_applied,
                "mixed_layer_capacity": layer_capacity_for_trim,
                "units_by_weight": max(
                    int(uniform_fit["units_by_weight"]),
                    int(mixed_fit.get("global_best_max_units_per_box", 0)),
                ),
                "recommended_units_by_space": int(uniform_fit["recommended_units_by_space"]),
                "gross_weight_est_first_box": round(gross_weight_est, 3),
                "trim_info_first_box": trim_info,
            }
        )

    return {
        "fit_results": fit_results,
        "no_fit": no_fit,
        "unresolved": repack_result.get("unresolved", []),
        "invalid_specs": repack_result.get("invalid_specs", []),
    }

def _format_issue_block(title: str, items: List[dict]) -> List[str]:
    if not items:
        return []

    lines = [title]
    for item in items:
        product_name = item.get("product_name", "상품명없음")
        qty = item.get("qty", "-")
        reason = item.get("reason", "-")
        lines.append(f"- {product_name} / {qty} / {reason}")
    return lines


def format_fixed_box_check_result(result: Dict[str, Any]) -> str:
    selected_box = result["selected_box"]

    lines: List[str] = []
    lines.append("[FIXED BOX CHECK]")
    lines.append(
        f"기준박스: {selected_box['박스명']} ({selected_box['박스코드']}) / "
        f"{_fmt_cm(selected_box['외경가로(cm)'])} x {_fmt_cm(selected_box['외경세로(cm)'])} x {_fmt_cm(selected_box['외경높이(cm)'])} cm"
    )
    lines.append("")
    lines.append("※ 현재 버전은 상품별 단독 기준 검사입니다.")
    lines.append("※ 여러 상품을 한 박스에 동시에 섞는 혼합 적재 최적화는 아직 미지원입니다.")

    fit_results = result.get("fit_results", [])
    no_fit = result.get("no_fit", [])

    if fit_results:
        lines.append("")
        lines.append("[fit_results]")
        for idx, row in enumerate(fit_results, start=1):
            qty_text = _build_display_qty(
                calc_qty=row.get("calc_qty"),
                calc_unit=row.get("calc_unit_type"),
                input_qty=row.get("original_qty"),
            )
            lines.append(
                f"{idx}. {row['product_name']} / 가능 / "
                f"{qty_text} / {row['boxes_needed']}박스 필요 / "
                f"박스당 최대 {row['global_best_max_units_per_box']}{_unit_label(row.get('calc_unit_type'))}"
            )

    if no_fit:
        lines.append("")
        lines.append("[no_fit]")
        for idx, row in enumerate(no_fit, start=1):
            qty_text = _build_display_qty(
                calc_qty=row.get("qty"),
                calc_unit=row.get("calc_unit_type"),
                input_qty=row.get("original_qty"),
            )
            lines.append(
                f"{idx}. {row['product_name']} / 불가 / {qty_text} / 해당 박스 1개에도 적재 불가"
            )

    for block in [
        _format_issue_block("[unresolved]", result.get("unresolved", [])),
        _format_issue_block("[invalid_specs]", result.get("invalid_specs", [])),
        _format_issue_block("[not_found]", result.get("not_found", [])),
    ]:
        if block:
            lines.append("")
            lines.extend(block)

    return "\n".join(lines).strip()

def run_fixed_box_check(
    order_lines: List[Any],
    box_query: str | None = None,
    outer_size_cm: Tuple[float, float, float] | None = None,
    master_path: str | Path | None = None,
) -> Dict[str, Any]:
    master_file = Path(master_path) if master_path else DEFAULT_MASTER

    raw_orders = _normalize_order_lines(order_lines)

    load_result = load_master_workbook(master_file)

    prepared_products = prepare_products_for_engine(
        load_result["products"],
        load_result["fullboxes"],
    )
    prepared_boxes = prepare_boxes_for_engine(
        load_result["boxes"],
    )
    prepared_packages = prepare_packages_for_engine(
        load_result["packages"],
    )

    selected_box = _find_fixed_box(
        prepared_boxes_df=prepared_boxes,
        box_query=box_query,
        outer_size_cm=outer_size_cm,
    )

    matched_orders = match_order_lines(
        raw_order_lines=raw_orders,
        prepared_products=prepared_products,
        fullboxes_master=load_result["fullboxes"],
        packages_master=load_result["packages"],
    )

    forced_remainder_result = _build_forced_remainder_result(matched_orders)

    repack_result = build_repack_candidates(
        fullbox_result=forced_remainder_result,
        prepared_products_df=prepared_products,
        prepared_packages_df=prepared_packages,
        fallback_fullboxes_df=load_result["fullboxes"],
    )

    eval_result = _evaluate_on_fixed_box(
        repack_result=repack_result,
        selected_box=selected_box,
        rules=load_result["rules"],
    )

    final_result = {
        "selected_box": selected_box,
        "fit_results": eval_result["fit_results"],
        "no_fit": eval_result["no_fit"],
        "unresolved": eval_result["unresolved"],
        "invalid_specs": eval_result["invalid_specs"],
        "not_found": forced_remainder_result["not_found"],
    }
    final_result["formatted_text"] = format_fixed_box_check_result(final_result)

    return final_result


if __name__ == "__main__":
    sample_orders = [
        {"product_name": "원톡스 100u", "qty": 50},
        {"product_name": "셀로좀 미드 리도", "qty": 250},
        {"product_name": "없는상품 테스트", "qty": 10},
    ]

    result = run_fixed_box_check(
        order_lines=sample_orders,
        box_query="72x48x40",
    )

    print(result["formatted_text"])
