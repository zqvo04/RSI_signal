# RSI 돌파매매 텔레그램 알림봇

OKX USDT 무기한 선물의 RSI(14) 반전 돌파를 감지해 Telegram으로 알려주는 GitHub Actions 기반 알림봇입니다. 주문을 실행하지 않으며, 공개 시세 데이터만 조회합니다.

## 감시 대상과 타임프레임

- 코인: BTC, ETH, SOL, HYPE, DOGE, WLD, XRP, PEPE, LIT, SUI, BNB, LINK, AVAX, PENGU, ONDO
- 타임프레임: `15m`, `1h`, `4h`
- 거래소: OKX USDT 무기한 선물 (`COIN/USDT:USDT`)

감시 대상은 [main.py](main.py)의 `WATCHLIST`에서 수정할 수 있습니다.

## 신호 기준

RSI 기간은 14입니다.

| 신호 | 조건 |
| --- | --- |
| LONG | 이전 완료 캔들 RSI가 30 미만이고, 최근 완료 캔들 RSI가 30 이상일 때 |
| SHORT | 이전 완료 캔들 RSI가 70 초과이고, 최근 완료 캔들 RSI가 70 이하일 때 |

API 데이터의 결측 행은 RSI 계산 전에 제거합니다. 또한 진행 중인 캔들은 사용하지 않습니다. 캔들의 종료 시각에 30초의 여유를 둔 뒤, 완전히 확정된 최근 두 캔들만 비교합니다. 따라서 1시간·4시간 캔들에서 마감 전 일시적인 RSI 돌파가 알림으로 발생하지 않습니다.

알림 중요도는 다음과 같이 표시됩니다.

- `4h`: 🔥 높은 신뢰도
- `1h`: ⚡️ 중간 신뢰도
- `15m`: 👀 단기 진입 타점

## 중복 알림 방지와 발화 타이밍

워크플로우는 15분마다 실행되지만, 1시간·4시간 캔들은 다음 캔들이 닫히기 전까지 계속 "가장 최근 완성 캔들"로 남습니다. 이 상태에서 매 실행마다 동일한 돌파를 다시 감지하면, 같은 1시간 신호가 최대 4번, 4시간 신호가 최대 16번 중복 전송됩니다.

이를 막기 위해 **돌파 캔들이 방금 닫힌 직후 첫 실행에서만** 알림을 보냅니다. 구체적으로, 돌파 캔들의 종료 시각이 직전 스캔 주기(15분) 이내일 때만 "신선한 신호"로 간주합니다(`is_freshly_closed`). GitHub Actions 실행은 상태를 유지하지 않으므로, 별도의 저장소 없이 시간 창(time window)만으로 중복을 제거합니다.

타임프레임별 발화 방식은 다음과 같습니다.

| 타임프레임 | 발화 시점 | 설명 |
| --- | --- | --- |
| `15m` | 15분 완성봉마다 | 매 15분 새 캔들이 닫히므로 돌파가 있는 완성봉마다 알림 가능 |
| `1h` | 완성봉 종료 후 첫 실행(약 정각 +1분) | 이후 3회 스캔은 동일 캔들이므로 건너뜀 |
| `4h` | 완성봉 종료 후 첫 실행(약 정각 +1분) | 이후 15회 스캔은 동일 캔들이므로 건너뜀 |

외부 크론이 `1,16,31,46 * * * *`로 실행하므로, 캔들 종료(정각·15분 경계) 직후 `+1분` 실행에서 신호가 발화합니다. 러너 시작 지연은 약 13분까지 허용되며, 그 이상 크게 지연되는 드문 경우에는 해당 신호를 놓칠 수 있습니다(무상태 방식의 트레이드오프).

## 전체 동작 로직

한 번의 실행(`main.py`의 `main`)은 감시 대상 15개 코인 × 3개 타임프레임, 총 45건을 다음 순서로 처리합니다.

1. **시세 조회**: OKX 공개 스왑 마켓에서 각 심볼의 최근 OHLCV 100개를 가져옵니다(`fetch_rsi_frame`). 비공개 인증은 사용하지 않습니다.
2. **데이터 정제 및 RSI 계산**: 숫자로 변환할 수 없는 값을 제거하고, 시간순 정렬·중복 제거 후 RSI(14)를 계산합니다. RSI가 없는 초기 행은 버립니다.
3. **완성 캔들 선별**: 종료 시각에 30초 여유(`CANDLE_CLOSE_GRACE_SECONDS`)를 둬 진행 중인 캔들을 배제하고, 완전히 확정된 최근 두 캔들을 고릅니다(`latest_completed_candles`).
4. **신호 판정**: 두 캔들의 RSI로 LONG/SHORT 돌파를 판정합니다(`find_signal`). 이때 돌파 캔들이 방금 닫힌 경우에만(위의 중복 방지 규칙) 신호를 반환합니다.
5. **알림 전송**: 신호가 있으면 메시지를 구성해(`format_message`) Telegram으로 전송합니다(`send_telegram_message`). 토큰·채팅 ID는 공백을 제거해 사용하며, Telegram이 오류를 반환하면 응답 본문(예: `chat not found`)을 로그에 남겨 원인을 확인할 수 있게 합니다.
6. **속도 제한**: 각 검사 사이에 짧은 지연(`REQUEST_DELAY_SECONDS`)을 둬 OKX·Telegram 엔드포인트 부하를 줄입니다.
7. **실행 요약**: 검사 건수·신호 수·오류 수와 BTC의 최근 확정 캔들 RSI를 GitHub Actions Summary에 기록합니다(`write_workflow_summary`). 모든 검사가 실패하면 워크플로우를 실패로 종료합니다.

개별 코인/타임프레임에서 오류가 나도 나머지 검사는 계속 진행되며, 오류 건수만 요약에 집계됩니다.

## Telegram 설정

GitHub 저장소의 **Settings → Secrets and variables → Actions**에서 다음 Repository secrets를 등록합니다.

| Secret | 설명 |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | BotFather가 발급한 Telegram Bot Token |
| `TELEGRAM_CHAT_ID` | 알림을 받을 개인 또는 그룹 채팅 ID |

이 봇은 OKX 공개 OHLCV 데이터만 사용하므로 OKX API Key, Secret, Passphrase는 필요하지 않습니다.

## 외부 크론 트리거 설정

워크플로우는 GitHub 내장 cron 대신 `repository_dispatch` 이벤트를 받습니다. [console.cron-job.org](https://console.cron-job.org/)에서 다음 요청을 15분마다 실행하도록 설정합니다.

- Cron: `1,16,31,46 * * * *`
- Timezone: `Asia/Seoul`
- Method: `POST`
- URL: `https://api.github.com/repos/zqvo04/RSI_signal/dispatches`

요청 헤더:

```text
Accept: application/vnd.github+json
Authorization: Bearer <GITHUB_FINE_GRAINED_TOKEN>
X-GitHub-Api-Version: 2026-03-10
Content-Type: application/json
```

요청 본문:

```json
{
  "event_type": "rsi-signal-check",
  "client_payload": {
    "source": "console.cron-job.org"
  }
}
```

외부 크론용 Fine-grained Personal Access Token은 `RSI_signal` 저장소만 선택하고, Repository permission의 `Contents`를 **Read and write**로 설정합니다. 토큰은 console.cron-job.org에만 저장하며, 코드·GitHub Secrets·메신저에는 절대 넣지 않습니다.

## 동작 확인

1. GitHub 저장소의 **Actions** 탭에서 `RSI Telegram Signal Bot`을 선택합니다.
2. **Run workflow**로 수동 실행합니다.
3. 실행 항목의 **Summary** 탭에서 검사 결과를 확인합니다.

Summary에는 검사 건수, 발생 신호 수, 오류 수가 표시됩니다. 정상 환경에서는 15개 코인 × 3개 타임프레임으로 총 45건을 검사합니다. Telegram은 실제 LONG/SHORT 신호가 발생했을 때만 전송됩니다.

## 로컬 실행

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="your-token"
export TELEGRAM_CHAT_ID="your-chat-id"
python main.py
```

Windows PowerShell에서는 `export` 대신 `$env:TELEGRAM_BOT_TOKEN` 및 `$env:TELEGRAM_CHAT_ID`를 사용합니다.

## 주의사항

- 이 프로젝트는 정보성 알림 도구이며 투자 조언이나 주문 실행 시스템이 아닙니다.
- 외부 크론이 정시에 요청해도 GitHub Actions 러너 시작은 약간 지연될 수 있습니다. 로직은 종료가 확정된 캔들만 사용하므로 이 지연으로 미완성 캔들 신호가 발생하지 않습니다.
