from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
import tkinter as tk
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from core import CancelledError, MediaFile, iter_media, process_file


APP_NAME = "HashVeil"
APP_VERSION = "1.2.0"
PAGE_SIZE = 500
BG, PANEL, PANEL_2 = "#111318", "#191c23", "#222631"
TEXT, MUTED, ACCENT, GOOD, BAD = "#f4f5f7", "#979eaa", "#6ee7b7", "#52d39a", "#ff7185"


def human_bytes(value: float) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    return "0 B"


class HashVeilApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} — 媒体哈希扰动工具")
        self.geometry("940x680")
        self.minsize(820, 610)
        self.configure(bg=BG)
        self._events: queue.Queue = queue.Queue()
        self._cancel = threading.Event()
        self._running = False
        self._scan_finished = False
        self._files: list[MediaFile] = []
        self._file_states: list[str] = []
        self._page = 0
        self._build_style()
        self._build_ui()
        self.after(80, self._poll_events)

    def _build_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Microsoft YaHei UI", 10))
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Title.TLabel", foreground=TEXT, font=("Microsoft YaHei UI Semibold", 24))
        style.configure("Metric.TLabel", background=PANEL, foreground=TEXT, font=("Microsoft YaHei UI Semibold", 17))
        style.configure("MetricName.TLabel", background=PANEL, foreground=MUTED, font=("Microsoft YaHei UI", 9))
        style.configure("TButton", background=PANEL_2, foreground=TEXT, borderwidth=0, padding=(16, 10), font=("Microsoft YaHei UI", 10))
        style.map("TButton", background=[("active", "#303644"), ("disabled", "#1d2027")], foreground=[("disabled", "#686e78")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#0d2a21", font=("Microsoft YaHei UI Semibold", 10))
        style.map("Accent.TButton", background=[("active", "#8df0ca")])
        style.configure("TEntry", fieldbackground=PANEL_2, foreground=TEXT, insertcolor=TEXT, borderwidth=0, padding=10)
        style.configure("TCombobox", fieldbackground=PANEL_2, background=PANEL_2, foreground=TEXT, arrowcolor=TEXT, padding=8)
        style.configure("Horizontal.TProgressbar", troughcolor=PANEL_2, background=ACCENT, borderwidth=0, thickness=10)
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT, rowheight=30, borderwidth=0, font=("Microsoft YaHei UI", 9))
        style.configure("Treeview.Heading", background=PANEL_2, foreground=MUTED, borderwidth=0, font=("Microsoft YaHei UI Semibold", 9))
        style.map("Treeview", background=[("selected", "#2b4a41")])

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=(28, 24))
        outer.pack(fill="both", expand=True)
        head = ttk.Frame(outer)
        head.pack(fill="x")
        ttk.Label(head, text="HASH / VEIL", style="Title.TLabel").pack(side="left")
        ttk.Label(head, text="不转码 · 无画质损失 · 极速批处理", style="Muted.TLabel").pack(side="left", padx=18, pady=(10, 0))

        pick = ttk.Frame(outer, style="Panel.TFrame", padding=18)
        pick.pack(fill="x", pady=(22, 12))
        self.source_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.mode_var = tk.StringVar(value="输出到新文件夹（推荐）")
        self.workers_var = tk.StringVar(value=str(min(16, max(4, (os.cpu_count() or 4) * 2))))
        self._row(pick, 0, "源文件夹", self.source_var, self._choose_source)
        self._row(pick, 1, "输出位置", self.output_var, self._choose_output)
        ttk.Label(pick, text="处理方式", background=PANEL, foreground=MUTED).grid(row=2, column=0, sticky="w", pady=(10, 0))
        mode = ttk.Combobox(pick, textvariable=self.mode_var, state="readonly", values=("输出到新文件夹（推荐）", "直接修改原文件"), width=28)
        mode.grid(row=2, column=1, sticky="w", padx=(12, 18), pady=(10, 0))
        mode.bind("<<ComboboxSelected>>", lambda _e: self._sync_mode())
        ttk.Label(pick, text="并发任务", background=PANEL, foreground=MUTED).grid(row=2, column=2, sticky="e", pady=(10, 0))
        ttk.Combobox(pick, textvariable=self.workers_var, state="normal", values=("1", "2", "4", "8", "16", "24", "32", "48", "64"), width=5).grid(row=2, column=3, sticky="e", pady=(10, 0))
        pick.columnconfigure(1, weight=1)

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(0, 12))
        self.start_btn = ttk.Button(controls, text="边扫描边处理", style="Accent.TButton", command=self._start)
        self.start_btn.pack(side="left")
        self.cancel_btn = ttk.Button(controls, text="取消", command=self._request_cancel, state="disabled")
        self.cancel_btn.pack(side="left")
        self.open_btn = ttk.Button(controls, text="打开输出目录", command=self._open_output, state="disabled")
        self.open_btn.pack(side="right")

        metrics = ttk.Frame(outer, style="Panel.TFrame", padding=16)
        metrics.pack(fill="x")
        self.count_value = self._metric(metrics, "0", "媒体文件", 0)
        self.size_value = self._metric(metrics, "0 B", "总大小", 1)
        self.done_value = self._metric(metrics, "0", "已完成", 2)
        self.speed_value = self._metric(metrics, "0 MB/s", "实时速度", 3)
        for i in range(4): metrics.columnconfigure(i, weight=1)

        progress_frame = ttk.Frame(outer)
        progress_frame.pack(fill="x", pady=(16, 10))
        self.status_var = tk.StringVar(value="选择源文件夹后即可边扫描边处理")
        ttk.Label(progress_frame, textvariable=self.status_var).pack(side="left")
        self.percent_var = tk.StringVar(value="0%")
        ttk.Label(progress_frame, textvariable=self.percent_var, style="Muted.TLabel").pack(side="right")
        self.progress = ttk.Progressbar(outer, mode="determinate", maximum=100)
        self.progress.pack(fill="x")

        self.tree = ttk.Treeview(outer, columns=("file", "size", "state"), show="headings")
        self.tree.heading("file", text="文件")
        self.tree.heading("size", text="大小")
        self.tree.heading("state", text="状态")
        self.tree.column("file", width=570)
        self.tree.column("size", width=110, anchor="e")
        self.tree.column("state", width=110, anchor="center")
        self.tree.pack(fill="both", expand=True, pady=(14, 0))
        pager = ttk.Frame(outer)
        pager.pack(fill="x", pady=(8, 0))
        self.prev_btn = ttk.Button(pager, text="上一页", command=lambda: self._change_page(-1), state="disabled")
        self.prev_btn.pack(side="left")
        self.page_var = tk.StringVar(value="第 0 / 0 页")
        ttk.Label(pager, textvariable=self.page_var, style="Muted.TLabel").pack(side="left", padx=14)
        self.next_btn = ttk.Button(pager, text="下一页", command=lambda: self._change_page(1), state="disabled")
        self.next_btn.pack(side="left")
        ttk.Label(pager, text=f"每页 {PAGE_SIZE} 条", style="Muted.TLabel").pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _row(self, parent, row, label, variable, command) -> None:
        ttk.Label(parent, text=label, background=PANEL, foreground=MUTED).grid(row=row, column=0, sticky="w", pady=5)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, columnspan=2, sticky="ew", padx=12, pady=5)
        ttk.Button(parent, text="浏览…", command=command).grid(row=row, column=3, sticky="e", pady=5)

    def _metric(self, parent, value, name, column):
        box = ttk.Frame(parent, style="Panel.TFrame")
        box.grid(row=0, column=column, sticky="ew", padx=12)
        val = ttk.Label(box, text=value, style="Metric.TLabel")
        val.pack(anchor="w")
        ttk.Label(box, text=name, style="MetricName.TLabel").pack(anchor="w")
        return val

    def _choose_source(self):
        value = filedialog.askdirectory(title="选择包含视频和图片的文件夹")
        if value:
            self.source_var.set(value)
            if not self.output_var.get(): self.output_var.set(str(Path(value).parent / f"{Path(value).name}_HashVeil"))

    def _choose_output(self):
        value = filedialog.askdirectory(title="选择输出文件夹")
        if value: self.output_var.set(value)

    def _sync_mode(self):
        if self.mode_var.get().startswith("直接"):
            self.output_var.set("（原地处理，不生成副本）")
        elif not self.output_var.get() or self.output_var.get().startswith("（"):
            source = self.source_var.get()
            if source: self.output_var.set(str(Path(source).parent / f"{Path(source).name}_HashVeil"))

    def _start(self):
        source = Path(self.source_var.get())
        if not source.is_dir():
            messagebox.showwarning(APP_NAME, "请先选择有效的源文件夹。")
            return
        try:
            workers = int(self.workers_var.get())
            if not 1 <= workers <= 64: raise ValueError
        except ValueError:
            messagebox.showwarning(APP_NAME, "并发任务数必须是 1 到 64 之间的整数。")
            return
        if self.mode_var.get().startswith("直接") and not messagebox.askyesno(APP_NAME, "此模式会直接修改原文件。建议提前备份。\n\n确定继续吗？"):
            return
        self._running, self._scan_finished, self._cancel = True, False, threading.Event()
        self._files, self._file_states, self._page = [], [], 0
        self._show_page()
        self.start_btn.config(state="disabled"); self.cancel_btn.config(state="normal")
        self.open_btn.config(state="disabled")
        self.count_value.config(text="0"); self.size_value.config(text="0 B"); self.done_value.config(text="0")
        self.speed_value.config(text="0 MB/s"); self.percent_var.set("扫描中")
        self.progress.config(mode="indeterminate"); self.progress.start(12)
        self.status_var.set("正在扫描 · 发现文件后立即处理…")
        self._show_page()
        threading.Thread(target=self._run_batch, daemon=True).start()

    def _run_batch(self):
        source_root = Path(self.source_var.get())
        in_place = self.mode_var.get().startswith("直接")
        output_root = source_root if in_place else Path(self.output_var.get())
        state = {"bytes": 0, "lock": threading.Lock(), "start": time.monotonic()}
        def add_bytes(amount):
            with state["lock"]:
                state["bytes"] += amount
                self._events.put(("progress", state["bytes"], time.monotonic() - state["start"]))
        def one(index, media):
            dest = media.source if in_place else output_root / media.relative
            self._events.put(("file", index, "处理中"))
            process_file(media.source, dest, in_place=in_place, cancel=self._cancel, on_bytes=add_bytes)
            return index
        ok, failed, discovered, total_bytes = 0, [], 0, 0
        workers = min(64, max(1, int(self.workers_var.get())))
        def collect(done_futures, futures):
            nonlocal ok
            for future in done_futures:
                i, media = futures.pop(future)
                try:
                    future.result(); ok += 1; self._events.put(("file", i, "完成"))
                except CancelledError: self._events.put(("file", i, "已取消"))
                except Exception as exc:
                    failed.append(f"{media.relative}: {exc}"); self._events.put(("file", i, "失败"))
                self._events.put(("done_count", ok, len(failed), discovered, False))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            for media in iter_media(source_root, None if in_place else output_root):
                if self._cancel.is_set(): break
                index = discovered; discovered += 1; total_bytes += media.size
                self._events.put(("discovered", index, media, discovered, total_bytes))
                future = pool.submit(one, index, media)
                futures[future] = (index, media)
                if len(futures) >= workers * 4:
                    done, _ = wait(futures, return_when=FIRST_COMPLETED)
                    collect(done, futures)
            self._events.put(("scan_done", discovered, total_bytes, ok, len(failed)))
            while futures:
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                collect(done, futures)
        self._events.put(("finished", ok, failed, self._cancel.is_set(), str(output_root)))

    def _request_cancel(self):
        self._cancel.set(); self.status_var.set("正在安全取消…")

    def _poll_events(self):
        try:
            while True:
                event = self._events.get_nowait(); kind = event[0]
                if kind == "progress":
                    _, done, elapsed = event
                    self.speed_value.config(text=f"{human_bytes(done / max(elapsed, .01))}/s")
                elif kind == "discovered":
                    _, index, media, count, total_bytes = event
                    self._files.append(media); self._file_states.append("等待")
                    self.count_value.config(text=str(count)); self.size_value.config(text=human_bytes(total_bytes))
                    if index // PAGE_SIZE == self._page:
                        self.tree.insert("", "end", values=(str(media.relative), human_bytes(media.size), "等待"))
                        self._update_pager()
                    self.status_var.set(f"正在扫描 · 已发现 {count} · 处理同步进行中")
                elif kind == "file":
                    i, state = event[1:]
                    if i < len(self._file_states):
                        self._file_states[i] = state
                        start = self._page * PAGE_SIZE
                        if start <= i < start + PAGE_SIZE:
                            items = self.tree.get_children()
                            row = i - start
                            if row < len(items): self.tree.set(items[row], "state", state)
                elif kind == "done_count":
                    done, failed, found = event[1:4]
                    self.done_value.config(text=str(done + failed))
                    if self._scan_finished and found:
                        pct = min(100, (done + failed) / found * 100); self.progress["value"] = pct; self.percent_var.set(f"{pct:.0f}%")
                elif kind == "scan_done":
                    self._scan_finished = True; self.progress.stop(); self.progress.config(mode="determinate", maximum=100)
                    found, _, ok, failed = event[1:]
                    pct = (ok + failed) / found * 100 if found else 100
                    self.progress["value"] = pct; self.percent_var.set(f"{pct:.0f}%")
                    self.status_var.set(f"扫描完成 · 共 {found} 个 · 正在完成剩余任务")
                elif kind == "finished": self._finished(*event[1:])
        except queue.Empty: pass
        self.after(80, self._poll_events)

    def _show_page(self):
        self.tree.delete(*self.tree.get_children())
        pages = (len(self._files) + PAGE_SIZE - 1) // PAGE_SIZE
        if pages == 0:
            self._page = 0
        else:
            self._page = min(max(0, self._page), pages - 1)
        start = self._page * PAGE_SIZE
        end = min(start + PAGE_SIZE, len(self._files))
        for index in range(start, end):
            media = self._files[index]
            state = self._file_states[index] if index < len(self._file_states) else "等待"
            self.tree.insert("", "end", values=(str(media.relative), human_bytes(media.size), state))
        self._update_pager()

    def _update_pager(self):
        pages = (len(self._files) + PAGE_SIZE - 1) // PAGE_SIZE
        start = self._page * PAGE_SIZE
        end = min(start + PAGE_SIZE, len(self._files))
        self.page_var.set(f"第 {self._page + 1 if pages else 0} / {pages} 页 · {start + 1 if self._files else 0}–{end} / {len(self._files)}")
        self.prev_btn.config(state="normal" if self._page > 0 else "disabled")
        self.next_btn.config(state="normal" if self._page + 1 < pages else "disabled")

    def _change_page(self, delta):
        self._page += delta
        self._show_page()

    def _finished(self, ok, failed, cancelled, output):
        self._running = False
        self.progress.stop(); self.progress.config(mode="determinate")
        self.start_btn.config(state="normal"); self.cancel_btn.config(state="disabled")
        self.open_btn.config(state="normal" if not self.mode_var.get().startswith("直接") else "disabled")
        if not cancelled and not failed: self.progress["value"] = 100; self.percent_var.set("100%")
        self.status_var.set(("已取消" if cancelled else "处理完成") + f" · 成功 {ok} · 失败 {len(failed)}")
        if failed: messagebox.showerror(APP_NAME, "部分文件处理失败：\n\n" + "\n".join(failed[:10]))
        elif not cancelled: messagebox.showinfo(APP_NAME, f"全部处理完成，共 {ok} 个文件。")

    def _open_output(self):
        path = Path(self.output_var.get())
        if path.exists(): subprocess.Popen(["explorer", str(path)])

    def _close(self):
        if self._running and not messagebox.askyesno(APP_NAME, "任务仍在运行，确定取消并退出吗？"): return
        self._cancel.set(); self.destroy()


if __name__ == "__main__":
    HashVeilApp().mainloop()
