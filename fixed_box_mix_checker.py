from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from master_loader import (
    load_master_workbook,
    prepare_boxes_for_engine,
    prepare_packages_for_engine,
    prepare_products_for_engine,
)
from matcher import match_order_lines
from repack_engine import build_repack_candidates

from fixed_box_checker import (
    RawOrderLine,
    _normalize_order_lines,
    _find_fixed_box,
    _build_forced_remainder_result,
    _evaluate_on_fixed_box,
    _fmt_cm,
    _fmt_qty,
    _fmt_weight,
    _build_display_qty,
    _unit_label,
)


DEFAULT_MASTER = Path("data/packing_engine_normalized_masters_ko_json_schema_fixed.xlsx")


def _to_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default

def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(round(float(value)))
    except Exception:
        return default


def _safe_div(a: float, b: float) -> float:
    if b <= 0:
        return 0.0
    return float(a) / float(b)


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

def _merge_display_rows_by_product(rows: List[dict]) -> List[dict]:
    merged = []
    merged_map: Dict[str, dict] = {}

    for row in rows:
        product_name = row.get("product_name", "상품명없음")
        qty_value = row.get("calc_qty", row.get("qty", 0))
        calc_unit_type = row.get("calc_unit_type", "item")
        package_pack_qty = row.get("package_pack_qty", 1)
        fill_ratio = float(row.get("fill_ratio", 0) or 0)
        max_units_per_box = row.get("max_units_per_box")

        calc_unit_norm = _unit_label(calc_unit_type)

        try:
            qty_num = int(round(float(qty_value)))
        except Exception:
            qty_num = 0

        try:
            pack_num = int(round(float(package_pack_qty)))
        except Exception:
            pack_num = 1

        if calc_unit_norm == "package":
            qty_text = f"{_fmt_qty(qty_value)}package({_fmt_qty(qty_num * pack_num)}ea)"
        else:
            qty_text = f"{_fmt_qty(qty_value)}ea"

        capacity_text = None
        if max_units_per_box not in (None, ""):
            capacity_text = f"{_fmt_qty(max_units_per_box)}{calc_unit_norm}"

        if product_name not in merged_map:
            payload = {
                "product_name": product_name,
                "qty_parts": [],
                "fill_ratio": 0.0,
                "capacity_parts": [],
            }
            merged_map[product_name] = payload
            merged.append(payload)

        target = merged_map[product_name]
        target["qty_parts"].append(qty_text)
        target["fill_ratio"] += fill_ratio

        if capacity_text and capacity_text not in target["capacity_parts"]:
            target["capacity_parts"].append(capacity_text)

    return merged

def _build_capacity_reference(
    fit_results: List[dict],
    candidate_lookup: Dict[str, dict],
) -> List[dict]:
    rows = []

    for row in fit_results:
        product_name = row["product_name"]
        meta = candidate_lookup.get(product_name, {})

        calc_qty = int(row.get("calc_qty", 0))
        max_units_per_box = int(row.get("max_units_per_box", 0))
        unit_weight_kg = float(meta.get("unit_weight_kg", 0) or 0)

        fill_ratio = _safe_div(calc_qty, max_units_per_box)
        est_total_weight = calc_qty * unit_weight_kg

        rows.append(
            {
                "product_name": product_name,
                "calc_qty": calc_qty,
                "calc_unit_type": row.get("calc_unit_type", "item"),
                "package_pack_qty": row.get("package_pack_qty", 1),
                "max_units_per_box": max_units_per_box,
                "fill_ratio": fill_ratio,
                "unit_weight_kg": unit_weight_kg,
                "est_total_weight": est_total_weight,
            }
        )

    return rows


def _allocate_boxes_by_fill_ratio(
    fit_results: List[dict],
    candidate_lookup: Dict[str, dict],
    selected_box: dict,
    rules: dict,
) -> Dict[str, Any]:
    """
    실무형 합포 판정
    - 각 상품의 '고정 박스 최대 적재량'을 기준으로 박스 점유율 계산
    - 점유율 합이 1 이하면 1박스 가능
    - 초과하면 추가 박스 생성
    - 중량 제한도 같이 체크
    """
    effective_weight_capacity = min(
        float(selected_box["최대허용중량(kg)"]),
        float(rules.get("BOX_MAX_WEIGHT_KG", 30)),
    ) - float(selected_box["박스중량(kg)"])

    items = []
    for row in fit_results:
        product_name = row["product_name"]
        meta = candidate_lookup.get(product_name, {})

        max_units_per_box = int(row.get("max_units_per_box", 0))
        calc_qty = int(row.get("calc_qty", 0))
        calc_unit_type = row.get("calc_unit_type", "item")
        package_pack_qty = int(row.get("package_pack_qty", 1))
        unit_weight_kg = float(meta.get("unit_weight_kg", 0) or 0)

        if max_units_per_box <= 0:
            return {
                "boxes": [],
                "pack_failed": True,
                "pack_failed_reason": f"INVALID_MAX_UNITS_PER_BOX: {product_name}",
            }

        if unit_weight_kg <= 0:
            return {
                "boxes": [],
                "pack_failed": True,
                "pack_failed_reason": f"INVALID_UNIT_WEIGHT: {product_name}",
            }

        items.append(
            {
                "product_name": product_name,
                "remaining_qty": calc_qty,
                "calc_unit_type": calc_unit_type,
                "package_pack_qty": package_pack_qty,
                "max_units_per_box": max_units_per_box,
                "fill_per_unit": 1.0 / float(max_units_per_box),
                "unit_weight_kg": unit_weight_kg,
            }
        )

    # 큰 점유율 상품부터 채우는 실무형 greedy
    items.sort(key=lambda x: (-x["fill_per_unit"], -x["unit_weight_kg"], x["product_name"]))

    boxes = []

    while any(item["remaining_qty"] > 0 for item in items):
        remaining_fill = 1.0
        remaining_weight = effective_weight_capacity
        box_allocations = []
        progress = False

        for item in items:
            if item["remaining_qty"] <= 0:
                continue

            max_by_fill = int((remaining_fill + 1e-12) / item["fill_per_unit"])
            max_by_weight = int((remaining_weight + 1e-12) // item["unit_weight_kg"])

            alloc_qty = min(
                int(item["remaining_qty"]),
                int(max_by_fill),
                int(max_by_weight),
            )

            if alloc_qty <= 0:
                continue

            item["remaining_qty"] -= alloc_qty
            remaining_fill -= alloc_qty * item["fill_per_unit"]
            remaining_weight -= alloc_qty * item["unit_weight_kg"]
            progress = True

            box_allocations.append(
                {
                    "product_name": item["product_name"],
                    "qty": alloc_qty,
                    "calc_unit_type": item["calc_unit_type"],
                    "package_pack_qty": item["package_pack_qty"],
                    "max_units_per_box": item["max_units_per_box"],
                    "fill_ratio": alloc_qty * item["fill_per_unit"],
                    "item_weight_total": round(alloc_qty * item["unit_weight_kg"], 3),
                }
            )

        if not progress:
            return {
                "boxes": boxes,
                "pack_failed": True,
                "pack_failed_reason": "NO_PROGRESS_IN_FILL_RATIO_PACKING",
            }

        used_fill_ratio = sum(x["fill_ratio"] for x in box_allocations)
        item_weight_total = sum(x["item_weight_total"] for x in box_allocations)

        boxes.append(
            {
                "box_no": len(boxes) + 1,
                "items": box_allocations,
                "used_fill_ratio": round(used_fill_ratio, 4),
                "remaining_fill_ratio": round(max(0.0, 1.0 - used_fill_ratio), 4),
                "item_weight_total": round(item_weight_total, 3),
                "gross_weight_est": round(float(selected_box["박스중량(kg)"]) + item_weight_total, 3),
            }
        )

    return {
        "boxes": boxes,
        "pack_failed": False,
        "pack_failed_reason": "",
    }


def format_fixed_box_mix_result(result: Dict[str, Any]) -> str:
    selected_box = result["selected_box"]
    boxes = result.get("boxes", [])
    fit_results = result.get("fit_results", [])
    capacity_reference = result.get("capacity_reference", [])
    not_found = result.get("not_found", [])
    unresolved = result.get("unresolved", [])
    invalid_specs = result.get("invalid_specs", [])
    no_fit = result.get("no_fit", [])
    pack_failed = bool(result.get("pack_failed", False))
    pack_failed_reason = result.get("pack_failed_reason", "")

    lines: List[str] = []
    lines.append("[FIXED BOX MIX CHECK]")
    lines.append(
        f"기준박스: {selected_box['박스명']} ({selected_box['박스코드']}) / "
        f"{_fmt_cm(selected_box['외경가로(cm)'])} x {_fmt_cm(selected_box['외경세로(cm)'])} x {_fmt_cm(selected_box['외경높이(cm)'])} cm"
    )

    if pack_failed:
        lines.append("")
        lines.append(f"판정: 합포 계산 실패 / {pack_failed_reason}")
    else:
        total_fill_ratio = sum(row["fill_ratio"] for row in capacity_reference)

        lines.append("")
        if len(boxes) == 1:
            lines.append("판정: 전량 1박스 적재 가능")
        else:
            lines.append(f"판정: 전량 1박스 적재 불가 / 총 {len(boxes)}박스 필요")

        lines.append(f"총 박스수: {len(boxes)}박스")
        lines.append(f"총 점유율: {round(total_fill_ratio * 100, 2)}%")

    if capacity_reference:
        merged_capacity_rows = _merge_display_rows_by_product(capacity_reference)

        lines.append("")
        lines.append("[capacity_reference]")
        for idx, row in enumerate(merged_capacity_rows, start=1):
            qty_text = " + ".join(row["qty_parts"])
            capacity_text = " + ".join(row["capacity_parts"]) if row["capacity_parts"] else "-"
            lines.append(
                f"{idx}. {row['product_name']} / "
                f"{qty_text} / "
                f"박스당최대 {capacity_text} / "
                f"점유율 {round(row['fill_ratio'] * 100, 2)}%"
            )

    if boxes:
        lines.append("")
        lines.append("[box_summary]")
        for box in boxes:
            lines.append(
                f"BOX {box['box_no']} / "
                f"점유율={round(box['used_fill_ratio'] * 100, 2)}% / "
                f"예상총중량={_fmt_weight(box['gross_weight_est'])} kg"
            )

            merged_box_items = _merge_display_rows_by_product(box["items"])
            for item in merged_box_items:
                qty_text = " + ".join(item["qty_parts"])
                lines.append(
                    f"- {item['product_name']} / {qty_text} / "
                    f"점유율 {round(item['fill_ratio'] * 100, 2)}%"
                )
            lines.append("")

        if lines and lines[-1] == "":
            lines.pop()

    for block in [
        _format_issue_block("[no_fit]", no_fit),
        _format_issue_block("[unresolved]", unresolved),
        _format_issue_block("[invalid_specs]", invalid_specs),
        _format_issue_block("[not_found]", not_found),
    ]:
        if block:
            lines.append("")
            lines.extend(block)

    return "\n".join(lines).strip()

def run_fixed_box_mix_check(
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

    candidate_lookup = {
        row["product_name"]: row
        for row in repack_result.get("candidates", [])
    }

    # 여기서 상품별 '고정 박스 최대 적재량' 산출
    single_eval_result = _evaluate_on_fixed_box(
        repack_result=repack_result,
        selected_box=selected_box,
        rules=load_result["rules"],
    )

    fit_results = single_eval_result.get("fit_results", [])
    no_fit = single_eval_result.get("no_fit", [])

    pack_result = _allocate_boxes_by_fill_ratio(
        fit_results=fit_results,
        candidate_lookup=candidate_lookup,
        selected_box=selected_box,
        rules=load_result["rules"],
    )

    capacity_reference = _build_capacity_reference(
        fit_results=fit_results,
        candidate_lookup=candidate_lookup,
    )

    final_result = {
        "selected_box": selected_box,
        "fit_results": fit_results,
        "capacity_reference": capacity_reference,
        "boxes": pack_result["boxes"],
        "pack_failed": pack_result["pack_failed"],
        "pack_failed_reason": pack_result["pack_failed_reason"],
        "no_fit": no_fit,
        "unresolved": repack_result.get("unresolved", []),
        "invalid_specs": repack_result.get("invalid_specs", []),
        "not_found": forced_remainder_result.get("not_found", []),
    }
    final_result["formatted_text"] = format_fixed_box_mix_result(final_result)

    return final_result


if __name__ == "__main__":
    sample_orders = [
    {"product_name": "비에녹스200u", "qty": 75},
    {"product_name": "리체스 딥 리도(C)", "qty": 225},
    {"product_name": "엘라스티 D 플러스(1syr)", "qty": 25},
]

    result = run_fixed_box_mix_check(
        order_lines=sample_orders,
        box_query="72x48x40",
    )

    print(result["formatted_text"])
