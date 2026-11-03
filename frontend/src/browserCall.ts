/**
 * Browser voice call: streams the laptop microphone to the backend (/voice/browser),
 * which relays it to the Grok Voice Agent, and plays the agent's replies.
 * Audio on the wire is 24 kHz mono PCM16, base64-encoded.
 */
import { wsUrl } from "./api";

const RATE = 24000;

const WORKLET = `
class Capture extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor("capture", Capture);
`;

function floatToB64(samples: Float32Array) {
  const pcm = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  let bin = "";
  const bytes = new Uint8Array(pcm.buffer);
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}

function b64ToFloat(b64: string) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const pcm = new Int16Array(bytes.buffer);
  const out = new Float32Array(pcm.length);
  for (let i = 0; i < pcm.length; i++) out[i] = pcm[i] / 0x8000;
  return out;
}

export type CallHandle = { hangup: () => void };

export async function startBrowserCall(onStatus: (s: string) => void): Promise<CallHandle> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  const ctx = new AudioContext({ sampleRate: RATE });
  const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
  await ctx.audioWorklet.addModule(url);
  const src = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, "capture");
  src.connect(node);

  const ws = new WebSocket(wsUrl("/voice/browser"));
  let buffered: Float32Array[] = [];
  let bufferedLen = 0;
  let playAt = 0;
  const playing: AudioBufferSourceNode[] = [];

  node.port.onmessage = (e) => {
    buffered.push(e.data as Float32Array);
    bufferedLen += (e.data as Float32Array).length;
    if (bufferedLen >= RATE / 10 && ws.readyState === WebSocket.OPEN) {
      const chunk = new Float32Array(bufferedLen);
      let o = 0;
      for (const b of buffered) { chunk.set(b, o); o += b.length; }
      buffered = []; bufferedLen = 0;
      ws.send(JSON.stringify({ audio: floatToB64(chunk) }));
    }
  };

  ws.onopen = () => onStatus("connected");
  ws.onclose = () => onStatus("ended");
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === "audio") {
      const samples = b64ToFloat(msg.audio);
      const buf = ctx.createBuffer(1, samples.length, RATE);
      buf.copyToChannel(samples, 0);
      const s = ctx.createBufferSource();
      s.buffer = buf;
      s.connect(ctx.destination);
      playAt = Math.max(playAt, ctx.currentTime);
      s.start(playAt);
      playAt += buf.duration;
      playing.push(s);
      s.onended = () => playing.splice(playing.indexOf(s), 1);
    } else if (msg.type === "interrupt") {
      playing.forEach((s) => s.stop());
      playAt = ctx.currentTime;
    } else if (msg.type === "call_error") {
      onStatus("error: " + msg.error);
    }
  };

  return {
    hangup: () => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "hangup" }));
      ws.close();
      stream.getTracks().forEach((t) => t.stop());
      ctx.close();
      URL.revokeObjectURL(url);
    },
  };
}
