# EMA Cross (8 / 21)

> [← README](../../Readme.md#3-신호-목록) · 공통 규칙: [rules.md](../rules.md)

| 항목 | 내용 |
| --- | --- |
| 그룹 | CORE |
| 대상 | 9코인 × 1h·4h·1d (27건) |
| 거래량 필터 | 1h 1.0× · 4h 0.8× · 1d 제외 |
| 상위 TF 필터 | 미적용 |

## 1. 조건

| 방향 | 핵심 조건 |
| --- | --- |
| 📈 Golden Cross | EMA8이 EMA21을 아래→위 돌파, 종가 > EMA8, 직전 3봉 모두 EMA8 < EMA21 |
| 📉 Dead Cross | EMA8이 EMA21을 위→아래 돌파, 종가 < EMA8, 직전 3봉 모두 EMA8 > EMA21 |

## 2. 추가 필터

\|EMA8 − EMA21\| ≥ ATR(14) × 0.15 · EMA8 기울기(3봉) 방향 일치
