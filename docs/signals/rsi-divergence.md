# RSI 다이버전스 (TradingView 방식)

> [← README](../../Readme.md#3-신호-목록) · 공통 규칙: [rules.md](../rules.md)

| 항목 | 내용 |
| --- | --- |
| 그룹 | 구조 신호 |
| 대상 | 9코인 × 1h·4h·1d (27건) |
| 종류 | 일반·히든 × LONG·SHORT (4종) |
| 거래량 · 상위 TF 필터 | 미적용 |
| 설계서 | [plan_rsi_divergence_fib.md](../plan_rsi_divergence_fib.md) |

TradingView 기본 **RSI Divergence Indicator**와 같은 방식입니다. **RSI(14)에서 스윙(피벗)을 찾고, 그 봉의 가격(저가/고가)을 비교**합니다.

## 1. 피벗 정의 (좌 5봉 / 우 3봉)

- 피벗 저점: RSI가 왼쪽 5봉보다 낮고, 오른쪽 3봉보다 낮거나 같음
- 피벗 고점: RSI가 왼쪽 5봉보다 높고, 오른쪽 3봉보다 높거나 같음
- 오른쪽 3봉이 닫혀야 피벗이 확정됩니다. 그래서 신호는 **스윙 봉보다 3봉 늦게** 나갑니다. 1h는 3시간, 4h는 12시간, 1d는 3일입니다.

## 2. 판정

연속한 두 피벗(직전 피벗 → 방금 확정된 피벗)만 비교합니다.

| 종류 | 방향 | 가격 | RSI |
| --- | --- | --- | --- |
| 일반 상승 | 📈 LONG | 저점 하락 (LL) | 저점 상승 (HL) |
| 히든 상승 | 📈 LONG | 저점 상승 (HL) | 저점 하락 (LL) |
| 일반 하락 | 📉 SHORT | 고점 상승 (HH) | 고점 하락 (LH) |
| 히든 하락 | 📉 SHORT | 고점 하락 (LH) | 고점 상승 (HH) |

- 두 피벗 간격은 TradingView Range 5–60과 같습니다. 실제 봉 간격으로는 6–61봉입니다.
- 값이 같으면(동률) 신호를 내지 않습니다.
- 저점 쪽과 고점 쪽이 같은 봉에서 동시에 확정되면 두 신호 모두 보냅니다.

## 3. TradingView 대조

RSI Divergence Indicator → Pivot Lookback Left `5`, Right `3`, Range `5`–`60`, Hidden Bullish/Bearish 표시 ON.  
TradingView는 라벨을 스윙 봉에 표시하므로, 봇 알림 시각보다 3봉 앞에 라벨이 보이면 일치하는 것입니다.
