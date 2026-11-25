export const PROTOCOL_VERSION = 2;
export const SERVICE_NAME = "voxselect-readio-service";

export function encodeRequest(id, command, params = {}) {
  return JSON.stringify({ id, protocol: PROTOCOL_VERSION, command, params }) + "\n";
}

export class JsonlTransport {
  constructor({ onEvent = () => {}, onFailure = () => {} } = {}) {
    this.onEvent = onEvent;
    this.onFailure = onFailure;
    this.child = null;
    this.sequence = 1;
    this.pending = new Map();
    this.readyInfo = null;
    this.readyPromise = null;
    this.readyResolve = null;
    this.readyReject = null;
    this.readyTimer = null;
    this.failure = null;
  }

  attach(child) {
    this.child = child;
  }

  waitForReady(timeoutMs) {
    if (this.readyInfo) return Promise.resolve(this.readyInfo);
    if (this.failure) return Promise.reject(this.failure);
    if (this.readyPromise) return this.readyPromise;

    this.readyPromise = new Promise((resolve, reject) => {
      this.readyResolve = resolve;
      this.readyReject = reject;
    });
    this.readyPromise.catch(() => {});
    this.readyTimer = setTimeout(
      () => this.fail(new Error(`Readio service was not ready after ${timeoutMs / 1000}s`)),
      timeoutMs
    );
    return this.readyPromise;
  }

  handleLine(line) {
    let message;
    try {
      message = JSON.parse(typeof line === "string" ? line.trim() : String(line).trim());
    } catch {
      console.warn("Unparsed sidecar output:", line);
      return;
    }
    if (!message || typeof message !== "object") {
      this.fail(new Error("Sidecar output must be a JSON object"));
      return;
    }

    if (message.event === "ready") {
      if (
        message.protocol !== PROTOCOL_VERSION ||
        message.service !== SERVICE_NAME
      ) {
        this.fail(
          new Error(`Unsupported sidecar protocol or service: ${JSON.stringify(message)}`)
        );
        return;
      }
      this.readyInfo = message;
      this.clearReadyWait();
      this.readyResolve?.(message);
      this.readyResolve = null;
      this.readyReject = null;
      return;
    }

    if (message.protocol !== PROTOCOL_VERSION) {
      const error = new Error(
        `Sidecar protocol mismatch: expected ${PROTOCOL_VERSION}, received ${message.protocol ?? "none"}`
      );
      if (message.id && this.pending.has(message.id)) {
        this.rejectPending(message.id, error);
      } else {
        this.fail(error);
      }
      return;
    }

    if (message.event) {
      this.onEvent(message);
      return;
    }

    if (!message.id || !this.pending.has(message.id)) return;
    const entry = this.pending.get(message.id);
    clearTimeout(entry.timer);
    this.pending.delete(message.id);
    if (message.ok === false) {
      const detail = message.error?.result?.message || message.error?.message || message.error;
      entry.reject(new Error(detail || "Sidecar request failed"));
    } else {
      entry.resolve(message.result ?? message);
    }
  }

  async request(command, params = {}, timeoutMs = 15000) {
    if (this.failure) throw this.failure;
    const child = this.child;
    if (!child) throw new Error("Readio service is not running");
    const id = `r${this.sequence++}`;
    const response = new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`${command} timed out`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
    });
    response.catch(() => {});
    try {
      await child.write(encodeRequest(id, command, params));
    } catch (error) {
      this.rejectPending(id, error);
      throw error;
    }
    return response;
  }

  fail(error) {
    if (this.failure) return;
    this.failure = error instanceof Error ? error : new Error(String(error));
    this.child = null;
    this.clearReadyWait();
    this.readyReject?.(this.failure);
    this.readyResolve = null;
    this.readyReject = null;
    for (const id of this.pending.keys()) this.rejectPending(id, this.failure);
    this.onFailure(this.failure);
  }

  rejectPending(id, error) {
    const entry = this.pending.get(id);
    if (!entry) return;
    clearTimeout(entry.timer);
    this.pending.delete(id);
    entry.reject(error);
  }

  clearReadyWait() {
    if (this.readyTimer) {
      clearTimeout(this.readyTimer);
      this.readyTimer = null;
    }
  }
}

export class SpeechGeneration {
  constructor() {
    this.sequence = 0;
    this.generation = null;
    this.awaiting = false;
    this.buffered = [];
  }

  begin() {
    this.sequence += 1;
    this.generation = null;
    this.awaiting = true;
    this.buffered = [];
    return this.sequence;
  }

  observe(event) {
    if (this.awaiting) {
      this.buffered.push(event);
      return [];
    }
    return this.matches(event.generation) ? [event] : [];
  }

  acknowledge(sequence, generation) {
    if (!this.isCurrent(sequence)) return [];
    this.generation = generation;
    this.awaiting = false;
    const events = this.buffered;
    this.buffered = [];
    return events.filter((event) => this.matches(event.generation));
  }

  cancel() {
    this.sequence += 1;
    this.generation = null;
    this.awaiting = false;
    this.buffered = [];
  }

  fail(generation) {
    if (this.matches(generation)) this.generation = null;
  }

  isCurrent(sequence) {
    return this.sequence === sequence;
  }

  matches(generation) {
    return this.generation === generation;
  }
}
