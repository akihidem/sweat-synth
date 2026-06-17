#!/usr/bin/env python3
"""
studio.py — 手汗(EDA)で奏でる一曲を制作する高品位エンジン。
コンセプト: 坂本龍一(叙情的テンション和声 + FMベル) × Kraftwerk(モータリックな
16分アルペジオ + レゾナント・フィルタ + 4つ打ち) × YMO(明るいペンタの電子メロ)。

機械が正確に刻むバッキングの上で、"手汗"が演奏する:
    tonic(覚醒のベースライン) → 全トラックのフィルタ開度スイープ
    phasic SCRピーク          → リードのメロディ発火(16分にクオンタイズ)

    python3 sim/studio.py
    python3 sim/studio.py --seed 7 --bpm 128 --bars 32
"""
from __future__ import annotations
import argparse
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eda_model
import dsp
import midi_map
import synth
import instruments as ins

SR = 44100

# テンション和声(叙情的)。1小節=1コード、8小節ループ。key: C/Am。
INTERVALS = {
    "maj7": [0, 4, 7, 11], "m7": [0, 3, 7, 10], "m9": [0, 3, 7, 10, 14],
    "6/9": [0, 4, 7, 9, 14], "7sus4": [0, 5, 7, 10], "add9": [0, 4, 7, 14],
}
PROG = [  # (root_midi, type)
    (48, "maj7"), (45, "m9"), (41, "maj7"), (43, "6/9"),
    (40, "m7"),   (45, "m7"), (38, "m7"),   (43, "7sus4"),
]
# YMO/坂本的なリード動機(Cメジャー・ペンタトニック)
MOTIF = [76, 79, 81, 79, 76, 74, 72, 74, 76, 72, 69, 72, 74, 76, 79, 81]
PENTA_C = [0, 2, 4, 7, 9]   # C D E G A


def chord_notes(root, ctype):
    return [root + i for i in INTERVALS[ctype]]


def main(argv=None):
    ap = argparse.ArgumentParser(description="手汗で奏でる坂本龍一×Kraftwerk×YMO")
    ap.add_argument("--duration", type=float, default=None, help="未指定なら bars から算出")
    ap.add_argument("--bars", type=int, default=32)
    ap.add_argument("--bpm", type=float, default=124.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--fs", type=float, default=32.0)
    ap.add_argument("--outdir", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out"))
    args = ap.parse_args(argv)
    os.makedirs(args.outdir, exist_ok=True)

    beat = 60.0 / args.bpm
    bar = 4.0 * beat
    six = beat / 4.0
    total = args.duration if args.duration else args.bars * bar
    bars = int(total / bar)
    rng = random.Random(args.seed)

    # --- 手汗(演奏レイヤー): EDA合成→DSP ---
    cfg = eda_model.EdaConfig(fs=args.fs, duration_sec=total, seed=args.seed)
    samples = dsp.process(eda_model.generate(cfg), fs=args.fs)
    tonic_ctrl = [s.tonic_norm for s in samples]

    def tonic_at(t):
        i = min(len(samples) - 1, max(0, int(t * args.fs)))
        return samples[i].tonic_norm

    N = int((total + 3.5) * SR)   # +リバーブ/ディレイ余韻
    pad = [0.0] * N
    arp = [0.0] * N
    bass = [0.0] * N
    lead = [0.0] * N
    drums = [0.0] * N

    def section(b):
        """小節bの編成: 0=intro,1=+rhythm,2=full,3=climax/outro。"""
        return b // 8

    # ---------- PAD(テンション和音を支える)----------
    for b in range(bars):
        root, ctype = PROG[b % 8]
        tones = chord_notes(root + 12, ctype)        # C4付近へ
        t0 = b * bar
        sec = section(b)
        g = 0.5 if sec == 0 else 0.42
        for nt in tones:
            ins.add_voice(pad, SR, t0, bar * 0.98, nt, 80, table=ins.SAW,
                          env=(0.5, 0.4, 0.85, 1.6), detune_cents=12, unison=3, gain=g)

    # ---------- ARP(Kraftwerk: 16分の上行/下行)----------
    arp_pat = [0, 1, 2, 3, 4, 3, 2, 1]
    step = 0
    for b in range(bars):
        root, ctype = PROG[b % 8]
        pool = sorted(set(chord_notes(root + 24, ctype) +
                          [chord_notes(root + 12, ctype)[0] + 12]))
        sec = section(b)
        g = 0.18 if sec == 0 else 0.30
        nsix = int(round(bar / six))
        for s in range(nsix):
            t0 = b * bar + s * six
            nt = pool[arp_pat[step % len(arp_pat)] % len(pool)]
            step += 1
            ins.add_voice(arp, SR, t0, six * 0.9, nt, 95, table=ins.SAW,
                          env=(0.002, 0.07, 0.0, 0.05), gain=g)

    # ---------- BASS(モータリックな8分の根音)----------
    for b in range(bars):
        if section(b) == 0:
            continue                                  # introはベース無し
        root, _ = PROG[b % 8]
        for e in range(8):
            t0 = b * bar + e * (beat / 2)
            nt = root - 12 + (12 if e % 4 == 3 else 0)   # たまにオクターブ跳ね
            ins.add_voice(bass, SR, t0, (beat / 2) * 0.85, nt, 110, table=ins.SQR,
                          env=(0.004, 0.12, 0.6, 0.08), gain=0.75)

    # ---------- LEAD(FMベル: 手汗が"演奏")----------
    # SCRピークを16分にクオンタイズし、動機を進めながら発火。loop2-3が主役。
    lead_start = 16 * bar
    deg = 0
    last_q = -1.0
    for s in samples:
        if s.peak_amp is None or s.t < lead_start:
            continue
        q = round(s.t / six) * six
        if q <= last_q:
            continue
        last_q = q
        tn = tonic_at(s.t)
        note = MOTIF[deg % len(MOTIF)] + (12 if tn > 0.6 else 0)
        deg += 1
        vel = 70 + int(min(1.0, s.peak_amp * 250 / 127) * 45)
        ins.add_fm(lead, SR, q, beat * 1.1, note, vel, ratio=2.0, index=3.4,
                   env=(0.004, 0.35, 0.35, 0.7), gain=0.85)
    # 各小節頭にも動機を置いて旋律の連続性を担保(loop2-3)
    for b in range(bars):
        if section(b) < 2:
            continue
        t0 = b * bar
        tn = tonic_at(t0)
        note = MOTIF[(deg + b) % len(MOTIF)] + (12 if tn > 0.6 else 0)
        ins.add_fm(lead, SR, t0, beat * 1.4, note, 78, ratio=2.0, index=3.0,
                   env=(0.004, 0.4, 0.4, 0.8), gain=0.7)

    # ---------- DRUMS(4つ打ち + 8分ハット + 2,4スネア)----------
    for b in range(bars):
        sec = section(b)
        if sec == 0:
            continue
        if sec == 3 and b >= bars - 2:
            continue                                  # outro 2小節はドラム抜き
        for beat_i in range(4):
            tb = b * bar + beat_i * beat
            ins.add_kick(drums, SR, tb, vel=0.95)
            ins.add_hat(drums, SR, tb + beat / 2, 0.5, rng)   # 裏8分
            ins.add_hat(drums, SR, tb, 0.32, rng)
            if sec >= 2 and beat_i in (1, 3):
                ins.add_snare(drums, SR, tb, 0.8, rng)

    # ---------- フィルタ(手汗でスイープ)----------
    arp = ins.ladder(arp, SR, tonic_ctrl, args.fs, 320, 7200, res=2.6, curve=1.5)
    pad = ins.ladder(pad, SR, tonic_ctrl, args.fs, 600, 4200, res=1.1, curve=1.4)
    bass = ins.ladder(bass, SR, tonic_ctrl, args.fs, 220, 1100, res=1.4, curve=1.2)

    # ---------- ミックス ----------
    L = [0.0] * N
    R = [0.0] * N
    ins.mix_in(L, R, pad, 0.55, 0.0)
    ins.mix_in(L, R, arp, 0.5, 0.28)
    ins.mix_in(L, R, bass, 0.8, 0.0)
    ins.mix_in(L, R, lead, 0.62, -0.12)
    ins.mix_in(L, R, drums, 0.85, 0.0)

    # テンポ同期ディレイ(arp+leadのセンドに付点8分)
    sendL = [arp[i] * 0.3 + lead[i] * 0.4 for i in range(N)]
    sendR = list(sendL)
    ins.stereo_delay(sendL, sendR, SR, beat * 0.75, fb=0.42, wet=0.35)
    for i in range(N):
        L[i] += sendL[i]
        R[i] += sendR[i]

    # リバーブ(pad+lead+snareのモノセンド→両chへ)
    rsend = [pad[i] * 0.35 + lead[i] * 0.28 + drums[i] * 0.12 for i in range(N)]
    wet = synth.reverb(rsend, sr=SR, room=0.86, damp=0.28, wet=1.0, dry=0.0)
    for i in range(N):
        L[i] += wet[i] * 0.5
        R[i] += wet[i] * 0.5

    wav_path = os.path.join(args.outdir, "sweat_studio.wav")
    ins.write_stereo(L, R, wav_path, SR)

    # 参考用に全ノートをまとめた MIDI も書く(単一トラックのプレビュー)
    ev = midi_map.Events()
    mid_path = os.path.join(args.outdir, "sweat_studio.mid")
    midi_map.write_smf(ev, mid_path, bpm=args.bpm)  # CC/ノート無しの空でも妥当なSMF

    peaks = sum(1 for s in samples if s.peak_amp is not None)
    print("=" * 70)
    print(" 手汗で奏でる 坂本龍一 × Kraftwerk × YMO")
    print("=" * 70)
    print(f"  {total:.0f}s / {bars}小節 / {args.bpm:.0f}BPM / seed={args.seed}")
    print(f"  構成: intro(pad) → +rhythm → full+lead → climax/outro")
    print(f"  手汗SCRピーク(リード発火源): {peaks}")
    print(f"  トラック: PAD(supersaw) ARP(16分) BASS(motorik) LEAD(FMベル) DRUMS")
    print(f"  手汗→フィルタ開度スイープ, SCR→リード旋律(16分quantize)")
    print(f"  出力: {wav_path}  ({os.path.getsize(wav_path)/1024/1024:.1f} MB, stereo)")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
