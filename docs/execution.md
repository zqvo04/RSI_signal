# 실행 흐름과 알림

> [← README](../Readme.md)

## 목차

1. [실행 흐름](#1-실행-흐름)
2. [Telegram 알림 예시](#2-telegram-알림-예시)
3. [GitHub Actions Summary](#3-github-actions-summary)

---

## 1. 실행 흐름

한 번의 실행(`python main.py`)은 코인마다 아래 순서로 검사해 총 **268건**을 처리합니다.

```
RSI (27) → RSI 다이버전스 (27) → 피보나치 (27)   ← 같은 RSI 데이터 재사용
  → MACD (27) → Stochastic (27) → Engulfing (27)
  → EMA Cross (27) → VWAP Cross (15) → Supertrend (27) → BB Squeeze (27)
  → 펀딩비 (5) → OI (5)   ← 5개 코인, 코인별 마지막에 실행
```

1. OKX에서 최근 OHLCV 100개를 조회하고 지표를 계산합니다. RSI 다이버전스와 피보나치는 RSI 데이터를 재사용하므로 추가 API 호출이 없습니다.
2. 완성 캔들로 신호를 판정하고, 1회 발화 규칙과 그룹별 필터를 적용합니다 ([공통 규칙](rules.md)).
3. 통과한 신호는 대기열에 담습니다. 검사 사이에는 `0.5초` 지연이 있습니다.
4. 모든 검사가 끝나면 신호 대기열을 **요약 메시지 1개**로 Telegram에 보냅니다. 펀딩비·OI 정보가 있으면 **별도 메시지 1개**가 이어서 전송됩니다. 실행 중 오류로 중단되어도 그때까지 찾은 내용은 `finally`에서 전송됩니다.

개별 코인·타임프레임·신호에서 오류가 나도 **나머지 검사는 계속** 진행됩니다.

## 2. Telegram 알림 예시

```
🚨 신호 요약 (UTC 04:00)
📈 BTC LONG
 🔥 4h: RSI 일반 Div
 ⚡️ 1h: 피보나치 되돌림 지지 반등(0.5~0.618), EMA Cross
📉 SOL SHORT
 ⚡️ 1h: RSI 히든 Div
```

펀딩비·OI 극단이 있으면 위 메시지와 **따로** 아래 형식이 전송됩니다 ([상세](derivatives.md)).

```
📊 파생 지표 극단 (UTC 08:00)
BTC
 · 펀딩비 +0.081% (직전 89회 중 상위 2%) — 롱 쏠림
```

## 3. GitHub Actions Summary

- 총 검사 · 발생 · 오류 건수
- 신호별 검사/발생 건수 (펀딩비·OI 포함)
- BTC 최근 확정 캔들의 RSI · MACD · Stochastic 값
