from __future__ import annotations
from collections import Counter
from itertools import permutations
import re
import math

from result_formatter import format_engine_result
from dataclasses import dataclass
from itertools import permutations
from math import floor, ceil
from typing import Any, Dict, List, Tuple

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


def _round_cm(value: float) -> float:
    return round(float(value), 1)


def _rule_bool(value, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    text = _norm_text(value).upper()
    if text in {"TRUE", "1", "Y", "YES"}:
        return True
    if text in {"FALSE", "0", "N", "NO"}:
        return False
    return default


def _rule_int(value, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except Exception:
        return default


BOX_CAP_POLICY_MAP: Dict[str, Dict[str, int]] = {
    "BOX_CAP_BX010_300_BX008_200": {
        "BX010": 300,
        "BX008": 200,
    },
}


def _resolve_box_cap_override(item: dict | None, box_code: Any) -> int | None:
    if not isinstance(item, dict):
        return None

    policy_code = _norm_text(item.get("packing_policy_code", "")).upper()
    if not policy_code:
        return None

    box_caps = BOX_CAP_POLICY_MAP.get(policy_code)
    if not box_caps:
        return None

    normalized_box_code = _norm_text(box_code).upper()
    cap = box_caps.get(normalized_box_code)
    if cap is None:
        return None
    return max(0, int(cap))


@dataclass
class RepackCandidate:
    product_name: str
    qty: int
    original_qty: int
    package_pack_qty: int
    calc_unit_type: str
    length_cm: float
    width_cm: float
    height_cm: float
    unit_weight_kg: float
    special_group: str
    package_product: bool
    packing_policy_code: str
    source_reason: str
    spec_source: str
    candidate_note: str = ""
    mixed_unpack_allowed: bool = False
    fallback_item_length_cm: float = 0.0
    fallback_item_width_cm: float = 0.0
    fallback_item_height_cm: float = 0.0
    fallback_item_unit_weight_kg: float = 0.0


def _build_product_lookup(prepared_products_df: pd.DataFrame) -> Dict[str, dict]:
    df = prepared_products_df.copy()

    lookup: Dict[str, dict] = {}

    for _, row in df.iterrows():
        name = _norm_text(row.get(_norm_col("국문상품명"), ""))
        if not name:
            continue

        package_policy_ref = _norm_text(row.get(_norm_col("패키지정책참조"), ""))
        packing_policy_code = _norm_text(row.get(_norm_col("패킹정책코드"), ""))

        lookup[name] = {
            "상품코드": _norm_text(row.get(_norm_col("상품코드"), "")),
            "국문상품명": name,
            "가로(cm)": _to_float(row.get(_norm_col("가로(cm)"), 0)),
            "세로(cm)": _to_float(row.get(_norm_col("세로(cm)"), 0)),
            "높이(cm)": _to_float(row.get(_norm_col("높이(cm)"), 0)),
            "개당중량(kg)": _to_float(row.get(_norm_col("개당중량(kg)"), 0)),
            "특수상품군": _norm_text(row.get(_norm_col("특수상품군"), "")),
            "패키지상품여부": _to_bool(row.get(_norm_col("패키지상품여부"), False))
            or (package_policy_ref != "")
            or packing_policy_code.upper().startswith("PACKAGE_"),
            "패킹정책코드": packing_policy_code,
            "패키지정책참조": package_policy_ref,
        }

    return lookup


def _build_fullbox_package_lookup(fullboxes_df: pd.DataFrame | None) -> Dict[str, dict]:
    if fullboxes_df is None or len(fullboxes_df) == 0:
        return {}

    df = fullboxes_df.copy()
    lookup: Dict[str, dict] = {}

    for _, row in df.iterrows():
        name = _norm_text(row.get(_norm_col("국문상품명"), ""))
        if not name:
            continue

        product_code = _norm_text(row.get(_norm_col("상품코드"), ""))
        payload = {
            "상품코드": _norm_text(row.get(_norm_col("상품코드"), "")),
            "국문상품명": name,
            "완박스입수량": _to_float(row.get(_norm_col("완박스입수량"), 0)),
            "완박스가로(cm)": _to_float(row.get(_norm_col("완박스가로(cm)"), 0)),
            "완박스세로(cm)": _to_float(row.get(_norm_col("완박스세로(cm)"), 0)),
            "완박스높이(cm)": _to_float(row.get(_norm_col("완박스높이(cm)"), 0)),
            # fullboxes_master weight means gross weight for the fullbox package,
            # not per-item weight.
            "완박스중량(kg)": _to_float(row.get(_norm_col("완박스중량(kg)"), 0)),
        }

        lookup[f"name::{name}"] = payload
        lookup[f"name_nospace::{name.replace(' ', '')}"] = payload
        if product_code:
            lookup[f"code::{product_code}"] = payload

    return lookup


def _find_fullbox_package_spec(product_name: str, product_code: str, fullbox_lookup: Dict[str, dict]) -> dict | None:
    product_name = _norm_text(product_name)
    product_code = _norm_text(product_code)
    product_name_nospace = product_name.replace(" ", "")

    if product_code and f"code::{product_code}" in fullbox_lookup:
        return fullbox_lookup[f"code::{product_code}"]

    if product_name and f"name::{product_name}" in fullbox_lookup:
        return fullbox_lookup[f"name::{product_name}"]

    if product_name_nospace and f"name_nospace::{product_name_nospace}" in fullbox_lookup:
        return fullbox_lookup[f"name_nospace::{product_name_nospace}"]

    return None


def _build_package_only_product_lookup(prepared_packages_df: pd.DataFrame | None) -> Dict[str, dict]:
    if prepared_packages_df is None or len(prepared_packages_df) == 0:
        return {}

    df = prepared_packages_df.copy()
    lookup: Dict[str, dict] = {}

    for _, row in df.iterrows():
        name = _norm_text(row.get(_norm_col("국문상품명"), ""))
        if not name:
            continue

        lookup[name] = {
            "상품코드": _norm_text(row.get(_norm_col("상품코드"), "")),
            "국문상품명": name,
            # packages_master only entries can still be packed as package units.
            # Item-level remainder calc will remain unresolved if no item spec exists.
            "가로(cm)": 0.0,
            "세로(cm)": 0.0,
            "높이(cm)": 0.0,
            "개당중량(kg)": 0.0,
            "특수상품군": "",
            "패키지상품여부": True,
            "패킹정책코드": _norm_text(row.get(_norm_col("엔진기본처리정책"), "")),
            "패키지정책참조": "packages_master",
        }

    return lookup


def _build_package_lookup(prepared_packages_df: pd.DataFrame | None) -> Dict[str, dict]:
    if prepared_packages_df is None or len(prepared_packages_df) == 0:
        return {}

    df = prepared_packages_df.copy()
    lookup: Dict[str, dict] = {}

    for _, row in df.iterrows():
        product_code = _norm_text(row.get(_norm_col("상품코드"), ""))
        product_name = _norm_text(row.get(_norm_col("국문상품명"), ""))
        product_name_nospace = product_name.replace(" ", "")

        payload = {
            "상품코드": product_code,
            "국문상품명": product_name,
            "패키지입수량": _to_float(row.get(_norm_col("패키지입수량"), 0)),
            "패키지가로(cm)": _to_float(row.get(_norm_col("패키지가로(cm)"), 0)),
            "패키지세로(cm)": _to_float(row.get(_norm_col("패키지세로(cm)"), 0)),
            "패키지높이(cm)": _to_float(row.get(_norm_col("패키지높이(cm)"), 0)),
            "패키지중량(kg)": _to_float(row.get(_norm_col("패키지중량(kg)"), 0)),
            "패키지해체정책": _norm_text(row.get(_norm_col("패키지해체정책"), "")),
            "엔진기본처리정책": _norm_text(row.get(_norm_col("엔진기본처리정책"), "")),
            "패키지BOM존재여부": _to_bool(row.get(_norm_col("패키지BOM존재여부"), False)),
            "해체허용조건": _norm_text(row.get(_norm_col("해체허용조건"), "")),
            "해체후계산단위": _norm_text(row.get(_norm_col("해체후계산단위"), "")),
            "잔량처리방식": _norm_text(row.get(_norm_col("잔량처리방식"), "")),
        }

        if product_name:
            lookup[f"name::{product_name}"] = payload

        if product_name_nospace:
            lookup[f"name_nospace::{product_name_nospace}"] = payload

        if product_code:
            lookup[f"code::{product_code}"] = payload

    return lookup


def _find_package_spec(product: dict, package_lookup: Dict[str, dict]) -> dict | None:
    product_name = _norm_text(product.get("국문상품명", ""))
    product_code = _norm_text(product.get("상품코드", ""))
    product_name_nospace = product_name.replace(" ", "")

    if product_code and f"code::{product_code}" in package_lookup:
        return package_lookup[f"code::{product_code}"]

    if product_name and f"name::{product_name}" in package_lookup:
        return package_lookup[f"name::{product_name}"]

    if product_name_nospace and f"name_nospace::{product_name_nospace}" in package_lookup:
        return package_lookup[f"name_nospace::{product_name_nospace}"]

    return None


def _has_valid_package_spec(package_spec: dict | None) -> bool:
    if not package_spec:
        return False

    return (
        float(package_spec.get("패키지가로(cm)", 0)) > 0
        and float(package_spec.get("패키지세로(cm)", 0)) > 0
        and float(package_spec.get("패키지높이(cm)", 0)) > 0
        and float(package_spec.get("패키지중량(kg)", 0)) > 0
    )


def _should_use_package_spec(product: dict, package_spec: dict | None) -> bool:
    if not _has_valid_package_spec(package_spec):
        return False

    package_flag = bool(product.get("패키지상품여부", False))
    packing_policy = _norm_text(product.get("패킹정책코드", "")).upper()
    source_ref = _norm_text(product.get("패키지정책참조", ""))
    package_policy = _norm_text(package_spec.get("엔진기본처리정책", "")).upper()

    if package_flag or packing_policy.startswith("PACKAGE_") or source_ref != "":
        return True

    # If a validated packages_master row exists, prefer package-level packing by default.
    return package_policy in {"PACKAGE_AS_IS_FIRST", "PACKAGE_SEALED", "PACKAGE_ONLY"} or True


def _build_candidate_note(source_reason: str, candidate_kind: str) -> str:
    reason = _norm_text(source_reason).upper()
    kind = _norm_text(candidate_kind).lower()

    if kind == "package_unit":
        return "패키지 기준 포장"
    if kind == "package_remainder_item":
        return "패키지 잔량 낱개 포장"

    reason_map = {
        "FULLBOX_MODE_NO_FULLBOX_SPEC_FALLBACK": "완박스 스펙 없음 -> 재포장",
        "FULLBOX_MODE_PACKAGE_MIX_NOT_ALLOWED": "완박스 예외 미허용 -> 재포장",
        "PACKAGE_PRODUCT_REMAINDER_TO_REPACK": "패키지 상품 잔량 재포장",
        "PACKAGE_SEALED_REMAINDER_TO_REPACK": "패키지 밀봉 잔량 재포장",
        "FULLBOX_MIX_NOT_ALLOWED": "혼합완박스 불가 -> 재포장",
        "FULLBOX_MIX_REMAINDER": "완박스 잔량 재포장",
        "NO_FULLBOX_SPEC": "완박스 스펙 없음 -> 재포장",
        "FORCED_REPACK": "재포장 지정",
    }
    return reason_map.get(reason, "")


def _build_box_lookup(prepared_boxes_df: pd.DataFrame) -> List[dict]:
    df = prepared_boxes_df.copy()

    results = []
    for _, row in df.iterrows():
        results.append(
            {
                "박스코드": _norm_text(row.get(_norm_col("박스코드"), "")),
                "박스명": _norm_text(row.get(_norm_col("박스명"), "")),
                "외경가로(cm)": _to_float(row.get(_norm_col("외경가로(cm)"), 0)),
                "외경세로(cm)": _to_float(row.get(_norm_col("외경세로(cm)"), 0)),
                "외경높이(cm)": _to_float(row.get(_norm_col("외경높이(cm)"), 0)),
                "내경가로(cm)": _to_float(row.get(_norm_col("내경가로(cm)"), 0)),
                "내경세로(cm)": _to_float(row.get(_norm_col("내경세로(cm)"), 0)),
                "내경높이(cm)": _to_float(row.get(_norm_col("내경높이(cm)"), 0)),
                "박스중량(kg)": _to_float(row.get(_norm_col("박스중량(kg)"), 0)),
                "최대허용중량(kg)": _to_float(row.get(_norm_col("최대허용중량(kg)"), 0)),
                "박스정렬우선순위": _to_float(row.get(_norm_col("박스정렬우선순위"), 999999)),
            }
        )
    return results


def build_repack_candidates(
    fullbox_result: Dict[str, List[dict]],
    prepared_products_df: pd.DataFrame,
    prepared_packages_df: pd.DataFrame | None = None,
    fallback_fullboxes_df: pd.DataFrame | None = None,
) -> Dict[str, List[dict]]:
    product_lookup = _build_product_lookup(prepared_products_df)
    for product_name, payload in _build_package_only_product_lookup(prepared_packages_df).items():
        product_lookup.setdefault(product_name, payload)
    package_lookup = _build_package_lookup(prepared_packages_df)
    fullbox_package_lookup = _build_fullbox_package_lookup(fallback_fullboxes_df)

    candidates: List[RepackCandidate] = []
    unresolved: List[dict] = []
    invalid_specs: List[dict] = []

    remainders = fullbox_result.get("remainders", [])

    for item in remainders:
        product_name = _norm_text(item.get("product_name", ""))
        original_qty = int(item.get("qty", 0))
        reason = _norm_text(item.get("reason", ""))

        if original_qty <= 0:
            continue

        product = product_lookup.get(product_name)
        if not product:
            fullbox_package_spec = _find_fullbox_package_spec(
                product_name=product_name,
                product_code="",
                fullbox_lookup=fullbox_package_lookup,
            )
            if not fullbox_package_spec:
                unresolved.append(
                    {
                        "product_name": product_name,
                        "qty": original_qty,
                        "reason": "REPACK_PRODUCT_LOOKUP_FAILED",
                        "source_reason": reason,
                    }
                )
                continue

            fullbox_pack_qty = int(_to_float(fullbox_package_spec.get("완박스입수량", 0), 0))
            package_qty = original_qty // fullbox_pack_qty if fullbox_pack_qty > 0 else 0
            remainder_item_qty = original_qty % fullbox_pack_qty if fullbox_pack_qty > 0 else original_qty

            package_length_cm = float(fullbox_package_spec.get("완박스가로(cm)", 0) or 0)
            package_width_cm = float(fullbox_package_spec.get("완박스세로(cm)", 0) or 0)
            package_height_cm = float(fullbox_package_spec.get("완박스높이(cm)", 0) or 0)
            package_unit_weight_kg = float(fullbox_package_spec.get("완박스중량(kg)", 0) or 0)

            if fullbox_pack_qty <= 0:
                invalid_specs.append(
                    {
                        "product_name": product_name,
                        "qty": original_qty,
                        "reason": "INVALID_FULLBOX_PACK_QTY",
                        "source_reason": reason,
                        "length_cm": package_length_cm,
                        "width_cm": package_width_cm,
                        "height_cm": package_height_cm,
                        "unit_weight_kg": package_unit_weight_kg,
                        "spec_source": "fullboxes_master",
                    }
                )
                continue

            if (
                package_length_cm <= 0
                or package_width_cm <= 0
                or package_height_cm <= 0
                or package_unit_weight_kg <= 0
            ):
                invalid_specs.append(
                    {
                        "product_name": product_name,
                        "qty": original_qty,
                        "reason": "INVALID_FULLBOX_PACKAGE_SPEC",
                        "source_reason": reason,
                        "length_cm": package_length_cm,
                        "width_cm": package_width_cm,
                        "height_cm": package_height_cm,
                        "unit_weight_kg": package_unit_weight_kg,
                        "spec_source": "fullboxes_master",
                    }
                )
                continue

            if package_qty > 0:
                candidates.append(
                    RepackCandidate(
                        product_name=product_name,
                        qty=package_qty,
                        original_qty=package_qty * fullbox_pack_qty,
                        package_pack_qty=fullbox_pack_qty,
                        calc_unit_type="package",
                        length_cm=package_length_cm,
                        width_cm=package_width_cm,
                        height_cm=package_height_cm,
                        unit_weight_kg=package_unit_weight_kg,
                        special_group="",
                        package_product=True,
                        packing_policy_code="FULLBOX_PACKAGE_ONLY",
                        source_reason=reason,
                        spec_source="fullboxes_master",
                        candidate_note=_build_candidate_note(reason, "package_unit"),
                    )
                )

            if remainder_item_qty > 0:
                unresolved.append(
                    {
                        "product_name": product_name,
                        "qty": remainder_item_qty,
                        "reason": "FULLBOX_ONLY_REMAINDER_SPEC_NOT_AVAILABLE",
                        "source_reason": reason,
                        "package_pack_qty": fullbox_pack_qty,
                        "spec_source": "fullboxes_master",
                    }
                )

            continue

        package_spec = _find_package_spec(product, package_lookup)
        use_package_spec = _should_use_package_spec(product, package_spec)

        # -------------------------------------------------
        # 1) package spec 우선 처리
        #    - 나눠떨어지지 않아도
        #      몫은 package / 잔량은 item 으로 분리
        # -------------------------------------------------
        if use_package_spec:
            package_pack_qty = int(_to_float(package_spec.get("패키지입수량", 0), 0))

            if package_pack_qty <= 0:
                invalid_specs.append(
                    {
                        "product_name": product_name,
                        "qty": original_qty,
                        "reason": "INVALID_PACKAGE_PACK_QTY",
                        "source_reason": reason,
                        "length_cm": 0,
                        "width_cm": 0,
                        "height_cm": 0,
                        "unit_weight_kg": 0,
                        "spec_source": "packages_master",
                    }
                )
                continue

            package_qty = original_qty // package_pack_qty
            remainder_item_qty = original_qty % package_pack_qty

            # 1-A) package 부분 candidate 생성
            if package_qty > 0:
                package_length_cm = float(package_spec["패키지가로(cm)"])
                package_width_cm = float(package_spec["패키지세로(cm)"])
                package_height_cm = float(package_spec["패키지높이(cm)"])
                package_unit_weight_kg = float(package_spec["패키지중량(kg)"])
                fallback_item_length_cm = float(product["가로(cm)"])
                fallback_item_width_cm = float(product["세로(cm)"])
                fallback_item_height_cm = float(product["높이(cm)"])
                fallback_item_unit_weight_kg = float(product["개당중량(kg)"])
                package_policy = _norm_text(package_spec.get("엔진기본처리정책", "")).upper()
                mixed_unpack_allowed = (
                    package_policy != "PACKAGE_ONLY"
                    and fallback_item_length_cm > 0
                    and fallback_item_width_cm > 0
                    and fallback_item_height_cm > 0
                    and fallback_item_unit_weight_kg > 0
                )

                if (
                    package_length_cm <= 0
                    or package_width_cm <= 0
                    or package_height_cm <= 0
                    or package_unit_weight_kg <= 0
                ):
                    invalid_specs.append(
                        {
                            "product_name": product_name,
                            "qty": package_qty * package_pack_qty,
                            "reason": "INVALID_PACKAGE_SPEC",
                            "source_reason": reason,
                            "length_cm": package_length_cm,
                            "width_cm": package_width_cm,
                            "height_cm": package_height_cm,
                            "unit_weight_kg": package_unit_weight_kg,
                            "spec_source": "packages_master",
                        }
                    )
                else:
                    candidates.append(
                        RepackCandidate(
                            product_name=product_name,
                            qty=package_qty,
                            original_qty=package_qty * package_pack_qty,
                            package_pack_qty=package_pack_qty,
                            calc_unit_type="package",
                            length_cm=package_length_cm,
                            width_cm=package_width_cm,
                            height_cm=package_height_cm,
                            unit_weight_kg=package_unit_weight_kg,
                            special_group=product["특수상품군"],
                            package_product=bool(product["패키지상품여부"]),
                            packing_policy_code=product["패킹정책코드"],
                            source_reason=reason,
                            spec_source="packages_master",
                            candidate_note=_build_candidate_note(reason, "package_unit"),
                            mixed_unpack_allowed=mixed_unpack_allowed,
                            fallback_item_length_cm=fallback_item_length_cm,
                            fallback_item_width_cm=fallback_item_width_cm,
                            fallback_item_height_cm=fallback_item_height_cm,
                            fallback_item_unit_weight_kg=fallback_item_unit_weight_kg,
                        )
                    )

            # 1-B) 잔량은 item 기준 candidate 생성
            if remainder_item_qty > 0:
                item_length_cm = float(product["가로(cm)"])
                item_width_cm = float(product["세로(cm)"])
                item_height_cm = float(product["높이(cm)"])
                item_unit_weight_kg = float(product["개당중량(kg)"])

                if (
                    item_length_cm <= 0
                    or item_width_cm <= 0
                    or item_height_cm <= 0
                    or item_unit_weight_kg <= 0
                ):
                    unresolved.append(
                        {
                            "product_name": product_name,
                            "qty": remainder_item_qty,
                            "reason": "PACKAGE_REMAINDER_ITEM_SPEC_NOT_AVAILABLE",
                            "source_reason": reason,
                            "package_pack_qty": package_pack_qty,
                        }
                    )
                else:
                    candidates.append(
                        RepackCandidate(
                            product_name=product_name,
                            qty=remainder_item_qty,
                            original_qty=remainder_item_qty,
                            package_pack_qty=1,
                            calc_unit_type="item",
                            length_cm=item_length_cm,
                            width_cm=item_width_cm,
                            height_cm=item_height_cm,
                            unit_weight_kg=item_unit_weight_kg,
                            special_group=product["특수상품군"],
                            package_product=bool(product["패키지상품여부"]),
                            packing_policy_code=product["패킹정책코드"],
                            source_reason=reason,
                            spec_source="products_master",
                            candidate_note=_build_candidate_note(reason, "package_remainder_item"),
                        )
                    )

            # package / remainder 둘 다 0이면 이상 케이스
            if package_qty <= 0 and remainder_item_qty <= 0:
                unresolved.append(
                    {
                        "product_name": product_name,
                        "qty": original_qty,
                        "reason": "INVALID_CALC_QTY",
                        "source_reason": reason,
                    }
                )

            continue

        # -------------------------------------------------
        # 2) package spec 안 쓰는 일반 item 처리
        # -------------------------------------------------
        calc_qty = original_qty
        package_pack_qty = 1
        calc_unit_type = "item"

        length_cm = float(product["가로(cm)"])
        width_cm = float(product["세로(cm)"])
        height_cm = float(product["높이(cm)"])
        unit_weight_kg = float(product["개당중량(kg)"])
        spec_source = "products_master"

        if calc_qty <= 0:
            unresolved.append(
                {
                    "product_name": product_name,
                    "qty": original_qty,
                    "reason": "INVALID_CALC_QTY",
                    "source_reason": reason,
                }
            )
            continue

        if length_cm <= 0 or width_cm <= 0 or height_cm <= 0 or unit_weight_kg <= 0:
            invalid_specs.append(
                {
                    "product_name": product_name,
                    "qty": original_qty,
                    "reason": "INVALID_PRODUCT_SPEC",
                    "source_reason": reason,
                    "length_cm": length_cm,
                    "width_cm": width_cm,
                    "height_cm": height_cm,
                    "unit_weight_kg": unit_weight_kg,
                    "spec_source": spec_source,
                }
            )
            continue

        candidates.append(
            RepackCandidate(
                product_name=product_name,
                qty=calc_qty,
                original_qty=original_qty,
                package_pack_qty=package_pack_qty,
                calc_unit_type=calc_unit_type,
                length_cm=length_cm,
                width_cm=width_cm,
                height_cm=height_cm,
                unit_weight_kg=unit_weight_kg,
                special_group=product["특수상품군"],
                package_product=bool(product["패키지상품여부"]),
                packing_policy_code=product["패킹정책코드"],
                source_reason=reason,
                spec_source=spec_source,
                candidate_note=_build_candidate_note(reason, "item"),
            )
        )

    return {
        "candidates": [c.__dict__ for c in candidates],
        "unresolved": unresolved,
        "invalid_specs": invalid_specs,
    }


def _all_orientations(length_cm: float, width_cm: float, height_cm: float) -> List[Tuple[float, float, float]]:
    return list(set(permutations([length_cm, width_cm, height_cm], 3)))


def _calc_fit_units_for_orientation(
    box_inner_l: float,
    box_inner_w: float,
    box_inner_h: float,
    item_l: float,
    item_w: float,
    item_h: float,
) -> int:
    if item_l <= 0 or item_w <= 0 or item_h <= 0:
        return 0

    nx = floor(box_inner_l / item_l)
    ny = floor(box_inner_w / item_w)
    nz = floor(box_inner_h / item_h)

    if nx <= 0 or ny <= 0 or nz <= 0:
        return 0

    return int(nx * ny * nz)


def _calc_units_by_weight(box: dict, item: dict, rules: dict) -> int:
    box_weight = float(box["박스중량(kg)"])
    box_limit = float(box["최대허용중량(kg)"])
    global_limit = float(rules.get("BOX_MAX_WEIGHT_KG", 30))
    unit_weight = float(item["unit_weight_kg"])

    effective_limit = min(box_limit, global_limit)

    if unit_weight <= 0 or effective_limit <= box_weight:
        return 0

    return max(0, floor((effective_limit - box_weight) / unit_weight))


def _calc_layer_capacity(
    inner_size_cm: Tuple[float, float, float],
    orientation: Tuple[float, float, float] | None,
) -> int:
    if not orientation:
        return 0

    inner_l, inner_w, _ = [float(x) for x in inner_size_cm]
    item_l, item_w, _ = [float(x) for x in orientation]

    if item_l <= 0 or item_w <= 0:
        return 0

    nx = floor(inner_l / item_l)
    ny = floor(inner_w / item_w)

    if nx <= 0 or ny <= 0:
        return 0

    return int(nx * ny)


def _calc_trim_info(
    inner_size_cm: Tuple[float, float, float],
    outer_size_cm: Tuple[float, float, float],
    orientation: Tuple[float, float, float] | None,
    item_qty: int,
    keep_height_cm: float = 1.0,
    min_remaining_height_for_trim_cm: float = 3.0,
) -> dict:
    if not orientation or item_qty <= 0:
        return {
            "layer_capacity": 0,
            "layers_needed": 0,
            "used_height_cm": 0.0,
            "remaining_height_cm": 0.0,
            "trim_cut_height_cm": 0.0,
            "trimmed_inner_height_cm": _round_cm(inner_size_cm[2]),
            "trimmed_outer_height_cm": _round_cm(outer_size_cm[2]),
        }

    inner_l, inner_w, inner_h = [float(x) for x in inner_size_cm]
    _, _, outer_h = [float(x) for x in outer_size_cm]
    _, _, item_h = [float(x) for x in orientation]

    layer_capacity = _calc_layer_capacity(inner_size_cm, orientation)

    if layer_capacity <= 0 or item_h <= 0:
        return {
            "layer_capacity": 0,
            "layers_needed": 0,
            "used_height_cm": 0.0,
            "remaining_height_cm": 0.0,
            "trim_cut_height_cm": 0.0,
            "trimmed_inner_height_cm": _round_cm(inner_h),
            "trimmed_outer_height_cm": _round_cm(outer_h),
        }

    layers_needed = ceil(item_qty / layer_capacity)
    used_height_cm = layers_needed * item_h
    remaining_height_cm = max(0.0, inner_h - used_height_cm)

    ignore_trim_for_small_box = (
        abs(float(outer_size_cm[0]) - 22.0) <= 0.01
        and abs(float(outer_size_cm[1]) - 19.0) <= 0.01
        and abs(float(outer_size_cm[2]) - 9.0) <= 0.01
    ) or (
        abs(float(outer_size_cm[0]) - 27.0) <= 0.01
        and abs(float(outer_size_cm[1]) - 18.0) <= 0.01
        and abs(float(outer_size_cm[2]) - 15.0) <= 0.01
    )

    trim_cut_height_cm = 0.0
    if (not ignore_trim_for_small_box) and remaining_height_cm >= min_remaining_height_for_trim_cm:
        trim_cut_height_cm = max(0.0, remaining_height_cm - keep_height_cm)

    trimmed_inner_height_cm = max(0.0, inner_h - trim_cut_height_cm)
    trimmed_outer_height_cm = max(0.0, outer_h - trim_cut_height_cm)

    return {
        "layer_capacity": int(layer_capacity),
        "layers_needed": int(layers_needed),
        "used_height_cm": _round_cm(used_height_cm),
        "remaining_height_cm": _round_cm(remaining_height_cm),
        "trim_cut_height_cm": _round_cm(trim_cut_height_cm),
        "trimmed_inner_height_cm": _round_cm(trimmed_inner_height_cm),
        "trimmed_outer_height_cm": _round_cm(trimmed_outer_height_cm),
    }


from collections import Counter
from itertools import permutations
from typing import Dict, List, Optional, Tuple, Any

GRID_SCALE = 10  # 0.1 cm 단위 정수화


def _to_grid(v: float, scale: int = GRID_SCALE) -> int:
    return int(round(float(v) * scale))


def _from_grid(v: int, scale: int = GRID_SCALE) -> float:
    return round(v / scale, 1)


def _unique_axis_orientations(spec_cm: Tuple[float, float, float]) -> List[Tuple[int, int, int]]:
    """
    (x, y, z) 모든 축 회전 경우의 수(중복 제거)를 0.1cm 정수 단위로 반환
    """
    dims = tuple(_to_grid(x) for x in spec_cm)
    seen = set()
    out = []

    for p in permutations(dims, 3):
        if p not in seen:
            seen.add(p)
            out.append(p)

    return out


def _solve_best_mixed_layer(
    floor_l: int,
    floor_w: int,
    a: int,
    b: int,
) -> Dict[str, Any]:
    """
    한 층에서 footprint (a,b)와 (b,a)를 혼합 허용하여
    최대로 들어가는 개수를 계산한다.

    여기서는 실무형 strip/shelf 모델을 사용한다.
    - row mix: 층의 폭 방향으로 서로 다른 row 높이(a 또는 b)를 혼합
    - col mix: 층의 길이 방향으로 서로 다른 column 폭(a 또는 b)를 혼합

    반환 예시:
    {
        "count": 123,
        "mode": "row" or "col",
        "rows_a": ...,
        "rows_b": ...,
        "cols_a": ...,
        "cols_b": ...,
        "used_l": ...,
        "used_w": ...,
        "primary": (a,b),
        "rotated": (b,a),
    }
    """
    best: Optional[Dict[str, Any]] = None

    def _is_better(cand: Dict[str, Any], cur: Optional[Dict[str, Any]]) -> bool:
        if cur is None:
            return True

        # 우선순위:
        # 1) 개수 최대
        # 2) 사용 면적 최대
        # 3) 보조축 사용량 최대
        # 4) 주축 사용량 최대
        cand_key = (
            cand["count"],
            cand["used_area"],
            cand["used_secondary"],
            cand["used_primary"],
        )
        cur_key = (
            cur["count"],
            cur["used_area"],
            cur["used_secondary"],
            cur["used_primary"],
        )
        return cand_key > cur_key

    # -------------------------------------------------
    # 1) row mix
    #   - orientation A row: row 높이 = b, row당 개수 = floor_l // a
    #   - orientation B row: row 높이 = a, row당 개수 = floor_l // b
    # -------------------------------------------------
    if a <= floor_l and b <= floor_w:
        per_row_a = floor_l // a  # (a,b)
        per_row_b = floor_l // b  # (b,a)

        max_rows_a = floor_w // b
        for rows_a in range(max_rows_a + 1):
            rem_w = floor_w - rows_a * b
            rows_b = rem_w // a

            count = rows_a * per_row_a + rows_b * per_row_b
            used_w = rows_a * b + rows_b * a

            cand = {
                "count": count,
                "mode": "row",
                "rows_a": rows_a,
                "rows_b": rows_b,
                "cols_a": 0,
                "cols_b": 0,
                "used_l": floor_l,
                "used_w": used_w,
                "used_area": count * a * b,
                "used_primary": floor_l,
                "used_secondary": used_w,
                "primary": (a, b),
                "rotated": (b, a),
            }
            if _is_better(cand, best):
                best = cand

    # -------------------------------------------------
    # 2) col mix
    #   - orientation A col: col 폭 = a, col당 개수 = floor_w // b
    #   - orientation B col: col 폭 = b, col당 개수 = floor_w // a
    # -------------------------------------------------
    if b <= floor_l and a <= floor_w:
        per_col_a = floor_w // b  # (a,b)
        per_col_b = floor_w // a  # (b,a)

        max_cols_a = floor_l // a
        for cols_a in range(max_cols_a + 1):
            rem_l = floor_l - cols_a * a
            cols_b = rem_l // b

            count = cols_a * per_col_a + cols_b * per_col_b
            used_l = cols_a * a + cols_b * b

            cand = {
                "count": count,
                "mode": "col",
                "rows_a": 0,
                "rows_b": 0,
                "cols_a": cols_a,
                "cols_b": cols_b,
                "used_l": used_l,
                "used_w": floor_w,
                "used_area": count * a * b,
                "used_primary": floor_w,
                "used_secondary": used_l,
                "primary": (a, b),
                "rotated": (b, a),
            }
            if _is_better(cand, best):
                best = cand

    if best is None:
        return {
            "count": 0,
            "mode": None,
            "rows_a": 0,
            "rows_b": 0,
            "cols_a": 0,
            "cols_b": 0,
            "used_l": 0,
            "used_w": 0,
            "used_area": 0,
            "used_primary": 0,
            "used_secondary": 0,
            "primary": (a, b),
            "rotated": (b, a),
        }

    return best


def _build_layer_variants(
    item_spec_cm: Tuple[float, float, float],
    box_inner_cm: Tuple[float, float, float],
) -> List[Dict[str, Any]]:
    """
    층 높이(z축) 후보별로 layer variant 생성
    한 variant는:
    - 해당 층 높이
    - 그 층에서 가능한 최대 적재수
    - 혼합 회전 배치 방식(row/col)
    를 가진다.
    """
    inner_l, inner_w, inner_h = [_to_grid(x) for x in box_inner_cm]

    variants: List[Dict[str, Any]] = []
    seen = set()

    for x, y, z in _unique_axis_orientations(item_spec_cm):
        if z > inner_h:
            continue

        # 같은 layer 높이 + 같은 footprint 조합은 중복 제거
        key = (tuple(sorted((x, y))), z)
        if key in seen:
            continue
        seen.add(key)

        layer_fit = _solve_best_mixed_layer(inner_l, inner_w, x, y)
        if layer_fit["count"] <= 0:
            continue

        variants.append(
            {
                "layer_height": z,
                "layer_height_cm": _from_grid(z),
                "count": layer_fit["count"],
                "count_per_layer": layer_fit["count"],
                "footprint_primary_cm": (_from_grid(x), _from_grid(y)),
                "footprint_rotated_cm": (_from_grid(y), _from_grid(x)),
                "base_orientation_cm": (_from_grid(x), _from_grid(y), _from_grid(z)),
                "layout": layer_fit,
            }
        )

    # 층당 적재량 높은 순, 같으면 층 높이 낮은 순
    variants.sort(key=lambda v: (v["count"], -v["layer_height"]), reverse=True)
    return variants


def _compose_layers_by_dp(
    inner_h: int,
    layer_variants: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    박스 전체 높이 안에서 서로 다른 layer variant를 조합하여
    최대 적재 개수를 찾는다.
    """
    if not layer_variants:
        return {
            "max_fit": 0,
            "used_height": 0,
            "remaining_height": inner_h,
            "layer_sequence": [],
            "layer_summary": [],
        }

    dp_count = [-1] * (inner_h + 1)
    dp_layers = [10**9] * (inner_h + 1)
    prev: List[Optional[Tuple[int, int]]] = [None] * (inner_h + 1)

    dp_count[0] = 0
    dp_layers[0] = 0

    for used_h in range(inner_h + 1):
        if dp_count[used_h] < 0:
            continue

        for idx, variant in enumerate(layer_variants):
            nh = used_h + variant["layer_height"]
            if nh > inner_h:
                continue

            new_count = dp_count[used_h] + variant["count"]
            new_layers = dp_layers[used_h] + 1

            cur_count = dp_count[nh]
            cur_layers = dp_layers[nh]

            # 우선순위:
            # 1) 총 적재수 최대
            # 2) 같은 적재수면 층 수 적은 쪽
            if (
                new_count > cur_count
                or (new_count == cur_count and new_layers < cur_layers)
            ):
                dp_count[nh] = new_count
                dp_layers[nh] = new_layers
                prev[nh] = (used_h, idx)

    best_h = 0
    for h in range(inner_h + 1):
        if dp_count[h] < 0:
            continue
        if dp_count[h] > dp_count[best_h]:
            best_h = h
        elif dp_count[h] == dp_count[best_h]:
            if h > best_h:
                best_h = h

    # layer sequence 복원
    layer_indices: List[int] = []
    cur = best_h
    while cur > 0 and prev[cur] is not None:
        ph, idx = prev[cur]
        layer_indices.append(idx)
        cur = ph
    layer_indices.reverse()

    layer_sequence = []
    for idx in layer_indices:
        variant = layer_variants[idx]
        layer_sequence.append(
            {
                "layer_height_cm": variant["layer_height_cm"],
                "count": variant["count"],
                "base_orientation_cm": variant["base_orientation_cm"],
                "footprint_primary_cm": variant["footprint_primary_cm"],
                "footprint_rotated_cm": variant["footprint_rotated_cm"],
                "layout": variant["layout"],
            }
        )

    # 같은 layer variant 묶어서 summary 생성
    counter = Counter(layer_indices)
    layer_summary = []
    for idx, n in counter.items():
        variant = layer_variants[idx]
        layer_summary.append(
            {
                "repeat": n,
                "layer_height_cm": variant["layer_height_cm"],
                "count_per_layer": variant["count"],
                "total_count": variant["count"] * n,
                "base_orientation_cm": variant["base_orientation_cm"],
                "footprint_primary_cm": variant["footprint_primary_cm"],
                "footprint_rotated_cm": variant["footprint_rotated_cm"],
                "layout": variant["layout"],
            }
        )

    layer_summary.sort(
        key=lambda x: (
            x["layer_height_cm"],
            x["count_per_layer"],
        ),
        reverse=True,
    )

    return {
        "max_fit": dp_count[best_h],
        "used_height": best_h,
        "remaining_height": inner_h - best_h,
        "layer_sequence": layer_sequence,
        "layer_summary": layer_summary,
    }


def _calc_layered_mixed_fit(
    item_spec_cm: Tuple[float, float, float],
    box_inner_cm: Tuple[float, float, float],
) -> Dict[str, Any]:
    """
    새 핵심 함수
    - 층 기반
    - 층 내 혼합 회전 허용
    - 층별 다른 배치 허용
    """
    inner_l, inner_w, inner_h = [_to_grid(x) for x in box_inner_cm]

    variants = _build_layer_variants(item_spec_cm, box_inner_cm)
    dp_result = _compose_layers_by_dp(inner_h, variants)

    max_fit = dp_result["max_fit"]
    used_height_cm = _from_grid(dp_result["used_height"])
    remaining_height_cm = _from_grid(dp_result["remaining_height"])

    # 하위 호환용 대표 orientation 1개
    representative_orientation = None
    if dp_result["layer_sequence"]:
        representative_orientation = dp_result["layer_sequence"][0]["base_orientation_cm"]

    return {
        "max_fit": max_fit,
        "best_orientation": representative_orientation,  # 하위 호환용
        "box_inner_cm": box_inner_cm,
        "used_height_cm": used_height_cm,
        "remaining_height_cm": remaining_height_cm,
        "layer_variants": variants,
        "layer_sequence": dp_result["layer_sequence"],
        "layer_summary": dp_result["layer_summary"],
    }


def _extract_item_spec_cm(item) -> Tuple[float, float, float]:
    """
    item dict에서 상품 규격 3축(cm)을 최대한 유연하게 추출
    """
    if isinstance(item, dict):
        # 1) tuple/list 형태
        for key in ("spec_cm", "size_cm", "item_spec_cm", "dims_cm"):
            v = item.get(key)
            if isinstance(v, (tuple, list)) and len(v) == 3:
                return tuple(float(x) for x in v)

        # 2) 개별 컬럼 형태
        for keys in (
            ("length_cm", "width_cm", "height_cm"),
            ("spec_x", "spec_y", "spec_z"),
            ("x", "y", "z"),
        ):
            if all(k in item and item[k] not in (None, "") for k in keys):
                return tuple(float(item[k]) for k in keys)

        # 3) spec 문자열 파싱: "20.5x7.8x6.8 cm"
        spec = item.get("spec")
        if isinstance(spec, str):
            nums = re.findall(r"\d+(?:\.\d+)?", spec)
            if len(nums) >= 3:
                return tuple(float(x) for x in nums[:3])

    raise ValueError(f"item spec 추출 실패: {item}")


def _extract_box_inner_cm(box) -> Tuple[float, float, float]:
    """
    box dict에서 박스 내경 3축(cm)을 최대한 유연하게 추출
    """
    if isinstance(box, dict):
        # 1) tuple/list 형태
        for key in ("inner_size_cm", "box_inner_cm", "inner_cm"):
            v = box.get(key)
            if isinstance(v, (tuple, list)) and len(v) == 3:
                return tuple(float(x) for x in v)

        # 2) 영어 개별 컬럼 형태
        for keys in (
            ("inner_l", "inner_w", "inner_h"),
            ("box_inner_l", "box_inner_w", "box_inner_h"),
            ("inner_length", "inner_width", "inner_height"),
        ):
            if all(k in box and box[k] not in (None, "") for k in keys):
                return tuple(float(box[k]) for k in keys)

        # 3) 한글 내경 컬럼 형태
        for keys in (
            ("내경가로(cm)", "내경세로(cm)", "내경높이(cm)"),
            ("내경가로", "내경세로", "내경높이"),
        ):
            if all(k in box and box[k] not in (None, "") for k in keys):
                return tuple(float(box[k]) for k in keys)

        # 4) outer_size_cm만 있으면 -1cm 규칙 적용
        outer = box.get("outer_size_cm")
        if isinstance(outer, (tuple, list)) and len(outer) == 3:
            return tuple(float(x) - 1.0 for x in outer)

        # 5) 한글 외경 컬럼만 있으면 -1cm 규칙 적용
        for keys in (
            ("외경가로(cm)", "외경세로(cm)", "외경높이(cm)"),
            ("외경가로", "외경세로", "외경높이"),
        ):
            if all(k in box and box[k] not in (None, "") for k in keys):
                return (
                    float(box[keys[0]]) - 1.0,
                    float(box[keys[1]]) - 1.0,
                    float(box[keys[2]]) - 1.0,
                )

        # 6) spec 문자열 파싱
        spec = (
            box.get("spec")
            or box.get("box_spec")
            or box.get("size")
            or box.get("박스명")
        )
        if isinstance(spec, str):
            nums = re.findall(r"\d+(?:\.\d+)?", spec)
            if len(nums) >= 3:
                # 박스명은 보통 외경이므로 -1cm 적용
                return (
                    float(nums[0]) - 1.0,
                    float(nums[1]) - 1.0,
                    float(nums[2]) - 1.0,
                )

    raise ValueError(f"box inner spec 추출 실패: {box}")

def _calc_best_orientation_fit(*args, **kwargs):
    """
    하위호환 wrapper

    1) 새 방식:
       _calc_best_orientation_fit(item_spec_cm, box_inner_cm)
       -> (max_fit, best_orientation) 반환

    2) 기존 방식:
       _calc_best_orientation_fit(box, item, qty, rules)
       -> fit_info dict 반환
    """
    # ---------------------------
    # 새 2인자 호출 방식
    # ---------------------------
    if len(args) == 2 and not kwargs:
        item_spec_cm, box_inner_cm = args
        result = _calc_layered_mixed_fit(item_spec_cm, box_inner_cm)
        return result["max_fit"], result["best_orientation"]

    # ---------------------------
    # 기존 4인자 호출 방식
    # ---------------------------
    if len(args) >= 2:
        box = args[0]
        item = args[1]
        qty = args[2] if len(args) >= 3 else None
        rules = args[3] if len(args) >= 4 and isinstance(args[3], dict) else {}
        keep_height_cm = _resolve_trim_keep_height_cm(rules)

        item_spec_cm = _extract_item_spec_cm(item)
        box_inner_cm = _extract_box_inner_cm(box)

        result = _calc_layered_mixed_fit(item_spec_cm, box_inner_cm)

        global_best_units_by_space = int(result["max_fit"])
        global_best_orientation = result["best_orientation"]

        units_by_weight = int(_calc_units_by_weight(box, item, rules))

        # 현재는 새 엔진의 공간 최적 결과를 그대로 추천값으로 사용
        recommended_units_by_space = global_best_units_by_space
        recommended_max_units_per_box = min(recommended_units_by_space, units_by_weight)

        if recommended_max_units_per_box <= 0 or global_best_orientation is None:
            trim_info_first_box = {
                "layer_capacity": 0,
                "layers_needed": 0,
                "used_height_cm": 0.0,
                "remaining_height_cm": 0.0,
                "trim_cut_height_cm": 0.0,
                "trimmed_inner_height_cm": _round_cm(box_inner_cm[2]),
                "trimmed_outer_height_cm": _round_cm(float(box.get("외경높이(cm)", box_inner_cm[2] + 1.0))),
            }
            boxes_needed = None
        else:
            outer_size_cm = (
                float(box.get("외경가로(cm)", box_inner_cm[0] + 1.0)),
                float(box.get("외경세로(cm)", box_inner_cm[1] + 1.0)),
                float(box.get("외경높이(cm)", box_inner_cm[2] + 1.0)),
            )

            first_box_qty = min(int(qty), recommended_max_units_per_box) if qty is not None else recommended_max_units_per_box

            trim_info_first_box = _calc_trim_info(
                inner_size_cm=box_inner_cm,
                outer_size_cm=outer_size_cm,
                orientation=global_best_orientation,
                item_qty=first_box_qty,
                keep_height_cm=keep_height_cm,
            )

            boxes_needed = math.ceil(qty / recommended_max_units_per_box) if qty is not None else None

        fit_info = {
            # 기존 호환용 핵심 키
            "max_fit": global_best_units_by_space,
            "global_best_units_by_space": global_best_units_by_space,
            "global_best_max_units_per_box": global_best_units_by_space,
            "max_units_per_box": recommended_max_units_per_box,

            # orientation 호환 키
            "best_orientation": global_best_orientation,
            "global_best_orientation": global_best_orientation,
            "recommended_orientation": global_best_orientation,

            # 기존 evaluate_repack_box_candidates가 기대하는 키
            "recommended_units_by_space": recommended_units_by_space,
            "units_by_weight": units_by_weight,
            "recommended_max_units_per_box": recommended_max_units_per_box,
            "boxes_needed": boxes_needed,
            "trim_info_first_box": trim_info_first_box,

            # 새 엔진 상세 결과
            "box_inner_cm": result["box_inner_cm"],
            "used_height_cm": result["used_height_cm"],
            "remaining_height_cm": result["remaining_height_cm"],
            "layer_sequence": result["layer_sequence"],
            "layer_summary": result["layer_summary"],
            "fit_result": result,
        }

        return fit_info

    raise TypeError(f"_calc_best_orientation_fit 인자 해석 실패: args={args}, kwargs={kwargs}")
def _resolve_primary_large_box_code(boxes: List[dict], rules: dict) -> str:
    rule_box_code = _norm_text(rules.get("REPACK_PRIMARY_BOX_CODE", ""))
    if rule_box_code:
        for box in boxes:
            if _norm_text(box.get("박스코드", "")) == rule_box_code:
                return rule_box_code

    if not boxes:
        return ""

    largest_box = max(
        boxes,
        key=lambda b: (
            float(b.get("내경가로(cm)", 0)) * float(b.get("내경세로(cm)", 0)) * float(b.get("내경높이(cm)", 0)),
            -float(b.get("박스정렬우선순위", 999999)),
        ),
    )
    return _norm_text(largest_box.get("박스코드", ""))


def _resolve_trim_keep_height_cm(rules: dict | None) -> float:
    if not isinstance(rules, dict):
        return 1.0
    value = rules.get("REPACK_TRIM_KEEP_HEIGHT_CM", 1.0)
    try:
        keep_height = float(value)
    except Exception:
        keep_height = 1.0
    return max(0.0, keep_height)


def _resolve_trim_min_remaining_height_cm(rules: dict | None) -> float:
    if not isinstance(rules, dict):
        return 3.0
    value = rules.get("REPACK_TRIM_MIN_REMAINING_HEIGHT_CM", 3.0)
    try:
        min_remaining_height = float(value)
    except Exception:
        min_remaining_height = 3.0
    return max(0.0, min_remaining_height)


def evaluate_repack_box_candidates(
    repack_result: Dict[str, List[dict]],
    prepared_boxes_df: pd.DataFrame,
    rules: dict,
) -> Dict[str, List[dict]]:
    boxes = _build_box_lookup(prepared_boxes_df)

    box_candidates = []
    no_box_fit = []

    bulk_qty_threshold = _rule_int(rules.get("REPACK_BULK_QTY_THRESHOLD", 100), 100)
    prefer_big_box_first = _rule_bool(rules.get("REPACK_BULK_BIG_BOX_FIRST", True), True)
    primary_large_box_code = _resolve_primary_large_box_code(boxes, rules)

    for item in repack_result.get("candidates", []):
        product_name = item["product_name"]
        qty = int(item["qty"])
        item_unit_volume = float(item["length_cm"]) * float(item["width_cm"]) * float(item["height_cm"])

        per_box_results = []

        for box in boxes:
            fit_info = _calc_best_orientation_fit(box, item, qty, rules)

            global_best_units_by_space = int(fit_info["global_best_units_by_space"])
            global_best_max_units = int(fit_info["global_best_max_units_per_box"])
            global_best_orientation = fit_info["global_best_orientation"]

            recommended_units_by_space = int(fit_info["recommended_units_by_space"])
            units_by_weight = int(fit_info["units_by_weight"])
            recommended_max_units = int(fit_info["recommended_max_units_per_box"])
            orientation = fit_info["best_orientation"]
            trim_info_first_box = fit_info["trim_info_first_box"]

            policy_box_cap = _resolve_box_cap_override(item, box.get("박스코드", ""))
            if policy_box_cap is not None:
                recommended_max_units = min(recommended_max_units, policy_box_cap)
                if recommended_max_units > 0 and orientation is not None:
                    outer_size_cm = (
                        float(box.get("외경가로(cm)", box["내경가로(cm)"] + 1.0)),
                        float(box.get("외경세로(cm)", box["내경세로(cm)"] + 1.0)),
                        float(box.get("외경높이(cm)", box["내경높이(cm)"] + 1.0)),
                    )
                    first_box_qty = min(qty, recommended_max_units)
                    trim_info_first_box = _calc_trim_info(
                        inner_size_cm=(
                            float(box["내경가로(cm)"]),
                            float(box["내경세로(cm)"]),
                            float(box["내경높이(cm)"]),
                        ),
                        outer_size_cm=outer_size_cm,
                        orientation=orientation,
                        item_qty=first_box_qty,
                        keep_height_cm=_resolve_trim_keep_height_cm(rules),
                        min_remaining_height_for_trim_cm=_resolve_trim_min_remaining_height_cm(rules),
                    )

            if recommended_max_units <= 0 or orientation is None:
                continue

            boxes_needed = int(math.ceil(qty / recommended_max_units))

            inner_l = float(box["내경가로(cm)"])
            inner_w = float(box["내경세로(cm)"])
            inner_h = float(box["내경높이(cm)"])
            inner_volume_cm3 = inner_l * inner_w * inner_h

            first_box_qty = min(qty, recommended_max_units)
            estimated_fill_ratio_first_box = 0.0
            if inner_volume_cm3 > 0:
                estimated_fill_ratio_first_box = (first_box_qty * item_unit_volume) / inner_volume_cm3

            per_box_results.append(
                {
                    "product_name": product_name,
                    "qty": qty,
                    "box_code": box["박스코드"],
                    "box_name": box["박스명"],
                    "box_priority": box["박스정렬우선순위"],
                    "inner_size_cm": (
                        box["내경가로(cm)"],
                        box["내경세로(cm)"],
                        box["내경높이(cm)"],
                    ),
                    "outer_size_cm": (
                        box["외경가로(cm)"],
                        box["외경세로(cm)"],
                        box["외경높이(cm)"],
                    ),
                    "inner_volume_cm3": inner_volume_cm3,
                    "box_weight_kg": box["박스중량(kg)"],
                    "max_box_weight_kg": min(
                        float(box["최대허용중량(kg)"]),
                        float(rules.get("BOX_MAX_WEIGHT_KG", 30)),
                    ),
                    "units_by_space": recommended_units_by_space,
                    "units_by_weight": units_by_weight,
                    "max_units_per_box": recommended_max_units,
                    "boxes_needed": boxes_needed,
                    "best_orientation": orientation,
                    "unit_weight_kg": float(item["unit_weight_kg"]),
                    "estimated_fill_ratio_first_box": estimated_fill_ratio_first_box,
                    "source_reason": item["source_reason"],
                    "candidate_note": item.get("candidate_note", ""),
                    "packing_policy_code": item.get("packing_policy_code", ""),
                    "spec_source": item.get("spec_source", ""),
                    "mixed_unpack_allowed": bool(item.get("mixed_unpack_allowed", False)),
                    "fallback_item_length_cm": float(item.get("fallback_item_length_cm", 0) or 0),
                    "fallback_item_width_cm": float(item.get("fallback_item_width_cm", 0) or 0),
                    "fallback_item_height_cm": float(item.get("fallback_item_height_cm", 0) or 0),
                    "fallback_item_unit_weight_kg": float(item.get("fallback_item_unit_weight_kg", 0) or 0),
                    "first_box_trim_info": trim_info_first_box,
                    "global_best_units_by_space": global_best_units_by_space,
                    "global_best_max_units_per_box": global_best_max_units,
                    "global_best_orientation": global_best_orientation,
                    "recommended_units_by_space": recommended_units_by_space,
                    "recommended_max_units_per_box": recommended_max_units,
                }
            )

        is_bulk_case = prefer_big_box_first and qty >= bulk_qty_threshold

        if is_bulk_case:
            per_box_results = sorted(
                per_box_results,
                key=lambda x: (
                    x["boxes_needed"],
                    0 if x["box_code"] == primary_large_box_code else 1,
                    -x["inner_volume_cm3"],
                    -x["max_units_per_box"],
                    -x["estimated_fill_ratio_first_box"],
                    x["box_priority"],
                ),
            )
        else:
            per_box_results = sorted(
                per_box_results,
                key=lambda x: (
                    x["boxes_needed"],
                    x["inner_volume_cm3"],
                    -x["first_box_trim_info"]["trim_cut_height_cm"],
                    -x["estimated_fill_ratio_first_box"],
                    x["box_priority"],
                ),
            )

        if not per_box_results:
            no_box_fit.append(
                {
                    "product_name": product_name,
                    "qty": qty,
                    "reason": "NO_REPACK_BOX_FIT",
                    "source_reason": item["source_reason"],
                }
            )
            continue

        selected_recommended_box = per_box_results[0].copy()
        selected_recommended_box["selection_policy"] = (
            f"BULK_BIG_BOX_FIRST({primary_large_box_code})"
            if is_bulk_case
            else "DEFAULT_SMALLEST_BOX_FIRST"
        )

        box_candidates.append(
            {
                "product_name": product_name,
                "qty": qty,
                "original_qty": item.get("original_qty", qty),
                "package_pack_qty": item.get("package_pack_qty", 1),
                "calc_unit_type": item.get("calc_unit_type", "item"),
                "length_cm": float(item.get("length_cm", 0) or 0),
                "width_cm": float(item.get("width_cm", 0) or 0),
                "height_cm": float(item.get("height_cm", 0) or 0),
                "unit_weight_kg": float(item.get("unit_weight_kg", 0) or 0),
                "packing_policy_code": item.get("packing_policy_code", ""),
                "is_bulk_case": is_bulk_case,
                "candidate_note": item.get("candidate_note", ""),
                "packing_policy_code": item.get("packing_policy_code", ""),
                "mixed_unpack_allowed": bool(item.get("mixed_unpack_allowed", False)),
                "fallback_item_length_cm": float(item.get("fallback_item_length_cm", 0) or 0),
                "fallback_item_width_cm": float(item.get("fallback_item_width_cm", 0) or 0),
                "fallback_item_height_cm": float(item.get("fallback_item_height_cm", 0) or 0),
                "fallback_item_unit_weight_kg": float(item.get("fallback_item_unit_weight_kg", 0) or 0),
                "recommended_box": selected_recommended_box,
                "all_box_candidates": per_box_results,
            }
        )

    return {
        "box_candidates": box_candidates,
        "no_box_fit": no_box_fit,
        "unresolved": repack_result.get("unresolved", []),
        "invalid_specs": repack_result.get("invalid_specs", []),
    }


def _build_box_line_from_candidate(
    candidate: dict,
    item_qty: int,
    box_no: int,
    keep_height_cm: float,
    note: str = "",
) -> dict:
    gross_weight = candidate["box_weight_kg"] + (item_qty * candidate["unit_weight_kg"])
    trim_info = _calc_trim_info(
        inner_size_cm=candidate["inner_size_cm"],
        outer_size_cm=candidate["outer_size_cm"],
        orientation=candidate["best_orientation"],
        item_qty=item_qty,
        keep_height_cm=keep_height_cm,
    )

    return {
        "box_no": box_no,
        "box_code": candidate["box_code"],
        "box_name": candidate["box_name"],
        "qty": item_qty,
        "gross_weight_est": round(gross_weight, 3),
        "layer_capacity": trim_info["layer_capacity"],
        "layers_needed": trim_info["layers_needed"],
        "used_height_cm": trim_info["used_height_cm"],
        "remaining_height_cm": trim_info["remaining_height_cm"],
        "trim_cut_height_cm": trim_info["trim_cut_height_cm"],
        "trimmed_inner_height_cm": trim_info["trimmed_inner_height_cm"],
        "trimmed_outer_height_cm": trim_info["trimmed_outer_height_cm"],
        "outer_size_cm": candidate["outer_size_cm"],
        "inner_size_cm": candidate["inner_size_cm"],
        "best_orientation": candidate["best_orientation"],
        "display_trimmed_height": False,
        "note": str(note or "").strip(),
    }


def _select_repack_candidate_for_qty(
    candidates: List[dict],
    qty: int,
    keep_height_cm: float,
) -> dict | None:
    if qty <= 0:
        return None

    ranked = []
    for candidate in candidates or []:
        cap = int(candidate.get("max_units_per_box", 0) or 0)
        if cap <= 0:
            continue

        boxes_needed = int(math.ceil(qty / cap))
        first_box_qty = min(qty, cap)
        trim_info = _calc_trim_info(
            inner_size_cm=candidate["inner_size_cm"],
            outer_size_cm=candidate["outer_size_cm"],
            orientation=candidate["best_orientation"],
            item_qty=first_box_qty,
            keep_height_cm=keep_height_cm,
        )

        ranked.append(
            (
                (
                    boxes_needed,
                    candidate["inner_volume_cm3"],
                    -trim_info["trim_cut_height_cm"],
                    -candidate["estimated_fill_ratio_first_box"],
                    candidate["box_priority"],
                ),
                candidate,
            )
        )

    if not ranked:
        return None

    ranked.sort(key=lambda item: item[0])
    return ranked[0][1]


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


def _try_place_units_on_shelves(
    units: List[dict],
    floor_l_cm: float,
    floor_w_cm: float,
) -> List[int]:
    shelves: List[dict] = []
    placed_indices: List[int] = []
    used_width_cm = 0.0

    for unit in units:
        placed = False
        orientations = []
        l = float(unit["length_cm"])
        w = float(unit["width_cm"])
        orientations.append((l, w))
        if abs(l - w) > 1e-9:
            orientations.append((w, l))

        best_existing: tuple[int, float, float] | None = None
        for shelf_idx, shelf in enumerate(shelves):
            shelf_height_cm = float(shelf["height_cm"])
            remaining_l_cm = float(shelf["remaining_l_cm"])
            for orient_l_cm, orient_w_cm in orientations:
                if orient_l_cm <= remaining_l_cm + 1e-9 and orient_w_cm <= shelf_height_cm + 1e-9:
                    waste = remaining_l_cm - orient_l_cm
                    candidate = (shelf_idx, waste, orient_l_cm, orient_w_cm)
                    if best_existing is None or candidate[1] < best_existing[1]:
                        best_existing = candidate

        if best_existing is not None:
            shelf = shelves[best_existing[0]]
            shelf["remaining_l_cm"] -= best_existing[2]
            placed_indices.append(int(unit["idx"]))
            continue

        best_new: tuple[float, float] | None = None
        for orient_l_cm, orient_w_cm in orientations:
            if orient_l_cm <= floor_l_cm + 1e-9 and used_width_cm + orient_w_cm <= floor_w_cm + 1e-9:
                candidate = (orient_w_cm, orient_l_cm)
                if best_new is None or candidate[0] < best_new[0]:
                    best_new = candidate

        if best_new is None:
            continue

        shelves.append(
            {
                "height_cm": best_new[0],
                "remaining_l_cm": max(0.0, floor_l_cm - best_new[1]),
            }
        )
        used_width_cm += best_new[0]
        placed_indices.append(int(unit["idx"]))

    return placed_indices


def _pack_layer_units(
    units: List[dict],
    floor_l_cm: float,
    floor_w_cm: float,
) -> List[int]:
    row_first = _try_place_units_on_shelves(units, floor_l_cm, floor_w_cm)

    swapped_units = [
        {
            **unit,
            "length_cm": float(unit["width_cm"]),
            "width_cm": float(unit["length_cm"]),
        }
        for unit in units
    ]
    col_first = _try_place_units_on_shelves(swapped_units, floor_w_cm, floor_l_cm)

    if len(col_first) > len(row_first):
        return col_first
    return row_first


def _estimate_shared_mixed_height(
    items: List[dict],
    candidate_box: dict,
) -> float | None:
    floor_l_cm = float(candidate_box["inner_size_cm"][0])
    floor_w_cm = float(candidate_box["inner_size_cm"][1])
    inner_h_cm = float(candidate_box["inner_size_cm"][2])

    units: List[dict] = []
    idx = 0
    for item in items:
        qty = int(item.get("qty", 0) or 0)
        if qty <= 0:
            continue

        length_cm = float(item.get("length_cm", 0) or 0)
        width_cm = float(item.get("width_cm", 0) or 0)
        height_cm = float(item.get("height_cm", 0) or 0)
        if length_cm <= 0 or width_cm <= 0 or height_cm <= 0:
            return None

        for _ in range(qty):
            units.append(
                {
                    "idx": idx,
                    "length_cm": length_cm,
                    "width_cm": width_cm,
                    "height_cm": height_cm,
                    "volume_cm3": length_cm * width_cm * height_cm,
                }
            )
            idx += 1

    if not units:
        return 0.0

    used_height_cm = 0.0
    remaining_units = units

    while remaining_units:
        height_candidates = sorted({round(float(unit["height_cm"]), 3) for unit in remaining_units})
        best_layer: tuple[float, List[int], float] | None = None

        for layer_height_cm in height_candidates:
            eligible_units = [
                unit
                for unit in remaining_units
                if float(unit["height_cm"]) <= layer_height_cm + 1e-9
            ]
            eligible_units = sorted(
                eligible_units,
                key=lambda unit: (
                    max(float(unit["length_cm"]), float(unit["width_cm"])),
                    float(unit["length_cm"]) * float(unit["width_cm"]),
                    float(unit["height_cm"]),
                ),
                reverse=True,
            )
            placed_indices = _pack_layer_units(eligible_units, floor_l_cm, floor_w_cm)
            if not placed_indices:
                continue

            placed_set = set(placed_indices)
            placed_volume_cm3 = sum(
                float(unit["volume_cm3"])
                for unit in eligible_units
                if int(unit["idx"]) in placed_set
            )
            score = placed_volume_cm3 / max(layer_height_cm, 1e-9)
            candidate = (layer_height_cm, placed_indices, score)
            if best_layer is None or candidate[2] > best_layer[2]:
                best_layer = candidate

        if best_layer is None:
            return None

        used_height_cm += float(best_layer[0])
        if used_height_cm > inner_h_cm + 1e-9:
            return used_height_cm

        placed_set = set(best_layer[1])
        remaining_units = [
            unit for unit in remaining_units
            if int(unit["idx"]) not in placed_set
        ]

    return used_height_cm


def _calc_single_mixed_repack_fit(
    items: List[dict],
    candidate_box: dict,
    rules: dict | None,
) -> dict:
    rules = rules or {}
    effective_weight_capacity = float(candidate_box["max_box_weight_kg"]) - float(candidate_box["box_weight_kg"])
    total_item_weight = sum(int(item.get("qty", 0) or 0) * float(item.get("unit_weight_kg", 0) or 0) for item in items)
    if total_item_weight > effective_weight_capacity + 1e-9:
        return {
            "can_fit": False,
            "total_item_weight": round(total_item_weight, 3),
        }

    candidate_box_code = _norm_text(candidate_box.get("box_code", "")).upper()
    for item in items:
        box_cap_override = _resolve_box_cap_override(item, candidate_box_code)
        if box_cap_override is not None and int(item.get("qty", 0) or 0) > box_cap_override:
            return {
                "can_fit": False,
                "total_item_weight": round(total_item_weight, 3),
            }

    layer_tol_cm = float(rules.get("REPACK_MIX_LAYER_TOL_CM", 1.0) or 1.0)
    keep_height_cm = _resolve_trim_keep_height_cm(rules)
    min_remaining_height_for_trim_cm = _resolve_trim_min_remaining_height_cm(rules)
    inner_height_cm = float(candidate_box["inner_size_cm"][2])

    additive_used_height_cm = 0.0
    tallest_group_height_cm = 0.0
    max_group_volume_cm3 = 0.0
    box_info = {
        "내경가로(cm)": candidate_box["inner_size_cm"][0],
        "내경세로(cm)": candidate_box["inner_size_cm"][1],
        "내경높이(cm)": candidate_box["inner_size_cm"][2],
        "외경가로(cm)": candidate_box["outer_size_cm"][0],
        "외경세로(cm)": candidate_box["outer_size_cm"][1],
        "외경높이(cm)": candidate_box["outer_size_cm"][2],
        "박스중량(kg)": candidate_box["box_weight_kg"],
        "최대허용중량(kg)": candidate_box["max_box_weight_kg"],
    }

    grouped: Dict[Tuple[Any, ...], dict] = {}
    for item in items:
        qty = int(item.get("qty", 0) or 0)
        if qty <= 0:
            continue

        key = (
            round(float(item.get("length_cm", 0) or 0), 3),
            round(float(item.get("width_cm", 0) or 0), 3),
            round(float(item.get("height_cm", 0) or 0), 3),
            _norm_text(item.get("calc_unit_type", "item")),
            int(item.get("package_pack_qty", 1) or 1),
        )
        if key not in grouped:
            grouped[key] = {
                "qty": 0,
                "item": {
                    "product_name": item.get("product_name", ""),
                    "length_cm": item.get("length_cm", 0),
                    "width_cm": item.get("width_cm", 0),
                    "height_cm": item.get("height_cm", 0),
                    "unit_weight_kg": item.get("unit_weight_kg", 0),
                },
            }
        grouped[key]["qty"] += qty

    for grouped_item in grouped.values():
        fit_info = _calc_best_orientation_fit(box_info, grouped_item["item"], grouped_item["qty"], rules)
        max_units = int(fit_info.get("max_units_per_box", 0) or 0)
        if max_units <= 0 or int(grouped_item["qty"]) > max_units:
            return {
                "can_fit": False,
                "total_item_weight": round(total_item_weight, 3),
            }

        layer_variants = ((fit_info.get("fit_result") or {}).get("layer_variants") or [])
        min_height_cm = _calc_min_layer_height_for_qty(layer_variants, int(grouped_item["qty"]))
        if min_height_cm is None:
            return {
                "can_fit": False,
                "total_item_weight": round(total_item_weight, 3),
            }
        additive_used_height_cm += float(min_height_cm)
        tallest_group_height_cm = max(tallest_group_height_cm, float(min_height_cm))
        max_group_volume_cm3 = max(
            max_group_volume_cm3,
            float(grouped_item["item"].get("length_cm", 0) or 0)
            * float(grouped_item["item"].get("width_cm", 0) or 0)
            * float(grouped_item["item"].get("height_cm", 0) or 0)
            * int(grouped_item["qty"]),
        )

    used_height_cm = additive_used_height_cm
    all_item_mix = bool(items) and all(
        _norm_text(item.get("calc_unit_type", "item")) == "item"
        for item in items
    )
    max_item_height_cm = max(
        (float(item.get("height_cm", 0) or 0) for item in items),
        default=0.0,
    )

    if any(bool(item.get("mixed_unpacked", False)) for item in items):
        floor_area_cm2 = float(candidate_box["inner_size_cm"][0]) * float(candidate_box["inner_size_cm"][1])
        total_volume_cm3 = sum(
            int(item.get("qty", 0) or 0)
            * float(item.get("length_cm", 0) or 0)
            * float(item.get("width_cm", 0) or 0)
            * float(item.get("height_cm", 0) or 0)
            for item in items
        )
        shared_efficiency = float(rules.get("REPACK_MIX_UNPACK_SHARED_EFFICIENCY", 0.70) or 0.70)
        if all_item_mix and max_item_height_cm <= float(rules.get("REPACK_MIX_FLAT_ITEM_HEIGHT_CM", 4.0) or 4.0):
            shared_efficiency = float(rules.get("REPACK_MIX_FLAT_ITEM_SHARED_EFFICIENCY", 0.86) or 0.86)
        if floor_area_cm2 > 0 and shared_efficiency > 0:
            shared_used_height_cm = total_volume_cm3 / (floor_area_cm2 * shared_efficiency)
            max_group_volume_height_cm = max_group_volume_cm3 / (floor_area_cm2 * shared_efficiency)
            used_height_cm = max(max_group_volume_height_cm, shared_used_height_cm)
    elif all_item_mix:
        floor_area_cm2 = float(candidate_box["inner_size_cm"][0]) * float(candidate_box["inner_size_cm"][1])
        total_volume_cm3 = sum(
            int(item.get("qty", 0) or 0)
            * float(item.get("length_cm", 0) or 0)
            * float(item.get("width_cm", 0) or 0)
            * float(item.get("height_cm", 0) or 0)
            for item in items
        )
        shared_efficiency = float(rules.get("REPACK_MIX_ITEM_SHARED_EFFICIENCY", 0.80) or 0.80)
        if max_item_height_cm <= float(rules.get("REPACK_MIX_FLAT_ITEM_HEIGHT_CM", 4.0) or 4.0):
            shared_efficiency = float(rules.get("REPACK_MIX_FLAT_ITEM_SHARED_EFFICIENCY", 0.86) or 0.86)
        if floor_area_cm2 > 0 and shared_efficiency > 0:
            shared_used_height_cm = total_volume_cm3 / (floor_area_cm2 * shared_efficiency)
            max_group_volume_height_cm = max_group_volume_cm3 / (floor_area_cm2 * shared_efficiency)
            used_height_cm = max(max_group_volume_height_cm, shared_used_height_cm)

    can_fit = used_height_cm <= (inner_height_cm + layer_tol_cm)
    remaining_height_cm = max(0.0, inner_height_cm - used_height_cm)
    ignore_trim_for_small_box = (
        abs(float(candidate_box["outer_size_cm"][0]) - 22.0) <= 0.01
        and abs(float(candidate_box["outer_size_cm"][1]) - 19.0) <= 0.01
        and abs(float(candidate_box["outer_size_cm"][2]) - 9.0) <= 0.01
    ) or (
        abs(float(candidate_box["outer_size_cm"][0]) - 27.0) <= 0.01
        and abs(float(candidate_box["outer_size_cm"][1]) - 18.0) <= 0.01
        and abs(float(candidate_box["outer_size_cm"][2]) - 15.0) <= 0.01
    )
    trim_cut_height_cm = 0.0
    if can_fit and (not ignore_trim_for_small_box) and remaining_height_cm >= min_remaining_height_for_trim_cm:
        trim_cut_height_cm = max(0.0, remaining_height_cm - keep_height_cm)

    trimmed_inner_height_cm = max(0.0, inner_height_cm - trim_cut_height_cm)
    trimmed_outer_height_cm = max(0.0, float(candidate_box["outer_size_cm"][2]) - trim_cut_height_cm)

    return {
        "can_fit": can_fit,
        "total_item_weight": round(total_item_weight, 3),
        "used_height_cm": _round_cm(used_height_cm),
        "remaining_height_cm": _round_cm(remaining_height_cm),
        "trim_cut_height_cm": _round_cm(trim_cut_height_cm),
        "trimmed_inner_height_cm": _round_cm(trimmed_inner_height_cm),
        "trimmed_outer_height_cm": _round_cm(trimmed_outer_height_cm),
    }


def _try_allocate_single_mixed_repack_box(
    box_eval_result: Dict[str, List[dict]],
    rules: dict | None = None,
) -> List[dict]:
    rows = box_eval_result.get("box_candidates", []) or []
    if len(rows) <= 1:
        return []

    def _build_item_rows(use_unpacked_mix: bool = False) -> List[dict]:
        built_rows: List[dict] = []
        for row in rows:
            calc_unit_type = _norm_text(row.get("calc_unit_type", "item"))
            use_fallback_item = (
                use_unpacked_mix
                and calc_unit_type == "package"
                and bool(row.get("mixed_unpack_allowed", False))
                and float(row.get("fallback_item_length_cm", 0) or 0) > 0
                and float(row.get("fallback_item_width_cm", 0) or 0) > 0
                and float(row.get("fallback_item_height_cm", 0) or 0) > 0
                and float(row.get("fallback_item_unit_weight_kg", 0) or 0) > 0
            )

            if use_fallback_item:
                built_rows.append(
                    {
                        "product_name": row["product_name"],
                        "qty": int(row.get("original_qty", row["qty"])),
                        "original_qty": int(row.get("original_qty", row["qty"])),
                        "package_pack_qty": 1,
                        "calc_unit_type": "item",
                        "length_cm": float(row.get("fallback_item_length_cm", 0) or 0),
                        "width_cm": float(row.get("fallback_item_width_cm", 0) or 0),
                        "height_cm": float(row.get("fallback_item_height_cm", 0) or 0),
                        "unit_weight_kg": float(row.get("fallback_item_unit_weight_kg", 0) or 0),
                        "packing_policy_code": row.get("packing_policy_code", ""),
                        "mixed_unpacked": True,
                        "note": "패키지 해체 후 혼합 재포장",
                    }
                )
                continue

            built_rows.append(
                {
                    "product_name": row["product_name"],
                    "qty": int(row["qty"]),
                    "original_qty": int(row.get("original_qty", row["qty"])),
                    "package_pack_qty": int(row.get("package_pack_qty", 1)),
                    "calc_unit_type": calc_unit_type,
                    "length_cm": float(row.get("length_cm", 0) or 0),
                    "width_cm": float(row.get("width_cm", 0) or 0),
                    "height_cm": float(row.get("height_cm", 0) or 0),
                    "unit_weight_kg": float(row.get("unit_weight_kg", 0) or 0),
                    "packing_policy_code": row.get("packing_policy_code", ""),
                    "mixed_unpacked": False,
                    "note": str(row.get("candidate_note", "") or "").strip(),
                }
            )
        return built_rows

    common_box_map: Dict[str, dict] | None = None

    for row in rows:
        all_candidates = row.get("all_box_candidates", []) or []
        current_map = {
            str(candidate.get("box_code", "") or "").strip(): candidate
            for candidate in all_candidates
            if str(candidate.get("box_code", "") or "").strip()
        }
        if not current_map:
            return []

        if common_box_map is None:
            common_box_map = current_map
        else:
            common_box_map = {
                box_code: common_box_map[box_code]
                for box_code in list(common_box_map.keys())
                if box_code in current_map
            }
            if not common_box_map:
                return []

    ranked_candidates = sorted(
        common_box_map.values(),
        key=lambda candidate: (
            candidate["inner_volume_cm3"],
            -candidate.get("first_box_trim_info", {}).get("trim_cut_height_cm", 0.0),
            candidate["box_priority"],
        ),
    )

    for use_unpacked_mix in (False, True):
        item_rows = _build_item_rows(use_unpacked_mix=use_unpacked_mix)

        for candidate_box in ranked_candidates:
            fit_summary = _calc_single_mixed_repack_fit(item_rows, candidate_box, rules)
            if not fit_summary.get("can_fit", False):
                continue

            total_qty = 0
            total_weight = float(candidate_box["box_weight_kg"])
            for item in item_rows:
                calc_unit_type = _norm_text(item.get("calc_unit_type", "item"))
                package_pack_qty = int(item.get("package_pack_qty", 1) or 1)
                qty = int(item.get("qty", 0) or 0)
                total_weight += qty * float(item.get("unit_weight_kg", 0) or 0)
                if calc_unit_type == "package":
                    total_qty += qty * package_pack_qty
                else:
                    total_qty += qty

            return [
                {
                    "box_no": 1,
                    "box_code": candidate_box["box_code"],
                    "box_name": candidate_box["box_name"],
                    "outer_size_cm": candidate_box["outer_size_cm"],
                    "inner_size_cm": candidate_box["inner_size_cm"],
                    "gross_weight_est": round(total_weight, 3),
                    "each_qty": total_qty,
                    "used_height_cm": fit_summary.get("used_height_cm"),
                    "remaining_height_cm": fit_summary.get("remaining_height_cm"),
                    "trim_cut_height_cm": fit_summary.get("trim_cut_height_cm"),
                    "trimmed_inner_height_cm": fit_summary.get("trimmed_inner_height_cm"),
                    "trimmed_outer_height_cm": fit_summary.get("trimmed_outer_height_cm"),
                    "display_trimmed_height": True,
                    "note": "혼합 재포장",
                    "items": item_rows,
                }
            ]

    return []


def _build_mixed_item_row_from_candidate(row: dict, use_unpacked_mix: bool = False, qty_override: int | None = None) -> dict | None:
    calc_unit_type = _norm_text(row.get("calc_unit_type", "item"))
    package_pack_qty = int(row.get("package_pack_qty", 1) or 1)
    use_fallback_item = (
        use_unpacked_mix
        and calc_unit_type == "package"
        and bool(row.get("mixed_unpack_allowed", False))
        and float(row.get("fallback_item_length_cm", 0) or 0) > 0
        and float(row.get("fallback_item_width_cm", 0) or 0) > 0
        and float(row.get("fallback_item_height_cm", 0) or 0) > 0
        and float(row.get("fallback_item_unit_weight_kg", 0) or 0) > 0
    )

    qty = int(qty_override if qty_override is not None else row.get("qty", 0) or 0)
    if qty <= 0:
        return None

    if use_fallback_item:
        return {
            "product_name": row["product_name"],
            "qty": qty * package_pack_qty,
            "original_qty": int(row.get("original_qty", row["qty"])),
            "package_pack_qty": 1,
            "calc_unit_type": "item",
            "length_cm": float(row.get("fallback_item_length_cm", 0) or 0),
            "width_cm": float(row.get("fallback_item_width_cm", 0) or 0),
            "height_cm": float(row.get("fallback_item_height_cm", 0) or 0),
            "unit_weight_kg": float(row.get("fallback_item_unit_weight_kg", 0) or 0),
            "packing_policy_code": row.get("packing_policy_code", ""),
            "mixed_unpacked": True,
            "note": "패키지 해체 후 혼합 재포장",
        }

    return {
        "product_name": row["product_name"],
        "qty": qty,
        "original_qty": int(row.get("original_qty", row["qty"])),
        "package_pack_qty": package_pack_qty,
        "calc_unit_type": calc_unit_type,
        "length_cm": float(row.get("length_cm", 0) or 0),
        "width_cm": float(row.get("width_cm", 0) or 0),
        "height_cm": float(row.get("height_cm", 0) or 0),
        "unit_weight_kg": float(row.get("unit_weight_kg", 0) or 0),
        "packing_policy_code": row.get("packing_policy_code", ""),
        "mixed_unpacked": False,
        "note": str(row.get("candidate_note", "") or "").strip(),
    }


def _pool_item_display_qty(item: dict) -> int:
    calc_unit_type = _norm_text(item.get("calc_unit_type", "item"))
    qty = int(item.get("qty", 0) or 0)
    if calc_unit_type == "package":
        return qty * int(item.get("package_pack_qty", 1) or 1)
    return qty


def _resolve_policy_preferred_box(item: dict | None) -> Tuple[str, int] | None:
    if not isinstance(item, dict):
        return None

    policy_code = _norm_text(item.get("packing_policy_code", "")).upper()
    box_caps = BOX_CAP_POLICY_MAP.get(policy_code)
    if not box_caps:
        return None

    preferred_box_code, preferred_cap = max(
        box_caps.items(),
        key=lambda pair: (int(pair[1]), _norm_text(pair[0])),
    )
    return _norm_text(preferred_box_code).upper(), int(preferred_cap)


def _pool_item_volume_key(row: dict, use_unpacked_mix: bool = False) -> float:
    item_row = _build_mixed_item_row_from_candidate(row, use_unpacked_mix=use_unpacked_mix, qty_override=1)
    if not item_row:
        return 0.0
    return (
        float(item_row.get("length_cm", 0) or 0)
        * float(item_row.get("width_cm", 0) or 0)
        * float(item_row.get("height_cm", 0) or 0)
    )


def _max_addable_qty_for_box(
    current_items: List[dict],
    row: dict,
    max_qty: int,
    candidate_box: dict,
    rules: dict | None,
    use_unpacked_mix: bool,
) -> int:
    low = 0
    high = max(0, int(max_qty))
    best = 0

    while low <= high:
        mid = (low + high) // 2
        if mid <= 0:
            low = 1
            continue

        candidate_item = _build_mixed_item_row_from_candidate(
            row,
            use_unpacked_mix=use_unpacked_mix,
            qty_override=mid,
        )
        if not candidate_item:
            break

        fit_summary = _calc_single_mixed_repack_fit(current_items + [candidate_item], candidate_box, rules)
        if fit_summary.get("can_fit", False):
            best = mid
            low = mid + 1
        else:
            high = mid - 1

    return best


def _fill_single_pooled_mixed_box(
    remaining_rows: List[dict],
    candidate_box: dict,
    rules: dict | None,
    use_unpacked_mix: bool,
) -> dict | None:
    if not remaining_rows:
        return None

    selected_items: List[dict] = []
    consumed_by_index: Dict[int, int] = {}

    def _ordered_rows(desc: bool) -> List[Tuple[int, dict]]:
        indexed = list(enumerate(remaining_rows))
        indexed = [pair for pair in indexed if int(pair[1].get("remaining_qty", 0) or 0) > 0]
        return sorted(
            indexed,
            key=lambda pair: (
                _pool_item_volume_key(pair[1], use_unpacked_mix=use_unpacked_mix),
                int(pair[1].get("remaining_qty", 0) or 0),
                pair[0],
            ),
            reverse=desc,
        )

    for descending in (True, False):
        for row_idx, row in _ordered_rows(descending):
            remaining_qty = int(row.get("remaining_qty", 0) or 0)
            if remaining_qty <= 0:
                continue

            add_qty = _max_addable_qty_for_box(
                current_items=selected_items,
                row=row,
                max_qty=remaining_qty,
                candidate_box=candidate_box,
                rules=rules,
                use_unpacked_mix=use_unpacked_mix,
            )
            if add_qty <= 0:
                continue

            selected_item = _build_mixed_item_row_from_candidate(
                row,
                use_unpacked_mix=use_unpacked_mix,
                qty_override=add_qty,
            )
            if not selected_item:
                continue

            selected_items.append(selected_item)
            consumed_by_index[row_idx] = consumed_by_index.get(row_idx, 0) + add_qty
            row["remaining_qty"] = remaining_qty - add_qty

    if not selected_items:
        return None

    fit_summary = _calc_single_mixed_repack_fit(selected_items, candidate_box, rules)
    if not fit_summary.get("can_fit", False):
        return None

    total_qty = sum(_pool_item_display_qty(item) for item in selected_items)
    total_weight = float(candidate_box["box_weight_kg"]) + sum(
        int(item.get("qty", 0) or 0) * float(item.get("unit_weight_kg", 0) or 0)
        for item in selected_items
    )

    return {
        "candidate_box": candidate_box,
        "items": selected_items,
        "consumed_by_index": consumed_by_index,
        "fit_summary": fit_summary,
        "each_qty": total_qty,
        "gross_weight_est": round(total_weight, 3),
    }


def _allocate_policy_priority_boxes(
    box_eval_result: Dict[str, List[dict]],
    rules: dict | None = None,
) -> Tuple[List[dict], Dict[str, List[dict]]]:
    rows = [{**row, "qty": int(row.get("qty", 0) or 0)} for row in (box_eval_result.get("box_candidates", []) or [])]
    allocated_boxes: List[dict] = []
    box_no = 1

    policy_groups: Dict[Tuple[str, str, int], dict] = {}
    for idx, row in enumerate(rows):
        policy_preferred = _resolve_policy_preferred_box(row)
        if not policy_preferred:
            continue
        if _norm_text(row.get("calc_unit_type", "item")) != "item":
            continue

        preferred_box_code, preferred_cap = policy_preferred
        if preferred_cap <= 0:
            continue

        preferred_candidate = next(
            (
                candidate
                for candidate in (row.get("all_box_candidates", []) or [])
                if _norm_text(candidate.get("box_code", "")).upper() == preferred_box_code
            ),
            None,
        )
        if preferred_candidate is None:
            continue

        policy_code = _norm_text(row.get("packing_policy_code", "")).upper()
        group_key = (policy_code, preferred_box_code, preferred_cap)
        group = policy_groups.setdefault(
            group_key,
            {
                "preferred_candidate": preferred_candidate,
                "rows": [],
            },
        )
        group["rows"].append((idx, row))

    for (_, _, preferred_cap), group in policy_groups.items():
        preferred_candidate = group["preferred_candidate"]
        group_rows: List[Tuple[int, dict]] = group["rows"]

        while sum(int(row.get("qty", 0) or 0) for _, row in group_rows) >= preferred_cap:
            box_items: List[dict] = []
            remaining_capacity = preferred_cap

            for _, row in sorted(
                group_rows,
                key=lambda pair: (int(pair[1].get("qty", 0) or 0), -pair[0]),
                reverse=True,
            ):
                row_qty = int(row.get("qty", 0) or 0)
                if row_qty <= 0 or remaining_capacity <= 0:
                    continue

                take_qty = min(row_qty, remaining_capacity)
                item_row = _build_mixed_item_row_from_candidate(
                    row,
                    use_unpacked_mix=False,
                    qty_override=take_qty,
                )
                if not item_row:
                    continue

                box_items.append(item_row)
                row["qty"] = row_qty - take_qty
                remaining_capacity -= take_qty

            if remaining_capacity > 0 or not box_items:
                for item in box_items:
                    for _, row in group_rows:
                        if row["product_name"] == item["product_name"]:
                            row["qty"] = int(row.get("qty", 0) or 0) + int(item.get("qty", 0) or 0)
                            break
                break

            fit_summary = _calc_single_mixed_repack_fit(box_items, preferred_candidate, rules)
            if not fit_summary.get("can_fit", False):
                inner_size_cm = tuple(preferred_candidate.get("inner_size_cm", (0.0, 0.0, 0.0)))
                outer_size_cm = tuple(preferred_candidate.get("outer_size_cm", (0.0, 0.0, 0.0)))
                fit_summary = {
                    "can_fit": True,
                    "used_height_cm": inner_size_cm[2] if len(inner_size_cm) >= 3 else 0.0,
                    "remaining_height_cm": 0.0,
                    "trim_cut_height_cm": 0.0,
                    "trimmed_inner_height_cm": inner_size_cm[2] if len(inner_size_cm) >= 3 else 0.0,
                    "trimmed_outer_height_cm": outer_size_cm[2] if len(outer_size_cm) >= 3 else 0.0,
                }

            allocated_boxes.append(
                {
                    "box_no": box_no,
                    "box_code": preferred_candidate["box_code"],
                    "box_name": preferred_candidate["box_name"],
                    "outer_size_cm": preferred_candidate["outer_size_cm"],
                    "inner_size_cm": preferred_candidate["inner_size_cm"],
                    "gross_weight_est": round(
                        float(preferred_candidate["box_weight_kg"])
                        + sum(
                            int(item.get("qty", 0) or 0) * float(item.get("unit_weight_kg", 0) or 0)
                            for item in box_items
                        ),
                        3,
                    ),
                    "each_qty": sum(_pool_item_display_qty(item) for item in box_items),
                    "used_height_cm": fit_summary.get("used_height_cm"),
                    "remaining_height_cm": fit_summary.get("remaining_height_cm"),
                    "trim_cut_height_cm": fit_summary.get("trim_cut_height_cm"),
                    "trimmed_inner_height_cm": fit_summary.get("trimmed_inner_height_cm"),
                    "trimmed_outer_height_cm": fit_summary.get("trimmed_outer_height_cm"),
                    "display_trimmed_height": False,
                    "note": "정책 우선 포장",
                    "items": box_items,
                }
            )
            box_no += 1

    remaining_rows: List[dict] = []
    keep_height_cm = _resolve_trim_keep_height_cm(rules)
    for row in rows:
        if int(row.get("qty", 0) or 0) > 0:
            if _resolve_policy_preferred_box(row):
                row["packing_policy_code"] = ""
                row["is_bulk_case"] = False
                reselection = _select_repack_candidate_for_qty(
                    row.get("all_box_candidates", []) or [],
                    int(row.get("qty", 0) or 0),
                    keep_height_cm=keep_height_cm,
                )
                if reselection is not None:
                    reselection = reselection.copy()
                    reselection["selection_policy"] = "DEFAULT_SMALLEST_BOX_FIRST"
                    row["recommended_box"] = reselection
            remaining_rows.append(row)

    reduced_result = {
        **box_eval_result,
        "box_candidates": remaining_rows,
    }
    return allocated_boxes, reduced_result


def _try_allocate_pooled_mixed_repack_boxes(
    box_eval_result: Dict[str, List[dict]],
    rules: dict | None = None,
) -> List[dict]:
    rows = box_eval_result.get("box_candidates", []) or []
    if len(rows) <= 1:
        return []

    common_box_map: Dict[str, dict] | None = None
    for row in rows:
        all_candidates = row.get("all_box_candidates", []) or []
        current_map = {
            str(candidate.get("box_code", "") or "").strip(): candidate
            for candidate in all_candidates
            if str(candidate.get("box_code", "") or "").strip()
        }
        if not current_map:
            return []

        if common_box_map is None:
            common_box_map = current_map
        else:
            common_box_map = {
                box_code: common_box_map[box_code]
                for box_code in list(common_box_map.keys())
                if box_code in current_map
            }
            if not common_box_map:
                return []

    ranked_candidates = sorted(
        common_box_map.values(),
        key=lambda candidate: (
            -candidate["inner_volume_cm3"],
            candidate["box_priority"],
        ),
    )

    best_boxes: List[dict] = []

    for use_unpacked_mix in (False, True):
        remaining_rows = []
        for row in rows:
            remaining_rows.append({**row, "remaining_qty": int(row.get("qty", 0) or 0)})

        pooled_boxes: List[dict] = []
        box_no = 1
        guard = 0

        while any(int(row.get("remaining_qty", 0) or 0) > 0 for row in remaining_rows):
            guard += 1
            if guard > 1000:
                pooled_boxes = []
                break

            best_fill = None
            for candidate_box in ranked_candidates:
                trial_rows = [{**row} for row in remaining_rows]
                fill = _fill_single_pooled_mixed_box(
                    remaining_rows=trial_rows,
                    candidate_box=candidate_box,
                    rules=rules,
                    use_unpacked_mix=use_unpacked_mix,
                )
                if not fill:
                    continue

                score = (
                    int(fill["each_qty"]),
                    sum(fill["consumed_by_index"].values()),
                    candidate_box["inner_volume_cm3"],
                )
                if best_fill is None or score > best_fill["score"]:
                    best_fill = {
                        "score": score,
                        "fill": fill,
                    }

            if not best_fill:
                pooled_boxes = []
                break

            fill = best_fill["fill"]
            for row_idx, consumed_qty in fill["consumed_by_index"].items():
                remaining_rows[row_idx]["remaining_qty"] = max(
                    0,
                    int(remaining_rows[row_idx].get("remaining_qty", 0) or 0) - int(consumed_qty),
                )

            pooled_boxes.append(
                {
                    "box_no": box_no,
                    "box_code": fill["candidate_box"]["box_code"],
                    "box_name": fill["candidate_box"]["box_name"],
                    "outer_size_cm": fill["candidate_box"]["outer_size_cm"],
                    "inner_size_cm": fill["candidate_box"]["inner_size_cm"],
                    "gross_weight_est": fill["gross_weight_est"],
                    "each_qty": fill["each_qty"],
                    "used_height_cm": fill["fit_summary"].get("used_height_cm"),
                    "remaining_height_cm": fill["fit_summary"].get("remaining_height_cm"),
                    "trim_cut_height_cm": fill["fit_summary"].get("trim_cut_height_cm"),
                    "trimmed_inner_height_cm": fill["fit_summary"].get("trimmed_inner_height_cm"),
                    "trimmed_outer_height_cm": fill["fit_summary"].get("trimmed_outer_height_cm"),
                    "display_trimmed_height": True,
                    "note": "혼합 재포장",
                    "items": fill["items"],
                }
            )
            box_no += 1

        if pooled_boxes and (not best_boxes or len(pooled_boxes) < len(best_boxes)):
            best_boxes = pooled_boxes

    return best_boxes


def build_repack_final_plan(
    box_eval_result: Dict[str, List[dict]],
    rules: dict | None = None,
) -> Dict[str, List[dict]]:
    preallocated_policy_boxes, working_box_eval_result = _allocate_policy_priority_boxes(box_eval_result, rules)

    mixed_repack_boxes = _try_allocate_single_mixed_repack_box(working_box_eval_result, rules)
    if mixed_repack_boxes:
        combined_boxes = preallocated_policy_boxes + mixed_repack_boxes
        for idx, box in enumerate(combined_boxes, start=1):
            box["box_no"] = idx
        return {
            "mixed_repack_boxes": combined_boxes,
            "final_plans": [],
            "no_box_fit": working_box_eval_result.get("no_box_fit", []),
            "unresolved": working_box_eval_result.get("unresolved", []),
            "invalid_specs": working_box_eval_result.get("invalid_specs", []),
        }

    pooled_mixed_repack_boxes = _try_allocate_pooled_mixed_repack_boxes(working_box_eval_result, rules)
    if pooled_mixed_repack_boxes:
        combined_boxes = preallocated_policy_boxes + pooled_mixed_repack_boxes
        for idx, box in enumerate(combined_boxes, start=1):
            box["box_no"] = idx
        return {
            "mixed_repack_boxes": combined_boxes,
            "final_plans": [],
            "no_box_fit": working_box_eval_result.get("no_box_fit", []),
            "unresolved": working_box_eval_result.get("unresolved", []),
            "invalid_specs": working_box_eval_result.get("invalid_specs", []),
        }

    final_plans = []
    keep_height_cm = _resolve_trim_keep_height_cm(rules)

    for row in working_box_eval_result.get("box_candidates", []):
        rec = row["recommended_box"]
        all_candidates = row.get("all_box_candidates", []) or []

        product_name = row["product_name"]
        qty = int(row["qty"])
        original_qty = int(row.get("original_qty", qty))
        package_pack_qty = int(row.get("package_pack_qty", 1))
        calc_unit_type = _norm_text(row.get("calc_unit_type", "item"))
        is_bulk_case = bool(row.get("is_bulk_case", False))
        candidate_note = str(row.get("candidate_note", "") or "").strip()

        per_box = int(rec["max_units_per_box"])

        if per_box <= 0:
            continue

        box_lines = []
        remaining_qty = qty
        box_no = 1
        primary_candidate = rec
        primary_cap = int(primary_candidate.get("max_units_per_box", 0) or 0)

        if is_bulk_case and primary_cap > 0 and remaining_qty >= primary_cap:
            full_primary_boxes = remaining_qty // primary_cap
            for _ in range(full_primary_boxes):
                box_lines.append(
                    _build_box_line_from_candidate(
                        primary_candidate,
                        primary_cap,
                        box_no,
                        keep_height_cm,
                        candidate_note,
                    )
                )
                remaining_qty -= primary_cap
                box_no += 1

        if is_bulk_case and primary_cap > 0 and remaining_qty > 0 and not box_lines:
            box_lines.append(
                _build_box_line_from_candidate(
                    primary_candidate,
                    remaining_qty,
                    box_no,
                    keep_height_cm,
                    candidate_note,
                )
            )
            remaining_qty = 0
            box_no += 1

        if remaining_qty > 0:
            remainder_candidate = _select_repack_candidate_for_qty(
                all_candidates or [rec],
                remaining_qty,
                keep_height_cm,
            ) or rec

            remainder_cap = int(remainder_candidate.get("max_units_per_box", 0) or 0)
            if remainder_cap > 0:
                while remaining_qty > 0:
                    item_qty = min(remaining_qty, remainder_cap)
                    box_lines.append(
                        _build_box_line_from_candidate(
                            remainder_candidate,
                            item_qty,
                            box_no,
                            keep_height_cm,
                            candidate_note,
                        )
                    )
                    remaining_qty -= item_qty
                    box_no += 1

        first_box = box_lines[0] if box_lines else None
        selected_box_code = first_box.get("box_code") if first_box else rec["box_code"]
        selected_box_name = first_box.get("box_name") if first_box else rec["box_name"]
        selected_outer_size_cm = first_box.get("outer_size_cm") if first_box else rec["outer_size_cm"]
        selected_inner_size_cm = first_box.get("inner_size_cm") if first_box else rec["inner_size_cm"]
        selected_orientation = first_box.get("best_orientation") if first_box else rec["best_orientation"]

        selection_policy = rec.get("selection_policy", "")
        box_codes_in_plan = {str(line.get("box_code", "") or "").strip() for line in box_lines}
        if len(box_codes_in_plan) > 1:
            selection_policy = "PRIMARY_BIG_BOX_THEN_OPTIMAL_REMAINDER"

        final_plans.append(
            {
                "product_name": product_name,
                "total_qty": qty,
                "original_qty": original_qty,
                "package_pack_qty": package_pack_qty,
                "calc_unit_type": calc_unit_type,
                "selected_box_code": selected_box_code,
                "selected_box_name": selected_box_name,
                "units_per_box": int(rec["max_units_per_box"]),
                "boxes_needed": len(box_lines),
                "inner_size_cm": selected_inner_size_cm,
                "outer_size_cm": selected_outer_size_cm,
                "best_orientation": selected_orientation,
                "spec_source": rec.get("spec_source", ""),
                "selection_policy": selection_policy,
                "note": candidate_note,
                "global_best_max_units_per_box": rec.get("global_best_max_units_per_box", rec["max_units_per_box"]),
                "global_best_orientation": rec.get("global_best_orientation"),
                "box_lines": box_lines,
            }
        )

    return {
        "mixed_repack_boxes": preallocated_policy_boxes,
        "final_plans": final_plans,
        "no_box_fit": working_box_eval_result.get("no_box_fit", []),
        "unresolved": working_box_eval_result.get("unresolved", []),
        "invalid_specs": working_box_eval_result.get("invalid_specs", []),
    }


def print_repack_candidates(repack_result: Dict[str, List[dict]]) -> None:
    print("\n" + "=" * 90)
    print("[REPACK INPUT]")

    print("\n[candidates]")
    for i, item in enumerate(repack_result.get("candidates", []), start=1):
        print(
            f"{i}. {item['product_name']} / "
            f"입력수량={item.get('original_qty', item['qty'])} / "
            f"계산수량={item['qty']} / "
            f"계산단위={item.get('calc_unit_type', 'item')} / "
            f"패키지입수량={item.get('package_pack_qty', 1)} / "
            f"spec={item['length_cm']}x{item['width_cm']}x{item['height_cm']} cm / "
            f"unit_weight={item['unit_weight_kg']} kg / "
            f"special_group={item['special_group']} / "
            f"package_product={item['package_product']} / "
            f"packing_policy={item['packing_policy_code']} / "
            f"spec_source={item.get('spec_source', '')} / "
            f"source_reason={item['source_reason']}"
        )

    print("\n[unresolved]")
    for i, item in enumerate(repack_result.get("unresolved", []), start=1):
        print(
            f"{i}. {item['product_name']} / {item['qty']} / "
            f"{item['reason']} / source_reason={item['source_reason']}"
        )

    print("\n[invalid_specs]")
    for i, item in enumerate(repack_result.get("invalid_specs", []), start=1):
        print(
            f"{i}. {item['product_name']} / {item['qty']} / "
            f"{item['reason']} / "
            f"spec={item['length_cm']}x{item['width_cm']}x{item['height_cm']} / "
            f"weight={item['unit_weight_kg']} / "
            f"spec_source={item.get('spec_source', '')} / "
            f"source_reason={item['source_reason']}"
        )

    print("=" * 90)


def print_repack_box_candidates(box_eval_result: Dict[str, List[dict]], top_n: int = 5) -> None:
    print("\n" + "=" * 90)
    print("[REPACK BOX CANDIDATES]")

    print("\n[recommended]")
    for i, row in enumerate(box_eval_result.get("box_candidates", []), start=1):
        rec = row["recommended_box"]
        print(
            f"{i}. {row['product_name']} / qty={row['qty']} / "
            f"추천박스={rec['box_name']} ({rec['box_code']}) / "
            f"최대적재가능={rec.get('global_best_max_units_per_box', rec['max_units_per_box'])} / "
            f"권장회전기준최대={rec['max_units_per_box']} / "
            f"필요박스수={rec['boxes_needed']} / "
            f"공간기준={rec['recommended_units_by_space']} / "
            f"중량기준={rec['units_by_weight']} / "
            f"내경={rec['inner_size_cm']} / "
            f"권장회전={rec['best_orientation']} / "
            f"최대적재회전={rec.get('global_best_orientation')} / "
            f"충전율={round(rec['estimated_fill_ratio_first_box'] * 100, 2)}% / "
            f"정책={rec.get('selection_policy', '')} / "
            f"spec_source={rec.get('spec_source', '')}"
        )

        for j, cand in enumerate(row["all_box_candidates"][:top_n], start=1):
            print(
                f"   {j}) {cand['box_name']} ({cand['box_code']}) / "
                f"최대적재가능={cand.get('global_best_max_units_per_box', cand['max_units_per_box'])} / "
                f"권장회전기준최대={cand['max_units_per_box']} / "
                f"필요박스수={cand['boxes_needed']} / "
                f"충전율={round(cand['estimated_fill_ratio_first_box'] * 100, 2)}%"
            )

    print("\n[no_box_fit]")
    for i, item in enumerate(box_eval_result.get("no_box_fit", []), start=1):
        print(
            f"{i}. {item['product_name']} / {item['qty']} / "
            f"{item['reason']} / source_reason={item['source_reason']}"
        )

    print("\n[unresolved]")
    for i, item in enumerate(box_eval_result.get("unresolved", []), start=1):
        print(f"{i}. {item['product_name']} / {item['qty']} / {item['reason']}")

    print("\n[invalid_specs]")
    for i, item in enumerate(box_eval_result.get("invalid_specs", []), start=1):
        print(f"{i}. {item['product_name']} / {item['qty']} / {item['reason']}")

    print("=" * 90)


def print_repack_final_plan(final_plan_result, packing_list_needed=False):
    print("\n" + "=" * 90)

    try:
        formatted_text = format_engine_result(
            final_plan_result,
            packing_list_needed=packing_list_needed,
        )
        print(formatted_text)

    except Exception as e:
        print("[formatter_error]")
        print(str(e))

        print("\n[raw_result_fallback]")
        print(final_plan_result)

    print("=" * 90)

    
