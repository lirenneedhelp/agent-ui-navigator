let websocket;
let audioContext;
let nextPlayTime = 0;

let aiAnalyser;
let dataArrayAi;
let activeSources = []; 

// Connect to FastAPI immediately when the offscreen document is created
function startSession() {
    audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });

    aiAnalyser = audioContext.createAnalyser();
    aiAnalyser.fftSize = 256;
    dataArrayAi = new Uint8Array(aiAnalyser.frequencyBinCount);
    aiAnalyser.connect(audioContext.destination); 

    // Start blasting the volume data to the active tab 20x a second
    setInterval(() => {
        if (!aiAnalyser) return;
        aiAnalyser.getByteFrequencyData(dataArrayAi);
        let sum = 0;
        for (let i = 0; i < dataArrayAi.length; i++) sum += dataArrayAi[i];
        let aiVolume = sum / dataArrayAi.length;

        chrome.runtime.sendMessage({
            action: "FORWARD_TO_TAB",
            payload: { action: "UPDATE_ORB_VOLUME", volume: aiVolume }
        });
    }, 50);

    websocket = new WebSocket("ws://localhost:8000/ws/stream");
    websocket.binaryType = "arraybuffer";

    websocket.onopen = async () => {
        console.log("🎤 Offscreen Engine Connected. Starting Mic...");
        await startMicrophone();
    };

    websocket.onmessage = async (event) => {
        if (typeof event.data === "string") {
            const msg = JSON.parse(event.data);
            
            if (msg.status === "ai_interrupted") {
                console.log("🛑 Interruption received. Flushing audio queue.");
                activeSources.forEach(source => {
                    try { source.stop(); } catch(e) {}
                });
                activeSources = [];
                if (audioContext) nextPlayTime = audioContext.currentTime;
                return; 
            }

            // Forward the volume updates to the orb UI
            chrome.runtime.sendMessage({ action: "FORWARD_TO_TAB", payload: msg });
            
        } else {
            // It's binary audio data, play Astra's voice!
            playAudioChunk(event.data);
        }
    };
}

async function startMicrophone() {
    console.log("🎙️ 1. startMicrophone() triggered.");
    try {
        
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        
        console.log("✅ 2. Microphone stream acquired! Active:", stream.active);
        
        // Force the audio context to wake up just in case Chrome suspended it
        if (audioContext.state === 'suspended') {
            console.log("⚠️ AudioContext is suspended. Waking it up...");
            await audioContext.resume();
        }
        console.log("🔊 3. AudioContext state:", audioContext.state);

        const source = audioContext.createMediaStreamSource(stream);
        await audioContext.audioWorklet.addModule(`pcm-worker.js?t=${Date.now()}`);
        const workletNode = new AudioWorkletNode(audioContext, 'pcm-worker');

        console.log("🌐 4. WebSocket status before sending:", websocket ? websocket.readyState : "WEBSOCKET IS NULL");

        workletNode.port.onmessage = (event) => {
            const pcm16Buffer = event.data;
            if (websocket && websocket.readyState === WebSocket.OPEN && pcm16Buffer.byteLength > 0) {
                websocket.send(pcm16Buffer); 
            } else if (!websocket || websocket.readyState !== WebSocket.OPEN) {
                // If the socket is dead, flood the console so we know!
                console.warn("🚫 Audio dropping! WebSocket is not OPEN. ReadyState:", websocket ? websocket.readyState : "NULL");
            }
        };

        source.connect(workletNode);
        workletNode.connect(audioContext.destination);
        
        console.log("🚀 5. Audio routing complete. Listening...");
    } catch (err) {
        console.error("Microphone Error:", err);
        if (err.name === 'NotAllowedError' || err.message.includes('dismissed')) {
            console.log("🔄 Triggering setup page...");
            chrome.runtime.sendMessage({ action: "OPEN_SETUP_PAGE" });
        }
    }
}

function playAudioChunk(arrayBuffer) {
    if (!audioContext) return;
    if (audioContext.state === 'suspended') audioContext.resume();

    let safeBuffer = arrayBuffer;
    if (safeBuffer.byteLength % 2 !== 0) safeBuffer = safeBuffer.slice(0, safeBuffer.byteLength - 1);
    if (safeBuffer.byteLength === 0) return;

    const pcm16Data = new Int16Array(safeBuffer);
    const float32Data = new Float32Array(pcm16Data.length);
    for (let i = 0; i < pcm16Data.length; i++) float32Data[i] = pcm16Data[i] / 32768.0;

    const audioBuffer = audioContext.createBuffer(1, float32Data.length, 24000);
    audioBuffer.copyToChannel(float32Data, 0);

    const source = audioContext.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(aiAnalyser);

    activeSources.push(source);
    source.onended = () => {
        activeSources = activeSources.filter(s => s !== source);
    };

    const currentTime = audioContext.currentTime;
    if (nextPlayTime < currentTime) nextPlayTime = currentTime + 0.05;

    source.start(nextPlayTime);
    nextPlayTime += audioBuffer.duration;
}

// Start immediately
startSession();