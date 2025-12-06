#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# GitHub 저장소 초기화 스크립트
# 사용법:
#     python gitinit.py "C:\경로\폴더"           # private 저장소 생성 (기본)
#     python gitinit.py "C:\경로\폴더" --public  # public 저장소 생성
#

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib import request, error


SCRIPT_DIR = Path(__file__).parent
ENV_FILE = SCRIPT_DIR / ".env"
GITIGNORE_TEMPLATE = SCRIPT_DIR / ".gitignore"


def load_credentials() -> tuple[str, str]:
    """.env 파일에서 계정 정보 로드"""
    if not ENV_FILE.exists():
        print(f"오류: 설정 파일이 없습니다: {ENV_FILE}")
        print(".env 파일에 GITHUB_USER와 GITHUB_TOKEN을 설정하세요.")
        sys.exit(1)
    
    credentials = {}
    with open(ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                credentials[key.strip()] = value.strip()
    
    user = credentials.get("GITHUB_USER", "")
    token = credentials.get("GITHUB_TOKEN", "")
    
    if not user or not token:
        print("오류: .env에 GITHUB_USER와 GITHUB_TOKEN을 설정하세요.")
        sys.exit(1)
    
    return user, token


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


def github_api(token: str, endpoint: str, method: str = "GET", data: dict | None = None) -> tuple[bool, dict]:
    """GitHub API 호출"""
    url = f"https://api.github.com{endpoint}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "gitinit-python"
    }
    
    req_data = None
    if data:
        req_data = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    
    req = request.Request(url, data=req_data, headers=headers, method=method)
    
    try:
        with request.urlopen(req) as response:
            return True, json.loads(response.read().decode("utf-8"))
    except error.HTTPError as e:
        try:
            error_body = json.loads(e.read().decode("utf-8"))
            return False, error_body
        except:
            return False, {"message": str(e)}
    except Exception as e:
        return False, {"message": str(e)}


def check_repo_exists(user: str, token: str, repo_name: str) -> bool:
    """원격 저장소 존재 여부 확인"""
    success, _ = github_api(token, f"/repos/{user}/{repo_name}")
    return success


def create_repo(user: str, token: str, repo_name: str, private: bool = True) -> bool:
    """GitHub 저장소 생성"""
    data = {
        "name": repo_name,
        "private": private
    }
    
    # 개인 저장소 생성 시도
    success, response = github_api(token, "/user/repos", "POST", data)
    
    if success:
        return True
    
    # 실패 시 조직 저장소 생성 시도
    print(f"  개인 저장소 생성 실패, 조직 저장소로 시도...")
    success, response = github_api(token, f"/orgs/{user}/repos", "POST", data)
    
    if success:
        return True
    
    print(f"  오류: {response.get('message', '알 수 없는 오류')}")
    return False


def update_repo_visibility(user: str, token: str, repo_name: str, private: bool) -> bool:
    """저장소 공개/비공개 설정 변경"""
    data = {"private": private}
    success, response = github_api(token, f"/repos/{user}/{repo_name}", "PATCH", data)
    
    if not success:
        print(f"  경고: 공개 설정 변경 실패 - {response.get('message', '')}")
    
    return success


def init_repository(repo_path: str, public: bool = False):
    """저장소 초기화 및 푸시"""
    # 설정 로드
    github_user, github_token = load_credentials()
    
    repo_path = os.path.abspath(repo_path)
    repo_name = os.path.basename(repo_path)
    private = not public
    visibility = "public" if public else "private"
    
    print(f"\n{'='*50}")
    print(f" GitHub 저장소 초기화")
    print(f"{'='*50}")
    print(f"  경로: {repo_path}")
    print(f"  저장소: {github_user}/{repo_name}")
    print(f"  공개 설정: {visibility}")
    print()
    
    # 설정 확인
    if not github_user or not github_token:
        print("오류: .env에 GITHUB_USER와 GITHUB_TOKEN을 설정하세요.")
        return False
    
    # 경로 확인
    if not os.path.exists(repo_path):
        print(f"오류: 경로가 존재하지 않습니다: {repo_path}")
        return False
    
    os.chdir(repo_path)
    
    # 1. .gitignore 복사
    print("[1/6] .gitignore 설정...")
    target_gitignore = Path(repo_path) / ".gitignore"
    if target_gitignore.exists():
        # 타겟 폴더에 이미 .gitignore가 있으면 덮어쓰지 않음
        print("  .gitignore 이미 존재 (기존 파일 유지)")
    elif GITIGNORE_TEMPLATE.exists():
        shutil.copy(GITIGNORE_TEMPLATE, target_gitignore)
        print("  .gitignore 복사 완료")
    else:
        print(f"  경고: .gitignore 템플릿 없음 ({GITIGNORE_TEMPLATE})")
    
    # 2. Git 초기화
    print("[2/6] Git 초기화...")
    if os.path.exists(os.path.join(repo_path, ".git")):
        shutil.rmtree(os.path.join(repo_path, ".git"))
        print("  기존 .git 폴더 삭제")
    
    success, output = run_git(["init"], repo_path)
    if not success:
        print(f"  오류: git init 실패 - {output}")
        return False
    
    run_git(["branch", "-M", "main"], repo_path)
    print("  초기화 완료")
    
    # 3. 파일 추가 및 커밋
    print("[3/6] 파일 추가 및 커밋...")
    run_git(["add", "."], repo_path)
    success, output = run_git(["commit", "-m", "Initial commit"], repo_path)
    if not success and "nothing to commit" not in output:
        print(f"  경고: {output}")
    print("  커밋 완료")
    
    # 4. 원격 저장소 확인/생성
    print("[4/6] 원격 저장소 확인...")
    if check_repo_exists(github_user, github_token, repo_name):
        print(f"  저장소 존재 → {visibility}로 설정 변경")
        update_repo_visibility(github_user, github_token, repo_name, private)
    else:
        print(f"  저장소 없음 → {visibility}로 생성")
        if not create_repo(github_user, github_token, repo_name, private):
            print("  오류: 저장소 생성 실패")
            return False
        print("  생성 완료")
    
    # 5. 푸시 (토큰 포함 URL 사용)
    print("[5/6] 푸시 중...")
    run_git(["remote", "remove", "origin"], repo_path)
    
    push_url = f"https://{github_user}:{github_token}@github.com/{github_user}/{repo_name}.git"
    run_git(["remote", "add", "origin", push_url], repo_path)
    
    success, output = run_git(["push", "--force", "-u", "origin", "main"], repo_path)
    if not success:
        print(f"  오류: 푸시 실패 - {output}")
        return False
    print("  푸시 완료")
    
    # 6. 보안을 위해 토큰 없는 URL로 변경
    print("[6/6] 정리...")
    run_git(["remote", "remove", "origin"], repo_path)
    clean_url = f"https://github.com/{github_user}/{repo_name}.git"
    run_git(["remote", "add", "origin", clean_url], repo_path)
    print("  원격 URL 정리 완료")
    
    print(f"\n{'='*50}")
    print(f" 완료!")
    print(f"{'='*50}")
    print(f"  https://github.com/{github_user}/{repo_name}")
    print()
    
    return True


def main():
    parser = argparse.ArgumentParser(
        description="GitHub 저장소 초기화 및 푸시",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  python gitinit.py "C:\\Projects\\myapp"           # private 저장소 (기본)
  python gitinit.py "C:\\Projects\\myapp" --public  # public 저장소
        """
    )
    
    parser.add_argument(
        "path",
        help="로컬 저장소 경로"
    )
    
    parser.add_argument(
        "--public", "-p",
        action="store_true",
        help="public 저장소로 생성 (기본: private)"
    )
    
    args = parser.parse_args()
    
    success = init_repository(args.path, args.public)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
