# Vercel Deploy

## 준비된 파일

- `app.py`
  - Vercel용 FastAPI entrypoint
- `api/index.py`
  - Vercel Python Function entrypoint
- `requirements.txt`
  - 런타임 의존성
- `.python-version`
  - Python 3.12 지정
- `vercel.json`
  - Vercel 기본 설정 파일

## 배포 순서

```bash
cd /Users/leehyunbin/Downloads/packing_engine
git add .
git commit -m "Prepare Vercel deployment"
git push origin main
```

CLI 배포 시에는 먼저 Vercel CLI를 최신으로 올리는 것을 권장합니다.

```bash
npm i -g vercel@latest
vercel --version
```

그 다음 Vercel에서:

1. New Project
2. GitHub 저장소 `packing_engine` 선택
3. Framework Preset은 비워두거나 자동 감지 그대로 진행
4. Root Directory는 저장소 루트(`/`) 그대로 사용
5. Environment Variables가 필요하면 추가
   - `PUBLIC_SERVER_URL=https://<your-project>.vercel.app`
6. Deploy

## 로컬 확인

```bash
cd /Users/leehyunbin/Downloads/packing_engine
source .venv/bin/activate
uvicorn app:app --reload
```

Vercel 함수 엔트리포인트 확인:

```bash
.venv/bin/python -c "import app; print(type(app.app).__name__)"
```

## 접속 경로

- `/app`
  - 웹 UI
- `/pack`
  - 결과 조회 API
- `/pack/export-xlsx`
  - 엑셀 다운로드 API

## 참고

- Vercel 환경에서는 함수 파일시스템이 기본적으로 읽기 전용이므로,
  엑셀 다운로드용 임시 파일은 `/tmp/packing_engine_exports` 아래에 생성되도록 처리되어 있습니다.
- 현재 프로젝트는 Vercel의 FastAPI zero-config 방식을 기준으로 루트 `app.py`를 엔트리포인트로 사용합니다.
- `PUBLIC_SERVER_URL`을 넣어두면 OpenAPI 문서의 서버 주소가 실제 배포 주소로 보입니다.
