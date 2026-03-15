class PCMWorkletProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        // The Jitter Buffer: Gemini loves stable, consistent chunks (e.g., 4096 frames = ~250ms of audio)
        this.bufferSize = 4096; 
        this.buffer = new Float32Array(this.bufferSize);
        this.bytesWritten = 0;
    }

    process(inputs, outputs, parameters) {
        const input = inputs[0];
        // If the mic is silent or disconnected, just keep the processor alive
        if (!input || input.length === 0) return true;

        const channelCount = input.length;
        const samples = input[0].length;

        // 1. Process and Downmix to Mono (Guarantees 1 channel regardless of hardware)
        for (let i = 0; i < samples; i++) {
            let sum = 0;
            for (let c = 0; c < channelCount; c++) {
                sum += input[c][i];
            }
            this.buffer[this.bytesWritten++] = sum / channelCount;

            // 2. When our buffer is full, encode and flush it to the backend
            if (this.bytesWritten >= this.bufferSize) {
                this.flush();
            }
        }
        
        return true; 
    }
    
    flush() {
        // Create an ArrayBuffer with exactly 2 bytes per sample (16-bit)
        const outBuffer = new ArrayBuffer(this.bufferSize * 2);
        
        // Use DataView to EXPLICITLY control the byte order (Endianness)
        const view = new DataView(outBuffer);

        for (let i = 0; i < this.bufferSize; i++) {
            // Clamp the audio signal to prevent clipping
            let s = Math.max(-1, Math.min(1, this.buffer[i]));
            
            // Convert to 16-bit PCM. 
            // The 'true' argument forces Little-Endian (s16le), guaranteeing Gemini accepts it.
            view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7FFF, true); 
        }

        // Send the perfectly formatted chunk to the main thread
        this.port.postMessage(outBuffer);
        
        // Reset the buffer counter
        this.bytesWritten = 0;
    }
}

registerProcessor('pcm-worker', PCMWorkletProcessor);