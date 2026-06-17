#!/usr/bin/env python3
"""
llm_compose.py — 「音楽的評価」を外注する作曲パイプライン。

前回の失敗(ベタ書きの進行/動機=才能の模倣)の反省から、作曲と"良し悪しの判断"を
ローカルLLM(ollama gemma4)に委ねる。LLMが記号領域(和音/旋律)で:
    compose  → 一曲を生成
    critique → 自分の作を辛口批評(=コードに書けなかった尺度)
    revise   → 批評に基づき改稿
してから、我々の合成エンジン(instruments.py)で発音する。
手汗(EDA)は tonic→フィルタ開度スイープの"演奏レイヤー"として上に乗せる。

    python3 sim/llm_compose.py
    python3 sim/llm_compose.py --bars 16 --bpm 120 --rounds 1 --model gemma4:latest
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eda_model
import dsp
import synth
import instruments as ins

SR = 44100
OLLAMA = "http://localhost:11434/api/generate"

SCHEMA_HINT = """Return ONLY JSON, no prose. Schema:
{
 "title": string,
 "bpm": int,
 "bars": int,
 "key": string,
 "chords": [ {"bar": int, "notes": [int,...]} ],   // absolute MIDI, 3-5 note extended voicings, one or more per bar, cover every bar 0..bars-1
 "bass":   [ {"beat": float, "note": int, "dur": float} ],   // beat = absolute beats from start (0..bars*4)
 "melody": [ {"beat": float, "note": int, "dur": float, "vel": int} ]  // leave gaps (rests); shape a real phrase
}
MIDI 36..84. Keep it musical: voice-lead the chords, give the melody contour and breath, use tension and release."""


def _loose_json(s):
    """不正/末尾ゴミ付きJSONから最初の完全なオブジェクトを救出。失敗時 None。"""
    s = s.strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    instr = esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            instr = not instr
        elif not instr:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start:i + 1])
                    except Exception:
                        return None
    return None   # 途中で切れている(不均衡)


def ollama_json(prompt, system="", model="gemma4:latest", temperature=0.8,
                timeout=300, retries=3):
    """壊れたJSONはリトライ→loose救出。全滅したら None を返す(呼び出し側で前版維持)。"""
    for attempt in range(retries):
        body = json.dumps({
            "model": model, "prompt": prompt, "system": system,
            "format": "json", "stream": False,
            "options": {"temperature": temperature + 0.05 * attempt},
        }).encode()
        req = urllib.request.Request(OLLAMA, body, {"Content-Type": "application/json"})
        resp = json.loads(urllib.request.urlopen(req, timeout=timeout).read())["response"]
        obj = _loose_json(resp)
        if obj is not None:
            return obj
        print(f"      (JSON parse失敗 retry {attempt+1}/{retries})", flush=True)
    return None


def ollama_text(prompt, system="", model="gemma4:latest", temperature=0.7, timeout=300):
    body = json.dumps({
        "model": model, "prompt": prompt, "system": system, "stream": False,
        "options": {"temperature": temperature},
    }).encode()
    req = urllib.request.Request(OLLAMA, body, {"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())["response"]


STYLE = ("Fuse three sensibilities: Ryuichi Sakamoto (lush extended harmony — maj7/m9/6-9, "
         "voice leading, melancholy, space and restraint), Kraftwerk (steady motorik pulse, "
         "hypnotic repetition), and YMO (bright pentatonic electronic melody, playful syncopation). "
         "Avoid clichés and avoid a static looping melody — develop it.")


def compose(bpm, bars, model, temperature=0.85):
    sys_p = "You are a world-class composer and arranger. " + STYLE
    prompt = (f"Compose an original instrumental piece. {bars} bars, {bpm} BPM, 4/4. "
              f"Pick a key with emotional pull. Make the harmony move (no single-chord vamp) and "
              f"the melody breathe and develop across the bars.\n\n{SCHEMA_HINT}")
    return ollama_json(prompt, sys_p, model, temperature)


def critique(piece, model):
    sys_p = ("You are a ruthless but constructive music critic with the ears of Sakamoto. "
             "Judge harmony, voice leading, melodic shape/development, tension-release, rhythm, "
             "and originality. Be specific and brief.")
    prompt = ("Critique this piece. List its 3 worst weaknesses as bullet points, then one line "
              "on what would make it genuinely good.\n\n" + json.dumps(piece, ensure_ascii=False))
    return ollama_text(prompt, sys_p, model, 0.5)


def revise(piece, crit, bpm, bars, model):
    sys_p = "You are a world-class composer revising your draft. " + STYLE
    prompt = ("Here is your draft and a critic's notes. Rewrite the piece to fix every weakness — "
              "stronger voice leading, a melody that develops (not a loop), real tension and release.\n\n"
              f"DRAFT:\n{json.dumps(piece, ensure_ascii=False)}\n\nCRITIC:\n{crit}\n\n{SCHEMA_HINT}")
    return ollama_json(prompt, sys_p, model, 0.6)


# ---------------- サニタイズ ----------------
def _clampnote(n):
    try:
        n = int(round(n))
    except Exception:
        return None
    return max(24, min(96, n))


def sanitize(piece, bpm, bars):
    out = {"title": str(piece.get("title", "untitled"))[:80],
           "bpm": int(piece.get("bpm", bpm) or bpm),
           "bars": int(piece.get("bars", bars) or bars),
           "key": str(piece.get("key", "?"))[:24]}
    # chords: bar -> notes
    by_bar = {}
    for c in piece.get("chords", []) or []:
        try:
            b = int(c.get("bar", 0))
        except Exception:
            continue
        notes = [x for x in (_clampnote(n) for n in c.get("notes", [])) if x is not None]
        if notes:
            by_bar[b] = sorted(set(notes))[:5]
    # 穴埋め(直前のコードを継続)
    chords = []
    last = [60, 64, 67, 71]
    for b in range(out["bars"]):
        last = by_bar.get(b, last)
        chords.append({"bar": b, "notes": last})
    out["chords"] = chords

    def clean_seq(seq, with_vel):
        res = []
        for e in seq or []:
            n = _clampnote(e.get("note"))
            if n is None:
                continue
            try:
                beat = float(e.get("beat", 0.0))
                dur = float(e.get("dur", 0.5))
            except Exception:
                continue
            if dur <= 0 or beat < 0 or beat > out["bars"] * 4 + 4:
                continue
            item = {"beat": beat, "note": n, "dur": min(dur, 8.0)}
            if with_vel:
                v = e.get("vel", 90)
                try:
                    item["vel"] = max(20, min(127, int(v)))
                except Exception:
                    item["vel"] = 90
            res.append(item)
        return sorted(res, key=lambda x: x["beat"])

    out["bass"] = clean_seq(piece.get("bass"), False)
    out["melody"] = clean_seq(piece.get("melody"), True)
    return out


# ---------------- 発音(instruments.pyで) ----------------
def render(piece, seed=42):
    bpm = piece["bpm"]
    bars = piece["bars"]
    beat = 60.0 / bpm
    bar = 4.0 * beat
    six = beat / 4.0
    total = bars * bar
    N = int((total + 3.5) * SR)

    # 手汗(演奏レイヤー)
    fs = 32.0
    samples = dsp.process(eda_model.generate(
        eda_model.EdaConfig(fs=fs, duration_sec=total, seed=seed)), fs=fs)
    tonic_ctrl = [s.tonic_norm for s in samples]

    pad = [0.0] * N
    arp = [0.0] * N
    bass = [0.0] * N
    lead = [0.0] * N
    drums = [0.0] * N

    # PAD: コードを小節保持(supersaw)
    chord_by_bar = {c["bar"]: c["notes"] for c in piece["chords"]}
    for b in range(bars):
        notes = chord_by_bar.get(b, [60, 64, 67])
        for nt in notes:
            ins.add_voice(pad, SR, b * bar, bar * 0.98, nt, 78, table=ins.SAW,
                          env=(0.4, 0.4, 0.85, 1.5), detune_cents=11, unison=3, gain=0.42)

    # ARP: コード構成音を16分でアルペジオ(Kraftwerk)
    pat = [0, 1, 2, 3, 2, 1]
    step = 0
    nsix = int(round(bar / six))
    for b in range(bars):
        pool = sorted(set(chord_by_bar.get(b, [60, 64, 67])))
        pool = pool + [pool[0] + 12]
        for s in range(nsix):
            t0 = b * bar + s * six
            nt = pool[pat[step % len(pat)] % len(pool)] + 12
            step += 1
            ins.add_voice(arp, SR, t0, six * 0.9, nt, 92, table=ins.SAW,
                          env=(0.002, 0.07, 0.0, 0.05), gain=0.26)

    # BASS / MELODY: LLMの記号をそのまま時間に
    for e in piece["bass"]:
        ins.add_voice(bass, SR, e["beat"] * beat, e["dur"] * beat, e["note"], 110,
                      table=ins.SQR, env=(0.004, 0.12, 0.6, 0.1), gain=0.7)
    for e in piece["melody"]:
        ins.add_fm(lead, SR, e["beat"] * beat, e["dur"] * beat, e["note"], e["vel"],
                   ratio=2.0, index=3.2, env=(0.004, 0.35, 0.4, 0.6), gain=0.85)

    # DRUMS: 中盤のみ4つ打ち
    import random
    rng = random.Random(seed)
    d0, d1 = bars // 4, bars * 3 // 4
    for b in range(d0, d1):
        for bi in range(4):
            tb = b * bar + bi * beat
            ins.add_kick(drums, SR, tb, 0.95)
            ins.add_hat(drums, SR, tb + beat / 2, 0.5, rng)
            if bi in (1, 3):
                ins.add_snare(drums, SR, tb, 0.8, rng)

    # 手汗→フィルタスイープ
    arp = ins.ladder(arp, SR, tonic_ctrl, fs, 320, 7000, res=2.6, curve=1.5)
    pad = ins.ladder(pad, SR, tonic_ctrl, fs, 600, 4200, res=1.1, curve=1.4)
    bass = ins.ladder(bass, SR, tonic_ctrl, fs, 220, 1100, res=1.4, curve=1.2)

    L = [0.0] * N
    R = [0.0] * N
    ins.mix_in(L, R, pad, 0.55, 0.0)
    ins.mix_in(L, R, arp, 0.45, 0.28)
    ins.mix_in(L, R, bass, 0.8, 0.0)
    ins.mix_in(L, R, lead, 0.7, -0.12)
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=16)
    ap.add_argument("--bpm", type=int, default=120)
    ap.add_argument("--rounds", type=int, default=1, help="批評→改稿の回数")
    ap.add_argument("--model", default="gemma4:latest")
    ap.add_argument("--seed", type=int, default=42, help="手汗(演奏レイヤー)のseed")
    ap.add_argument("--outdir", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out"))
    args = ap.parse_args(argv)
    os.makedirs(args.outdir, exist_ok=True)

    print(f"[1/?] compose ({args.model}) ...", flush=True)
    raw = compose(args.bpm, args.bars, args.model)
    if raw is None:
        print("compose が有効なJSONを返しませんでした。中止。")
        return 1
    piece = sanitize(raw, args.bpm, args.bars)
    print(f"      → '{piece['title']}'  key={piece['key']} "
          f"chords={len(piece['chords'])} melody={len(piece['melody'])} bass={len(piece['bass'])}")

    for i in range(args.rounds):
        print(f"[round {i+1}] critique ...", flush=True)
        crit = critique(piece, args.model)
        print("---- 外注した評価(LLMの批評) ----")
        print(crit.strip()[:1200])
        print("---------------------------------")
        print(f"[round {i+1}] revise ...", flush=True)
        raw = revise(piece, crit, args.bpm, args.bars, args.model)
        if raw is None:
            print("      改稿のJSONが壊れていたので前の版を維持して続行")
            continue
        piece = sanitize(raw, args.bpm, args.bars)
        print(f"      改稿 → chords={len(piece['chords'])} "
              f"melody={len(piece['melody'])} bass={len(piece['bass'])}")

    # 記号も保存(再現/検証用)
    with open(os.path.join(args.outdir, "llm_piece.json"), "w") as f:
        json.dump(piece, f, ensure_ascii=False, indent=2)

    print("[render] instruments.py で発音 ...", flush=True)
    L, R = render(piece, seed=args.seed)
    wav = os.path.join(args.outdir, "sweat_llm.wav")
    ins.write_stereo(L, R, wav, SR)
    print(f"出力: {wav}  ({os.path.getsize(wav)/1024/1024:.1f} MB, stereo)")
    print(f"記号: {os.path.join(args.outdir, 'llm_piece.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
