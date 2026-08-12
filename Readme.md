# OKX 텔레그램 시그널 봇

OKX USDT 무기한 선물의 기술적 신호를 감지해 Telegram으로 알려주는 **GitHub Actions 기반 정보성 알림봇**입니다.  
주문을 실행하지 않으며, OKX 공개 OHLCV 데이터만 사용합니다.

---

## 한눈에 보기

| 항목 | 내용 |
| --- | --- |
| 감시 코인 | BTC, ETH, SOL, HYPE, DOGE, XRP, LIT, SUI, BNB (9개) |
| 신호 종류 | RSI · MACD · Stochastic · Engulfing · EMA Cross · VWAP Cross |
| 1회 검사 | **102건** (코인 × 타임프레임 × 신호 조합) |
| 실행 주기 | 15분마다 (외부 크론 → GitHub Actions) |
| 거래소 | OKX USDT 무기한 선물 (`COIN/USDT:USDT`) |

### 신호별 검사 범위

| 신호 | 타임프레임 | 대상 코인 | 검사 건수 |
| --- | --- | --- | ---: |
| RSI(14) | 15m, 1h, 4h | 9코인 (15m는 BTC·ETH만) | 20 |
| MACD(12,26,9) | 1h, 4h | 9코인 | 18 |
| Stochastic(KDJ 14,3,3) | 1h, 4h | 9코인 | 18 |
| Engulfing | 1h, 4h | 9코인 | 18 |
| EMA Cross(8/21) | 1h, 4h | 9코인 | 18 |
| VWAP Cross | 1h, 4h | BTC, ETH, SOL, BNB, XRP | 10 |

감시 대상은 [main.py](main.py) 상단의 `WATCHLIST`, `RSI_15M_COINS`, `VWAP_COINS`에서 수정할 수 있습니다.

---

## 공통 규칙

모든 신호는 아래 3가지 규칙을 **반드시** 통과해야 알림이 발송됩니다.

### 1. 완성 캔들만 사용

- 진행 중인 캔들은 사용하지 않습니다.
- 캔들 종료 시각 + **30초 여유**(`CANDLE_CLOSE_GRACE_SECONDS`) 후, 확정된 최근 **2개** 완료 캔들로 신호를 판정합니다.

### 2. 중복 알림 방지

- 돌파가 발생한 캔들이 **방금 닫힌 직후 첫 실행**에서만 알림을 보냅니다 (`is_freshly_closed`).
- 15분마다 스캔하므로 1h/4h 신호는 캔들 종료 후 첫 실행(약 정각+1분)에만 1회 발화합니다.

| 타임프레임 | 발화 시점 |
| --- | --- |
| 15m (RSI, BTC·ETH) | 15분 완성봉마다 |
| 1h | 완성봉 종료 후 첫 실행 → 이후 3회 건너뜀 |
| 4h | 완성봉 종료 후 첫 실행 → 이후 15회 건너뜀 |

### 3. 거래량 필터

신호 캔들(돌파가 발생한 최근 완료 캔들)의 거래량이 **직전 20개 완료 캔들 평균 × 배수** 이상이어야 합니다.

| 신호 | 최소 배수 |
| --- | ---: |
| RSI · MACD · Stochastic | 1.1× |
| EMA Cross | 1.2× |
| VWAP Cross | 1.3× + 직전 3봉 평균 초과 |
| Engulfing | 1.4× |

### 신뢰도 태그

- `4h` → 🔥 높은 신뢰도
- `1h` → ⚡️ 중간 신뢰도
- `15m` → 👀 단기 진입 타점 (RSI, BTC·ETH만)

---

## 신호 기준

### RSI (기간 14)

| 방향 | 조건 |
| --- | --- |
| 📈 LONG | 이전 완료 캔들 RSI < 30 → 최근 완료 캔들 RSI ≥ 30 |
| 📉 SHORT | 이전 완료 캔들 RSI > 70 → 최근 완료 캔들 RSI ≤ 70 |

`15m` RSI는 **BTC, ETH만** 감시합니다.

---

### MACD (12, 26, 9)

DIF = MACD선, DEA = Signal선

| 방향 | 조건 |
| --- | --- |
| 📈 LONG | DIF가 DEA를 아래→위로 돌파, DIF·DEA **모두 음수** |
| 📉 SHORT | DIF가 DEA를 위→아래로 돌파, DIF·DEA **모두 양수** |

---

### Stochastic — OKX KDJ (14, 3, 3)

%K = 14주기 Stochastic, %D = %K의 3주기 SMA

| 방향 | 조건 |
| --- | --- |
| 📈 LONG | %K·%D 모두 ≤ 20, %K가 %D를 아래→위로 돌파 |
| 📉 SHORT | %K·%D 모두 ≥ 80, %K가 %D를 위→아래로 돌파 |

---

### Engulfing (품질 필터 적용)

| 방향 | 핵심 조건 |
| --- | --- |
| 📈 Bullish | 음봉→양봉 감싸기, 바디 강도(1h 1.1× / 4h 1.0×), Close > EMA20, 저점이 직전 8/5봉 최저 이하 |
| 📉 Bearish | 양봉→음봉 감싸기, 바디 강도, Close < EMA20, 고점이 직전 8/5봉 최고 이상 |

**추가 필터:** 도지 차단 · 꼬리 과다 차단 · 직전 3봉 내 동일 방향 Engulfing 차단 · 종가가 바디 중간 이상/이하

---

### EMA Cross (8 / 21)

| 방향 | 핵심 조건 |
| --- | --- |
| 📈 Golden Cross | EMA8이 EMA21을 아래→위 돌파, Close > EMA8, 직전 3봉 모두 EMA8 < EMA21 |
| 📉 Dead Cross | EMA8이 EMA21을 위→아래 돌파, Close < EMA8, 직전 3봉 모두 EMA8 > EMA21 |

**추가 필터:** \|EMA8 − EMA21\| ≥ ATR(14) × 0.15 · EMA8 기울기(3봉) 방향 일치

---

### VWAP Cross (BTC · ETH · SOL · BNB · XRP)

| 타임프레임 | VWAP 계산 기간 |
| --- | ---: |
| 1h | 최근 24개 완료 캔들 |
| 4h | 최근 6개 완료 캔들 |

공식: `VWAP = Σ(TypicalPrice × Volume) / Σ(Volume)`  
TypicalPrice = (High + Low + Close) / 3

| 방향 | 핵심 조건 |
| --- | --- |
| 📈 상향 돌파 | Close가 VWAP 아래→위 돌파, 돌파 강도(1h 0.3% / 4h 0.15%), 직전 4봉 모두 VWAP 아래, 양봉 |
| 📉 하향 돌파 | Close가 VWAP 위→아래 돌파, 돌파 강도, 직전 4봉 모두 VWAP 위, 음봉 |

**추가 필터:** 종가가 캔들 상·하단 40% 이내 · 거래량 1.3× + 직전 3봉 평균 초과

---

## Telegram 알림 예시

```
🚨 [1h] RSI 신호 발생⚡️
- 코인: BTC
- 포지션: 📈 LONG
```

```
🚨 [4h] Engulfing 신호 발생🔥
- 코인: SOL
- 포지션: 📈 LONG
```

```
🚨 [4h] EMA Cross 신호 발생🔥
- 코인: BTC
- 포지션: 📈 LONG
```

```
🚨 [1h] VWAP Cross 신호 발생⚡️
- 코인: ETH
- 포지션: 📈 LONG
```

---

## 실행 흐름

한 번의 실행(`python main.py`)은 아래 순서로 **102건**을 처리합니다.

```
RSI (20) → MACD (18) → Stochastic (18)
  → Engulfing (18) → EMA Cross (18) → VWAP Cross (10)
```

각 검사마다:

1. OKX에서 OHLCV 100개를 조회하고 지표를 계산합니다.
2. 완성 캔들 2개로 신호를 판정합니다.
3. 중복 방지 · 거래량 필터를 통과하면 Telegram으로 전송합니다.
4. 검사 사이 `0.5초` 지연으로 API 부하를 줄입니다.

개별 코인/타임프레임에서 오류가 나도 **나머지 검사는 계속** 진행됩니다.

GitHub Actions Summary에는 총 검사·발생·오류 건수와 **신호별 breakdown**, BTC 최근 확정 캔들 RSI/MACD/Stochastic 값이 기록됩니다.

---

## 설정

### 1. Telegram Secrets

GitHub 저장소 → **Settings → Secrets and variables → Actions**

| Secret | 설명 |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | BotFather가 발급한 Bot Token |
| `TELEGRAM_CHAT_ID` | 알림을 받을 채팅 ID |

OKX API Key는 **필요 없습니다** (공개 데이터만 사용).

### 2. 외부 크론 트리거

워크플로우는 `repository_dispatch` 이벤트로 실행됩니다.  
[console.cron-job.org](https://console.cron-job.org/)에서 15분마다 POST 요청을 보냅니다.

| 항목 | 값 |
| --- | --- |
| Cron | `1,16,31,46 * * * *` |
| Timezone | `Asia/Seoul` |
| URL | `https://api.github.com/repos/zqvo04/RSI_signal/dispatches` |

**헤더**

```text
Accept: application/vnd.github+json
Authorization: Bearer <GITHUB_FINE_GRAINED_TOKEN>
X-GitHub-Api-Version: 2026-03-10
Content-Type: application/json
```

**본문**

```json
{
  "event_type": "rsi-signal-check",
  "client_payload": {
    "source": "console.cron-job.org"
  }
}
```

Fine-grained PAT는 `RSI_signal` 저장소만 선택하고, `Contents: Read and write` 권한을 부여합니다.

### 3. 동작 확인

1. GitHub **Actions** → `RSI Telegram Signal Bot` 선택
2. **Run workflow**로 수동 실행
3. **Summary** 탭에서 102건 검사 결과 확인

---

## 로컬 실행

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="your-token"
export TELEGRAM_CHAT_ID="your-chat-id"
python main.py
```

Windows PowerShell:

```powershell
$env:TELEGRAM_BOT_TOKEN = "your-token"
$env:TELEGRAM_CHAT_ID = "your-chat-id"
python main.py
```

---

## 주의사항

- 이 프로젝트는 **정보성 알림 도구**이며, 투자 조언이나 자동 매매 시스템이 아닙니다.
- GitHub Actions 러너 시작이 지연되어도, 완성 캔들만 사용하므로 미완성 데이터가 포함되지 않습니다.
- 거래량 필터 미통과 시 신호는 차단되며, 로그에 `Skipped ... volume below ...`로 기록됩니다.
