# OKX 텔레그램 시그널 봇

> OKX USDT 무기한 선물의 기술적 신호를 감지해 Telegram으로 알려주는 **GitHub Actions 기반 정보성 알림봇**.  
> 주문을 실행하지 않으며, OKX 공개 OHLCV 데이터만 사용한다. OKX API Key 불필요.

| 항목 | 내용 |
| --- | --- |
| 감시 코인 | BTC, ETH, SOL, HYPE, DOGE, XRP, LIT, SUI, BNB (9개) |
| 신호 | 기술적 신호 10종 + 정보성 펀딩비·OI 극단 |
| 타임프레임 | 1h · 4h · 1d (모든 신호 공통) |
| 1회 검사 | **268건** (코인 × 타임프레임 × 신호 + 펀딩/OI 10건) |
| 실행 주기 | 매 15분 정각 (`00`, `15`, `30`, `45`분, 외부 크론 → GitHub Actions) |
| 거래소 | OKX USDT 무기한 선물 (`COIN/USDT:USDT`) |

---

## 목차

1. [개요](#1-개요)
2. [공통 규칙과 필터](#2-공통-규칙과-필터)
3. [신호 목록](#3-신호-목록)
4. [펀딩비 · OI 극단 (정보성)](#4-펀딩비--oi-극단-정보성)
5. [실행 흐름과 알림](#5-실행-흐름과-알림)
6. [설치 · 활용](#6-설치--활용)
7. [주의사항](#7-주의사항)
8. [관련 문서](#8-관련-문서)

---

## 1. 개요

- 15분마다 9개 코인 × 3개 타임프레임 × 신호 10종을 검사한다.
- 조건을 통과한 신호는 실행 종료 시 **요약 메시지 1개**로 Telegram에 전송된다.
- 펀딩비·OI 극단은 매매 신호가 아니며 **별도 메시지**로 전송된다.
- 감시 대상은 [main.py](main.py) 상단의 `WATCHLIST`, `VWAP_COINS`(펀딩/OI 대상 `DERIV_COINS`도 동일)에서 수정한다.

---

## 2. 공통 규칙과 필터

> 상세 내용은 **[공통 규칙과 필터](docs/rules.md)** 문서 참고.

- **완성 캔들만 사용**: 캔들 종료 + 30초 여유가 지난 캔들만 판정.
- **1회만 발화**: 신호 캔들이 닫힌 직후 첫 실행에서만 알림.
- **신뢰도 태그**: 🏆 1d · 🔥 4h · ⚡️ 1h.
- **그룹별 필터**

| 그룹 | 거래량 필터 | 상위 TF 방향 필터 |
| --- | --- | --- |
| CORE | 신호별 배수 (4h ×0.8 완화, 1d 제외) | 미적용 |
| SECONDARY | 1.5× (4h 1.2×, 1d 제외) | 1h만 적용 (4h 방향 일치) |
| 구조 신호 | 미적용 | 미적용 |

---

## 3. 신호 목록

> 각 신호 이름을 누르면 해당 신호의 상세 조건 문서로 이동한다.

| 신호 | 그룹 | 요약 | 검사 건수 |
| --- | --- | --- | ---: |
| [RSI(14)](docs/signals/rsi.md) | CORE | 30 상향 / 70 하향 복귀 | 27 |
| [RSI 다이버전스](docs/signals/rsi-divergence.md) | 구조 | TradingView 방식, 일반·히든 × 롱·숏 | 27 |
| [피보나치 되돌림](docs/signals/fibonacci.md) | 구조 | 0.5–0.618 구간 반전 마감 | 27 |
| [MACD(12,26,9)](docs/signals/macd.md) | CORE | 0선 아래 골든 / 위 데드 크로스 | 27 |
| [Stochastic(KDJ 14,3,3)](docs/signals/stochastic.md) | SECONDARY | 20 이하 / 80 이상 %K·%D 크로스 | 27 |
| [Engulfing](docs/signals/engulfing.md) | CORE | 품질 필터 적용 장악형 | 27 |
| [EMA Cross(8/21)](docs/signals/ema-cross.md) | CORE | ATR·기울기 필터 크로스 | 27 |
| [VWAP Cross](docs/signals/vwap-cross.md) | CORE | VWAP 돌파 (BTC·ETH·SOL·BNB·XRP) | 15 |
| [Supertrend(ATR 10, 3.0)](docs/signals/supertrend.md) | SECONDARY | Supertrend 돌파 | 27 |
| [BB Squeeze Breakout](docs/signals/bb-squeeze.md) | SECONDARY | 스퀴즈 후 밴드 돌파 | 27 |

RSI 다이버전스 · 피보나치의 설계 배경은 [설계서](docs/plan_rsi_divergence_fib.md) 참고.

---

## 4. 펀딩비 · OI 극단 (정보성)

> 상세 내용은 **[펀딩비 · OI 극단](docs/derivatives.md)** 문서 참고.

- **매매 신호가 아님.** 방향 없이 "파생 지표가 평소와 크게 다르다"는 사실만 알림.
- 대상: BTC · ETH · SOL · BNB · XRP. 검사 10건/실행.
- 판정: 코인 자신의 최근 이력 대비 순위 (펀딩 꼬리 5%, OI 꼬리 1%).
- 재발화 차단: 펀딩 직전 1회, OI 직전 24시간.

---

## 5. 실행 흐름과 알림

> 상세 내용은 **[실행 흐름과 알림](docs/execution.md)** 문서 참고.

```
RSI → RSI 다이버전스 → 피보나치 → MACD → Stochastic → Engulfing
  → EMA Cross → VWAP Cross → Supertrend → BB Squeeze → 펀딩비 → OI
```

```
🚨 신호 요약 (UTC 04:00)
📈 BTC LONG
 🔥 4h: RSI 일반 Div
 ⚡️ 1h: 피보나치 되돌림 지지 반등(0.5~0.618), EMA Cross
```

---

## 6. 설치 · 활용

> 상세 내용은 **[설치 · 활용 가이드](docs/setup.md)** 문서 참고.

1. 저장소 Fork → Actions 활성화
2. Telegram Bot 생성 → `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 확보
3. GitHub Secrets 등록
4. 외부 크론(`0,15,30,45 * * * *`)으로 `repository_dispatch` 호출
5. 수동 실행으로 첫 동작 확인

로컬 실행:

```bash
pip install -r requirements.txt
python main.py                              # TELEGRAM_* 환경변수 필요
python -m unittest test_signal_policy -v    # 네트워크 불필요
```

---

## 7. 주의사항

- 이 프로젝트는 **정보성 알림 도구**이며, 투자 조언이나 자동 매매 시스템이 아니다.
- 신호 임계값은 백테스트로 검증된 값이 아니다.
- RSI는 최근 100봉으로 계산하므로 TradingView(전체 이력) 값과 소수점이 조금 다를 수 있고, 드물게 피벗 판정이 달라질 수 있다.
- 저장소가 public이면 Actions 로그와 Summary(발생 신호 포함)를 누구나 볼 수 있다. 비밀값은 Secrets로만 관리한다.

---

## 8. 관련 문서

| 문서 | 내용 |
| --- | --- |
| [docs/rules.md](docs/rules.md) | 완성 캔들 · 1회 발화 · 신뢰도 · 그룹별 필터 |
| [docs/signals/](docs/signals/) | 신호 10종 개별 문서 |
| [docs/derivatives.md](docs/derivatives.md) | 펀딩비 · OI 극단, 백테스트, 한계 |
| [docs/execution.md](docs/execution.md) | 실행 흐름, Telegram 메시지, Actions Summary |
| [docs/setup.md](docs/setup.md) | Fork 설치, 외부 크론, 로컬 실행 |
| [docs/plan_rsi_divergence_fib.md](docs/plan_rsi_divergence_fib.md) | RSI 다이버전스 · 피보나치 설계서 |
