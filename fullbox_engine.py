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


def _build_fullbox_carton_lookup(fullboxes_df: pd.DataFrame | None) -> Dict[str, dict]:
    lookup: Dict[str, dict] = {}
    if fullboxes_df is None or len(fullboxes_df) == 0:
        return lookup

    for _, row in fullboxes_df.iterrows():
        box_code = _norm_text(row.get(_norm_col("완박스박스코드"), ""))
        if not box_code or box_code in lookup:
            continue

        outer_l = _to_float(row.get(_norm_col("완박스가로(cm)"), 0))
        outer_w = _to_float(row.get(_norm_col("완박스세로(cm)"), 0))
        outer_h = _to_float(row.get(_norm_col("완박스높이(cm)"), 0))
        if min(outer_l, outer_w, outer_h) <= 0:
            continue

        lookup[box_code] = {
            "박스코드": box_code,
            "박스명": _norm_text(row.get(_norm_col("완박스박스명"), "")),
            "외경가로(cm)": outer_l,
            "외경세로(cm)": outer_w,
            "외경높이(cm)": outer_h,
            # inner dims are approximated because fullboxes master has only carton outer dims.
            "내경가로(cm)": outer_l,
            "내경세로(cm)": outer_w,
            "내경높이(cm)": outer_h,
            "박스중량(kg)": 0.0,
            "최대허용중량(kg)": 30.0,
            "박스정렬우선순위": 0.0,
        }

    return lookup


def _build_fixed_box_mix_candidates(remainders: List[dict]) -> Tuple[List[dict], Dict[str, dict]]:
    candidates: List[dict] = []
    candidate_lookup: Dict[str, dict] = {}

    for item in remainders:
        qty = int(item.get("qty", 0) or 0)
        product = item.get("product", {}) or {}
        if qty <= 0:
            continue

        product_name = _norm_text(product.get("국문상품명", ""))
        length_cm = _to_float(product.get("가로(cm)"), 0)
        width_cm = _to_float(product.get("세로(cm)"), 0)
        height_cm = _to_float(product.get("높이(cm)"), 0)
        unit_weight_kg = _to_float(product.get("개당중량(kg)"), 0)

        if not product_name or min(length_cm, width_cm, height_cm, unit_weight_kg) <= 0:
            return [], {}

        candidate = {
            "product_name": product_name,
            "qty": qty,
            "original_qty": qty,
            "package_pack_qty": 1,
            "calc_unit_type": "item",
            "length_cm": length_cm,
            "width_cm": width_cm,
            "height_cm": height_cm,
            "unit_weight_kg": unit_weight_kg,
            "special_group": _norm_text(product.get("특수상품군", "")),
            "package_product": bool(product.get("패키지상품여부", False)),
            "packing_policy_code": _norm_text(product.get("패킹정책코드", "")),
            "fullbox_pack_limit": int(product.get("완박스입수량", 0) or 0),
            "source_reason": item.get("reason", ""),
            "spec_source": "products_master",
        }
        candidates.append(candidate)
        candidate_lookup[product_name] = candidate

    return candidates, candidate_lookup


def _calc_min_layer_height_for_qty(layer_variants: List[dict], target_qty: int) -> float | None:
    target_qty = int(target_qty or 0)
    if target_qty <= 0:
        return 0.0

    inf = 10**9
    dp = [inf] * (target_qty + 1)
    dp[0] = 0

    parsed_variants = []
    for variant in layer_variants or []:
        count_per_layer = int(variant.get("count_per_layer", variant.get("count", 0)) or 0)
        layer_height_tenth_cm = int(round(float(variant.get("layer_height_cm", 0) or 0) * 10))
        if count_per_layer <= 0 or layer_height_tenth_cm <= 0:
            continue
        parsed_variants.append((count_per_layer, layer_height_tenth_cm))

    if not parsed_variants:
        return None

    for filled_qty in range(target_qty + 1):
        if dp[filled_qty] >= inf:
            continue
        for count_per_layer, layer_height_tenth_cm in parsed_variants:
            next_qty = min(target_qty, filled_qty + count_per_layer)
            next_height = dp[filled_qty] + layer_height_tenth_cm
            if next_height < dp[next_qty]:
                dp[next_qty] = next_height

    if dp[target_qty] >= inf:
        return None

    return dp[target_qty] / 10.0


def _can_fit_mixed_partial_fullbox_carton_by_layers(
    remainders: List[dict],
    candidate_lookup: Dict[str, dict],
    selected_box: dict,
    rules: dict,
) -> bool:
    from repack_engine import _calc_best_orientation_fit

    layer_groups: Dict[Tuple, dict] = {}
    total_weight_kg = 0.0

    for item in remainders:
        qty = int(item.get("qty", 0) or 0)
        product = item.get("product", {}) or {}
        product_name = _norm_text(product.get("국문상품명", ""))
        if qty <= 0 or not product_name:
            continue

        meta = candidate_lookup.get(product_name, {}) or {}
        total_weight_kg += qty * float(meta.get("unit_weight_kg", 0) or 0)

        group_key = (
            round(float(meta.get("length_cm", 0) or 0), 3),
            round(float(meta.get("width_cm", 0) or 0), 3),
            round(float(meta.get("height_cm", 0) or 0), 3),
            _norm_text(product.get("완박스박스코드", "")),
        )
        if group_key not in layer_groups:
            layer_groups[group_key] = {
                "qty": 0,
                "candidate": meta,
                "pack_limit": int(product.get("완박스입수량", 0) or 0),
            }
        layer_groups[group_key]["qty"] += qty
        pack_limit = int(product.get("완박스입수량", 0) or 0)
        if pack_limit > 0:
            current_limit = int(layer_groups[group_key]["pack_limit"] or 0)
            if current_limit <= 0:
                layer_groups[group_key]["pack_limit"] = pack_limit
            else:
                layer_groups[group_key]["pack_limit"] = min(current_limit, pack_limit)

    effective_weight_capacity = min(
        float(selected_box["최대허용중량(kg)"]),
        float(rules.get("BOX_MAX_WEIGHT_KG", 30)),
    ) - float(selected_box["박스중량(kg)"])
    if total_weight_kg > effective_weight_capacity + 1e-9:
        return False

    inner_height_cm = float(selected_box["내경높이(cm)"])
    layer_tol_cm = float(rules.get("FULLBOX_MIX_LAYER_TOL_CM", 1.0) or 1.0)
    used_height_cm = 0.0

    for group in layer_groups.values():
        fit_info = _calc_best_orientation_fit(selected_box, group["candidate"], group["qty"], rules)
        physical_max_units = int(fit_info.get("global_best_max_units_per_box", 0) or 0)
        pack_limit = int(group.get("pack_limit", 0) or 0)
        effective_cap = min(physical_max_units, pack_limit) if pack_limit > 0 else physical_max_units

        if effective_cap <= 0 or int(group["qty"]) > effective_cap:
            return False

        layer_variants = ((fit_info.get("fit_result") or {}).get("layer_variants") or [])
        min_height_cm = _calc_min_layer_height_for_qty(layer_variants, int(group["qty"]))
        if min_height_cm is None:
            return False

        used_height_cm += float(min_height_cm)

    return used_height_cm <= (inner_height_cm + layer_tol_cm)


def _try_allocate_mixed_partial_fullbox_carton(
    remainders: List[dict],
    rules: dict,
    fallback_fullboxes_df: pd.DataFrame | None = None,
    shipping_method: str = "auto",
) -> Tuple[List[dict], List[dict]]:
    fullbox_mode = str(shipping_method or "").strip().lower() == "fullbox"
    if not fullbox_mode or not remainders:
        return remainders, []

    carton_lookup = _build_fullbox_carton_lookup(fallback_fullboxes_df)
    if not carton_lookup:
        return remainders, []

    candidates, candidate_lookup = _build_fixed_box_mix_candidates(remainders)
    if not candidates:
        return remainders, []

    candidate_box_codes = []
    for item in remainders:
        product = item.get("product", {}) or {}
        box_code = _norm_text(product.get("완박스박스코드", ""))
        if box_code and box_code in carton_lookup and box_code not in candidate_box_codes:
            candidate_box_codes.append(box_code)

    candidate_box_codes.sort(
        key=lambda code: (
            carton_lookup[code]["외경가로(cm)"] * carton_lookup[code]["외경세로(cm)"] * carton_lookup[code]["외경높이(cm)"],
            code,
        )
    )

    if not candidate_box_codes:
        return remainders, []

    best_box = None

    for box_code in candidate_box_codes:
        selected_box = carton_lookup[box_code]
        if _can_fit_mixed_partial_fullbox_carton_by_layers(
            remainders=remainders,
            candidate_lookup=candidate_lookup,
            selected_box=selected_box,
            rules=rules,
        ):
            best_box = selected_box
            break

    if not best_box:
        return remainders, []

    allocation = {
        "type": "mixed_partial_fullbox_carton",
        "box_code": best_box["박스코드"],
        "box_name": best_box["박스명"],
        "pack_size": 0,
        "gross_weight_kg": round(
            sum(
                int(item.get("qty", 0) or 0)
                * float(candidate_lookup.get(_norm_text((item.get("product") or {}).get("국문상품명", "")), {}).get("unit_weight_kg", 0) or 0)
                for item in remainders
            ),
            3,
        ),
        "items": [
            {
                "product_name": alloc.get("product_name", ""),
                "qty": int(alloc.get("qty", 0) or 0),
            }
            for alloc in [
                {
                    "product_name": _norm_text((item.get("product") or {}).get("국문상품명", "")),
                    "qty": int(item.get("qty", 0) or 0),
                }
                for item in remainders
            ]
        ],
    }

    return [], [allocation]


def _allocate_partial_fullbox_cartons(
    remainders: List[dict],
    shipping_method: str = "auto",
) -> Tuple[List[dict], List[dict]]:
    fullbox_mode = str(shipping_method or "").strip().lower() == "fullbox"
    if not fullbox_mode:
        return remainders, []

    allocations = []
    remaining = []

    for item in remainders:
        qty = int(item.get("qty", 0))
        product = item.get("product", {}) or {}

        if qty <= 0:
            continue

        if not _is_fullbox_candidate(product):
            remaining.append(item)
            continue

        gross_weight = round(float(product.get("개당중량(kg)", 0) or 0) * qty, 3)
        allocations.append(
            {
                "type": "partial_fullbox_carton",
                "box_code": product["완박스박스코드"],
                "box_name": product["완박스박스명"],
                "pack_size": int(product.get("완박스입수량", 0) or 0),
                "gross_weight_kg": gross_weight,
                "items": [
                    {
                        "product_name": product["국문상품명"],
                        "qty": qty,
                    }
                ],
            }
        )

    return remaining, allocations


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

    partial_fullbox_inputs = direct_repack + mix_candidates
    partial_fullbox_inputs, mixed_partial_fullbox_allocations = _try_allocate_mixed_partial_fullbox_carton(
        partial_fullbox_inputs,
        rules,
        fallback_fullboxes_df=fallback_fullboxes_df,
        shipping_method=shipping_method,
    )
    partial_fullbox_inputs, partial_fullbox_allocations = _allocate_partial_fullbox_cartons(
        partial_fullbox_inputs,
        shipping_method=shipping_method,
    )

    for item in partial_fullbox_inputs:
        if int(item["qty"]) > 0 and not item.get("reason"):
            item["reason"] = "FULLBOX_MIX_REMAINDER"

    repack_or_failed = _build_repack_or_failed(partial_fullbox_inputs, rules)

    return {
        "single_fullboxes": single_allocations,
        "group_mixed_fullboxes": group_mix_allocations,
        "tolerance_mixed_fullboxes": tol_mix_allocations,
        "mixed_partial_fullbox_cartons": mixed_partial_fullbox_allocations,
        "partial_fullbox_cartons": partial_fullbox_allocations,
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

    print("\n[mixed_partial_fullbox_cartons]")
    for i, box in enumerate(result.get("mixed_partial_fullbox_cartons", []), start=1):
        print(f"{i}. {box['box_name']} ({box['box_code']})")
        for item in box["items"]:
            print(f"   - {item['product_name']}: {item['qty']}")

    print("\n[partial_fullbox_cartons]")
    for i, box in enumerate(result.get("partial_fullbox_cartons", []), start=1):
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
