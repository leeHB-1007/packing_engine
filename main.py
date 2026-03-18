from pathlib import Path

from master_loader import (
    load_master_workbook,
    prepare_products_for_engine,
    prepare_boxes_for_engine,
    prepare_packages_for_engine,
    print_load_summary,
)
from matcher import (
    RawOrderLine,
    match_order_lines,
    print_match_result,
)
from fullbox_engine import (
    OrderLine,
    run_fullbox_engine,
    print_fullbox_result,
)
from repack_engine import (
    build_repack_candidates,
    evaluate_repack_box_candidates,
    print_repack_candidates,
    print_repack_box_candidates,
    build_repack_final_plan,
    print_repack_final_plan,
)
from test_orders import get_test_case
from sulu_med_exporter import export_engine_result_to_sulu_med_xlsx


DEFAULT_MASTER = Path("data/packing_engine_normalized_masters_ko_json_schema_fixed.xlsx")


def build_forced_repack_fullbox_result(matched_orders, extra_not_found):
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


def main():
    print("[START] packing engine prototype")
    print("[STEP] master load 시작")

    load_result = load_master_workbook(DEFAULT_MASTER)

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

    print_load_summary(
        load_result,
        prepared_products_df=prepared_products,
        prepared_boxes_df=prepared_boxes,
        prepared_packages_df=prepared_packages,
    )

    print("[STEP] matcher 시작")

    # 여기 케이스명만 바꿔서 테스트
    case_name = "case_13_fullbox_redtox100_name_10"
    # case_name = "case_12_repack_cellozome_mid_lido_500"

    test_case = get_test_case(case_name)
    test_orders = test_case["orders"]
    ship_type = str(test_case.get("ship_type", "auto")).strip().lower()
    packing_list = str(test_case.get("packing_list", "no")).strip().lower()

    print(f"[TEST CASE] {case_name}")
    print(f"[DESCRIPTION] {test_case.get('description', '')}")
    print(f"[SHIP TYPE] {ship_type}")
    print(f"[PACKING LIST] {packing_list}")

    raw_orders = [
        RawOrderLine(row["product_name"], row["qty"])
        for row in test_orders
    ]

    matched_orders = match_order_lines(
        raw_order_lines=raw_orders,
        prepared_products=prepared_products,
        fullboxes_master=load_result["fullboxes"],
        packages_master=load_result["packages"],
    )
    print_match_result(matched_orders)

    print("[STEP] fullbox engine 시작")

    engine_orders = []
    extra_not_found = []

    for row in matched_orders:
        if getattr(row, "matched", False) and getattr(row, "matched_name", ""):
            engine_orders.append(
                OrderLine(
                    product_name=row.matched_name,
                    qty=row.qty,
                )
            )
        else:
            extra_not_found.append(
                {
                    "product_name": getattr(row, "item_text", getattr(row, "raw_input", "")),
                    "qty": getattr(row, "qty", 0),
                    "reason": getattr(row, "message", "") or "PRODUCT_NOT_FOUND",
                }
            )

    if ship_type == "repack":
        print("[ROUTE] 강제 재포장 모드 - fullbox engine 스킵")
        fullbox_result = build_forced_repack_fullbox_result(
            matched_orders=matched_orders,
            extra_not_found=extra_not_found,
        )
    else:
        print("[ROUTE] 자동/완박스 모드 - fullbox engine 실행")
        fullbox_result = run_fullbox_engine(
            order_lines=engine_orders,
            prepared_products_df=prepared_products,
            rules=load_result["rules"],
        )
        fullbox_result["not_found"] = fullbox_result.get("not_found", []) + extra_not_found

    print_fullbox_result(fullbox_result)

    print("[STEP] repack input build 시작")
    repack_result = build_repack_candidates(
        fullbox_result=fullbox_result,
        prepared_products_df=prepared_products,
        prepared_packages_df=prepared_packages,
    )
    print_repack_candidates(repack_result)

    print("[STEP] repack box evaluate 시작")
    repack_box_result = evaluate_repack_box_candidates(
        repack_result=repack_result,
        prepared_boxes_df=prepared_boxes,
        rules=load_result["rules"],
    )
    print_repack_box_candidates(repack_box_result, top_n=5)

    repack_final_result = build_repack_final_plan(
        box_eval_result=repack_box_result,
    )
    print_repack_final_plan(repack_final_result)

    print("[STEP] Sulu Med 엑셀 export 시작")
    export_result = export_engine_result_to_sulu_med_xlsx(
        engine_result={
            "match_result": matched_orders,
            "fullbox_result": fullbox_result,
            "repack_box_result": repack_box_result,
            "final_result": repack_final_result,
        },
        output_path=Path("output") / f"{case_name}_sulu_med.xlsx",
        title=f"Sulu Med 출하건 ({case_name})",
        master_path=DEFAULT_MASTER,
    )
    print(f"[EXPORT] saved to {export_result['output_path']}")
    print(f"[EXPORT] rows={export_result['row_count']} / issues={export_result['issue_count']}")


if __name__ == "__main__":
    main()
