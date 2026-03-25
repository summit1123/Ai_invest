# FINANCE_DISCORD_ENV_SNIPPET_V1.md

## 목적
Finance Discord alerts 연결을 위해 `.env`에 추가할 최소 설정값을 정리한다.

## 추가할 값
아래 2개를 `.env`에 추가하면 된다.

```env
SEND_DISCORD=true
DISCORD_WEBHOOK_FINANCE_ALERTS=여기에_finance_alerts_webhook_url
```

## 권장
- webhook URL은 코드/문서에 하드코딩하지 않는다
- `.env`에만 저장한다
- 필요 시 webhook 재생성 가능하게 관리한다

## 현재 연결 범위
- `PAUSE`
- `RECON_FAIL`

## 다음 연결 예정
- `RESUME`

## 한 줄 결론
Finance Discord alerts를 켜려면 우선 `.env`에 `SEND_DISCORD`와 `DISCORD_WEBHOOK_FINANCE_ALERTS`만 추가하면 된다.
