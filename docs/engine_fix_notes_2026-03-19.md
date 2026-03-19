# Packing Engine Fix Notes (2026-03-19)

## 목적

샘플 출하건을 기준으로 테스트했을 때 발견된 런타임 오류와 엔진 경로 불일치를 정리하고, 이번 수정에서 무엇을 어떻게 고쳤는지 기록한다.

## 수정 요약

### 1. 재포장 박스 평가 중 `None` 크래시 방지

- 파일: `packing_engine/repack_engine.py`
- 증상:
  - 어떤 상품이 특정 박스에 전혀 적재되지 않으면 `_calc_best_orientation_fit()`가 `boxes_needed=None`을 반환할 수 있었다.
  - 이 값을 `int()`로 바로 변환하면서 `TypeError`가 발생했다.
- 수정:
  - `recommended_max_units <= 0` 또는 `best_orientation is None` 인 경우 먼저 건너뛰도록 유지했다.
  - 그 뒤 `boxes_needed`가 `None` 또는 빈 문자열인 후보는 제외하도록 가드 로직을 추가했다.
- 효과:
  - 박스 미적합 후보가 있어도 엔진 전체가 죽지 않고, 유효한 박스 후보만 계속 평가한다.

### 2. 실사용 엔진과 테스트 엔진의 매칭 기준 통일

- 파일: `packing_engine/run_packing_engine.py`
- 기존 문제:
  - `main.py`는 `products + fullboxes + packages`를 모두 matcher에 넘겼지만,
  - `run_packing_engine.py`는 `prepared_products`만 matcher에 넘기고 있었다.
- 수정:
  - `run_packing_engine.py`도 `fullboxes_master`, `packages_master`를 함께 넘기도록 변경했다.
- 효과:
  - 테스트에서는 잡히는데 실제 엔진/API 경로에서는 못 잡히는 상품 차이가 줄어든다.

### 3. `fullboxes_master`에만 있는 상품이 중간에 유실되는 문제 보완

- 파일: `packing_engine/fullbox_engine.py`
- 기존 문제:
  - matcher는 `fullboxes_master` 기준으로 상품을 찾을 수 있어도,
  - `run_fullbox_engine()` 내부 `resolve_orders()`는 `prepared_products_df`만 조회해서 `PRODUCT_NOT_FOUND`로 떨어질 수 있었다.
- 수정:
  - `run_fullbox_engine()`에 `fallback_fullboxes_df` 인자를 추가했다.
  - `resolve_orders()`가 `prepared_products_df` lookup에 없는 상품은 `fullboxes_master` 기반 fallback lookup으로 한 번 더 찾도록 바꿨다.
  - fallback row는 완박스 관련 필드와 이름/코드를 유지하도록 구성했다.
- 효과:
  - `products_master`에는 없고 `fullboxes_master`에만 있는 완박스 전용 상품도 fullbox 단계까지 이어질 수 있다.

## 보조 수정

### 4. `RawOrderLine` 재입력 처리 버그 수정

- 파일:
  - `packing_engine/run_packing_engine.py`
  - `packing_engine/fixed_box_checker.py`
- 기존 문제:
  - `RawOrderLine` 객체를 그대로 받았을 때 존재하지 않는 `input_name` 속성을 읽고 있었다.
- 수정:
  - `item_text` 우선, 없으면 `raw_text`를 읽도록 변경했다.
- 효과:
  - 이미 파싱된 `RawOrderLine` 객체를 다시 넘겨도 정상 동작한다.

### 5. not found / match 직렬화 필드 정리

- 파일:
  - `packing_engine/run_packing_engine.py`
  - `packing_engine/fixed_box_checker.py`
  - `packing_engine/fixed_box_mix_checker.py`
- 수정:
  - 매칭 실패 시 `input_name` 같은 없는 필드 대신 `item_text` 또는 `raw_input`을 사용하도록 변경했다.
  - 매칭 결과 직렬화 시 `status`, `message`, `match_source`, `match_reason`를 포함하도록 정리했다.
  - 고정 박스 체크 경로도 matcher에 `fullboxes_master`, `packages_master`를 함께 넘기도록 통일했다.
- 효과:
  - 실패 사유와 입력 원문이 더 정확하게 남고, 고정 박스 경로도 일반 엔진과 같은 기준으로 매칭한다.

## 이번 수정으로 해결된 범위

- 박스 미적합 후보 때문에 엔진이 중간에 죽는 문제
- 테스트 경로와 실사용 경로의 매칭 기준 차이
- `fullboxes_master` 전용 상품이 fullbox 단계에서 유실되는 문제
- 일부 입력 객체 처리 시 발생 가능한 속성 참조 오류

## 아직 남아 있는 한계

### 1. 마스터에 아예 없는 상품명

- 예: 현재 샘플 기준으로 마스터에 없는 품목은 여전히 unresolved / not_found로 남는다.
- 이 부분은 코드가 아니라 데이터 보강이 필요하다.

### 2. 일반 혼합 재포장 박스 구성

- 샘플 출하건처럼 서로 다른 여러 상품을 하나의 재포장 박스에 넣는 일반 혼합 박스 로직은 아직 없다.
- 현재 엔진은 주로 상품별 추천 박스 계산과 완박스/잔량 처리에 초점이 맞춰져 있다.

### 3. ambiguous 상품 자동 확정 정책

- 예: 동일 점수 후보가 여러 개면 지금도 자동 확정하지 않는다.
- 이 부분은 코드 우선 / source 우선 / 별도 alias master 같은 추가 정책이 필요하다.

## 테스트 메모

- 수정 전에는 샘플 주문 역산 테스트 시 `repack_engine.py`에서 `boxes_needed=None` 때문에 크래시가 발생했다.
- 수정 후에는 해당 크래시 없이 다음 단계까지 진행할 수 있어야 한다.
- 다만 샘플 출하건과 완전히 같은 결과를 만들려면 데이터 보강과 혼합 재포장 로직이 추가로 필요하다.
