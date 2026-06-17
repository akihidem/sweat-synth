"""
dsp.py — 手汗(EDA/GSR)信号の処理パイプライン。

ファーム(firmware/sweat_midi.ino)と「同じロジック」をサンプル単位の
オンライン処理として実装している。ここで挙動を詰めてから C++ に移植する。

信号の流れ:
    raw(µS) → ノイズ除去LP → tonic推定(超低域LP) → phasic = filt - tonic
            → tonic正規化(0..1, 適応レンジ) → phasicのピーク(SCR)検出

EDA の帯域:
    - SCL(tonic/緊張のベースライン) … ~0.05 Hz 以下のゆっくりした変動
    - SCR(phasic/一過性の発汗反応)  … 立ち上がり1〜3秒・減衰3〜5秒
    motion artifact や電源ハムはこれより速いので LP で潰す。
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field


class OnePole:
    """一次IIRローパス。 y += a*(x-y), a = dt/(RC+dt), RC = 1/(2*pi*fc)。"""

    def __init__(self, fs: float, fc: float, y0: float = 0.0):
        dt = 1.0 / fs
        rc = 1.0 / (2.0 * math.pi * fc)
        self.a = dt / (rc + dt)
        self.y = y0
        self._init = False

    def step(self, x: float) -> float:
        if not self._init:          # 最初のサンプルに張り付かせて立ち上がりを速くする
            self.y = x
            self._init = True
        else:
            self.y += self.a * (x - self.y)
        return self.y


class EnvelopeRange:
    """適応的な min/max 追従。tonic を 0..1 に正規化するためのレンジを推定。

    min は下にすぐ追従し上にゆっくり戻る。max はその逆。
    これで「今日・気温による絶対値のズレ」をキャンセルし、相対変化で鳴らす。
    """

    def __init__(self, fs: float, release_sec: float = 30.0, span_floor: float = 0.05):
        # release: レンジが縮む速さ(秒)。span_floor: 最小レンジ幅(µS)でゼロ割回避。
        self.up = math.exp(-1.0 / (release_sec * fs))
        self.span_floor = span_floor
        self.lo = None
        self.hi = None

    def step(self, x: float) -> tuple[float, float]:
        if self.lo is None:
            self.lo = self.hi = x
        # 速く張り付き、ゆっくり戻る
        self.lo = x if x < self.lo else self.lo * self.up + x * (1 - self.up)
        self.hi = x if x > self.hi else self.hi * self.up + x * (1 - self.up)
        if self.hi - self.lo < self.span_floor:
            mid = 0.5 * (self.hi + self.lo)
            self.lo, self.hi = mid - self.span_floor / 2, mid + self.span_floor / 2
        return self.lo, self.hi


@dataclass
class Sample:
    t: float
    raw: float
    filt: float
    tonic: float
    phasic: float
    tonic_norm: float          # 0..1
    peak_amp: float | None = None   # この時刻でSCRピーク確定なら振幅(µS)、無ければNone


@dataclass
class SweatProcessor:
    fs: float = 32.0
    noise_fc: float = 2.0          # ノイズ除去LPの遮断(Hz)
    tonic_fc: float = 0.05         # tonic抽出LPの遮断(Hz)
    peak_thresh: float = 0.03      # SCRとみなす phasic 閾値(µS)
    refractory_sec: float = 1.0    # ピーク連発防止(秒)

    def __post_init__(self):
        self._lp = OnePole(self.fs, self.noise_fc)
        self._tonic = OnePole(self.fs, self.tonic_fc)
        self._range = EnvelopeRange(self.fs)
        self._refractory = int(self.refractory_sec * self.fs)
        self._since_peak = self._refractory
        self._prev_phasic = 0.0
        self._rising = False
        self._n = 0

    def step(self, raw: float) -> Sample:
        t = self._n / self.fs
        self._n += 1
        self._since_peak += 1

        filt = self._lp.step(raw)
        tonic = self._tonic.step(filt)
        phasic = filt - tonic

        lo, hi = self._range.step(tonic)
        tonic_norm = (tonic - lo) / (hi - lo)
        tonic_norm = 0.0 if tonic_norm < 0 else 1.0 if tonic_norm > 1 else tonic_norm

        # --- SCRピーク検出: 閾値超え→上昇→下降に転じた点を山頂とする ---
        peak_amp = None
        if phasic > self.peak_thresh and phasic > self._prev_phasic:
            self._rising = True
        elif self._rising and phasic < self._prev_phasic:
            # 直前 _prev_phasic が山頂
            if self._since_peak >= self._refractory:
                peak_amp = self._prev_phasic
                self._since_peak = 0
            self._rising = False
        self._prev_phasic = phasic

        return Sample(t, raw, filt, tonic, phasic, tonic_norm, peak_amp)


def process(samples, fs: float = 32.0, **kw) -> list[Sample]:
    """(t, raw) または raw のイテラブルを丸ごと処理して Sample のリストを返す。"""
    proc = SweatProcessor(fs=fs, **kw)
    out = []
    for s in samples:
        raw = s[1] if isinstance(s, (tuple, list)) else s
        out.append(proc.step(raw))
    return out
