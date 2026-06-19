# sweat_receiver.pd — 焼いてすぐ鳴る受信音源

`sweat_midi.ino` を焼いた XIAO が飛ばす BLE-MIDI を受けて、その場で
**async/SOUL の声**(非整数倍音ピアノ + 不協和ドローン + 簡易リバーブ)で鳴らす
Pure Data(vanilla)パッチ。受信側の音源を別途用意しなくても音が出る初期プリセット。

## 使い方

1. [Pure Data](https://puredata.info/)(vanilla)をインストール。
2. XIAO に `sweat_midi.ino` を焼き、OS の Bluetooth/MIDI 設定で **"SweatSynth"** をペアリング。
   - macOS: *Audio MIDI 設定 → Bluetooth* から接続。
   - iOS: BLE-MIDI 対応アプリ(例: midimittr)で接続し、Pd mobile / AUM 等へ。
3. Pd で `sweat_receiver.pd` を開く。
4. Pd の *Media → MIDI Settings* で入力に SweatSynth(または仲介ポート)を選ぶ。
5. 右上の **DSP を ON**(Media → DSP On / Ctrl+/)。手汗で SCR ピークが出るたびに発音する。

## マッピング(firmware と一致)

| MIDI | 由来(firmware) | 受信側の作用 |
|------|----------------|--------------|
| Note On | 声部進行で選ばれた音 | ピアノ発音。pitch→mtof→非整数倍音(基音/2.01/3.04) |
| velocity | 汗ピークの強さ=タッチ | 音量 + ローパス開度(強いほど明るい) |
| CC74 | tonic(覚醒ベースライン) | ローパスの base カットオフ(音色の明るさ) |
| CC7 | tonic | マスター音量 |

## これは「出発点のプリセット」

実時間・単音で `sim/async_synth.py` の声を**近似**したもの(完全再現ではない)。
本物の余韻・退色・空間が欲しければ、受信を任意の音源(ピアノ音色 + リバーブ)に
差し替えてもよい。CC74→フィルタ / CC7→音量 / velocity→タッチ は GM 的な慣習に
沿っているので、多くのソフト音源にそのまま刺さる。

## 再生成

パッチは手書きでなく `make_receiver_pd.py` が生成する(結線番号のずれ事故を避け、
全 connect の妥当性を自動検証している)。音作りを変えるときは生成器を編集して:

```bash
python3 firmware/make_receiver_pd.py   # → firmware/sweat_receiver.pd を再生成
```
