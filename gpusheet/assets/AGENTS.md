## GPU 예약 — `gpusheet`

이 서버들은 여러 사람이 함께 쓰는 GPU 서버다. GPU 를 쓰기 전에는 **반드시 GPU 시트에 예약**해야 한다.
예약 없이 쓰면 다른 사람의 작업과 부딪힌다. 조회·예약은 모두 `gpusheet` 명령으로 한다
(웹 GPU 시트와 같은 예약 기록을 쓴다).

### 명령
```bash
gpusheet free --json                  # 지금 바로 쓸 수 있는 GPU (예약 없음 + 실제로 놀고 있음)
gpusheet free --here --json           # 지금 이 서버에서만
gpusheet free --mem 24 --model 4090 --json   # 조건 필터 (VRAM GB 이상, 모델명 일부)
gpusheet status --json                # 전체 현황 (누가 무엇을 예약했는지)
gpusheet mine --json                  # 내 예약
gpusheet reserve 12:1 --yes           # 예약 (오늘). 내일 칸은 --tmr. "12:1" = 12번 서버의 1번 GPU
gpusheet release 12:1                 # 해제. 내 예약 전부는 --all
gpusheet servers --json               # 서버별 IP(host)·GPU 구성
```
`free`/`status` 의 JSON 에도 각 GPU 가 있는 서버의 접속 주소 `host` 가 들어 있다.
`state` 값: `FREE`(비어 있음) · `RESERVED`(예약·사용 중) · `RESERVED_IDLE`(예약됐지만 유휴) ·
`IN_USE_UNRESERVED`(예약 없이 누가 사용 중) · `DOWN`(응답 없음)

### 반드시 지킬 것
1. **예약은 사용자에게 확인받은 뒤에만 한다.** `gpusheet free` 로 후보를 고르고
   "12:1 (RTX 4090 24G) 을 예약할까요?" 처럼 물은 다음, 동의하면 `gpusheet reserve … --yes` 를 실행한다.
   `--yes` 없이 실행하면 종료 코드 3 으로 멈춘다 — 정상 동작이다. 사용자 확인 없이 `--yes` 를 붙이지 않는다.
2. **필요한 만큼만** 예약한다. 1인당 날짜별 최대 장수가 정해져 있다(`gpusheet mine --json` 의 `max_per_person`).
   RTX 5090 은 2장으로 센다(5090 하나를 예약하면 그날은 더 예약할 수 없다).
3. **남의 예약은 건드리지 않는다.** `state` 가 `FREE` 가 아닌 GPU 는 쓰지 않는다.
   `IN_USE_UNRESERVED` 도 누가 쓰고 있는 것이니 피한다.
4. 예약한 GPU 로만 돌린다. 시트 번호와 CUDA 번호를 맞추기 위해 **반드시** 이렇게 실행한다:
   ```bash
   export CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=<예약한 GPU 번호>
   ```
   `CUDA_DEVICE_ORDER=PCI_BUS_ID` 가 없으면 CUDA 가 순서를 다르게 매겨 **남의 GPU 에서 돌 수 있다.**
5. 예약한 GPU 가 **다른 서버**에 있으면 그 서버로 SSH 해서 실행한다(`host` 값 = 그 서버 IP).
   지금 서버의 GPU 를 대신 쓰지 않는다. 서버 계정을 모르면 추측하지 말고 사용자에게 묻는다.
   SSH 가 안 되면 사용자에게 알리고 임의로 다른 방법을 쓰지 않는다.
6. 작업이 끝나면 `gpusheet release` 로 해제한다. 한 시간 넘게 GPU 를 안 쓰면 예약이 자동 해제되므로,
   GPU 를 오래 안 쓰는 단계(긴 전처리 등)가 있으면 사용자에게 알린다.
7. 예약은 오늘·내일 칸만 가능하다.

### 실패했을 때
- `이미 OOO 님이 예약한 GPU 입니다` → 다른 GPU 를 고른다.
- `1인당 최대 N장까지 …` → 사용자에게 알리고 기존 예약을 해제할지 묻는다.
- `이름이 등록되지 않았습니다` → 사용자에게 `gpusheet setup` 을 한 번 실행해 달라고 한다(이름은 사용자만 정한다).
- `GPU 시트 서버에 연결할 수 없습니다` → 사용자에게 알리고, **예약 없이 GPU 를 쓰지 않는다.**
