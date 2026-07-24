# ─────────────────────────────────────────────────────────────────────────────
# MA AI Dossier — 프로덕션 이미지
#  stage 1: React SPA 빌드 (frontend/out)
#  stage 2: Python 3.12 + Playwright chromium + Flask/Scheduler
# 프로세스는 fly.toml [processes] (web / scheduler) 로 분리 구동.
# 자격증명은 이미지에 포함하지 않음 — fly secrets (환경변수) 로 주입.
# ─────────────────────────────────────────────────────────────────────────────

FROM node:20-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build   # → /build/out

FROM python:3.12-slim
WORKDIR /app

# 핵심 의존성 + Playwright chromium (HIRA/KEB/대부분 스크레이퍼). 이 단계 실패는 배포 중단.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install chromium --with-deps \
    && rm -rf /root/.cache/pip

# UK MIMS Cloudflare 우회용 Scrapling+camoufox — **완전 non-fatal**: pip/브라우저 설치가
# 실패해도 배포는 계속(IT/국내/캐시 등 나머지 수정 반영). 없으면 UK 만 런타임 비활성(그레이스풀
# — uk_mims.py 가 import 실패 시 [] 반환). scrapling 이 lxml>=6 을 끌어와도 core 와 분리됨.
RUN ( pip install --no-cache-dir "scrapling[fetchers]>=0.4.11" \
      && playwright install firefox --with-deps \
      && scrapling install ) \
    || echo "WARN: scrapling/camoufox 설치 실패 — UK MIMS(Scrapling) 런타임 비활성, 나머지 정상 배포"
RUN rm -rf /root/.cache/pip

COPY . .
COPY --from=frontend /build/out ./frontend/out

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Seoul \
    HOST=0.0.0.0

EXPOSE 5001
# web(Flask) + scheduler(APScheduler) 동시 구동 — sqlite volume 공유 (단일 머신)
CMD ["bash", "scripts/start_production.sh"]
