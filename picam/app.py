"""Pi Cam desktop window — Mesh Chat–style UI for the Pi camera."""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

from PIL import Image, ImageTk

from picam.camera import Camera, stitch_panorama
from picam.gameboy import gameboy_frame
from picam.paths import data_dir, panoramas_dir, photos_dir, videos_dir
from picam.settings import load_settings, save_settings
from picam.update import check_update, install_root, install_update, local_revision, relaunch

BG = "#e8e8ed"
PANEL = "#f7f7f8"
INK = "#111111"
MUTED = "#8e8e93"
MINE = "#0b84ff"
MINE_INK = "#ffffff"
FIELD = "#ffffff"
ACCENT = "#248a3d"
SELECT = "#d6e8ff"
LINE = "#d1d1d6"
DANGER = "#c0392b"


def ui_font(size: int, bold: bool = False) -> tuple:
    family = "Segoe UI" if os.name == "nt" else "DejaVu Sans"
    return (family, size, "bold" if bold else "normal")


def stamp(prefix: str, ext: str) -> str:
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"


def running_revision() -> str:
    sha = local_revision(install_root())
    if not sha:
        return "local"
    return sha[:7]


class App:
    def __init__(self) -> None:
        self.camera = Camera()
        prefs = load_settings()
        self.camera.rotation = int(prefs.get("rotation") or 0)
        self.root = tk.Tk()
        self.root.title(f"Pi Cam {running_revision()}")
        self.root.geometry("1024x780")
        self.root.minsize(800, 640)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self._jobs: queue.Queue = queue.Queue()
        self._photo_ref: ImageTk.PhotoImage | None = None
        self._last_preview: Image.Image | None = None
        self._preview_running = False
        self._gb_live = False
        self._gb_lcd = bool(prefs.get("gb_lcd", True))
        self._pano_frames: list[Image.Image] = []
        self._pano_mode = False
        self._status_var = tk.StringVar(value="Starting camera…")
        self._mode_var = tk.StringVar(value="Live")
        self._preview_message = "Opening camera…"

        self._build()
        self.root.after(100, self._pump)
        threading.Thread(target=self._boot_camera, daemon=True).start()

    def _build(self) -> None:
        # Pack bottom chrome first so the preview keeps the tall middle.
        status_bar = tk.Frame(self.root, bg=PANEL)
        status_bar.pack(fill="x", side="bottom")
        tk.Label(
            status_bar,
            textvariable=self._status_var,
            bg=PANEL,
            fg=MUTED,
            anchor="w",
            padx=12,
            pady=6,
            font=ui_font(10),
        ).pack(side="left", fill="x", expand=True)
        tk.Label(
            status_bar,
            textvariable=self._mode_var,
            bg=PANEL,
            fg=ACCENT,
            anchor="e",
            padx=12,
            pady=6,
            font=ui_font(10, True),
        ).pack(side="right")

        header = tk.Frame(self.root, bg=FIELD, padx=12, pady=8)
        header.pack(fill="x", side="top")
        tk.Label(header, text="Pi Cam", bg=FIELD, fg=INK, font=ui_font(18, True)).pack(side="left")
        self.update_btn = tk.Button(
            header,
            text="Update",
            command=self.check_for_update,
            bg=FIELD,
            fg=MINE,
            activebackground=SELECT,
            activeforeground=MINE,
            relief="flat",
            padx=10,
            pady=4,
            font=ui_font(11, True),
        )
        self.update_btn.pack(side="right")
        self.backend_label = tk.Label(header, text="", bg=FIELD, fg=MUTED, font=ui_font(10))
        self.backend_label.pack(side="right", padx=(0, 10))
        tk.Frame(self.root, bg=LINE, height=1).pack(fill="x", side="top")

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=10)

        tools = tk.Frame(body, bg=BG)
        tools.pack(fill="x", side="bottom", pady=(10, 0))

        preview_wrap = tk.Frame(body, bg="#111", highlightthickness=1, highlightbackground=LINE)
        preview_wrap.pack(fill="both", expand=True)
        # Canvas letterboxes the frame so 16:9 / 4:3 never becomes a thin strip.
        self.preview = tk.Canvas(preview_wrap, bg="#111", highlightthickness=0)
        self.preview.pack(fill="both", expand=True)
        self.preview.bind("<Configure>", lambda _e: self._redraw_preview())

        def btn(parent, text, cmd, *, primary=False, danger=False):
            return tk.Button(
                parent,
                text=text,
                command=cmd,
                bg=DANGER if danger else (MINE if primary else FIELD),
                fg=MINE_INK if (primary or danger) else INK,
                activebackground="#0066d6" if primary else (SELECT if not danger else "#a93226"),
                activeforeground=MINE_INK if (primary or danger) else INK,
                relief="flat",
                padx=10,
                pady=5,
                font=ui_font(10, True),
            )

        row1 = tk.Frame(tools, bg=BG)
        row1.pack(fill="x")
        btn(row1, "Photo", self.take_photo, primary=True).pack(side="left")
        self.video_btn = btn(row1, "Record", self.toggle_video)
        self.video_btn.pack(side="left", padx=(8, 0))
        btn(row1, "Game Boy shot", self.take_gameboy).pack(side="left", padx=(8, 0))
        btn(row1, "Open folder", self.open_folder).pack(side="right")

        row2 = tk.Frame(tools, bg=BG)
        row2.pack(fill="x", pady=(6, 0))
        self.rotate_btn = btn(row2, self._rotate_label(), self.rotate_image)
        self.rotate_btn.pack(side="left")
        self.gb_live_btn = btn(row2, "GB live: off", self.toggle_gb_live)
        self.gb_live_btn.pack(side="left", padx=(8, 0))
        self.lcd_btn = btn(row2, f"LCD tint: {'on' if self._gb_lcd else 'off'}", self.toggle_lcd)
        self.lcd_btn.pack(side="left", padx=(8, 0))
        self.pano_btn = btn(row2, "Panorama: start", self.toggle_pano)
        self.pano_btn.pack(side="left", padx=(8, 0))
        self.pano_snap_btn = btn(row2, "Add pano frame", self.add_pano_frame)
        self.pano_snap_btn.pack(side="left", padx=(8, 0))
        self.pano_snap_btn.configure(state="disabled")
        btn(row2, "Finish pano", self.finish_pano).pack(side="left", padx=(8, 0))

    def _rotate_label(self) -> str:
        return f"Rotate: {int(self.camera.rotation) % 360}°"

    def _persist(self) -> None:
        save_settings({"rotation": int(self.camera.rotation) % 360, "gb_lcd": self._gb_lcd})

    def rotate_image(self) -> None:
        if self.camera.recording:
            self._status_var.set("Stop recording before rotating")
            return
        self.camera.rotation = (int(self.camera.rotation) + 90) % 360
        self.rotate_btn.configure(text=self._rotate_label())
        self._persist()
        self._status_var.set(f"Rotation {self.camera.rotation}° (saved)")

    def _boot_camera(self) -> None:
        info = self.camera.open()
        self._jobs.put(lambda: self._on_camera_ready(info))

    def _on_camera_ready(self, info) -> None:
        self.backend_label.configure(text=f"{info.backend} · {info.detail}")
        if info.backend == "none":
            self._status_var.set(info.detail)
            self._preview_message = info.detail + "\n\nOn the Pi run: bash install.sh"
            self._last_preview = None
            self._redraw_preview()
            return
        self._status_var.set(f"Camera ready · rotation {self.camera.rotation}°")
        self._preview_running = True
        self.root.after(30, self._tick_preview)

    def _tick_preview(self) -> None:
        if not self._preview_running:
            return
        try:
            frame = self.camera.grab()
            if frame is not None:
                show = frame
                if self._gb_live:
                    show = gameboy_frame(frame, lcd_tint=self._gb_lcd, scale=3)
                self._show_frame(show)
                tag = f"{show.size[0]}×{show.size[1]}"
                if self.camera.recording:
                    self._mode_var.set(f"Recording · {tag}")
                elif self._pano_mode:
                    self._mode_var.set(f"Pano {len(self._pano_frames)} · {tag}")
                elif self._gb_live:
                    self._mode_var.set(f"GB live · {tag}")
                else:
                    self._mode_var.set(f"Live · {tag} · {int(self.camera.rotation) % 360}°")
        except Exception as exc:
            self._status_var.set(f"Preview error: {exc}")
        self.root.after(33, self._tick_preview)

    def _show_frame(self, image: Image.Image) -> None:
        self._last_preview = image.convert("RGB")
        self._preview_message = ""
        w, h = self._last_preview.size
        self._mode_var.set(f"{w}×{h} · rot {int(self.camera.rotation) % 360}°")
        self._redraw_preview()

    def _redraw_preview(self) -> None:
        canvas = self.preview
        canvas.update_idletasks()
        cw = max(canvas.winfo_width(), 2)
        ch = max(canvas.winfo_height(), 2)
        canvas.delete("all")
        canvas.create_rectangle(0, 0, cw, ch, fill="#1a1a1a", outline="")
        image = self._last_preview
        if image is None:
            if self._preview_message:
                canvas.create_text(
                    cw // 2,
                    ch // 2,
                    text=self._preview_message,
                    fill="#ddd",
                    font=ui_font(12),
                    justify="center",
                    width=max(cw - 40, 100),
                )
            return
        w, h = image.size
        if w < 1 or h < 1:
            return
        # Largest rectangle with the frame's true aspect ratio (never stretch).
        scale = min(cw / float(w), ch / float(h))
        tw = max(1, int(round(w * scale)))
        th = max(1, int(round(h * scale)))
        # Guard against rounding drift that would reintroduce squash.
        if tw / th > w / h:
            tw = max(1, int(round(th * (w / h))))
        else:
            th = max(1, int(round(tw * (h / w))))
        fitted = image.resize((tw, th), Image.Resampling.BILINEAR)
        photo = ImageTk.PhotoImage(fitted)
        self._photo_ref = photo
        x0 = (cw - tw) // 2
        y0 = (ch - th) // 2
        canvas.create_rectangle(x0 - 1, y0 - 1, x0 + tw + 1, y0 + th + 1, outline="#333")
        canvas.create_image(x0, y0, image=photo, anchor="nw")

    def take_photo(self) -> None:
        frame = self.camera.still()
        if frame is None:
            self._status_var.set("No frame yet")
            return
        path = photos_dir() / stamp("photo", "jpg")
        frame.convert("RGB").save(path, quality=92)
        self._status_var.set(f"Saved {path}")

    def take_gameboy(self) -> None:
        frame = self.camera.still()
        if frame is None:
            self._status_var.set("No frame yet")
            return
        gb = gameboy_frame(frame, lcd_tint=self._gb_lcd, scale=4)
        path = photos_dir() / stamp("gameboy", "png")
        gb.save(path)
        self._show_frame(gb)
        self._status_var.set(f"Saved {path}")

    def toggle_video(self) -> None:
        if self.camera.recording:
            path = self.camera.stop_video()
            self.video_btn.configure(text="Record", bg=FIELD, fg=INK)
            self._status_var.set(f"Saved {path}" if path else "Recording stopped")
            return
        path = videos_dir() / stamp("video", "avi")
        try:
            self.camera.start_video(path)
        except Exception as exc:
            messagebox.showerror("Video", str(exc), parent=self.root)
            return
        self.video_btn.configure(text="Stop", bg=DANGER, fg=MINE_INK)
        self._status_var.set(f"Recording {path.name}")

    def toggle_gb_live(self) -> None:
        self._gb_live = not self._gb_live
        self.gb_live_btn.configure(text=f"GB live: {'on' if self._gb_live else 'off'}")

    def toggle_lcd(self) -> None:
        self._gb_lcd = not self._gb_lcd
        self.lcd_btn.configure(text=f"LCD tint: {'on' if self._gb_lcd else 'off'}")
        self._persist()

    def toggle_pano(self) -> None:
        if self._pano_mode:
            self._pano_mode = False
            self._pano_frames.clear()
            self.pano_btn.configure(text="Panorama: start")
            self.pano_snap_btn.configure(state="disabled")
            self._status_var.set("Panorama cancelled")
            return
        self._pano_mode = True
        self._pano_frames = []
        self.pano_btn.configure(text="Panorama: cancel")
        self.pano_snap_btn.configure(state="normal")
        self._status_var.set("Panorama: pan slowly and add overlapping frames")

    def add_pano_frame(self) -> None:
        if not self._pano_mode:
            return
        frame = self.camera.still()
        if frame is None:
            self._status_var.set("No frame yet")
            return
        self._pano_frames.append(frame.copy())
        self._status_var.set(f"Panorama frame {len(self._pano_frames)} added")

    def finish_pano(self) -> None:
        if len(self._pano_frames) < 2:
            self._status_var.set("Need at least 2 panorama frames")
            return
        frames = list(self._pano_frames)
        self._status_var.set("Stitching panorama…")

        def work() -> None:
            try:
                out = stitch_panorama(frames)
                path = panoramas_dir() / stamp("pano", "jpg")
                out.save(path, quality=92)
                self._jobs.put(lambda: self._pano_done(out, path))
            except Exception as exc:
                self._jobs.put(lambda: self._status_var.set(f"Panorama failed: {exc}"))

        threading.Thread(target=work, daemon=True).start()

    def _pano_done(self, image: Image.Image, path: Path) -> None:
        self._pano_mode = False
        self._pano_frames.clear()
        self.pano_btn.configure(text="Panorama: start")
        self.pano_snap_btn.configure(state="disabled")
        self._show_frame(image)
        self._status_var.set(f"Saved {path}")

    def open_folder(self) -> None:
        path = data_dir() / "media"
        path.mkdir(parents=True, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                import subprocess

                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            self._status_var.set(f"Could not open folder: {exc}")

    def check_for_update(self) -> None:
        self.update_btn.configure(state="disabled", text="Checking")
        threading.Thread(target=self._look_for_update, daemon=True).start()

    def _look_for_update(self) -> None:
        try:
            status = check_update(install_root())
        except Exception as exc:
            self._jobs.put(lambda detail=str(exc): self._update_failed(detail))
            return
        self._jobs.put(lambda: self._offer_update(status))

    def _restore_update_button(self) -> None:
        self.update_btn.configure(state="normal", text="Update")

    def _update_failed(self, detail: str) -> None:
        self._restore_update_button()
        messagebox.showerror("Update", detail or "Could not reach GitHub.", parent=self.root)

    def _offer_update(self, status) -> None:
        if not status.available:
            self._restore_update_button()
            messagebox.showinfo("Update", "Pi Cam is up to date.", parent=self.root)
            return
        question = "A newer Pi Cam is on GitHub. Install it and restart?"
        if not messagebox.askyesno("Update", question, parent=self.root):
            self._restore_update_button()
            return
        self.update_btn.configure(text="Downloading")
        threading.Thread(target=self._install_update, args=(status.remote,), daemon=True).start()

    def _install_status(self, text: str) -> None:
        self._jobs.put(lambda: self.update_btn.configure(text=text))

    def _install_update(self, sha: str) -> None:
        self._preview_running = False
        try:
            if self.camera.recording:
                self.camera.stop_video()
        except Exception:
            pass
        self.camera.close()
        try:
            install_update(install_root(), sha, on_status=self._install_status)
        except Exception as exc:
            self._jobs.put(lambda detail=str(exc): self._update_failed(detail))
            return
        self._jobs.put(self._finish_update)

    def _finish_update(self) -> None:
        messagebox.showinfo("Update", "Pi Cam is updated and will restart.", parent=self.root)
        self.close()
        relaunch()

    def _pump(self) -> None:
        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                break
            try:
                job()
            except Exception:
                pass
        if self.root.winfo_exists():
            self.root.after(100, self._pump)

    def close(self) -> None:
        self._preview_running = False
        try:
            if self.camera.recording:
                self.camera.stop_video()
        except Exception:
            pass
        self.camera.close()
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    App().run()
    return 0
