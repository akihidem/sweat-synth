/*
 * sweat_midi.ino — 手汗(EDA/GSR)→ BLE-MIDI コントローラ
 * ターゲット: Seeed XIAO nRF52840 (Sense でなくとも可)
 *
 * このファームは sim/dsp.py と「同じ信号処理」を float でオンライン実装する。
 * シミュレータで詰めた挙動(ノイズLP→tonic抽出→正規化→SCRピーク検出)をそのまま実機へ。
 *
 *   GSRモジュール出力 → A0(ADC) → DSP → BLE-MIDI
 *        tonic_norm → CC74(フィルタ) + CC7(音量)
 *        SCRピーク  → Note On(ペンタトニック)
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
// Cマイナー・ペンタトニック(sim/midi_map.py と同一)
static const uint8_t PENTA[]   = {60, 63, 65, 67, 70, 72, 75, 77};
static const int     N_PENTA   = sizeof(PENTA) / sizeof(PENTA[0]);

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
int   pentaDeg = 0;

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

void setup() {
  Serial.begin(115200);
  analogReadResolution(10);

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
      float amp = prevPhasic;
      int vel = 40 + (int)(min(1.0f, amp * 250.0f / 127.0f) * 80.0f);
      if (vel > 120) vel = 120; if (vel < 40) vel = 40;
      uint8_t note = PENTA[pentaDeg % N_PENTA];
      pentaDeg++;
      if (lastNote >= 0) MIDI.sendNoteOff(lastNote, 0, CH);
      MIDI.sendNoteOn(note, vel, CH);
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
