#!/usr/bin/env python3
"""
demo.py — 手汗演奏を「見る」ためのデモ表示。
時間軸を揃えて raw EDA / tonic / SCRピーク / ピアノロール / 発音リストを出す。

    python3 sim/demo.py            # 60s
    python3 sim/demo.py --duration 30 --seed 7
"""
from __future__ import annotations
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eda_model
import dsp
import midi_map
import viz

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(n: int) -> str:
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 1}"


def piano_roll(notes, total_sec: float, width: int = 64) -> str:
    """実際に鳴った各音を行に、時間を列にしたASCIIピアノロール。
    粒感モードでは1列に複数粒が重なるので濃淡(░▒▓█)で密度を表す。"""
    pitches = sorted({n[1] for n in notes}, reverse=True)  # 高音が上
    shades = " ░▒▓█"
    rows = []
    for p in pitches:
        counts = [0] * width
        for (t, note, vel, dur) in notes:
            if note != p:
                continue
            x = int(t / total_sec * (width - 1))
            if 0 <= x < width:
                counts[x] += 1
        mx = max(counts) or 1
        lane = "".join(shades[min(4, (c * 4 + mx - 1) // mx)] if c else " " for c in counts)
        rows.append(f"  {note_name(p):>4} │{lane}")
    return "\n".join(rows)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--fs", type=float, default=32.0)
    ap.add_argument("--density", type=float, default=1.5)
    args = ap.parse_args(argv)

    cfg = eda_model.EdaConfig(fs=args.fs, duration_sec=args.duration, seed=args.seed)
    samples = dsp.process(eda_model.generate(cfg), fs=args.fs)
    ev = midi_map.map_events_granular(samples, density=args.density, fs=args.fs)

    W = 64
    raw = [s.raw for s in samples]
    tn = [s.tonic_norm for s in samples]
    peaks = [s for s in samples if s.peak_amp is not None]

    print("╔" + "═" * (W + 8) + "╗")
    print(f"║ 手汗演奏デモ  {args.duration:.0f}s  seed={args.seed}".ljust(W + 8) + " ║")
    print("╚" + "═" * (W + 8) + "╝")
    print(f"  手汗(raw) │{viz.sparkline(raw, W)}")
    print(f"  明るさ/音量│{viz.sparkline(tn, W)}   (tonic→CC74/CC7)")
    print(f"  SCRピーク │{viz.peak_lane([0],[p.t for p in peaks],args.duration,W)}   (発汗の瞬間→発音)")
    print()
    print("  ── ピアノロール(ペンタトニック) ───────────────────────────────────")
    print(piano_roll(ev.notes, args.duration, W))
    print("  " + " " * 5 + "└" + "─" * W)
    print(f"  {'':>5}  0s{'時間 →'.center(W-6)}{args.duration:.0f}s")
    print()
    print(f"  発音 {len(ev.notes)} 音 / CCイベント {len(ev.cc)} 個")
    print("  最初の8音:")
    for (t, note, vel, dur) in ev.notes[:8]:
        bar = "▓" * int(vel / 127 * 20)
        print(f"    {t:5.1f}s  {note_name(note):>4}  vel{vel:3d} {bar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
