# Stochastic: OKX KDJ (14, 3, 3)

> [← README](../../Readme.md#3-신호-목록) · 공통 규칙: [rules.md](../rules.md)

| 항목 | 내용 |
| --- | --- |
| 그룹 | SECONDARY |
| 대상 | 9코인 × 1h·4h·1d (27건) |
| 거래량 필터 | 1h 1.5× · 4h 1.2× · 1d 제외 |
| 상위 TF 필터 | 1h만 적용 (4h 방향 일치) |

%K = 14주기 Stochastic, %D = %K의 3주기 SMA (J는 사용하지 않음)

## 조건

| 방향 | 조건 |
| --- | --- |
| 📈 LONG | %K·%D 모두 ≤ 20, %K가 %D를 아래→위로 돌파 |
| 📉 SHORT | %K·%D 모두 ≥ 80, %K가 %D를 위→아래로 돌파 |
