from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence

import pandas as pd


@dataclass
class SheetLoadResult:
    sheet_name: str
    header_row: int | None
    df: pd.DataFrame
    columns: List[str]
    loaded: bool = True


class MasterLoadError(Exception):
    pass


def _norm_text(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value)
    text = text.replace("\n", " ").replace("\r", " ")
    text = " ".join(text.split())
    return text.strip()


def _norm_col(value) -> str:
    return _norm_text(value).replace(" ", "")


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [_norm_col(c) for c in df.columns]
    return out


def _sheet_exists(excel_path: str | Path, sheet_name: str) -> bool:
    xls = pd.ExcelFile(excel_path, engine="openpyxl")
    return sheet_name in xls.sheet_names


def _detect_header_row(
    excel_path: str | Path,
    sheet_name: str,
    required_alias_groups: Sequence[Sequence[str]],
    scan_rows: int = 8,
) -> int:
    preview = pd.read_excel(
        excel_path,
        sheet_name=sheet_name,
        header=None,
        nrows=scan_rows,
        engine="openpyxl",
    )

    alias_groups = [set(_norm_col(x) for x in group) for group in required_alias_groups]
    best_row = None
    best_score = -1

    for row_idx in range(len(preview)):
        row_values = {_norm_col(v) for v in preview.iloc[row_idx].tolist()}
        score = sum(1 for group in alias_groups if row_values & group)
        if score > best_score:
            best_score = score
            best_row = row_idx
        if score == len(alias_groups):
            return row_idx

    raise MasterLoadError(
        f"[{sheet_name}] 헤더 행을 찾지 못했습니다. "
        f"required_alias_groups={required_alias_groups}, best_row={best_row}, best_score={best_score}"
    )


def _read_sheet(
    excel_path: str | Path,
    sheet_name: str,
    required_alias_groups: Sequence[Sequence[str]],
    required_exact_columns: Sequence[str],
    scan_rows: int = 8,
) -> SheetLoadResult:
    header_row = _detect_header_row(
        excel_path,
        sheet_name,
        required_alias_groups,
        scan_rows=scan_rows,
    )

    df = pd.read_excel(
        excel_path,
        sheet_name=sheet_name,
        header=header_row,
        engine="openpyxl",
    )
    df = _normalize_columns(df)

    actual_columns = list(df.columns)
    missing = [_norm_col(c) for c in required_exact_columns if _norm_col(c) not in actual_columns]

    if missing:
        raise MasterLoadError(
            f"[{sheet_name}] 필수 컬럼 누락: {missing}\n"
            f"header_row={header_row}, actual_columns={actual_columns}"
        )

    return SheetLoadResult(
        sheet_name=sheet_name,
        header_row=header_row,
        df=df,
        columns=actual_columns,
        loaded=True,
    )


def _make_empty_sheet_result(sheet_name: str, columns: Sequence[str]) -> SheetLoadResult:
    norm_cols = [_norm_col(c) for c in columns]
    return SheetLoadResult(
        sheet_name=sheet_name,
        header_row=None,
        df=pd.DataFrame(columns=norm_cols),
        columns=norm_cols,
        loaded=False,
    )


def _bool_yn(value) -> bool:
    return _norm_text(value).upper() in {"Y", "YES", "TRUE", "1"}


def _to_float(value, default: float = 0.0) -> float:
    if pd.isna(value) or value == "":
        return default
    try:
        return float(value)
    except Exception:
        return default


def build_rules_dict(rules_df: pd.DataFrame) -> Dict[str, object]:
    rules_df = _normalize_columns(rules_df)

    key_candidates = ["rule_key", "규칙코드", "key", "코드", "항목"]
    value_candidates = ["rule_value", "규칙값", "value", "값", "설정값"]
    type_candidates = ["rule_type", "타입", "유형"]

    key_col = next((c for c in key_candidates if _norm_col(c) in rules_df.columns), None)
    value_col = next((c for c in value_candidates if _norm_col(c) in rules_df.columns), None)
    type_col = next((c for c in type_candidates if _norm_col(c) in rules_df.columns), None)

    if key_col is None or value_col is None:
        raise MasterLoadError(
            f"[rules_master] 규칙 코드/값 컬럼을 찾지 못했습니다. actual_columns={list(rules_df.columns)}"
        )

    key_col = _norm_col(key_col)
    value_col = _norm_col(value_col)
    type_col = _norm_col(type_col) if type_col else None

    result: Dict[str, object] = {}
    for _, row in rules_df.iterrows():
        key = _norm_col(row.get(key_col, ""))
        raw_value = row.get(value_col)
        raw_type = _norm_text(row.get(type_col, "")) if type_col else ""

        if not key:
            continue

        value_text = _norm_text(raw_value)
        type_text = raw_type.lower()
        upper = value_text.upper()

        if type_text == "bool" or upper in {"Y", "N", "YES", "NO", "TRUE", "FALSE"}:
            result[key] = _bool_yn(value_text)
        elif type_text == "number":
            result[key] = float(value_text) if "." in value_text else int(float(value_text))
        else:
            try:
                if value_text != "" and "." in value_text:
                    result[key] = float(value_text)
                elif value_text != "":
                    result[key] = int(value_text)
                else:
                    result[key] = value_text
            except Exception:
                result[key] = value_text

    return result


PRODUCTS_REQUIRED = [
    "상품코드",
    "국문상품명",
    "가로(cm)",
    "세로(cm)",
    "높이(cm)",
    "개당중량(kg)",
    "특수상품군",
    "사용여부",
    "원본파일",
    "패키지상품여부",
    "특수우선박스코드",
    "특수우선입수량",
    "패킹정책코드",
    "패키지정책참조",
    "완박스입수량",
    "완박스박스코드",
    "완박스박스명",
    "혼합완박스허용여부",
    "완박스혼합그룹",
]

FULLBOXES_REQUIRED = [
    "상품코드",
    "국문상품명",
    "완박스입수량",
    "완박스박스코드",
    "완박스박스명",
    "혼합완박스허용여부",
    "완박스혼합그룹",
    "사용여부",
]

BOXES_REQUIRED = [
    "박스코드",
    "박스명",
    "외경가로(cm)",
    "외경세로(cm)",
    "외경높이(cm)",
    "내경가로(cm)",
    "내경세로(cm)",
    "내경높이(cm)",
    "박스중량(kg)",
    "최대허용중량(kg)",
    "active",
    "박스정렬우선순위",
]

RULES_REQUIRED = ["rule_key", "rule_value"]

PACKAGES_RECOMMENDED_COLUMNS = [
    "상품코드",
    "국문상품명",
    "패키지입수량",
    "패키지가로(cm)",
    "패키지세로(cm)",
    "패키지높이(cm)",
    "패키지중량(kg)",
    "패키지해체정책",
    "사용여부",
    "원본파일",
    "엔진기본처리정책",
    "패키지BOM존재여부",
    "해체허용조건",
    "해체후계산단위",
    "잔량처리방식",
    "데이터검증상태",
    "검증메모",
]


def _rename_packages_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = _normalize_columns(df)

    alias_map = {
        "상품코드": [
            "상품코드",
            "패키지상품코드",
            "package_product_code",
        ],
        "국문상품명": [
            "국문상품명",
            "패키지상품명",
            "package_product_name",
        ],
        "패키지입수량": [
            "패키지입수량",
            "package_qty",
            "package_pack_qty",
        ],
        "패키지가로(cm)": [
            "패키지가로(cm)",
            "package_length(cm)",
            "package_length",
        ],
        "패키지세로(cm)": [
            "패키지세로(cm)",
            "package_width(cm)",
            "package_width",
        ],
        "패키지높이(cm)": [
            "패키지높이(cm)",
            "package_height(cm)",
            "package_height",
        ],
        "패키지중량(kg)": [
            "패키지중량(kg)",
            "package_weight(kg)",
            "package_weight",
        ],
        "패키지해체정책": [
            "패키지해체정책",
            "unpack_policy",
            "package_unpack_policy",
        ],
        "사용여부": [
            "사용여부",
            "active",
            "use_yn",
            "is_active",
        ],
        "원본파일": [
            "원본파일",
            "source_file",
            "file_name",
        ],
        "엔진기본처리정책": [
            "엔진기본처리정책",
            "engine_default_policy",
            "engine_policy",
        ],
        "패키지BOM존재여부": [
            "패키지BOM존재여부",
            "package_bom_exist_yn",
            "bom_exist_yn",
        ],
        "해체허용조건": [
            "해체허용조건",
            "unpack_allow_condition",
        ],
        "해체후계산단위": [
            "해체후계산단위",
            "post_unpack_calc_unit",
        ],
        "잔량처리방식": [
            "잔량처리방식",
            "remainder_process_type",
        ],
        "데이터검증상태": [
            "데이터검증상태",
            "data_validation_status",
        ],
        "검증메모": [
            "검증메모",
            "validation_memo",
        ],
    }

    rename_map = {}
    current_cols = list(df.columns)

    for canonical, aliases in alias_map.items():
        canonical_norm = _norm_col(canonical)
        if canonical_norm in current_cols:
            continue

        for alias in aliases:
            alias_norm = _norm_col(alias)
            if alias_norm in current_cols:
                rename_map[alias_norm] = canonical_norm
                break

    if rename_map:
        df = df.rename(columns=rename_map)

    for col in [_norm_col(x) for x in PACKAGES_RECOMMENDED_COLUMNS]:
        if col not in df.columns:
            df[col] = ""

    ordered = [_norm_col(x) for x in PACKAGES_RECOMMENDED_COLUMNS]
    remain = [c for c in df.columns if c not in ordered]
    df = df[ordered + remain].copy()

    return df


def _read_packages_sheet(excel_path: str | Path) -> SheetLoadResult:
    sheet_name = "packages_master"

    if not _sheet_exists(excel_path, sheet_name):
        return _make_empty_sheet_result(sheet_name, PACKAGES_RECOMMENDED_COLUMNS)

    try:
        packages = _read_sheet(
            excel_path,
            sheet_name,
            required_alias_groups=[
                ["상품코드", "패키지상품코드", "package_product_code"],
                ["국문상품명", "패키지상품명", "package_product_name"],
                ["패키지입수량", "package_qty"],
                ["패키지해체정책", "unpack_policy", "package_unpack_policy"],
            ],
            required_exact_columns=[],
        )

        packages.df = _rename_packages_columns(packages.df)
        packages.columns = list(packages.df.columns)
        return packages

    except MasterLoadError as e:
        print(f"[WARN] packages_master optional sheet skip: {e}")
        return _make_empty_sheet_result(sheet_name, PACKAGES_RECOMMENDED_COLUMNS)


def load_master_workbook(excel_path: str | Path) -> Dict[str, object]:
    excel_path = Path(excel_path)
    if not excel_path.exists():
        raise FileNotFoundError(f"마스터 파일이 없습니다: {excel_path}")

    products = _read_sheet(
        excel_path,
        "products_master",
        required_alias_groups=[
            ["상품코드"],
            ["국문상품명"],
            ["가로(cm)"],
            ["세로(cm)"],
            ["높이(cm)"],
        ],
        required_exact_columns=PRODUCTS_REQUIRED,
    )

    fullboxes = _read_sheet(
        excel_path,
        "fullboxes_master",
        required_alias_groups=[
            ["상품코드"],
            ["국문상품명"],
            ["완박스입수량"],
            ["완박스박스코드"],
            ["혼합완박스허용여부"],
        ],
        required_exact_columns=FULLBOXES_REQUIRED,
    )

    boxes = _read_sheet(
        excel_path,
        "boxes_master",
        required_alias_groups=[
            ["박스코드"],
            ["박스명"],
            ["내경가로(cm)"],
            ["내경세로(cm)"],
            ["내경높이(cm)"],
        ],
        required_exact_columns=BOXES_REQUIRED,
    )

    rules = _read_sheet(
        excel_path,
        "rules_master",
        required_alias_groups=[
            ["rule_key", "규칙코드"],
            ["rule_value", "규칙값"],
        ],
        required_exact_columns=RULES_REQUIRED,
    )

    packages = _read_packages_sheet(excel_path)

    return {
        "excel_path": str(excel_path),
        "products": products.df.copy(),
        "fullboxes": fullboxes.df.copy(),
        "boxes": boxes.df.copy(),
        "rules_df": rules.df.copy(),
        "rules": build_rules_dict(rules.df.copy()),
        "packages": packages.df.copy(),
        "header_rows": {
            "products_master": products.header_row,
            "fullboxes_master": fullboxes.header_row,
            "boxes_master": boxes.header_row,
            "rules_master": rules.header_row,
            "packages_master": packages.header_row,
        },
        "columns": {
            "products_master": products.columns,
            "fullboxes_master": fullboxes.columns,
            "boxes_master": boxes.columns,
            "rules_master": rules.columns,
            "packages_master": packages.columns,
        },
        "sheet_loaded": {
            "products_master": products.loaded,
            "fullboxes_master": fullboxes.loaded,
            "boxes_master": boxes.loaded,
            "rules_master": rules.loaded,
            "packages_master": packages.loaded,
        },
    }


def _prepare_products_base(products_df: pd.DataFrame) -> pd.DataFrame:
    df = _normalize_columns(products_df)

    if "사용여부" in df.columns:
        df = df[df["사용여부"].apply(_bool_yn)].copy()

    numeric_cols = ["가로(cm)", "세로(cm)", "높이(cm)", "개당중량(kg)", "완박스입수량", "특수우선입수량"]
    for col in map(_norm_col, numeric_cols):
        if col in df.columns:
            df[col] = df[col].apply(_to_float)

    for col in map(_norm_col, ["혼합완박스허용여부", "패키지상품여부"]):
        if col in df.columns:
            df[col] = df[col].apply(_bool_yn)

    for col in map(
        _norm_col,
        [
            "상품코드",
            "국문상품명",
            "완박스박스코드",
            "완박스박스명",
            "완박스혼합그룹",
            "패킹정책코드",
            "특수우선박스코드",
            "패키지정책참조",
            "특수상품군",
            "원본파일",
        ],
    ):
        if col in df.columns:
            df[col] = df[col].apply(_norm_text)

    return df.reset_index(drop=True)


def _prepare_fullboxes_base(fullboxes_df: pd.DataFrame) -> pd.DataFrame:
    df = _normalize_columns(fullboxes_df)

    if "사용여부" in df.columns:
        df = df[df["사용여부"].apply(_bool_yn)].copy()

    numeric_cols = [
        "완박스입수량",
        "완박스가로(cm)",
        "완박스세로(cm)",
        "완박스높이(cm)",
        "완박스중량(kg)",
        "우선순위",
    ]
    for col in map(_norm_col, numeric_cols):
        if col in df.columns:
            df[col] = df[col].apply(_to_float)

    for col in map(_norm_col, ["혼합완박스허용여부"]):
        if col in df.columns:
            df[col] = df[col].apply(_bool_yn)

    for col in map(
        _norm_col,
        [
            "상품코드",
            "국문상품명",
            "완박스박스코드",
            "완박스박스명",
            "완박스혼합그룹",
            "잔량처리방식",
        ],
    ):
        if col in df.columns:
            df[col] = df[col].apply(_norm_text)

    if _norm_col("우선순위") in df.columns:
        df = df.sort_values(
            [_norm_col("우선순위"), _norm_col("국문상품명")],
            ascending=[True, True],
        ).copy()

    return df.reset_index(drop=True)


def prepare_boxes_for_engine(boxes_df: pd.DataFrame) -> pd.DataFrame:
    df = _normalize_columns(boxes_df)

    active_col = _norm_col("active")
    if active_col in df.columns:
        df = df[df[active_col].apply(_bool_yn)].copy()

    numeric_cols = [
        "외경가로(cm)",
        "외경세로(cm)",
        "외경높이(cm)",
        "내경가로(cm)",
        "내경세로(cm)",
        "내경높이(cm)",
        "박스중량(kg)",
        "최대허용중량(kg)",
        "박스정렬우선순위",
    ]
    for col in map(_norm_col, numeric_cols):
        if col in df.columns:
            df[col] = df[col].apply(_to_float)

    for col in map(_norm_col, ["박스코드", "박스명", "원본박스명", "원본파일"]):
        if col in df.columns:
            df[col] = df[col].apply(_norm_text)

    sort_col = _norm_col("박스정렬우선순위")
    if sort_col in df.columns:
        df = df.sort_values(
            [sort_col, _norm_col("내경가로(cm)"), _norm_col("내경세로(cm)")],
            ascending=[True, True, True],
        ).copy()

    return df.reset_index(drop=True)


def prepare_products_for_engine(products_df: pd.DataFrame, fullboxes_df: pd.DataFrame) -> pd.DataFrame:
    products = _prepare_products_base(products_df)
    fullboxes = _prepare_fullboxes_base(fullboxes_df)

    by_code = fullboxes.drop_duplicates(subset=[_norm_col("상품코드")], keep="first").copy()
    by_name = fullboxes.drop_duplicates(subset=[_norm_col("국문상품명")], keep="first").copy()

    fb_cols = [
        _norm_col("상품코드"),
        _norm_col("국문상품명"),
        _norm_col("완박스입수량"),
        _norm_col("완박스박스코드"),
        _norm_col("완박스박스명"),
        _norm_col("혼합완박스허용여부"),
        _norm_col("완박스혼합그룹"),
        _norm_col("잔량처리방식"),
    ]

    by_code = by_code[fb_cols].rename(
        columns={c: f"fb_code__{c}" for c in fb_cols if c != _norm_col("상품코드")}
    )
    by_name = by_name[fb_cols].rename(
        columns={c: f"fb_name__{c}" for c in fb_cols if c != _norm_col("국문상품명")}
    )

    merged = products.merge(by_code, on=_norm_col("상품코드"), how="left")
    merged = merged.merge(by_name, on=_norm_col("국문상품명"), how="left")

    def _is_meaningful_base(col: str, value) -> bool:
        if pd.isna(value):
            return False
        text = str(value).strip()
        if text == "":
            return False

        if col in {_norm_col("완박스입수량"), _norm_col("혼합완박스허용여부")} and text in {
            "0", "0.0", "False", "false", "N"
        }:
            return False

        if col in {_norm_col("완박스박스코드"), _norm_col("완박스박스명"), _norm_col("완박스혼합그룹")} and text in {
            "nan", "None"
        }:
            return False

        return True

    def choose(row, base_col: str):
        col = _norm_col(base_col)
        base = row.get(col)
        code_v = row.get(f"fb_code__{col}")
        name_v = row.get(f"fb_name__{col}")

        if pd.notna(code_v) and str(code_v).strip() != "":
            return code_v, "fullboxes_master:code"

        if pd.notna(name_v) and str(name_v).strip() != "":
            return name_v, "fullboxes_master:name"

        if _is_meaningful_base(col, base):
            return base, "products_master"

        return base, ""

    sources = []
    for target_col in ["완박스입수량", "완박스박스코드", "완박스박스명", "혼합완박스허용여부", "완박스혼합그룹"]:
        values = []
        srcs = []

        for _, row in merged.iterrows():
            v, s = choose(row, target_col)
            values.append(v)
            srcs.append(s)

        merged[_norm_col(target_col)] = values
        sources.append(pd.Series(srcs, name=f"source__{_norm_col(target_col)}"))

    for s in sources:
        merged[s.name] = s.values

    for col in map(_norm_col, ["완박스입수량", "특수우선입수량", "가로(cm)", "세로(cm)", "높이(cm)", "개당중량(kg)"]):
        if col in merged.columns:
            merged[col] = merged[col].apply(_to_float)

    for col in map(_norm_col, ["혼합완박스허용여부", "패키지상품여부"]):
        if col in merged.columns:
            merged[col] = merged[col].apply(_bool_yn)

    return merged.reset_index(drop=True)


def prepare_packages_for_engine(packages_df: pd.DataFrame) -> pd.DataFrame:
    df = _rename_packages_columns(packages_df)

    use_col = _norm_col("사용여부")
    if use_col in df.columns:
        non_empty_mask = df[use_col].astype(str).str.strip() != ""
        if non_empty_mask.any():
            df = df[df[use_col].apply(_bool_yn)].copy()

    numeric_cols = [
        "패키지입수량",
        "패키지가로(cm)",
        "패키지세로(cm)",
        "패키지높이(cm)",
        "패키지중량(kg)",
    ]
    for col in map(_norm_col, numeric_cols):
        if col in df.columns:
            df[col] = df[col].apply(lambda x: _to_float(x, default=0.0))

    bool_cols = [
        "패키지BOM존재여부",
        "사용여부",
    ]
    for col in map(_norm_col, bool_cols):
        if col in df.columns:
            df[col] = df[col].apply(_bool_yn)

    text_cols = [
        "상품코드",
        "국문상품명",
        "패키지해체정책",
        "원본파일",
        "엔진기본처리정책",
        "해체허용조건",
        "해체후계산단위",
        "잔량처리방식",
        "데이터검증상태",
        "검증메모",
    ]
    for col in map(_norm_col, text_cols):
        if col in df.columns:
            df[col] = df[col].apply(_norm_text)

    return df.reset_index(drop=True)


def print_load_summary(
    load_result: Dict[str, object],
    prepared_products_df: pd.DataFrame | None = None,
    prepared_boxes_df: pd.DataFrame | None = None,
    prepared_packages_df: pd.DataFrame | None = None,
) -> None:
    print("=" * 90)
    print("[MASTER LOAD SUMMARY]")
    print(f"file: {load_result['excel_path']}")

    for sheet_name, header_row in load_result["header_rows"].items():
        loaded = load_result.get("sheet_loaded", {}).get(sheet_name, True)
        columns = load_result["columns"].get(sheet_name, [])
        if loaded:
            print(f"- {sheet_name}: header_row={header_row}, columns={columns}")
        else:
            print(f"- {sheet_name}: NOT FOUND (optional), columns={columns}")

    print("[rules sample]")
    keys = [
        "PACKING_MODE_FULLBOX",
        "PACKING_MODE_REPACK",
        "FULLBOX_MIX_ENABLE",
        "FULLBOX_MIX_GROUP_FIRST",
        "FULLBOX_MIX_TOL_LENGTH_CM",
        "FULLBOX_MIX_TOL_WIDTH_CM",
        "FULLBOX_MIX_TOL_HEIGHT_CM",
        "FULLBOX_MIX_TOL_WEIGHT_KG",
        "FULLBOX_REMAINDER_TO_REPACK",
        "BOX_MAX_WEIGHT_KG",
    ]
    for k in keys:
        print(f"  {k} = {load_result['rules'].get(k)}")

    if prepared_products_df is not None:
        total = len(prepared_products_df)
        with_fullbox = int((prepared_products_df[_norm_col("완박스입수량")] > 0).sum())
        code_match = int((prepared_products_df["source__완박스입수량"] == "fullboxes_master:code").sum())
        name_match = int((prepared_products_df["source__완박스입수량"] == "fullboxes_master:name").sum())
        base_match = int((prepared_products_df["source__완박스입수량"] == "products_master").sum())

        print("[prepared products]")
        print(f"  total_active_products = {total}")
        print(f"  with_fullbox_spec = {with_fullbox}")
        print(f"  source_products_master = {base_match}")
        print(f"  source_fullboxes_master_code = {code_match}")
        print(f"  source_fullboxes_master_name = {name_match}")

    if prepared_boxes_df is not None:
        print("[prepared boxes]")
        print(f"  total_active_boxes = {len(prepared_boxes_df)}")

    if prepared_packages_df is not None:
        print("[prepared packages]")
        print(f"  total_active_package_rows = {len(prepared_packages_df)}")

    print("=" * 90)