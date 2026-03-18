# packing_api.py
# 목적:
# - gpt_packing_bridge.run_packing() 을 HTTP API로 노출
# - GPT Actions에서 읽을 수 있도록 OpenAPI servers 주소 포함
# - shipping_method(완박스/재포장) 를 별도 파라미터로 받음
# - order_text 를 최대한 안전하게 정리해서 엔진에 전달
# - 오래 걸리는 요청은 타임아웃 처리

import re
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

from gpt_packing_bridge import run_packing


# 중요:
# cloudflared를 다시 켜서 trycloudflare 주소가 바뀌면
# 아래 PUBLIC_SERVER_URL 도 같이 바꿔줘야 함.
PUBLIC_SERVER_URL = "https://daniel-naples-riders-prices.trycloudflare.com"

ENGINE_TIMEOUT_SECONDS = 90
EXECUTOR = ThreadPoolExecutor(max_workers=2)

app = FastAPI(
    title="Packing Bot API",
    version="1.1.0",
    description="패킹봇 로컬 API",
    servers=[
        {"url": PUBLIC_SERVER_URL, "description": "Public tunnel URL"},
        {"url": "http://127.0.0.1:8000", "description": "Local development URL"},
    ],
)


class PackRequest(BaseModel):
    shipping_method: Optional[str] = Field(
        default=None,
        description="출고 방법. 예: 완박스 / 재포장",
        examples=["완박스", "재포장"],
    )
    order_text: str = Field(
        ...,
        description="주문 텍스트 전체",
        examples=[
            "완박스\n패킹리스트 yes\n1. 비에녹스200u / 75\n2. 리체스 딥 리도(C) / 225\n3. 엘라스티 D 플러스(1syr) / 25"
        ],
    )
    packing_list_needed: Optional[str] = Field(
        default="no",
        description="패킹리스트 출력 여부. yes / no / 예 / 아니오 / 필요 / 불필요"
    )


class PackResponse(BaseModel):
    success: bool
    shipping_method: str
    packing_list_needed: str
    normalized_order_text: str
    result: str


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def normalize_product_name(name: str) -> str:
    name = normalize_space(name)
    # 괄호 안팎 불필요 공백 정리
    name = re.sub(r"\(\s*", "(", name)
    name = re.sub(r"\s*\)", ")", name)
    # 슬래시 주변 공백은 결과 표준화용
    return name.strip()


def normalize_shipping_method(value: Optional[str], raw_text: str) -> str:
    candidates = [
        normalize_space(value or "").lower(),
        normalize_space(raw_text or "").lower(),
    ]
    combined = "\n".join(candidates)

    if "완박스" in combined or "fullbox" in combined or "full box" in combined or "full-box" in combined:
        return "완박스"
    if "재포장" in combined or "repack" in combined or "re-pack" in combined or "re pack" in combined:
        return "재포장"

    # 기본값은 빈 문자열
    return ""


def _normalize_yes_no_value(value: Optional[str]) -> str:
    text = normalize_space(value or "").lower()

    yes_set = {"yes", "y", "예", "필요", "true", "1"}
    no_set = {"no", "n", "아니오", "불필요", "false", "0"}

    if text in yes_set:
        return "yes"
    if text in no_set:
        return "no"
    return ""


def normalize_packing_list_needed(value: Optional[str], raw_text: str) -> str:
    direct = _normalize_yes_no_value(value)
    if direct:
        return direct

    for raw_line in (raw_text or "").splitlines():
        line = normalize_space(raw_line)
        compact = re.sub(r"\s+", "", line).lower()

        if compact.startswith("패킹리스트"):
            if any(token in compact for token in ["yes", "예", "필요"]):
                return "yes"
            if any(token in compact for token in ["no", "아니오", "불필요"]):
                return "no"

        if compact.startswith("packinglistneeded"):
            if "yes" in compact:
                return "yes"
            if "no" in compact:
                return "no"

    return "no"


def is_meta_line(line: str) -> bool:
    compact = re.sub(r"\s+", "", line).lower()

    if not compact:
        return True

    meta_exact = {
        "완박스",
        "재포장",
        "yes",
        "no",
        "예",
        "아니오",
        "필요",
        "불필요",
    }

    if compact in meta_exact:
        return True

    meta_prefixes = [
        "패킹리스트",
        "packinglistneeded",
        "출고방법",
        "shippingmethod",
    ]

    return any(compact.startswith(prefix) for prefix in meta_prefixes)


def clean_line(line: str) -> str:
    line = (line or "").strip()
    line = re.sub(r"^\d+\.\s*", "", line)       # 1. 상품명 / 수량
    line = re.sub(r"^[\-\*\•]\s*", "", line)    # - 상품명 / 수량
    return normalize_space(line)


def parse_order_line(line: str):
    # 형식 1: 상품명 / 수량
    m = re.match(r"(.+?)\s*/\s*(\d+)\s*$", line)
    if m:
        name = normalize_product_name(m.group(1))
        qty = m.group(2)
        return name, qty

    # 형식 2: 상품명 수량
    m = re.match(r"(.+?)\s+(\d+)\s*$", line)
    if m:
        name = normalize_product_name(m.group(1))
        qty = m.group(2)
        return name, qty

    return None


def extract_order_lines(raw_text: str) -> list[str]:
    raw = (raw_text or "").replace("\r", "\n").strip()

    if not raw:
        return []

    # 한 줄로 붙여 넣었을 때 번호 앞에서 줄바꿈 보정
    raw = re.sub(r"\s+(?=\d+\.\s*)", "\n", raw)

    lines: list[str] = []

    for original_line in raw.splitlines():
        line = clean_line(original_line)

        if not line:
            continue
        if is_meta_line(line):
            continue

        parsed = parse_order_line(line)
        if parsed:
            name, qty = parsed
            lines.append(f"{name} / {qty}")

    if lines:
        return lines

    # fallback:
    # 한 줄 입력 + 번호 없음 + 여러 상품이 연속된 경우 최대한 추출 시도
    flat = normalize_space(raw)
    matches = re.finditer(
        r"([^/]+?)\s*/\s*(\d+)(?=\s+(?:[가-힣A-Za-z]|\d+\.)|$)",
        flat
    )

    fallback_lines = []
    for match in matches:
        name = normalize_product_name(match.group(1))
        qty = match.group(2)

        if is_meta_line(name):
            continue
        if name and qty:
            fallback_lines.append(f"{name} / {qty}")

    return fallback_lines


def build_engine_order_text(
    shipping_method: str,
    packing_list_needed: str,
    order_lines: list[str],
) -> str:
    parts = []

    if shipping_method:
        parts.append(shipping_method)

    parts.append(f"패킹리스트 {packing_list_needed}")
    parts.extend(order_lines)

    return "\n".join(parts).strip()


@app.get("/")
def root():
    return {
        "message": "Packing Bot API is running",
        "docs_url": "/docs",
        "openapi_url": "/openapi.json",
        "public_server_url": PUBLIC_SERVER_URL,
        "version": "1.1.0",
        "engine_timeout_seconds": ENGINE_TIMEOUT_SECONDS,
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "1.1.0"
    }


@app.post("/pack", response_model=PackResponse, operation_id="pack_pack_post")
def pack(request: PackRequest):
    raw_order_text = (request.order_text or "").strip()
    shipping_method = normalize_shipping_method(request.shipping_method, raw_order_text)
    packing_flag = normalize_packing_list_needed(request.packing_list_needed, raw_order_text)
    order_lines = extract_order_lines(raw_order_text)

    if not order_lines:
        return PackResponse(
            success=False,
            shipping_method=shipping_method,
            packing_list_needed=packing_flag,
            normalized_order_text="",
            result="[ERROR]\n주문 텍스트에서 상품명 / 수량을 추출하지 못했습니다."
        )

    engine_order_text = build_engine_order_text(
        shipping_method=shipping_method,
        packing_list_needed=packing_flag,
        order_lines=order_lines,
    )

    print("\n[PACK API REQUEST]")
    print(f"shipping_method={shipping_method!r}")
    print(f"packing_list_needed={packing_flag!r}")
    print("[normalized_order_text]")
    print(engine_order_text)

    try:
        future = EXECUTOR.submit(
            run_packing,
            order_text=engine_order_text,
            packing_list_needed=packing_flag
        )
        result = future.result(timeout=ENGINE_TIMEOUT_SECONDS)

    except FuturesTimeoutError:
        return PackResponse(
            success=False,
            shipping_method=shipping_method,
            packing_list_needed=packing_flag,
            normalized_order_text=engine_order_text,
            result=f"[ERROR]\n엔진 응답 시간이 {ENGINE_TIMEOUT_SECONDS}초를 초과했습니다."
        )

    except Exception as e:
        print("\n[PACK API EXCEPTION]")
        print(traceback.format_exc())

        return PackResponse(
            success=False,
            shipping_method=shipping_method,
            packing_list_needed=packing_flag,
            normalized_order_text=engine_order_text,
            result=f"[ERROR]\n{str(e)}"
        )

    success = True
    if not result or result.strip() == "":
        success = False
        result = "[ERROR]\n결과가 비어 있습니다."
    elif "[ERROR]" in result or "[gpt_packing_bridge 실행 오류]" in result:
        success = False

    print("\n[PACK API RESULT PREVIEW]")
    print((result or "")[:1000])

    return PackResponse(
        success=success,
        shipping_method=shipping_method,
        packing_list_needed=packing_flag,
        normalized_order_text=engine_order_text,
        result=result
    )