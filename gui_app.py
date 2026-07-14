# -*- coding: utf-8 -*-
"""
图形界面主体-tkinter
"""

import os
import sys
import json
import threading
import traceback
import queue

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import cb_extract
import cb_repack

APP_TITLE = "基岩版命令方块提取/回写工具"


class LogQueueWriter:
    def __init__(self, q):
        self.q = q

    def write(self, text):
        if text:
            self.q.put(text)

    def flush(self):
        pass


class App:
    def __init__(self, root):
        self.root = root
        self.current_json = None
        self.current_json_path = None

        root.title(APP_TITLE)
        root.geometry("1100x800")
        root.minsize(900, 700)

        self.log_queue = queue.Queue()
        self._poll_log()

        self.nb = ttk.Notebook(root)
        self.nb.pack(fill="both", expand=True, padx=8, pady=8)

        self._build_extract_tab()
        self._build_repack_tab()

        # 日志面板
        log_frame = ttk.LabelFrame(root, text="日志")
        log_frame.pack(fill="x", padx=8, pady=(0, 8))
        self.log_text = scrolledtext.ScrolledText(log_frame, height=6, wrap="word")
        self.log_text.pack(fill="x", padx=4, pady=4)

    # ==================== 提取 Tab ====================
    def _build_extract_tab(self):
        ext_frame = ttk.Frame(self.nb)
        self.nb.add(ext_frame, text="提取命令方块")

        row = 0
        ttk.Label(ext_frame, text="存档文件:").grid(row=row, column=0, sticky="w", padx=8, pady=4)
        self.ext_path_var = tk.StringVar()
        ttk.Entry(ext_frame, textvariable=self.ext_path_var).grid(
            row=row, column=1, sticky="we", padx=4, pady=4)
        ttk.Button(ext_frame, text="浏览…",
                   command=self._browse_mcworld).grid(row=row, column=2, padx=4, pady=4)
        ext_frame.columnconfigure(1, weight=1)

        row += 1
        ttk.Label(ext_frame, text="输出 JSON:").grid(row=row, column=0, sticky="w", padx=8, pady=4)
        self.ext_output_var = tk.StringVar()
        ttk.Entry(ext_frame, textvariable=self.ext_output_var).grid(
            row=row, column=1, sticky="we", padx=4, pady=4)
        ttk.Button(ext_frame, text="浏览…",
                   command=self._browse_output_json).grid(row=row, column=2, padx=4, pady=4)

        row += 1
        self.ext_run_btn = ttk.Button(ext_frame, text="开始提取",
                                     command=self._do_extract)
        self.ext_run_btn.grid(row=row, column=0, columnspan=3, pady=8)

        self.ext_status_var = tk.StringVar(value="就绪")
        ttk.Label(ext_frame, textvariable=self.ext_status_var).grid(
            row=row, column=0, columnspan=3, sticky="w", padx=8)

        # 命令方块列表
        row += 1
        ttk.Label(ext_frame, text="命令方块列表:").grid(
            row=row, column=0, columnspan=3, sticky="w", padx=8, pady=(8, 0))

        row += 1
        cols = ("cb_key", "pos", "command", "name")
        tree_frame = ttk.Frame(ext_frame)
        tree_frame.grid(row=row, column=0, columnspan=3, sticky="nsew", padx=8, pady=4)
        ext_frame.rowconfigure(row, weight=1)

        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=18)
        self.tree.heading("cb_key", text="编号")
        self.tree.heading("pos", text="坐标")
        self.tree.heading("command", text="命令文本")
        self.tree.heading("name", text="悬浮文字")
        self.tree.column("cb_key", width=60)
        self.tree.column("pos", width=100)
        self.tree.column("command", width=450)
        self.tree.column("name", width=150)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        # 编辑区
        row += 1
        edit_frame = ttk.LabelFrame(ext_frame, text="编辑选中行")
        edit_frame.grid(row=row, column=0, columnspan=3, sticky="we", padx=8, pady=4)

        ttk.Label(edit_frame, text="命令:").grid(row=0, column=0, sticky="w", padx=4, pady=2)
        self.edit_cmd_var = tk.StringVar()
        ttk.Entry(edit_frame, textvariable=self.edit_cmd_var).grid(
            row=0, column=1, sticky="we", padx=4, pady=2)
        ttk.Label(edit_frame, text="悬浮文字:").grid(row=1, column=0, sticky="w", padx=4, pady=2)
        self.edit_name_var = tk.StringVar()
        ttk.Entry(edit_frame, textvariable=self.edit_name_var).grid(
            row=1, column=1, sticky="we", padx=4, pady=2)
        edit_frame.columnconfigure(1, weight=1)

        btn_row = ttk.Frame(edit_frame)
        btn_row.grid(row=2, column=0, columnspan=2, pady=4)
        ttk.Button(btn_row, text="应用修改", command=self._apply_edit).pack(side="left", padx=4)
        ttk.Button(btn_row, text="保存JSON", command=self._save_json).pack(side="left", padx=4)

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

    # ==================== 回写 Tab ====================
    def _build_repack_tab(self):
        rp_frame = ttk.Frame(self.nb)
        self.nb.add(rp_frame, text="回写存档")

        row = 0
        ttk.Label(rp_frame, text="JSON 文件:").grid(row=row, column=0, sticky="w", padx=8, pady=4)
        self.rp_json_var = tk.StringVar()
        ttk.Entry(rp_frame, textvariable=self.rp_json_var).grid(
            row=row, column=1, sticky="we", padx=4, pady=4)
        ttk.Button(rp_frame, text="浏览…",
                   command=self._browse_json).grid(row=row, column=2, padx=4, pady=4)
        rp_frame.columnconfigure(1, weight=1)

        row += 1
        ttk.Label(rp_frame, text="输出存档:").grid(row=row, column=0, sticky="w", padx=8, pady=4)
        self.rp_output_var = tk.StringVar()
        ttk.Entry(rp_frame, textvariable=self.rp_output_var).grid(
            row=row, column=1, sticky="we", padx=4, pady=4)
        ttk.Button(rp_frame, text="浏览…",
                   command=self._browse_output).grid(row=row, column=2, padx=4, pady=4)

        row += 1
        self.rp_run_btn = ttk.Button(rp_frame, text="开始回写",
                                     command=self._do_repack)
        self.rp_run_btn.grid(row=row, column=0, columnspan=3, pady=8)

        self.rp_status_var = tk.StringVar(value="就绪")
        ttk.Label(rp_frame, textvariable=self.rp_status_var).grid(
            row=row, column=0, columnspan=3, sticky="w", padx=8)

        row += 1
        ttk.Label(rp_frame, text="说明：JSON 文件中包含完整的世界数据（_world_data 字段），\n"
                                   "回写时不需要原始 .mcworld 文件。").grid(
            row=row, column=0, columnspan=3, sticky="w", padx=8, pady=8)

    # ==================== 日志 ====================
    def _poll_log(self):
        try:
            while True:
                text = self.log_queue.get_nowait()
                self.log_text.insert("end", text)
                self.log_text.see("end")
        except queue.Empty:
            pass
        self.root.after(200, self._poll_log)

    def log(self, msg):
        self.log_queue.put(str(msg) + "\n")

    def run_in_thread(self, task, done_callback):
        result = [None, None]
        def wrapper():
            try:
                result[0] = task()
                result[1] = True
            except Exception as e:
                result[0] = e
                result[1] = False
            self.root.after(0, lambda: done_callback(result[1], result[0]))
        t = threading.Thread(target=wrapper, daemon=True)
        t.start()

    # ==================== 提取逻辑 ====================
    def _browse_mcworld(self):
        path = filedialog.askopenfilename(
            title="选择 .mcworld 存档",
            filetypes=[("Minecraft 存档", "*.mcworld"), ("所有文件", "*.*")])
        if path:
            self.ext_path_var.set(path)
            # 自动填充默认输出 JSON 路径（用户可手动修改）
            self.ext_output_var.set(os.path.splitext(path)[0] + "_commands.json")

    def _browse_output_json(self):
        path = filedialog.asksaveasfilename(
            title="选择输出 JSON 路径",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")])
        if path:
            self.ext_output_var.set(path)

    def _do_extract(self):
        mcworld_path = self.ext_path_var.get().strip()
        if not mcworld_path or not os.path.isfile(mcworld_path):
            messagebox.showerror(APP_TITLE, "请选择有效的 .mcworld 文件")
            return

        custom_output = self.ext_output_var.get().strip()
        if custom_output:
            json_path = custom_output
        else:
            json_path = os.path.splitext(mcworld_path)[0] + "_commands.json"

        self.ext_run_btn.configure(state="disabled")
        self.ext_status_var.set("提取中…")
        self.log(f"开始提取: {mcworld_path}")
        self.log(f"输出 JSON: {json_path}")

        def task():
            output = cb_extract.build_output_json(mcworld_path)
            return output

        def done(ok, result):
            self.ext_run_btn.configure(state="normal")
            if ok:
                self.current_json = result
                self.current_json_path = json_path
                count = result["world_data"]["command_block_count"]
                self.log(f"提取完成: {count} 个命令方块")

                try:
                    with open(json_path, "w", encoding="utf-8") as f:
                        json.dump(result, f, ensure_ascii=False, indent=2)
                    self.log(f"JSON 已保存: {json_path}")
                except Exception as e:
                    self.log(f"JSON 保存失败: {e}")

                self._populate_tree(result)
                self.ext_status_var.set(f"完成: {count} 个命令方块, JSON 已保存")
                messagebox.showinfo(APP_TITLE,
                    f"提取完成！\n{count} 个命令方块\nJSON 已保存:\n{json_path}")
            else:
                self.ext_status_var.set("提取失败")
                self.log(f"提取失败: {result}")
                messagebox.showerror(APP_TITLE, f"提取失败:\n{result}")

        self.run_in_thread(task, done)

    def _populate_tree(self, json_data):
        self.tree.delete(*self.tree.get_children())
        commands = json_data.get("commands", {})
        names = json_data.get("names", {})
        meta = json_data.get("_meta", [])
        for m in meta:
            cb_key = m["cb_key"]
            self.tree.insert("", "end", iid=cb_key, values=(
                cb_key,
                m.get("pos", ""),
                commands.get(cb_key, ""),
                names.get(cb_key, ""),
            ))

    def _on_tree_select(self, _event):
        sel = self.tree.selection()
        if sel:
            values = self.tree.item(sel[0], "values")
            self.edit_cmd_var.set(values[2] if len(values) > 2 else "")
            self.edit_name_var.set(values[3] if len(values) > 3 else "")

    def _apply_edit(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning(APP_TITLE, "请先选择一行")
            return
        cb_key = sel[0]
        new_cmd = self.edit_cmd_var.get()
        new_name = self.edit_name_var.get()
        self.tree.item(cb_key, values=(cb_key,
            self.tree.item(cb_key, "values")[1], new_cmd, new_name))
        self.current_json["commands"][cb_key] = new_cmd
        self.current_json["names"][cb_key] = new_name
        self.log(f"已修改 {cb_key}: 命令='{new_cmd[:50]}' 悬浮文字='{new_name}'")

    def _save_json(self):
        if not self.current_json:
            messagebox.showwarning(APP_TITLE, "请先提取")
            return
        path = self.current_json_path or "output.json"
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            initialfile=os.path.basename(path),
            filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.current_json, f, ensure_ascii=False, indent=2)
            self.current_json_path = path
            self.log(f"JSON 已保存: {path}")
            messagebox.showinfo(APP_TITLE, f"已保存:\n{path}")
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"保存失败:\n{e}")

    # ==================== 回写逻辑 ====================
    def _browse_json(self):
        path = filedialog.askopenfilename(
            title="选择 JSON 文件",
            filetypes=[("JSON", "*.json"), ("所有文件", "*.*")])
        if path:
            self.rp_json_var.set(path)

    def _browse_output(self):
        path = filedialog.asksaveasfilename(
            title="选择输出存档路径",
            defaultextension=".mcworld",
            filetypes=[("Minecraft 存档", "*.mcworld")])
        if path:
            self.rp_output_var.set(path)

    def _do_repack(self):
        json_path = self.rp_json_var.get().strip()
        output_path = self.rp_output_var.get().strip()
        if not json_path or not os.path.isfile(json_path):
            messagebox.showerror(APP_TITLE, "请选择有效的 JSON 文件")
            return
        if not output_path:
            messagebox.showerror(APP_TITLE, "请指定输出存档路径")
            return

        self.rp_run_btn.configure(state="disabled")
        self.rp_status_var.set("回写中…")
        self.log(f"开始回写: {json_path} → {output_path}")

        def task():
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cmd_c, name_c, chunk_c = cb_repack.repack_mcworld_from_json(data, output_path)
            self.log(f"修改了 {cmd_c} 条命令, {name_c} 条悬浮文字（涉及 {chunk_c} 个区块）")
            self.log(f"新存档: {output_path}")
            return (cmd_c, name_c, chunk_c)

        def done(ok, result):
            self.rp_run_btn.configure(state="normal")
            if ok:
                cmd_c, name_c, chunk_c = result
                self.rp_status_var.set(
                    f"完成: 修改 {cmd_c} 条命令, {name_c} 条悬浮文字, {chunk_c} 个区块")
                messagebox.showinfo(APP_TITLE,
                    f"回写完成！\n修改 {cmd_c} 条命令, {name_c} 条悬浮文字\n"
                    f"涉及 {chunk_c} 个区块\n新存档:\n{output_path}")
            else:
                self.rp_status_var.set("回写失败")
                self.log(f"回写失败: {result}")
                messagebox.showerror(APP_TITLE, f"回写失败:\n{result}")

        self.run_in_thread(task, done)


def run():
    root = tk.Tk()
    app = App(root)
    root.mainloop()


if __name__ == "__main__":
    run()
