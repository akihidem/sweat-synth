"""
instruments.py — 高品位レンダリング用の合成楽器/エフェクト(stdlibのみ)。

ウェーブテーブル(帯域制限加算合成を1度だけ生成→位相読みで安価)、ADSR、
レゾナント・ラダー(Moog風)フィルタ、FMベル、ドラム合成、テンポ同期ディレイ、
ステレオ書き出しを提供する。studio.py がこれを組んで一曲にする。
"""
from __future__ import annotations
import math
import struct
import wave

TWO_PI = 2.0 * math.pi
TABLE_LEN = 2048


def midi_hz(n: float) -> float:
    return 440.0 * 2 ** ((n - 69) / 12.0)


# ---------------- ウェーブテーブル(帯域制限) ----------------
def _build_saw(harm: int) -> list[float]:
    t = [0.0] * TABLE_LEN
    for i in range(TABLE_LEN):
        ph = i / TABLE_LEN
        s = 0.0
        for h in range(1, harm + 1):
            s += math.sin(TWO_PI * h * ph) / h
        t[i] = s * (2.0 / math.pi)
    return t


def _build_square(harm: int) -> list[float]:
    t = [0.0] * TABLE_LEN
    for i in range(TABLE_LEN):
        ph = i / TABLE_LEN
        s = 0.0
        for h in range(1, harm + 1, 2):
            s += math.sin(TWO_PI * h * ph) / h
        t[i] = s * (4.0 / math.pi)
    return t


SAW = _build_saw(40)
SQR = _build_square(40)


def _osc(table: list[float], phase: float) -> float:
    x = phase * TABLE_LEN
    i = int(x)
    frac = x - i
    a = table[i & (TABLE_LEN - 1)]
    b = table[(i + 1) & (TABLE_LEN - 1)]
    return a + (b - a) * frac


# ---------------- 包絡(ADSR) ----------------
def adsr(t: float, dur: float, a: float, d: float, s: float, r: float) -> float:
    if t < 0:
        return 0.0
    if t < a:
        return t / a if a > 0 else 1.0
    if t < a + d:
        return 1.0 - (1.0 - s) * ((t - a) / d) if d > 0 else s
    if t < dur:
        return s
    rt = t - dur
    if rt < r:
        return s * (1.0 - rt / r) if r > 0 else 0.0
    return 0.0


# ---------------- 声部レンダ(テーブル発振+デチューン) ----------------
def add_voice(buf, sr, t0, dur, note, vel, table=SAW, env=(0.005, 0.1, 0.7, 0.2),
              detune_cents=0.0, unison=1, gain=1.0):
    f = midi_hz(note)
    a = (vel / 127.0) * gain
    atk, dec, sus, rel = env
    start = int(t0 * sr)
    length = int((dur + rel) * sr)
    if unison > 1:
        incs = [midi_hz(note + (u - (unison - 1) / 2) * detune_cents / 100.0) / sr
                for u in range(unison)]
    else:
        incs = [f / sr]
    phases = [(_idx * 0.13) % 1.0 for _idx in range(len(incs))]  # 位相をばらす
    norm = a / len(incs)
    for k in range(length):
        idx = start + k
        if idx >= len(buf):
            break
        tt = k / sr
        e = adsr(tt, dur, atk, dec, sus, rel)
        if e <= 0.0 and tt > dur:
            break
        s = 0.0
        for v in range(len(incs)):
            s += _osc(table, phases[v])
            phases[v] += incs[v]
            if phases[v] >= 1.0:
                phases[v] -= 1.0
        buf[idx] += norm * e * s


def add_fm(buf, sr, t0, dur, note, vel, ratio=2.0, index=4.0,
           env=(0.004, 0.4, 0.3, 0.6), gain=1.0):
    """FMベル/エレピ(坂本龍一/YMO的)。indexは時間で減衰しベルらしくする。"""
    f = midi_hz(note)
    a = (vel / 127.0) * gain
    atk, dec, sus, rel = env
    start = int(t0 * sr)
    length = int((dur + rel) * sr)
    for k in range(length):
        idx = start + k
        if idx >= len(buf):
            break
        tt = k / sr
        e = adsr(tt, dur, atk, dec, sus, rel)
        if e <= 0.0 and tt > dur:
            break
        eidx = index * math.exp(-tt * 2.2)
        mod = math.sin(TWO_PI * f * ratio * tt)
        s = math.sin(TWO_PI * f * tt + eidx * mod)
        buf[idx] += a * e * s


# ---------------- レゾナント・ラダーフィルタ(Moog風 4極) ----------------
def ladder(buf, sr, ctrl, ctrl_rate, lo_hz, hi_hz, res=1.5, drive=1.0, curve=1.6):
    """ctrl(0..1, ctrl_rate Hz)で遮断周波数を lo→hi 指数スイープ。res:0..4。"""
    y1 = y2 = y3 = y4 = 0.0
    out = [0.0] * len(buf)
    ratio = hi_hz / lo_hz
    nctrl = len(ctrl)
    for n in range(len(buf)):
        ci = int(n / sr * ctrl_rate)
        v = ctrl[ci] if ci < nctrl else ctrl[-1] if nctrl else 0.5
        fc = lo_hz * (ratio ** (v ** curve))
        f = 1.0 - math.exp(-TWO_PI * fc / sr)
        inp = buf[n] * drive - res * y4
        y1 += f * (inp - y1)
        y2 += f * (y1 - y2)
        y3 += f * (y2 - y3)
        y4 += f * (y3 - y4)
        out[n] = y4
    return out


# ---------------- ドラム合成 ----------------
def add_kick(buf, sr, t0, vel=1.0):
    start = int(t0 * sr)
    length = int(0.4 * sr)
    ph = 0.0
    for k in range(length):
        idx = start + k
        if idx >= len(buf):
            break
        tt = k / sr
        pitch = 48.0 + 90.0 * math.exp(-tt * 42.0)   # ピッチ落とし
        ph += pitch / sr
        env = math.exp(-tt * 6.5)
        s = math.sin(TWO_PI * ph)
        if tt < 0.006:
            s += 0.8 * math.exp(-tt * 900.0)         # アタックのクリック
        buf[idx] += vel * env * s * 0.95


def add_snare(buf, sr, t0, vel, rng):
    start = int(t0 * sr)
    length = int(0.22 * sr)
    for k in range(length):
        idx = start + k
        if idx >= len(buf):
            break
        tt = k / sr
        env = math.exp(-tt * 24.0)
        tone = math.sin(TWO_PI * 185.0 * tt) * math.exp(-tt * 32.0)
        noise = rng.random() * 2.0 - 1.0
        buf[idx] += vel * (0.6 * noise * env + 0.4 * tone) * 0.5


def add_hat(buf, sr, t0, vel, rng, open_=False):
    start = int(t0 * sr)
    length = int((0.14 if open_ else 0.05) * sr)
    prev = 0.0
    decay = 16.0 if open_ else 60.0
    for k in range(length):
        idx = start + k
        if idx >= len(buf):
            break
        tt = k / sr
        n = rng.random() * 2.0 - 1.0
        hp = n - prev                                # 簡易ハイパス
        prev = n
        buf[idx] += vel * hp * math.exp(-tt * decay) * 0.3


# ---------------- エフェクト/ミックス ----------------
def stereo_delay(L, R, sr, dt, fb=0.4, wet=0.3):
    """テンポ同期ピンポン・ディレイ(クロスフィードバック)。"""
    d = int(dt * sr)
    for n in range(d, len(L)):
        L[n] += wet * R[n - d] * fb
        R[n] += wet * L[n - d] * fb


def mix_in(L, R, buf, gain=1.0, pan=0.0):
    """pan: -1(左)..0(中)..+1(右) を等パワーで。"""
    gl = gain * math.cos((pan + 1.0) * math.pi / 4.0)
    gr = gain * math.sin((pan + 1.0) * math.pi / 4.0)
    for i in range(len(buf)):
        L[i] += buf[i] * gl
        R[i] += buf[i] * gr


def write_stereo(L, R, path, sr=44100):
    peak = max(max((abs(x) for x in L), default=1.0),
               max((abs(x) for x in R), default=1.0)) or 1.0
    g = 0.89 / peak if peak > 0.89 else 1.0
    frames = bytearray()
    for i in range(len(L)):
        yl = math.tanh(L[i] * g)
        yr = math.tanh(R[i] * g)
        frames += struct.pack("<hh",
                              int(max(-1.0, min(1.0, yl)) * 32767),
                              int(max(-1.0, min(1.0, yr)) * 32767))
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))
    return path
