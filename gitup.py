#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# GitHub 저장소 히스토리 초기화 스크립트
# 기존 저장소의 모든 커밋 히스토리를 삭제하고 "Initial commit" 하나로 리셋
#
# 사용법:
#     python gitup.py "C:\경로\폴더"           # 폴더명을 저장소 이름으로 사용
#     python gitup.py "C:\경로\폴더" --name myrepo  # 저장소 이름 직접 지정
#     python gitup.py "C:\경로\폴더" --public  # public 저장소로 생성
#
# 주의: 이 스크립트는 원격 저장소의 모든 히스토리를 삭제합니다!
#

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from urllib import request, error


def remove_readonly(func, path, excinfo):
    """읽기 전용 파일 삭제를 위한 오류 핸들러"""
    os.chmod(path, stat.S_IWRITE)
    func(path)


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
        "User-Agent": "gitup-python"
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


def delete_repo(user: str, token: str, repo_name: str) -> bool:
    """GitHub 저장소 삭제"""
    success, response = github_api(token, f"/repos/{user}/{repo_name}", "DELETE")
    if not success:
        print(f"  경고: 저장소 삭제 실패 - {response.get('message', '')}")
    return success


def create_repo(user: str, token: str, repo_name: str, private: bool = True) -> bool:
    """GitHub 저장소 생성"""
    data = {
        "name": repo_name,
        "private": private
    }
    
    success, response = github_api(token, "/user/repos", "POST", data)
    
    if success:
        return True
    
    print(f"  오류: {response.get('message', '알 수 없는 오류')}")
    return False


def confirm_action(repo_name: str) -> bool:
    """사용자 확인 프롬프트"""
    print()
    print("!" * 60)
    print("  경고: 이 작업은 원격 저장소의 모든 히스토리를 삭제합니다!")
    print(f"  저장소: {repo_name}")
    print("!" * 60)
    print()
    
    response = input("계속하시겠습니까? (y/N): ").strip().lower()
    return response == 'y'


def reset_repository(local_path: str, repo_name: str | None = None, public: bool = False, force: bool = False):
    """저장소 히스토리 초기화 및 재업로드"""
    
    # 설정 로드
    github_user, github_token = load_credentials()
    
    local_path = os.path.abspath(local_path)
    
    # 저장소 이름 결정: 명시적 지정 또는 폴더명 사용
    if repo_name:
        final_repo_name = repo_name
    else:
        final_repo_name = os.path.basename(local_path)
    
    private = not public
    visibility = "public" if public else "private"
    
    print(f"\n{'='*60}")
    print(f" GitHub 저장소 히스토리 초기화 (gitup)")
    print(f"{'='*60}")
    print(f"  로컬 경로: {local_path}")
    print(f"  저장소: {github_user}/{final_repo_name}")
    print(f"  공개 설정: {visibility}")
    print()
    
    # 경로 확인
    if not os.path.exists(local_path):
        print(f"오류: 경로가 존재하지 않습니다: {local_path}")
        return False
    
    # 확인 프롬프트 (--force 옵션이 없으면)
    if not force:
        if not confirm_action(final_repo_name):
            print("작업이 취소되었습니다.")
            return False
    
    # 1. .gitignore 복사
    print("[1/7] .gitignore 설정...")
    dest_gitignore = Path(local_path) / ".gitignore"
    if dest_gitignore.exists():
        # 타겟 폴더에 이미 .gitignore가 있으면 덮어쓰지 않음
        print("  .gitignore 이미 존재 (기존 파일 유지)")
    elif GITIGNORE_TEMPLATE.exists():
        shutil.copy(GITIGNORE_TEMPLATE, dest_gitignore)
        print("  .gitignore 복사 완료")
    else:
        print(f"  경고: .gitignore 템플릿 없음 ({GITIGNORE_TEMPLATE})")
    
    # 2. 기존 .git 폴더 삭제
    print("[2/7] 기존 Git 히스토리 삭제...")
    git_dir = os.path.join(local_path, ".git")
    if os.path.exists(git_dir):
        shutil.rmtree(git_dir, onexc=remove_readonly)
        print("  .git 폴더 삭제 완료")
    else:
        print("  .git 폴더 없음 (신규)")
    
    # 3. Git 초기화
    print("[3/7] Git 초기화...")
    success, output = run_git(["init"], local_path)
    if not success:
        print(f"  오류: git init 실패 - {output}")
        return False
    
    run_git(["branch", "-M", "main"], local_path)
    print("  초기화 완료 (main 브랜치)")
    
    # 4. README.md 생성 (없는 경우만)
    print("[4/7] README.md 확인...")
    readme_path = os.path.join(local_path, "README.md")
    if not os.path.exists(readme_path):
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(f"# {final_repo_name}\n")
        print("  README.md 생성 완료")
    else:
        print("  README.md 이미 존재 (유지)")
    
    # 5. 파일 추가 및 커밋
    print("[5/7] 파일 추가 및 커밋...")
    run_git(["add", "."], local_path)
    success, output = run_git(["commit", "-m", "Initial commit"], local_path)
    if not success and "nothing to commit" not in output:
        print(f"  경고: {output}")
    print("  Initial commit 완료")
    
    # 6. 원격 저장소 처리
    print("[6/7] 원격 저장소 처리...")
    if check_repo_exists(github_user, github_token, final_repo_name):
        print(f"  기존 저장소 존재 → 삭제 후 재생성")
        if delete_repo(github_user, github_token, final_repo_name):
            print("  삭제 완료")
            import time
            time.sleep(2)  # GitHub API 동기화 대기
        else:
            print("  경고: 삭제 실패 - force push로 시도")
    
    # 저장소 생성
    if not check_repo_exists(github_user, github_token, final_repo_name):
        print(f"  {visibility} 저장소 생성 중...")
        if not create_repo(github_user, github_token, final_repo_name, private):
            print("  오류: 저장소 생성 실패")
            return False
        print("  생성 완료")
        import time
        time.sleep(2)  # 저장소 생성 완료 대기
    
    # 7. 푸시
    print("[7/7] 푸시 중...")
    
    # 기존 remote 제거 (있으면)
    run_git(["remote", "remove", "origin"], local_path)
    
    # 토큰 포함 URL로 push
    push_url = f"https://{github_user}:{github_token}@github.com/{github_user}/{final_repo_name}.git"
    run_git(["remote", "add", "origin", push_url], local_path)
    
    success, output = run_git(["push", "-u", "origin", "main"], local_path)
    if not success:
        print(f"  일반 push 실패, force push 시도...")
        success, output = run_git(["push", "--force", "-u", "origin", "main"], local_path)
        if not success:
            print(f"  오류: 푸시 실패 - {output}")
            return False
    print("  푸시 완료")
    
    # 보안을 위해 토큰 없는 URL로 변경
    run_git(["remote", "remove", "origin"], local_path)
    clean_url = f"https://github.com/{github_user}/{final_repo_name}.git"
    run_git(["remote", "add", "origin", clean_url], local_path)
    print("  원격 URL 정리 완료 (토큰 제거)")
    
    print(f"\n{'='*60}")
    print(f" 완료!")
    print(f"{'='*60}")
    print(f"  저장소 URL: https://github.com/{github_user}/{final_repo_name}")
    print(f"  로컬 경로: {local_path}")
    print(f"  상태: 모든 히스토리가 'Initial commit'으로 리셋됨")
    print()
    
    return True


def main():
    parser = argparse.ArgumentParser(
        description="GitHub 저장소 히스토리 초기화 - 모든 커밋을 삭제하고 Initial commit으로 리셋",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  python gitup.py "E:\\projects\\myapp"                    # 폴더명을 저장소 이름으로
  python gitup.py "E:\\projects\\myapp" --name newrepo     # 저장소 이름 지정
  python gitup.py "E:\\projects\\myapp" --public           # public 저장소로
  python gitup.py "E:\\projects\\myapp" --force            # 확인 없이 실행

주의: 이 스크립트는 원격 저장소의 모든 히스토리를 삭제합니다!
        """
    )
    
    parser.add_argument(
        "path",
        help="로컬 저장소 경로"
    )
    
    parser.add_argument(
        "--name", "-n",
        help="GitHub 저장소 이름 (미지정시 폴더명 사용)"
    )
    
    parser.add_argument(
        "--public", "-p",
        action="store_true",
        help="public 저장소로 생성 (기본: private)"
    )
    
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="확인 프롬프트 없이 실행"
    )
    
    args = parser.parse_args()
    
    success = reset_repository(args.path, args.name, args.public, args.force)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
