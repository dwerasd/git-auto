#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# GitHub 구독 저장소 동기화 GUI
# 구독 목록 확인, 업데이트 체크, 선택/전체 업데이트
#

import json
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk, scrolledtext, messagebox
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent
ENV_FILE = SCRIPT_DIR / ".env"
DATA_DIR = SCRIPT_DIR / "data"
REPOS_FILE = DATA_DIR / "repos.json"
CONFIG_FILE = SCRIPT_DIR / "gitsync_gui.json"


def load_env_config() -> dict:
    """.env 파일에서 설정 로드"""
    config = {"GITHUB_USER": "", "GITHUB_TOKEN": "", "CLONE_BASE_PATH": ""}
    if ENV_FILE.exists():
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    if key.strip() in config:
                        config[key.strip()] = value.strip()
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
    if not DATA_DIR.exists():
        DATA_DIR.mkdir(parents=True)
    with open(REPOS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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
    """git 출력이 머지 충돌(미병합 파일) 또는 히스토리 불일치로 인한 실패인지 여부"""
    if not git_output:
        return False
    text = git_output.lower()
    return (
        "unmerged" in text
        or "unmerged files" in text
        or "fix conflicts" in text
        or "unresolved conflict" in text
        or "you have unmerged paths" in text
        or "unrelated histories" in text  # 히스토리 완전 불일치(force push 등)
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
    """원격 저장소의 최신 커밋 SHA"""
    success, output = run_git(["rev-parse", f"origin/{branch}"], repo_path)
    return output if success else None


def get_behind_ahead_count(repo_path: str, branch: str) -> tuple[int, int]:
    """로컬이 원격보다 뒤처진(behind)/앞선(ahead) 커밋 수 반환
    
    Returns:
        (behind_count, ahead_count)
    """
    # behind: HEAD..origin/branch
    ok1, out1 = run_git(["rev-list", "--count", f"HEAD..origin/{branch}"], repo_path)
    behind = int(out1) if ok1 and out1.isdigit() else 0
    
    # ahead: origin/branch..HEAD
    ok2, out2 = run_git(["rev-list", "--count", f"origin/{branch}..HEAD"], repo_path)
    ahead = int(out2) if ok2 and out2.isdigit() else 0
    
    return behind, ahead


class GitSyncGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("GitHub Sync")
        self.root.minsize(800, 500)
        
        # 상태
        self.is_running = False
        self.subscriptions = []
        self.check_results = {}  # repo -> {"status": ..., "local": ..., "remote": ...}
        
        # 설정 로드
        self.gui_config = self.load_gui_config()
        self.env_config = load_env_config()
        self.restore_window_geometry()
        
        self.setup_ui()
        
        # 창 닫기 이벤트
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # 시작 시 목록 로드 후 자동으로 업데이트 확인
        self.root.after(100, self._startup_check)
    
    def load_gui_config(self) -> dict:
        """GUI 설정 파일 로드"""
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}
    
    def save_gui_config(self):
        """GUI 설정 파일 저장"""
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.gui_config, f, indent=2)
        except Exception:
            pass
    
    def restore_window_geometry(self):
        """저장된 창 위치/크기 복원"""
        if "geometry" in self.gui_config:
            try:
                self.root.geometry(self.gui_config["geometry"])
            except Exception:
                self.root.geometry("900x600")
                self.center_window()
        else:
            self.root.geometry("900x600")
            self.center_window()
    
    def center_window(self):
        """창을 화면 중앙에 배치"""
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (width // 2)
        y = (self.root.winfo_screenheight() // 2) - (height // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
    
    def on_closing(self):
        """창 닫기 이벤트 처리"""
        self.gui_config["geometry"] = self.root.geometry()
        self.save_gui_config()
        self.root.destroy()
    
    def setup_ui(self):
        """UI 구성"""
        # 메인 프레임
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 저장소 목록 (Treeview)
        list_frame = ttk.LabelFrame(main_frame, text="구독 저장소 목록", padding="5")
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        # Treeview with scrollbar
        tree_frame = ttk.Frame(list_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        
        columns = ("status", "repo", "branch", "local_path", "update_info", "auto_update")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="extended")
        
        self.tree.heading("status", text="상태")
        self.tree.heading("repo", text="저장소")
        self.tree.heading("branch", text="브랜치")
        self.tree.heading("local_path", text="로컬 경로")
        self.tree.heading("update_info", text="업데이트 정보")
        self.tree.heading("auto_update", text="자동")
        
        self.tree.column("status", width=60, anchor="center")
        self.tree.column("repo", width=200)
        self.tree.column("branch", width=80, anchor="center")
        self.tree.column("local_path", width=300)
        self.tree.column("update_info", width=150)
        self.tree.column("auto_update", width=40, anchor="center")
        
        # Scrollbars
        v_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        h_scroll = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)
        
        self.tree.grid(row=0, column=0, sticky="nsew")
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")
        
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)
        
        # 드래그 앤 드롭 관련 변수
        self.drag_item = None
        self.drag_start_y = 0
        
        # 트리뷰 이벤트 바인딩
        self.tree.bind("<Double-1>", self.on_tree_double_click)
        self.tree.bind("<Button-3>", self.on_tree_right_click)
        self.tree.bind("<Button-1>", self.on_tree_click)
        self.tree.bind("<ButtonRelease-1>", self.on_tree_button_release)
        self.tree.bind("<B1-Motion>", self.on_tree_drag_motion)
        self.tree.bind("<space>", self.on_tree_space)
        self.tree.bind("<F5>", self.on_refresh_key)
        
        # 루트 윈도우에도 F5 바인딩 (어디서든 작동하도록)
        self.root.bind("<F5>", self.on_refresh_key)
        
        # 행 색상 태그 설정
        self.tree.tag_configure("error", background="#ffcccc")  # 연한 빨간색
        self.tree.tag_configure("normal", background="")
        
        # 컨텍스트 메뉴 생성
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="업데이트", command=self.menu_check_and_update)      # index 0
        self.context_menu.add_command(label="업데이트 확인", command=self.menu_check_selected_updates)  # index 1
        self.context_menu.add_separator()                                                          # index 2
        self.context_menu.add_command(label="폴더 열기", command=self.menu_open_folder)           # index 3
        self.context_menu.add_command(label="저장소 열기", command=self.menu_open_repo)           # index 4
        self.context_menu.add_separator()                                                          # index 5
        self.context_menu.add_command(label="자동업데이트 켜기(선택)", command=lambda: self.menu_set_auto_update_selected(True))   # index 6
        self.context_menu.add_command(label="자동업데이트 끄기(선택)", command=lambda: self.menu_set_auto_update_selected(False))  # index 7
        self.context_menu.add_separator()                                                          # index 8
        self.context_menu.add_command(label="강제 업데이트", command=self.menu_update)            # index 9
        self.context_menu.add_command(label="재다운로드(재클론)", command=self.menu_reclone)      # index 10
        self.context_menu.add_separator()                                                          # index 11
        self.context_menu.add_command(label="삭제", command=self.menu_delete)                     # index 12
        
        # 출력 영역
        output_frame = ttk.LabelFrame(main_frame, text="로그", padding="5")
        output_frame.pack(fill=tk.BOTH, expand=True)
        
        self.output = scrolledtext.ScrolledText(
            output_frame,
            wrap=tk.WORD,
            font=("Consolas", 9),
            height=8,
            state=tk.DISABLED,
            bg="#1e1e1e",
            fg="#d4d4d4"
        )
        self.output.pack(fill=tk.BOTH, expand=True)
        
        # 태그 설정
        self.output.tag_config("error", foreground="#f44747")
        self.output.tag_config("success", foreground="#6a9955")
        self.output.tag_config("info", foreground="#569cd6")
        self.output.tag_config("warning", foreground="#ce9178")
    
    def append_log(self, text: str, tag: str | None = None):
        """로그 출력"""
        self.output.config(state=tk.NORMAL)
        if tag:
            self.output.insert(tk.END, text, tag)
        else:
            self.output.insert(tk.END, text)
        self.output.see(tk.END)
        self.output.config(state=tk.DISABLED)

    def _abort_merge(self, repo_path: str) -> tuple[bool, str]:
        """진행 중인 merge를 취소"""
        return run_git(["merge", "--abort"], repo_path)

    def _backup_local_folder(self, repo_path: str) -> tuple[bool, str]:
        """강제 리셋 전 로컬 폴더를 백업 (unrelated histories 등 대비)
        
        Returns:
            (success, backup_path or error_message)
        """
        if not os.path.exists(repo_path):
            return True, "(폴더 없음)"
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"{repo_path}_backup_{timestamp}"
            shutil.copytree(repo_path, backup_path)
            return True, backup_path
        except Exception as e:
            return False, str(e)

    def _hard_reset_to_remote(self, repo_path: str, branch: str) -> tuple[bool, str]:
        """로컬 변경을 폐기하고 origin/branch로 강제 맞춤 (위험)"""
        ok, out = run_git(["reset", "--hard", f"origin/{branch}"], repo_path)
        if not ok:
            return ok, out
        ok2, out2 = run_git(["clean", "-fd"], repo_path)
        if not ok2:
            return ok2, out2
        return True, (out + "\n" + out2).strip()

    def _pull_with_token(self, repo: str, repo_path: str, branch: str, token: str) -> tuple[bool, str]:
        """토큰 설정/복원까지 포함한 pull 실행"""
        if token:
            try:
                owner, repo_name = repo.split("/")
                token_url = f"https://{token}@github.com/{owner}/{repo_name}.git"
                run_git(["remote", "set-url", "origin", token_url], repo_path)
            except Exception:
                pass

        success, output = run_git(["pull", "origin", branch], repo_path)

        if token:
            try:
                owner, repo_name = repo.split("/")
                clean_url = f"https://github.com/{owner}/{repo_name}.git"
                run_git(["remote", "set-url", "origin", clean_url], repo_path)
            except Exception:
                pass

        return success, output

    def _log_git_status_summary(self, repo_path: str):
        """충돌/실패 상황에서 원인 파악을 돕는 최소한의 상태 요약 로그"""
        ok, out = run_git(["status", "-sb"], repo_path)
        if ok and out:
            self.root.after(0, lambda o=out: self.append_log(f"  ℹ️ status -sb: {o}\n", "info"))
        ok2, out2 = run_git(["status", "--porcelain"], repo_path)
        if ok2 and out2:
            lines = out2.splitlines()
            preview = "\n".join(lines[:10])
            suffix = "\n  ..." if len(lines) > 10 else ""
            self.root.after(0, lambda p=preview, s=suffix: self.append_log(f"  ℹ️ status --porcelain:\n{p}{s}\n", "info"))

    def _auto_recover_and_pull(self, repo: str, repo_path: str, branch: str, token: str) -> tuple[bool, str]:
        """머지 충돌/미병합 파일이 있더라도 무인으로 최신 상태까지 맞추려 시도.

        전략:
          1) merge --abort
          2) pull 재시도
          3) 여전히 충돌이면 fetch 후 reset --hard origin/branch + clean -fd
          4) checkout -f branch (브랜치/DETACHED 등 꼬임 대비)
          5) 최종 pull
        """
        self._log_git_status_summary(repo_path)

        # 1) merge --abort
        self.root.after(0, lambda: self.append_log("  ▶ 자동 복구: merge --abort 시도\n", "warning"))
        ok_abort, out_abort = self._abort_merge(repo_path)
        if ok_abort:
            self.root.after(0, lambda: self.append_log("  ✅ merge --abort 완료\n", "info"))
        else:
            # merge 중이 아니면 실패할 수 있으니 정보성 로그만
            if out_abort:
                self.root.after(0, lambda o=out_abort: self.append_log(f"  ℹ️ merge --abort: {o}\n", "info"))

        # 2) pull 재시도
        self.root.after(0, lambda: self.append_log("  ▶ 재시도: pull\n", "warning"))
        ok_pull, out_pull = self._pull_with_token(repo, repo_path, branch, token)
        if ok_pull:
            return True, out_pull

        # 여전히 충돌/미병합이면 강제 맞춤
        if not (is_merge_conflict_error(out_pull) or has_unmerged_paths(repo_path)):
            return False, out_pull

        self.root.after(0, lambda: self.append_log("  ⚠️ 재시도도 충돌. 로컬을 원격으로 강제 맞춤합니다.\n", "warning"))

        # 강제 리셋 전 로컬 백업 (unrelated histories 등 대비)
        ok_backup, backup_result = self._backup_local_folder(repo_path)
        if ok_backup and backup_result != "(폴더 없음)":
            self.root.after(0, lambda b=backup_result: self.append_log(f"  📦 로컬 백업 완료: {b}\n", "info"))
        elif not ok_backup:
            self.root.after(0, lambda e=backup_result: self.append_log(f"  ⚠️ 백업 실패: {e}\n", "warning"))

        # 3) fetch
        ok_fetch, out_fetch = run_git(["fetch", "origin"], repo_path)
        if not ok_fetch:
            return False, f"fetch 실패: {out_fetch}"

        # 4) reset + clean
        ok_reset, out_reset = self._hard_reset_to_remote(repo_path, branch)
        if not ok_reset:
            return False, f"reset/clean 실패: {out_reset}"

        # 5) checkout -f branch
        run_git(["checkout", "-f", branch], repo_path)

        # 6) 최종 pull
        ok_pull2, out_pull2 = self._pull_with_token(repo, repo_path, branch, token)
        if ok_pull2:
            return True, out_pull2
        return False, out_pull2
    
    def clear_log(self):
        """로그 클리어"""
        self.output.config(state=tk.NORMAL)
        self.output.delete(1.0, tk.END)
        self.output.config(state=tk.DISABLED)
    
    def set_running(self, running: bool, status: str = ""):
        """실행 중 상태 설정"""
        self.is_running = running
    
    def refresh_list(self):
        """구독 목록 새로고침"""
        # 트리 클리어
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        self.check_results.clear()
        
        # 구독 목록 로드
        repos_data = load_repos()
        self.subscriptions = repos_data.get("subscriptions", [])
        
        # 자동업데이트 상태에 따라 정렬: 체크된 항목 먼저, 그 다음 체크 안 된 항목
        sorted_subs = sorted(self.subscriptions, key=lambda x: (not x.get("auto_update", True), self.subscriptions.index(x)))
        
        for sub in sorted_subs:
            repo = sub.get("repo", "")
            branch = sub.get("branch", "main")
            local_path = sub.get("local_path", "")
            auto_update = sub.get("auto_update", True)  # 기본값 True
            
            # 폴더 존재 여부
            if os.path.exists(local_path):
                status = "📁"
                update_info = "확인 전"
                tag = "normal"
            else:
                status = "📭"
                update_info = "폴더 없음"
                tag = "error"
            
            # 컬럼 순서: status, repo, branch, local_path, update_info, auto_update
            self.tree.insert("", tk.END, iid=repo, values=(
                status,
                repo,
                branch,
                local_path,
                update_info,
                "✓" if auto_update else ""
            ), tags=(tag,))
        
        self.append_log(f"📋 {len(self.subscriptions)}개 저장소 로드됨\n", "info")
    
    def _startup_check(self):
        """시작 시 목록 로드 후 자동 업데이트 확인 및 자동 업데이트 실행"""
        self.refresh_list()
        # 목록이 있으면 자동으로 업데이트 확인 시작
        if self.subscriptions:
            self.root.after(200, self._check_and_auto_update)
    
    def _check_and_auto_update(self):
        """업데이트 확인 후 자동업데이트 대상 업데이트"""
        if self.is_running:
            return
        thread = threading.Thread(target=self._check_and_auto_update_thread, daemon=True)
        thread.start()
    
    def _check_and_auto_update_thread(self):
        """업데이트 확인 + 자동업데이트 스레드"""
        # 먼저 업데이트 확인
        self._check_updates_thread()
        
        # 자동업데이트 대상 찾기
        auto_update_repos = []
        for sub in self.subscriptions:
            repo = sub.get("repo", "")
            if sub.get("auto_update", False):
                result = self.check_results.get(repo, {})
                if result.get("status") == "update-available":
                    auto_update_repos.append(repo)
        
        # 자동업데이트 실행
        if auto_update_repos:
            self.root.after(0, lambda: self.append_log(f"\n🔄 자동업데이트 대상: {len(auto_update_repos)}개\n", "info"))
            self._sync_repos(auto_update_repos)
        else:
            self.root.after(0, lambda: self.append_log(f"\n✅ 자동업데이트 대상 없음\n", "success"))
    
    def on_tree_click(self, event):
        """트리뷰 클릭 - 자동업데이트 컬럼 클릭 시 토글"""
        region = self.tree.identify_region(event.x, event.y)
        if region == "cell":
            column = self.tree.identify_column(event.x)
            item = self.tree.identify_row(event.y)
            if column == "#6" and item:  # 여섯 번째 컬럼 (자동업데이트 - 맨 오른쪽)
                self._toggle_auto_update(item)
    
    def on_tree_space(self, event):
        """트리뷰 스페이스 키 - 선택한 저장소 자동업데이트 토글"""
        selection = self.tree.selection()
        if selection:
            for repo in selection:
                self._toggle_auto_update(repo)
    
    def on_refresh_key(self, event):
        """F5 키 - 전체 리스트 갱신"""
        if not self.is_running:
            self.append_log("\n🔄 F5 - 리스트 갱신 중...\n", "info")
            self.refresh_list()
            self.append_log("✅ 리스트 갱신 완료\n\n", "info")
    
    def on_tree_button_release(self, event):
        """마우스 버튼 릴리즈 - 드래그 앤 드롭 완료"""
        if self.drag_item:
            # 드롭 위치 확인
            drop_target = self.tree.identify_row(event.y)
            
            if drop_target and drop_target != self.drag_item:
                # 드래그한 항목과 드롭 위치가 다른 경우 순서 변경
                self._reorder_items(self.drag_item, drop_target)
            
            # 드래그 상태 초기화
            self.drag_item = None
            self.drag_start_y = 0
    
    def on_tree_drag_motion(self, event):
        """마우스 드래그 중"""
        if not self.drag_item:
            # 드래그 시작
            item = self.tree.identify_row(event.y)
            if item:
                self.drag_item = item
                self.drag_start_y = event.y
        else:
            # 드래그 중 - 시각적 피드백을 위해 선택 유지
            drop_target = self.tree.identify_row(event.y)
            if drop_target:
                self.tree.selection_set(drop_target)
    
    def _reorder_items(self, source_item: str, target_item: str):
        """트리뷰와 JSON에서 항목 순서 변경 (같은 그룹 내에서만)"""
        try:
            # 소스와 타겟의 auto_update 상태 확인
            source_sub = next((s for s in self.subscriptions if s.get("repo") == source_item), None)
            target_sub = next((s for s in self.subscriptions if s.get("repo") == target_item), None)
            
            if not source_sub or not target_sub:
                return
            
            source_auto = source_sub.get("auto_update", True)
            target_auto = target_sub.get("auto_update", True)
            
            # 같은 그룹(체크/미체크)이 아니면 이동 불가
            if source_auto != target_auto:
                self.append_log(f"⚠️ 같은 그룹 내에서만 순서를 변경할 수 있습니다\n", "warning")
                return
            
            # 현재 모든 항목의 순서 가져오기
            all_items = self.tree.get_children()
            items_list = list(all_items)
            
            # 소스와 타겟의 인덱스 찾기
            source_idx = items_list.index(source_item)
            target_idx = items_list.index(target_item)
            
            # 리스트에서 순서 변경
            items_list.insert(target_idx, items_list.pop(source_idx))
            
            # 트리뷰 순서 재정렬
            for idx, item in enumerate(items_list):
                self.tree.move(item, "", idx)
            
            # subscriptions 순서도 변경 (같은 순서로 재정렬)
            new_subscriptions = []
            for item in items_list:
                sub = next((s for s in self.subscriptions if s.get("repo") == item), None)
                if sub:
                    new_subscriptions.append(sub)
            
            self.subscriptions = new_subscriptions
            
            # repos.json에 저장
            repos_data = load_repos()
            repos_data["subscriptions"] = self.subscriptions
            save_repos(repos_data)
            
            self.append_log(f"📋 '{source_item}' 위치를 '{target_item}' 위치로 이동\n", "info")
        
        except Exception as e:
            self.append_log(f"❌ 순서 변경 실패: {str(e)}\n", "error")
    
    def _toggle_auto_update(self, repo: str):
        """자동업데이트 토글 및 위치 이동"""
        # subscriptions에서 찾아서 토글
        for idx, sub in enumerate(self.subscriptions):
            if sub.get("repo") == repo:
                current = sub.get("auto_update", False)
                new_state = not current
                sub["auto_update"] = new_state
                
                # subscriptions에서 제거
                removed_sub = self.subscriptions.pop(idx)
                
                # 새 위치 결정
                if new_state:
                    # 체크 활성화: 체크된 항목들의 맨 아래로 이동
                    # 체크된 항목들 중 마지막 인덱스 찾기
                    last_checked_idx = -1
                    for i, s in enumerate(self.subscriptions):
                        if s.get("auto_update", True):
                            last_checked_idx = i
                    
                    # 체크된 항목들의 바로 다음에 삽입
                    insert_idx = last_checked_idx + 1
                else:
                    # 체크 해제: 맨 아래로 이동
                    insert_idx = len(self.subscriptions)
                
                # 새 위치에 삽입
                self.subscriptions.insert(insert_idx, removed_sub)
                
                # repos.json 저장
                repos_data = load_repos()
                repos_data["subscriptions"] = self.subscriptions
                save_repos(repos_data)
                
                # 트리뷰 전체 갱신 (순서가 변경되므로)
                self._refresh_tree_order()
                
                status = "활성화" if new_state else "비활성화"
                position = "체크된 항목들의 맨 아래" if new_state else "맨 아래"
                self.append_log(f"🔄 {repo} 자동업데이트 {status} → {position}로 이동\n", "info")
                break
    
    def _refresh_tree_order(self):
        """트리뷰 순서를 subscriptions 순서에 맞게 갱신"""
        # 현재 트리의 모든 항목 상태 저장
        tree_data = {}
        for item in self.tree.get_children():
            values = self.tree.item(item, "values")
            tags = self.tree.item(item, "tags")
            tree_data[item] = {"values": values, "tags": tags}
        
        # 트리 클리어
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        # subscriptions 순서대로 다시 삽입
        for sub in self.subscriptions:
            repo = sub.get("repo", "")
            if repo in tree_data:
                data = tree_data[repo]
                # auto_update 컬럼 업데이트
                values = list(data["values"])
                values[5] = "✓" if sub.get("auto_update", True) else ""
                self.tree.insert("", tk.END, iid=repo, values=tuple(values), tags=data["tags"])
    
    def _get_selected_repo(self) -> dict | None:
        """현재 선택된 저장소 정보 반환"""
        selection = self.tree.selection()
        if not selection:
            return None
        repo = selection[0]
        return next((s for s in self.subscriptions if s.get("repo") == repo), None)
    
    def on_tree_double_click(self, event):
        """트리뷰 더블클릭 - 폴더 열기"""
        sub = self._get_selected_repo()
        if sub:
            local_path = sub.get("local_path", "")
            if os.path.exists(local_path):
                os.startfile(local_path)
            else:
                messagebox.showwarning("경고", f"폴더가 존재하지 않습니다:\n{local_path}")
    
    def on_tree_right_click(self, event):
        """트리뷰 우클릭 - 컨텍스트 메뉴"""
        # 클릭한 위치의 항목 선택
        item = self.tree.identify_row(event.y)
        if item:
            # 여러개 선택된 상태에서 우클릭하면 selection을 유지해야 함.
            # 단, 우클릭한 항목이 현재 selection에 없으면 해당 항목만 선택.
            current_selection = set(self.tree.selection())
            if item not in current_selection:
                self.tree.selection_set(item)
            
            # 저장소 정보 확인
            repo = item
            sub = next((s for s in self.subscriptions if s.get("repo") == repo), None)
            
            # 업데이트 (index 0) - 선택된 항목 중 로컬 폴더가 하나라도 있고, 작업 중이 아닐 때만 활성화
            if not self.is_running:
                selections = list(self.tree.selection())
                has_any_local = False
                for r in selections:
                    s = next((x for x in self.subscriptions if x.get("repo") == r), None)
                    if s and os.path.exists(s.get("local_path", "")):
                        has_any_local = True
                        break
                self.context_menu.entryconfig(0, state=(tk.NORMAL if has_any_local else tk.DISABLED))
            else:
                self.context_menu.entryconfig(0, state=tk.DISABLED)

            # 업데이트 확인 (index 1) - 작업 중이 아닐 때만 활성화 (auto_update 꺼짐도 허용)
            if not self.is_running:
                self.context_menu.entryconfig(1, state=tk.NORMAL)
            else:
                self.context_menu.entryconfig(1, state=tk.DISABLED)

            # 자동업데이트 일괄 ON/OFF (index 6, 7) - 선택이 있고 작업 중이 아닐 때만 활성화
            if not self.is_running and self.tree.selection():
                self.context_menu.entryconfig(6, state=tk.NORMAL)
                self.context_menu.entryconfig(7, state=tk.NORMAL)
            else:
                self.context_menu.entryconfig(6, state=tk.DISABLED)
                self.context_menu.entryconfig(7, state=tk.DISABLED)
            
            # 강제 업데이트 메뉴 (index 9) 활성화/비활성화 결정
            result = self.check_results.get(repo, {})
            status = result.get("status", "")
            
            # 업데이트 가능한 경우에만 활성화
            if status == "update-available":
                self.context_menu.entryconfig(9, state=tk.NORMAL)
            else:
                self.context_menu.entryconfig(9, state=tk.DISABLED)

            # 재다운로드(재클론) (index 10) - 작업 중이 아니면 활성화 (폴더 없어도 가능)
            if not self.is_running:
                self.context_menu.entryconfig(10, state=tk.NORMAL)
            else:
                self.context_menu.entryconfig(10, state=tk.DISABLED)
            
            # 폴더 열기 메뉴 (index 3) - 폴더가 없으면 비활성화
            if sub and os.path.exists(sub.get("local_path", "")):
                self.context_menu.entryconfig(3, state=tk.NORMAL)
            else:
                self.context_menu.entryconfig(3, state=tk.DISABLED)
            
            self.context_menu.post(event.x_root, event.y_root)

    def menu_set_auto_update_selected(self, new_state: bool):
        """컨텍스트 메뉴: 선택한(1개 또는 여러개) 저장소의 자동업데이트를 일괄 설정"""
        if self.is_running:
            return

        selection = list(self.tree.selection())
        if not selection:
            return

        # subscriptions에서 일괄 반영
        changed = 0
        selected_set = set(selection)
        for sub in self.subscriptions:
            repo = sub.get("repo")
            if repo in selected_set:
                if sub.get("auto_update", False) != new_state:
                    sub["auto_update"] = new_state
                    changed += 1

        # 변화가 없다면 그대로 종료
        if changed == 0:
            return

        # auto_update 그룹 정렬(ON 먼저) + 같은 그룹 내에서는 현재 순서 유지
        def _group_key(s: dict) -> int:
            return 0 if s.get("auto_update", True) else 1

        # stable sort라서 기존 순서가 유지됨
        self.subscriptions.sort(key=_group_key)

        # repos.json 저장
        repos_data = load_repos()
        repos_data["subscriptions"] = self.subscriptions
        save_repos(repos_data)

        # 트리뷰 갱신
        self._refresh_tree_order()

        state_text = "활성화" if new_state else "비활성화"
        self.append_log(f"🔁 선택 {len(selection)}개 자동업데이트 {state_text} (변경 {changed}개)\n", "info")
    
    def menu_check_and_update(self):
        """컨텍스트 메뉴: 선택한(1개 또는 여러개) 저장소를 업데이트 확인 후 필요시 업데이트"""
        if self.is_running:
            return

        selection = list(self.tree.selection())
        if not selection:
            return

        thread = threading.Thread(target=self._check_and_update_selected_thread, args=(selection,), daemon=True)
        thread.start()

    def _check_and_update_selected_thread(self, repos: list[str]):
        """선택 저장소들을 순차 업데이트(확인+필요시 pull)"""
        self.root.after(0, lambda: self.set_running(True, f"업데이트 중... ({len(repos)}개)"))
        self.root.after(0, lambda: self.append_log(f"\n⬇️ 선택 {len(repos)}개 저장소 업데이트 시작\n", "info"))

        for repo in repos:
            # 단일 업데이트 루틴을 재사용하되, running 상태는 바깥에서 관리
            self._check_and_update_single_thread(repo, manage_running=False)

        self.root.after(0, lambda: self.append_log("\n✅ 선택 업데이트 완료\n\n", "success"))
        self.root.after(0, lambda: self.set_running(False))

    def menu_check_selected_updates(self):
        """컨텍스트 메뉴: 선택한(1개 또는 여러개) 저장소 업데이트 확인만 수행"""
        if self.is_running:
            return

        selection = list(self.tree.selection())
        if not selection:
            return

        thread = threading.Thread(target=self._check_selected_updates_thread, args=(selection,), daemon=True)
        thread.start()

    def _check_selected_updates_thread(self, repos: list[str]):
        """선택 저장소들의 업데이트 확인 스레드 (auto_update=False도 강제 체크)"""
        self.root.after(0, lambda: self.set_running(True, "업데이트 확인 중..."))
        self.root.after(0, lambda: self.append_log(f"\n🔍 선택 {len(repos)}개 저장소 업데이트 확인\n", "info"))

        token = self.env_config.get("GITHUB_TOKEN", "")
        update_count = 0
        error_count = 0

        for repo in repos:
            sub = next((s for s in self.subscriptions if s.get("repo") == repo), None)
            if not sub:
                error_count += 1
                self.root.after(0, lambda r=repo: self.append_log(f"  ❌ {r}: 설정 정보를 찾을 수 없음\n", "error"))
                continue

            local_path = sub.get("local_path", "")
            branch = sub.get("branch", "main")

            # 폴더/깃 확인
            if not os.path.exists(local_path):
                error_count += 1
                self.check_results[repo] = {"status": "missing", "message": "폴더 없음"}
                self.root.after(0, lambda r=repo: self._update_tree_item(r, "📭", "폴더 없음", True))
                continue

            if not os.path.exists(os.path.join(local_path, ".git")):
                error_count += 1
                self.check_results[repo] = {"status": "not-git", "message": "Git 저장소 아님"}
                self.root.after(0, lambda r=repo: self._update_tree_item(r, "⚠️", "Git 저장소 아님", True))
                continue

            # fetch
            if token:
                try:
                    owner, repo_name = repo.split("/")
                    token_url = f"https://{token}@github.com/{owner}/{repo_name}.git"
                    run_git(["remote", "set-url", "origin", token_url], local_path)
                except Exception:
                    pass

            success, output = run_git(["fetch", "origin"], local_path)

            if token:
                try:
                    owner, repo_name = repo.split("/")
                    clean_url = f"https://github.com/{owner}/{repo_name}.git"
                    run_git(["remote", "set-url", "origin", clean_url], local_path)
                except Exception:
                    pass

            if not success:
                error_count += 1
                self.check_results[repo] = {"status": "fetch-failed", "message": output}
                self.root.after(0, lambda r=repo: self._update_tree_item(r, "⚠️", "fetch 실패", True))
                self.root.after(0, lambda r=repo, o=output: self.append_log(f"  ❌ {r}: fetch 실패: {o}\n", "error"))
                continue

            # commit 비교
            local_commit = get_local_commit(local_path)
            remote_commit = get_remote_commit(local_path, branch)

            if not local_commit or not remote_commit:
                error_count += 1
                self.check_results[repo] = {"status": "commit-failed", "message": "커밋 정보 확인 실패"}
                self.root.after(0, lambda r=repo: self._update_tree_item(r, "⚠️", "커밋 확인 실패", True))
                continue

            # behind/ahead 확인
            behind, ahead = get_behind_ahead_count(local_path, branch)
            
            if behind == 0 and ahead == 0:
                self.check_results[repo] = {"status": "up-to-date", "local": local_commit, "remote": remote_commit}
                self.root.after(0, lambda r=repo, c=local_commit: self._update_tree_item(r, "✅", f"최신({c[:7]})"))
            elif behind == 0 and ahead > 0:
                # 로컬이 앞서있음 (원격 force push?) - 강제 리셋 필요
                update_count += 1
                self.check_results[repo] = {"status": "update-available", "local": local_commit, "remote": remote_commit, "ahead": ahead}
                self.root.after(0, lambda r=repo, a=ahead: self._update_tree_item(r, "⚠️", f"강제리셋필요(ahead {a})"))
            else:
                update_count += 1
                self.check_results[repo] = {"status": "update-available", "local": local_commit, "remote": remote_commit, "behind": behind}
                self.root.after(0, lambda r=repo, b=behind: self._update_tree_item(r, "🔄", f"업데이트 가능({b}커밋)"))

        self.root.after(0, lambda: self.append_log(
            f"\n✅ 선택 업데이트 확인 완료: {update_count}개 업데이트 가능 | ❌ {error_count}개 오류\n\n",
            "success" if error_count == 0 else "warning"
        ))
        self.root.after(0, lambda: self.set_running(False))
    
    def _check_and_update_single_thread(self, repo: str, manage_running: bool = True):
        """단일 저장소 업데이트 확인 및 업데이트 스레드"""
        if manage_running:
            self.root.after(0, lambda: self.set_running(True, f"{repo} 확인 중..."))
        
        sub = next((s for s in self.subscriptions if s.get("repo") == repo), None)
        if not sub:
            self.root.after(0, lambda: self.append_log(f"❌ {repo} 정보를 찾을 수 없음\n"))
            if manage_running:
                self.root.after(0, lambda: self.set_running(False))
            return
        
        local_path = sub.get("local_path", "")
        branch = sub.get("branch", "main")
        token = self.env_config.get("GITHUB_TOKEN", "")
        
        self.root.after(0, lambda: self.append_log(f"\n🔍 {repo} 업데이트 확인 중...\n"))
        
        # 1. 폴더 존재 확인
        if not os.path.exists(local_path):
            self.root.after(0, lambda: self.append_log(f"  ❌ 로컬 폴더 없음: {local_path}\n"))
            if manage_running:
                self.root.after(0, lambda: self.set_running(False))
            return
        
        if not os.path.exists(os.path.join(local_path, ".git")):
            self.root.after(0, lambda: self.append_log(f"  ❌ Git 저장소 아님\n"))
            if manage_running:
                self.root.after(0, lambda: self.set_running(False))
            return
        
        # 2. Fetch
        self.root.after(0, lambda: self.append_log(f"  📡 원격 정보 가져오는 중...\n"))
        
        if token:
            owner, repo_name = repo.split("/")
            token_url = f"https://{token}@github.com/{owner}/{repo_name}.git"
            run_git(["remote", "set-url", "origin", token_url], local_path)
        
        success, output = run_git(["fetch", "origin"], local_path)
        
        if token:
            owner, repo_name = repo.split("/")
            clean_url = f"https://github.com/{owner}/{repo_name}.git"
            run_git(["remote", "set-url", "origin", clean_url], local_path)
        
        if not success:
            self.root.after(0, lambda: self.append_log(f"  ❌ fetch 실패: {output}\n"))
            if manage_running:
                self.root.after(0, lambda: self.set_running(False))
            return
        
        # 3. 커밋 비교
        local_commit = get_local_commit(local_path)
        remote_commit = get_remote_commit(local_path, branch)
        
        if not local_commit or not remote_commit:
            self.root.after(0, lambda: self.append_log(f"  ❌ 커밋 정보 확인 실패\n"))
            if manage_running:
                self.root.after(0, lambda: self.set_running(False))
            return
        
        # 4. behind/ahead 확인
        behind, ahead = get_behind_ahead_count(local_path, branch)
        
        if behind == 0 and ahead == 0:
            self.root.after(0, lambda: self.append_log(f"  ✅ 이미 최신 버전입니다\n"))
            self.root.after(0, lambda: self.append_log(f"  커밋: {local_commit[:7]}\n\n"))
            if manage_running:
                self.root.after(0, lambda: self.set_running(False))
            return
        
        if behind == 0 and ahead > 0:
            # 로컬이 앞서있음 (원격 force push?) - 강제 리셋 필요
            self.root.after(0, lambda: self.append_log(f"  ⚠️ 로컬이 {ahead}커밋 앞서있음 (원격 force push?). 강제 리셋 시도...\n", "warning"))
            # 백업 후 강제 리셋
            ok_backup, backup_result = self._backup_local_folder(local_path)
            if ok_backup and backup_result != "(폴더 없음)":
                self.root.after(0, lambda b=backup_result: self.append_log(f"  📦 로컬 백업: {b}\n", "info"))
            ok_reset, out_reset = self._hard_reset_to_remote(local_path, branch)
            if not ok_reset:
                self.root.after(0, lambda o=out_reset: self.append_log(f"  ❌ 강제 리셋 실패: {o}\n", "error"))
                if manage_running:
                    self.root.after(0, lambda: self.set_running(False))
                return
            new_commit = get_local_commit(local_path)
            if new_commit:
                repos_data = load_repos()
                for s in repos_data.get("subscriptions", []):
                    if s.get("repo") == repo:
                        s["last_commit"] = new_commit
                        break
                save_repos(repos_data)
            self.root.after(0, lambda: self.append_log(f"  ✅ 강제 리셋 완료: {local_commit[:7]} → {remote_commit[:7]}\n\n", "success"))
            self.root.after(0, lambda: self.tree.set(repo, "update_info", f"✅ 리셋 {remote_commit[:7]}"))
            if manage_running:
                self.root.after(0, lambda: self.set_running(False))
            return
        
        # 5. 업데이트 실행 (behind > 0)
        self.root.after(0, lambda: self.append_log(f"  🔄 업데이트 필요: {local_commit[:7]} → {remote_commit[:7]}\n"))
        self.root.after(0, lambda: self.append_log(f"  ⬇️ 업데이트 중...\n"))

        success, output = self._pull_with_token(repo, local_path, branch, token)

        if success:
            # 커밋 SHA 업데이트
            new_commit = get_local_commit(local_path)
            if new_commit:
                repos_data = load_repos()
                for s in repos_data.get("subscriptions", []):
                    if s.get("repo") == repo:
                        s["last_commit"] = new_commit
                        break
                save_repos(repos_data)
            
            self.root.after(0, lambda: self.append_log("  ✅ 업데이트 완료!\n", "success"))
            self.root.after(0, lambda: self.append_log(f"  새 커밋: {remote_commit[:7]}\n\n", "info"))
            
            # 트리뷰 업데이트
            self.root.after(0, lambda: self.tree.set(repo, "update_info", f"✅ {local_commit[:7]} → {remote_commit[:7]}"))
        else:
            if is_merge_conflict_error(output) or has_unmerged_paths(local_path):
                self.root.after(0, lambda: self.append_log("  ❌ 업데이트 실패: 머지 충돌(미병합 파일)이 있습니다.\n", "error"))
                ok2, out2 = self._auto_recover_and_pull(repo, local_path, branch, token)
                if ok2:
                    new_commit = get_local_commit(local_path)
                    if new_commit:
                        repos_data = load_repos()
                        for s in repos_data.get("subscriptions", []):
                            if s.get("repo") == repo:
                                s["last_commit"] = new_commit
                                break
                        save_repos(repos_data)
                    self.root.after(0, lambda: self.append_log("  ✅ 자동 복구 후 업데이트 완료!\n\n", "success"))
                    self.root.after(0, lambda: self.tree.set(repo, "update_info", "✅ 업데이트 완료"))
                else:
                    self.root.after(0, lambda o=out2: self.append_log(f"  ❌ 자동 복구 실패: {o}\n\n", "error"))
                    self.root.after(0, lambda: self.tree.set(repo, "update_info", "⚠️ 업데이트 실패"))
            else:
                self.root.after(0, lambda: self.append_log(f"  ❌ 업데이트 실패: {output}\n\n", "error"))
                self.root.after(0, lambda: self.tree.set(repo, "update_info", "❌ 업데이트 실패"))
        
        if manage_running:
            self.root.after(0, lambda: self.set_running(False))
    
    def menu_open_folder(self):
        """컨텍스트 메뉴: 폴더 열기"""
        sub = self._get_selected_repo()
        if sub:
            local_path = sub.get("local_path", "")
            if os.path.exists(local_path):
                os.startfile(local_path)
            else:
                messagebox.showwarning("경고", f"폴더가 존재하지 않습니다:\n{local_path}")
    
    def menu_open_repo(self):
        """컨텍스트 메뉴: GitHub 저장소 열기"""
        sub = self._get_selected_repo()
        if sub:
            repo = sub.get("repo", "")
            if repo:
                import webbrowser
                webbrowser.open(f"https://github.com/{repo}")
    
    def menu_update(self):
        """컨텍스트 메뉴: 선택한 저장소 업데이트"""
        sub = self._get_selected_repo()
        if sub and not self.is_running:
            repo = sub.get("repo", "")
            thread = threading.Thread(target=self._sync_thread, args=([repo],), daemon=True)
            thread.start()

    def menu_reclone(self):
        """컨텍스트 메뉴: 선택한(1개 또는 여러개) 저장소를 로컬 삭제 후 재클론"""
        if self.is_running:
            return

        selection = list(self.tree.selection())
        if not selection:
            return

        # 위험 작업: 확인
        if not messagebox.askyesno(
            "재다운로드(재클론) 확인",
            f"선택한 {len(selection)}개 저장소의 로컬 폴더를 삭제한 뒤 다시 다운로드(클론)합니다.\n\n"
            "⚠️ 로컬 변경사항/미추적 파일은 모두 삭제됩니다.\n"
            "계속하시겠습니까?",
            icon="warning",
        ):
            return

        thread = threading.Thread(target=self._reclone_selected_thread, args=(selection,), daemon=True)
        thread.start()

    def _delete_folder_tree(self, local_path: str) -> tuple[bool, str]:
        """Windows 포함: 로컬 폴더를 최대한 강제로 삭제"""
        if not os.path.exists(local_path):
            return True, "(폴더 없음)"
        try:
            import shutil
            import stat

            def remove_readonly(func, path, excinfo):
                try:
                    os.chmod(path, stat.S_IWRITE)
                    func(path)
                except Exception:
                    pass

            shutil.rmtree(local_path, onerror=remove_readonly)
            return True, ""
        except Exception as e:
            # 대체 방법(rmdir)
            try:
                result = subprocess.run(
                    ["cmd", "/c", "rmdir", "/s", "/q", local_path],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    return True, ""
                return False, (result.stdout + result.stderr).strip() or str(e)
            except Exception as e2:
                return False, str(e2)

    def _clone_repo(self, repo_full: str, local_path: str, token: str) -> tuple[bool, str]:
        """지정 경로로 저장소를 클론한다. (기본: --recursive)"""
        try:
            owner, name = repo_full.split("/")
        except ValueError:
            return False, f"잘못된 repo 형식: {repo_full}"

        url = f"https://github.com/{owner}/{name}.git"
        if token:
            url = f"https://{token}@github.com/{owner}/{name}.git"

        parent = os.path.dirname(local_path)
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)

        # submodule 많은 repo 대비 --recursive
        return run_git(["clone", "--recursive", url, local_path], cwd=None)

    def _reclone_selected_thread(self, repos: list[str]):
        """선택 저장소들을 순차적으로 재클론"""
        self.root.after(0, lambda: self.set_running(True, f"재다운로드 중... ({len(repos)}개)"))
        self.root.after(0, lambda: self.append_log(f"\n♻️ 선택 {len(repos)}개 저장소 재다운로드(재클론) 시작\n", "info"))

        token = self.env_config.get("GITHUB_TOKEN", "")
        ok_count = 0
        fail_count = 0

        for repo in repos:
            sub = next((s for s in self.subscriptions if s.get("repo") == repo), None)
            if not sub:
                fail_count += 1
                self.root.after(0, lambda r=repo: self.append_log(f"  ❌ {r}: 설정 정보를 찾을 수 없음\n", "error"))
                continue

            local_path = sub.get("local_path", "")
            if not local_path:
                fail_count += 1
                self.root.after(0, lambda r=repo: self.append_log(f"  ❌ {r}: local_path가 비어있음\n", "error"))
                continue

            self.root.after(0, lambda r=repo: self.append_log(f"\n🧹 {r}: 로컬 폴더 정리 중...\n", "info"))
            self.root.after(0, lambda r=repo: self._update_tree_item(r, "♻️", "재다운로드 준비"))

            ok_del, out_del = self._delete_folder_tree(local_path)
            if not ok_del:
                fail_count += 1
                self.root.after(0, lambda r=repo, o=out_del: self.append_log(f"  ❌ 삭제 실패: {o}\n", "error"))
                self.root.after(0, lambda r=repo: self._update_tree_item(r, "⚠️", "삭제 실패", True))
                continue

            self.root.after(0, lambda r=repo: self.append_log(f"  ⬇️ {r}: 클론 중...\n", "info"))
            ok_clone, out_clone = self._clone_repo(repo, local_path, token)
            if not ok_clone:
                fail_count += 1
                self.root.after(0, lambda r=repo, o=out_clone: self.append_log(f"  ❌ 클론 실패: {o}\n", "error"))
                self.root.after(0, lambda r=repo: self._update_tree_item(r, "⚠️", "클론 실패", True))
                continue

            # last_commit 업데이트
            new_commit = get_local_commit(local_path)
            if new_commit:
                repos_data = load_repos()
                for s in repos_data.get("subscriptions", []):
                    if s.get("repo") == repo:
                        s["last_commit"] = new_commit
                        break
                save_repos(repos_data)

            ok_count += 1
            self.root.after(0, lambda r=repo: self.append_log("  ✅ 재다운로드 완료\n", "success"))
            self.root.after(0, lambda r=repo: self._update_tree_item(r, "✅", "재다운로드 완료"))

        self.root.after(0, lambda: self.append_log(
            f"\n✅ 재다운로드 완료: {ok_count}개 성공 | ❌ {fail_count}개 실패\n\n",
            "success" if fail_count == 0 else "warning",
        ))
        self.root.after(0, lambda: self.set_running(False))
    
    def menu_delete(self):
        """컨텍스트 메뉴: 선택한 저장소 삭제 (로컬 폴더 + JSON)"""
        sub = self._get_selected_repo()
        if not sub:
            return
        
        repo = sub.get("repo", "")
        local_path = sub.get("local_path", "")
        
        # 확인 대화상자
        result = messagebox.askyesno(
            "저장소 삭제 확인",
            f"다음 저장소를 삭제하시겠습니까?\n\n"
            f"저장소: {repo}\n"
            f"경로: {local_path}\n\n"
            f"⚠️ 경고: 로컬 폴더와 구독 정보가 모두 삭제됩니다!\n"
            f"이 작업은 되돌릴 수 없습니다.",
            icon='warning'
        )
        
        if not result:
            return
        
        self.append_log(f"\n🗑️ {repo} 삭제 중...\n")
        
        # 1. 로컬 폴더 삭제
        deleted_folder = False
        if os.path.exists(local_path):
            try:
                import shutil
                import stat
                
                self.append_log(f"  📁 로컬 폴더 삭제 중: {local_path}\n")
                
                # Windows에서 읽기 전용 파일 처리를 위한 오류 핸들러
                def remove_readonly(func, path, excinfo):
                    """읽기 전용 속성 제거 후 다시 시도"""
                    try:
                        os.chmod(path, stat.S_IWRITE)
                        func(path)
                    except Exception as e:
                        self.append_log(f"    ⚠️ 파일 삭제 실패: {path} - {e}\n")
                
                # shutil.rmtree with error handler
                shutil.rmtree(local_path, onerror=remove_readonly)
                self.append_log(f"  ✅ 로컬 폴더 삭제 완료\n")
                deleted_folder = True
            except PermissionError as e:
                # 권한 문제 발생 시 대체 방법 시도
                self.append_log(f"  ⚠️ 권한 오류 발생, 대체 방법 시도 중...\n")
                try:
                    # Windows의 rmdir /s /q 명령 사용
                    result = subprocess.run(
                        ["cmd", "/c", "rmdir", "/s", "/q", local_path],
                        capture_output=True,
                        text=True
                    )
                    if result.returncode == 0:
                        self.append_log(f"  ✅ 로컬 폴더 삭제 완료 (대체 방법)\n")
                        deleted_folder = True
                    else:
                        raise Exception(f"rmdir 실패: {result.stderr}")
                except Exception as e2:
                    self.append_log(f"  ❌ 대체 방법도 실패: {e2}\n")
                    messagebox.showerror(
                        "삭제 실패",
                        f"로컬 폴더 삭제 실패:\n{e}\n\n"
                        f"가능한 원인:\n"
                        f"1. 폴더나 파일이 다른 프로그램에서 사용 중\n"
                        f"2. 탐색기에서 해당 폴더를 열어둠\n"
                        f"3. 바이러스 백신이나 인덱싱 서비스가 파일 접근 중\n\n"
                        f"해결 방법:\n"
                        f"- 관련 프로그램을 모두 닫고 다시 시도\n"
                        f"- 탐색기를 닫고 다시 시도\n"
                        f"- 수동으로 폴더 삭제: {local_path}"
                    )
                    return
            except Exception as e:
                self.append_log(f"  ❌ 로컬 폴더 삭제 실패: {e}\n")
                messagebox.showerror("오류", f"로컬 폴더 삭제 실패:\n{e}")
                return
        else:
            self.append_log(f"  ⚠️ 로컬 폴더가 존재하지 않음\n")
        
        # 2. repos.json에서 제거
        try:
            repos_data = load_repos()
            original_count = len(repos_data.get("subscriptions", []))
            
            repos_data["subscriptions"] = [
                s for s in repos_data.get("subscriptions", [])
                if s.get("repo") != repo
            ]
            
            if len(repos_data["subscriptions"]) < original_count:
                save_repos(repos_data)
                self.append_log(f"  ✅ 구독 정보 삭제 완료\n")
            else:
                self.append_log(f"  ⚠️ 구독 정보를 찾을 수 없음\n")
            
            self.append_log(f"✅ {repo} 삭제 완료!\n\n")
            
            # 3. 목록 새로고침
            self.refresh_list()
            
            messagebox.showinfo(
                "삭제 완료",
                f"저장소가 삭제되었습니다:\n{repo}"
            )
            
        except Exception as e:
            self.append_log(f"  ❌ 구독 정보 삭제 실패: {e}\n")
            messagebox.showerror("오류", f"구독 정보 삭제 실패:\n{e}")
    
    def check_updates(self):
        """업데이트 확인 (fetch + 비교)"""
        if self.is_running:
            return
        
        thread = threading.Thread(target=self._check_updates_thread, daemon=True)
        thread.start()
    
    def _check_updates_thread(self):
        """업데이트 확인 스레드"""
        self.root.after(0, lambda: self.set_running(True, "업데이트 확인 중..."))
        self.root.after(0, self.clear_log)
        
        token = self.env_config.get("GITHUB_TOKEN", "")
        
        update_count = 0
        skipped_count = 0
        
        for sub in self.subscriptions:
            repo = sub.get("repo", "")
            local_path = sub.get("local_path", "")
            branch = sub.get("branch", "main")
            auto_update = sub.get("auto_update", True)  # 기본값 True
            
            # 자동업데이트가 체크되지 않은 경우 건너뛰기
            if not auto_update:
                self.root.after(0, lambda r=repo: self.append_log(f"⏭️ {r} 건너뜀 (자동업데이트 꺼짐)\n", "info"))
                self.check_results[repo] = {"status": "skipped", "message": "자동업데이트 꺼짐"}
                self.root.after(0, lambda r=repo: self._update_tree_item(r, "⏭️", "자동업데이트 꺼짐"))
                skipped_count += 1
                continue
            
            self.root.after(0, lambda r=repo: self.append_log(f"🔍 {r} 확인 중...\n"))
            
            # 폴더 없음
            if not os.path.exists(local_path):
                self.check_results[repo] = {"status": "missing", "message": "폴더 없음"}
                self.root.after(0, lambda r=repo: self._update_tree_item(r, "📭", "폴더 없음", True))
                continue
            
            # Git 저장소 아님
            if not os.path.exists(os.path.join(local_path, ".git")):
                self.check_results[repo] = {"status": "error", "message": "Git 저장소 아님"}
                self.root.after(0, lambda r=repo: self._update_tree_item(r, "⚠️", "Git 저장소 아님", True))
                continue
            
            # fetch
            owner, repo_name = repo.split("/")
            if token:
                token_url = f"https://{token}@github.com/{owner}/{repo_name}.git"
                run_git(["remote", "set-url", "origin", token_url], local_path)
            
            success, output = run_git(["fetch", "origin"], local_path)
            
            if token:
                clean_url = f"https://github.com/{owner}/{repo_name}.git"
                run_git(["remote", "set-url", "origin", clean_url], local_path)
            
            if not success:
                self.check_results[repo] = {"status": "error", "message": f"fetch 실패"}
                self.root.after(0, lambda r=repo: self._update_tree_item(r, "⚠️", "fetch 실패", True))
                continue
            
            # 커밋 비교
            local_commit = get_local_commit(local_path)
            remote_commit = get_remote_commit(local_path, branch)
            
            if not local_commit or not remote_commit:
                self.check_results[repo] = {"status": "error", "message": "커밋 확인 실패"}
                self.root.after(0, lambda r=repo: self._update_tree_item(r, "⚠️", "커밋 확인 실패", True))
                continue
            
            if local_commit == remote_commit:
                self.check_results[repo] = {"status": "up-to-date", "message": "최신 상태"}
                self.root.after(0, lambda r=repo: self._update_tree_item(r, "✅", "최신 상태"))
            else:
                update_count += 1
                msg = f"업데이트 있음 ({local_commit[:7]} → {remote_commit[:7]})"
                self.check_results[repo] = {
                    "status": "update-available",
                    "message": msg,
                    "local": local_commit,
                    "remote": remote_commit
                }
                self.root.after(0, lambda r=repo, m=msg: self._update_tree_item(r, "🔄", m))
                self.root.after(0, lambda r=repo: self.append_log(f"  ↳ 업데이트 가능\n", "warning"))
        
        msg = f"\n✅ 확인 완료: {update_count}개 업데이트 가능"
        if skipped_count > 0:
            msg += f", {skipped_count}개 건너뜀\n"
        else:
            msg += "\n"
        self.root.after(0, lambda: self.append_log(msg, "success"))
        self.root.after(0, lambda: self.set_running(False))
    
    def _update_tree_item(self, repo: str, status: str, update_info: str, is_error: bool = False):
        """트리뷰 항목 업데이트"""
        try:
            # 컬럼 순서: status(0), repo(1), branch(2), local_path(3), update_info(4), auto_update(5)
            values = list(self.tree.item(repo, "values"))
            values[0] = status  # 상태는 첫 번째 컬럼 (인덱스 0)
            values[4] = update_info  # 업데이트 정보는 다섯 번째 컬럼 (인덱스 4)
            tag = "error" if is_error else "normal"
            self.tree.item(repo, values=values, tags=(tag,))
        except Exception:
            pass
    
    def _sync_repos(self, repos: list[str]):
        """저장소 목록 동기화 (스레드 내에서 호출)"""
        token = self.env_config.get("GITHUB_TOKEN", "")
        
        updated = 0
        errors = 0
        
        for repo in repos:
            sub = next((s for s in self.subscriptions if s.get("repo") == repo), None)
            if not sub:
                continue
            
            local_path = sub.get("local_path", "")
            branch = sub.get("branch", "main")
            
            self.root.after(0, lambda r=repo: self.append_log(f"⬇️ {r} 업데이트 중...\n", "info"))
            
            if not os.path.exists(local_path):
                self.root.after(0, lambda r=repo: self.append_log(f"  📭 폴더 없음\n", "error"))
                self.root.after(0, lambda r=repo: self._update_tree_item(r, "📭", "폴더 없음", True))
                errors += 1
                continue
            
            owner, repo_name = repo.split("/")
            success, output = self._pull_with_token(repo, local_path, branch, token)
            
            if success:
                updated += 1
                new_commit = get_local_commit(local_path)
                self.root.after(0, lambda r=repo: self.append_log(f"  ✅ 업데이트 완료\n", "success"))
                self.root.after(0, lambda r=repo: self._update_tree_item(r, "✅", "최신 상태"))
                self.check_results[repo] = {"status": "up-to-date", "message": "최신 상태"}
                
                if new_commit:
                    self._update_last_commit(owner, repo_name, new_commit)
            else:
                errors += 1
                if is_merge_conflict_error(output) or has_unmerged_paths(local_path):
                    self.root.after(0, lambda r=repo: self.append_log("  ❌ 실패: 머지 충돌(미병합 파일)이 있습니다.\n", "error"))
                    ok2, out2 = self._auto_recover_and_pull(repo, local_path, branch, token)
                    if ok2:
                        updated += 1
                        new_commit = get_local_commit(local_path)
                        self.root.after(0, lambda r=repo: self.append_log("  ✅ 자동 복구 후 업데이트 완료\n", "success"))
                        self.root.after(0, lambda r=repo: self._update_tree_item(r, "✅", "최신 상태"))
                        self.check_results[repo] = {"status": "up-to-date", "message": "최신 상태"}
                        if new_commit:
                            self._update_last_commit(owner, repo_name, new_commit)
                        # errors는 복구 성공했으니 되돌림
                        errors -= 1
                    else:
                        self.root.after(0, lambda r=repo, o=out2: self.append_log(f"  ❌ 자동 복구 실패: {o}\n", "error"))
                        self.root.after(0, lambda r=repo: self._update_tree_item(r, "⚠️", "업데이트 실패", True))
                        self.check_results[repo] = {"status": "update-failed", "message": "업데이트 실패"}
                else:
                    self.root.after(0, lambda r=repo, o=output: self.append_log(f"  ❌ 실패: {o}\n", "error"))
                    self.root.after(0, lambda r=repo: self._update_tree_item(r, "⚠️", "업데이트 실패", True))
                    self.check_results[repo] = {"status": "update-failed", "message": "업데이트 실패"}
        
        return updated, errors
    
    def _sync_thread(self, repos: list[str]):
        """동기화 스레드"""
        self.root.after(0, lambda: self.set_running(True, "업데이트 중..."))
        self.root.after(0, self.clear_log)
        
        updated, errors = self._sync_repos(repos)
        
        self.root.after(0, lambda: self.append_log(f"\n{'='*50}\n"))
        self.root.after(0, lambda: self.append_log(f"✅ 업데이트: {updated}개 | ❌ 실패: {errors}개\n", "success" if errors == 0 else "warning"))
        self.root.after(0, lambda: self.set_running(False))
    
    def _update_last_commit(self, owner: str, repo_name: str, commit_sha: str):
        """마지막 커밋 SHA 업데이트"""
        repos_data = load_repos()
        repo_full = f"{owner}/{repo_name}"
        for sub in repos_data.get("subscriptions", []):
            if sub.get("repo") == repo_full:
                sub["last_commit"] = commit_sha
                save_repos(repos_data)
                break


def main():
    root = tk.Tk()
    
    # 스타일 설정
    style = ttk.Style()
    style.theme_use("clam")
    
    app = GitSyncGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
