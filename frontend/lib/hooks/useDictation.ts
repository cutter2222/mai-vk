"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api/client";

/**
 * Голосовой ввод в чате. Модель распознаёт записи до 25 с и слов по одному не отдаёт, поэтому
 * фразы режет браузер: микрофон → 16 кГц моно → детектор по энергии (порог от шума первых
 * 300 мс, конец фразы — 700 мс тишины, не длиннее 20 с, меньше 300 мс речи — не фраза) → WAV
 * PCM16 → `POST /api/speech/transcribe`. Ответы дописываются в поле ввода в порядке фраз, пока
 * запись идёт. Остановка дожидается последних фраз; отмена (отправка сообщения) их отбрасывает.
 */

const RATE = 16000;
const FRAME = 320; // 20 мс
const CALIBRATION_FRAMES = 15; // 300 мс
const END_SILENCE_FRAMES = 35; // 700 мс
const MIN_SPEECH_FRAMES = 15; // 300 мс
const PRE_ROLL_FRAMES = 10; // 200 мс до начала речи
const MAX_PHRASE_FRAMES = 1000; // 20 с
const MIN_THRESHOLD = 0.004;

export type DictationState = "idle" | "starting" | "recording" | "stopping";

export function dictationSupported(): boolean {
  return typeof navigator !== "undefined" && Boolean(navigator.mediaDevices?.getUserMedia) && typeof window !== "undefined" && "AudioContext" in window;
}

/** 16-битный PCM моно в WAV. */
export function encodeWav(samples: Float32Array, rate = RATE): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const text = (offset: number, value: string) => { for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i)); };
  text(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // моно
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

/** Поток другой частоты → 16 кГц: сглаживание окном по кратности и линейная интерполяция. */
class Resampler {
  private readonly ratio: number;
  private readonly width: number;
  private pos = 0;
  private history: number[] = [];

  constructor(private readonly from: number) {
    this.ratio = from / RATE;
    this.width = Math.max(1, Math.round(this.ratio));
  }

  push(input: Float32Array): Float32Array {
    if (this.from === RATE) return input;
    // Скользящее среднее — грубый фильтр от наложения частот перед прореживанием.
    const smoothed = new Float32Array(input.length);
    for (let i = 0; i < input.length; i++) {
      this.history.push(input[i]);
      if (this.history.length > this.width) this.history.shift();
      let sum = 0;
      for (const v of this.history) sum += v;
      smoothed[i] = sum / this.history.length;
    }
    const out: number[] = [];
    while (this.pos + 1 < smoothed.length) {
      const i = Math.floor(this.pos);
      const f = this.pos - i;
      out.push(smoothed[i] * (1 - f) + smoothed[i + 1] * f);
      this.pos += this.ratio;
    }
    this.pos -= smoothed.length;
    if (this.pos < 0) this.pos = 0;
    return Float32Array.from(out);
  }
}

/** Детектор фраз по энергии кадров 20 мс; отдаёт готовые фразы через `onPhrase`. */
export class PhraseDetector {
  private buffer: number[] = [];
  private noise: number[] = [];
  private threshold = 0;
  private preRoll: Float32Array[] = [];
  private phrase: Float32Array[] = [];
  private speechFrames = 0;
  private silentTail = 0;
  private speaking = false;

  constructor(private readonly onPhrase: (samples: Float32Array) => void) {}

  push(samples: Float32Array): void {
    for (const s of samples) this.buffer.push(s);
    while (this.buffer.length >= FRAME) {
      const frame = Float32Array.from(this.buffer.splice(0, FRAME));
      this.frame(frame);
    }
  }

  private frame(frame: Float32Array): void {
    let sum = 0;
    for (const s of frame) sum += s * s;
    const rms = Math.sqrt(sum / frame.length);
    if (this.noise.length < CALIBRATION_FRAMES) {
      this.noise.push(rms);
      const mean = this.noise.reduce((a, b) => a + b, 0) / this.noise.length;
      this.threshold = Math.max(MIN_THRESHOLD, mean * 3);
      this.keepPreRoll(frame);
      return;
    }
    const loud = rms > this.threshold;
    if (!this.speaking) {
      if (!loud) {
        this.keepPreRoll(frame);
        return;
      }
      this.speaking = true;
      this.phrase = [...this.preRoll];
      this.preRoll = [];
      this.speechFrames = 0;
      this.silentTail = 0;
    }
    this.phrase.push(frame);
    if (loud) {
      this.speechFrames += 1;
      this.silentTail = 0;
    } else {
      this.silentTail += 1;
    }
    if (this.silentTail >= END_SILENCE_FRAMES || this.phrase.length >= MAX_PHRASE_FRAMES) this.finish();
  }

  private keepPreRoll(frame: Float32Array): void {
    this.preRoll.push(frame);
    if (this.preRoll.length > PRE_ROLL_FRAMES) this.preRoll.shift();
  }

  /** Закончить текущую фразу (тишина, предел длины или остановка записи). */
  finish(): void {
    if (!this.speaking) return;
    const frames = this.phrase;
    const speech = this.speechFrames;
    // Хвост тишины длиннее 200 мс модели не нужен.
    const trim = Math.max(0, this.silentTail - PRE_ROLL_FRAMES);
    this.speaking = false;
    this.phrase = [];
    this.speechFrames = 0;
    this.silentTail = 0;
    if (speech < MIN_SPEECH_FRAMES) return;
    const kept = frames.slice(0, frames.length - trim);
    const out = new Float32Array(kept.length * FRAME);
    kept.forEach((f, i) => out.set(f, i * FRAME));
    this.onPhrase(out);
  }
}

const TAP_WORKLET = `
class DictationTap extends AudioWorkletProcessor {
  constructor() { super(); this.chunk = []; }
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) {
      for (let i = 0; i < channel.length; i++) this.chunk.push(channel[i]);
      if (this.chunk.length >= 1024) { this.port.postMessage(Float32Array.from(this.chunk)); this.chunk = []; }
    }
    return true;
  }
}
registerProcessor("dictation-tap", DictationTap);
`;

interface Capture {
  stream: MediaStream;
  context: AudioContext;
  nodes: AudioNode[];
}

async function openContext(stream: MediaStream): Promise<{ context: AudioContext; source: MediaStreamAudioSourceNode }> {
  // 16 кГц сразу, если браузер умеет; Firefox не соединяет поток с контекстом другой частоты —
  // тогда контекст по умолчанию и пересэмплирование в коде.
  try {
    const context = new AudioContext({ sampleRate: RATE });
    try {
      return { context, source: context.createMediaStreamSource(stream) };
    } catch {
      await context.close();
    }
  } catch {
    // частоту не задать: ниже контекст по умолчанию
  }
  const context = new AudioContext();
  return { context, source: context.createMediaStreamSource(stream) };
}

export function useDictation({ onText, onError }: { onText: (text: string) => void; onError: (message: string) => void }) {
  const [state, setState] = useState<DictationState>("idle");
  const [pending, setPending] = useState(0);
  const capture = useRef<Capture | null>(null);
  const detector = useRef<PhraseDetector | null>(null);
  // Номер сеанса записи: ответы прошлого сеанса после отмены в поле не попадают.
  const session = useRef(0);
  const nextSeq = useRef(0);
  const nextEmit = useRef(0);
  const ready = useRef(new Map<number, string>());
  const inflight = useRef(new Set<Promise<void>>());
  const aborts = useRef(new Set<AbortController>());
  const textRef = useRef(onText);
  const errorRef = useRef(onError);
  useEffect(() => { textRef.current = onText; errorRef.current = onError; }, [onText, onError]);

  const release = useCallback(() => {
    const c = capture.current;
    capture.current = null;
    if (!c) return;
    c.nodes.forEach((n) => { try { n.disconnect(); } catch { /* уже отключён */ } });
    c.stream.getTracks().forEach((t) => t.stop());
    void c.context.close().catch(() => undefined);
  }, []);

  const emitReady = useCallback(() => {
    while (ready.current.has(nextEmit.current)) {
      const text = ready.current.get(nextEmit.current) ?? "";
      ready.current.delete(nextEmit.current);
      nextEmit.current += 1;
      if (text.trim()) textRef.current(text.trim());
    }
  }, []);

  const send = useCallback((samples: Float32Array) => {
    const mine = session.current;
    const seq = nextSeq.current++;
    const controller = new AbortController();
    aborts.current.add(controller);
    setPending((n) => n + 1);
    const job = api.speech.transcribe(encodeWav(samples), controller.signal)
      .then((r) => {
        if (session.current !== mine) return;
        ready.current.set(seq, r.text);
        emitReady();
      })
      .catch((e: unknown) => {
        if (session.current !== mine || controller.signal.aborted) return;
        // Фраза пропала — следующие всё равно дописываются по порядку.
        ready.current.set(seq, "");
        emitReady();
        session.current += 1;
        release();
        detector.current = null;
        setState("idle");
        errorRef.current(e instanceof ApiError ? e.message : "Распознавание речи сейчас недоступно, наберите текст");
      })
      .finally(() => {
        aborts.current.delete(controller);
        inflight.current.delete(job);
        setPending((n) => Math.max(0, n - 1));
      });
    inflight.current.add(job);
  }, [emitReady, release]);

  const start = useCallback(async () => {
    if (capture.current || !dictationSupported()) return;
    setState("starting");
    session.current += 1;
    nextSeq.current = 0;
    nextEmit.current = 0;
    ready.current.clear();
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    } catch (e) {
      setState("idle");
      const denied = e instanceof DOMException && (e.name === "NotAllowedError" || e.name === "SecurityError");
      errorRef.current(denied ? "Разрешите доступ к микрофону в браузере" : "Микрофон не найден или занят другим приложением");
      return;
    }
    try {
      const { context, source } = await openContext(stream);
      const resampler = new Resampler(context.sampleRate);
      const phrases = new PhraseDetector(send);
      detector.current = phrases;
      const feed = (chunk: Float32Array) => { if (detector.current === phrases) phrases.push(resampler.push(chunk)); };
      // Узел должен быть в графе, иначе браузер его не вызывает; звук наружу не идёт.
      const mute = context.createGain();
      mute.gain.value = 0;
      mute.connect(context.destination);
      let tap: AudioNode;
      if (context.audioWorklet && typeof AudioWorkletNode !== "undefined") {
        const url = URL.createObjectURL(new Blob([TAP_WORKLET], { type: "application/javascript" }));
        try {
          await context.audioWorklet.addModule(url);
        } finally {
          URL.revokeObjectURL(url);
        }
        const node = new AudioWorkletNode(context, "dictation-tap");
        node.port.onmessage = (event: MessageEvent<Float32Array>) => feed(event.data);
        tap = node;
      } else {
        const node = context.createScriptProcessor(4096, 1, 1);
        node.onaudioprocess = (event) => feed(Float32Array.from(event.inputBuffer.getChannelData(0)));
        tap = node;
      }
      source.connect(tap);
      tap.connect(mute);
      capture.current = { stream, context, nodes: [source, tap, mute] };
      setState("recording");
      // Прогрев: после простоя сервис выгружает модель, и её загрузка (2–3 с) легла бы на первую
      // фразу. Полсекунды тишины будят модель, пока человек только начинает говорить.
      void api.speech.transcribe(encodeWav(new Float32Array(RATE / 2))).catch(() => undefined);
    } catch {
      stream.getTracks().forEach((t) => t.stop());
      detector.current = null;
      setState("idle");
      errorRef.current("Не удалось включить запись в этом браузере");
    }
  }, [send]);

  /** Выключить запись: последняя фраза уходит на распознавание, ответы дописываются. */
  const stop = useCallback(async () => {
    if (!capture.current) return;
    setState("stopping");
    detector.current?.finish();
    detector.current = null;
    release();
    await Promise.allSettled([...inflight.current]);
    setState("idle");
  }, [release]);

  /** Выключить запись и забыть недослушанное: сообщение уже отправлено. */
  const cancel = useCallback(() => {
    session.current += 1;
    detector.current = null;
    aborts.current.forEach((c) => c.abort());
    aborts.current.clear();
    ready.current.clear();
    release();
    setPending(0);
    setState("idle");
  }, [release]);

  useEffect(() => () => {
    session.current += 1;
    aborts.current.forEach((c) => c.abort());
    const c = capture.current;
    capture.current = null;
    if (c) {
      c.stream.getTracks().forEach((t) => t.stop());
      void c.context.close().catch(() => undefined);
    }
  }, []);

  return { state, pending, start, stop, cancel, recording: state === "recording" };
}
