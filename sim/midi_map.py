"""
midi_map.py — 処理済み手汗信号を「音楽イベント」に写像し、標準MIDIファイル(.mid)に書き出す。

写像の方針(発汗は遅い信号なので“正確な制御”でなく“身体状態の漏れ”として鳴らす):
    tonic_norm(0..1, 連続) → CC74(フィルタ開度) + CC7(音量)  … ドローンの明るさ/厚み
    phasic ピーク(SCR)      → ペンタトニックの Note On       … 緊張の瞬間=発音

.mid は pure stdlib でバイナリ生成。DAW(Ableton/Logic等)でそのまま開ける
=「あなたの手汗の楽譜」。
"""

from __future__ import annotations
import struct
from dataclasses import dataclass, field

# Cマイナー・ペンタトニック(暗め・東洋的)。好みで差し替え可。
PENTATONIC = [60, 63, 65, 67, 70, 72, 75, 77]  # C Eb F G Bb C Eb F

# 粒感(グラニュラー)用の広いプール: C3〜Bb6 の4オクターブ・ペンタトニック。
PENTATONIC_WIDE = [48 + 12 * o + d for o in range(4) for d in (0, 3, 5, 7, 10)]


@dataclass
class MapConfig:
    cc_channel: int = 0
    note_channel: int = 0
    cutoff_cc: int = 74
    volume_cc: int = 7
    cc_decimate: int = 4          # CCは制御レートを間引いて送る(MIDI詰まり防止)
    note_dur_sec: float = 0.9
    vel_min: int = 40
    vel_max: int = 120
    peak_to_vel: float = 250.0    # peak_amp(µS) → velocity スケール
    # --- グラニュラー(粒感)パラメータ ---
    grain_rate_min: float = 3.0   # tonic最小時の粒レート(粒/秒)
    grain_rate_max: float = 24.0  # tonic最大時の粒レート(粒/秒)
    phasic_drive: float = 45.0    # phasic(急な発汗)が粒密度を押し上げる量
    grain_dur_min: float = 0.06   # 高密度時の粒の長さ(秒)=短く締まる
    grain_dur_max: float = 0.22   # 低密度時の粒の長さ(秒)
    grain_vel_min: int = 32
    grain_vel_max: int = 112


@dataclass
class Events:
    """レンダリング用の構造化イベント。"""
    notes: list[tuple[float, int, int, float]] = field(default_factory=list)  # (t, note, vel, dur)
    cc: list[tuple[float, int, int]] = field(default_factory=list)            # (t, ctrl, val0-127)


def _emit_cc(ev: Events, samples, cfg: MapConfig):
    """tonic_norm を CC74(明るさ)/CC7(音量) として吐く(間引きあり)。"""
    for i, s in enumerate(samples):
        if i % cfg.cc_decimate == 0:
            v = int(round(s.tonic_norm * 127))
            v = 0 if v < 0 else 127 if v > 127 else v
            ev.cc.append((s.t, cfg.cutoff_cc, v))
            ev.cc.append((s.t, cfg.volume_cc, 30 + int(s.tonic_norm * 97)))  # 無音にしない床


def map_events(samples, cfg: MapConfig | None = None) -> Events:
    """疎(sparse)モード: SCRピークごとに1音。発汗の“瞬間”だけを点で鳴らす。"""
    cfg = cfg or MapConfig()
    ev = Events()
    _emit_cc(ev, samples, cfg)
    deg = 0
    for s in samples:
        if s.peak_amp is not None:
            note = PENTATONIC[deg % len(PENTATONIC)]
            deg += 1
            vel = cfg.vel_min + int(min(1.0, s.peak_amp * cfg.peak_to_vel / 127) *
                                    (cfg.vel_max - cfg.vel_min))
            vel = max(cfg.vel_min, min(cfg.vel_max, vel))
            ev.notes.append((s.t, note, vel, cfg.note_dur_sec))
    return ev


# 粒の動きを作るスケール内ステップ(同じ音の連打を避ける)
_GRAIN_PATTERN = [0, 2, 1, 3, 2, 4, 3, 1, 4, 0]


def map_events_granular(samples, cfg: MapConfig | None = None,
                        density: float = 1.0, fs: float | None = None) -> Events:
    """粒感(グラニュラー)モード: 覚醒度に比例して粒を撒く。

    粒レート = tonic(覚醒のベースライン) + phasic(急な発汗) で決まる。
    汗をかくほど密に・短く・高く、SCRピークでは強アクセント+オクターブの煌めき。
    density は全体の粒密度の倍率(>1 で MAX 寄り)。
    """
    cfg = cfg or MapConfig()
    ev = Events()
    _emit_cc(ev, samples, cfg)
    if fs is None:
        fs = (1.0 / (samples[1].t - samples[0].t)) if len(samples) > 1 else 32.0
    pool = PENTATONIC_WIDE
    span = cfg.grain_rate_max - cfg.grain_rate_min
    phase = 0.0
    k = 0
    for s in samples:
        rate = cfg.grain_rate_min + (s.tonic_norm ** 0.7) * span
        if s.phasic > 0:
            rate += s.phasic * cfg.phasic_drive
        rate *= density
        phase += rate / fs
        accent = s.peak_amp is not None
        while phase >= 1.0:
            phase -= 1.0
            center = int(s.tonic_norm * (len(pool) - 6))  # tonicで音域が上がる
            deg = max(0, min(len(pool) - 1, center + _GRAIN_PATTERN[k % len(_GRAIN_PATTERN)]))
            note = pool[deg]
            vel = cfg.grain_vel_min + int(s.tonic_norm * (cfg.grain_vel_max - cfg.grain_vel_min))
            if accent:
                vel = min(127, vel + 25)
            vel = max(1, min(127, vel))
            dur = cfg.grain_dur_max - s.tonic_norm * (cfg.grain_dur_max - cfg.grain_dur_min)
            ev.notes.append((s.t, note, vel, dur))
            if accent:  # SCRの瞬間はオクターブ上を薄く重ねて煌めかせる
                hi = pool[min(len(pool) - 1, deg + 5)]
                ev.notes.append((s.t, hi, vel, dur * 0.7))
            k += 1
    return ev


# ---------------- 標準MIDIファイル(SMF format 0) 書き出し ----------------

def map_events_ambient(samples, cfg: MapConfig | None = None,
                       fs: float | None = None, pad_period: float = 6.0) -> Events:
    """アンビエントモード: 疎で長く、重なり合う音。

    - SCRピーク → 柔らかい長音のswell(5〜9秒)。発汗の瞬間が霧のように立ち上がる。
    - tonic    → 数秒ごとに更新される持続パッド和音(根音+5度+オクターブ相当)。
                 覚醒度で音域と厚みがゆっくり動く。
    音数は数十。リバーブ(synth.reverb)前提の素材。
    """
    cfg = cfg or MapConfig()
    ev = Events()
    _emit_cc(ev, samples, cfg)
    if fs is None:
        fs = (1.0 / (samples[1].t - samples[0].t)) if len(samples) > 1 else 32.0
    pool = PENTATONIC_WIDE
    total = samples[-1].t if samples else 0.0

    # SCRピーク → 柔らかい長音swell
    deg = 0
    for s in samples:
        if s.peak_amp is not None:
            center = int(s.tonic_norm * (len(pool) - 4))
            note = pool[max(0, min(len(pool) - 1, center + (deg % 3)))]
            vel = 28 + int(s.tonic_norm * 45)        # 28〜73 と柔らかめ
            dur = 5.0 + s.tonic_norm * 4.0           # 5〜9秒
            ev.notes.append((s.t, note, vel, dur))
            deg += 1

    # tonic → 持続パッド和音(pad_period秒ごと、隣と重ねる)
    def tonic_at(t):
        idx = min(len(samples) - 1, max(0, int(t * fs)))
        return samples[idx].tonic_norm
    t = 0.0
    while t < total:
        tn = tonic_at(t)
        root = int(tn * (len(pool) - 6))
        chord = [pool[root], pool[min(len(pool) - 1, root + 2)],
                 pool[min(len(pool) - 1, root + 4)]]      # 開いた3声
        vel = 22 + int(tn * 30)                            # 22〜52 と静か
        for nt in chord:
            ev.notes.append((t, nt, vel, pad_period + 3.0))  # 次のパッドと重なる
        t += pad_period
    return ev


def _vlq(n: int) -> bytes:
    """MIDI可変長数値。"""
    if n == 0:
        return b"\x00"
    out = bytearray()
    out.append(n & 0x7F)
    n >>= 7
    while n:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(out))


def write_smf(ev: Events, path: str, ppq: int = 480, bpm: float = 90.0,
              cfg: MapConfig | None = None):
    cfg = cfg or MapConfig()
    sec_per_tick = 60.0 / (bpm * ppq)

    # 全イベントを (tick, priority, bytes) に展開して時刻順に
    raw: list[tuple[int, int, bytes]] = []
    for (t, ctrl, val) in ev.cc:
        tick = int(round(t / sec_per_tick))
        raw.append((tick, 1, bytes([0xB0 | cfg.cc_channel, ctrl, val])))
    for (t, note, vel, dur) in ev.notes:
        on = int(round(t / sec_per_tick))
        off = int(round((t + dur) / sec_per_tick))
        raw.append((on, 2, bytes([0x90 | cfg.note_channel, note, vel])))
        raw.append((off, 0, bytes([0x80 | cfg.note_channel, note, 0])))
    raw.sort(key=lambda x: (x[0], x[1]))

    track = bytearray()
    # テンポメタ
    mpqn = int(round(60_000_000 / bpm))
    track += _vlq(0) + b"\xFF\x51\x03" + mpqn.to_bytes(3, "big")
    prev = 0
    for (tick, _, msg) in raw:
        track += _vlq(tick - prev) + msg
        prev = tick
    track += _vlq(0) + b"\xFF\x2F\x00"  # End of Track

    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, ppq)
    chunk = b"MTrk" + struct.pack(">I", len(track)) + bytes(track)
    with open(path, "wb") as f:
        f.write(header + chunk)
    return path
