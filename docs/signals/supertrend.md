# Supertrend (ATR 10, 배수 3.0)

> [← README](../../Readme.md#3-신호-목록) · 공통 규칙: [rules.md](../rules.md)

| 항목 | 내용 |
| --- | --- |
| 그룹 | SECONDARY |
| 대상 | 9코인 × 1h·4h·1d (27건) |
| 거래량 필터 | 1h 1.5× · 4h 1.2× · 1d 제외 |
| 상위 TF 필터 | 1h만 적용 (4h 방향 일치) |
| 캔들 확인 | OKX `confirm = "1"` 캔들만 사용 |

`Upper Band = (High + Low) / 2 + ATR(10) × 3.0`  
`Lower Band = (High + Low) / 2 − ATR(10) × 3.0`

## 1. 조건

| 방향 | 핵심 조건 |
| --- | --- |
| 📈 LONG | 종가가 Supertrend를 아래→위 돌파하고 양봉 |
| 📉 SHORT | 종가가 Supertrend를 위→아래 돌파하고 음봉 |

## 2. 추가 필터

돌파 강도 ≥ ATR(10) × 0.2 · 직전 3봉이 돌파 전 추세 방향 유지
