"""Local OpenCV monitor for Orca ego RGB and NaVILA decisions."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import textwrap
import time
from typing import Any, Callable, TypeVar

import numpy as np

from .contracts import RenderFrame


_T = TypeVar("_T")


class LiveMonitorError(RuntimeError):
    """Raised when the requested desktop monitor cannot be created."""


class LiveNavigationMonitor:
    """Display fresh ego RGB beside instruction and action diagnostics."""

    def __init__(
        self,
        *,
        window_name: str = "live monitor",
        panel_width: int = 560,
        cv2_module: Any | None = None,
    ) -> None:
        if panel_width < 320:
            raise ValueError("panel_width must be at least 320 pixels")
        if cv2_module is None:
            try:
                import cv2 as cv2_module
            except ImportError as exc:
                raise LiveMonitorError(
                    "OpenCV is required for --live-monitor"
                ) from exc
        self.cv2 = cv2_module
        self.window_name = str(window_name)
        self.panel_width = int(panel_width)
        self._opened = False
        self._last_canvas: np.ndarray | None = None

    def update(
        self,
        frame: RenderFrame,
        *,
        instruction: str,
        vlm_output: str = "Waiting for first VLM decision...",
        command: str = "none",
        status: str = "initializing",
        decision: int = 0,
        chunk_result: str = "none completed yet",
    ) -> None:
        rgb = np.asarray(frame.rgb)
        if rgb.ndim != 3 or rgb.shape[2] != 3:
            raise LiveMonitorError(f"invalid ego RGB shape {rgb.shape}")
        if rgb.dtype != np.uint8:
            rgb = np.clip(rgb, 0, 255).astype(np.uint8)
        ego_bgr = np.ascontiguousarray(rgb[..., ::-1])
        height = int(ego_bgr.shape[0])
        panel = np.full((height, self.panel_width, 3), 24, dtype=np.uint8)

        y = 32
        y = self._draw_line(panel, "live monitor", y, (80, 220, 255), 0.72)
        y = self._draw_line(
            panel,
            f"decision={decision}  step={frame.step_id}  sim={frame.sim_time_s:.2f}s",
            y + 8,
            (190, 190, 190),
            0.5,
        )
        y = self._draw_section(panel, "STATUS", status, y + 14, (130, 230, 130))
        y = self._draw_section(panel, "INSTRUCTION", instruction, y + 12, (235, 235, 235))
        y = self._draw_section(panel, "VLM OUTPUT", vlm_output, y + 12, (120, 210, 255))
        y = self._draw_section(panel, "EXECUTED CHUNK", command, y + 12, (255, 190, 100))
        self._draw_section(
            panel,
            "LAST CHUNK: IDEAL VS ACTUAL",
            chunk_result,
            y + 12,
            (180, 230, 140),
        )

        canvas = np.concatenate((ego_bgr, panel), axis=1)
        self._last_canvas = canvas
        self._show(canvas)

    def _draw_line(
        self,
        panel: np.ndarray,
        text: str,
        y: int,
        color: tuple[int, int, int],
        scale: float,
    ) -> int:
        if y < panel.shape[0] - 4:
            self.cv2.putText(
                panel,
                text,
                (18, y),
                self.cv2.FONT_HERSHEY_SIMPLEX,
                scale,
                color,
                1,
                self.cv2.LINE_AA,
            )
        return y + int(28 * max(scale, 0.5))

    def _draw_section(
        self,
        panel: np.ndarray,
        title: str,
        value: str,
        y: int,
        color: tuple[int, int, int],
    ) -> int:
        y = self._draw_line(panel, title, y, color, 0.55)
        width = max(24, int((self.panel_width - 36) / 9.0))
        lines = textwrap.wrap(str(value), width=width) or [""]
        for line in lines[:7]:
            y = self._draw_line(panel, line, y + 2, (225, 225, 225), 0.48)
        return y

    def _show(self, canvas: np.ndarray) -> None:
        try:
            if not self._opened:
                self.cv2.namedWindow(self.window_name, self.cv2.WINDOW_NORMAL)
                self.cv2.resizeWindow(
                    self.window_name,
                    int(canvas.shape[1]),
                    int(canvas.shape[0]),
                )
                self._opened = True
            self.cv2.imshow(self.window_name, canvas)
            self.cv2.waitKey(1)
        except Exception as exc:
            raise LiveMonitorError(
                "failed to open the ego/VLM monitor window; check DISPLAY and OpenCV GUI support"
            ) from exc

    def pump(self) -> None:
        """Keep the desktop window responsive while VLM inference blocks."""

        if self._opened:
            self.cv2.waitKey(20)

    def run_while_responsive(self, operation: Callable[[], _T]) -> _T:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="navila-vlm") as pool:
            future = pool.submit(operation)
            while not future.done():
                self.pump()
                time.sleep(0.02)
            return future.result()

    def close(self) -> None:
        if not self._opened:
            return
        try:
            self.cv2.destroyWindow(self.window_name)
            self.cv2.waitKey(1)
        except Exception:
            pass
        finally:
            self._opened = False
            self._last_canvas = None
