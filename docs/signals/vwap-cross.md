# VWAP Cross

> [← README](../../Readme.md#3-신호-목록) · 공통 규칙: [rules.md](../rules.md)

| 항목 | 내용 |
| --- | --- |
| 그룹 | CORE |
| 대상 | BTC · ETH · SOL · BNB · XRP (`VWAP_COINS`) × 1h·4h·1d (15건) |
| 거래량 필터 | 1h 1.0× · 4h 0.8× · 1d 제외 |
| 상위 TF 필터 | 미적용 |

`VWAP = Σ(TypicalPrice × Volume) / Σ(Volume)`, `TypicalPrice = (High + Low + Close) / 3`

## 1. 계산 기간

| 타임프레임 | VWAP 계산 기간 | 돌파 강도 |
| --- | ---: | ---: |
| 1h | 최근 24봉 | 0.3% |
| 4h | 최근 6봉 | 0.15% |
| 1d | 최근 7봉 | 0.15% |

## 2. 조건

| 방향 | 핵심 조건 |
| --- | --- |
| 📈 상향 돌파 | 종가가 VWAP 아래→위 돌파, 돌파 강도 이상, 직전 4봉 모두 VWAP 아래, 양봉 |
| 📉 하향 돌파 | 종가가 VWAP 위→아래 돌파, 돌파 강도 이상, 직전 4봉 모두 VWAP 위, 음봉 |
