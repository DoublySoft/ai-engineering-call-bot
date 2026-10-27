// Browser side of the /browser/audio WebSocket protocol (see apps/api/.../browser/transport.py):
//   page -> server: binary PCM16 16 kHz mono frames; {"type":"mark","name"} once audio up to a mark has played; {"type":"stop"}
//   server -> page: binary PCM16 24 kHz mono; {"type":"mark"|"clear"|"hangup"}

export type VoiceState = "idle" | "connecting" | "listening" | "agent-speaking" | "ended" | "error";

export interface VoiceClientOptions {
  url: string;
  outputSampleRate: number;
  /** Let the caller interrupt the agent. Without headphones the agent can hear itself, so default off. */
  allowBargeIn: boolean;
  onState: (state: VoiceState, detail?: string) => void;
  onLevel: (rms: number) => void;
}

export class BrowserVoiceClient {
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  private ws: WebSocket | null = null;
  private playHead = 0;
  private sources = new Set<AudioBufferSourceNode>();
  private marks: { name: string; at: number }[] = [];
  private markTimer: ReturnType<typeof setInterval> | null = null;
  private state: VoiceState = "idle";
  /** Set by stop(); start() checks it after every await so a late permission grant can't open a session. */
  private cancelled = false;

  constructor(private opts: VoiceClientOptions) {}

  private setState(state: VoiceState, detail?: string) {
    if (this.state === "ended" || this.state === "error") return;
    this.state = state;
    this.opts.onState(state, detail);
  }

  private get playing(): boolean {
    return !!this.ctx && this.ctx.currentTime < this.playHead - 0.02;
  }

  async start(): Promise<void> {
    this.setState("connecting");
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (e) {
      if (!this.cancelled) this.setState("error", `Microphone permission denied or unavailable (${(e as Error).name})`);
      return;
    }
    if (this.cancelled) return this.teardown();
    this.ctx = new AudioContext();
    await this.ctx.audioWorklet.addModule("/mic-worklet.js");
    if (this.cancelled) return this.teardown();
    this.node = new AudioWorkletNode(this.ctx, "mic-processor");
    this.ctx.createMediaStreamSource(this.stream).connect(this.node);

    this.ws = new WebSocket(this.opts.url);
    this.ws.binaryType = "arraybuffer";
    this.ws.onopen = () => this.setState("listening");
    this.ws.onmessage = (e) => this.onMessage(e);
    this.ws.onclose = (e) => {
      if (e.code === 1008) this.setState("error", "Session rejected (token expired or already used)");
      this.teardown();
      this.setState("ended");
    };
    this.ws.onerror = () => this.setState("error", "Could not reach the API WebSocket");

    this.node.port.onmessage = ({ data }) => {
      if (data.level !== undefined) this.opts.onLevel(data.level);
      if (!data.pcm || this.ws?.readyState !== WebSocket.OPEN) return;
      // Half-duplex unless barge-in is allowed: don't send the agent's own voice back.
      if (this.playing && !this.opts.allowBargeIn) return;
      this.ws.send(data.pcm);
    };

    this.markTimer = setInterval(() => this.flushMarks(), 50);
  }

  private onMessage(e: MessageEvent) {
    if (!this.ctx) return;
    if (e.data instanceof ArrayBuffer) {
      this.enqueue(new Int16Array(e.data));
      return;
    }
    const msg = JSON.parse(e.data as string);
    if (msg.type === "mark") this.marks.push({ name: msg.name, at: Math.max(this.playHead, this.ctx.currentTime) });
    else if (msg.type === "clear") this.clear();
    else if (msg.type === "hangup") this.setState("ended", msg.reason);
  }

  private enqueue(pcm: Int16Array) {
    const ctx = this.ctx!;
    const buffer = ctx.createBuffer(1, pcm.length, this.opts.outputSampleRate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 0x8000;
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);
    const startAt = Math.max(ctx.currentTime + 0.03, this.playHead);
    source.start(startAt);
    this.playHead = startAt + buffer.duration;
    this.sources.add(source);
    source.onended = () => this.sources.delete(source);
    this.setState("agent-speaking");
  }

  private flushMarks() {
    if (!this.ctx) return;
    const now = this.ctx.currentTime;
    while (this.marks.length && this.marks[0].at <= now) {
      const mark = this.marks.shift()!;
      this.ws?.send(JSON.stringify({ type: "mark", name: mark.name }));
    }
    if (this.state === "agent-speaking" && !this.playing) this.setState("listening");
  }

  private clear() {
    this.sources.forEach((s) => s.stop());
    this.sources.clear();
    this.marks = [];
    this.playHead = this.ctx?.currentTime ?? 0;
    this.setState("listening");
  }

  stop() {
    this.cancelled = true;
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ type: "stop" }));
    this.ws?.close();
    this.teardown();
    this.setState("ended");
  }

  private teardown() {
    if (this.markTimer) clearInterval(this.markTimer);
    this.markTimer = null;
    this.stream?.getTracks().forEach((t) => t.stop());
    this.node?.disconnect();
    void this.ctx?.close();
    this.ctx = null;
  }
}
