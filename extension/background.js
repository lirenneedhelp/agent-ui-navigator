function injectSiriOrb(tabId) {
    chrome.scripting.executeScript({
        target: { tabId: tabId },
        func: () => {
            if (document.getElementById('gemini-siri-orb')) return;
            
            const style = document.createElement('style');
            style.innerHTML = `
                #gemini-siri-orb {
                    position: fixed;
                    bottom: 40px;
                    right: 40px;
                    width: 80px;
                    height: 80px;
                    border-radius: 50%;
                    background: radial-gradient(circle at 30% 30%, #ffffff 0%, #4285f4 20%, #1967d2 45%, #8e24aa 75%, #311b92 95%);
                    background-size: 200% 200%;
                    box-shadow: 
                        inset -10px -10px 20px rgba(0, 0, 0, 0.7),
                        inset 10px 10px 20px rgba(255, 255, 255, 0.8),
                        0 0 25px rgba(0, 225, 255, 0.8);
                    animation: siriSpin 5s linear infinite;
                    z-index: 2147483647;
                    pointer-events: none;
                    transition: transform 0.05s linear, box-shadow 0.05s linear;
                }

                #gemini-siri-orb::after {
                    content: '';
                    position: absolute;
                    top: 8px;
                    left: 14px;
                    width: 44px;
                    height: 22px;
                    border-radius: 50%;
                    background: linear-gradient(to bottom, rgba(255,255,255,0.9), rgba(255,255,255,0.05));
                    transform: rotate(-25deg);
                    pointer-events: none;
                }
                
                @keyframes siriSpin {
                    0% { background-position: 0% 50%; }
                    50% { background-position: 100% 50%; }
                    100% { background-position: 0% 50%; }
                }
            `;
            document.head.appendChild(style);
            
            const orb = document.createElement('div');
            orb.id = 'gemini-siri-orb';
            document.body.appendChild(orb);

            if (!window.orbListenerActive) {
                window.orbListenerActive = true;
                chrome.runtime.onMessage.addListener((msg) => {
                    if (msg.action === "UPDATE_ORB_VOLUME") {
                        const orbEl = document.getElementById('gemini-siri-orb');
                        if (orbEl) {
                            let vol = msg.volume;
                            if (vol > 2) { 
                                let scale = 1 + (vol / 255) * 0.6; 
                                orbEl.style.transform = `scale(${scale})`;
                                orbEl.style.boxShadow = `inset -10px -10px 20px rgba(0,0,0,0.7), inset 10px 10px 20px rgba(255,255,255,0.8), 0 0 ${30 + (vol * 1.5)}px rgba(0, 225, 255, 1)`;
                            } else { 
                                orbEl.style.transform = `scale(1)`;
                                orbEl.style.boxShadow = `inset -10px -10px 20px rgba(0,0,0,0.7), inset 10px 10px 20px rgba(255,255,255,0.8), 0 0 25px rgba(0, 225, 255, 0.8)`;
                            }
                        }
                    }
                });
            }
        }
    }).catch(err => console.log("Silently ignoring restricted page injection."));
}

// 2. Trigger on Extension Icon Click
chrome.action.onClicked.addListener(async (tab) => {
    const existingContexts = await chrome.runtime.getContexts({
        contextTypes: ['OFFSCREEN_DOCUMENT'],
        documentUrls: [chrome.runtime.getURL('offscreen.html')]
    });

    if (existingContexts.length === 0) {
        await chrome.offscreen.createDocument({
            url: 'offscreen.html',
            reasons: ['USER_MEDIA', 'AUDIO_PLAYBACK'],
            justification: 'Recording and playing audio for the AI copilot'
        });
    }
    
    // Inject immediately on click
    injectSiriOrb(tab.id);
});

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
    // Wait for the new page to finish loading, and ensure it's a real website (not a chrome:// settings page)
    if (changeInfo.status === 'complete' && tab.url && tab.url.startsWith('http')) {
        
        // Check if our offscreen audio engine is currently running
        const existingContexts = await chrome.runtime.getContexts({
            contextTypes: ['OFFSCREEN_DOCUMENT'],
            documentUrls: [chrome.runtime.getURL('offscreen.html')]
        });

        // If the AI is awake, automatically respawn the orb!
        if (existingContexts.length > 0) {
            injectSiriOrb(tabId);
        }
    }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    
    if (message.action === "OPEN_SETUP_PAGE") {
        chrome.tabs.create({ url: chrome.runtime.getURL("setup.html") }); 
        sendResponse({ status: "opened" }); // ✅ THE FIX: We fulfill the promise!
        return true; 
    }

    if (message.action === "FORWARD_TO_TAB") {
        chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
            if (tabs && tabs[0]) {
                chrome.tabs.sendMessage(tabs[0].id, message.payload, (tabResponse) => {
                    // Catch empty tabs and disconnected content scripts!
                    if (chrome.runtime.lastError) {
                        sendResponse({ status: "failed", error: chrome.runtime.lastError.message });
                    } else {
                        sendResponse(tabResponse || { status: "success" });
                    }
                });
            } else {
                sendResponse({ status: "failed", error: "No active web tab found." });
            }
        });
        return true; 
    }
});

chrome.runtime.onInstalled.addListener(({ reason }) => {
    if (reason === 'install') {
        chrome.tabs.create({ url: 'setup.html' });
    }
});