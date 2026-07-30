import numpy as np

import navila_orca.live_monitor as live_monitor_module
from navila_orca.contracts import RenderFrame
from navila_orca.live_monitor import LiveNavigationMonitor


class FakeCv2:
    WINDOW_NORMAL = 0
    FONT_HERSHEY_SIMPLEX = 0
    LINE_AA = 0

    def __init__(self):
        self.canvas = None

    def putText(self, image, *_args, **_kwargs):
        return image

    def namedWindow(self, *_args):
        pass

    def resizeWindow(self, *_args):
        pass

    def imshow(self, _name, canvas):
        self.canvas = np.array(canvas, copy=True)

    def waitKey(self, _delay):
        return -1

    def destroyWindow(self, *_args):
        pass


def test_live_monitor_composes_ego_and_text_panel():
    cv2 = FakeCv2()
    monitor = LiveNavigationMonitor(panel_width=560, cv2_module=cv2)
    frame = RenderFrame(
        25,
        0.5,
        "ego",
        np.zeros((512, 512, 3), dtype=np.uint8),
        "25",
    )
    monitor.update(
        frame,
        instruction="Walk to the orange bin.",
        vlm_output="The next action is turn left 15 degree.",
        command="wz=0.524 rad/s, duration=0.50s",
        status="executing motion chunk",
        decision=2,
    )

    assert cv2.canvas.shape == (512, 1072, 3)
    monitor.close()


def test_live_monitor_replaces_opencv_missing_qt_font_path(
    tmp_path, monkeypatch
):
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    (fonts / "TestSans.ttf").write_bytes(b"test font placeholder")
    monkeypatch.setenv("QT_QPA_FONTDIR", str(tmp_path / "cv2/qt/fonts"))
    monkeypatch.delenv("NAVILA_ORCA_QT_FONTDIR", raising=False)
    monkeypatch.setattr(live_monitor_module, "_SYSTEM_QT_FONT_DIRS", (fonts,))

    LiveNavigationMonitor(cv2_module=FakeCv2())

    assert live_monitor_module.os.environ["QT_QPA_FONTDIR"] == str(fonts)
