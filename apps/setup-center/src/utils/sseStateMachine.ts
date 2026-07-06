export type SseConsumptionState =
  | "idle"
  | "reading"
  | "resuming"
  | "completed"
  | "aborted"
  | "failed";

export type SseFrame = {
  event: string;
  data: string;
  id: string | null;
  retry: number | null;
  rawLines: string[];
};

type PendingFrame = {
  event: string | null;
  dataLines: string[];
  id: string | null;
  retry: number | null;
  rawLines: string[];
};

const createPendingFrame = (): PendingFrame => ({
  event: null,
  dataLines: [],
  id: null,
  retry: null,
  rawLines: [],
});

export class SseStateMachine {
  private decoder = new TextDecoder();
  private buffer = "";
  private pending = createPendingFrame();
  private _state: SseConsumptionState = "idle";

  get state(): SseConsumptionState {
    return this._state;
  }

  start(): void {
    if (this._state === "idle" || this._state === "resuming" || this._state === "completed") {
      this._state = "reading";
    }
  }

  push(chunk: Uint8Array): SseFrame[] {
    this.start();
    this.buffer += this.decoder.decode(chunk, { stream: true });
    return this.drainLines(false);
  }

  finish(): SseFrame[] {
    if (this._state === "aborted" || this._state === "failed") return [];
    this.buffer += this.decoder.decode();
    const frames = this.drainLines(true);
    const trailing = this.dispatchPendingFrame();
    if (trailing) frames.push(trailing);
    this._state = "completed";
    return frames;
  }

  resetStream(): void {
    this.decoder = new TextDecoder();
    this.buffer = "";
    this.pending = createPendingFrame();
    this._state = "resuming";
  }

  abort(): void {
    this.buffer = "";
    this.pending = createPendingFrame();
    this._state = "aborted";
  }

  fail(): void {
    this._state = "failed";
  }

  private drainLines(flush: boolean): SseFrame[] {
    const frames: SseFrame[] = [];

    while (this.buffer.length > 0) {
      const boundary = this.findNextLineBoundary(this.buffer, flush);
      if (!boundary) break;

      const line = this.buffer.slice(0, boundary.lineEnd);
      this.buffer = this.buffer.slice(boundary.nextStart);
      const frame = this.consumeLine(line);
      if (frame) frames.push(frame);
    }

    if (flush && this.buffer.length > 0) {
      const frame = this.consumeLine(this.buffer);
      this.buffer = "";
      if (frame) frames.push(frame);
    }

    return frames;
  }

  private findNextLineBoundary(
    text: string,
    flush: boolean,
  ): { lineEnd: number; nextStart: number } | null {
    for (let i = 0; i < text.length; i += 1) {
      const ch = text[i];
      if (ch !== "\n" && ch !== "\r") continue;
      if (ch === "\r" && i === text.length - 1 && !flush) return null;
      const nextStart = ch === "\r" && text[i + 1] === "\n" ? i + 2 : i + 1;
      return { lineEnd: i, nextStart };
    }
    return null;
  }

  private consumeLine(rawLine: string): SseFrame | null {
    const line = this.pending.rawLines.length === 0 && rawLine.startsWith("\uFEFF")
      ? rawLine.slice(1)
      : rawLine;

    if (line === "") {
      return this.dispatchPendingFrame();
    }

    this.pending.rawLines.push(line);
    if (line.startsWith(":")) return null;

    const colonIdx = line.indexOf(":");
    const field = colonIdx >= 0 ? line.slice(0, colonIdx) : line;
    let value = colonIdx >= 0 ? line.slice(colonIdx + 1) : "";
    if (value.startsWith(" ")) value = value.slice(1);

    switch (field) {
      case "event":
        this.pending.event = value;
        break;
      case "data":
        this.pending.dataLines.push(value);
        break;
      case "id":
        if (!value.includes("\u0000")) this.pending.id = value;
        break;
      case "retry": {
        const retry = Number.parseInt(value, 10);
        if (Number.isFinite(retry) && retry >= 0) this.pending.retry = retry;
        break;
      }
      default:
        break;
    }

    return null;
  }

  private dispatchPendingFrame(): SseFrame | null {
    if (this.pending.dataLines.length === 0) {
      this.pending = createPendingFrame();
      return null;
    }

    const frame: SseFrame = {
      event: this.pending.event || "message",
      data: this.pending.dataLines.join("\n"),
      id: this.pending.id,
      retry: this.pending.retry,
      rawLines: [...this.pending.rawLines],
    };
    this.pending = createPendingFrame();
    return frame;
  }
}

export type SseResumeReason = "read_error" | "eof";
export type SseFrameAction = "continue" | "stop" | void;

export type ConsumeSseStreamOptions = {
  reader: ReadableStreamDefaultReader<Uint8Array>;
  signal?: AbortSignal;
  machine?: SseStateMachine;
  onFrame: (frame: SseFrame, machine: SseStateMachine) => SseFrameAction | Promise<SseFrameAction>;
  onChunk?: (chunk: Uint8Array, machine: SseStateMachine) => void;
  onReaderChange?: (reader: ReadableStreamDefaultReader<Uint8Array>) => void;
  resume?: (
    reason: SseResumeReason,
    error: unknown,
    machine: SseStateMachine,
  ) => ReadableStreamDefaultReader<Uint8Array> | null | Promise<ReadableStreamDefaultReader<Uint8Array> | null>;
};

export type ConsumeSseStreamResult = {
  state: SseConsumptionState;
  reader: ReadableStreamDefaultReader<Uint8Array>;
  stoppedByHandler: boolean;
};

export async function consumeSseStream(
  options: ConsumeSseStreamOptions,
): Promise<ConsumeSseStreamResult> {
  const machine = options.machine ?? new SseStateMachine();
  let reader = options.reader;
  let stoppedByHandler = false;

  options.onReaderChange?.(reader);
  machine.start();

  const emitFrames = async (frames: SseFrame[]): Promise<boolean> => {
    for (const frame of frames) {
      const action = await options.onFrame(frame, machine);
      if (action === "stop") return true;
    }
    return false;
  };

  const tryResume = async (reason: SseResumeReason, error: unknown) => {
    if (!options.resume) return false;
    const nextReader = await options.resume(reason, error, machine);
    if (!nextReader) return false;
    reader = nextReader;
    machine.resetStream();
    machine.start();
    options.onReaderChange?.(reader);
    return true;
  };

  while (true) {
    if (options.signal?.aborted) {
      machine.abort();
      break;
    }

    let result: ReadableStreamReadResult<Uint8Array>;
    try {
      result = await reader.read();
    } catch (error) {
      if (options.signal?.aborted) {
        machine.abort();
        break;
      }
      if (await tryResume("read_error", error)) continue;
      machine.fail();
      throw error;
    }

    const { done, value } = result;
    let frames: SseFrame[] = [];
    if (value) {
      options.onChunk?.(value, machine);
      frames = machine.push(value);
    }

    if (options.signal?.aborted) {
      machine.abort();
      break;
    }

    if (done) {
      frames = [...frames, ...machine.finish()];
    }

    if (await emitFrames(frames)) {
      stoppedByHandler = true;
      break;
    }

    if (!done) continue;
    if (await tryResume("eof", undefined)) continue;
    break;
  }

  return { state: machine.state, reader, stoppedByHandler };
}
