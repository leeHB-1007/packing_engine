# packing_api.py
# 목적:
# - gpt_packing_bridge.run_packing() 을 HTTP API로 노출
# - GPT Actions에서 읽을 수 있도록 OpenAPI servers 주소 포함
# - shipping_method(완박스/재포장) 를 별도 파라미터로 받음
# - order_text 를 최대한 안전하게 정리해서 엔진에 전달
# - 오래 걸리는 요청은 타임아웃 처리

import os
import re
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from contextlib import redirect_stdout
from datetime import datetime
import io
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from router import route_packing
from router import print_final_summary
from sulu_med_exporter import (
    build_router_display_payload,
    export_router_result_to_sulu_med_xlsx,
)
from text_order_runner import print_clean_final_result


PUBLIC_SERVER_URL = os.getenv("PUBLIC_SERVER_URL", "").strip()

ENGINE_TIMEOUT_SECONDS = 90
EXECUTOR = ThreadPoolExecutor(max_workers=2)
WEB_APP_DIR = Path(__file__).resolve().parent / "1" / "web-apply"
IS_VERCEL = bool(os.getenv("VERCEL") or os.getenv("VERCEL_ENV"))
WEB_EXPORT_DIR = (
    Path("/tmp/packing_engine_exports")
    if IS_VERCEL
    else Path(__file__).resolve().parent / "output" / "web_exports"
)

servers = [{"url": "http://127.0.0.1:8000", "description": "Local development URL"}]
if PUBLIC_SERVER_URL:
    servers.insert(0, {"url": PUBLIC_SERVER_URL, "description": "Public deployment URL"})

app = FastAPI(
    title="Packing Bot API",
    version="1.1.0",
    description="패킹봇 로컬 API",
    servers=servers,
)

if WEB_APP_DIR.exists():
    app.mount("/app-assets", StaticFiles(directory=str(WEB_APP_DIR)), name="app-assets")


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
    selected_engine: str = ""
    table_groups: list[dict[str, Any]] = Field(default_factory=list)
    table_totals: dict[str, Any] = Field(default_factory=dict)


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
    # 형식 1: 상품명 / 수량 또는 상품명 - 수량
    m = re.match(r"(.+?)\s*(?:/|-)\s*(\d+)\s*$", line)
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


def build_order_items(order_lines: list[str]) -> list[dict]:
    items = []

    for line in order_lines:
        parsed = parse_order_line(line)
        if not parsed:
            continue

        product_name, qty = parsed
        items.append(
            {
                "product_name": product_name,
                "qty": int(qty),
            }
        )

    return items


def build_export_filename(prefix: str = "packing_result") -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = uuid4().hex[:8]
    return f"{prefix}_{stamp}_{suffix}.xlsx"


def execute_router(order_items: list[dict]) -> dict:
    with redirect_stdout(io.StringIO()):
        return route_packing(order_items=order_items, debug=False)


def render_router_result(router_result: dict, packing_list_needed: str) -> str:
    buffer = io.StringIO()

    with redirect_stdout(buffer):
        if packing_list_needed == "yes":
            print("\n" + "=" * 90)
            print("[패킹리스트 출력]")
            print("=" * 90)
            print(f"선택 엔진: {router_result['selected_engine']}")
            print_final_summary(
                selected_engine=router_result["selected_engine"],
                result=router_result["result"],
            )
        else:
            print_clean_final_result(router_result)

    return buffer.getvalue().strip()


def build_table_totals(groups: list[dict[str, Any]]) -> dict[str, Any]:
    total_box_count = 0
    total_each_qty = 0
    total_weight = 0.0

    for group in groups:
        total_box_count += int(group.get("box_count", 0) or 0)
        total_each_qty += int(group.get("each_qty", 0) or 0)
        total_weight += float(group.get("total_weight_kg", 0) or 0)

    return {
        "box_count": total_box_count,
        "each_qty": total_each_qty,
        "total_weight_kg": round(total_weight, 3),
    }


@app.get("/")
def root():
    return {
        "message": "Packing Bot API is running",
        "docs_url": "/docs",
        "openapi_url": "/openapi.json",
        "web_app_url": "/app",
        "public_server_url": PUBLIC_SERVER_URL,
        "version": "1.1.0",
        "engine_timeout_seconds": ENGINE_TIMEOUT_SECONDS,
    }


@app.get("/app")
def web_app():
    index_file = WEB_APP_DIR / "index.html"
    if not index_file.exists():
        return {
            "message": "web app not found",
            "expected_path": str(index_file),
        }
    return FileResponse(index_file)


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

    order_items = build_order_items(order_lines)
    if not order_items:
        return PackResponse(
            success=False,
            shipping_method=shipping_method,
            packing_list_needed=packing_flag,
            normalized_order_text=engine_order_text,
            result="[ERROR]\n주문 항목 생성에 실패했습니다."
        )

    try:
        future = EXECUTOR.submit(execute_router, order_items)
        routed_result = future.result(timeout=ENGINE_TIMEOUT_SECONDS)
        result = render_router_result(routed_result, packing_flag)
        display_payload = build_router_display_payload(routed_result)
        table_groups = display_payload["groups"]
        table_totals = build_table_totals(table_groups)

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
    elif "[ERROR]" in result:
        success = False

    print("\n[PACK API RESULT PREVIEW]")
    print((result or "")[:1000])

    return PackResponse(
        success=success,
        shipping_method=shipping_method,
        packing_list_needed=packing_flag,
        normalized_order_text=engine_order_text,
        result=result,
        selected_engine=str(routed_result.get("selected_engine", "") or ""),
        table_groups=table_groups,
        table_totals=table_totals,
    )


@app.post("/pack/export-xlsx")
def pack_export_xlsx(request: PackRequest):
    raw_order_text = (request.order_text or "").strip()
    shipping_method = normalize_shipping_method(request.shipping_method, raw_order_text)
    packing_flag = normalize_packing_list_needed(request.packing_list_needed, raw_order_text)
    order_lines = extract_order_lines(raw_order_text)

    if not order_lines:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "주문 텍스트에서 상품명 / 수량을 추출하지 못했습니다.",
            },
        )

    order_items = build_order_items(order_lines)
    if not order_items:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "엑셀 export용 주문 항목 생성에 실패했습니다.",
            },
        )

    try:
        routed_result = execute_router(order_items)

        WEB_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        output_file = WEB_EXPORT_DIR / build_export_filename(prefix="sulu_med_export")

        export_router_result_to_sulu_med_xlsx(
            router_result=routed_result,
            output_path=output_file,
            title="Sulu Med 출하건 (Web Export)",
        )

    except Exception as e:
        print("\n[PACK API XLSX EXCEPTION]")
        print(traceback.format_exc())
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": str(e),
            },
        )

    download_name = output_file.name
    if shipping_method:
        method_slug = re.sub(r"\s+", "_", shipping_method.strip())
        download_name = f"{method_slug}_{output_file.name}"

    return FileResponse(
        path=output_file,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=download_name,
    )
