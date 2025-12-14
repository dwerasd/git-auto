#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# GitHub 구독 저장소 동기화 스크립트
# 구독 중인 타인의 저장소를 자동으로 업데이트
#
# 사용법:
#     python gitsync.py                    # 모든 구독 저장소 업데이트
#     python gitsync.py --list             # 구독 목록 확인
#     python gitsync.py --remove owner/repo  # 구독 해제
#

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent
ENV_FILE = SCRIPT_DIR / ".env"
DATA_DIR = SCRIPT_DIR / "data"
REPOS_FILE = DATA_DIR / "repos.json"


def load_config() -> dict:
    """.env 파일에서 설정 로드"""
    config = {
        "GITHUB_USER": "",
        "GITHUB_TOKEN": "",
        "CLONE_BASE_PATH": ""
    }
    
    if not ENV_FILE.exists():
        print(f"오류: 설정 파일이 없습니다: {ENV_FILE}")
        sys.exit(1)
    
    with open(ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if key in config:
                    config[key] = value
    
    return config


def load_repos() -> dict:
    """repos.json 파일 로드"""
    if not REPOS_FILE.exists():
        return {"subscriptions": []}
    
    try:
        with open(REPOS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"subscriptions": []}


def save_repos(data: dict):
    """repos.json 파일 저장"""
    # data 폴더가 없으면 생성
    if not DATA_DIR.exists():
        DATA_DIR.mkdir(parents=True)
    with open(REPOS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def find_subscription(repos_data: dict, owner: str, repo_name: str) -> dict | None:
    """구독 목록에서 저장소 찾기"""
    repo_full = f"{owner}/{repo_name}"
    for sub in repos_data.get("subscriptions", []):
        if sub.get("repo") == repo_full:
            return sub
    return None


def remove_subscription(owner: str, repo_name: str) -> bool:
    """구독 목록에서 저장소 제거"""
    repos_data = load_repos()
    repo_full = f"{owner}/{repo_name}"
    
    original_len = len(repos_data.get("subscriptions", []))
    repos_data["subscriptions"] = [
        sub for sub in repos_data.get("subscriptions", [])
        if sub.get("repo") != repo_full
    ]
    
    if len(repos_data["subscriptions"]) < original_len:
        save_repos(repos_data)
        return True
    return False


def update_last_commit(owner: str, repo_name: str, commit_sha: str):
    """마지막 커밋 SHA 업데이트"""
    repos_data = load_repos()
    sub = find_subscription(repos_data, owner, repo_name)
    if sub:
        sub["last_commit"] = commit_sha
        save_repos(repos_data)


def parse_repo_input(repo_input: str) -> tuple[str, str]:
    """owner/repo 형식 파싱 (URL도 지원)"""
    repo_input = repo_input.strip()
    
    # URL 형식: https://github.com/owner/repo (다양한 후속 경로 허용)
    https_match = re.match(r'https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:/.*)?$', repo_input)
    if https_match:
        owner = https_match.group(1)
        repo = https_match.group(2)
        repo = repo.removesuffix('.git')
        return owner, repo
    
    # 단순 형식: owner/repo
    simple_match = re.match(r'^([^/]+)/([^/]+)$', repo_input)
    if simple_match:
        return simple_match.group(1), simple_match.group(2)
    
    print(f"오류: 올바른 저장소 형식이 아닙니다: {repo_input}")
    print("형식: owner/repo 또는 https://github.com/owner/repo")
    sys.exit(1)


def run_git(args: list[str], cwd: str | None = None) -> tuple[bool, str]:
    """Git 명령 실행"""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace"
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output
    except Exception as e:
        return False, str(e)


def is_merge_conflict_error(git_output: str) -> bool:
    """git 출력이 머지 충돌(미병합 파일)로 인한 실패인지 여부"""
    if not git_output:
        return False
    text = git_output.lower()
    return (
        "unmerged" in text
        or "unmerged files" in text
        or "fix conflicts" in text
        or "unresolved conflict" in text
        or "you have unmerged paths" in text
    )


def has_unmerged_paths(repo_path: str) -> bool:
    """현재 작업 트리에 미병합 경로가 있는지(머지 진행/충돌 상태) 빠르게 확인"""
    success, output = run_git(["status", "--porcelain"], repo_path)
    if not success:
        return False
    # porcelain에서 'UU', 'AA', 'DD', 'AU', 'UA', 'DU', 'UD' 등은 미병합 상태
    for line in output.splitlines():
        if len(line) >= 2 and line[:2] in {"UU", "AA", "DD", "AU", "UA", "DU", "UD"}:
            return True
    return False


def get_local_commit(repo_path: str) -> str | None:
    """로컬 저장소의 현재 HEAD 커밋 SHA"""
    success, output = run_git(["rev-parse", "HEAD"], repo_path)
    return output if success else None


def get_remote_commit(repo_path: str, branch: str = "main") -> str | None:
    """원격 저장소의 최신 커밋 SHA (fetch 후)"""
    success, output = run_git(["rev-parse", f"origin/{branch}"], repo_path)
    return output if success else None


def _set_remote_url_with_token(repo_full: str, repo_path: str, token: str) -> None:
    """origin URL에 토큰을 임시로 삽입"""
    if not token:
        return
    try:
        owner, repo_name = repo_full.split("/")
        token_url = f"https://{token}@github.com/{owner}/{repo_name}.git"
        run_git(["remote", "set-url", "origin", token_url], repo_path)
    except Exception:
        pass


def _restore_remote_url(repo_full: str, repo_path: str, token: str) -> None:
    """origin URL에서 토큰 제거(보안)"""
    if not token:
        return
    try:
        owner, repo_name = repo_full.split("/")
        clean_url = f"https://github.com/{owner}/{repo_name}.git"
        run_git(["remote", "set-url", "origin", clean_url], repo_path)
    except Exception:
        pass


def pull_with_token(repo_full: str, repo_path: str, branch: str, token: str) -> tuple[bool, str]:
    """토큰 설정/복원까지 포함한 pull 실행"""
    _set_remote_url_with_token(repo_full, repo_path, token)
    success, output = run_git(["pull", "origin", branch], repo_path)
    _restore_remote_url(repo_full, repo_path, token)
    return success, output


def fetch_with_token(repo_full: str, repo_path: str, token: str) -> tuple[bool, str]:
    """토큰 설정/복원까지 포함한 fetch 실행"""
    _set_remote_url_with_token(repo_full, repo_path, token)
    success, output = run_git(["fetch", "origin"], repo_path)
    _restore_remote_url(repo_full, repo_path, token)
    return success, output


def abort_merge(repo_path: str) -> tuple[bool, str]:
    """진행 중인 merge를 취소"""
    return run_git(["merge", "--abort"], repo_path)


def hard_reset_to_remote(repo_path: str, branch: str) -> tuple[bool, str]:
    """로컬 변경을 폐기하고 origin/branch로 강제 맞춤 (위험)"""
    ok, out = run_git(["reset", "--hard", f"origin/{branch}"], repo_path)
    if not ok:
        return ok, out
    ok2, out2 = run_git(["clean", "-fd"], repo_path)
    if not ok2:
        return ok2, out2
    return True, (out + "\n" + out2).strip()


def auto_recover_and_pull(repo_full: str, repo_path: str, branch: str, token: str) -> tuple[bool, str]:
    """머지 충돌/미병합 파일이 있더라도 무인으로 최신 상태까지 맞추려 시도.

    전략:
      1) merge --abort
      2) pull 재시도
      3) 여전히 충돌이면 fetch 후 reset --hard origin/branch + clean -fd
      4) checkout -f branch (브랜치/DETACHED 등 꼬임 대비)
      5) 최종 pull
    """
    # 1) merge --abort
    abort_merge(repo_path)

    # 2) pull 재시도
    ok_pull, out_pull = pull_with_token(repo_full, repo_path, branch, token)
    if ok_pull:
        return True, out_pull

    # 충돌/미병합이 아니면 이 루틴으로 해결 불가
    if not (is_merge_conflict_error(out_pull) or has_unmerged_paths(repo_path)):
        return False, out_pull

    # 3) fetch
    ok_fetch, out_fetch = fetch_with_token(repo_full, repo_path, token)
    if not ok_fetch:
        return False, f"fetch 실패: {out_fetch}"

    # 4) reset + clean
    ok_reset, out_reset = hard_reset_to_remote(repo_path, branch)
    if not ok_reset:
        return False, f"reset/clean 실패: {out_reset}"

    # 5) checkout -f branch
    run_git(["checkout", "-f", branch], repo_path)

    # 6) 최종 pull
    return pull_with_token(repo_full, repo_path, branch, token)


def sync_repository(sub: dict, token: str) -> dict:
    """단일 저장소 동기화 (업데이트 체크 + pull)
    
    Returns:
        {"status": "updated|up-to-date|error|missing", "message": str}
    """
    repo = sub.get("repo", "")
    local_path = sub.get("local_path", "")
    branch = sub.get("branch", "main")
    
    # 로컬 경로 확인
    if not os.path.exists(local_path):
        return {"status": "missing", "message": "로컬 폴더 없음"}
    
    if not os.path.exists(os.path.join(local_path, ".git")):
        return {"status": "error", "message": "Git 저장소 아님"}
    
    # 토큰이 있으면 fetch에 사용
    owner, repo_name = repo.split("/")
    if token:
        # 임시로 토큰 URL 설정
        token_url = f"https://{token}@github.com/{owner}/{repo_name}.git"
        run_git(["remote", "set-url", "origin", token_url], local_path)
    
    # fetch로 원격 정보 가져오기
    success, output = fetch_with_token(repo, local_path, token)
    
    if not success:
        return {"status": "error", "message": f"fetch 실패: {output}"}
    
    # 로컬과 원격 커밋 비교
    local_commit = get_local_commit(local_path)
    remote_commit = get_remote_commit(local_path, branch)
    
    if not local_commit or not remote_commit:
        return {"status": "error", "message": "커밋 정보 확인 실패"}
    
    if local_commit == remote_commit:
        return {"status": "up-to-date", "message": "최신 상태"}
    
    # 업데이트 필요 - pull 실행
    success, output = pull_with_token(repo, local_path, branch, token)

    if not success:
        # GUI와 동일하게: 충돌이면 무인 자동 복구로 최신까지 맞추기
        if is_merge_conflict_error(output) or has_unmerged_paths(local_path):
            ok2, out2 = auto_recover_and_pull(repo, local_path, branch, token)
            if not ok2:
                return {"status": "error", "message": f"자동 복구 실패: {out2}"}
            # 복구 후 새 커밋 SHA 저장
            new_commit = get_local_commit(local_path)
            if new_commit:
                update_last_commit(owner, repo_name, new_commit)
            return {"status": "updated", "message": "자동 복구 후 업데이트 완료"}

        return {"status": "error", "message": f"pull 실패: {output}"}
    
    # 새 커밋 SHA 저장
    new_commit = get_local_commit(local_path)
    if new_commit:
        update_last_commit(owner, repo_name, new_commit)
    
    return {
        "status": "updated",
        "message": f"{local_commit[:7]} → {remote_commit[:7]}"
    }


def sync_all():
    """모든 구독 저장소 동기화"""
    config = load_config()
    token = config.get("GITHUB_TOKEN", "")
    repos_data = load_repos()
    subscriptions = repos_data.get("subscriptions", [])
    
    if not subscriptions:
        print("구독 중인 저장소가 없습니다.")
        print("  python gitclone.py owner/repo 로 저장소를 추가하세요.")
        return
    
    print(f"\n{'='*60}")
    print(f" 구독 저장소 동기화")
    print(f"{'='*60}")
    print(f"  총 {len(subscriptions)}개 저장소 확인")
    print("  (gitsync.py는 실행 시 모든 항목을 자동 업데이트합니다: auto_update 플래그 무시)")
    print()
    
    updated = 0
    up_to_date = 0
    errors = 0
    missing = 0
    
    for i, sub in enumerate(subscriptions, 1):
        repo = sub.get("repo", "알 수 없음")
        print(f"[{i}/{len(subscriptions)}] {repo}...", end=" ", flush=True)
        
        result = sync_repository(sub, token)
        status = result["status"]
        message = result["message"]
        
        if status == "updated":
            print(f"✅ 업데이트됨 ({message})")
            updated += 1
        elif status == "up-to-date":
            print(f"⬜ 최신 상태")
            up_to_date += 1
        elif status == "missing":
            print(f"⚠️ {message}")
            missing += 1
        else:
            print(f"❌ 오류: {message}")
            errors += 1
    
    # 결과 요약
    print(f"\n{'='*60}")
    print(f" 동기화 완료")
    print(f"{'='*60}")
    print(f"  ✅ 업데이트됨: {updated}개")
    print(f"  ⬜ 최신 상태: {up_to_date}개")
    if missing > 0:
        print(f"  ⚠️ 폴더 없음: {missing}개")
    if errors > 0:
        print(f"  ❌ 오류: {errors}개")
    print()


def list_subscriptions():
    """구독 목록 출력"""
    repos_data = load_repos()
    subscriptions = repos_data.get("subscriptions", [])
    
    if not subscriptions:
        print("구독 중인 저장소가 없습니다.")
        print("  python gitclone.py owner/repo 로 저장소를 추가하세요.")
        return
    
    print(f"\n{'='*60}")
    print(f" 구독 저장소 목록 ({len(subscriptions)}개)")
    print(f"{'='*60}")
    print()
    
    for i, sub in enumerate(subscriptions, 1):
        repo = sub.get("repo", "알 수 없음")
        local_path = sub.get("local_path", "알 수 없음")
        branch = sub.get("branch", "main")
        added = sub.get("added", "알 수 없음")
        last_commit = sub.get("last_commit", "")[:7] or "없음"
        
        exists = "✅" if os.path.exists(local_path) else "❌"
        
        print(f"{i}. {repo}")
        print(f"   {exists} 경로: {local_path}")
        print(f"   브랜치: {branch} | 추가일: {added} | 커밋: {last_commit}")
        print()


def remove_repo(repo_input: str, delete_local: bool = False):
    """구독 해제"""
    owner, repo_name = parse_repo_input(repo_input)
    repos_data = load_repos()
    
    # 구독 정보 찾기
    sub = find_subscription(repos_data, owner, repo_name)
    if not sub:
        print(f"오류: '{owner}/{repo_name}'은 구독 목록에 없습니다.")
        return False
    
    local_path = sub.get("local_path", "")
    
    # 구독 해제
    if remove_subscription(owner, repo_name):
        print(f"✅ '{owner}/{repo_name}' 구독이 해제되었습니다.")
        
        # 로컬 폴더 삭제 옵션
        if delete_local and local_path and os.path.exists(local_path):
            try:
                shutil.rmtree(local_path)
                print(f"   로컬 폴더도 삭제되었습니다: {local_path}")
            except Exception as e:
                print(f"   로컬 폴더 삭제 실패: {e}")
        elif local_path and os.path.exists(local_path):
            print(f"   로컬 폴더는 유지됩니다: {local_path}")
            print(f"   (삭제하려면 --delete-local 옵션 사용)")
        
        return True
    
    return False


def main():
    parser = argparse.ArgumentParser(
        description="GitHub 구독 저장소 동기화",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  python gitsync.py                          # 모든 구독 저장소 업데이트
  python gitsync.py --list                   # 구독 목록 확인
  python gitsync.py --remove owner/repo      # 구독 해제
  python gitsync.py --remove owner/repo --delete-local  # 구독 해제 + 폴더 삭제

저장소 추가:
  python gitclone.py owner/repo              # gitclone.py로 클론 + 구독 등록
        """
    )
    
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="구독 목록 확인"
    )
    
    parser.add_argument(
        "--remove",
        metavar="REPO",
        help="구독 해제 (owner/repo)"
    )
    
    parser.add_argument(
        "--delete-local",
        action="store_true",
        help="--remove 시 로컬 폴더도 삭제"
    )
    
    args = parser.parse_args()
    
    # 명령 실행
    if args.list:
        list_subscriptions()
    elif args.remove:
        success = remove_repo(args.remove, args.delete_local)
        sys.exit(0 if success else 1)
    else:
        # 기본 동작: 동기화
        sync_all()


if __name__ == "__main__":
    main()
