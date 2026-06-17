"""
eda_model.py — 手汗(EDA/皮膚コンダクタンス)信号の合成モデル。

実機がまだ無いので、リアルな手汗っぽい信号を作ってパイプライン全体を
ハード無しで検証・試奏できるようにする。決定論的(seed指定)で再現可能。

構成:
    tonic(SCL)  … ゆっくりした random walk。緊張のベースライン(µS)。
    phasic(SCR) … 一過性の発汗反応。biexponential(速い立ち上がり/遅い減衰)。
                  「緊張した瞬間」のスクリプト + 自発SCR でトリガ。
    artifact    … 微小ノイズ + 速いリップル(LPで落ちることの確認用)。
"""

from __future__ import annotations
import math
import random
from dataclasses import dataclass, field


def scr_kernel(t: float, tau_rise: float = 0.7, tau_decay: float = 4.0) -> float:
    """SCR一発の波形(0..~1)。t<0 で 0。biexponential。"""
    if t < 0:
        return 0.0
    return math.exp(-t / tau_decay) - math.exp(-t / tau_rise)


# 正規化用に kernel のピーク値を求めておく
_T_PEAK = (0.7 * 4.0 / (4.0 - 0.7)) * math.log(4.0 / 0.7)
_PEAK_VAL = scr_kernel(_T_PEAK)


@dataclass
class EdaConfig:
    fs: float = 32.0
    duration_sec: float = 60.0
    seed: int = 42
    baseline: float = 6.0          # tonic初期値(µS)。手のひらは数µS〜十数µS。
    walk_sigma: float = 0.004      # tonic random walk の1ステップ標準偏差(µS)
    walk_pull: float = 0.0008      # baselineへ戻す力(平均回帰)
    spont_rate_hz: float = 0.05    # 自発SCRの発生率(回/秒)
    noise_sigma: float = 0.01      # 白色ノイズ(µS)
    ripple_amp: float = 0.03       # 速いリップル(µS, LP除去確認用)
    ripple_hz: float = 8.0
    # 「緊張イベント」スクリプト: (発生時刻sec, 強さµS)。手汗で演奏するときの“見せ場”。
    stimuli: list[tuple[float, float]] = field(default_factory=lambda: [
        (5.0, 0.45), (9.0, 0.30), (14.0, 0.6), (22.0, 0.5),
        (23.5, 0.35), (33.0, 0.7), (45.0, 0.4), (52.0, 0.55),
    ])


def generate(cfg: EdaConfig | None = None):
    """[(t, conductance_µS), ...] を返す。"""
    cfg = cfg or EdaConfig()
    rng = random.Random(cfg.seed)
    n = int(cfg.duration_sec * cfg.fs)
    dt = 1.0 / cfg.fs

    # SCRイベント(時刻, 振幅)を集める: スクリプト + 自発
    events: list[tuple[float, float]] = list(cfg.stimuli)
    p_spont = cfg.spont_rate_hz * dt
    for i in range(n):
        if rng.random() < p_spont:
            events.append((i * dt, rng.uniform(0.1, 0.35)))

    # tonic を random walk で生成
    tonic = [0.0] * n
    val = cfg.baseline
    for i in range(n):
        val += -cfg.walk_pull * (val - cfg.baseline) + rng.gauss(0, cfg.walk_sigma)
        tonic[i] = val

    # phasic を SCRカーネルの重ね合わせで生成
    out = []
    for i in range(n):
        t = i * dt
        phasic = 0.0
        for (te, amp) in events:
            dtau = t - te
            if 0 <= dtau <= 25.0:      # 25秒以上前のイベントは無視(減衰済み)
                phasic += amp * scr_kernel(dtau) / _PEAK_VAL
        ripple = cfg.ripple_amp * math.sin(2 * math.pi * cfg.ripple_hz * t)
        noise = rng.gauss(0, cfg.noise_sigma)
        cond = tonic[i] + phasic + ripple + noise
        out.append((t, max(0.0, cond)))
    return out


if __name__ == "__main__":
    data = generate()
    print(f"generated {len(data)} samples, "
          f"{data[-1][0]:.1f}s @ {EdaConfig().fs}Hz")
    print(f"range: {min(c for _, c in data):.2f}..{max(c for _, c in data):.2f} µS")
