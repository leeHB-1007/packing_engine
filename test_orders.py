# test_orders.py
# 실무 검증용 테스트 주문 세트

TEST_ORDERS = {
    "case_11_repack_kiarareju_109": {
        "description": "키아라레쥬 109개 재포장 단일상품 케이스",
        "ship_type": "repack",
        "packing_list": "no",
        "orders": [
            {"product_name": "키아라레쥬", "qty": 109},
        ],
        "expected": {
            "check_point": "전량 재포장으로 들어가야 함",
        },
    },
    "case_12_repack_cellozome_mid_lido_500": {
        "description": "셀로좀 미드 리도 500개 재포장 단일상품 케이스",
        "ship_type": "repack",
        "packing_list": "no",
        "orders": [
            {"product_name": "셀로좀 미드 리도", "qty": 500},
        ],
        "expected": {
            "check_point": "전량 재포장으로 들어가야 함",
        },
    },
    "case_13_fullbox_redtox100_name_10": {
        "description": "레드톡스 100u 이름 매칭 테스트",
        "ship_type": "fullbox",
        "packing_list": "no",
        "orders": [
            {"product_name": "레드톡스 100u", "qty": 10},
        ],
        "expected": {
            "check_point": "products에 없어도 fullboxes fallback 이름 매칭이 되어야 함",
        },
    },
    "case_14_fullbox_redtox100_code_10": {
        "description": "A100110 코드 매칭 테스트",
        "ship_type": "fullbox",
        "packing_list": "no",
        "orders": [
            {"product_name": "A100110", "qty": 10},
        ],
        "expected": {
            "check_point": "상품코드 EXACT_CODE 매칭이 되어야 함",
        },
    },
}


def get_test_case(case_name: str):
    case = TEST_ORDERS.get(case_name)
    if not case:
        raise KeyError(f"존재하지 않는 테스트 케이스입니다: {case_name}")
    return case


def get_test_order(case_name: str):
    case = get_test_case(case_name)
    return case["orders"]


def list_test_cases():
    rows = []
    for key, value in TEST_ORDERS.items():
        rows.append({
            "case_name": key,
            "description": value.get("description", ""),
            "ship_type": value.get("ship_type", "auto"),
        })
    return rows


if __name__ == "__main__":
    print("[TEST CASE LIST]")
    for row in list_test_cases():
        print(f"- {row['case_name']} / {row['description']} / ship_type={row['ship_type']}")