"""viz.py — ターミナル用ASCIIスパークライン。matplotlib不要。"""
from __future__ import annotations

_BLOCKS = " ▁▂▃▄▅▆▇█"


def sparkline(values, width: int = 72) -> str:
    if not values:
        return ""
    # widthへダウンサンプル(平均)
    n = len(values)
    if n > width:
        step = n / width
        buckets = []
        for i in range(width):
            a, b = int(i * step), int((i + 1) * step)
            seg = values[a:max(b, a + 1)]
            buckets.append(sum(seg) / len(seg))
        values = buckets
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    return "".join(_BLOCKS[min(8, int((v - lo) / span * 8))] for v in values)


def peak_lane(times, peak_times, total_sec: float, width: int = 72) -> str:
    """ピーク発生位置を下線で示すレーン。"""
    lane = [" "] * width
    for pt in peak_times:
        x = int(pt / total_sec * (width - 1))
        if 0 <= x < width:
            lane[x] = "▲"
    return "".join(lane)
