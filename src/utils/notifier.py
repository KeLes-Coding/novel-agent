# src/utils/notifier.py
import sys
import logging
import threading
from typing import Dict, Any, List, Union


class Notifier:
    def __init__(self, cfg: Dict[str, Any], run_id: str = "unknown"):
        self.cfg_notify = cfg.get("notification", {})
        self.enabled = self.cfg_notify.get("enabled", True)

        # 支持配置为字符串或列表
        method_cfg = self.cfg_notify.get("method", "console")
        if isinstance(method_cfg, str):
            self.methods = [method_cfg]
        else:
            self.methods = list(method_cfg)

        self.webhook_url = self.cfg_notify.get("webhook_url", "")
        self.logger = logging.getLogger("novel_agent.notifier")
        self.run_id = run_id

    def notify(self, title: str, message: str, payload: Dict[str, Any] = None):
        if not self.enabled:
            return

        full_msg = f"🔔 [{title}] {message}"

        # 传递 extra 信息以满足 LogAdapter/Formatter 的要求
        self.logger.info(full_msg, extra={"run_id": self.run_id, "step": "notifier"})

        if "console" in self.methods:
            self._notify_console(full_msg)

        if "popup" in self.methods:
            self._notify_popup(title, message)

    def _notify_console(self, msg: str):
        print(f"\n{'-'*40}")
        print(msg)
        print(f"{'-'*40}\n")
        try:
            # 尝试触发系统提示音
            sys.stdout.write("\a")
            sys.stdout.flush()
        except Exception:
            pass

    def _notify_popup(self, title: str, msg: str):
        def _run():
            try:
                import tkinter as tk
                from tkinter import messagebox

                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                # 汉化标题，如果没传特定title
                display_title = title if title else "Novel Agent 通知"
                messagebox.showinfo(display_title, msg)
                root.destroy()
            except Exception as e:
                print(f"[Notifier] 弹窗失败: {e}")

        t = threading.Thread(target=_run)
        t.start()
