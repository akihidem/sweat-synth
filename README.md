# sweat-synth — 手汗で演奏する

手汗(EDA / 皮膚コンダクタンス)を音楽のコントロール信号にする楽器。
**不随意の発汗そのもの**を音にする ―― 「触れて鳴らす」のではなく「身体の状態が漏れて鳴る」。

```
手汗(EDA) ──▶ DSP ──▶ 音楽イベント ──▶ 音
  ↑緊張・呼吸・手の握り    正規化/ピーク検出   tonic→明るさ/音量
                                            SCRピーク→発音(ペンタトニック)
```

## いま動くもの(ハード不要)

実機(リング＋GSR＋XIAO)がまだ無くても、**手汗を合成して end-to-end で試奏**できる。

```bash
python3 sim/run.py
# → out/sweat_demo.wav  (耳で確認)
# → out/sweat_demo.mid  (Ableton/Logic 等でそのまま開ける“手汗の楽譜”)
```

オプション:
```bash
python3 sim/run.py --mode ambient                      # アンビエント(持続パッド+リバーブ)
python3 sim/run.py --mode granular --density 2.0       # 粒感MAX(音数を最大化)
python3 sim/run.py --mode sparse                       # SCRピークごとに1音(疎)
python3 sim/run.py --duration 30 --seed 7 --bpm 100    # 別の汗のかき方
python3 sim/run.py --no-audio                          # MIDIだけ高速生成
```

実行するとターミナルに ASCII で raw EDA / フィルタ後 / tonic / SCRピーク位置が出る。
**見る用デモ**(波形＋ピアノロール):
```bash
python3 sim/demo.py --mode ambient
python3 sim/demo.py --mode granular --density 2.0
```

### 🎼 制作エンジン: 手汗で奏でる「坂本龍一 × Kraftwerk × YMO」
```bash
python3 sim/studio.py                 # → out/sweat_studio.wav (ステレオ・約62秒)
python3 sim/studio.py --seed 7 --bpm 128 --bars 32
```
機械が正確に刻むバッキングの上で、**手汗が演奏する**一曲を制作する:

| 要素 | 由来 | 実装 |
|------|------|------|
| テンション和声(maj7/m9/6-9)+ FMベルのリード | 坂本龍一 | `instruments.add_fm` / `studio.PROG` |
| 16分アルペジオ・4つ打ち・レゾナント・ラダーフィルタ | Kraftwerk | `instruments.ladder` / motorik bass |
| 明るいペンタトニックの電子メロ・シンコペーション | YMO | `studio.MOTIF` |
| **tonic→フィルタ開度スイープ / SCRピーク→リード発火(16分quantize)** | 手汗 | 演奏レイヤー |

構成: intro(pad) → +rhythm(bass/drums) → full+lead → climax/outro。
ステレオ、ADSR、Moog風レゾナントフィルタ、テンポ同期ピンポンディレイ、Freeverbを全部 stdlib で実装。
`sim/instruments.py` が楽器/エフェクト、`sim/studio.py` が編曲。

### 3つの鳴らし方(シンプル版)
- **granular(既定)** … 覚醒度(tonic+phasic)に比例して粒を撒く。汗をかくほど密に・短く・高く、
  SCRの瞬間は強アクセント+オクターブの煌めき。`--density` で粒の量を調整(60秒で~2000粒)。
- **ambient** … 疎で長く重なり合う持続音。SCRは柔らかいswell(5〜9秒)、tonicは数秒ごとに動く
  パッド和音。Freeverb方式のリバーブ(`synth.reverb`)で広い空間に。
- **sparse** … SCRピークだけを点で鳴らす(発汗の“瞬間”を1音ずつ)。

## テスト
```bash
python3 tests/test_pipeline.py      # 素のpythonで7/7
pytest tests/                       # pytestでも可
```

## 構成

| パス | 中身 | 状態 |
|------|------|------|
| `sim/eda_model.py` | 手汗(EDA)信号の合成(tonic random walk + SCRイベント + ノイズ) | ✅ stdlib |
| `sim/dsp.py`       | 信号処理(ノイズLP→tonic抽出→正規化→SCRピーク検出) | ✅ stdlib |
| `sim/midi_map.py`  | 信号→音楽イベント写像 + 標準MIDIファイル書き出し | ✅ stdlib |
| `sim/synth.py`     | イベント→WAV レンダラ(ドローン+プラック) | ✅ stdlib |
| `sim/run.py`       | end-to-end デモ + ASCII可視化 | ✅ |
| `tests/`           | DSP/写像/MIDI の回帰テスト | ✅ 7/7 |
| `firmware/sweat_midi.ino` | XIAO nRF52840 用 BLE-MIDI ファーム(simと同一DSP) | ✅ 焼くだけ |
| `docs/bom.md`      | 部品表(~$25) | ✅ |
| `docs/wiring.md`   | 配線設計 | ✅ |

> **電子工作(はんだ付け・物理組み立て)は範囲外**。`firmware/` は実機ができたら焼くだけ、
> `docs/` はその設計記録。それ以外のソフトは全部この環境で動く。

## マッピング設計

発汗は遅い信号(数百ms〜秒オーダー)なので、正確な制御ではなく「身体状態の漏れ」として扱う:

| 汗の成分 | 取り出し方 | 音への割り当て |
|----------|-----------|----------------|
| ベースライン(tonic/SCL) | 超低域LP | ドローンの音量・フィルタ開度(CC7/CC74) |
| 急な発汗(phasic/SCR) | ピーク検出 | ペンタトニックの発音(Note On) |
| 個人差・温湿度のズレ | 適応レンジ正規化 | 絶対値でなく相対変化で鳴らす |

## 実機への道

1. ✅ シミュレータで DSP/マッピングを確定(済) ← `firmware/` は同じ式を移植済み
2. ⬜ `docs/bom.md` の部品を発注
3. ⬜ `docs/wiring.md` に従って組む(=電子工作。範囲外)
4. ⬜ `firmware/sweat_midi.ino` を XIAO に焼く → iPad/PC と BLE-MIDI ペアリング
5. ⬜ 音源(Ableton/Max/VCV)で CC74/CC7 とノートを受けて発音

実機で出る信号はシミュレータと同じ処理を通るので、ここで作り込んだ音作りがそのまま使える。
