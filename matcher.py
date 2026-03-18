from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# =========================
# 데이터 구조
# =========================
class RawOrderLine:
    def __init__(self, *args, **kwargs):
        # 1) 키워드 방식
        # RawOrderLine(raw_text="...", item_text="...", qty=10)
        if kwargs:
            raw_text = kwargs.get("raw_text")
            item_text = kwargs.get("item_text")
            qty = kwargs.get("qty")

            if item_text is None and raw_text is not None:
                item_text = raw_text
            if raw_text is None and item_text is not None:
                raw_text = item_text

            if qty is None:
                raise TypeError("RawOrderLine.__init__() missing required argument: 'qty'")

            self.raw_text = str(raw_text or "").strip()
            self.item_text = str(item_text or "").strip()
            self.qty = int(qty)
            return

        # 2) 기존 방식
        # RawOrderLine("키아라레쥬", 109)
        if len(args) == 2:
            item_text, qty = args
            self.raw_text = str(item_text).strip()
            self.item_text = str(item_text).strip()
            self.qty = int(qty)
            return

        # 3) 새 방식
        # RawOrderLine("키아라레쥬 / 109", "키아라레쥬", 109)
        if len(args) == 3:
            raw_text, item_text, qty = args
            self.raw_text = str(raw_text).strip()
            self.item_text = str(item_text).strip()
            self.qty = int(qty)
            return

        raise TypeError(
            "RawOrderLine expects either "
            "(item_text, qty) or (raw_text, item_text, qty)"
        )

    def __repr__(self):
        return (
            f"RawOrderLine(raw_text={self.raw_text!r}, "
            f"item_text={self.item_text!r}, qty={self.qty!r})"
        )


@dataclass
class Candidate:
    source: str
    product_code: str
    product_name: str
    score: int
    reason: str
    row: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MatchRow:
    raw_input: str
    item_text: str
    qty: int

    matched: bool = False
    matched_code: str = ""
    matched_name: str = ""
    match_source: str = ""
    match_reason: str = ""
    master_row: Dict[str, Any] = field(default_factory=dict)

    status: str = "unresolved"   # matched / ambiguous / unresolved
    message: str = ""
    candidates: List[Candidate] = field(default_factory=list)

    @property
    def product_code(self) -> str:
        return self.matched_code

    @property
    def product_name(self) -> str:
        return self.matched_name


# =========================
# 기본 유틸
# =========================
def _to_records(data: Any) -> List[Dict[str, Any]]:
    """
    pandas DataFrame / list[dict] 둘 다 받기
    """
    if data is None:
        return []

    if hasattr(data, "to_dict"):
        try:
            return data.to_dict(orient="records")
        except Exception:
            pass

    if isinstance(data, list):
        return data

    return []


def _pick_first(row: Dict[str, Any], keys: List[str]) -> str:
    for k in keys:
        if k in row and row[k] is not None:
            value = str(row[k]).strip()
            if value and value.lower() != "nan":
                return value
    return ""


def normalize_name(text: str) -> str:
    if text is None:
        return ""

    s = str(text).strip().upper()

    s = s.replace("（", "(").replace("）", ")")
    s = s.replace("[", "(").replace("]", ")")
    s = s.replace("_", " ")
    s = s.replace("-", " ")
    s = s.replace("/", " ")
    s = s.replace("·", " ")
    s = s.replace(".", " ")

    s = re.sub(r"\s+", " ", s).strip()
    return s


def compact_name(text: str) -> str:
    s = normalize_name(text)
    s = re.sub(r"[\s\(\)]", "", s)
    return s


def normalize_code(text: str) -> str:
    if text is None:
        return ""
    s = str(text).strip().upper()
    s = re.sub(r"[^A-Z0-9]", "", s)
    return s


def looks_like_product_code(text: str) -> bool:
    s = normalize_code(text)
    return bool(re.fullmatch(r"[A-Z]+[0-9]+", s))


# =========================
# 매처
# =========================
class ProductMatcher:
    def __init__(
        self,
        products_data: Any = None,
        fullboxes_data: Any = None,
        packages_data: Any = None,
    ):
        self.sources = {
            "products": _to_records(products_data),
            "fullboxes": _to_records(fullboxes_data),
            "packages": _to_records(packages_data),
        }

        self.code_keys = ["상품코드", "product_code", "code", "item_code"]
        self.name_keys = ["국문상품명", "상품명", "product_name", "name", "item_name"]

        self.index = self._build_index()

    def _build_index(self) -> Dict[str, List[Dict[str, Any]]]:
        indexed_rows: List[Dict[str, Any]] = []

        for source_name, records in self.sources.items():
            for row in records:
                product_code = normalize_code(_pick_first(row, self.code_keys))
                product_name = _pick_first(row, self.name_keys)

                if not product_code and not product_name:
                    continue

                indexed_rows.append({
                    "source": source_name,
                    "product_code": product_code,
                    "product_name": product_name.strip(),
                    "name_norm": normalize_name(product_name),
                    "name_compact": compact_name(product_name),
                    "row": row,
                })

        return {"rows": indexed_rows}

    def match(self, item_text: str) -> Tuple[str, Optional[Candidate], List[Candidate], str]:
        raw = str(item_text or "").strip()
        if not raw:
            return "unresolved", None, [], "입력 상품명이 비어 있습니다."

        candidates: List[Candidate] = []

        input_name_norm = normalize_name(raw)
        input_name_compact = compact_name(raw)
        input_code = normalize_code(raw)

        # 1) 코드 완전일치 우선
        if looks_like_product_code(raw):
            for row in self.index["rows"]:
                if row["product_code"] and row["product_code"] == input_code:
                    candidates.append(Candidate(
                        source=row["source"],
                        product_code=row["product_code"],
                        product_name=row["product_name"],
                        score=1000,
                        reason="EXACT_CODE",
                        row=row["row"],
                    ))

        # 2) 이름 exact 일치
        for row in self.index["rows"]:
            if row["name_norm"] and row["name_norm"] == input_name_norm:
                candidates.append(Candidate(
                    source=row["source"],
                    product_code=row["product_code"],
                    product_name=row["product_name"],
                    score=900,
                    reason="EXACT_NAME",
                    row=row["row"],
                ))

        # 3) compact exact 일치
        for row in self.index["rows"]:
            if row["name_compact"] and row["name_compact"] == input_name_compact:
                candidates.append(Candidate(
                    source=row["source"],
                    product_code=row["product_code"],
                    product_name=row["product_name"],
                    score=850,
                    reason="EXACT_COMPACT_NAME",
                    row=row["row"],
                ))

        # 4) 포함 검색
        for row in self.index["rows"]:
            if not row["name_compact"]:
                continue

            if input_name_compact and input_name_compact in row["name_compact"]:
                candidates.append(Candidate(
                    source=row["source"],
                    product_code=row["product_code"],
                    product_name=row["product_name"],
                    score=700,
                    reason="INPUT_IN_MASTER_NAME",
                    row=row["row"],
                ))
            elif row["name_compact"] in input_name_compact:
                candidates.append(Candidate(
                    source=row["source"],
                    product_code=row["product_code"],
                    product_name=row["product_name"],
                    score=650,
                    reason="MASTER_NAME_IN_INPUT",
                    row=row["row"],
                ))

        # 5) 중복 제거
        dedup: Dict[Tuple[str, str, str], Candidate] = {}
        for c in candidates:
            key = (c.source, c.product_code, compact_name(c.product_name))
            if key not in dedup or c.score > dedup[key].score:
                dedup[key] = c

        unique_candidates = list(dedup.values())

        # 6) 우선순위 정렬
        source_priority = {
            "products": 3,
            "fullboxes": 2,
            "packages": 1,
        }

        unique_candidates.sort(
            key=lambda x: (
                x.score,
                source_priority.get(x.source, 0),
                len(compact_name(x.product_name)),
            ),
            reverse=True,
        )

        if not unique_candidates:
            return "unresolved", None, [], "일치하는 상품을 찾지 못했습니다."

        top_score = unique_candidates[0].score
        top_group = [c for c in unique_candidates if c.score == top_score]

        unique_top_products = {
            (c.product_code, compact_name(c.product_name))
            for c in top_group
        }

        if len(unique_top_products) > 1:
            return (
                "ambiguous",
                None,
                top_group[:10],
                f"동일 점수 후보가 {len(unique_top_products)}개라 자동 확정하지 않았습니다.",
            )

        selected = unique_candidates[0]
        return (
            "matched",
            selected,
            unique_candidates[:10],
            f"{selected.product_name} / {selected.product_code} / source={selected.source}",
        )


# =========================
# 외부에서 호출하는 함수
# =========================
def match_order_lines(
    raw_order_lines,
    prepared_products=None,
    fullboxes_master=None,
    packages_master=None,
):
    matcher = ProductMatcher(
        products_data=prepared_products,
        fullboxes_data=fullboxes_master,
        packages_data=packages_master,
    )

    results: List[MatchRow] = []

    for line in raw_order_lines:
        raw_input = getattr(line, "raw_text", getattr(line, "item_text", ""))
        item_text = getattr(line, "item_text", "")
        qty = getattr(line, "qty", 0)

        status, selected, candidates, message = matcher.match(item_text)

        row = MatchRow(
            raw_input=raw_input,
            item_text=item_text,
            qty=qty,
            status=status,
            message=message,
            candidates=candidates,
        )

        if status == "matched" and selected is not None:
            row.matched = True
            row.matched_code = selected.product_code
            row.matched_name = selected.product_name
            row.match_source = selected.source
            row.match_reason = selected.reason
            row.master_row = selected.row

        results.append(row)

    return results


def print_match_result(match_rows):
    print("\n" + "=" * 90)
    print("[MATCH RESULT]")
    print("=" * 90)

    matched = [r for r in match_rows if getattr(r, "status", "") == "matched"]
    ambiguous = [r for r in match_rows if getattr(r, "status", "") == "ambiguous"]
    unresolved = [r for r in match_rows if getattr(r, "status", "") == "unresolved"]

    print("\n[matched]")
    for i, r in enumerate(matched, 1):
        print(
            f"{i}. 입력={r.raw_input} / qty={r.qty} "
            f"-> {r.matched_name} / {r.matched_code} "
            f"/ source={r.match_source} / reason={r.match_reason}"
        )

    print("\n[ambiguous]")
    for i, r in enumerate(ambiguous, 1):
        print(f"{i}. 입력={r.raw_input} / qty={r.qty} / message={r.message}")
        for c in r.candidates:
            print(
                f"   - 후보: {c.product_name} / {c.product_code} "
                f"/ source={c.source} / score={c.score} / reason={c.reason}"
            )

    print("\n[unresolved]")
    for i, r in enumerate(unresolved, 1):
        print(f"{i}. 입력={r.raw_input} / qty={r.qty} / message={r.message}")