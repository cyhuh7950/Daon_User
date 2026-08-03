# Daon User 운영 Web/API Compose

기존 `deploy/r1-m5-01`·`deploy/r1-m5-02` 검증 Compose와 분리된 운영 앱 패키지다. Web 외부 포트는 `3330`이며 Web/API는 전용 Compose network와 API runtime volume을 사용하고 외부 `proxy-network`에 연결한다.

브라우저 요청은 Web의 same-origin BFF가 처리하며 `DAON_API_INTERNAL_URL`은 서버 환경변수로만 주입한다. Upstage 및 객체 저장소 자격은 파일 secret reference로만 주입한다. `DAON_CLOUD_DATABASE_DSN`, Gateway URL, trusted proxy IP, object-storage endpoint/bucket과 세 secret 파일은 배포자가 제공해야 한다. 기존 공용 자원은 변경하지 않는다.
