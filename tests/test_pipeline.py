"""
test_pipeline.py — DSP/写像/MIDI出力の回帰テスト。

pytest でも素の python3 でも走る:
    python3 tests/test_pipeline.py        # 自前ランナー
    pytest tests/test_pipeline.py
"""
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sim"))
import dsp
import eda_model
import midi_map


def test_tonic_converges_to_constant():
    """一定入力 → tonic はその値へ収束する。"""
    fs = 32.0
    out = dsp.process([6.0] * int(fs * 40), fs=fs)
    assert abs(out[-1].tonic - 6.0) < 0.05, out[-1].tonic


def test_tonic_norm_bounded():
    """tonic_norm は常に 0..1。"""
    data = eda_model.generate(eda_model.EdaConfig(duration_sec=30, seed=1))
    out = dsp.process(data)
    assert all(0.0 <= s.tonic_norm <= 1.0 for s in out)


def test_clean_scr_pulse_detected():
    """きれいなSCRパルス1発 → ちょうど1ピークを、立ち上がりから数秒以内に検出。"""
    fs = 32.0
    n = int(fs * 30)
    onset = 5.0
    sig = []
    for i in range(n):
        t = i / fs
        sig.append(6.0 + 0.6 * eda_model.scr_kernel(t - onset) / eda_model._PEAK_VAL)
    out = dsp.process(sig, fs=fs)
    peaks = [s for s in out if s.peak_amp is not None]
    assert len(peaks) == 1, f"expected 1 peak, got {len(peaks)}"
    # SCRの山頂は onset + ~2.4s 付近。妥当な窓に入っているか。
    assert onset + 0.5 < peaks[0].t < onset + 6.0, peaks[0].t


def test_refractory_blocks_double_trigger():
    """不応期内の2つ目の小山は無視される。"""
    fs = 32.0
    n = int(fs * 20)
    sig = []
    for i in range(n):
        t = i / fs
        a = 0.6 * eda_model.scr_kernel(t - 3.0) / eda_model._PEAK_VAL
        b = 0.4 * eda_model.scr_kernel(t - 3.4) / eda_model._PEAK_VAL  # 0.4s後=不応期内
        sig.append(6.0 + a + b)
    out = dsp.process(sig, fs=fs, refractory_sec=1.0)
    peaks = [s for s in out if s.peak_amp is not None]
    assert len(peaks) == 1, f"refractory should merge to 1, got {len(peaks)}"


def test_flat_input_no_peaks():
    """無刺激の平坦入力ではピーク0(誤発火しない)。"""
    out = dsp.process([6.0] * int(32 * 20), fs=32.0)
    assert sum(1 for s in out if s.peak_amp is not None) == 0


def test_smf_is_valid():
    """write_smf が妥当なSMF(MThd/MTrk)を吐く。"""
    data = eda_model.generate(eda_model.EdaConfig(duration_sec=20, seed=3))
    ev = midi_map.map_events(dsp.process(data))
    assert ev.notes, "ノートが1つも無い"
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.mid")
        midi_map.write_smf(ev, p)
        b = open(p, "rb").read()
    assert b[:4] == b"MThd"
    assert b[8:14] == bytes.fromhex("000000010001") or b[8:10] == b"\x00\x00"  # format 0
    assert b"MTrk" in b
    assert b[-4:] == b"\xFF\x2F\x00".rjust(4, b"\x00")[-4:] or b[-3:] == b"\xFF\x2F\x00"


def test_velocity_in_range():
    """velocity は 1..127 の範囲。"""
    data = eda_model.generate(eda_model.EdaConfig(duration_sec=40, seed=5))
    ev = midi_map.map_events(dsp.process(data))
    for (_, note, vel, _) in ev.notes:
        assert 1 <= vel <= 127, vel
        assert 0 <= note <= 127


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns)-failed}/{len(fns)} passed")
    return failed


if __name__ == "__main__":
    raise SystemExit(1 if _run_all() else 0)
