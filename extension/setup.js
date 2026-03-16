document.getElementById('auth-btn').addEventListener('click', async () => {
    const statusText = document.getElementById('status');
    const btn = document.getElementById('auth-btn');
    
    try {
        // Request the microphone
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        
        // If successful, update UI
        btn.style.display = 'none';
        statusText.innerText = "✅ Voice Link Established. Closing setup...";
        statusText.style.color = "#3fb950";
        
        // Instantly release the microphone so we don't keep the red recording dot on needlessly
        stream.getTracks().forEach(track => track.stop());
        
        // Auto-close the tab after 1.5 seconds so the judge can read the success message
        setTimeout(() => {
            window.close();
        }, 1500);

    } catch (err) {
        // If they deny it or there is an error
        statusText.innerText = "❌ Microphone access denied. Astra cannot hear you.";
        statusText.style.color = "#f85149"; // Error red
    }
});