from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

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


def _to_float(value, default: float = 0.0) -> float:
    if pd.isna(value) or value == "":
        return default
    try:
        return float(value)
    except Exception:
        return default


def _to_bool(value) -> bool:
    return _norm_text(value).upper() in {"Y", "YES", "TRUE", "1"}


@dataclass
class OrderLine:
    product_name: str
    qty: int


class FullboxEngineError(Exception):
    pass


def _build_product_lookup(prepared_products_df: pd.DataFrame) -> Dict[str, dict]:
    df = prepared_products_df.copy()

    lookup = {}
    for _, row in df.iterrows():
        name = _norm_text(row.get(_norm_col("국문상품명"), ""))
        if not name:
            continue

        package_flag = _to_bool(row.get(_norm_col("패키지상품여부"), False))
        package_policy_ref = _norm_text(row.get(_norm_col("패키지정책참조"), ""))
        packing_policy_code = _norm_text(row.get(_norm_col("패킹정책코드"), ""))

        lookup[name] = {
            "상품코드": _norm_text(row.get(_norm_col("상품코드"), "")),
            "국문상품명": name,
            "가로(cm)": _to_float(row.get(_norm_col("가로(cm)"), 0)),
            "세로(cm)": _to_float(row.get(_norm_col("세로(cm)"), 0)),
            "높이(cm)": _to_float(row.get(_norm_col("높이(cm)"), 0)),
            "개당중량(kg)": _to_float(row.get(_norm_col("개당중량(kg)"), 0)),
            "완박스입수량": int(_to_float(row.get(_norm_col("완박스입수량"), 0))),
            "완박스박스코드": _norm_text(row.get(_norm_col("완박스박스코드"), "")),
            "완박스박스명": _norm_text(row.get(_norm_col("완박스박스명"), "")),
            "혼합완박스허용여부": _to_bool(row.get(_norm_col("혼합완박스허용여부"), False)),
            "완박스혼합그룹": _norm_text(row.get(_norm_col("완박스혼합그룹"), "")),
            "패킹정책코드": packing_policy_code,
            "패키지상품여부": package_flag,
            "패키지정책참조": package_policy_ref,
        }

    return lookup


def _build_fullbox_only_lookup(fullboxes_df: pd.DataFrame | None) -> Dict[str, dict]:
    if fullboxes_df is None or len(fullboxes_df) == 0:
        return {}

    df = fullboxes_df.copy()
    lookup = {}

    for _, row in df.iterrows():
        name = _norm_text(row.get(_norm_col("국문상품명"), ""))
        if not name:
            continue

        fullbox_pack = int(_to_float(row.get(_norm_col("완박스입수량"), 0)))
        fullbox_weight = _to_float(row.get(_norm_col("완박스중량(kg)"), 0))

        lookup[name] = {
            "상품코드": _norm_text(row.get(_norm_col("상품코드"), "")),
            "국문상품명": name,
            "가로(cm)": _to_float(row.get(_norm_col("완박스가로(cm)"), 0)),
            "세로(cm)": _to_float(row.get(_norm_col("완박스세로(cm)"), 0)),
            "높이(cm)": _to_float(row.get(_norm_col("완박스높이(cm)"), 0)),
            "개당중량(kg)": fullbox_weight,
            "완박스입수량": fullbox_pack,
            "완박스박스코드": _norm_text(row.get(_norm_col("완박스박스코드"), "")),
            "완박스박스명": _norm_text(row.get(_norm_col("완박스박스명"), "")),
            "혼합완박스허용여부": _to_bool(row.get(_norm_col("혼합완박스허용여부"), False)),
            "완박스혼합그룹": _norm_text(row.get(_norm_col("완박스혼합그룹"), "")),
            "패킹정책코드": "",
            "패키지상품여부": False,
            "패키지정책참조": "",
        }

    return lookup


def _build_resolve_lookup(
    prepared_products_df: pd.DataFrame,
    fallback_fullboxes_df: pd.DataFrame | None = None,
) -> Dict[str, dict]:
    lookup = _build_product_lookup(prepared_products_df)

    for name, payload in _build_fullbox_only_lookup(fallback_fullboxes_df).items():
        lookup.setdefault(name, payload)

    return lookup


def _is_fullbox_candidate(product: dict) -> bool:
    return (
        product.get("완박스입수량", 0) > 0
        and _norm_text(product.get("완박스박스코드", "")) != ""
    )


def _same_fullbox_spec(a: dict, b: dict) -> bool:
    return (
        a.get("완박스입수량", 0) == b.get("완박스입수량", 0)
        and _norm_text(a.get("완박스박스코드", "")) == _norm_text(b.get("완박스박스코드", ""))
        and _norm_text(a.get("완박스박스명", "")) == _norm_text(b.get("완박스박스명", ""))
    )


def _within_tolerance(a: dict, b: dict, rules: dict) -> bool:
    tol_l = float(rules.get("FULLBOX_MIX_TOL_LENGTH_CM", 0.3))
    tol_w = float(rules.get("FULLBOX_MIX_TOL_WIDTH_CM", 0.3))
    tol_h = float(rules.get("FULLBOX_MIX_TOL_HEIGHT_CM", 0.3))
    tol_kg = float(rules.get("FULLBOX_MIX_TOL_WEIGHT_KG", 0.005))

    return (
        abs(float(a.get("가로(cm)", 0)) - float(b.get("가로(cm)", 0))) <= tol_l
        and abs(float(a.get("세로(cm)", 0)) - float(b.get("세로(cm)", 0))) <= tol_w
        and abs(float(a.get("높이(cm)", 0)) - float(b.get("높이(cm)", 0))) <= tol_h
        and abs(float(a.get("개당중량(kg)", 0)) - float(b.get("개당중량(kg)", 0))) <= tol_kg
    )


def _is_package_product(product: dict) -> bool:
    raw_flag = product.get("패키지상품여부", False)
    package_policy_ref = _norm_text(product.get("패키지정책참조", ""))
    packing_policy = _norm_text(product.get("패킹정책코드", "")).upper()

    return bool(raw_flag) or (package_policy_ref != "") or packing_policy.startswith("PACKAGE_")


def _is_package_sealed(product: dict) -> bool:
    policy = _norm_text(product.get("패킹정책코드", "")).upper()
    return policy == "PACKAGE_SEALED"


def _can_use_mixed_fullbox(product: dict, shipping_method: str = "auto") -> bool:
    if not product.get("혼합완박스허용여부", False):
        return False

    package_product = _is_package_product(product)
    fullbox_mode = str(shipping_method or "").strip().lower() == "fullbox"

    if package_product and not fullbox_mode:
        return False

    if _is_package_sealed(product):
        return False

    return True


def _can_mix_fullbox(a: dict, b: dict, rules: dict, shipping_method: str = "auto") -> bool:
    if not bool(rules.get("FULLBOX_MIX_ENABLE", True)):
        return False

    if not _can_use_mixed_fullbox(a, shipping_method=shipping_method):
        return False

    if not _can_use_mixed_fullbox(b, shipping_method=shipping_method):
        return False

    if not _same_fullbox_spec(a, b):
        return False

    return _within_tolerance(a, b, rules)


def _allocate_single_fullboxes(resolved_lines: List[dict]) -> Tuple[List[dict], List[dict]]:
    fullbox_allocations = []

    for line in resolved_lines:
        product = line["product"]
        qty = int(line["qty"])

        if not _is_fullbox_candidate(product):
            line["single_fullbox_count"] = 0
            line["remainder_qty"] = qty
            continue

        fullbox_pack = int(product["완박스입수량"])
        fullbox_count = qty // fullbox_pack
        remainder = qty % fullbox_pack

        line["single_fullbox_count"] = fullbox_count
        line["remainder_qty"] = remainder

        for _ in range(fullbox_count):
            fullbox_allocations.append(
                {
                    "type": "single_fullbox",
                    "box_code": product["완박스박스코드"],
                    "box_name": product["완박스박스명"],
                    "pack_size": fullbox_pack,
                    "items": [
                        {
                            "product_name": product["국문상품명"],
                            "qty": fullbox_pack,
                        }
                    ],
                }
            )

    return resolved_lines, fullbox_allocations


def _split_remainders_after_single(
    resolved_lines: List[dict],
    shipping_method: str = "auto",
) -> Tuple[List[dict], List[dict]]:
    mix_candidates = []
    direct_repack = []
    fullbox_mode = str(shipping_method or "").strip().lower() == "fullbox"

    for line in resolved_lines:
        product = line["product"]
        rem = int(line.get("remainder_qty", 0))

        if rem <= 0:
            continue

        payload = {
            "product": product,
            "qty": rem,
        }

        package_flag = _is_package_product(product)
        package_sealed = _is_package_sealed(product)

        if not _is_fullbox_candidate(product):
            if fullbox_mode:
                payload["reason"] = "FULLBOX_MODE_NO_FULLBOX_SPEC_FALLBACK"
            elif package_sealed:
                payload["reason"] = "PACKAGE_SEALED_NO_FULLBOX_SPEC"
            elif package_flag:
                payload["reason"] = "PACKAGE_PRODUCT_NO_FULLBOX_SPEC"
            else:
                payload["reason"] = "NO_FULLBOX_SPEC"

            direct_repack.append(payload)
            continue

        if package_sealed:
            payload["reason"] = "PACKAGE_SEALED_REMAINDER_TO_REPACK"
            direct_repack.append(payload)
            continue

        if package_flag and not fullbox_mode:
            payload["reason"] = "PACKAGE_PRODUCT_REMAINDER_TO_REPACK"
            direct_repack.append(payload)
            continue

        if not _can_use_mixed_fullbox(product, shipping_method=shipping_method):
            if fullbox_mode and package_flag:
                payload["reason"] = "FULLBOX_MODE_PACKAGE_MIX_NOT_ALLOWED"
            else:
                payload["reason"] = "FULLBOX_MIX_NOT_ALLOWED"
            direct_repack.append(payload)
            continue

        mix_candidates.append(payload)

    return mix_candidates, direct_repack


def _group_key_for_mix(product: dict) -> tuple:
    return (
        _norm_text(product.get("완박스혼합그룹", "")),
        int(product.get("완박스입수량", 0)),
        _norm_text(product.get("완박스박스코드", "")),
        _norm_text(product.get("완박스박스명", "")),
    )


def _allocate_group_mix_boxes(remainders: List[dict], rules: dict) -> Tuple[List[dict], List[dict]]:
    if not bool(rules.get("FULLBOX_MIX_GROUP_FIRST", True)):
        return remainders, []

    allocations = []
    grouped = {}

    for item in remainders:
        product = item["product"]
        group_name = _norm_text(product.get("완박스혼합그룹", ""))
        if not group_name:
            continue

        key = _group_key_for_mix(product)
        grouped.setdefault(key, []).append(item)

    for key, items in grouped.items():
        _, pack_size, box_code, box_name = key
        total_qty = sum(int(x["qty"]) for x in items)

        while total_qty >= pack_size:
            need = pack_size
            box_items = []

            for item in items:
                if need <= 0:
                    break

                take = min(int(item["qty"]), need)
                if take > 0:
                    box_items.append(
                        {
                            "product_name": item["product"]["국문상품명"],
                            "qty": take,
                        }
                    )
                    item["qty"] -= take
                    need -= take

            allocations.append(
                {
                    "type": "group_mixed_fullbox",
                    "box_code": box_code,
                    "box_name": box_name,
                    "pack_size": pack_size,
                    "items": box_items,
                }
            )
            total_qty = sum(int(x["qty"]) for x in items)

    remaining = [x for x in remainders if int(x["qty"]) > 0]
    return remaining, allocations


def _allocate_tolerance_mix_boxes(
    remainders: List[dict],
    rules: dict,
    shipping_method: str = "auto",
) -> Tuple[List[dict], List[dict]]:
    allocations = []

    while True:
        remainders = [x for x in remainders if int(x["qty"]) > 0]
        if not remainders:
            break

        allocated_this_round = False

        for seed in remainders:
            seed_product = seed["product"]
            pack_size = int(seed_product["완박스입수량"])

            if pack_size <= 0:
                continue

            compatible = []
            for item in remainders:
                p = item["product"]
                if _can_mix_fullbox(seed_product, p, rules, shipping_method=shipping_method):
                    compatible.append(item)

            total_compatible_qty = sum(int(x["qty"]) for x in compatible)
            if total_compatible_qty < pack_size:
                continue

            need = pack_size
            box_items = []

            for item in compatible:
                if need <= 0:
                    break

                take = min(int(item["qty"]), need)
                if take > 0:
                    box_items.append(
                        {
                            "product_name": item["product"]["국문상품명"],
                            "qty": take,
                        }
                    )
                    item["qty"] -= take
                    need -= take

            allocations.append(
                {
                    "type": "tolerance_mixed_fullbox",
                    "box_code": seed_product["완박스박스코드"],
                    "box_name": seed_product["완박스박스명"],
                    "pack_size": pack_size,
                    "items": box_items,
                }
            )

            allocated_this_round = True
            break

        if not allocated_this_round:
            break

    remaining = [x for x in remainders if int(x["qty"]) > 0]
    return remaining, allocations


def _build_repack_or_failed(remainders: List[dict], rules: dict) -> List[dict]:
    result = []
    to_repack = bool(rules.get("FULLBOX_REMAINDER_TO_REPACK", True))

    for item in remainders:
        if int(item["qty"]) <= 0:
            continue

        result.append(
            {
                "type": "repack_remainder" if to_repack else "fullbox_failed_remainder",
                "product_name": item["product"]["국문상품명"],
                "qty": int(item["qty"]),
                "reason": item.get("reason", "FULLBOX_REMAINDER"),
            }
        )

    return result


def resolve_orders(
    order_lines: List[OrderLine],
    prepared_products_df: pd.DataFrame,
    fallback_fullboxes_df: pd.DataFrame | None = None,
) -> Dict[str, List[dict]]:
    lookup = _build_resolve_lookup(prepared_products_df, fallback_fullboxes_df)

    resolved = []
    not_found = []

    for line in order_lines:
        product_name = _norm_text(line.product_name)
        qty = int(line.qty)

        if product_name not in lookup:
            not_found.append(
                {
                    "product_name": product_name,
                    "qty": qty,
                    "reason": "PRODUCT_NOT_FOUND",
                }
            )
            continue

        resolved.append(
            {
                "product_name": product_name,
                "qty": qty,
                "product": lookup[product_name],
            }
        )

    return {
        "resolved_lines": resolved,
        "not_found": not_found,
    }


def run_fullbox_engine(
    order_lines: List[OrderLine],
    prepared_products_df: pd.DataFrame,
    rules: dict,
    fallback_fullboxes_df: pd.DataFrame | None = None,
    shipping_method: str = "auto",
) -> Dict[str, List[dict]]:
    resolved_result = resolve_orders(
        order_lines=order_lines,
        prepared_products_df=prepared_products_df,
        fallback_fullboxes_df=fallback_fullboxes_df,
    )
    resolved_lines = resolved_result["resolved_lines"]
    not_found = resolved_result["not_found"]

    resolved_lines, single_allocations = _allocate_single_fullboxes(resolved_lines)

    mix_candidates, direct_repack = _split_remainders_after_single(
        resolved_lines,
        shipping_method=shipping_method,
    )

    mix_candidates, group_mix_allocations = _allocate_group_mix_boxes(mix_candidates, rules)
    mix_candidates, tol_mix_allocations = _allocate_tolerance_mix_boxes(
        mix_candidates,
        rules,
        shipping_method=shipping_method,
    )

    for item in mix_candidates:
        if int(item["qty"]) > 0 and not item.get("reason"):
            item["reason"] = "FULLBOX_MIX_REMAINDER"

    repack_or_failed = _build_repack_or_failed(direct_repack + mix_candidates, rules)

    return {
        "single_fullboxes": single_allocations,
        "group_mixed_fullboxes": group_mix_allocations,
        "tolerance_mixed_fullboxes": tol_mix_allocations,
        "remainders": repack_or_failed,
        "not_found": not_found,
    }


def print_fullbox_result(result: Dict[str, List[dict]]) -> None:
    print("\n" + "=" * 90)
    print("[FULLBOX RESULT]")

    print("\n[single_fullboxes]")
    for i, box in enumerate(result["single_fullboxes"], start=1):
        print(f"{i}. {box['box_name']} ({box['box_code']})")
        for item in box["items"]:
            print(f"   - {item['product_name']}: {item['qty']}")

    print("\n[group_mixed_fullboxes]")
    for i, box in enumerate(result["group_mixed_fullboxes"], start=1):
        print(f"{i}. {box['box_name']} ({box['box_code']})")
        for item in box["items"]:
            print(f"   - {item['product_name']}: {item['qty']}")

    print("\n[tolerance_mixed_fullboxes]")
    for i, box in enumerate(result["tolerance_mixed_fullboxes"], start=1):
        print(f"{i}. {box['box_name']} ({box['box_code']})")
        for item in box["items"]:
            print(f"   - {item['product_name']}: {item['qty']}")

    print("\n[remainders]")
    for i, item in enumerate(result["remainders"], start=1):
        print(f"{i}. {item['type']} / {item['product_name']} / {item['qty']} / {item['reason']}")

    print("\n[not_found]")
    for i, item in enumerate(result["not_found"], start=1):
        print(f"{i}. {item['product_name']} / {item['qty']} / {item['reason']}")

    print("=" * 90)
