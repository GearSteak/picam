"""Camera capture — picamera2 on Pi, OpenCV webcam fallback elsewhere."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image


@dataclass
class CameraInfo:
    backend: str
    detail: str


class Camera:
    """Thread-safe preview + still/video helpers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._picam = None
        self._cv = None
        self._cv_index = 0
        self._recording = False
        self._writer = None
        self._record_path: Path | None = None
        self._info = CameraInfo("none", "Camera not opened")
        self._last_frame: Image.Image | None = None
        self.rotation = 0  # 0, 90, 180, 270 — applied to every grabbed frame

    @property
    def info(self) -> CameraInfo:
        return self._info

    @property
    def recording(self) -> bool:
        return self._recording

    def open(self) -> CameraInfo:
        with self._lock:
            if self._picam is not None or self._cv is not None:
                return self._info
            if self._try_picamera2():
                return self._info
            if self._try_opencv():
                return self._info
            self._info = CameraInfo("none", "No camera found (picamera2 / OpenCV)")
            return self._info

    def close(self) -> None:
        with self._lock:
            self._stop_writer_unlocked()
            if self._picam is not None:
                try:
                    self._picam.stop()
                    self._picam.close()
                except Exception:
                    pass
                self._picam = None
            if self._cv is not None:
                try:
                    self._cv.release()
                except Exception:
                    pass
                self._cv = None
            self._info = CameraInfo("none", "Camera closed")

    def grab(self) -> Image.Image | None:
        with self._lock:
            frame = self._grab_unlocked()
            if frame is not None:
                self._last_frame = frame
                if self._recording and self._writer is not None:
                    self._write_frame_unlocked(frame)
            return frame

    def last_frame(self) -> Image.Image | None:
        with self._lock:
            return self._last_frame

    def still(self) -> Image.Image | None:
        frame = self.grab()
        if frame is None:
            with self._lock:
                return self._last_frame
        return frame

    def start_video(self, path: Path, fps: int = 24) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if self._recording:
                return
            frame = self._grab_unlocked() or self._last_frame
            if frame is None:
                raise RuntimeError("No frame yet — wait for the preview.")
            w, h = frame.size
            self._open_writer_unlocked(path, w, h, fps)
            self._recording = True
            self._record_path = path

    def stop_video(self) -> Path | None:
        with self._lock:
            path = self._record_path
            self._stop_writer_unlocked()
            self._recording = False
            self._record_path = None
            return path

    def _try_picamera2(self) -> bool:
        try:
            from picamera2 import Picamera2
        except Exception:
            return False
        try:
            cam = Picamera2()
            config = cam.create_preview_configuration(
                main={"size": (1280, 720), "format": "RGB888"}
            )
            cam.configure(config)
            cam.start()
            time.sleep(0.3)
            self._picam = cam
            self._info = CameraInfo("picamera2", "Raspberry Pi camera")
            return True
        except Exception as exc:
            self._info = CameraInfo("none", f"picamera2 failed: {exc}")
            return False

    def _try_opencv(self) -> bool:
        try:
            import cv2
        except Exception:
            return False
        for index in (0, 1, 2):
            cap = cv2.VideoCapture(index)
            if not cap.isOpened():
                cap.release()
                continue
            ok, _ = cap.read()
            if not ok:
                cap.release()
                continue
            self._cv = cap
            self._cv_index = index
            self._info = CameraInfo("opencv", f"Webcam index {index}")
            return True
        return False

    def _grab_unlocked(self) -> Image.Image | None:
        image = None
        if self._picam is not None:
            try:
                arr = self._picam.capture_array("main")
                # Picamera2 RGB888 is already RGB order on recent builds.
                if arr.ndim == 3 and arr.shape[2] >= 3:
                    rgb = arr[:, :, :3]
                    image = Image.fromarray(rgb.copy(), mode="RGB")
            except Exception:
                return None
        elif self._cv is not None:
            try:
                import cv2

                ok, frame = self._cv.read()
                if not ok or frame is None:
                    return None
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image = Image.fromarray(rgb)
            except Exception:
                return None
        if image is None:
            return None
        return self._apply_rotation(image)

    def _apply_rotation(self, image: Image.Image) -> Image.Image:
        degrees = int(self.rotation) % 360
        if degrees == 0:
            return image
        if degrees == 90:
            return image.transpose(Image.Transpose.ROTATE_90)
        if degrees == 180:
            return image.transpose(Image.Transpose.ROTATE_180)
        if degrees == 270:
            return image.transpose(Image.Transpose.ROTATE_270)
        return image.rotate(-degrees, expand=True)

    def _open_writer_unlocked(self, path: Path, width: int, height: int, fps: int) -> None:
        try:
            import cv2
        except Exception as exc:
            raise RuntimeError("OpenCV is required for video recording") from exc
        fourcc = cv2.VideoWriter_fourcc(*"MJPG")
        writer = cv2.VideoWriter(str(path), fourcc, float(fps), (width, height))
        if not writer.isOpened():
            raise RuntimeError(f"Could not open video file {path}")
        self._writer = writer

    def _write_frame_unlocked(self, frame: Image.Image) -> None:
        if self._writer is None:
            return
        try:
            import cv2

            arr = np.asarray(frame.convert("RGB"))
            bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            self._writer.write(bgr)
        except Exception:
            pass

    def _stop_writer_unlocked(self) -> None:
        if self._writer is not None:
            try:
                self._writer.release()
            except Exception:
                pass
        self._writer = None


def stitch_panorama(frames: list[Image.Image]) -> Image.Image:
    """Stitch left-to-right stills into a panorama."""
    if not frames:
        raise RuntimeError("No panorama frames")
    if len(frames) == 1:
        return frames[0].convert("RGB")
    try:
        import cv2
    except Exception as exc:
        raise RuntimeError("OpenCV is required for panorama") from exc

    bgrs = []
    for im in frames:
        rgb = np.asarray(im.convert("RGB"))
        bgrs.append(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    stitcher = cv2.Stitcher_create(cv2.Stitcher_PANORAMA)
    status, out = stitcher.stitch(bgrs)
    if status != cv2.Stitcher_OK or out is None:
        # Fallback: simple side-by-side if stitch fails (not enough overlap).
        heights = [im.size[1] for im in frames]
        h = min(heights)
        resized = [im.convert("RGB").resize((int(im.size[0] * h / im.size[1]), h)) for im in frames]
        total_w = sum(im.size[0] for im in resized)
        canvas = Image.new("RGB", (total_w, h))
        x = 0
        for im in resized:
            canvas.paste(im, (x, 0))
            x += im.size[0]
        return canvas
    rgb = cv2.cvtColor(out, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)
