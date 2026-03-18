# gpt_packing_bridge.py
# 목적:
# - text_order_runner.py를 수정하지 않고 GPT 연결용 브릿지 역할 수행
# - 주문 입력 종료(blank line)와 패킹리스트 yes/no 입력을 안정적으로 분리
# - "상품명 / 수량" 형식도 자동 정리
# - 결과에서는 실제 결과 위주로 반환

import os
import re
import io
import sys
import runpy
import builtins
from contextlib import redirect_stdout, redirect_stderr


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUNNER_PATH = os.path.join(BASE_DIR, "text_order_runner.py")


def _normalize_yes_no(value: str) -> str:
    if value is None:
        return "no"

    v = str(value).strip().lower()

    yes_set = {"yes", "y", "예", "필요", "ㅇ", "o", "true", "1"}
    no_set = {"no", "n", "아니오", "불필요", "ㄴ", "x", "false", "0"}

    if v in yes_set:
        return "yes"
    if v in no_set:
        return "no"

    return "no"


def _normalize_order_line(line: str) -> str:
    """
    사용자 입력에서 실제 주문줄만 남기고 정리

    예)
    1. 비에녹스200u / 75   -> 비에녹스200u 75
    비에녹스200u / 75      -> 비에녹스200u 75
    비에녹스200u 75        -> 비에녹스200u 75

    아래 같은 머리말/설정 줄은 제거:
    - 재포장
    - 완박스
    - 패킹리스트 no
    - 패킹리스트 yes
    - 출고 방법: 재포장
    - 패킹리스트 필요 여부: no
    """
    if not line:
        return ""

    s = line.strip()
    if not s:
        return ""

    s_lower = s.lower()

    # 자주 들어오는 머리말/설정 줄 제거
    skip_exact = {
        "재포장",
        "완박스",
        "fullbox",
        "repacking",
    }

    if s in skip_exact:
        return ""

    if s_lower in {"yes", "no"}:
        return ""

    # 패킹리스트/출고방법 관련 줄 제거
    if (
        s.startswith("패킹리스트")
        or s.startswith("출고 방법")
        or s.startswith("출고방법")
        or s.startswith("shipping method")
        or s.startswith("packing list")
    ):
        return ""

    # 앞 번호 제거: 1. / 2) / 3 .
    s = re.sub(r"^\s*\d+\s*[\.\)]\s*", "", s).strip()

    # 번호 제거 후 다시 한 번 머리말 검사
    s_lower = s.lower()
    if not s:
        return ""

    if s in skip_exact:
        return ""

    if s_lower in {"yes", "no"}:
        return ""

    if (
        s.startswith("패킹리스트")
        or s.startswith("출고 방법")
        or s.startswith("출고방법")
        or s.startswith("shipping method")
        or s.startswith("packing list")
    ):
        return ""

    # "상품명 / 수량" -> "상품명 수량"
    m = re.match(r"^(.*?)\s*/\s*([0-9]+)\s*$", s)
    if m:
        product_name = m.group(1).strip()
        qty = m.group(2).strip()
        return f"{product_name} {qty}"

    # "상품명 수량" 패턴만 통과
    # 마지막 토큰이 숫자일 때만 주문줄로 인정
    m2 = re.match(r"^(.*\S)\s+([0-9]+)\s*$", s)
    if m2:
        product_name = m2.group(1).strip()
        qty = m2.group(2).strip()
        return f"{product_name} {qty}"

    # 그 외 문장은 주문줄로 보지 않고 제거
    return ""

def _normalize_order_text(order_text: str) -> str:
    lines = []
    for raw_line in (order_text or "").splitlines():
        normalized = _normalize_order_line(raw_line)
        if normalized:
            lines.append(normalized)
    return "\n".join(lines)


def _build_stdin_text(order_text: str, packing_list_needed: str) -> str:
    """
    text_order_runner.py용 가짜 입력 생성

    핵심:
    - 주문 입력 끝내는 빈 줄을 2번 넣음
    - 그 다음 패킹리스트 yes/no를 여러 번 넣어서
      혹시 한 번 더 물어도 대응
    """
    normalized_order_text = _normalize_order_text(order_text)
    packing_flag = _normalize_yes_no(packing_list_needed)

    stdin_parts = [
        normalized_order_text,
        "",              # 주문 입력 종료용 blank 1
        "",              # 주문 입력 종료용 blank 2
        packing_flag,    # 패킹리스트 입력
        packing_flag,
        packing_flag,
        packing_flag,
        "",
        "",
    ]
    return "\n".join(stdin_parts) + "\n"


class FakeStdin(io.StringIO):
    @property
    def encoding(self):
        return "utf-8"

    def isatty(self):
        return False

    def readline(self, *args, **kwargs):
        line = super().readline(*args, **kwargs)
        if line == "":
            return "\n"
        return line


def _clean_result_text(raw_text: str, packing_list_needed: str) -> str:
    if not raw_text:
        return ""

    text = raw_text.replace("\r\n", "\n").strip()

    if "[ERROR]" in text:
        idx = text.find("[ERROR]")
        return text[idx:].strip()

    packing_flag = _normalize_yes_no(packing_list_needed)

    if packing_flag == "no":
        marker = "[최종 결과]"
        if marker in text:
            idx = text.find(marker)
            return text[idx:].strip()

    yes_markers = [
        "[capacity_reference]",
        "[box_summary]",
        "[최종 결과]",
        "[파싱된 주문]",
        "[ROUTER]",
    ]

    positions = []
    for marker in yes_markers:
        pos = text.find(marker)
        if pos != -1:
            positions.append(pos)

    if positions:
        return text[min(positions):].strip()

    return text


def run_packing(order_text: str, packing_list_needed: str = "no") -> str:
    if not os.path.exists(RUNNER_PATH):
        return f"[gpt_packing_bridge 오류]\ntext_order_runner.py 파일을 찾을 수 없습니다: {RUNNER_PATH}"

    fake_input_text = _build_stdin_text(order_text, packing_list_needed)
    fake_stdin = FakeStdin(fake_input_text)

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()

    original_stdin = sys.stdin
    original_input = builtins.input
    original_cwd = os.getcwd()

    packing_flag = _normalize_yes_no(packing_list_needed)

    def patched_input(prompt: str = "") -> str:
        prompt_text = (prompt or "").lower()

        # 패킹리스트 질문이면 남은 blank를 무시하고 무조건 yes/no 반환
        if (
            "패킹" in prompt_text
            or "yes / no" in prompt_text
            or "yes/no" in prompt_text
            or "필요" in prompt_text
            or "불필요" in prompt_text
            or "아니오" in prompt_text
        ):
            return packing_flag

        line = fake_stdin.readline()
        return line.rstrip("\n")

    try:
        os.chdir(BASE_DIR)
        sys.stdin = fake_stdin
        builtins.input = patched_input

        with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
            runpy.run_path(RUNNER_PATH, run_name="__main__")

    except Exception as e:
        stdout_text = stdout_buffer.getvalue().strip()
        stderr_text = stderr_buffer.getvalue().strip()

        error_lines = ["[gpt_packing_bridge 실행 오류]", str(e)]

        if stdout_text:
            error_lines.append("")
            error_lines.append("[stdout]")
            error_lines.append(stdout_text)

        if stderr_text:
            error_lines.append("")
            error_lines.append("[stderr]")
            error_lines.append(stderr_text)

        return "\n".join(error_lines)

    finally:
        os.chdir(original_cwd)
        sys.stdin = original_stdin
        builtins.input = original_input

    stdout_text = stdout_buffer.getvalue().strip()
    stderr_text = stderr_buffer.getvalue().strip()

    cleaned = _clean_result_text(stdout_text, packing_list_needed)

    if stderr_text:
        if cleaned:
            return cleaned + "\n\n[stderr]\n" + stderr_text
        return "[stderr]\n" + stderr_text

    return cleaned


def run_packing_raw(raw_input_text: str) -> str:
    raw_lines = [line.rstrip() for line in (raw_input_text or "").splitlines()]
    raw_lines = [line for line in raw_lines if line.strip() != ""]

    if not raw_lines:
        return "[gpt_packing_bridge 오류]\n입력값이 비어 있습니다."

    packing_flag = "no"
    order_lines = raw_lines[:]

    last_line = raw_lines[-1].strip().lower()
    if last_line in {"yes", "no", "예", "아니오", "필요", "불필요", "y", "n"}:
        packing_flag = last_line
        order_lines = raw_lines[:-1]

    order_text = "\n".join(order_lines)
    return run_packing(order_text=order_text, packing_list_needed=packing_flag)


if __name__ == "__main__":
    print("=" * 90)
    print("[GPT PACKING BRIDGE TEST]")
    print("=" * 90)
    print("주문 텍스트를 붙여넣고, 마지막 줄에서 엔터 2번 누르세요.")
    print("예시:")
    print("1. 비에녹스200u / 75")
    print("2. 리체스 딥 리도(C) / 225")
    print("3. 엘라스티 D 플러스(1syr) / 25")
    print("-" * 90)

    lines = []
    empty_count = 0

    while True:
        line = input()

        if line.strip() == "":
            empty_count += 1
            if empty_count >= 2:
                break
            continue

        empty_count = 0
        lines.append(line)

    print()
    print("패킹리스트 필요 여부를 입력하세요. (yes / no / 예 / 아니오 / 필요 / 불필요)")
    packing_flag = input("입력: ").strip()

    order_text = "\n".join(lines)
    result = run_packing(order_text=order_text, packing_list_needed=packing_flag)

    print()
    print("=" * 90)
    print("[BRIDGE RESULT]")
    print("=" * 90)
    print(result)