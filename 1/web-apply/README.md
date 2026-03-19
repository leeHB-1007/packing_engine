# Web Apply

간단한 웹 테스트 화면입니다.

## 실행

```bash
cd /Users/leehyunbin/Downloads/packing_engine
source .venv/bin/activate
uvicorn packing_api:app --reload
```

브라우저에서 아래 주소로 접속합니다.

```text
http://127.0.0.1:8000/app
```

## 구성

- `index.html`
  - 주문 입력 폼과 결과 미리보기 화면
- `styles.css`
  - 간단한 레이아웃과 스타일
- `app.js`
  - `/pack` API 호출과 결과 렌더링

## 현재 범위

- 주문 텍스트 입력
- 출고 방법 선택
- 패킹리스트 여부 선택
- 정규화 주문 텍스트 표시
- 엔진 결과 텍스트 표시
- `Sulu Med` 형식 엑셀 다운로드

엑셀 다운로드나 상세 결과 테이블은 아직 붙이지 않았고,
현재는 기존 패킹 엔진 API를 웹에서 바로 써보는 용도의 최소 버전입니다.
