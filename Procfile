# 배포 시 web+scheduler 가 함께 구동돼야 자동화가 보장된다.
# ⚠️ sqlite 공유 때문에 두 프로세스는 **같은 머신/볼륨**이어야 함
#    → Fly.io 는 Dockerfile CMD(scripts/start_production.sh) 가 둘을 동시 구동 (이 파일 무시됨).
#    → Render/Railway 등 Procfile 호스트에서도 web 하나로 동시 구동:
web: bash scripts/start_production.sh
