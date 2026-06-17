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


@dataclass
class Events:
    """レンダリング用の構造化イベント。"""
    notes: list[tuple[float, int, int, float]] = field(default_factory=list)  # (t, note, vel, dur)
    cc: list[tuple[float, int, int]] = field(default_factory=list)            # (t, ctrl, val0-127)


def map_events(samples, cfg: MapConfig | None = None) -> Events:
    cfg = cfg or MapConfig()
    ev = Events()
    deg = 0
    for i, s in enumerate(samples):
        if i % cfg.cc_decimate == 0:
            v = int(round(s.tonic_norm * 127))
            v = 0 if v < 0 else 127 if v > 127 else v
            ev.cc.append((s.t, cfg.cutoff_cc, v))
            ev.cc.append((s.t, cfg.volume_cc, 30 + int(s.tonic_norm * 97)))  # 無音にしない床
        if s.peak_amp is not None:
            note = PENTATONIC[deg % len(PENTATONIC)]
            deg += 1
            vel = cfg.vel_min + int(min(1.0, s.peak_amp * cfg.peak_to_vel / 127) *
                                    (cfg.vel_max - cfg.vel_min))
            vel = max(cfg.vel_min, min(cfg.vel_max, vel))
            ev.notes.append((s.t, note, vel, cfg.note_dur_sec))
    return ev


# ---------------- 標準MIDIファイル(SMF format 0) 書き出し ----------------

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
