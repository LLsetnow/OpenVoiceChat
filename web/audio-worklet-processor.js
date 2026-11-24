/**
 * AudioWorkletRecorderProcessor
 *
 * Runs in the audio rendering thread (AudioWorkletGlobalScope).
 * - Performs real-time smooth linear resampling from native device sample rates
 *   (e.g., 44.1 kHz, 48 kHz) to 16 kHz 16-bit mono PCM.
 * - Batches samples into 512-sample (32ms / 1024 bytes) speech frames.
 * - Computes chunk RMS energy for speech detection and echo/barge-in suppression.
 * - Sends zero-copy transferable ArrayBuffers to the main thread via port.postMessage.
 */
class AudioWorkletRecorderProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    this.targetSampleRate = opts.targetSampleRate || 16000;
    this.bufferSize = opts.bufferSize || 512; // 512 samples at 16kHz = 32ms (1024 bytes)

    this.sourceSampleRate = typeof sampleRate !== "undefined" ? sampleRate : 48000;
    this.ratio = this.sourceSampleRate / this.targetSampleRate;

    // Resampling state across quantum boundaries
    this.resampleOffset = 0;
    this.leftoverSample = 0;
    this.leftoverCount = 0;

    // Reusable working buffer for zero allocations in process()
    this.workBuffer = new Float32Array(256);

    // Output PCM16 buffer
    this.outputBuffer = new Int16Array(this.bufferSize);
    this.outputCount = 0;

    // Energy tracking
    this.sumSquare = 0;
    this.sampleCount = 0;

    this.paused = false;

    this.port.onmessage = (event) => {
      const data = event.data;
      if (!data) return;
      if (data.command === "pause") {
        this.paused = true;
      } else if (data.command === "resume") {
        this.paused = false;
      } else if (data.command === "flush" || data.command === "stop") {
        this._flush();
        this.resampleOffset = 0;
        this.leftoverCount = 0;
        this.leftoverSample = 0;
      } else if (data.command === "reset") {
        this.resampleOffset = 0;
        this.leftoverCount = 0;
        this.leftoverSample = 0;
        this.outputCount = 0;
        this.sumSquare = 0;
        this.sampleCount = 0;
        this.paused = false;
      }
    };
  }

  process(inputs, outputs, parameters) {
    if (this.paused) {
      return true;
    }

    const input = inputs[0];
    if (!input || input.length === 0) {
      return true;
    }

    let channelData = input[0];
    if (!channelData || channelData.length === 0) {
      return true;
    }

    // Downmix multi-channel to mono if necessary
    if (input.length > 1) {
      const ch0 = input[0];
      const ch1 = input[1];
      const mono = new Float32Array(ch0.length);
      for (let i = 0; i < ch0.length; i++) {
        mono[i] = (ch0[i] + ch1[i]) * 0.5;
      }
      channelData = mono;
    }

    const len = channelData.length;

    // Calculate RMS from raw input samples
    for (let i = 0; i < len; i++) {
      const s = channelData[i];
      this.sumSquare += s * s;
    }
    this.sampleCount += len;

    if (this.sourceSampleRate === this.targetSampleRate) {
      for (let i = 0; i < len; i++) {
        const s = Math.max(-1, Math.min(1, channelData[i]));
        this.outputBuffer[this.outputCount++] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        if (this.outputCount >= this.bufferSize) {
          this._flush();
        }
      }
    } else {
      const neededLen = this.leftoverCount + len + 2;
      if (this.workBuffer.length < neededLen) {
        this.workBuffer = new Float32Array(neededLen);
      }

      if (this.leftoverCount > 0) {
        this.workBuffer[0] = this.leftoverSample;
        this.workBuffer.set(channelData, 1);
      } else {
        this.workBuffer.set(channelData, 0);
      }

      const total = this.leftoverCount + len;
      let pos = this.resampleOffset;
      const limit = total - 1;

      while (pos < limit) {
        const idx = Math.floor(pos);
        const frac = pos - idx;
        const s = this.workBuffer[idx] * (1 - frac) + this.workBuffer[idx + 1] * frac;
        const clamped = Math.max(-1, Math.min(1, s));
        this.outputBuffer[this.outputCount++] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7FFF;
        if (this.outputCount >= this.bufferSize) {
          this._flush();
        }
        pos += this.ratio;
      }

      this.leftoverSample = this.workBuffer[limit];
      this.leftoverCount = 1;
      this.resampleOffset = pos - limit;
    }

    return true;
  }

  _flush() {
    if (this.outputCount === 0) return;
    const rms = this.sampleCount > 0 ? Math.sqrt(this.sumSquare / this.sampleCount) : 0;
    const chunk = new Int16Array(this.outputCount);
    chunk.set(this.outputBuffer.subarray(0, this.outputCount));
    this.port.postMessage(
      {
        type: "audio",
        buffer: chunk.buffer,
        rms: rms,
      },
      [chunk.buffer]
    );

    this.outputCount = 0;
    this.sumSquare = 0;
    this.sampleCount = 0;
  }
}

registerProcessor("recorder-worklet-processor", AudioWorkletRecorderProcessor);
