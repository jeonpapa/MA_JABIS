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

# Playwright chromium + 시스템 의존성 (HIRA/KEB/스크레이퍼 headless 다운로드용)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install chromium --with-deps \
    && rm -rf /root/.cache/pip

COPY . .
COPY --from=frontend /build/out ./frontend/out

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Seoul \
    HOST=0.0.0.0

EXPOSE 5001
# web(Flask) + scheduler(APScheduler) 동시 구동 — sqlite volume 공유 (단일 머신)
CMD ["bash", "scripts/start_production.sh"]
