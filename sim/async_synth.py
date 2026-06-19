#!/usr/bin/env python3
"""
async_synth.py — 手汗(EDA)で奏でる「async/SOUL系」アンビエント。

~/ambient-tiktok の SOUL.md(坂本龍一『async』血統)と ambient.py の技法を参照。
これまでの密・グリッド・協和・無菌・盛り盛り路線を全部捨て、SOULの七原理に従う:
  間と静寂が素材 / グリッドに乗せない(rubato) / 不協和の色 / 環境と一体(ノイズ・ゆらぎ)
  / 不完全と退色(wow&flutter・detune・プリペアド) / 過剰を削る / 沈思と喪失。

手汗は"演奏"のまま: SCRピークが疎なピアノの所作を引き起こす(クオンタイズしない)。
評価は尺度化しない代わりに、SOULの採否チェックを自動の"否定フィルタ"として出す。

依存: numpy, scipy。  python3 sim/async_synth.py [--dur 64 --seed 42 --drone theta]
"""
from __future__ import annotations
import argparse
import os
import sys
import numpy as np
from scipy.io import wavfile
from scipy.signal import fftconvolve

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eda_model
import dsp

SR = 48000
RNG = np.random.default_rng(0)   # render時に seed 上書き

# 不協和寄りの色(根音からの半音): M2,m3,tritone,m7,M7,b9,9,#11
COLOR = [0, 2, 3, 6, 10, 11, 13, 14, 18]
# ドローン根音(Hz)。脳波/弛緩の連想で命名。低い sub〜高い gamma まで音律を選べる
DRONE = {"sub": 49.0, "delta": 68.05, "theta": 100.0, "alpha": 110.0, "gamma": 141.0}


def midi_hz(n):
    return 440.0 * 2 ** ((n - 69) / 12.0)


def _snap(freq, D):
    """周波数を 1/D の整数倍へ → 周期Dで完全ループ(ambient.py の心臓部)。"""
    return max(1, round(freq * D)) / D


# ---------- 退色: tape wow&flutter(ゆっくりした時間ドリフト, 秒) ----------
def make_drift(N, sr):
    t = np.arange(N) / sr
    slow = 0.0045 * (np.sin(2 * np.pi * 0.07 * t) + 0.5 * np.sin(2 * np.pi * 0.19 * t + 1.3))
    # 微小ブラウン運動を足してテープらしい不規則さ
    b = np.cumsum(RNG.standard_normal(N)) / sr
    b = b / (np.max(np.abs(b)) or 1) * 0.0015
    return slow + b


# ---------- プリペアド/減衰ピアノ(倍音は非整数=inharmonic, detune) ----------
def piano_note(f, dur, sr, drift_slice, decay=6.0, B=0.00045, bright=0.5, prepared=False):
    n = int(dur * sr)
    t = np.arange(n) / sr
    td = t + drift_slice[:n]                      # wow&flutter で時間を歪ませる
    f *= 1.0 + RNG.uniform(-0.004, 0.004)         # 一音ごとの調律ずれ
    if prepared:
        B = B * 8.0                               # プリペアド: 弦に物を挟んだ金属的な非整数倍音
    # 強く弾くほど上倍音が立つ(実ピアノのタッチ): 弱=暗い1.8 / 強=明るい1.0 のロールオフ
    rolloff = 1.8 - 0.8 * bright
    sig = np.zeros(n)
    for p in range(1, 9):
        fp = f * p * np.sqrt(1.0 + B * p * p)     # ピアノの非整数倍音
        sig += (1.0 / p ** rolloff) * np.sin(2 * np.pi * fp * td)
    env = np.exp(-t / decay)
    atk = int((0.012 - 0.009 * bright) * sr)       # 弱打鍵=柔らかく遅い / 強打鍵=速い立上り
    if atk:
        env[:atk] *= np.linspace(0, 1, atk)
    # フェルトの打鍵ノイズ(thunk): 強い打鍵ほど硬い音(短く鋭く)。プリペアドはミュート/バズで増す
    thunk_gain = (0.12 + 0.12 * bright) * (2.4 if prepared else 1.0)
    thunk = RNG.standard_normal(n) * np.exp(-t * (55 + 50 * bright)) * thunk_gain
    return sig * env + thunk * env


# ---------- ドローン: 不協和クラスタ + 完全ループ + 退色 ----------
def drone_bed(root_hz, D, sr, drift):
    N = int(round(D * sr))
    t = np.arange(N) / sr
    td = t + drift
    out = np.zeros(N)
    # 根音 + M2 + #11 のクラスタ(安心させない)。色音はゆっくり明滅し和声が呼吸する
    parts = [(1, 1.0, False), (1.122, 0.5, True), (1.414, 0.32, True),
             (2, 0.4, False), (3, 0.16, True)]
    for i, (mult, g, color) in enumerate(parts):
        f = _snap(root_hz * mult, D)
        f2 = _snap(root_hz * mult + 0.6 / D, D)   # 唸り
        partial = np.sin(2 * np.pi * f * td) + 0.6 * np.sin(2 * np.pi * f2 * td)
        if color:                                  # 色音だけ超低速で出入り(完全ループ保持)
            rate = _snap(0.018 * (i + 1), D)       # 1/D の整数倍 → 周期Dで閉じる
            glfo = 0.45 + 0.55 * (0.5 + 0.5 * np.sin(2 * np.pi * rate * t + i * 1.7))
            partial = partial * glfo
        out += g * partial
    lfo = 0.55 + 0.45 * np.sin(2 * np.pi * _snap(0.05, D) * t)  # 全体の呼吸
    out *= lfo
    return out / (np.max(np.abs(out)) or 1)


# ---------- 環境: テープヒス + ルームトーン ----------
def noise_floor(N, sr):
    white = RNG.standard_normal(N)
    hiss = white - np.concatenate([[0], white[:-1]])      # 簡易ハイパス=テープヒス
    brown = np.cumsum(RNG.standard_normal(N))
    brown = brown / (np.max(np.abs(brown)) or 1)          # ルームトーン(低域)
    return hiss * 0.006 + brown * 0.05


# ---------- コンボリューション・リバーブ(広い空間) ----------
def reverb_stereo(mono, sr, decay=3.6):
    n = int(decay * sr)
    env = np.exp(-np.arange(n) / (0.3 * decay * sr))
    irL = RNG.standard_normal(n) * env
    irR = RNG.standard_normal(n) * env
    wetL = fftconvolve(mono, irL)[:len(mono)]
    wetR = fftconvolve(mono, irR)[:len(mono)]
    m = max(np.max(np.abs(wetL)), np.max(np.abs(wetR))) or 1
    return wetL / m, wetR / m


def render(dur, seed, drone_name):
    global RNG
    RNG = np.random.default_rng(seed)
    # プリペアド判定用の専用RNG(主系列を乱さない=既存の音列/クラスタを保つ)
    prng = np.random.default_rng((int(seed) * 2654435761) & 0xFFFFFFFF)
    D = int(round(dur))
    N = D * SR
    drift = make_drift(N + 12 * SR, SR)            # ピアノ余韻ぶん長めに

    # 手汗(演奏レイヤー)
    fs = 32.0
    samples = dsp.process(eda_model.generate(
        eda_model.EdaConfig(fs=fs, duration_sec=D, seed=seed)), fs=fs)

    def tonic_at(t):
        return samples[min(len(samples) - 1, int(t * fs))].tonic_norm

    L = np.zeros(N + 12 * SR)
    R = np.zeros(N + 12 * SR)

    # --- ピアノ: SCRピークを間引いて疎に(rubato, グリッドに乗せない) ---
    # 調性の重心をドローン根音に合わせ、piano と drone を関係づける(狙った不協和)
    drone_hz = DRONE.get(drone_name, 100.0)
    home_pc = int(round(12 * np.log2(drone_hz / 440.0) + 69)) % 12
    home = 48 + home_pc                             # 重心(C3付近)
    root_pc = home
    prev_pitch = home                               # 声部進行の参照点
    notes = []
    last_t = -10.0
    min_gap = 3.2                                   # 静寂を恐れない
    for s in samples:
        if s.peak_amp is None or s.t - last_t < min_gap:
            continue
        last_t = s.t
        # 長尺(>120s)は重心自体をゆっくり転調させ「楽章」感を出す(短尺は不動)
        home_t = home + (int(round(3.0 * np.sin(2 * np.pi * s.t / D))) if D > 120 else 0)
        # 重心は揺れて"帰る": ランダムウォーク + 重心への復元力
        if RNG.random() < 0.18:
            root_pc += int(RNG.choice([-2, -1, 1, 2]))
            if abs(root_pc - home_t) > 5:           # 遠ざかり過ぎたら引き戻す
                root_pc += int(np.sign(home_t - root_pc))
        # 発汗(覚醒)が音域を緩く牽引 ─ 手汗の気配を残しつつ旋律の論理も生かす
        tn = tonic_at(s.t)
        target = home_t + int(round(tn * 12))       # tonic がレジスタを"そっと"動かす
        # 声部進行: color 度 × オクターブ候補から「前音と目標域に近い」線を選ぶ
        cands = [root_pc + deg + 12 * o for deg in COLOR for o in (-1, 0, 1, 2)]
        cands = [c for c in cands if 36 <= c <= 84]
        if RNG.random() < 0.20:                     # 20%: 跳躍で気配を変える
            note = int(RNG.choice(cands))
        else:                                       # 80%: 前音優先・目標域は弱い牽引=滑らかな線
            d = np.array([abs(c - prev_pitch) + 0.3 * abs(c - target) for c in cands], float)
            w = np.exp(-d / 4.0)
            note = int(RNG.choice(cands, p=w / w.sum()))
        prev_pitch = note
        touch = min(1.0, s.peak_amp * 4)            # 汗ピークの強さ=打鍵の強さ
        amp = 0.18 + 0.45 * touch
        # 12%: プリペアドピアノの所作(物を挟んだ短く金属的な減衰音)。
        # 専用prng で判定し主系列を乱さない(既存の音列/クラスタを保つ)
        prepared = prng.random() < 0.12
        r = RNG.random()                            # decay 用に従来通り1ドロー(系列維持)
        decay = (1.2 + 1.3 * r) if prepared else (5.0 + 6.0 * r)
        notes.append((s.t, note, amp, decay, touch, prepared))
        # 15%: 短2/長2度のクラスタ(不協和の色)。寄り添う音は柔らかく弾く
        if RNG.random() < 0.15:
            notes.append((s.t + RNG.uniform(0, 0.05), note + int(RNG.choice([1, 2])),
                          amp * 0.7, decay * 0.8, touch * 0.6, False))

    # 大局フォーム: 入り・退きは静かに、中盤で深まる弧(音は増やさず強弱で形を作る)
    # 長尺は複数の呼吸(楽章)を重ね、均質な平坦さを避ける
    def arc(t0):
        x = min(1.0, max(0.0, t0 / max(D, 1)))      # 0..1
        a = 0.55 + 0.45 * np.sin(np.pi * x)         # 全体の大アーチ
        if D > 120:
            a *= 0.75 + 0.25 * (0.5 + 0.5 * np.sin(2 * np.pi * (D / 75.0) * x))
        return a

    for (t0, note, amp, decay, touch, prepared) in notes:
        start = int(t0 * SR)
        amp *= arc(t0)
        sig = piano_note(midi_hz(note), decay, SR, drift[start:], decay=decay,
                         bright=touch, prepared=prepared)
        end = start + len(sig)
        pan = RNG.uniform(-0.4, 0.4)
        gl = amp * np.cos((pan + 1) * np.pi / 4)
        gr = amp * np.sin((pan + 1) * np.pi / 4)
        L[start:end] += sig * gl
        R[start:end] += sig * gr

    # --- ドローン: 中盤で全音下降する構造的な"沈み"(沈思・喪失) ---
    # 各ベッドは内部で完全ループ。クロスフェードで和声がひとつ動く
    bed_hi = drone_bed(drone_hz, D, SR, drift[:N])
    bed_lo = drone_bed(drone_hz * 2 ** (-2 / 12), D, SR, drift[:N])
    tsec = np.arange(N) / SR
    xf = 0.5 * (1.0 + np.tanh((tsec - 0.62 * D) / 3.5))   # ~62%で滑らかに hi→lo
    bed = (bed_hi * (1 - xf) + bed_lo * xf) * 0.16
    L[:N] += bed
    R[:N] += bed

    # --- 環境ノイズ ---
    nf = noise_floor(len(L), SR)
    L += nf
    R += nf * 0.93                                 # 左右で僅かに相関を崩す

    # --- 広いリバーブ(piano+drone のセンド) ---
    send = (L + R) * 0.5
    wetL, wetR = reverb_stereo(send, SR, decay=3.6)
    L += wetL * 0.5
    R += wetR * 0.5

    # ピーク正規化 -3dBFS(intimate に控えめ)
    peak = max(np.max(np.abs(L)), np.max(np.abs(R))) or 1.0
    g = 10 ** (-3 / 20) / peak
    return L * g, R * g, notes, nf, D


def soul_check(L, R, notes, nf, D):
    """SOULの採否チェックを自動の否定フィルタとして。良いは測れないが"違う"は弾く。"""
    mono = (L + R) * 0.5
    dens = len(notes) / D
    # 静寂率: 振幅エンベロープが床近くの時間割合
    win = SR // 10
    envv = np.sqrt(np.convolve(mono ** 2, np.ones(win) / win, "same"))
    silence = float(np.mean(envv < 0.02))
    pcs = sorted({n % 12 for (_, n, *_rest) in notes})
    has_dissonance = any(((b - a) % 12) in (1, 2) for a in pcs for b in pcs if a != b)
    noise_present = float(np.sqrt(np.mean(nf ** 2))) > 1e-4
    checks = [
        ("急いでいない(密度≤0.5/s)", dens <= 0.5, f"{dens:.2f}/s"),
        ("静寂がある(間≥15%)", silence >= 0.15, f"{silence*100:.0f}%"),
        ("不協和の色がある(m2/M2)", has_dissonance, "あり" if has_dissonance else "なし"),
        ("無菌でない(環境ノイズ)", noise_present, "あり" if noise_present else "なし"),
        ("レイヤー≤3(削れている)", True, "piano/drone/noise"),
    ]
    print("---- SOUL 採否チェック(自動否定フィルタ) ----")
    allok = True
    for name, ok, val in checks:
        allok &= ok
        print(f"  [{'PASS' if ok else 'REJECT'}] {name}: {val}")
    print(f"  → {'SOULに沿う' if allok else '★SOULに違反(弾くべき)'}")
    return allok


def main(argv=None):
    ap = argparse.ArgumentParser(description="手汗で奏でる async/SOUL系アンビエント")
    ap.add_argument("--dur", type=float, default=64.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--drone", default="theta", choices=list(DRONE))
    ap.add_argument("--outdir", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out"))
    args = ap.parse_args(argv)
    os.makedirs(args.outdir, exist_ok=True)

    print(f"[render] async/SOUL  dur={int(args.dur)}s seed={args.seed} drone={args.drone}", flush=True)
    L, R, notes, nf, D = render(args.dur, args.seed, args.drone)
    print(f"  ピアノの所作: {len(notes)} 音 / {D}s  (疎・rubato)")
    soul_check(L, R, notes, nf, D)

    stereo = np.stack([np.clip(L, -1, 1), np.clip(R, -1, 1)], axis=1)
    wav = os.path.join(args.outdir, "sweat_async.wav")
    wavfile.write(wav, SR, (stereo * 32767).astype(np.int16))
    print(f"出力: {wav}  ({os.path.getsize(wav)/1024/1024:.1f} MB, stereo {SR}Hz)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
