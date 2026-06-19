#!/usr/bin/env python3
"""make_receiver_pd.py — firmware(sweat_midi.ino)の BLE-MIDI を受けて鳴らす
Pure Data(vanilla)パッチ sweat_receiver.pd を生成する。

「焼いてすぐ鳴る」受信側プリセット: XIAO から飛ぶ Note + CC74/CC7 を
async/SOUL の声(非整数倍音ピアノ + ドローン + 簡易リバーブ)で鳴らす。
sim/async_synth.py の音作りを実時間・単音で近似したもの。

なぜジェネレータか: .pd は全オブジェクト(コメント含む)を宣言順で 0 始まりに
数え、connect がその番号を参照する。手書きは番号ずれ事故が起きやすいので
コードで番号を管理し、最後に全 connect の妥当性を検証する。

  python3 firmware/make_receiver_pd.py   # → firmware/sweat_receiver.pd

マッピング(firmware と一致):
  Note On(声部進行) → ピアノ発音(velocity=タッチ→明るさ+音量)
  CC74(tonic)       → ローパス開度(音色の base brightness)
  CC7 (tonic)       → マスター音量
依存: Pure Data vanilla のみ(外部ライブラリ不要)。
"""
from __future__ import annotations
import os

# ---- ネットリスト構築ヘルパ(宣言順=index) ----
nodes: list[str] = []
conns: list[tuple[int, int, int, int]] = []
# inlet/outlet 数を control 検証のため記録(best-effort)
io: dict[int, tuple[int, int]] = {}   # idx -> (n_inlets, n_outlets)


def add(text: str, x: int, y: int, ins: int, outs: int, kind: str = "obj") -> int:
    idx = len(nodes)
    nodes.append(f"#X {kind} {x} {y} {text};")
    io[idx] = (ins, outs)
    return idx


def comment(text: str, x: int, y: int) -> int:
    return add(text, x, y, 0, 0, kind="text")


def msg(text: str, x: int, y: int) -> int:
    return add(text, x, y, 1, 1, kind="msg")


def connect(a: int, outlet: int, b: int, inlet: int) -> None:
    conns.append((a, outlet, b, inlet))


# ---------------- パッチ本体 ----------------
comment("sweat-synth receiver — XIAO(SweatSynth)を BLE-MIDI で接続して開く", 20, 15)
comment("Note=声部進行 / CC74=明るさ / CC7=音量 / vanilla Pd のみ", 20, 33)

# --- MIDI 入力 ---
notein = add("notein", 30, 70, 0, 3)          # out: pitch, vel, ch
strip = add("stripnote", 30, 100, 2, 2)        # note-on のみ通す
connect(notein, 0, strip, 0)
connect(notein, 1, strip, 1)

# ピッチ → mtof、ベロシティ → 0..1
tpitch = add("t b f", 30, 130, 1, 2)           # 右→左発火: f(freq)を先に出し b(env)を後で
connect(strip, 0, tpitch, 0)
mtof = add("mtof", 30, 160, 1, 1)
connect(tpitch, 1, mtof, 0)                     # outlet1=f(右) → 先に周波数確定

vscale = add("/ 127", 150, 130, 2, 1)
connect(strip, 1, vscale, 0)
velsig = add("sig~", 150, 160, 1, 1)           # velocity を信号化(振幅/明るさ用)
connect(vscale, 0, velsig, 0)

# --- 非整数倍音ピアノ(基音 + 2.01倍 + 3.04倍 のベル的倍音) ---
m1 = add("* 1", 30, 195, 2, 1)
m2 = add("* 2.01", 110, 195, 2, 1)             # 僅かに非整数=金属的
m3 = add("* 3.04", 190, 195, 2, 1)
connect(mtof, 0, m1, 0)
connect(mtof, 0, m2, 0)
connect(mtof, 0, m3, 0)
o1 = add("osc~", 30, 225, 2, 1)
o2 = add("osc~", 110, 225, 2, 1)
o3 = add("osc~", 190, 225, 2, 1)
connect(m1, 0, o1, 0)
connect(m2, 0, o2, 0)
connect(m3, 0, o3, 0)
g2 = add("*~ 0.5", 110, 255, 2, 1)             # 上倍音は控えめ
g3 = add("*~ 0.28", 190, 255, 2, 1)
connect(o2, 0, g2, 0)
connect(o3, 0, g3, 0)
sum12 = add("+~", 30, 285, 2, 1)
connect(o1, 0, sum12, 0)
connect(g2, 0, sum12, 1)
tone = add("+~", 30, 312, 2, 1)
connect(sum12, 0, tone, 0)
connect(g3, 0, tone, 1)

# --- 打鍵エンベロープ(速い立上り→長い減衰) ---
envmsg = msg("1 5, 0 3500", 320, 160)          # 5ms で 1、3.5s で 0
connect(tpitch, 0, envmsg, 0)                  # outlet0=b(左) → 後で envelope 起動
env = add("vline~", 320, 195, 1, 1)
connect(envmsg, 0, env, 0)
enva = add("*~", 30, 342, 2, 1)                # tone * env
connect(tone, 0, enva, 0)
connect(env, 0, enva, 1)
velamp = add("*~", 30, 372, 2, 1)              # * velocity(タッチ→音量)
connect(enva, 0, velamp, 0)
connect(velsig, 0, velamp, 1)

# --- 明るさ: ローパス。base 開度=CC74、打鍵タッチでも開く ---
cc74 = add("ctlin 74", 470, 70, 0, 2)
cutmap = add("* 38", 470, 100, 2, 1)           # 0..127 → 0..4826
cutadd = add("+ 300", 470, 130, 2, 1)          # +300Hz 下駄 → 300..5126Hz
connect(cc74, 0, cutmap, 0)
connect(cutmap, 0, cutadd, 0)
lop = add("lop~ 1200", 30, 402, 2, 1)          # 既定1200Hzで開いた状態(CC74前でも鳴る)
connect(velamp, 0, lop, 0)
connect(cutadd, 0, lop, 1)                     # cutoff(Hz)

# --- 不協和ドローン(根音 + M2 + #11 クラスタ・低ゲイン) ---
d1 = add("osc~ 100", 600, 200, 2, 1)
d2 = add("osc~ 112.2", 600, 228, 2, 1)
d3 = add("osc~ 141.4", 600, 256, 2, 1)
dsum = add("+~", 600, 286, 2, 1)
dsum2 = add("+~", 600, 314, 2, 1)
connect(d1, 0, dsum, 0)
connect(d2, 0, dsum, 1)
connect(dsum, 0, dsum2, 0)
connect(d3, 0, dsum2, 1)
dgain = add("*~ 0.06", 600, 344, 2, 1)
connect(dsum2, 0, dgain, 0)

# --- ピアノ + ドローン ミックス ---
mix = add("+~", 30, 432, 2, 1)
connect(lop, 0, mix, 0)
connect(dgain, 0, mix, 1)

# --- 簡易リバーブ(vanilla: フィードバックディレイ) ---
rvwrite = add("delwrite~ swrev 130", 300, 432, 1, 0)   # buffer>read(90)で端の不安定を回避
rvread = add("delread~ swrev 90", 300, 462, 1, 1)
rvfb = add("*~ 0.32", 300, 492, 2, 1)          # フィードバック量
connect(mix, 0, rvwrite, 0)
connect(rvread, 0, rvfb, 0)
connect(rvfb, 0, rvwrite, 0)                   # フィードバックループ
wet = add("+~", 30, 462, 2, 1)                 # dry + wet
connect(mix, 0, wet, 0)
connect(rvread, 0, wet, 1)

# --- マスター音量(CC7) ---
cc7 = add("ctlin 7", 470, 170, 0, 2)
vol = add("/ 127", 470, 200, 2, 1)
volsig = add("sig~", 470, 230, 1, 1)
master = add("*~", 30, 492, 2, 1)
connect(cc7, 0, vol, 0)
connect(vol, 0, volsig, 0)
connect(wet, 0, master, 0)
connect(volsig, 0, master, 1)
out = add("*~ 0.6", 30, 522, 2, 1)             # 余裕(intimate)
connect(master, 0, out, 0)
dac = add("dac~", 30, 552, 2, 0)
connect(out, 0, dac, 0)
connect(out, 0, dac, 1)


# ---------------- 出力 + 検証 ----------------
def build() -> str:
    head = "#N canvas 60 60 820 640 12;\n"
    body = "\n".join(nodes)
    cs = "\n".join(f"#X connect {a} {o} {b} {i};" for (a, o, b, i) in conns)
    return head + body + "\n" + cs + "\n"


def validate() -> list[str]:
    errs = []
    n = len(nodes)
    for (a, o, b, i) in conns:
        if not (0 <= a < n) or not (0 <= b < n):
            errs.append(f"connect 範囲外: {a}->{b} (n={n})")
            continue
        ai_out = io[a][1]
        bi_in = io[b][0]
        if o >= ai_out:
            errs.append(f"src {a}({nodes[a]}) に outlet{o} は無い(outs={ai_out})")
        if i >= bi_in:
            errs.append(f"dst {b}({nodes[b]}) に inlet{i} は無い(ins={bi_in})")
    return errs


if __name__ == "__main__":
    errs = validate()
    here = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(here, "sweat_receiver.pd")
    if errs:
        print("検証NG:")
        for e in errs:
            print("  -", e)
        raise SystemExit(1)
    with open(out_path, "w") as f:
        f.write(build())
    print(f"OK: {len(nodes)} オブジェクト / {len(conns)} 結線 すべて妥当")
    print(f"出力: {out_path}")
