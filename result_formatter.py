from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


def format_engine_result(result: Dict[str, Any], packing_list_needed: Any = False) -> str:
    packing_list_needed = _to_bool(packing_list_needed)

    if not isinstance(result, dict) or not result:
        return "[no_result]"

    rows = _collect_rows(result)

    lines: List[str] = []

    if packing_list_needed:
        lines.extend(_format_packing_list(rows))
    else:
        lines.extend(_format_final_only(rows))

    issue_lines = _format_issues(result)
    if issue_lines:
        lines.append("")
        lines.extend(issue_lines)

    return "\n".join(lines).strip()


# =========================================================
# 공통 유틸
# =========================================================

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
    num = _to_float(value, None)
    if num is None:
        return "-"
    return f"{num:,.3f}".rstrip("0").rstrip(".")


def _fmt_cm(value: Any) -> str:
    num = _to_float(value, None)
    if num is None:
        return "-"
    return f"{num:,.1f}".rstrip("0").rstrip(".")


def _has_meaningful_number(value: Any) -> bool:
    num = _to_float(value, None)
    return num is not None


def _is_positive_number(value: Any) -> bool:
    num = _to_float(value, None)
    return num is not None and num > 0


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

def _parse_dims(value: Any) -> Optional[Tuple[float, float, float]]:
    if value is None:
        return None

    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return (float(value[0]), float(value[1]), float(value[2]))
        except Exception:
            return None

    if isinstance(value, str):
        nums = re.findall(r"-?\d+(?:\.\d+)?", value)
        if len(nums) >= 3:
            try:
                return (float(nums[0]), float(nums[1]), float(nums[2]))
            except Exception:
                return None

    return None


def _build_cut_box_display(box_label: str, outer_dims: Any, cut_height: Any, trimmed_outer_height: Any) -> str:
    """
    규칙:
    - 제단 박스면 치수만 출력
    - 제단 안 하면 4호/5호 같은 박스명만 출력
    """
    dims = _parse_dims(outer_dims)
    cut_height_f = _to_float(cut_height, None)
    trimmed_outer_height_f = _to_float(trimmed_outer_height, None)

    box_name_only = str(box_label).split("(")[0].strip()
    if not box_name_only:
        box_name_only = str(box_label)

    is_cut = False

    if cut_height_f is not None and cut_height_f > 0:
        is_cut = True
    elif dims and trimmed_outer_height_f is not None:
        if trimmed_outer_height_f < float(dims[2]):
            is_cut = True

    if is_cut and dims:
        final_h = trimmed_outer_height_f
        if final_h is None and cut_height_f is not None:
            final_h = float(dims[2]) - cut_height_f
        if final_h is None:
            final_h = float(dims[2])

        return f"{_fmt_cm(dims[0])} x {_fmt_cm(dims[1])} x {_fmt_cm(final_h)} cm"

    return box_name_only


# =========================================================
# 실제 결과 구조 전용 파서
# =========================================================

def _collect_rows(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    final_plans = result.get("final_plans") or []
    if not isinstance(final_plans, list):
        return rows

    for plan in final_plans:
        if not isinstance(plan, dict):
            continue

        product_name = str(plan.get("product_name", "상품명없음"))
        input_qty = plan.get("original_qty")
        calc_qty = plan.get("total_qty")
        calc_unit = _unit_label(plan.get("calc_unit_type", "ea"))
        package_in_qty = plan.get("package_pack_qty")

        box_code = plan.get("selected_box_code")
        box_name = plan.get("selected_box_name")
        outer_dims = plan.get("outer_size_cm")
        inner_dims = plan.get("inner_size_cm")

        if box_name and box_code:
            box_label = f"{box_name} ({box_code})"
        elif box_name:
            box_label = str(box_name)
        elif box_code:
            box_label = str(box_code)
        else:
            box_label = "박스정보없음"

        max_capacity = plan.get("global_best_max_units_per_box")
        recommended_capacity = plan.get("units_per_box")
        recommended_rotation = plan.get("best_orientation")
        max_rotation = plan.get("global_best_orientation")
        spec_source = plan.get("spec_source")

        box_lines = plan.get("box_lines") or []

        if isinstance(box_lines, list) and box_lines:
            for idx, box in enumerate(box_lines, start=1):
                if not isinstance(box, dict):
                    continue

                row_box_code = box.get("box_code", box_code)
                row_box_name = box.get("box_name", box_name)

                if row_box_name and row_box_code:
                    row_box_label = f"{row_box_name} ({row_box_code})"
                elif row_box_name:
                    row_box_label = str(row_box_name)
                elif row_box_code:
                    row_box_label = str(row_box_code)
                else:
                    row_box_label = box_label

                rows.append(
                    {
                        "product_name": product_name,
                        "box_label": row_box_label,
                        "box_no": box.get("box_no", idx),
                        "box_count": 1,
                        "input_qty": input_qty,
                        "calc_qty": box.get("qty", calc_qty),
                        "calc_unit": calc_unit,
                        "package_in_qty": package_in_qty,
                        "estimated_weight": box.get("gross_weight_est"),
                        "per_layer": box.get("layer_capacity"),
                        "layers": box.get("layers_needed"),
                        "used_height": box.get("used_height_cm"),
                        "remaining_height": box.get("remaining_height_cm"),
                        "cut_height": box.get("trim_cut_height_cm"),
                        "post_cut_outer_height": box.get("trimmed_outer_height_cm"),
                        "post_cut_inner_height": box.get("trimmed_inner_height_cm"),
                        "max_capacity": max_capacity,
                        "recommended_capacity": recommended_capacity,
                        "recommended_rotation": recommended_rotation,
                        "max_rotation": max_rotation,
                        "spec_source": spec_source,
                        "outer_dims": outer_dims,
                        "inner_dims": inner_dims,
                    }
                )
        else:
            rows.append(
                {
                    "product_name": product_name,
                    "box_label": box_label,
                    "box_no": None,
                    "box_count": _to_int(plan.get("boxes_needed"), 1),
                    "input_qty": input_qty,
                    "calc_qty": calc_qty,
                    "calc_unit": calc_unit,
                    "package_in_qty": package_in_qty,
                    "estimated_weight": None,
                    "per_layer": None,
                    "layers": None,
                    "used_height": None,
                    "remaining_height": None,
                    "cut_height": None,
                    "post_cut_outer_height": None,
                    "post_cut_inner_height": None,
                    "max_capacity": max_capacity,
                    "recommended_capacity": recommended_capacity,
                    "recommended_rotation": recommended_rotation,
                    "max_rotation": max_rotation,
                    "spec_source": spec_source,
                    "outer_dims": outer_dims,
                    "inner_dims": inner_dims,
                }
            )

    return rows


# =========================================================
# 최종결과 출력 (패킹리스트 NO)
# =========================================================

def _format_final_only(rows: List[Dict[str, Any]]) -> List[str]:
    lines: List[str] = ["[FINAL RESULT]"]

    total_boxes = sum(_to_int(row.get("box_count"), 1) for row in rows) if rows else 0
    lines.append(f"총 박스수: {total_boxes}박스")

    if not rows:
        lines.append("")
        lines.append("선택된 패킹 결과가 없습니다.")
        return lines

    lines.append("")

    for idx, row in enumerate(rows, start=1):
        parts: List[str] = []

        parts.append(str(row.get("product_name", "상품명없음")))
        parts.append(
            _build_cut_box_display(
                box_label=str(row.get("box_label", "박스정보없음")),
                outer_dims=row.get("outer_dims"),
                cut_height=row.get("cut_height"),
                trimmed_outer_height=row.get("post_cut_outer_height"),
            )
        )

        box_count = _to_int(row.get("box_count"), 1)
        if box_count > 1:
            parts.append(f"{box_count}박스")

        calc_qty = row.get("calc_qty")
        calc_unit = _unit_label(row.get("calc_unit", "ea"))
        input_qty = row.get("input_qty")
        if calc_qty not in (None, ""):
            parts.append(_build_display_qty(calc_qty, calc_unit, input_qty))

        est_weight = row.get("estimated_weight")
        if est_weight not in (None, ""):
            parts.append(f"{_fmt_weight(est_weight)} kg")

        lines.append(f"{idx}. " + " / ".join(parts))

    return lines

# =========================================================
# 패킹리스트 출력 (패킹리스트 YES)
# =========================================================

def _format_packing_list(rows: List[Dict[str, Any]]) -> List[str]:
    lines: List[str] = ["[PACKING LIST]"]

    total_boxes = sum(_to_int(row.get("box_count"), 1) for row in rows) if rows else 0
    lines.append(f"총 박스수: {total_boxes}박스")

    if not rows:
        lines.append("")
        lines.append("선택된 패킹 결과가 없습니다.")
        return lines

    for idx, row in enumerate(rows, start=1):
        calc_unit = _unit_label(row.get("calc_unit", "ea"))

        lines.append("")
        lines.append(f"BOX {idx}")
        lines.append(f"- 상품명: {row.get('product_name', '상품명없음')}")
        lines.append(f"- 박스: {row.get('box_label', '박스정보없음')}")

        if row.get("box_no") not in (None, ""):
            lines.append(f"- 박스번호: {_to_int(row.get('box_no'))}")

        if row.get("input_qty") not in (None, ""):
            lines.append(f"- 입력수량: {_fmt_qty(row.get('input_qty'))}ea")

        if row.get("calc_qty") not in (None, ""):
            lines.append(
                f"- 계산수량: {_build_display_qty(row.get('calc_qty'), calc_unit, row.get('input_qty'))}"
            )

        if calc_unit == "package" and _is_positive_number(row.get("package_in_qty")):
            lines.append(f"- 패키지입수량: {_fmt_qty(row.get('package_in_qty'))}")

        if row.get("estimated_weight") not in (None, ""):
            lines.append(f"- 예상총중량: {_fmt_weight(row.get('estimated_weight'))} kg")

        if _is_positive_number(row.get("per_layer")):
            lines.append(f"- 층당적재: {_fmt_qty(row.get('per_layer'))}")

        if _is_positive_number(row.get("layers")):
            lines.append(f"- 필요층수: {_fmt_qty(row.get('layers'))}")

        if _is_positive_number(row.get("used_height")):
            lines.append(f"- 사용높이: {_fmt_cm(row.get('used_height'))} cm")

        if _is_positive_number(row.get("remaining_height")):
            lines.append(f"- 남는높이: {_fmt_cm(row.get('remaining_height'))} cm")

        if _is_positive_number(row.get("cut_height")):
            lines.append(f"- 제단높이: {_fmt_cm(row.get('cut_height'))} cm")

        if _is_positive_number(row.get("post_cut_outer_height")):
            lines.append(f"- 제단후외경높이: {_fmt_cm(row.get('post_cut_outer_height'))} cm")

        if _is_positive_number(row.get("post_cut_inner_height")):
            lines.append(f"- 제단후내경높이: {_fmt_cm(row.get('post_cut_inner_height'))} cm")

        if _is_positive_number(row.get("max_capacity")):
            lines.append(f"- 최대적재가능: {_fmt_qty(row.get('max_capacity'))}")

        if _is_positive_number(row.get("recommended_capacity")):
            lines.append(f"- 권장회전기준최대: {_fmt_qty(row.get('recommended_capacity'))}")

        if row.get("recommended_rotation") not in (None, ""):
            lines.append(f"- 권장회전: {row.get('recommended_rotation')}")

        if row.get("max_rotation") not in (None, ""):
            lines.append(f"- 최대적재회전: {row.get('max_rotation')}")

        if row.get("spec_source") not in (None, ""):
            lines.append(f"- spec_source: {row.get('spec_source')}")

    return lines

# =========================================================
# 예외/경고 출력
# =========================================================

def _format_issue_item(item: Any) -> str:
    if not isinstance(item, dict):
        return f"- {item}"

    product_name = item.get("product_name", "상품명없음")
    qty = item.get("qty", "-")
    reason = item.get("reason", "-")
    source_reason = item.get("source_reason")

    parts = [str(product_name), str(qty), str(reason)]

    if source_reason not in (None, ""):
        parts.append(f"source_reason={source_reason}")

    if item.get("package_pack_qty") not in (None, ""):
        parts.append(f"package_pack_qty={item.get('package_pack_qty')}")

    if item.get("spec_source") not in (None, ""):
        parts.append(f"spec_source={item.get('spec_source')}")

    return "- " + " / ".join(parts)


def _format_issues(result: Dict[str, Any]) -> List[str]:
    lines: List[str] = []

    no_box_fit = result.get("no_box_fit") or []
    unresolved = result.get("unresolved") or []
    invalid_specs = result.get("invalid_specs") or []
    not_found = result.get("not_found") or []

    if no_box_fit:
        lines.append("[no_box_fit]")
        for item in no_box_fit:
            lines.append(_format_issue_item(item))

    if unresolved:
        if lines:
            lines.append("")
        lines.append("[unresolved]")
        for item in unresolved:
            lines.append(_format_issue_item(item))

    if invalid_specs:
        if lines:
            lines.append("")
        lines.append("[invalid_specs]")
        for item in invalid_specs:
            lines.append(_format_issue_item(item))

    if not_found:
        if lines:
            lines.append("")
        lines.append("[not_found]")
        for item in not_found:
            lines.append(_format_issue_item(item))

    return lines

# =========================================================
# 단독 테스트용
# =========================================================

if __name__ == "__main__":
    sample_result = {
        "final_plans": [
            {
                "product_name": "나보타 100u",
                "total_qty": 5,
                "original_qty": 50,
                "package_pack_qty": 10,
                "calc_unit_type": "package",
                "selected_box_code": "BX005",
                "selected_box_name": "4호",
                "units_per_box": 8,
                "boxes_needed": 1,
                "inner_size_cm": (40.0, 30.0, 27.0),
                "outer_size_cm": (41.0, 31.0, 28.0),
                "best_orientation": (9.3, 25.6, 9.3),
                "spec_source": "packages_master",
                "global_best_max_units_per_box": 12,
                "global_best_orientation": (9.3, 9.3, 25.6),
                "box_lines": [
                    {
                        "box_no": 1,
                        "box_code": "BX005",
                        "box_name": "4호",
                        "qty": 5,
                        "gross_weight_est": 2.405,
                        "layer_capacity": 4,
                        "layers_needed": 2,
                        "used_height_cm": 18.6,
                        "remaining_height_cm": 8.4,
                        "trim_cut_height_cm": 6.4,
                        "trimmed_inner_height_cm": 20.6,
                        "trimmed_outer_height_cm": 21.6,
                    }
                ],
            }
        ],
        "no_box_fit": [],
        "unresolved": [],
        "invalid_specs": [],
    }

    print("===== 패킹리스트 NO =====")
    print(format_engine_result(sample_result, packing_list_needed=False))
    print()
    print("===== 패킹리스트 YES =====")
    print(format_engine_result(sample_result, packing_list_needed=True))