#!/usr/bin/env python3
"""
run.py — 手汗演奏 end-to-end デモ(ハード不要)。

  手汗(EDA)合成 → DSP(正規化/ピーク検出) → 音楽イベント写像
                → out/sweat_demo.wav (耳で確認) + out/sweat_demo.mid (DAWで開ける)

使い方:
    python3 sim/run.py
    python3 sim/run.py --duration 30 --seed 7 --bpm 100
"""
from __future__ import annotations
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eda_model
import dsp
import midi_map
import synth
import viz


def main(argv=None):
    ap = argparse.ArgumentParser(description="手汗で演奏 end-to-end デモ")
    ap.add_argument("--duration", type=float, default=60.0, help="秒")
    ap.add_argument("--fs", type=float, default=32.0, help="センサ標本化レート(Hz)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--bpm", type=float, default=90.0)
    ap.add_argument("--outdir", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out"))
    ap.add_argument("--no-audio", action="store_true", help="WAV生成を省く(速い)")
    args = ap.parse_args(argv)

    os.makedirs(args.outdir, exist_ok=True)

    # 1) 手汗信号を合成
    cfg = eda_model.EdaConfig(fs=args.fs, duration_sec=args.duration, seed=args.seed)
    raw = eda_model.generate(cfg)

    # 2) DSP
    samples = dsp.process(raw, fs=args.fs)

    # 3) 音楽イベントへ写像
    ev = midi_map.map_events(samples)

    # 4) 書き出し
    mid_path = os.path.join(args.outdir, "sweat_demo.mid")
    midi_map.write_smf(ev, mid_path, bpm=args.bpm)

    wav_path = None
    if not args.no_audio:
        buf = synth.render(ev, duration_sec=args.duration)
        wav_path = os.path.join(args.outdir, "sweat_demo.wav")
        synth.write_wav(buf, wav_path)

    # 5) サマリ + ASCII可視化
    peaks = [s for s in samples if s.peak_amp is not None]
    tonic_norm = [s.tonic_norm for s in samples]
    raw_vals = [s.raw for s in samples]
    filt_vals = [s.filt for s in samples]

    print("=" * 78)
    print(f" 手汗演奏デモ  {args.duration:.0f}s @ {args.fs:.0f}Hz  seed={args.seed}  bpm={args.bpm:.0f}")
    print("=" * 78)
    print(f" raw EDA   : {viz.sparkline(raw_vals)}")
    print(f" filtered  : {viz.sparkline(filt_vals)}   (リップル/ノイズ除去後)")
    print(f" tonic_norm: {viz.sparkline(tonic_norm)}   → CC74/CC7(明るさ・音量)")
    print(f" SCR peaks : {viz.peak_lane([0], [p.t for p in peaks], args.duration)}   → Note On")
    print("-" * 78)
    print(f" 検出ピーク(発音数) : {len(peaks)}")
    print(f" CCイベント数        : {len(ev.cc)}")
    print(f" raw EDA レンジ      : {min(raw_vals):.2f}..{max(raw_vals):.2f} µS")
    print(f" 出力 MIDI           : {mid_path}")
    if wav_path:
        sz = os.path.getsize(wav_path)
        print(f" 出力 WAV            : {wav_path}  ({sz/1024:.0f} KB)")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
