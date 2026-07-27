from __future__ import annotations

import json
import os
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

import app

CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "DOI文献批量下载器"
CONFIG_PATH = CONFIG_DIR / "settings.json"


def load_api_settings() -> dict[str, str]:
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return {
                "core_api_key": str(payload.get("core_api_key") or "").strip(),
                "elsevier_api_key": str(payload.get("elsevier_api_key") or "").strip(),
            }
    except (OSError, ValueError, TypeError):
        pass
    return {"core_api_key": "", "elsevier_api_key": ""}


def apply_api_settings(settings: dict[str, str]) -> None:
    for env_name, key_name in (
        ("CORE_API_KEY", "core_api_key"),
        ("ELSEVIER_API_KEY", "elsevier_api_key"),
    ):
        value = settings.get(key_name, "").strip()
        if value:
            os.environ[env_name] = value
        else:
            os.environ.pop(env_name, None)


class EnhancedPaperDownloaderApp(app.PaperDownloaderApp):
    def __init__(self) -> None:
        self.api_settings = load_api_settings()
        apply_api_settings(self.api_settings)
        super().__init__()
        self._build_menu()

    def _build_menu(self) -> None:
        menu = tk.Menu(self)
        settings_menu = tk.Menu(menu, tearoff=False)
        settings_menu.add_command(label="开放获取接口设置", command=self._show_api_settings)
        menu.add_cascade(label="设置", menu=settings_menu)
        self.configure(menu=menu)

    def _show_api_settings(self) -> None:
        window = tk.Toplevel(self)
        window.title("开放获取接口设置")
        window.resizable(False, False)
        window.transient(self)
        window.grab_set()

        frame = ttk.Frame(window, padding=16)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        core_value = tk.StringVar(value=self.api_settings.get("core_api_key", ""))
        elsevier_value = tk.StringVar(value=self.api_settings.get("elsevier_api_key", ""))

        ttk.Label(frame, text="CORE API Key：").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Entry(frame, textvariable=core_value, width=52, show="*").grid(
            row=0, column=1, sticky="ew", padx=(8, 0)
        )
        ttk.Label(
            frame,
            text="可不填；填写后可提高开放仓储全文检索额度。",
            foreground="#666666",
        ).grid(row=1, column=1, sticky="w", padx=(8, 0))

        ttk.Label(frame, text="Elsevier API Key：").grid(row=2, column=0, sticky="w", pady=(14, 6))
        ttk.Entry(frame, textvariable=elsevier_value, width=52, show="*").grid(
            row=2, column=1, sticky="ew", padx=(8, 0), pady=(14, 6)
        )
        ttk.Label(
            frame,
            text="仅用于已确认开放获取的 Elsevier 文献；不绕过订阅权限。",
            foreground="#666666",
        ).grid(row=3, column=1, sticky="w", padx=(8, 0))

        ttk.Label(
            frame,
            text="密钥只保存在本机用户配置目录，不会上传到 GitHub 或写入下载日志。",
            foreground="#666666",
            wraplength=520,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(16, 8))

        button_row = ttk.Frame(frame)
        button_row.grid(row=5, column=0, columnspan=2, sticky="e", pady=(8, 0))

        def save() -> None:
            settings = {
                "core_api_key": core_value.get().strip(),
                "elsevier_api_key": elsevier_value.get().strip(),
            }
            try:
                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                CONFIG_PATH.write_text(
                    json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except OSError as exc:
                messagebox.showerror(app.APP_NAME, f"保存设置失败：{exc}", parent=window)
                return
            self.api_settings = settings
            apply_api_settings(settings)
            messagebox.showinfo(app.APP_NAME, "接口设置已保存，下一批下载立即生效。", parent=window)
            window.destroy()

        ttk.Button(button_row, text="取消", command=window.destroy).pack(side="right")
        ttk.Button(button_row, text="保存", command=save).pack(side="right", padx=(0, 8))

        window.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - window.winfo_width()) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - window.winfo_height()) // 3)
        window.geometry(f"+{x}+{y}")
