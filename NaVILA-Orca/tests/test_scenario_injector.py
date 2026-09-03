import numpy as np
from PIL import Image
import pytest

from navila_orca.veto import HazardInjectionEvent, ScenarioInjector


def _blank_frame(size=(32, 32)) -> Image.Image:
    return Image.fromarray(np.zeros((size[1], size[0], 3), dtype=np.uint8))


def test_frame_unchanged_outside_scheduled_window():
    injector = ScenarioInjector()
    injector.schedule(at_step=5, duration_steps=2)
    frame = _blank_frame()
    out = injector.inject(frame, step=0)
    assert np.array(out).sum() == 0  # untouched, still all-black


def test_frame_marked_during_scheduled_window():
    injector = ScenarioInjector()
    injector.schedule(at_step=5, duration_steps=2, label="PEDESTRIAN")
    frame = _blank_frame()
    out = injector.inject(frame, step=5)
    assert np.array(out).sum() > 0  # the red bars changed pixels
    # window persists across its whole duration
    out_next = injector.inject(frame, step=6)
    assert np.array(out_next).sum() > 0
    # and ends right after
    out_after = injector.inject(frame, step=7)
    assert np.array(out_after).sum() == 0


def test_inject_does_not_mutate_the_input_frame():
    injector = ScenarioInjector()
    injector.schedule(at_step=0, duration_steps=1)
    frame = _blank_frame()
    injector.inject(frame, step=0)
    assert np.array(frame).sum() == 0  # original frame is untouched


def test_active_event_reports_none_when_nothing_scheduled():
    injector = ScenarioInjector()
    assert injector.active_event(0) is None


def test_hazard_injection_event_rejects_bad_values():
    with pytest.raises(ValueError):
        HazardInjectionEvent(at_step=-1)
    with pytest.raises(ValueError):
        HazardInjectionEvent(at_step=0, duration_steps=0)
    with pytest.raises(ValueError):
        HazardInjectionEvent(at_step=0, label="")
