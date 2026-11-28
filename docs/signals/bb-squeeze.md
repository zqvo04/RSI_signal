# BB Squeeze Breakout

> [← README](../../Readme.md#3-신호-목록) · 공통 규칙: [rules.md](../rules.md)

| 항목 | 내용 |
| --- | --- |
| 그룹 | SECONDARY |
| 대상 | 9코인 × 1h·4h·1d (27건) |
| 거래량 필터 | 1h 1.5× · 4h 1.2× · 1d 제외 |
| 상위 TF 필터 | 1h만 적용 (4h 방향 일치) |
| 캔들 확인 | OKX `confirm = "1"` 캔들만 사용 |

20주기 SMA와 표준편차(σ)로 밴드를 계산합니다. `Upper = SMA20 + 2σ`, `Lower = SMA20 − 2σ`

## 1. 조건

| 방향 | 핵심 조건 |
| --- | --- |
| 📈 LONG | 직전 4봉이 Upper Band 이하, 종가가 Upper Band를 상향 돌파하고 양봉 |
| 📉 SHORT | 직전 4봉이 Lower Band 이상, 종가가 Lower Band를 하향 돌파하고 음봉 |

## 2. Squeeze 조건

신호봉 직전 10봉 중 8봉 이상이 `BB Width ≤ ATR(14) × 1.5`

## 3. 추가 필터

돌파 강도 ≥ ATR(14) × 0.15
