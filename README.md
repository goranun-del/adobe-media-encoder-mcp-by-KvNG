# AME MCP

Claude가 After Effects 컴프를 Adobe Media Encoder(AME)로 보내 출력 경로·인코딩 값을 지정해 렌더하고, 끝났는지 확인하는 MCP 서버.

## 동작 경로

Claude → `server.py`(MCP, stdio) → `AfterFX.com -r`(AE 메인 엔진에서 jsx 실행) → BridgeTalk → AME

- aep는 AE로 열지 않는다. 컴프 이름은 aep 파일에서 직접 읽어 AE 아이템 ID로 바꾸고, AME가 Dynamic Link로 렌더한다.
- AE 프로젝트·렌더 대기열은 건드리지 않는다. AE는 BridgeTalk 중계만 한다.
- 출력 파일은 덮어쓰지 않는다. 같은 이름이 있으면 `_02`, `_03` ...으로 바꾼다.
- 설치 경로는 하드코딩하지 않는다. 실행 중인 AE, Program Files 순으로 찾는다.

## 파일

| 파일 | 역할 |
|---|---|
| `server.py` | MCP 서버(도구 6개) |
| `ae_runner.jsx` | AE 쪽: AME 스크립트를 BridgeTalk로 보내고 회신을 파일로 남김 |
| `ame_lib.es` | AME 쪽 함수(상태, 대기열 투입, 시작, 이벤트 기록) |
| `requirements.txt` | 파이썬 패키지 |
| `presets\` | `make_preset`으로 만든 프리셋(실행 중 생성) |
| `_work\` | 실행 중 생기는 요청·결과·이벤트 파일(`events.jsonl`), 작업 정보(`jobs\`) |

## 요구 사항

- After Effects, Media Encoder 같은 세대(실측: AE 2026 26.5, AME 2026 26.5.2)
- Python 3 + `mcp`, `psutil` (실측: Python 3.14, mcp 1.12.4). 설치: `pip install -r requirements.txt`
- 출력 실측(선택): `ffprobe` PATH
- AE가 꺼져 있으면 `AfterFX.com -r`이 AE를 띄운다(기동 시간 걸림). AME가 꺼져 있으면 BridgeTalk가 AME를 띄운다(약 6초).

## 등록

등록은 아직 하지 않았다. 쓰려면 아래 한 줄(파이썬 경로는 PC에 맞게).

```
claude mcp add --scope user ame -- "<python.exe 경로>" -B "<설치폴더>\server.py"
```

AE 경로를 직접 지정하려면 환경변수 `AMEMCP_AFTERFX_COM`에 `AfterFX.com` 전체 경로.

## 도구

| 도구 | 하는 일 |
|---|---|
| `ame_status()` | AE·AME 실행 PID, AME 배치 상태(stopped/running 등), 포맷 목록. AME가 꺼져 있으면 띄우지 않음 |
| `list_presets(query, limit)` | 프리셋 검색(이 서버가 만든 것, AME 시스템, 사용자 프리셋). 이름·포맷·해상도·fps·비트레이트·fps 잠금 여부 |
| `make_preset(base, name, width, height, fps, target_mbps, max_mbps)` | 프리셋 복제 후 값 변경 → `presets\<name>.epr` |
| `queue_comp(aep, comp, preset, output, width, height, start, format)` | 컴프를 AME 대기열에 넣음. comp는 이름 또는 AE 아이템 ID. job_id 반환 |
| `start()` | AME 대기열 렌더 시작 |
| `wait(job_id, timeout_sec)` | 완료까지 대기. ok, AME 로그 요약, 출력 파일 ffprobe 결과, fps 불일치 시 warning |

사용 예:

1. `list_presets("Twitter 720p")` → fps 잠금 없는 H.264 프리셋 확인
2. `make_preset(base=<경로>, name="납품 720p 24fps 6M", width=1280, height=720, fps=24, target_mbps=6)`
3. `queue_comp(aep=<aep>, comp="메인컴프", preset=<2의 경로>, output="D:\\납품\\메인.mp4", start=true)`
4. `wait(job_id, 120)` 반복

## 실측으로 확인된 주의점

- **렌더 시작은 AME 대기열 전체를 돌린다.** 사용자가 AME에 넣어둔 다른 항목도 같이 렌더된다. 시작 전에 AME 대기열을 확인할 것.
- **aep는 저장된 상태로 렌더된다.** AE에서 저장 안 한 변경은 반영되지 않는다.
- **fps 변경:** AME 시스템 프리셋 대부분은 fps가 "소스 일치"로 잠겨 있어 값을 바꿔도 무시된다. `list_presets`의 `fps_locked_to_source=false`인 프리셋(예: H.264 `Twitter 720p HD`)을 base로 써야 한다. 잠긴 base에 fps를 주면 `make_preset`은 프리셋을 만들되 `warning`을 붙이고, `wait`가 실제 출력 fps(ffprobe)를 프리셋 fps와 대조해 다르면 `warning`을 준다.
- **해상도·비트레이트:** 프리셋 복제 수정으로 반영됨(8Mbps 지정 → 실측 8.7Mbps). `queue_comp`의 width/height로도 덮어쓸 수 있다.
- **코덱 변경**은 실측하지 않았다. 원하는 포맷의 프리셋을 base로 고른다.
- **포맷 이름**은 AME 언어를 따른다(예: `HEVC(H.265)`, `H.264 블루레이`). H.264·QuickTime·HEVC·MPEG4는 프리셋 폴더로 자동 판별, 나머지는 `queue_comp`의 `format`에 `ame_status`의 포맷 이름을 그대로 넣는다.
- **진행률:** AME 진행률 이벤트는 출력 경로가 비어 오거나 시작 이벤트보다 먼저 올 때가 있어, `wait` 시간 초과 시의 `queue_progress`는 대기열 기준 값이다. 완료 판정은 출력 경로로 정확히 매칭한다.
- **AE MCP 브릿지와 별개:** AE MCP가 CEP 경로로 붙은 상태에서는 BridgeTalk를 쓸 수 없어서, 이 서버는 `AfterFX.com -r`로 AE를 직접 호출한다(`AfterFX.exe -r`은 동작 안 함).
- **실행 중인 AE를 그대로 쓴다.** `-r` 스크립트는 AE 프로젝트를 건드리지 않지만, 실작업 프로젝트가 열린 AE에서도 실행된다. 신경 쓰이면 작업 중이 아닐 때 쓸 것.
- **AE에 대화상자가 떠 있으면** `-r` 스크립트가 막혀 응답 없음 오류가 난다.
- 실패 렌더(result=False) 경로는 실측하지 않았다.

## 테스트 기록
 PC에서 MCP 프로토콜로 확인한 것(테스트 스크립트·로그는 저장소에 넣지 않음):

- AME 꺼진 상태 / 켜진 상태에서 queue_comp → start → wait 실렌더: 1280x720, 24fps·30fps, 3.0초 H.264 (ffprobe)
- 출력 이름 중복 시 _02 ~ _08 생성, 시작 전 wait 시간 초과 응답
- 잠긴 base에 fps 지정 시 make_preset·wait 경고
- 잘못된 입력(빈 aep·없는 aep·aep 아닌 파일·없는 컴프·없는 프리셋·포맷 모름·확장자 없는 출력·빈 프리셋 이름·음수 값·없는 job_id·limit 0)은 원인 문장으로 오류 반환
- 미확인: AME 렌더 실패(result=False) 경로, 코덱 변경

## 라이선스

만든 사람: 강동희 (KvNG). All rights reserved.

원본 그대로 내려받아 설치·사용할 수 있다. 재배포(전체·일부)와 수정·변형, 수정본 배포는 금지한다. 자세한 내용은 [LICENSE](LICENSE) 참조.
