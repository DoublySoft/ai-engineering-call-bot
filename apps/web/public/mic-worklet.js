// Captures the microphone, downsamples to 16 kHz mono PCM16 and posts 20 ms frames.
// `sampleRate` is the AudioContext rate (a global in AudioWorkletGlobalScope).
const TARGET_RATE = 16000;
const FRAME = 320; // 20 ms at 16 kHz

class MicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / TARGET_RATE;
    this.carry = new Float32Array(0);
    this.out = new Int16Array(FRAME);
    this.outLen = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    const data = new Float32Array(this.carry.length + channel.length);
    data.set(this.carry);
    data.set(channel, this.carry.length);

    let pos = 0;
    let sumSq = 0;
    let produced = 0;
    // Box-filter decimation: average every `ratio` input samples into one output sample.
    while (pos + this.ratio <= data.length) {
      const start = Math.floor(pos);
      const end = Math.floor(pos + this.ratio);
      let acc = 0;
      for (let i = start; i < end; i++) acc += data[i];
      const v = Math.max(-1, Math.min(1, acc / Math.max(1, end - start)));
      sumSq += v * v;
      produced++;
      this.out[this.outLen++] = v < 0 ? v * 0x8000 : v * 0x7fff;
      if (this.outLen === FRAME) {
        this.port.postMessage({ pcm: this.out.buffer.slice(0) });
        this.outLen = 0;
      }
      pos += this.ratio;
    }
    this.carry = data.slice(Math.floor(pos));
    if (produced) this.port.postMessage({ level: Math.sqrt(sumSq / produced) });
    return true;
  }
}

registerProcessor("mic-processor", MicProcessor);
