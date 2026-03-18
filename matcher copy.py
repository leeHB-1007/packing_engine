from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import pandas as pd


def _norm_text(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value)
    text = text.replace("\n", " ").replace("\r", " ")
    text = " ".join(text.split())
    return text.strip()


def _norm_col(value) -> str:
    return _norm_text(value).replace(" ", "")


def _to_bool(value) -> bool:
    return _norm_text(value).upper() in {"Y", "YES", "TRUE", "1"}


def _match_key(value) -> str:
    """
    매칭용 정규화 키
    - 앞뒤/중간 공백 제거
    - 영문 대소문자 무시
    """
    return _norm_text(value).replace(" ", "").lower()


@dataclass
class RawOrderLine:
    product_name: str
    qty: int


@dataclass
class MatchedOrderLine:
    input_name: str
    matched: bool
    matched_name: str | None
    product_code: str | None
    qty: int
    package_product: bool = False
    package_policy_ref: str = ""
    packing_policy_code: str = ""
    special_group: str = ""
    reason: str | None = None


def build_exact_name_lookup(prepared_products_df: pd.DataFrame) -> Dict[str, dict]:
    df = prepared_products_df.copy()

    name_col = _norm_col("국문상품명")
    code_col = _norm_col("상품코드")
    package_product_col = _norm_col("패키지상품여부")
    package_policy_ref_col = _norm_col("패키지정책참조")
    packing_policy_code_col = _norm_col("패킹정책코드")
    special_group_col = _norm_col("특수상품군")

    lookup: Dict[str, dict] = {}

    for _, row in df.iterrows():
        product_name = _norm_text(row.get(name_col, ""))
        product_code = _norm_text(row.get(code_col, ""))
        package_policy_ref = _norm_text(row.get(package_policy_ref_col, ""))

        if not product_name:
            continue

        raw_package_flag = _to_bool(row.get(package_product_col, False))
        inferred_package_flag = raw_package_flag or (package_policy_ref != "")

        lookup[_norm_text(product_name)] = {
            "matched_name": product_name,
            "product_code": product_code,
            "package_product": inferred_package_flag,
            "package_policy_ref": package_policy_ref,
            "packing_policy_code": _norm_text(row.get(packing_policy_code_col, "")),
            "special_group": _norm_text(row.get(special_group_col, "")),
        }

    return lookup


def build_normalized_name_lookup(prepared_products_df: pd.DataFrame) -> Dict[str, List[dict]]:
    df = prepared_products_df.copy()

    name_col = _norm_col("국문상품명")
    code_col = _norm_col("상품코드")
    package_product_col = _norm_col("패키지상품여부")
    package_policy_ref_col = _norm_col("패키지정책참조")
    packing_policy_code_col = _norm_col("패킹정책코드")
    special_group_col = _norm_col("특수상품군")

    lookup: Dict[str, List[dict]] = {}

    for _, row in df.iterrows():
        product_name = _norm_text(row.get(name_col, ""))
        product_code = _norm_text(row.get(code_col, ""))
        package_policy_ref = _norm_text(row.get(package_policy_ref_col, ""))

        if not product_name:
            continue

        raw_package_flag = _to_bool(row.get(package_product_col, False))
        inferred_package_flag = raw_package_flag or (package_policy_ref != "")

        key = _match_key(product_name)
        if not key:
            continue

        lookup.setdefault(key, []).append(
            {
                "matched_name": product_name,
                "product_code": product_code,
                "package_product": inferred_package_flag,
                "package_policy_ref": package_policy_ref,
                "packing_policy_code": _norm_text(row.get(packing_policy_code_col, "")),
                "special_group": _norm_text(row.get(special_group_col, "")),
            }
        )

    return lookup


def match_order_lines(
    raw_orders: List[RawOrderLine],
    prepared_products_df: pd.DataFrame,
) -> List[MatchedOrderLine]:
    exact_lookup = build_exact_name_lookup(prepared_products_df)
    normalized_lookup = build_normalized_name_lookup(prepared_products_df)

    results: List[MatchedOrderLine] = []

    for order in raw_orders:
        input_name = _norm_text(order.product_name)
        qty = int(order.qty)

        # 1) exact 매칭 우선
        found = exact_lookup.get(input_name)
        if found:
            results.append(
                MatchedOrderLine(
                    input_name=input_name,
                    matched=True,
                    matched_name=found["matched_name"],
                    product_code=found["product_code"],
                    qty=qty,
                    package_product=bool(found.get("package_product", False)),
                    package_policy_ref=_norm_text(found.get("package_policy_ref", "")),
                    packing_policy_code=_norm_text(found.get("packing_policy_code", "")),
                    special_group=_norm_text(found.get("special_group", "")),
                    reason=None,
                )
            )
            continue

        # 2) 공백/대소문자 무시 매칭
        normalized_key = _match_key(input_name)
        normalized_candidates = normalized_lookup.get(normalized_key, [])

        if len(normalized_candidates) == 1:
            found = normalized_candidates[0]
            results.append(
                MatchedOrderLine(
                    input_name=input_name,
                    matched=True,
                    matched_name=found["matched_name"],
                    product_code=found["product_code"],
                    qty=qty,
                    package_product=bool(found.get("package_product", False)),
                    package_policy_ref=_norm_text(found.get("package_policy_ref", "")),
                    packing_policy_code=_norm_text(found.get("packing_policy_code", "")),
                    special_group=_norm_text(found.get("special_group", "")),
                    reason="NORMALIZED_MATCH",
                )
            )
            continue

        if len(normalized_candidates) > 1:
            results.append(
                MatchedOrderLine(
                    input_name=input_name,
                    matched=False,
                    matched_name=None,
                    product_code=None,
                    qty=qty,
                    package_product=False,
                    package_policy_ref="",
                    packing_policy_code="",
                    special_group="",
                    reason="AMBIGUOUS_NORMALIZED_MATCH",
                )
            )
            continue

        # 3) 최종 실패
        results.append(
            MatchedOrderLine(
                input_name=input_name,
                matched=False,
                matched_name=None,
                product_code=None,
                qty=qty,
                package_product=False,
                package_policy_ref="",
                packing_policy_code="",
                special_group="",
                reason="PRODUCT_NOT_FOUND",
            )
        )

    return results


def split_matched_orders(
    matched_orders: List[MatchedOrderLine],
) -> dict:
    matched = []
    unmatched = []

    for row in matched_orders:
        if row.matched:
            matched.append(
                {
                    "input_name": row.input_name,
                    "matched_name": row.matched_name,
                    "product_code": row.product_code,
                    "qty": row.qty,
                    "package_product": row.package_product,
                    "package_policy_ref": row.package_policy_ref,
                    "packing_policy_code": row.packing_policy_code,
                    "special_group": row.special_group,
                    "reason": row.reason,
                }
            )
        else:
            unmatched.append(
                {
                    "input_name": row.input_name,
                    "qty": row.qty,
                    "reason": row.reason,
                }
            )

    return {
        "matched": matched,
        "unmatched": unmatched,
    }


def print_match_result(matched_orders: List[MatchedOrderLine]) -> None:
    print("\n" + "=" * 90)
    print("[MATCH RESULT]")

    for i, row in enumerate(matched_orders, start=1):
        print(f"\n{i}. 입력값: {row.input_name}")
        print(f"   수량: {row.qty}")
        print(f"   매칭 여부: {row.matched}")
        print(f"   매칭 상품명: {row.matched_name}")
        print(f"   상품코드: {row.product_code}")

        if row.matched:
            print(f"   패키지상품여부: {row.package_product}")
            print(f"   패키지정책참조: {row.package_policy_ref}")
            print(f"   패킹정책코드: {row.packing_policy_code}")
            print(f"   특수상품군: {row.special_group}")

        if row.reason:
            print(f"   사유: {row.reason}")

    print("=" * 90)