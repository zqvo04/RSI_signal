# OKX 텔레그램 시그널 봇

OKX USDT 무기한 선물의 기술적 신호를 감지해 Telegram으로 알려주는 **GitHub Actions 기반 정보성 알림봇**입니다.  
주문을 실행하지 않으며, OKX 공개 OHLCV 데이터만 사용합니다. OKX API Key는 필요 없습니다.

---

## 한눈에 보기

| 항목 | 내용 |
| --- | --- |
| 감시 코인 | BTC, ETH, SOL, HYPE, DOGE, XRP, LIT, SUI, BNB (9개) |
| 신호 종류 | RSI · **RSI 다이버전스** · **피보나치 되돌림** · MACD · Stochastic · Engulfing · EMA Cross · VWAP Cross · Supertrend · BB Squeeze (10종) |
| 타임프레임 | 1h · 4h · 1d (모든 신호 공통) |
| 1회 검사 | **258건** (코인 × 타임프레임 × 신호) |
| 실행 주기 | 매 15분 정각 (`00`, `15`, `30`, `45`분, 외부 크론 → GitHub Actions) |
| 거래소 | OKX USDT 무기한 선물 (`COIN/USDT:USDT`) |

### 신호별 검사 범위

| 신호 | 그룹 | 대상 코인 | 검사 건수 |
| --- | --- | --- | ---: |
| RSI(14) | CORE | 9코인 | 27 |
| RSI 다이버전스 (일반·히든 / 롱·숏) | 구조 신호 | 9코인 | 27 |
| 피보나치 되돌림 0.5–0.618 (롱·숏) | 구조 신호 | 9코인 | 27 |
| MACD(12,26,9) | CORE | 9코인 | 27 |
| Stochastic(KDJ 14,3,3) | SECONDARY | 9코인 | 27 |
| Engulfing | CORE | 9코인 | 27 |
| EMA Cross(8/21) | CORE | 9코인 | 27 |
| VWAP Cross | CORE | BTC, ETH, SOL, BNB, XRP | 15 |
| Supertrend (ATR 10, 3.0) | SECONDARY | 9코인 | 27 |
| BB Squeeze Breakout | SECONDARY | 9코인 | 27 |

감시 대상은 [main.py](main.py) 상단의 `WATCHLIST`, `VWAP_COINS`에서 수정할 수 있습니다.

---

## 공통 규칙 (모든 신호)

### 1. 완성 캔들만 사용

- 진행 중인 캔들은 사용하지 않습니다. 캔들 종료 시각 + **30초 여유**(`CANDLE_CLOSE_GRACE_SECONDS`)가 지난 캔들만 완성 캔들로 봅니다.
- Supertrend · BB Squeeze · 상위 TF 방향 판정은 OKX `/api/v5/market/history-candles`의 `confirm = "1"` 캔들만 추가로 확인합니다.

### 2. 1회만 발화 (중복 방지)

신호 캔들이 **방금 닫힌 직후 첫 실행**에서만 알림을 보냅니다 (`is_freshly_closed`).

| 타임프레임 | 발화 시점 |
| --- | --- |
| 1h | 완성봉 종료 후 첫 실행 → 이후 3회 건너뜀 |
| 4h | 완성봉 종료 후 첫 실행 → 이후 15회 건너뜀 |
| 1d | OKX 일봉(`1Dutc`, UTC 00:00 = KST 09:00 마감) 종료 후 첫 실행 → 이후 95회 건너뜀 |

GitHub Actions 대기 시간 때문에 실제 시작 시각은 몇 분 늦어질 수 있습니다.

### 3. 신뢰도 태그

| 타임프레임 | 태그 |
| --- | --- |
| 1d | 🏆 최고 신뢰도 |
| 4h | 🔥 높은 신뢰도 |
| 1h | ⚡️ 중간 신뢰도 |

---

## 그룹별 필터

| 그룹 | 거래량 필터 | 상위 TF 방향 필터 |
| --- | --- | --- |
| CORE | 신호별 배수 적용 | 미적용 |
| SECONDARY | 1.5× | 1h만 적용 (4h 방향 일치) |
| 구조 신호 (RSI 다이버전스 · 피보나치) | 미적용 | 미적용 |

구조 신호는 신호 캔들이 스윙 봉이 아니라 "스윙 확정 봉"이라 거래량 비교의 의미가 맞지 않고, 일반 다이버전스는 본래 역추세 신호라 추세 필터를 걸지 않습니다.

### 거래량 필터

`신호 캔들 거래량 >= 직전 20개 완료 캔들 평균 거래량 × 최소 배수`

| 신호 | 최소 배수 |
| --- | ---: |
| RSI · MACD | 0.9× |
| EMA Cross · VWAP Cross | 1.0× |
| Engulfing | 1.2× |
| Stochastic · Supertrend · BB Squeeze | 1.5× |

미통과 신호는 차단되고 로그에 `Skipped ... volume below ...`로 남습니다.

### 상위 TF 방향 필터 (SECONDARY)

- `HIGHER_TF_MAP = {1h: 4h}`: 1h SECONDARY 신호는 4h 방향과 일치해야 통과합니다.
- 4h · 1d SECONDARY는 단독 신뢰로 보고 우회 통과합니다.
- 4h 방향은 최근 완료 캔들 기준입니다.
  - **LONG**: 종가 > EMA21 그리고 EMA8 ≥ EMA21
  - **SHORT**: 종가 < EMA21 그리고 EMA8 ≤ EMA21
  - 그 외는 중립
- 중립 · 데이터 부족 · 조회 오류는 모두 **차단**(fail-closed)합니다.

---

## 신호 기준

### RSI (기간 14)

| 방향 | 조건 |
| --- | --- |
| 📈 LONG | 이전 완료 캔들 RSI < 30 → 최근 완료 캔들 RSI ≥ 30 |
| 📉 SHORT | 이전 완료 캔들 RSI > 70 → 최근 완료 캔들 RSI ≤ 70 |

---

### RSI 다이버전스 (TradingView 방식)

TradingView 기본 **RSI Divergence Indicator**와 같은 방식입니다. **RSI(14)에서 스윙(피벗)을 찾고, 그 봉의 가격(저가/고가)을 비교**합니다.

**피벗 정의** (좌 5봉 / 우 3봉)

- 피벗 저점: RSI가 왼쪽 5봉보다 낮고, 오른쪽 3봉보다 낮거나 같음
- 피벗 고점: RSI가 왼쪽 5봉보다 높고, 오른쪽 3봉보다 높거나 같음
- 오른쪽 3봉이 닫혀야 피벗이 확정됩니다. 그래서 신호는 **스윙 봉보다 3봉 늦게** 나갑니다. 1h는 3시간, 4h는 12시간, 1d는 3일입니다.

**판정**: 연속한 두 피벗(직전 피벗 → 방금 확정된 피벗)만 비교합니다.

| 종류 | 방향 | 가격 | RSI |
| --- | --- | --- | --- |
| 일반 상승 | 📈 LONG | 저점 하락 (LL) | 저점 상승 (HL) |
| 히든 상승 | 📈 LONG | 저점 상승 (HL) | 저점 하락 (LL) |
| 일반 하락 | 📉 SHORT | 고점 상승 (HH) | 고점 하락 (LH) |
| 히든 하락 | 📉 SHORT | 고점 하락 (LH) | 고점 상승 (HH) |

- 두 피벗 간격은 TradingView Range 5–60과 같습니다. 실제 봉 간격으로는 6–61봉입니다.
- 값이 같으면(동률) 신호를 내지 않습니다.
- 저점 쪽과 고점 쪽이 같은 봉에서 동시에 확정되면 두 신호 모두 보냅니다.

**TradingView 대조 설정**: RSI Divergence Indicator → Pivot Lookback Left `5`, Right `3`, Range `5`–`60`, Hidden Bullish/Bearish 표시 ON.  
TradingView는 라벨을 스윙 봉에 표시하므로, 봇 알림 시각보다 3봉 앞에 라벨이 보이면 일치하는 것입니다.

---

### 피보나치 되돌림 (0.5–0.618)

**가격 스윙**(저가·고가 피벗, 좌 5 / 우 3)으로 직전 파동을 잡고, 되돌림이 0.5–0.618 구간에 닿은 뒤 반대로 마감하는 **첫 캔들**에서 신호를 보냅니다.

| 방향 | 셋업 | 발화 캔들 |
| --- | --- | --- |
| 📈 LONG | 마지막 스윙이 **고점**: 저점(L) → 고점(H) 상승 후 되돌림 중 | 저가 ≤ 0.5 레벨 · 종가 > 0.618 레벨 · 양봉 |
| 📉 SHORT | 마지막 스윙이 **저점**: 고점(H) → 저점(L) 하락 후 반등 중 | 고가 ≥ 0.5 레벨 · 종가 < 0.618 레벨 · 음봉 |

레벨 계산 (파동 폭 `R = H − L`)

| 레벨 | LONG | SHORT |
| --- | --- | --- |
| 0.5 | H − 0.5R | L + 0.5R |
| 0.618 | H − 0.618R | L + 0.618R |
| 0.786 (무효선) | H − 0.786R | L + 0.786R |

**예시 (LONG)**: L=100, H=200

- 구간은 150 ~ 138.2, 무효선은 121.4입니다.
- 저가 140, 종가 152인 양봉이 처음 나오면 → 📈 LONG

**셋업 무효 조건**

- 파동 폭 < ATR(14) × 3 (작은 파동 무시)
- 파동 간격 > 60봉
- 스윙 이후 0.786 레벨 밖으로 종가가 마감
- 스윙 이후 고점(LONG) / 저점(SHORT)을 갱신

**참고**

- 같은 스윙에서는 최초 1회만 발화합니다.
- 되돌림 중 새 스윙 저점이 확정되면 구조가 바뀌어 반대 방향(SHORT) 셋업으로 넘어갈 수 있습니다.

---

### MACD (12, 26, 9)

DIF = MACD선, DEA = Signal선

| 방향 | 조건 |
| --- | --- |
| 📈 LONG | DIF가 DEA를 아래→위로 돌파, DIF·DEA **모두 음수** |
| 📉 SHORT | DIF가 DEA를 위→아래로 돌파, DIF·DEA **모두 양수** |

---

### Stochastic: OKX KDJ (14, 3, 3)

%K = 14주기 Stochastic, %D = %K의 3주기 SMA (J는 사용하지 않음)

| 방향 | 조건 |
| --- | --- |
| 📈 LONG | %K·%D 모두 ≤ 20, %K가 %D를 아래→위로 돌파 |
| 📉 SHORT | %K·%D 모두 ≥ 80, %K가 %D를 위→아래로 돌파 |

---

### Engulfing (품질 필터 적용)

| 방향 | 핵심 조건 |
| --- | --- |
| 📈 Bullish | 음봉→양봉 감싸기, 종가 > EMA20, 저가가 직전 N봉 최저가 이하(0.5% 허용) |
| 📉 Bearish | 양봉→음봉 감싸기, 종가 < EMA20, 고가가 직전 N봉 최고가 이상(0.5% 허용) |

| 타임프레임 | 바디 강도 (현재/직전 바디) | 직전 N봉 |
| --- | ---: | ---: |
| 1h | 1.1× | 8 |
| 4h · 1d | 1.0× | 5 |

**추가 필터:** 도지 차단 · 꼬리 과다 차단 · 직전 3봉 내 같은 방향 Engulfing 차단 · 종가가 바디 중간 이상(Bullish)/이하(Bearish)

---

### EMA Cross (8 / 21)

| 방향 | 핵심 조건 |
| --- | --- |
| 📈 Golden Cross | EMA8이 EMA21을 아래→위 돌파, 종가 > EMA8, 직전 3봉 모두 EMA8 < EMA21 |
| 📉 Dead Cross | EMA8이 EMA21을 위→아래 돌파, 종가 < EMA8, 직전 3봉 모두 EMA8 > EMA21 |

**추가 필터:** \|EMA8 − EMA21\| ≥ ATR(14) × 0.15 · EMA8 기울기(3봉) 방향 일치

---

### VWAP Cross (BTC · ETH · SOL · BNB · XRP)

`VWAP = Σ(TypicalPrice × Volume) / Σ(Volume)`, `TypicalPrice = (High + Low + Close) / 3`

| 타임프레임 | VWAP 계산 기간 | 돌파 강도 |
| --- | ---: | ---: |
| 1h | 최근 24봉 | 0.3% |
| 4h | 최근 6봉 | 0.15% |
| 1d | 최근 7봉 | 0.15% |

| 방향 | 핵심 조건 |
| --- | --- |
| 📈 상향 돌파 | 종가가 VWAP 아래→위 돌파, 돌파 강도 이상, 직전 4봉 모두 VWAP 아래, 양봉 |
| 📉 하향 돌파 | 종가가 VWAP 위→아래 돌파, 돌파 강도 이상, 직전 4봉 모두 VWAP 위, 음봉 |

---

### Supertrend (ATR 10, 배수 3.0)

`Upper Band = (High + Low) / 2 + ATR(10) × 3.0`  
`Lower Band = (High + Low) / 2 − ATR(10) × 3.0`

| 방향 | 핵심 조건 |
| --- | --- |
| 📈 LONG | 종가가 Supertrend를 아래→위 돌파하고 양봉 |
| 📉 SHORT | 종가가 Supertrend를 위→아래 돌파하고 음봉 |

**추가 필터:** 돌파 강도 ≥ ATR(10) × 0.2 · 직전 3봉이 돌파 전 추세 방향 유지

---

### BB Squeeze Breakout

20주기 SMA와 표준편차(σ)로 밴드를 계산합니다. `Upper = SMA20 + 2σ`, `Lower = SMA20 − 2σ`

| 방향 | 핵심 조건 |
| --- | --- |
| 📈 LONG | 직전 4봉이 Upper Band 이하, 종가가 Upper Band를 상향 돌파하고 양봉 |
| 📉 SHORT | 직전 4봉이 Lower Band 이상, 종가가 Lower Band를 하향 돌파하고 음봉 |

**Squeeze 조건:** 신호봉 직전 10봉 중 8봉 이상이 `BB Width ≤ ATR(14) × 1.5`  
**추가 필터:** 돌파 강도 ≥ ATR(14) × 0.15

---

## 실행 흐름

한 번의 실행(`python main.py`)은 코인마다 아래 순서로 검사해 총 **258건**을 처리합니다.

```
RSI (27) → RSI 다이버전스 (27) → 피보나치 (27)   ← 같은 RSI 데이터 재사용
  → MACD (27) → Stochastic (27) → Engulfing (27)
  → EMA Cross (27) → VWAP Cross (15) → Supertrend (27) → BB Squeeze (27)
```

1. OKX에서 최근 OHLCV 100개를 조회하고 지표를 계산합니다. RSI 다이버전스와 피보나치는 RSI 데이터를 재사용하므로 추가 API 호출이 없습니다.
2. 완성 캔들로 신호를 판정하고, 1회 발화 규칙과 그룹별 필터를 적용합니다.
3. 통과한 신호는 대기열에 담습니다. 검사 사이에는 `0.5초` 지연이 있습니다.
4. 모든 검사가 끝나면 대기열을 **요약 메시지 1개**로 Telegram에 보냅니다. 실행 중 오류로 중단되어도 그때까지 찾은 신호는 `finally`에서 전송됩니다.

개별 코인·타임프레임·신호에서 오류가 나도 **나머지 검사는 계속** 진행됩니다.

### Telegram 알림 예시

```
🚨 신호 요약 (UTC 04:00)
📈 BTC LONG
 🔥 4h: RSI 일반 Div
 ⚡️ 1h: Fib 0.5-0.618, EMA Cross
📉 SOL SHORT
 ⚡️ 1h: RSI 히든 Div
```

### GitHub Actions Summary

- 총 검사 · 발생 · 오류 건수
- 10개 신호별 검사/발생 건수
- BTC 최근 확정 캔들의 RSI · MACD · Stochastic 값

---

## Fork 후 설치·활용 가이드

이 봇은 주문 권한이나 OKX API Key를 요구하지 않습니다.

### 1. 저장소 Fork 및 Actions 활성화

1. 이 페이지 오른쪽 위의 **Fork**를 눌러 자신의 GitHub 계정으로 복사합니다.
2. Fork된 저장소의 **Actions** 탭에서 **I understand my workflows, go ahead and enable them**을 선택합니다.
3. 필요하면 [main.py](main.py)의 `WATCHLIST`, `VWAP_COINS`를 수정하고 커밋합니다.

### 2. Telegram Bot과 Chat ID 준비

1. Telegram에서 `@BotFather`를 열고 `/newbot`으로 봇을 생성합니다.
2. 발급받은 토큰을 복사합니다. 이것이 `TELEGRAM_BOT_TOKEN`입니다.
3. 생성한 봇에게 먼저 메시지(예: `/start`)를 보냅니다.
4. 브라우저에서 `https://api.telegram.org/bot<토큰>/getUpdates`를 열고 응답의 `message.chat.id` 값을 확인합니다. 이것이 `TELEGRAM_CHAT_ID`입니다.

그룹 알림은 봇을 그룹에 초대하고 메시지를 보낸 뒤 같은 방식으로 `chat.id`를 확인합니다. 그룹 ID는 보통 음수입니다.

### 3. GitHub Secrets 등록

Fork한 저장소 → **Settings → Secrets and variables → Actions → New repository secret**

| Secret | 값 |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | BotFather가 발급한 토큰 |
| `TELEGRAM_CHAT_ID` | 알림을 받을 개인/그룹 Chat ID |

Secrets는 워크플로우 로그에 `***`로 가려집니다. 토큰을 README, 코드, 커밋 메시지에 직접 넣지 마세요.

### 4. 15분 정각 스케줄 등록 (외부 크론)

워크플로우는 `repository_dispatch` 이벤트로 실행됩니다. [console.cron-job.org](https://console.cron-job.org/) 등 외부 스케줄러에 아래 요청을 등록합니다.

| 항목 | 값 |
| --- | --- |
| Method | `POST` |
| Cron | `0,15,30,45 * * * *` |
| Timezone | `Asia/Seoul` |
| URL | `https://api.github.com/repos/<GITHUB_ID>/<FORK_REPOSITORY>/dispatches` |

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

Fine-grained PAT는 **Fork한 본인 저장소만** 선택하고 `Contents: Read and write` 권한을 부여합니다. 만료일을 설정하고, `main` 브랜치에 직접 push를 막는 보호 규칙을 함께 두는 것을 권장합니다. 토큰이 유출되어도 봇 코드를 바꿀 수 없게 하기 위해서입니다.

### 5. 첫 실행 확인

1. **Actions** → `RSI Telegram Signal Bot` → **Run workflow**로 수동 실행합니다.
2. 실행 **Summary**에서 258건 검사와 오류 유무를 확인합니다.
3. 다음 정각 실행 후 Telegram 알림을 확인합니다. 조건에 맞는 신호가 없으면 알림이 없는 것이 정상입니다.

---

## 로컬 실행 · 테스트

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="your-token"
export TELEGRAM_CHAT_ID="your-chat-id"
python main.py

# 단위 테스트 (네트워크 불필요)
python -m unittest test_signal_policy -v
```

Windows PowerShell:

```powershell
$env:TELEGRAM_BOT_TOKEN = "your-token"
$env:TELEGRAM_CHAT_ID = "your-chat-id"
python main.py
```

RSI 다이버전스 · 피보나치의 상세 설계는 [docs/plan_rsi_divergence_fib.md](docs/plan_rsi_divergence_fib.md)에 있습니다.

---

## 주의사항

- 이 프로젝트는 **정보성 알림 도구**이며, 투자 조언이나 자동 매매 시스템이 아닙니다.
- 신호 임계값은 백테스트로 검증된 값이 아닙니다.
- RSI는 최근 100봉으로 계산하므로 TradingView(전체 이력) 값과 소수점이 조금 다를 수 있고, 드물게 피벗 판정이 달라질 수 있습니다.
- 저장소가 public이면 Actions 로그와 Summary(발생 신호 포함)를 누구나 볼 수 있습니다. 비밀값은 Secrets로만 관리하세요.
