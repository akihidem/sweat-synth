/*
 * sweat_midi.ino — 手汗(EDA/GSR)→ BLE-MIDI コントローラ
 * ターゲット: Seeed XIAO nRF52840 (Sense でなくとも可)
 *
 * このファームは sim/dsp.py と「同じ信号処理」を float でオンライン実装する。
 * シミュレータで詰めた挙動(ノイズLP→tonic抽出→正規化→SCRピーク検出)をそのまま実機へ。
 *
 *   GSRモジュール出力 → A0(ADC) → DSP → BLE-MIDI
 *        tonic_norm → CC74(フィルタ) + CC7(音量) + 音域の牽引(覚醒→高音)
 *        SCRピーク  → Note On(声部進行: 移動root+重心復元+前音への近接)
 *                     velocity=タッチ(汗ピーク強)→受信側の音色明るさを駆動
 *
 * 必要ライブラリ(Arduino IDE / arduino-cli でインストール):
 *   - "MIDI Library"        by FortySevenEffects
 *   - "Arduino BLE-MIDI"    by lathoub   (依存: ArduinoBLE 不要、nRF用は Adafruit Bluefruit を使用)
 * Seeed nRF52 ボードパッケージ(Adafruit nRF52ベース)を入れておくこと。
 *
 * ※ 配線・はんだ付け等の「電子工作」部分はこのファイルの範囲外。
 *   接続は docs/wiring.md を参照。
 */

#include <bluefruit.h>
#include <MIDI.h>
#include <BLEMIDI_Transport.h>
#include <hardware/BLEMIDI_Bluefruit.h>

BLEMIDI_CREATE_INSTANCE("SweatSynth", MIDI);

// ---------------- 設定(sim/dsp.py と一致させる) ----------------
static const float FS          = 32.0f;   // 標本化レート(Hz)
static const int   ADC_PIN     = A0;
static const float NOISE_FC    = 2.0f;    // ノイズ除去LP遮断(Hz)
static const float TONIC_FC    = 0.05f;   // tonic抽出LP遮断(Hz)
static const float PEAK_THRESH = 0.03f;   // SCR閾値(正規化前の相対量。下のADC換算注記参照)
static const float REFRACT_SEC = 1.0f;
static const float RANGE_RELEASE_SEC = 30.0f;
static const float SPAN_FLOOR  = 0.05f;

static const uint8_t CC_CUTOFF = 74;
static const uint8_t CC_VOLUME = 7;
static const uint8_t CH        = 1;       // MIDIチャンネル(1-16)

// --- 声部進行(sim/async_synth.py 移植) ---
// 不協和寄りの色(根音からの半音): M2,m3,tritone,m7,M7,b9,9,#11
static const int COLOR[]   = {0, 2, 3, 6, 10, 11, 13, 14, 18};
static const int N_COLOR   = sizeof(COLOR) / sizeof(COLOR[0]);
static const int HOME      = 55;          // 調性重心(sim theta=100Hz の home_pc=G に一致)
static const int NOTE_LO   = 36, NOTE_HI = 84;   // 音域の上下限

// ---------------- 一次LP ----------------
struct OnePole {
  float a, y;
  bool init;
  void setup(float fs, float fc) {
    float dt = 1.0f / fs;
    float rc = 1.0f / (2.0f * PI * fc);
    a = dt / (rc + dt);
    y = 0; init = false;
  }
  float step(float x) {
    if (!init) { y = x; init = true; }
    else       { y += a * (x - y); }
    return y;
  }
};

// ---------------- 適応レンジ(min/max追従) ----------------
struct EnvRange {
  float up, lo, hi, floor_span;
  bool init;
  void setup(float fs, float release_sec, float span_floor) {
    up = expf(-1.0f / (release_sec * fs));
    floor_span = span_floor; init = false;
  }
  void step(float x, float &outLo, float &outHi) {
    if (!init) { lo = hi = x; init = true; }
    lo = (x < lo) ? x : lo * up + x * (1 - up);
    hi = (x > hi) ? x : hi * up + x * (1 - up);
    if (hi - lo < floor_span) {
      float mid = 0.5f * (hi + lo);
      lo = mid - floor_span / 2; hi = mid + floor_span / 2;
    }
    outLo = lo; outHi = hi;
  }
};

OnePole lpNoise, lpTonic;
EnvRange envRange;

int   refractSamples;
int   sincePeak;
float prevPhasic = 0;
bool  rising = false;
int   rootPc   = HOME;   // ゆっくり漂う調性の根音
int   prevPitch = HOME;  // 直前に弾いた音(声部進行の参照点)

uint32_t lastMicros = 0;
const uint32_t periodUs = (uint32_t)(1e6 / FS);

// 直近のNote Off管理(簡易: 1音保持)
int      lastNote = -1;
uint32_t noteOffAtMs = 0;
const uint32_t NOTE_DUR_MS = 900;

int   lastCutoff = -1, lastVolume = -1;

// ADC換算: nRF52 ADCは10bit(0..1023)。GSRモジュール出力電圧を
// おおまかな相対コンダクタンスとして扱う(絶対µSは要キャリブレーション)。
// 正規化レンジで吸収するので相対値でよい。
static inline float readEda() {
  int raw = analogRead(ADC_PIN);
  return (float)raw / 1023.0f * 10.0f;  // 0..10 の相対スケールへ
}

// 起動ごとに違う声部進行/跳躍にするための種をアナログノイズから収穫する。
// A0(EDAセンサ)の最下位ビットは実アナログ信号のジッタで揺れるので、
// 多数読んでLSBを畳み込み、micros()のタイミング揺らぎと混ぜる。
static uint32_t harvestSeed() {
  uint32_t s = micros();
  for (int i = 0; i < 32; i++) {
    s = (s << 1) | (analogRead(ADC_PIN) & 1u);
    s ^= (uint32_t)micros();          // 収集タイミングの揺らぎも混ぜる
    delayMicroseconds(157);           // ADC整定 + LSBが変わる間を置く(素数us)
  }
  return s ? s : 0xA5A5A5A5u;          // 万一全0でも縮退しない
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(10);
  randomSeed(harvestSeed());           // ← アナログノイズで乱数系列を毎回変える

  lpNoise.setup(FS, NOISE_FC);
  lpTonic.setup(FS, TONIC_FC);
  envRange.setup(FS, RANGE_RELEASE_SEC, SPAN_FLOOR);
  refractSamples = (int)(REFRACT_SEC * FS);
  sincePeak = refractSamples;

  BLEMIDI.setHandleConnected([]() { Serial.println("BLE-MIDI connected"); });
  BLEMIDI.setHandleDisconnected([]() { Serial.println("BLE-MIDI disconnected"); });
  MIDI.begin(MIDI_CHANNEL_OMNI);
  Serial.println("SweatSynth ready. Pair 'SweatSynth' over BLE-MIDI.");
}

void processSample(float raw) {
  float filt  = lpNoise.step(raw);
  float tonic = lpTonic.step(filt);
  float phasic = filt - tonic;

  float lo, hi;
  envRange.step(tonic, lo, hi);
  float tn = (tonic - lo) / (hi - lo);
  tn = tn < 0 ? 0 : (tn > 1 ? 1 : tn);

  // --- CC送信(変化時のみ) ---
  int cutoff = (int)(tn * 127.0f + 0.5f);
  int volume = 30 + (int)(tn * 97.0f);
  if (cutoff != lastCutoff) { MIDI.sendControlChange(CC_CUTOFF, cutoff, CH); lastCutoff = cutoff; }
  if (volume != lastVolume) { MIDI.sendControlChange(CC_VOLUME, volume, CH); lastVolume = volume; }

  // --- SCRピーク検出(sim/dsp.py と同じ状態機械) ---
  sincePeak++;
  if (phasic > PEAK_THRESH && phasic > prevPhasic) {
    rising = true;
  } else if (rising && phasic < prevPhasic) {
    if (sincePeak >= refractSamples) {
      // --- タッチ: 汗ピークの強さ→velocity(受信側の velocity→VCF で明るさを駆動) ---
      float touch = min(1.0f, prevPhasic * 4.0f);   // sim/async_synth.py の touch と同一スケール
      int vel = 40 + (int)(touch * 80.0f);
      if (vel > 120) vel = 120; if (vel < 40) vel = 40;

      // --- 声部進行(sim/async_synth.py render 移植) ---
      // 根音をゆっくりドリフト + 重心HOMEへの復元力(離れ過ぎたら引き戻す)
      if ((int)random(100) < 18) {
        static const int drift4[4] = {-2, -1, 1, 2};
        rootPc += drift4[(int)random(4)];
        if (rootPc - HOME >  5) rootPc -= 1;
        if (rootPc - HOME < -5) rootPc += 1;
      }
      // 発汗(覚醒tn)が音域の重心を緩く牽引
      int target = HOME + (int)lroundf(tn * 12.0f);
      // color度 × オクターブの候補を生成
      int cands[N_COLOR * 4]; int nc = 0;
      for (int i = 0; i < N_COLOR; i++)
        for (int o = -1; o <= 2; o++) {
          int c = rootPc + COLOR[i] + 12 * o;
          if (c >= NOTE_LO && c <= NOTE_HI) cands[nc++] = c;
        }
      int note = prevPitch;
      if (nc > 0) {
        if ((int)random(100) < 20) {            // 20%: 跳躍で気配を変える
          note = cands[(int)random(nc)];
        } else {                                 // 80%: 前音・目標域に近い=滑らかな線
          float best = 1e9f;
          for (int k = 0; k < nc; k++) {
            float cost = fabsf((float)(cands[k] - prevPitch))
                       + 0.3f * fabsf((float)(cands[k] - target));
            if (cost < best) { best = cost; note = cands[k]; }
          }
        }
      }
      prevPitch = note;

      if (lastNote >= 0) MIDI.sendNoteOff(lastNote, 0, CH);
      MIDI.sendNoteOn((uint8_t)note, vel, CH);
      lastNote = note;
      noteOffAtMs = millis() + NOTE_DUR_MS;
      sincePeak = 0;
    }
    rising = false;
  }
  prevPhasic = phasic;
}

void loop() {
  MIDI.read();

  // ノートのオフ処理
  if (lastNote >= 0 && (int32_t)(millis() - noteOffAtMs) >= 0) {
    MIDI.sendNoteOff(lastNote, 0, CH);
    lastNote = -1;
  }

  // 一定レート(FS)でサンプリング
  uint32_t now = micros();
  if (now - lastMicros >= periodUs) {
    lastMicros += periodUs;
    processSample(readEda());
  }
}
