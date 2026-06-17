#!/usr/bin/env python3
"""
corpus_compose.py — 実コーパスの統計から作曲する(#3 の本命)。

前回の反省: 才能を"表面(和音のベタ書き)"で模倣して失敗した。
ここでは才能の"過程"を借りる ── 公共ドメインの J.S.Bach コラール382曲(SATB)から、
**和音→次の実際のvoicingへの遷移統計(Markov)**を学習する。Bachが実際に書いた声部の
運び(voice-leading)をそのまま継承し、Markovで組み替えることで新しい進行を生む。
坂本龍一自身がBach/Debussy由来なので、コラールの声部進行を電子音響パレットで鳴らすのは
狙いの叙情と遠くない。手汗(EDA)は tonic→フィルタの演奏レイヤーとして上に乗せる。

    python3 sim/corpus_compose.py
    python3 sim/corpus_compose.py --bars 24 --bpm 100 --chords-per-bar 2 --transpose -3 --seed 7
"""
from __future__ import annotations
import argparse
import json
import os
import pickle
import random
import sys
import warnings
from collections import defaultdict

warnings.simplefilter("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eda_model
import dsp
import synth
import instruments as ins

SR = 44100
DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "jsb_chorales.pkl")


# ---------------- コーパス読み込み & 統計学習 ----------------
def load_pieces():
    d = pickle.load(open(DATA, "rb"), encoding="latin1")
    out = []
    for split in ("train", "valid", "test"):
        out.extend(d.get(split, []))
    return out


def _norm_shift(piece):
    """コラールは主和音で終わる → 最終和音のバス音名を C(=0) に正規化する移調量。"""
    for s in reversed(piece):
        notes = [int(x) for x in s if x]
        if notes:
            return -(min(notes) % 12)
    return 0


def _pcs(c):
    return frozenset(x % 12 for x in c)


def learn(pieces):
    """1次(和音→次)と2次((2和音)→次)の遷移、開始voicing集合を学習。"""
    trans1 = defaultdict(list)
    trans2 = defaultdict(list)
    starts = []
    for piece in pieces:
        shift = _norm_shift(piece)
        events = []
        prev_pcs = None
        for s in piece:
            notes = sorted(int(x) + shift for x in s if x)
            if len(notes) < 3:
                continue
            pcs = frozenset(n % 12 for n in notes)
            if pcs != prev_pcs:                 # 連続同一和音はまとめる(和声リズム復元)
                events.append(tuple(notes))
                prev_pcs = pcs
        if len(events) >= 2:
            starts.append(events[0])
            for a, b in zip(events, events[1:]):
                trans1[_pcs(a)].append(b)
            for a, b, c in zip(events, events[1:], events[2:]):
                trans2[(_pcs(a), _pcs(b))].append(c)
    return trans1, trans2, starts


def generate(trans1, trans2, starts, n, rng, order=2):
    """Markovを歩いて n 和音の進行を作る(C正規化のvoicing列)。
    order=2 は直前2和音で条件付け、未知の2-gramは1次へフォールバック。"""
    seq = [rng.choice(starts)]
    fallback = 0
    if n > 1:
        nxt = trans1.get(_pcs(seq[-1]))
        seq.append(rng.choice(nxt) if nxt else rng.choice(starts))
    while len(seq) < n:
        nxt = None
        if order >= 2:
            nxt = trans2.get((_pcs(seq[-2]), _pcs(seq[-1])))
            if not nxt:                          # 2-gram未知 → 1次へ
                fallback += 1
                nxt = trans1.get(_pcs(seq[-1]))
        else:
            nxt = trans1.get(_pcs(seq[-1]))
        seq.append(rng.choice(nxt) if nxt else rng.choice(starts))
    return seq, fallback


# ---------------- 発音(instruments.py)----------------
def render(seq, bpm, chords_per_bar, transpose, seed):
    beat = 60.0 / bpm
    chord_dur = 4.0 * beat / chords_per_bar      # 1和音の秒数
    six = beat / 4.0
    total = len(seq) * chord_dur
    N = int((total + 3.5) * SR)

    # 移調 + レンジ調整(全体を聴きやすい音域へ)
    voic = [sorted(n + transpose for n in c) for c in seq]

    fs = 32.0
    samples = dsp.process(eda_model.generate(
        eda_model.EdaConfig(fs=fs, duration_sec=total, seed=seed)), fs=fs)
    tonic_ctrl = [s.tonic_norm for s in samples]

    pad = [0.0] * N
    arp = [0.0] * N
    bass = [0.0] * N
    lead = [0.0] * N
    drums = [0.0] * N

    nsix = max(1, int(round(chord_dur / six)))
    for i, notes in enumerate(voic):
        t0 = i * chord_dur
        top = notes[-1]
        low = notes[0]
        # PAD: 4声をそのまま保持(Bachのvoicing)
        for nt in notes:
            ins.add_voice(pad, SR, t0, chord_dur * 0.98, nt + 12, 74, table=ins.SAW,
                          env=(0.35, 0.4, 0.85, 1.3), detune_cents=10, unison=2, gain=0.4)
        # BASS: 最低声部を8分でモータリックに
        ne = max(1, int(round(chord_dur / (beat / 2))))
        for e in range(ne):
            ins.add_voice(bass, SR, t0 + e * (beat / 2), (beat / 2) * 0.85, low - 12, 110,
                          table=ins.SQR, env=(0.004, 0.12, 0.6, 0.08), gain=0.72)
        # LEAD: ソプラノ(=Bachの旋律)をFMベルで
        ins.add_fm(lead, SR, t0, chord_dur * 0.95, top + 12, 96, ratio=2.0, index=3.0,
                   env=(0.004, 0.35, 0.45, 0.6), gain=0.8)
        # ARP: 声部を16分でアルペジオ(Kraftwerk)
        for s in range(nsix):
            nt = notes[s % len(notes)] + 24
            ins.add_voice(arp, SR, t0 + s * six, six * 0.9, nt, 90, table=ins.SAW,
                          env=(0.002, 0.07, 0.0, 0.05), gain=0.24)

    # DRUMS: 中盤のみ4つ打ち
    rng = random.Random(seed)
    bars = int(len(seq) / chords_per_bar)
    bar = 4.0 * beat
    for b in range(bars // 4, bars * 3 // 4):
        for bi in range(4):
            tb = b * bar + bi * beat
            ins.add_kick(drums, SR, tb, 0.92)
            ins.add_hat(drums, SR, tb + beat / 2, 0.5, rng)
            if bi in (1, 3):
                ins.add_snare(drums, SR, tb, 0.78, rng)

    # 手汗→フィルタスイープ
    arp = ins.ladder(arp, SR, tonic_ctrl, fs, 320, 7000, res=2.6, curve=1.5)
    pad = ins.ladder(pad, SR, tonic_ctrl, fs, 600, 4200, res=1.1, curve=1.4)
    bass = ins.ladder(bass, SR, tonic_ctrl, fs, 220, 1100, res=1.4, curve=1.2)

    L = [0.0] * N
    R = [0.0] * N
    ins.mix_in(L, R, pad, 0.55, 0.0)
    ins.mix_in(L, R, arp, 0.42, 0.28)
    ins.mix_in(L, R, bass, 0.8, 0.0)
    ins.mix_in(L, R, lead, 0.66, -0.12)
    ins.mix_in(L, R, drums, 0.85, 0.0)

    sendL = [arp[i] * 0.3 + lead[i] * 0.4 for i in range(N)]
    sendR = list(sendL)
    ins.stereo_delay(sendL, sendR, SR, beat * 0.75, fb=0.42, wet=0.33)
    for i in range(N):
        L[i] += sendL[i]
        R[i] += sendR[i]
    rsend = [pad[i] * 0.35 + lead[i] * 0.28 + drums[i] * 0.12 for i in range(N)]
    wet = synth.reverb(rsend, sr=SR, room=0.86, damp=0.28, wet=1.0, dry=0.0)
    for i in range(N):
        L[i] += wet[i] * 0.5
        R[i] += wet[i] * 0.5
    return L, R


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bachコラール統計で作曲 → 手汗で演奏")
    ap.add_argument("--bars", type=int, default=24)
    ap.add_argument("--bpm", type=float, default=100.0)
    ap.add_argument("--chords-per-bar", type=int, default=2)
    ap.add_argument("--transpose", type=int, default=-3, help="C正規化からの移調(半音)")
    ap.add_argument("--order", type=int, default=2, choices=[1, 2], help="Markov次数")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--outdir", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out"))
    args = ap.parse_args(argv)
    os.makedirs(args.outdir, exist_ok=True)

    print("[1/3] コーパス読込 & 統計学習 ...", flush=True)
    pieces = load_pieces()
    trans1, trans2, starts = learn(pieces)
    print(f"      {len(pieces)}曲 / 1次状態 {len(trans1)} / 2次状態 {len(trans2)} / 開始和音 {len(starts)}")

    print(f"[2/3] Markov生成 (order={args.order}) ...", flush=True)
    rng = random.Random(args.seed)
    n_chords = args.bars * args.chords_per_bar
    seq, fallback = generate(trans1, trans2, starts, n_chords, rng, args.order)
    uniq = len({tuple(c) for c in seq})
    if args.order >= 2:
        print(f"      {n_chords}和音 (ユニーク {uniq}) / 2次→1次フォールバック {fallback}/{n_chords-2} "
              f"({100*fallback/max(1,n_chords-2):.0f}%=2-gram未知率)")
    else:
        print(f"      {n_chords}和音 (ユニーク {uniq})")

    print("[3/3] 発音 ...", flush=True)
    L, R = render(seq, args.bpm, args.chords_per_bar, args.transpose, args.seed)
    wav = os.path.join(args.outdir, f"sweat_corpus_o{args.order}.wav")
    ins.write_stereo(L, R, wav, SR)
    with open(os.path.join(args.outdir, f"corpus_piece_o{args.order}.json"), "w") as f:
        json.dump([list(c) for c in seq], f)
    print(f"出力: {wav}  ({os.path.getsize(wav)/1024/1024:.1f} MB, stereo)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
