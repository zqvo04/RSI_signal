# 설치 · 활용 가이드

> [← README](../Readme.md) · 이 봇은 주문 권한이나 OKX API Key를 요구하지 않습니다.

## 목차

1. [저장소 Fork 및 Actions 활성화](#1-저장소-fork-및-actions-활성화)
2. [Telegram Bot과 Chat ID 준비](#2-telegram-bot과-chat-id-준비)
3. [GitHub Secrets 등록](#3-github-secrets-등록)
4. [15분 정각 스케줄 등록 (외부 크론)](#4-15분-정각-스케줄-등록-외부-크론)
5. [첫 실행 확인](#5-첫-실행-확인)
6. [로컬 실행 · 테스트](#6-로컬-실행--테스트)

---

## 1. 저장소 Fork 및 Actions 활성화

1. 저장소 페이지 오른쪽 위의 **Fork**를 눌러 자신의 GitHub 계정으로 복사합니다.
2. Fork된 저장소의 **Actions** 탭에서 **I understand my workflows, go ahead and enable them**을 선택합니다.
3. 필요하면 [main.py](../main.py)의 `WATCHLIST`, `VWAP_COINS`를 수정하고 커밋합니다.

## 2. Telegram Bot과 Chat ID 준비

1. Telegram에서 `@BotFather`를 열고 `/newbot`으로 봇을 생성합니다.
2. 발급받은 토큰을 복사합니다. 이것이 `TELEGRAM_BOT_TOKEN`입니다.
3. 생성한 봇에게 먼저 메시지(예: `/start`)를 보냅니다.
4. 브라우저에서 `https://api.telegram.org/bot<토큰>/getUpdates`를 열고 응답의 `message.chat.id` 값을 확인합니다. 이것이 `TELEGRAM_CHAT_ID`입니다.

그룹 알림은 봇을 그룹에 초대하고 메시지를 보낸 뒤 같은 방식으로 `chat.id`를 확인합니다. 그룹 ID는 보통 음수입니다.

## 3. GitHub Secrets 등록

Fork한 저장소 → **Settings → Secrets and variables → Actions → New repository secret**

| Secret | 값 |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | BotFather가 발급한 토큰 |
| `TELEGRAM_CHAT_ID` | 알림을 받을 개인/그룹 Chat ID |

Secrets는 워크플로우 로그에 `***`로 가려집니다. 토큰을 README, 코드, 커밋 메시지에 직접 넣지 마세요.

## 4. 15분 정각 스케줄 등록 (외부 크론)

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

## 5. 첫 실행 확인

1. **Actions** → `RSI Telegram Signal Bot` → **Run workflow**로 수동 실행합니다.
2. 실행 **Summary**에서 268건 검사와 오류 유무를 확인합니다.
3. 다음 정각 실행 후 Telegram 알림을 확인합니다. 조건에 맞는 신호가 없으면 알림이 없는 것이 정상입니다.

## 6. 로컬 실행 · 테스트

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
