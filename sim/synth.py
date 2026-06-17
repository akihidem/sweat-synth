"""
synth.py — 構造化イベント(Events)を音声(WAV)にレンダリングする簡易シンセ。

stdlib のみ(wave/struct/math)。MIDI出力ハードやDAWが無くても「手汗の演奏」を
1本のWAVとして耳で確認できる。

音作り:
    ドローン … のこぎり波(加算合成)。CC74(tonic)で通す倍音数=明るさを制御、
               CC7(tonic)で音量。緊張が高まるほど明るく前に出る。
    ノート  … ペンタトニックのプラック(正弦+速い減衰)。SCRピークで発音。
"""

from __future__ import annotations
import math
import struct
import wave


def _midi_to_hz(note: int) -> float:
    return 440.0 * 2 ** ((note - 69) / 12.0)


# ---------------- リバーブ(Freeverb方式: 8コムフィルタ + 4オールパス) ----------------
class _Comb:
    def __init__(self, size: int, feedback: float, damp: float):
        self.buf = [0.0] * max(1, size)
        self.i = 0
        self.fb = feedback
        self.damp1 = damp
        self.damp2 = 1.0 - damp
        self.store = 0.0

    def process(self, x: float) -> float:
        out = self.buf[self.i]
        self.store = out * self.damp2 + self.store * self.damp1
        self.buf[self.i] = x + self.store * self.fb
        self.i += 1
        if self.i >= len(self.buf):
            self.i = 0
        return out


class _Allpass:
    def __init__(self, size: int, feedback: float):
        self.buf = [0.0] * max(1, size)
        self.i = 0
        self.fb = feedback

    def process(self, x: float) -> float:
        b = self.buf[self.i]
        out = -x + b
        self.buf[self.i] = x + b * self.fb
        self.i += 1
        if self.i >= len(self.buf):
            self.i = 0
        return out


def reverb(buf: list[float], sr: int = 44100, room: float = 0.86,
           damp: float = 0.22, wet: float = 0.55, dry: float = 0.5) -> list[float]:
    """広いアンビエント空間を付与。room=残響長, damp=高域減衰, wet/dry=混合。"""
    scale = sr / 44100.0
    combs = [_Comb(int(t * scale), room, damp)
             for t in (1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617)]
    aps = [_Allpass(int(t * scale), 0.5) for t in (556, 441, 341, 225)]
    out = [0.0] * len(buf)
    for n, x in enumerate(buf):
        xin = x * 0.015                      # freeverb fixed input gain
        s = 0.0
        for c in combs:
            s += c.process(xin)
        for a in aps:
            s = a.process(s)
        out[n] = x * dry + s * wet
    return out


def _control_timeline(cc, ctrl: int, n_audio: int, sr: int, default: float):
    """指定CCを音声サンプル長の 0..1 配列に展開(線形補間)。"""
    pts = [(t, v / 127.0) for (t, c, v) in cc if c == ctrl]
    tl = [default] * n_audio
    if not pts:
        return tl
    pts.sort()
    j = 0
    for i in range(n_audio):
        t = i / sr
        while j + 1 < len(pts) and pts[j + 1][0] <= t:
            j += 1
        if j + 1 < len(pts):
            (t0, v0), (t1, v1) = pts[j], pts[j + 1]
            f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            tl[i] = v0 + (v1 - v0) * max(0.0, min(1.0, f))
        else:
            tl[i] = pts[j][1]
    return tl


def render(ev, duration_sec: float, sr: int = 44100,
           drone_note: int = 36, voice: str = "pluck",
           drone_harm: int = 16, drone_gain: float = 1.0,
           tail_sec: float = 1.0) -> list[float]:
    """voice="pluck"(粒/疎)か "pad"(アンビエント=緩やかな膨らみと長い余韻)。"""
    n = int((duration_sec + tail_sec) * sr)
    buf = [0.0] * n

    cutoff = _control_timeline(ev.cc, 74, n, sr, 0.3)   # 明るさ 0..1
    volume = _control_timeline(ev.cc, 7, n, sr, 0.4)    # 音量 0..1

    # --- ドローン: のこぎり波(倍音加算)。cutoffで倍音数を可変。 ---
    f0 = _midi_to_hz(drone_note)
    f0b = _midi_to_hz(drone_note + 0.08)   # 微妙にデチューンした2基で厚み
    for i in range(n):
        t = i / sr
        nh = 1 + int(cutoff[i] * (drone_harm - 1))
        s = 0.0
        for h in range(1, nh + 1):
            amp = 1.0 / h                     # のこぎり波の倍音則
            s += amp * (math.sin(2 * math.pi * f0 * h * t)
                        + math.sin(2 * math.pi * f0b * h * t))
        # 倍音数で振幅が暴れるので正規化してから音量を当てる
        s *= 0.18 / max(1.0, math.log2(nh + 1) + 1)
        buf[i] += drone_gain * s * (0.25 + 0.75 * volume[i])

    # --- ノート ---
    for (t0, note, vel, dur) in ev.notes:
        f = _midi_to_hz(note)
        a = vel / 127.0
        start = int(t0 * sr)
        if voice == "pad":
            # アンビエント: ゆっくり膨らみ(swell)→長い指数余韻。倍音は柔らかく。
            atk = min(1.4, dur * 0.35)
            rel = 0.45
            length = int((dur + 3.5) * sr)
            for k in range(length):
                idx = start + k
                if idx >= n:
                    break
                tt = k / sr
                if tt < atk:
                    env = tt / atk
                else:
                    env = math.exp(-(tt - atk) * rel)
                trem = 0.85 + 0.15 * math.sin(2 * math.pi * 0.16 * tt + note)  # 緩い揺れ
                v = (math.sin(2 * math.pi * f * tt)
                     + 0.25 * math.sin(2 * math.pi * f * 2 * tt)
                     + 0.12 * math.sin(2 * math.pi * f * 3 * tt))
                buf[idx] += 0.16 * a * env * trem * v
        else:
            # 粒/疎: 正弦プラック。短い粒ほど速く減衰させて締める。
            decay = max(4.0, 1.1 / max(0.05, dur))
            length = int((dur + min(0.3, dur * 2.5)) * sr)
            for k in range(length):
                idx = start + k
                if idx >= n:
                    break
                tt = k / sr
                env = math.exp(-tt * decay)
                if tt < 0.004:
                    env *= tt / 0.004
                v = (math.sin(2 * math.pi * f * tt)
                     + 0.3 * math.sin(2 * math.pi * f * 2 * tt))
                buf[idx] += 0.22 * a * env * v
    return buf


def write_wav(buf: list[float], path: str, sr: int = 44100):
    # ソフトクリップしてからピーク正規化
    peak = max((abs(x) for x in buf), default=1.0) or 1.0
    g = 0.95 / peak if peak > 0.95 else 1.0
    frames = bytearray()
    for x in buf:
        y = math.tanh(x * g)                  # 軽いサチュレーション
        s = int(max(-1.0, min(1.0, y)) * 32767)
        frames += struct.pack("<h", s)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))
    return path
