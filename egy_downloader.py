import os
import sys
import time
import threading
import queue
import json
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# --- CONFIGURATION & COLORS ---
BG_DARK = "#0D0D0D"      # Pure rich black background
SURFACE_DARK = "#161618" # Dark surface for cards/panels
SURFACE_LIGHT = "#222226"
ACCENT_RED = "#FF1E38"   # Vibrant EGY Red
ACCENT_HOVER = "#D9152B"
TEXT_WHITE = "#FFFFFF"
TEXT_GRAY = "#A0A0A5"
SUCCESS_GREEN = "#00C853"

class DownloadTask:
    def __init__(self, url, save_path, app_ref):
        self.url = url
        self.save_path = save_path
        self.app_ref = app_ref
        self.filename = os.path.basename(urllib.parse.urlparse(url).path)
        if not self.filename or "." not in self.filename:
            self.filename = "downloaded_file.bin"
        self.full_path = os.path.join(save_path, self.filename)
        self.total_size = 0
        self.downloaded_size = 0
        self.speed = 0.0
        self.progress = 0
        self.status = "Pending"  # Pending, Downloading, Paused, Completed, Error
        self.cancelled = False
        self.paused = False
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._download_worker, daemon=True)
        self.thread.start()

    def _download_worker(self):
        try:
            self.status = "Connecting..."
            self.app_ref.update_task_ui(self)

            req = urllib.request.Request(
                self.url,
                headers={"User-Agent": "EGY-Download-Manager/1.0"}
            )

            with urllib.request.urlopen(req) as response:
                meta = response.info()
                self.total_size = int(meta.get("Content-Length", 0))

                mode = "wb"
                downloaded = 0

                # Check if partial file exists for resume
                if os.path.exists(self.full_path):
                    downloaded = os.path.getsize(self.full_path)
                    if self.total_size and downloaded >= self.total_size:
                        self.status = "Completed"
                        self.progress = 100
                        self.app_ref.update_task_ui(self)
                        return

                self.status = "Downloading"
                self.app_ref.update_task_ui(self)

                chunk_size = 8192
                start_time = time.time()
                chunk_timer = start_time
                chunk_downloaded = 0

                with open(self.full_path, mode) as f:
                    while not self.cancelled:
                        if self.paused:
                            self.status = "Paused"
                            self.app_ref.update_task_ui(self)
                            while self.paused and not self.cancelled:
                                time.sleep(0.5)
                            if self.cancelled:
                                break
                            self.status = "Downloading"

                        data = response.read(chunk_size)
                        if not data:
                            break

                        f.write(data)
                        downloaded += len(data)
                        chunk_downloaded += len(data)
                        self.downloaded_size = downloaded

                        if self.total_size > 0:
                            self.progress = int((downloaded / self.total_size) * 100)

                        now = time.time()
                        elapsed = now - chunk_timer
                        if elapsed >= 0.5:
                            self.speed = chunk_downloaded / elapsed / (1024 * 1024) # MB/s
                            chunk_downloaded = 0
                            chunk_timer = now

                        self.app_ref.update_task_ui(self)

                if self.cancelled:
                    self.status = "Cancelled"
                else:
                    self.status = "Completed"
                    self.progress = 100
                    self.speed = 0.0
                self.app_ref.update_task_ui(self)

        except Exception as e:
            self.status = f"Error: {str(e)}"
            self.app_ref.update_task_ui(self)


class ExtensionServerHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_path = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed_path.query)

        if parsed_path.path == "/add":
            urls = query.get("url", [])
            if urls:
                target_url = urls[0]
                self.server.app.queue_download(target_url)
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "url": target_url}).encode("utf-8"))
                return

        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"Not Found")

    def do_POST(self):
        parsed_path = urllib.parse.urlparse(self.path)
        if parsed_path.path == "/add":
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode('utf-8'))
                target_url = data.get("url")
                if target_url:
                    self.server.app.queue_download(target_url)
                    self.send_response(200)
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "success", "url": target_url}).encode("utf-8"))
                    return
            except Exception:
                pass

        self.send_response(400)
        self.end_headers()

    def log_message(self, format, *args):
        pass # Suppress server logs for clean terminal


class EGYDownloadManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("EGY Download Manager")
        self.root.geometry("1000pxx650px")
        self.root.minsize(800, 500)
        self.root.configure(bg=BG_DARK)

        # Default download directory
        self.default_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        if not os.path.exists(self.default_dir):
            self.default_dir = os.getcwd()

        self.tasks = []
        self.task_items = {} # Maps task object to treeview item ID

        self.setup_styles()
        self.create_widgets()
        self.start_extension_server()

    def setup_styles(self):
        self.style = ttk.Style()
        self.style.theme_use("clam")

        # Configure custom dark & red theme for ttk Treeview and widgets
        self.style.configure(
            "Treeview",
            background=SURFACE_DARK,
            foreground=TEXT_WHITE,
            fieldbackground=SURFACE_DARK,
            borderwidth=0,
            rowheight=32,
            font=("Segoe UI", 10)
        )
        self.style.configure(
            "Treeview.Heading",
            background=SURFACE_LIGHT,
            foreground=TEXT_WHITE,
            font=("Segoe UI", 10, "bold"),
            borderwidth=0
        )
        self.style.map(
            "Treeview",
            background=[("selected", ACCENT_RED)],
            foreground=[("selected", TEXT_WHITE)]
        )
        self.style.map(
            "Treeview.Heading",
            background=[("active", ACCENT_HOVER)]
        )

    def create_widgets(self):
        # --- TOP HEADER BAR ---
        header_frame = tk.Frame(self.root, bg=SURFACE_DARK, height=65)
        header_frame.pack(fill=tk.X, side=tk.TOP)
        header_frame.pack_propagate(False)

        logo_label = tk.Label(
            header_frame,
            text="EGY DOWNLOAD MANAGER",
            bg=SURFACE_DARK,
            fg=ACCENT_RED,
            font=("Segoe UI", 14, "bold")
        )
        logo_label.pack(side=tk.LEFT, padx=20)

        server_status_label = tk.Label(
            header_frame,
            text="● Browser Extension Server Active (Port 25432)",
            bg=SURFACE_DARK,
            fg=SUCCESS_GREEN,
            font=("Segoe UI", 9)
        )
        server_status_label.pack(side=tk.RIGHT, padx=20)

        # --- URL ADDITION PANEL ---
        add_frame = tk.Frame(self.root, bg=BG_DARK, pady=15, padx=20)
        add_frame.pack(fill=tk.X, side=tk.TOP)

        url_label = tk.Label(
            add_frame,
            text="Download URL:",
            bg=BG_DARK,
            fg=TEXT_WHITE,
            font=("Segoe UI", 10, "bold")
        )
        url_label.pack(anchor=tk.W, pady=(0, 5))

        input_row = tk.Frame(add_frame, bg=BG_DARK)
        input_row.pack(fill=tk.X)

        self.url_entry = tk.Entry(
            input_row,
            bg=SURFACE_DARK,
            fg=TEXT_WHITE,
            insertbackground=TEXT_WHITE,
            font=("Segoe UI", 11),
            relief=tk.FLAT,
            bd=5
        )
        self.url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6, padx=(0, 10))

        browse_btn = tk.Button(
            input_row,
            text="Save Folder",
            bg=SURFACE_LIGHT,
            fg=TEXT_WHITE,
            activebackground=ACCENT_RED,
            activeforeground=TEXT_WHITE,
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT,
            padx=15,
            pady=6,
            command=self.browse_folder
        )
        browse_btn.pack(side=tk.LEFT, padx=(0, 10))

        download_btn = tk.Button(
            input_row,
            text="Start Download",
            bg=ACCENT_RED,
            fg=TEXT_WHITE,
            activebackground=ACCENT_HOVER,
            activeforeground=TEXT_WHITE,
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT,
            padx=20,
            pady=6,
            command=self.on_start_download_click
        )
        download_btn.pack(side=tk.LEFT)

        # Folder display label
        self.folder_label = tk.Label(
            add_frame,
            text=f"Saving to: {self.default_dir}",
            bg=BG_DARK,
            fg=TEXT_GRAY,
            font=("Segoe UI", 9)
        )
        self.folder_label.pack(anchor=tk.W, pady=(8, 0))

        # --- DOWNLOADS TABLE CONTAINER ---
        table_frame = tk.Frame(self.root, bg=BG_DARK, padx=20, pady=10)
        table_frame.pack(fill=tk.BOTH, expand=True, side=tk.TOP)

        columns = ("filename", "size", "progress", "speed", "status")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")

        self.tree.heading("filename", text="File Name")
        self.tree.heading("size", text="Size")
        self.tree.heading("progress", text="Progress")
        self.tree.heading("speed", text="Speed")
        self.tree.heading("status", text="Status")

        self.tree.column("filename", width=320, anchor=tk.W)
        self.tree.column("size", width=100, anchor=tk.CENTER)
        self.tree.column("progress", width=120, anchor=tk.CENTER)
        self.tree.column("speed", width=100, anchor=tk.CENTER)
        self.tree.column("status", width=140, anchor=tk.CENTER)

        tree_scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # --- BOTTOM ACTION CONTROL BAR ---
        action_bar = tk.Frame(self.root, bg=SURFACE_DARK, height=55, padx=20)
        action_bar.pack(fill=tk.X, side=tk.BOTTOM)
        action_bar.pack_propagate(False)

        pause_btn = tk.Button(
            action_bar,
            text="Pause",
            bg=SURFACE_LIGHT,
            fg=TEXT_WHITE,
            font=("Segoe UI", 9, "bold"),
            relief=tk.FLAT,
            padx=12,
            pady=4,
            command=self.pause_selected
        )
        pause_btn.pack(side=tk.LEFT, pady=10, padx=(0, 10))

        resume_btn = tk.Button(
            action_bar,
            text="Resume",
            bg=SURFACE_LIGHT,
            fg=TEXT_WHITE,
            font=("Segoe UI", 9, "bold"),
            relief=tk.FLAT,
            padx=12,
            pady=4,
            command=self.resume_selected
        )
        resume_btn.pack(side=tk.LEFT, pady=10, padx=(0, 10))

        cancel_btn = tk.Button(
            action_bar,
            text="Cancel / Delete",
            bg=SURFACE_LIGHT,
            fg=ACCENT_RED,
            font=("Segoe UI", 9, "bold"),
            relief=tk.FLAT,
            padx=12,
            pady=4,
            command=self.cancel_selected
        )
        cancel_btn.pack(side=tk.LEFT, pady=10, padx=(0, 10))

        open_folder_btn = tk.Button(
            action_bar,
            text="Open Folder",
            bg=SURFACE_LIGHT,
            fg=TEXT_WHITE,
            font=("Segoe UI", 9, "bold"),
            relief=tk.FLAT,
            padx=12,
            pady=4,
            command=self.open_download_folder
        )
        open_folder_btn.pack(side=tk.RIGHT, pady=10)

    def browse_folder(self):
        chosen = filedialog.askdirectory(initialdir=self.default_dir)
        if chosen:
            self.default_dir = chosen
            self.folder_label.config(text=f"Saving to: {self.default_dir}")

    def on_start_download_click(self):
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showwarning("Warning", "Please enter a valid download URL.")
            return
        self.queue_download(url)
        self.url_entry.delete(0, tk.END)

    def queue_download(self, url):
        task = DownloadTask(url, self.default_dir, self)
        self.tasks.append(task)

        # Format initial size
        size_str = "Connecting..."

        # Insert into treeview
        item_id = self.tree.insert("", tk.END, values=(task.filename, size_str, "0%", "0 MB/s", "Pending"))
        self.task_items[task] = item_id

        task.start()

    def update_task_ui(self, task):
        def _update():
            item_id = self.task_items.get(task)
            if not item_id:
                return

            size_mb = f"{task.total_size / (1024*1024):.2f} MB" if task.total_size > 0 else "Unknown"
            progress_str = f"{task.progress}%"
            speed_str = f"{task.speed:.2f} MB/s" if task.status == "Downloading" else "-"

            self.tree.item(item_id, values=(task.filename, size_mb, progress_str, speed_str, task.status))

        self.root.after(0, _update)

    def get_selected_task(self):
        selected_items = self.tree.selection()
        if not selected_items:
            return None
        item_id = selected_items[0]
        for task, tid in self.task_items.items():
            if tid == item_id:
                return task
        return None

    def pause_selected(self):
        task = self.get_selected_task()
        if task and task.status == "Downloading":
            task.paused = True

    def resume_selected(self):
        task = self.get_selected_task()
        if task and task.paused:
            task.paused = False

    def cancel_selected(self):
        task = self.get_selected_task()
        if task:
            task.cancelled = True
            task.status = "Cancelled"
            self.update_task_ui(task)

    def open_download_folder(self):
        if os.path.exists(self.default_dir):
            if sys.platform == "win32":
                os.startfile(self.default_dir)
            elif sys.platform == "darwin":
                os.system(f"open '{self.default_dir}'")
            else:
                os.system(f"xdg-open '{self.default_dir}'")

    def start_extension_server(self):
        try:
            server = HTTPServer(("127.0.0.1", 25432), ExtensionServerHandler)
            server.app = self
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
        except Exception as e:
            print(f"Could not start extension server: {e}")


if __name__ == "__main__":
    root = tk.Tk()
    app = EGYDownloadManagerApp(root)
    root.mainloop()
