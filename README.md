## 🛠️ Spin-Up Instructions

Because Astra physically drives the browser using raw hardware-level execution, it **must** be run locally on your machine. We have automated the entire startup process into a single script.

### Prerequisites
* **Python 3.10+** installed.
* **Google Chrome** installed.
* A **Gemini API Key**.

**CRITICAL:** You must completely close/quit all existing instances of Chrome before running the startup script, otherwise the required debugging backdoor will fail to open.

### Step 1: Add Your API Key
1. Open the root folder of this repository.
2. If you are on **Windows**, right-click and edit `start_astra.bat`.
3. If you are on **Mac/Linux**, open `start_astra.sh` in a text editor.
4. Find the line that says `your_api_key_here` and replace it with your actual Gemini API key. Save the file.

> **Note on Vertex AI:** If you are testing this using a standard Gemini API key, please ensure `GOOGLE_GENAI_USE_VERTEXAI=true` is **commented out or removed** from the `.env` file, otherwise the SDK will look for local Google Cloud credentials instead of your API key!

### Step 2: One-Click Startup (The Brain & Hands)
Run the script you just edited. 

* **Windows:** Double-click `start_astra.bat`.
* **Mac/Linux:** Open terminal, type `chmod +x start_astra.sh` (to make it executable), and then run `./start_astra.sh`.

**What this script does automatically:**
1. Creates a Python virtual environment.
2. Installs all required dependencies.
3. Launches a fresh, remote-debug enabled instance of Google Chrome.
4. Starts the FastAPI WebSocket backend.

### Step 3: Install the Extension (The Ears)
1. In your newly opened debug-mode Chrome browser, navigate to `chrome://extensions/`.
2. Turn on **Developer mode** in the top right corner.
3. Click **Load unpacked** in the top left.
4. Select the `extension/` folder located inside this repository.
5. A setup page will automatically open. Click **Allow Microphone** to grant the extension permission to hear you.

### Step 4: The Test Flight
You are ready to fly.
1. Navigate to a complex website (we recommend **Trip.com**).
2. Click the Astra extension icon in your Chrome toolbar.
3. The 3D Siri orb will appear in the bottom right corner, indicating the voice-link is active.
4. Speak naturally (e.g., *"Astra, find me the cheapest direct flight from Singapore to Tokyo for next week"*). 
5. Take your hands off the mouse and watch Astra drive.
