# sweat-synth — 手汗で演奏する

手汗(EDA / 皮膚コンダクタンス)を音楽のコントロール信号にする楽器。
**不随意の発汗そのもの**を音にする ―― 「触れて鳴らす」のではなく「身体の状態が漏れて鳴る」。

```
手汗(EDA) ──▶ DSP ──▶ 音楽イベント ──▶ 音
  ↑緊張・呼吸・手の握り    正規化/ピーク検出   tonic→明るさ/音量/音域
                                            SCRピーク→発音(声部進行・タッチ)
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

### 🧪 実験: 「音楽的評価」を外注する (`sim/llm_compose.py`)
手書きの進行/動機で“才能”を模倣しようとして失敗した反省から、**作曲と良し悪しの判断を
ローカルLLM(ollama gemma4)に外注**する実験。LLMが記号領域で compose→critique→revise し、
`instruments.py` が発音する(手汗は tonic→フィルタの演奏レイヤー)。
```bash
python3 sim/llm_compose.py --bars 16 --bpm 120 --rounds 2   # 要 ollama + gemma4
```
**知見**: 批評(尺度)の外注は機能する(指摘は鋭い)が、*良い音楽は出ない*。批評が和声停滞を
指摘→和音の多様性は上がるが旋律が痩せる、というモグラ叩きになり、批評は飽和して満足しない。
**壁は評価ではなく生成の天井**だった ―― 「診断できること ≠ 直せること」。
stdlib完結ではない唯一の部分(ollama依存)。

### 🌫 async/SOUL系アンビエント (`sim/async_synth.py`) ← 今の方向
「名人の才能」を追うのをやめ、~/ambient-tiktok の **SOUL.md(坂本龍一『async』血統)** の
方向に転換。密・グリッド・協和・無菌・盛り盛りを全部捨て、七原理に従う:
間と静寂が素材 / rubato(グリッドに乗せない) / 不協和の色(m2/M2/9th/#11) /
環境と一体(テープヒス・ルームトーン・wow&flutter) / 不完全と退色(プリペアド/非整数倍音ピアノ) /
過剰を削る(piano/drone/noiseの3層) / 沈思と喪失。手汗のSCRは疎なピアノの所作を引き起こす(間引き)。
ドローンは ambient.py の 1/Dスナップで完全ループ。依存: numpy, scipy。
```bash
python3 sim/async_synth.py --dur 64  --drone theta   # → out/sweat_async.wav (48kHz stereo)
python3 sim/async_synth.py --dur 180 --drone sub     # 長尺=楽章感(転調+多重スウェル)
# 音律(--drone): sub / delta / theta / alpha / gamma の5種
```
**音楽性の作り込み**(音を増やさず構造で／全てSOUL採否フィルタ内):
- **手汗→音楽の実配線** … 発汗(覚醒tonic)が音域を緩く牽引(相関+0.5)、ピーク強度が打鍵の明るさ(高域比1.5→8.9%)とアタックを駆動
- **声部進行** … 前音・目標域に近い候補を選び旋律の線に(平均跳躍 ~7→3半音)。調性重心に復元力をかけ漂流を防ぐ
- **狙った不協和** … ドローン根音とピアノ調性中心を結合(偶然のズレでなく色)
- **曲の形** … 入り→深まり→退きのフォーム弧＋中盤~62%でドローンが全音下降(沈思・喪失)。長尺(>120s)は重心がゆっくり転調し「楽章」感
- ドローン色音は完全ループ保持のまま超低速で明滅(和声が呼吸)
- **プリペアドピアノ** … ~12%の音は物を挟んだ短く金属的な所作(非整数倍音を強調・専用RNGで既存シードの音列は不変)
**評価問題の実用解**: 「良い/悪いは尺度化できない(最終判断は人間の耳)。だがSOULに照らして"違う"
ものは弾く」── 測れない品質の代わりに、SOUL採否チェックを**自動の否定フィルタ**として実装
(密度/静寂/不協和/ノイズ/レイヤー数を PASS/REJECT)。

### 🎹 コーパス統計で作曲 (`sim/corpus_compose.py`)
才能を“表面”でなく“過程”で借りる。公共ドメインの **J.S.Bachコラール382曲(SATB)** から
**和音→次の実voicing の遷移(Markov)を学習**し、Bachの声部進行(voice-leading)を継承したまま
組み替えて新しい進行を生む。坂本龍一自身がBach/Debussy由来なので電子音響で鳴らすと橋が架かる。
手汗は tonic→フィルタの演奏レイヤー。LLM不要・依存は numpy(pkl読込)のみ。
```bash
python3 sim/corpus_compose.py --order 2 --bpm 100 --transpose -3
# data/jsb_chorales.pkl を最初に取得(8.5MB, gitignore対象):
#   curl -L -o data/jsb_chorales.pkl \
#     https://raw.githubusercontent.com/czhuang/JSB-Chorales-dataset/master/jsb-chorales-16th.pkl
```
**Markov次数のトレードオフ**(実測, 48和音):

| order | ユニーク | 文脈の平均分岐 | 強制(=丸写し) | 性格 |
|---|---|---|---|---|
| 1 | 45/48 | 320 | 0% | 自由・新規性高/さまよいやすい |
| 2 | 48/48 | 32 | 9% | 局所が自然で脈絡あり/一部Bach丸写し |

gemma(LLM外注)が一番できなかった voice-leading が、ここでは**実在の名人の過程**として本物。
ただし長距離の楽曲構造(起承転結)はMarkovには無い ── これがこの手法の天井。

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
| ベースライン(tonic/SCL) | 超低域LP | ドローンの音量・フィルタ開度(CC7/CC74)・音域の牽引 |
| 急な発汗(phasic/SCR) | ピーク検出 | 声部進行で次の音を発音(Note On)、強度→velocity=タッチ |
| 個人差・温湿度のズレ | 適応レンジ正規化 | 絶対値でなく相対変化で鳴らす |

## 実機への道

1. ✅ シミュレータで DSP/マッピングを確定(済) ← `firmware/` は DSP も声部進行・タッチも同じ式を移植済み(起動ごとの種はA0ノイズから収穫)
2. ✅ `firmware/sweat_midi.ino` は実コンパイル検証済み(arduino-cli + adafruit:nrf52、16%使用)。BLE-MIDIはコア同梱の native BLEMidi
3. ⬜ `docs/bom.md` の部品を発注
4. ⬜ `docs/wiring.md` に従って組む(=電子工作。範囲外)
5. ⬜ `firmware/sweat_midi.ino` を XIAO に焼く → iPad/PC と BLE-MIDI ペアリング
6. ⬜ 音源で発音 ← `firmware/sweat_receiver.pd`(Pure Data, 焼いてすぐ鳴る)か任意の音源で CC74/CC7+Note を受ける

実機で出る信号はシミュレータと同じ処理を通るので、ここで作り込んだ音作りがそのまま使える。
