# Master Matching Policy

## 목적

`products_master` 와 `fullboxes_master` 가 함께 운영될 때,
상품 매칭과 완박스 가능 여부 판단을 분리해서 일관되게 처리하기 위한 규칙을 정리한다.

## 기본 원칙

1. `products_master` 는 상품의 기준 마스터다.
2. `fullboxes_master` 는 완박스 포장 규칙 마스터다.
3. 상품 존재 여부는 `products_master` 기준으로 판단한다.
4. 완박스 가능 여부는 `fullboxes_master` 존재 여부로 판단한다.

## 처리 규칙

### 1) `products_master` O / `fullboxes_master` O

- 정상 상품으로 매칭한다.
- 완박스 계산 대상이 된다.

### 2) `products_master` O / `fullboxes_master` X

- 정상 상품으로 매칭한다.
- 오류로 처리하지 않는다.
- 완박스 스펙이 없으므로 재포장/일반 박스 계산으로 넘긴다.

### 3) `products_master` X / `fullboxes_master` O

- 운영 원칙상 없어야 하는 데이터 상태다.
- 현재 엔진은 fallback 으로 매칭할 수는 있지만, 데이터 정합성 이슈로 본다.
- 장기적으로는 `products_master` 에도 동일 상품이 반드시 있어야 한다.

### 4) `products_master` X / `fullboxes_master` X

- 미등록 상품으로 처리한다.

## 매칭 우선순위

동일 점수 후보가 여러 개일 때는 아래 우선순위를 적용한다.

1. `products_master`
2. `fullboxes_master`
3. `packages_master`

즉, 같은 이름이 `products_master` 와 `fullboxes_master` 에 동시에 있어도
`products_master` 쪽을 먼저 확정한다.

## 코드 반영 내용

- `matcher.py`
  - 동일 점수 후보가 있으면 source 우선순위를 먼저 적용한다.
  - `products` 와 `fullboxes` 가 같은 점수로 동시에 잡혀도 `products` 를 우선 선택한다.
- `fullbox_engine.py`
  - `products_master` 에 완박스 스펙이 없으면 오류가 아니라 `NO_FULLBOX_SPEC` 으로 재포장 경로에 넘긴다.

## 기대 효과

- `products_master` 와 `fullboxes_master` 중복 등록 때문에 `ambiguous` 가 불필요하게 발생하는 문제를 줄인다.
- 완박스 요청이 들어와도 `products_master` 에만 있는 상품은 오류 없이 재포장으로 처리할 수 있다.
- 데이터 정합성 문제와 계산 정책 문제를 분리해서 운영할 수 있다.
