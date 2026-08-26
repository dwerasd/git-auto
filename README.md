# git-auto

## 한 줄 소개

GitHub 저장소 초기화·클론·동기화 작업을 자동화하는 개인용 Python CLI/GUI 도구 모음. `.env`에 저장된 GitHub 계정 정보와 GitHub REST API, `git` CLI를 이용해 반복적인 저장소 관리 작업을 스크립트 한 번으로 처리한다.

## 주요 기능

- **gitup.py** — 로컬 폴더의 기존 Git 히스토리를 전부 삭제하고 "Initial commit" 하나로 리셋한 뒤, 동일 이름의 원격 저장소를 삭제·재생성하여 강제 푸시한다(원격 히스토리도 완전히 초기화됨).
- **gitinit.py** — 로컬 폴더를 새 Git 저장소로 초기화하고, 동일 이름의 GitHub 저장소가 없으면 생성, 있으면 공개 설정만 변경한 뒤 강제 푸시한다.
- **gitclone.py** — `owner/repo`, GitHub URL, SSH URL 등 다양한 형식을 파싱해 저장소를 클론하고, `data/repos.json`의 구독 목록에 등록한다. `--reset`으로 기존 폴더 삭제 후 재클론 가능.
- **gitsync.py** — `data/repos.json`에 등록된 구독 저장소를 순회하며 fetch 후 behind/ahead를 비교해 자동으로 pull한다. 병합 충돌·로컬 변경 충돌·긴 파일명(Windows 260자 제한)·HTTP 500/네트워크 오류·원격 ref 충돌(D/F) 등을 자동 복구 로직으로 처리하며, 원격 히스토리가 급격히 줄어든 경우(초기화 의심)는 리셋을 거부하고 로컬을 백업만 한다.
- **gitclone_gui.py / gitsync_gui.py** — 위 `gitclone.py`/`gitsync.py`를 tkinter GUI로 감싼 버전. 저장소 목록을 트리뷰로 보여주며 우클릭 메뉴로 업데이트·재클론·자동업데이트 토글·순서 변경(드래그 앤 드롭) 등을 지원한다.

## 스택

- Python 3 (`argparse`, `json`, `subprocess`, `urllib.request`, `pathlib`, `re`, `shutil`, `tkinter`/`ttk`/`scrolledtext`)
- 외부 의존성 없음 — 표준 라이브러리만 사용
- `git` CLI, GitHub REST API(`api.github.com`)

## 폴더 구성

```
git-auto/
├── gitup.py          # 로컬 히스토리 리셋 + 원격 재생성 푸시
├── gitinit.py        # 신규 저장소 초기화 + 푸시
├── gitclone.py       # 타인 저장소 클론 + 구독 등록
├── gitsync.py        # 구독 저장소 일괄 동기화 (CLI)
├── gitclone_gui.py   # gitclone.py GUI
├── gitsync_gui.py    # gitsync.py GUI
└── LICENSE           # MIT
```

실행 시 스크립트 폴더에 `.env`(GITHUB_USER, GITHUB_TOKEN, 선택적으로 CLONE_BASE_PATH)와 `.gitignore` 템플릿, `data/repos.json`(구독 목록)이 필요하지만 저장소에는 포함되어 있지 않다(직접 생성 필요).

## 빌드·실행

Windows 환경을 전제로 작성됨(코드 내 예시 경로가 `C:\`, `E:\` 형식이며 `gitsync_gui.py`는 `os.startfile` 사용).

```
python gitup.py "C:\경로\폴더" [--name repo명] [--public] [--force]
python gitinit.py "C:\경로\폴더" [--public]
python gitclone.py owner/repo [--path 경로] [--reset]
python gitsync.py [--list] [--remove owner/repo [--delete-local]]
python gitclone_gui.py
python gitsync_gui.py
```

사전에 스크립트 폴더에 `.env` 파일을 만들어 `GITHUB_USER`, `GITHUB_TOKEN`을 설정해야 한다(gitclone.py/gitsync.py는 없어도 공개 저장소 한정으로 동작 가능, gitup.py/gitinit.py는 필수).

## 상태

개인용 스크립트 모음으로, 별도의 테스트 코드나 CI 설정은 없다. `gitup.py`와 `gitinit.py`는 원격 저장소를 강제로 삭제·초기화하거나 강제 푸시하므로 사용 시 주의가 필요하다.
