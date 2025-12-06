#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# GitHub 저장소 클론 GUI
# 저장소 URL을 붙여넣기하고 Enter 또는 버튼 클릭으로 클론 실행
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
GITCLONE_SCRIPT = SCRIPT_DIR / "gitclone.py"
CONFIG_FILE = SCRIPT_DIR / "gitclone_gui.json"


class GitCloneGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("GitHub Clone")
        self.root.minsize(500, 400)
        
        # 클론 진행 중 플래그
        self.is_running = False
        
        # 설정 로드 및 창 위치/크기 복원
        self.config = self.load_config()
        self.restore_window_geometry()
        
        self.setup_ui()
        
        # 시작 시 입력창에 포커스
        self.entry.focus_set()
        
        # 창 닫기 이벤트 바인딩
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
    
    def load_config(self) -> dict:
        """설정 파일 로드"""
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}
    
    def save_config(self):
        """설정 파일 저장"""
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
        except Exception:
            pass
    
    def restore_window_geometry(self):
        """저장된 창 위치/크기 복원"""
        if "geometry" in self.config:
            try:
                self.root.geometry(self.config["geometry"])
            except Exception:
                # 잘못된 geometry면 기본값 사용
                self.root.geometry("700x500")
                self.center_window()
        else:
            # 설정이 없으면 기본값 + 중앙 배치
            self.root.geometry("700x500")
            self.center_window()
    
    def save_window_geometry(self):
        """현재 창 위치/크기 저장"""
        self.config["geometry"] = self.root.geometry()
        self.save_config()
    
    def on_closing(self):
        """창 닫기 이벤트 처리"""
        self.save_window_geometry()
        self.root.destroy()
    
    def center_window(self):
        """창을 화면 중앙에 배치"""
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (width // 2)
        y = (self.root.winfo_screenheight() // 2) - (height // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
    
    def setup_ui(self):
        """UI 구성"""
        # 메인 프레임
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 입력 영역
        input_frame = ttk.LabelFrame(main_frame, text="저장소 URL 또는 owner/repo", padding="10")
        input_frame.pack(fill=tk.X, pady=(0, 10))
        
        # 입력창 + 버튼 프레임
        entry_frame = ttk.Frame(input_frame)
        entry_frame.pack(fill=tk.X)
        
        # 입력창
        self.entry_var = tk.StringVar()
        self.entry = ttk.Entry(entry_frame, textvariable=self.entry_var, font=("Consolas", 11))
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        self.entry.bind("<Return>", self.on_clone_click)
        self.entry.bind("<KP_Enter>", self.on_clone_click)
        
        # 클론 버튼
        self.clone_btn = ttk.Button(entry_frame, text="Clone", command=self.on_clone_click, width=10)
        self.clone_btn.pack(side=tk.RIGHT)
        
        # 옵션 프레임
        option_frame = ttk.Frame(input_frame)
        option_frame.pack(fill=tk.X, pady=(10, 0))
        
        # Reset 옵션
        self.reset_var = tk.BooleanVar(value=False)
        self.reset_check = ttk.Checkbutton(
            option_frame, 
            text="--reset (기존 폴더 삭제 후 재클론)", 
            variable=self.reset_var
        )
        self.reset_check.pack(side=tk.LEFT)
        
        # 출력 영역
        output_frame = ttk.LabelFrame(main_frame, text="출력", padding="10")
        output_frame.pack(fill=tk.BOTH, expand=True)
        
        # 출력창
        self.output = scrolledtext.ScrolledText(
            output_frame,
            wrap=tk.WORD,
            font=("Consolas", 10),
            state=tk.DISABLED,
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="#ffffff"
        )
        self.output.pack(fill=tk.BOTH, expand=True)
        
        # 태그 설정 (색상)
        self.output.tag_config("error", foreground="#f44747")
        self.output.tag_config("success", foreground="#6a9955")
        self.output.tag_config("info", foreground="#569cd6")
        
        # 하단 버튼 프레임
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(10, 0))
        
        # 클리어 버튼
        self.clear_btn = ttk.Button(button_frame, text="Clear", command=self.clear_output, width=10)
        self.clear_btn.pack(side=tk.LEFT)
        
        # 종료 버튼
        self.quit_btn = ttk.Button(button_frame, text="종료", command=self.root.quit, width=10)
        self.quit_btn.pack(side=tk.RIGHT)
        
        # 상태 표시
        self.status_var = tk.StringVar(value="Ready")
        self.status_label = ttk.Label(button_frame, textvariable=self.status_var)
        self.status_label.pack(side=tk.LEFT, padx=20)
    
    def append_output(self, text: str, tag: str | None = None):
        """출력창에 텍스트 추가"""
        self.output.config(state=tk.NORMAL)
        if tag:
            self.output.insert(tk.END, text, tag)
        else:
            self.output.insert(tk.END, text)
        self.output.see(tk.END)
        self.output.config(state=tk.DISABLED)
    
    def clear_output(self):
        """출력창 클리어"""
        self.output.config(state=tk.NORMAL)
        self.output.delete(1.0, tk.END)
        self.output.config(state=tk.DISABLED)
    
    def set_running(self, running: bool):
        """실행 중 상태 설정"""
        self.is_running = running
        if running:
            self.clone_btn.config(state=tk.DISABLED)
            self.entry.config(state=tk.DISABLED)
            self.status_var.set("Cloning...")
        else:
            self.clone_btn.config(state=tk.NORMAL)
            self.entry.config(state=tk.NORMAL)
            self.status_var.set("Ready")
            self.entry.focus_set()
    
    def on_clone_click(self, event=None):
        """클론 버튼 클릭 또는 Enter 키"""
        if self.is_running:
            return
        
        repo_input = self.entry_var.get().strip()
        if not repo_input:
            messagebox.showwarning("입력 필요", "저장소 URL 또는 owner/repo를 입력하세요.")
            return
        
        # 비동기로 클론 실행
        thread = threading.Thread(target=self.run_clone, args=(repo_input,), daemon=True)
        thread.start()
    
    def run_clone(self, repo_input: str):
        """클론 실행 (별도 스레드)"""
        self.root.after(0, lambda: self.set_running(True))
        self.root.after(0, self.clear_output)
        
        # 명령 구성
        cmd = [sys.executable, str(GITCLONE_SCRIPT), repo_input]
        if self.reset_var.get():
            cmd.append("--reset")
        
        self.root.after(0, lambda: self.append_output(f"$ python gitclone.py {repo_input}", "info"))
        if self.reset_var.get():
            self.root.after(0, lambda: self.append_output(" --reset", "info"))
        self.root.after(0, lambda: self.append_output("\n\n"))
        
        try:
            # UTF-8 출력을 위한 환경변수 설정
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            
            # 프로세스 실행 (실시간 출력)
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env
            )
            
            # 출력 읽기
            if process.stdout:
                for line in process.stdout:
                    self.root.after(0, lambda l=line: self.append_output(l))
            
            process.wait()
            
            # 완료 메시지
            if process.returncode == 0:
                self.root.after(0, lambda: self.append_output("\n✅ 클론 완료!\n", "success"))
                # 입력창 클리어
                self.root.after(0, lambda: self.entry_var.set(""))
            else:
                self.root.after(0, lambda: self.append_output(f"\n❌ 클론 실패 (코드: {process.returncode})\n", "error"))
        
        except Exception as e:
            self.root.after(0, lambda: self.append_output(f"\n❌ 오류: {e}\n", "error"))
        
        finally:
            self.root.after(0, lambda: self.set_running(False))


def main():
    root = tk.Tk()
    
    # 스타일 설정
    style = ttk.Style()
    style.theme_use("clam")  # 또는 "vista", "xpnative" (Windows)
    
    app = GitCloneGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
