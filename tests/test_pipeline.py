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


def test_granular_is_dense_and_valid():
    """粒感モード: sparseより遥かに音数が多く、note/velが範囲内、時刻は単調非減少。"""
    data = eda_model.generate(eda_model.EdaConfig(duration_sec=40, seed=5))
    samples = dsp.process(data)
    sparse = midi_map.map_events(samples)
    gran = midi_map.map_events_granular(samples, density=1.5, fs=32.0)
    assert len(gran.notes) > 10 * len(sparse.notes), (len(gran.notes), len(sparse.notes))
    last = -1.0
    for (t, note, vel, dur) in gran.notes:
        assert 0 <= note <= 127 and 1 <= vel <= 127 and dur > 0
        assert t >= last - 1e-9          # 時刻は単調(SMFの delta が負にならない)
        last = t


def test_granular_density_scales():
    """density を上げると粒が増える(単調)。"""
    samples = dsp.process(eda_model.generate(eda_model.EdaConfig(duration_sec=30, seed=2)))
    lo = len(midi_map.map_events_granular(samples, density=0.5, fs=32.0).notes)
    hi = len(midi_map.map_events_granular(samples, density=2.0, fs=32.0).notes)
    assert hi > lo, (lo, hi)


def test_ambient_is_sparse_long_overlapping():
    """アンビエント: 粒感より疎、長音(平均dur長い)、和音で重なる。"""
    samples = dsp.process(eda_model.generate(eda_model.EdaConfig(duration_sec=60, seed=42)))
    gran = midi_map.map_events_granular(samples, density=1.5, fs=32.0)
    amb = midi_map.map_events_ambient(samples, fs=32.0)
    assert len(amb.notes) < len(gran.notes) / 5, (len(amb.notes), len(gran.notes))
    avg_amb = sum(d for _, _, _, d in amb.notes) / len(amb.notes)
    avg_gran = sum(d for _, _, _, d in gran.notes) / len(gran.notes)
    assert avg_amb > 3.0 and avg_amb > avg_gran * 5, (avg_amb, avg_gran)
    for (_, note, vel, dur) in amb.notes:
        assert 0 <= note <= 127 and 1 <= vel <= 127 and dur > 0


def test_reverb_preserves_length_and_adds_tail():
    """reverbは長さを保ち、ドライ成分を残し、遅延後に残響を伸ばす。"""
    import synth
    buf = [0.0] * 6000               # 最短コム遅延(1116)より十分長く
    buf[100] = 1.0                   # インパルス
    out = synth.reverb(buf, sr=44100, wet=0.7, dry=0.3)
    assert len(out) == len(buf)
    assert abs(out[100] - 0.3) < 1e-6                  # ドライ成分が通る
    assert any(abs(x) > 1e-5 for x in out[1300:])      # コム遅延後に残響が出る


def test_instruments_midi_and_adsr():
    """音程換算とADSR包絡の基本性質。"""
    import instruments as ins
    assert abs(ins.midi_hz(69) - 440.0) < 1e-6
    assert abs(ins.midi_hz(81) - 880.0) < 1e-6
    env = (0.1, 0.1, 0.5, 0.2)
    assert ins.adsr(-1, 1.0, *env) == 0.0
    assert abs(ins.adsr(0.1, 1.0, *env) - 1.0) < 1e-6      # アタック頂点
    assert abs(ins.adsr(0.5, 1.0, *env) - 0.5) < 1e-6      # サステイン
    assert ins.adsr(1.0 + 0.2 + 0.01, 1.0, *env) == 0.0    # リリース後は無音


def test_ladder_attenuates_highs():
    """ラダーフィルタ: 低遮断にすると高域(ナイキスト付近)の振幅が大きく減る。"""
    import instruments as ins
    sr = 44100
    n = 4000
    hi = [(1.0 if i % 2 == 0 else -1.0) for i in range(n)]  # 最高周波(ナイキスト)
    out = ins.ladder(hi, sr, [0.0] * 64, 32.0, lo_hz=300, hi_hz=300, res=0.0)
    import statistics
    r_in = statistics.pstdev(hi[1000:])
    r_out = statistics.pstdev(out[1000:])
    assert r_out < r_in * 0.2, (r_in, r_out)


def test_write_stereo_valid(tmp_path=None):
    """write_stereo が2ch/16bitの妥当なWAVを書く。"""
    import wave as _w, tempfile, os as _os
    import instruments as ins
    L = [0.1 * ((i % 100) / 100) for i in range(2000)]
    R = [-x for x in L]
    d = tempfile.mkdtemp()
    p = _os.path.join(d, "s.wav")
    ins.write_stereo(L, R, p, 44100)
    w = _w.open(p, "rb")
    assert w.getnchannels() == 2 and w.getsampwidth() == 2 and w.getnframes() == 2000
    w.close()


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
