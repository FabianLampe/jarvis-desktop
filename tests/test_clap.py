import numpy as np

from voice_daemon.clap import ClapDetector

SR, FRAME = 16000, 1280


def _quiet():
    return (np.random.randn(FRAME) * 30).astype(np.int16)


def _clap():
    return (np.random.randn(FRAME) * 9000).astype(np.int16)


def _feed(det, frames):
    fired = False
    for f in frames:
        if det.process(f):
            fired = True
    return fired


def test_double_clap_triggers():
    det = ClapDetector(sr=SR, frame=FRAME, gap_min_s=0.15, gap_max_s=0.8)
    frames = [_quiet()] * 5 + [_clap()] + [_quiet()] * 4 + [_clap()] + [_quiet()] * 2
    assert _feed(det, frames) is True


def test_single_clap_does_not_trigger():
    det = ClapDetector(sr=SR, frame=FRAME)
    assert _feed(det, [_quiet()] * 5 + [_clap()] + [_quiet()] * 6) is False


def test_sustained_loud_does_not_trigger():
    det = ClapDetector(sr=SR, frame=FRAME)
    assert _feed(det, [_quiet()] * 3 + [_clap()] * 12 + [_quiet()] * 3) is False


def test_too_far_apart_does_not_trigger():
    det = ClapDetector(sr=SR, frame=FRAME, gap_max_s=0.8)
    assert _feed(det, [_quiet()] * 3 + [_clap()] + [_quiet()] * 20 + [_clap()] + [_quiet()] * 2) is False
