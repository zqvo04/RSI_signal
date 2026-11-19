# 작업 설계서: RSI 다이버전스 · 피보나치 되돌림 (1h · 4h · 1d)

> 상태: **구현 완료** (T1–T5, 단위 테스트 25개 통과)
> 대상 파일: `main.py`, `test_signal_policy.py`, `Readme.md` (이 3개 외 수정 금지)

---

## 0. 확정된 결정

| # | 결정 |
|---|---|
| D1 | 다이버전스 스윙 = **RSI에서 피벗 탐지 → 그 봉의 가격(low/high)을 읽음** (TradingView 기본 "RSI Divergence Indicator" 방식) |
| D2 | 피벗 `left=5`, `right=3` (TV 기본값 right=5와 다름 — **R=3 지연 수용**) |
| D3 | 4종 모두 발화: 일반/히든 × LONG/SHORT (TV 기본은 히든 OFF지만 여기선 ON) |
| D4 | 다이버전스와 피보나치는 **별도 신호** (합류 태그 없음) |
| D5 | 1h·4h·1d **동일 로직·동일 파라미터** |
| D6 | 거래량 필터·상위 TF 필터 **미적용** |
| D7 | TV 방식에 없는 필터(선 침범 검사, RSI 40/60 구간, 최소 RSI 차) **넣지 않음** → TV 차트와 1:1 대조 가능 |
| D8 | 신규 API 호출 0건: `fetch_rsi_frame()` 결과 frame 재사용 |

---

## 1. 상수 (main.py 상수부에 추가)

```python
PIVOT_LEFT = 5
PIVOT_RIGHT = 3
DIV_RANGE_MIN = 5    # TV rangeLower
DIV_RANGE_MAX = 60   # TV rangeUpper
FIB_ZONE_TOP = 0.5
FIB_ZONE_BOTTOM = 0.618
FIB_INVALID = 0.786
FIB_MIN_RANGE_ATR = 3.0
```

---

## 2. 함수 명세

### 2-1. `find_pivots(series: pd.Series, left: int, right: int) -> tuple[list[int], list[int]]`
- 반환: `(low_idx, high_idx)` — 위치 인덱스(0..n-1), 오름차순
- 위치 i 후보: `left <= i <= n-1-right`
- pivot low: `s[i] < min(s[i-left:i])` **and** `s[i] <= min(s[i+1:i+right+1])`
- pivot high: `s[i] > max(s[i-left:i])` **and** `s[i] >= max(s[i+1:i+right+1])`
- NaN이 창 안에 있으면 피벗 아님
- 데이터 부족 시 빈 리스트 (예외 금지)
- 주의: Pine `ta.pivotlow/pivothigh`의 동률 처리 규칙은 **확인 불가** → 7장 TV 대조에서 차이 나면 이 비교식만 조정

### 2-2. `find_rsi_divergences(frame: pd.DataFrame, timeframe: str) -> list[tuple[str, str]]`
- 반환: `[(side, label), ...]`, side ∈ `LONG/SHORT`, label ∈ `"RSI 일반 Div"`, `"RSI 히든 Div"`
- 절차:
  1. `completed = completed_candles(frame, timeframe).reset_index(drop=True)`; `n = len(completed)`
  2. `n < PIVOT_LEFT + PIVOT_RIGHT + 2` 또는 `not is_freshly_closed(completed.iloc[-1], timeframe)` → `[]`
  3. `lows, highs = find_pivots(completed["rsi"], PIVOT_LEFT, PIVOT_RIGHT)`
  4. **저점 측** (`len(lows) >= 2` and `lows[-1] == n-1-PIVOT_RIGHT`):
     - `p1, p2 = lows[-2], lows[-1]`
     - 거리: `DIV_RANGE_MIN + 1 <= p2 - p1 <= DIV_RANGE_MAX + 1`
       (TV `_inRange(plFound[1])` = `barssince` 기준이라 피벗 간격 −1로 계산됨 → 6..61)
     - 일반 상승 LONG: `rsi[p2] > rsi[p1]` and `low[p2] < low[p1]`
     - 히든 상승 LONG: `rsi[p2] < rsi[p1]` and `low[p2] > low[p1]`
  5. **고점 측** (`highs[-1] == n-1-PIVOT_RIGHT`, 같은 거리 조건):
     - 일반 하락 SHORT: `rsi[h2] < rsi[h1]` and `high[h2] > high[h1]`
     - 히든 하락 SHORT: `rsi[h2] > rsi[h1]` and `high[h2] < high[h1]`
  6. 동률(=)은 불발. 저점·고점 측 동시 성립 시 둘 다 반환
- 1회 발화 보장: `p2 == n-1-R` (방금 확정) + `is_freshly_closed`

### 2-3. `find_fib_signal(frame: pd.DataFrame, timeframe: str) -> Optional[tuple[str, str]]`
- 반환: `(side, FIB_LABELS[side])` (LONG: "피보나치 되돌림 지지 반등(0.5~0.618)", SHORT: "피보나치 되돌림 저항 반락(0.5~0.618)") 또는 `None`
- 가격 피벗: `lo_idx = find_pivots(completed["low"])[0]`, `hi_idx = find_pivots(completed["high"])[1]`
- ATR: `ta.atr(completed high/low/close, 14)`의 **마지막 값**; NaN/≤0 → None
- `c = n-1`, 미신선(`is_freshly_closed` False) → None

**LONG** (마지막 피벗이 고점: `hi_idx[-1] > lo_idx[-1]`)
- `PH = hi_idx[-1]`, `PL = max(i for i in lo_idx if i < PH)` (없으면 None)
- `H, L = high[PH], low[PL]`, `R = H - L`
- 유효성: `R >= ATR * FIB_MIN_RANGE_ATR`, `PH - PL <= DIV_RANGE_MAX`, `max(high[PH+1:c+1]) <= H`, `min(close[PH+1:c]) >= H - FIB_INVALID*R` (구간 비면 통과)
- 존: `top = H - 0.5R`, `bot = H - 0.618R`
- 조건 `cond(k)`: `low[k] <= top` and `close[k] > bot` and `close[k] > open[k]`
- 발화: `cond(c)` and `not any(cond(k) for k in range(PH+PIVOT_RIGHT, c))`

**SHORT** (마지막 피벗이 저점: `lo_idx[-1] > hi_idx[-1]`) — 대칭
- `PL = lo_idx[-1]`, `PH = max(i for i in hi_idx if i < PL)`
- 유효성: `min(low[PL+1:c+1]) >= L`, `max(close[PL+1:c]) <= L + FIB_INVALID*R`
- 존: `bot = L + 0.5R`, `top = L + 0.618R`
- `cond(k)`: `high[k] >= bot` and `close[k] < top` and `close[k] < open[k]`
- 발화: `cond(c)` and 이전 `[PL+R, c)` 구간 미성립

- 피벗 인덱스 동일(고점·저점이 같은 봉) → None
- 알려진 한계: 고점 확정 전(R봉 이내) 존 터치·반등은 늦게 잡히거나 누락

### 2-4. `main()` 연결 (RSI 루프 내부, `_process_signal_check("RSI", ...)` 직후)
- 같은 `frame` 사용. 각 신규 검사는 **자체 try/except** (`ValueError, KeyError, IndexError`) — 신규 코드 버그가 RSI/전체 스캔을 중단시키지 않게(현재 루프 except 튜플엔 IndexError/KeyError 없음)
- 검사마다 `checked_count += 1`, `signal_breakdown[...]["checked"] += 1`
- 결과마다 `_pending_signals.append((coin, side, timeframe, label))`, `signal_count += 1`, `["signals"] += 1`, `logging.info("Queued ...")`
- `signal_breakdown`에 `"RSI Divergence"`, `"Fibonacci"` 키 추가
- `fetch_rsi_frame`, `_process_signal_check`, 기존 신호 로직은 **수정 금지**

### 2-5. README
- 검사 건수 204 → **258** (전 위치), 신호 종류/검사 범위 표에 2행 추가
- 실행 흐름에 `RSI (27) → RSI Div (27) → Fib (27) → MACD ...` 반영
- 신호 기준 섹션 2개 추가 (2-2, 2-3 표 요약). 거래량·상위TF 필터 미적용 명시
- TV 대조 안내: "TradingView RSI Divergence Indicator에서 Pivot Lookback Right=3, Left=5, Range 5–60, Hidden 표시 ON"

---

## 3. 테스트 명세 (`test_signal_policy.py`에 추가)

공통: 합성 frame(`timestamp`=과거 고정값, `open/high/low/close/volume/rsi`), `patch("main.is_freshly_closed", return_value=True)`

| ID | 대상 | 케이스 |
|---|---|---|
| P1 | find_pivots | 단일 저점/고점 검출 |
| P2 | find_pivots | 우측 R봉 미만 → 미검출 |
| P3 | find_pivots | 같은 값 연속 → 1개만 |
| V1–V4 | divergence | 일반↑, 히든↑, 일반↓, 히든↓ 각각 정확한 (side,label) |
| V5 | divergence | 간격 5(=p2-p1) → 불발, 6 → 발화, 61 → 발화, 62 → 불발 |
| V6 | divergence | `p2 != n-1-R` → 불발 |
| V7 | divergence | 데이터 부족 → `[]` (예외 없음) |
| F1/F2 | fib | LONG / SHORT 발화 |
| F3 | fib | 0.786 이탈 마감 후 → None |
| F4 | fib | PH 이후 고점 갱신 → None |
| F5 | fib | 두 번째 조건 충족 봉 → None |
| F6 | fib | range < 3·ATR → None |

실행: `python -m unittest test_signal_policy -v` — 기존 9개 + 신규 전부 통과가 완료 기준

---

## 4. 작업 분할 (서브에이전트)

순서 실행(같은 파일 편집 → 병렬 시 충돌). 각 태스크 후 메인이 `git diff` 검토 → 통과 시 커밋.

| Task | 담당 | 모델 | 내용 | 의존 | 완료 기준 |
|---|---|---|---|---|---|
| T1 | 서브 | sonnet | 1장 상수 + 2-1 `find_pivots` + P1–P3 | — | 테스트 통과 |
| T2 | 서브 | sonnet | 2-2 `find_rsi_divergences` + V1–V7 | T1 | 테스트 통과 |
| T3 | 서브 | sonnet | 2-3 `find_fib_signal` + F1–F6 | T1 | 테스트 통과 |
| T4 | 서브 | haiku | 2-4 main() 연결 | T2,T3 | 테스트 통과 + `python -c "import main"` |
| T5 | 서브 | haiku | 2-5 README | T4 | 건수 258 일치 (`grep -n 204` 0건) |
| T6 | 메인 | — | 전체 diff 스펙 대조 리뷰, 전체 테스트 | T5 | 이상 없음 |
| T7 | 메인 | — | (선택) OKX 실데이터로 BTC 1h 과거 구간 신호 목록 출력 → 사용자가 TV 차트와 대조. 스크립트는 scratchpad, 커밋 안 함. OKX 접근 차단 시 생략 | T6 | 목록 전달 |
| T8 | 메인 | — | 커밋 정리 후 `ccr-a7c8106e-2r5cm1` push | T6 | push 성공 |

### 서브에이전트 프롬프트 템플릿
```
저장소 /home/user/RSI_signal. 설계서 docs/plan_rsi_divergence_fib.md 의 <섹션>만 구현.
규칙:
- 수정 허용: <파일/함수 목록>. 그 외 코드·포맷 변경 금지. 기존 스타일 유지.
- 설계서와 다르게 해야 할 이유가 생기면 구현하지 말고 이유만 보고.
- 완료 후 `python -m unittest test_signal_policy -v` 실행.
- 커밋·push 금지.
보고(10줄 이내): 추가 함수/테스트 이름, 테스트 결과(통과/실패 수), 설계와 다른 점.
```

---

## 5. 보안 검토

### 5-1. 이번 변경의 영향
- 새 네트워크 호출·비밀값·외부 입력 경로 **없음** (순수 계산)
- 유일한 위험은 **가용성**: 신규 코드 예외 → 스캔 중단. 2-4의 자체 try/except + V7 테스트로 차단

### 5-2. 기존 저장소 이슈 (public 저장소 기준, 이번 범위 밖 — 승인 시 별도 작업)

| ID | 등급 | 이슈 | 권장 조치 |
|---|---|---|---|
| S1 | 중 | cron-job.org에 저장된 PAT(`Contents: write`)가 유출되면 기본 브랜치에 `main.py`를 push → 다음 실행에서 Telegram 토큰 탈취 가능 | `main` 브랜치 ruleset(직접 push 금지·PR 필수), PAT 만료일 설정, 대상 저장소 1개 한정 유지 |
| S2 | 중 | `requirements.txt`가 `>=`로 미고정 → 악성/파손 릴리스가 토큰이 있는 환경에서 실행될 수 있음(공급망) | 현재 동작 버전으로 `==` 고정 (선택: `--require-hashes`) |
| S3 | 하 | `actions/checkout@v4`, `setup-python@v5` 태그 참조 → 태그 이동 시 임의 코드 | 커밋 SHA로 고정 |
| S4 | 하 | 토큰이 URL 경로에 포함 → `requests` 연결 예외 메시지에 URL이 찍힘. Actions는 등록 Secret을 로그에서 `***` 마스킹하지만 로컬 실행은 노출 | `send_telegram_message`에서 `requests.RequestException`을 잡아 토큰 없는 메시지로 재발생 |
| S5 | 정보 | public 저장소의 Actions 로그·Summary는 누구나 열람 가능(신호·전략 노출, 비밀값 없음). `pull_request` 트리거가 없어 포크 PR로 Secret 유출 경로 없음 | 현 상태 유지. `pull_request_target` 트리거 추가 금지 |

양호: `permissions: contents: read`, Telegram `parse_mode` 미사용(메시지 주입 없음), OKX 응답은 `to_numeric(errors="coerce")`로 처리.

---

## 6. 알려진 한계
- TV는 전체 이력으로 RSI를 계산하고, 여기선 100봉(`OHLCV_LIMIT`)만 씀 → RSI 소수점 차이로 드물게 피벗이 달라질 수 있음
- R=3 확정 지연: 1h 3시간 / 4h 12시간 / 1d 3일
- 임계값(3·ATR, 0.786 무효화)은 백테스트 미검증 초기값
