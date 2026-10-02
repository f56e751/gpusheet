# gpusheet

공유 GPU 서버의 **예약 시트를 터미널에서** 보고 예약하는 CLI 입니다.
사람이 직접 써도 되고, **Claude Code · Codex 같은 AI 코딩 에이전트**가 빈 GPU 를 찾아
사용자 확인을 받고 예약하도록 만드는 게 주목적입니다.

웹 GPU 시트와 **같은 예약 기록**을 쓰므로, CLI 로 한 예약은 웹 시트에 바로 보이고 그 반대도 같습니다.

```text
$ gpusheet free --model 4090
GPU      모델      메모리   사용률  온도  오늘  내일  유휴  상태
-------  --------  -------  ------  ----  ----  ----  ----  ---------
gpu6:0   RTX 4090  0.6/24G  0%      33°C  -     -     8분   비어 있음
gpu12:1  RTX 4090  0.4/24G  0%      54°C  -     -     47분  비어 있음

$ gpusheet reserve 6:0
오늘 예약 (홍길동):
  gpu6:0  RTX 4090 24G  (비어 있음)
진행할까요? [y/N] y
✔ gpu6:0 예약됨 (홍길동, 오늘)
```

## 설치

표준 라이브러리만 씁니다 (Python 3.8+).

```bash
pip install git+https://github.com/f56e751/gpusheet
# 또는 clone 해서 bin/gpusheet 를 PATH 에 연결
```

서버 관리자가 서버마다 설치해 두었다면 바로 `gpusheet` 를 쓰면 됩니다.

## 처음 한 번

```bash
gpusheet setup                      # 시트에 쓰는 이름 등록 + Claude/Codex 규칙 설치
gpusheet setup --url <서버 주소>     # 주소가 서버에 미리 설정돼 있지 않은 경우 (노트북 등)
```

`setup` 은 다음을 합니다.
- 이름을 `~/.config/gpusheet/config.json` 에 저장 (시트 명단에 있는 이름인지 확인)
- Claude Code 스킬 설치: `~/.claude/skills/gpusheet/SKILL.md`
- Codex 규칙 추가: `~/.codex/AGENTS.md` 의 `gpusheet` 블록 (다시 실행해도 중복되지 않음)

## 명령

| 명령 | 설명 |
|---|---|
| `gpusheet status` | 전체 현황 |
| `gpusheet free [--mem GB] [--model 4090] [--here] [--tmr]` | 지금 바로 쓸 수 있는 GPU (예약 없음 + 실제로 유휴) |
| `gpusheet mine` | 내 예약 |
| `gpusheet reserve 12:1 [--tmr] [--yes]` | 예약. `12:1` = 12번 서버의 1번 GPU |
| `gpusheet release 12:1 [--tmr]` / `--all` | 해제 (본인 예약만) |
| `gpusheet servers` | 서버 목록: IP·GPU 구성 (IP 는 시트 서버에서 받아옴) |
| `gpusheet guide` | AI 에이전트용 사용 규칙 |

- 모든 조회 명령에 `--json` 을 붙이면 기계가 읽기 좋은 출력이 나옵니다.
- **`--yes` 없이 `reserve` 하면** 터미널에서는 y/N 을 묻고, 비대화형(AI 에이전트)에서는
  종료 코드 3 으로 멈춥니다. AI 가 사용자 확인 없이 예약하지 못하게 하기 위해서입니다.
- 종료 코드: `0` 성공 · `1` 실패/거절 · `2` 사용법 오류 · `3` 사용자 확인 필요

## 예약한 GPU 로 실행

```bash
export CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=1
python train.py
```

`CUDA_DEVICE_ORDER=PCI_BUS_ID` 가 없으면 CUDA 가 GPU 를 다른 순서로 매겨서
시트의 번호와 실제 GPU 가 어긋날 수 있습니다.

## 서버 주소 설정

CLI 에는 서버 주소가 들어 있지 않습니다. 다음 순서로 찾습니다.

1. 환경변수 `GPUSHEET_URL`
2. `~/.config/gpusheet/config.json` 의 `url` (`gpusheet setup --url` 로 저장)
3. `/etc/gpusheet.conf` 의 `url = …` 줄 (서버 관리자가 배포)

시트 서버를 옮기면 관리자가 `/etc/gpusheet.conf` 만 바꾸면 되고, CLI 를 다시 설치할 필요는 없습니다.

## 규칙은 서버가 지킨다

예약 규칙(남의 예약 덮어쓰기 금지, 1인당 장수 제한, 오늘·내일만)은 **시트 서버가 검사**하고,
위반하면 이유를 돌려줍니다. CLI 는 그 이유를 그대로 보여 줍니다.
이 도구의 목적은 사칭 방지가 아니라 **사람과 AI 의 실수 방지**입니다 (웹 시트와 같은 신뢰 수준).

## 서버 API

| | |
|---|---|
| `GET /get_dashboard_info` | 서버별 GPU 상태와 예약자 (`user_today`, `user_tmr`) |
| `PUT /set_user` | `{"user": "이름" or null, "device_ids": [{"hostname": "gpu6", "index": "0"}], "date": "today"/"tmr"}` |
| `GET /names` | 명단 `{"names": [...], "max_per_person": N}` |

CLI 는 `X-Gpusheet-Client/Host/Account` 머리말로 출처를 알립니다 (서버 로그 추적용).
