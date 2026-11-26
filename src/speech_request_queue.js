export class SpeechRequestQueue {
  constructor() {
    this.pending = Promise.resolve();
  }

  run(action) {
    const operation = this.pending.catch(() => {}).then(action);
    this.pending = operation.catch(() => {});
    return operation;
  }

  stop(stopRequest) {
    return this.run(stopRequest);
  }

  replace(stopRequest, speakRequest) {
    return this.run(async () => {
      await stopRequest();
      return speakRequest();
    });
  }
}
