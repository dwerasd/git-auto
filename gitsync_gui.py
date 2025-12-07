#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# GitHub 구독 저장소 동기화 GUI
# 구독 목록 확인, 업데이트 체크, 선택/전체 업데이트
#

import json
import os
import subprocess
import sys
import threading
import tkinter as tk
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


def get_local_commit(repo_path: str) -> str | None:
    """로컬 저장소의 현재 HEAD 커밋 SHA"""
    success, output = run_git(["rev-parse", "HEAD"], repo_path)
    return output if success else None


def get_remote_commit(repo_path: str, branch: str = "main") -> str | None:
    """원격 저장소의 최신 커밋 SHA"""
    success, output = run_git(["rev-parse", f"origin/{branch}"], repo_path)
    return output if success else None


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
        
        # 트리뷰 이벤트 바인딩
        self.tree.bind("<Double-1>", self.on_tree_double_click)
        self.tree.bind("<Button-3>", self.on_tree_right_click)
        self.tree.bind("<Button-1>", self.on_tree_click)
        self.tree.bind("<space>", self.on_tree_space)
        
        # 행 색상 태그 설정
        self.tree.tag_configure("error", background="#ffcccc")  # 연한 빨간색
        self.tree.tag_configure("normal", background="")
        
        # 컨텍스트 메뉴 생성
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="📁 폴더 열기", command=self.menu_open_folder)
        self.context_menu.add_command(label="🌐 저장소 열기", command=self.menu_open_repo)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="⬇️ 업데이트", command=self.menu_update)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="🗑️ 삭제", command=self.menu_delete)
        
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
        
        for sub in self.subscriptions:
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
    
    def _toggle_auto_update(self, repo: str):
        """자동업데이트 토글"""
        # subscriptions에서 찾아서 토글
        for sub in self.subscriptions:
            if sub.get("repo") == repo:
                current = sub.get("auto_update", False)
                sub["auto_update"] = not current
                
                # repos.json 저장
                repos_data = load_repos()
                for s in repos_data.get("subscriptions", []):
                    if s.get("repo") == repo:
                        s["auto_update"] = not current
                        break
                save_repos(repos_data)
                
                # 트리뷰 업데이트
                values = list(self.tree.item(repo, "values"))
                values[5] = "✓" if not current else ""  # 여섯 번째 컬럼 (인덱스 5)
                self.tree.item(repo, values=values)
                
                status = "활성화" if not current else "비활성화"
                self.append_log(f"🔄 {repo} 자동업데이트 {status}\n", "info")
                break
    
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
            self.tree.selection_set(item)
            
            # 업데이트 메뉴 활성화/비활성화 결정
            repo = item
            result = self.check_results.get(repo, {})
            status = result.get("status", "")
            
            # 업데이트 가능한 경우에만 활성화
            if status == "update-available":
                self.context_menu.entryconfig("⬇️ 업데이트", state=tk.NORMAL)
            else:
                self.context_menu.entryconfig("⬇️ 업데이트", state=tk.DISABLED)
            
            # 폴더 열기 메뉴 - 폴더가 없으면 비활성화
            sub = next((s for s in self.subscriptions if s.get("repo") == repo), None)
            if sub and os.path.exists(sub.get("local_path", "")):
                self.context_menu.entryconfig("📁 폴더 열기", state=tk.NORMAL)
            else:
                self.context_menu.entryconfig("📁 폴더 열기", state=tk.DISABLED)
            
            self.context_menu.post(event.x_root, event.y_root)
    
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
        
        for sub in self.subscriptions:
            repo = sub.get("repo", "")
            local_path = sub.get("local_path", "")
            branch = sub.get("branch", "main")
            
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
        
        self.root.after(0, lambda: self.append_log(f"\n✅ 확인 완료: {update_count}개 업데이트 가능\n", "success"))
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
            if token:
                token_url = f"https://{token}@github.com/{owner}/{repo_name}.git"
                run_git(["remote", "set-url", "origin", token_url], local_path)
            
            success, output = run_git(["pull", "origin", branch], local_path)
            
            if token:
                clean_url = f"https://github.com/{owner}/{repo_name}.git"
                run_git(["remote", "set-url", "origin", clean_url], local_path)
            
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
                self.root.after(0, lambda r=repo, o=output: self.append_log(f"  ❌ 실패: {o}\n", "error"))
                self.root.after(0, lambda r=repo: self._update_tree_item(r, "⚠️", "업데이트 실패", True))
        
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
