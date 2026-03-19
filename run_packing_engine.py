from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from master_loader import (
    load_master_workbook,
    prepare_products_for_engine,
    prepare_boxes_for_engine,
    prepare_packages_for_engine,
)
from matcher import (
    RawOrderLine,
    match_order_lines,
)
from fullbox_engine import (
    OrderLine,
    run_fullbox_engine,
)
from repack_engine import (
    build_repack_candidates,
    evaluate_repack_box_candidates,
    build_repack_final_plan,
)
from result_formatter import format_engine_result


DEFAULT_MASTER = Path("data/packing_engine_normalized_masters_ko_json_schema_fixed.xlsx")


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"y", "yes", "true", "1", "필요", "예", "있음"}


def _normalize_shipping_method(value: Any) -> str:
    text = str(value or "").strip().lower()

    if text in {"완박스", "fullbox", "full_box", "full-box"}:
        return "fullbox"
    if text in {"재포장", "repack", "re-pack", "re_pack"}:
        return "repack"
    return "auto"


def _normalize_order_lines(order_lines: List[Any]) -> List[RawOrderLine]:
    """
    허용 입력 예시
    1) [{"product_name": "나보타 100u", "qty": 50}]
    2) [{"상품명": "나보타 100u", "수량": 50}]
    3) [("나보타 100u", 50), ("셀로좀 미드 리도", 250)]
    4) [RawOrderLine("나보타 100u", 50)]
    """

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
                or item.get("matched_name")
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


def _serialize_match_results(matched_orders: List[Any]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []

    for row in matched_orders:
        payload = {
            "raw_input": getattr(row, "raw_input", None),
            "item_text": getattr(row, "item_text", None),
            "input_name": getattr(row, "item_text", getattr(row, "raw_input", None)),
            "qty": getattr(row, "qty", None),
            "status": getattr(row, "status", None),
            "message": getattr(row, "message", None),
            "matched": getattr(row, "matched", None),
            "matched_name": getattr(row, "matched_name", None),
            "product_code": getattr(row, "product_code", None),
            "match_source": getattr(row, "match_source", None),
            "match_reason": getattr(row, "match_reason", None),
            "reason": getattr(row, "match_reason", None) or getattr(row, "message", None),
        }
        results.append(payload)

    return results


def build_forced_repack_fullbox_result(
    matched_orders: List[Any],
    extra_not_found: List[Dict[str, Any]],
) -> Dict[str, Any]:
    result = {
        "single_fullboxes": [],
        "group_mixed_fullboxes": [],
        "tolerance_mixed_fullboxes": [],
        "remainders": [],
        "not_found": list(extra_not_found),
    }

    for row in matched_orders:
        if getattr(row, "matched", False) and getattr(row, "matched_name", ""):
            result["remainders"].append(
                {
                    "type": "repack_remainder",
                    "product_name": row.matched_name,
                    "qty": row.qty,
                    "reason": "FORCED_REPACK",
                }
            )

    return result


def run_packing_engine(
    order_lines: List[Any],
    packing_list_needed: Any = False,
    master_path: str | Path | None = None,
    shipping_method: Any = None,
) -> Dict[str, Any]:
    """
    엔진 전체 실행 함수

    Parameters
    ----------
    order_lines : list
        예시:
        [
            {"product_name": "나보타 100u", "qty": 50},
            {"product_name": "셀로좀 미드 리도", "qty": 250},
        ]

    packing_list_needed : bool | str
        True / False 또는 yes / no 형태 허용

    master_path : str | Path | None
        마스터 파일 경로. None이면 DEFAULT_MASTER 사용
    """

    packing_list_needed = _to_bool(packing_list_needed)
    shipping_method = _normalize_shipping_method(shipping_method)
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

    matched_orders = match_order_lines(
        raw_order_lines=raw_orders,
        prepared_products=prepared_products,
        fullboxes_master=load_result["fullboxes"],
        packages_master=load_result["packages"],
    )

    engine_orders: List[OrderLine] = []
    extra_not_found: List[Dict[str, Any]] = []

    for row in matched_orders:
        if getattr(row, "matched", False) and getattr(row, "matched_name", None):
            engine_orders.append(
                OrderLine(
                    product_name=row.matched_name,
                    qty=int(row.qty),
                )
            )
        else:
            extra_not_found.append(
                {
                    "product_name": getattr(row, "item_text", getattr(row, "raw_input", None)),
                    "qty": getattr(row, "qty", None),
                    "reason": getattr(row, "message", None) or "PRODUCT_NOT_FOUND",
                }
            )

    if shipping_method == "repack":
        fullbox_result = build_forced_repack_fullbox_result(
            matched_orders=matched_orders,
            extra_not_found=extra_not_found,
        )
    else:
        fullbox_result = run_fullbox_engine(
            order_lines=engine_orders,
            prepared_products_df=prepared_products,
            rules=load_result["rules"],
            fallback_fullboxes_df=load_result["fullboxes"],
        )
        fullbox_result["not_found"] = fullbox_result.get("not_found", []) + extra_not_found

    repack_result = build_repack_candidates(
        fullbox_result=fullbox_result,
        prepared_products_df=prepared_products,
        prepared_packages_df=prepared_packages,
        fallback_fullboxes_df=load_result["fullboxes"],
    )

    repack_box_result = evaluate_repack_box_candidates(
        repack_result=repack_result,
        prepared_boxes_df=prepared_boxes,
        rules=load_result["rules"],
    )

    final_result = build_repack_final_plan(
        box_eval_result=repack_box_result,
    )

    final_result["not_found"] = fullbox_result.get("not_found", [])

    formatted_text = format_engine_result(
        final_result,
        packing_list_needed=packing_list_needed,
    )

    return {
        "formatted_text": formatted_text,
        "shipping_method": shipping_method,
        "final_result": final_result,
        "match_result": _serialize_match_results(matched_orders),
        "fullbox_result": fullbox_result,
        "repack_result": repack_result,
        "repack_box_result": repack_box_result,
    }


if __name__ == "__main__":
    sample_orders = [
        {"product_name": "셀로좀 미드 리도", "qty": 250},
        {"product_name": "셀로좀 임플란트 리도", "qty": 150},
        {"product_name": "나보타 100u", "qty": 50},
        {"product_name": "없는상품 테스트", "qty": 10},
    ]

    result = run_packing_engine(
        order_lines=sample_orders,
        packing_list_needed=False,
    )

    print(result["formatted_text"])
