document.getElementById('authBtn').addEventListener('click', async () => {
    try {
        // This triggers the native Chrome "Allow Microphone" popup!
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        
        // Turn off the hardware light immediately after getting permission
        stream.getTracks().forEach(track => track.stop());
        
        // Update the UI
        document.body.innerHTML = `
            <h2>✅ Microphone Granted!</h2>
            <p style="font-size: 18px;">You can safely close this tab.<br><br>Click the <b>Gemini Extension Icon</b> in your toolbar to start the Copilot.</p>
        `;
    } catch (err) {
        alert("Error getting permission: " + err.message);
    }
});