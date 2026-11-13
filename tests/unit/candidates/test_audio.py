"""Unit tests for audio RMS peak detection (pure math, no subprocess)."""

from __future__ import annotations

from clipsmith.candidates.audio import find_rms_peaks


def test_rms_peaks_detects_spike() -> None:
    # 9 windows at -20 dB, 1 window at -10 dB (clear spike)
    series = [(float(i), -20.0) for i in range(9)]
    series.append((9.0, -10.0))

    peaks = find_rms_peaks(series, peak_multiplier=2.0)

    assert len(peaks) == 1
    t, score = peaks[0]
    assert t == 9.0
    assert score > 0


def test_rms_peaks_flat_signal_no_peaks() -> None:
    # All windows at the same dB → std ≈ 0 → no peaks
    series = [(float(i), -20.0) for i in range(20)]

    peaks = find_rms_peaks(series, peak_multiplier=2.0)

    assert peaks == []


def test_rms_peaks_a_long_silence_does_not_hide_a_bang() -> None:
    # A pause (BRB, fade) far below the stream, steady background, one bang 15 dB above it.
    silence = [(float(i), -80.0) for i in range(300)]
    background = [(300.0 + i, -30.0 + (i % 3) - 1) for i in range(300)]
    series = [*silence, *background, (600.0, -15.0)]

    peaks = find_rms_peaks(series, peak_multiplier=2.0)

    assert [t for t, _ in peaks] == [600.0]
