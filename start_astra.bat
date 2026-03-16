set GEMINI_API_KEY=your_api_key_here

REM --- 1. Python Environment Setup ---
echo [1/4] Checking Python Virtual Environment...
if not exist .gla\Scripts\activate (
    echo Creating virtual environment...
    python -m venv .gla
)
call .gla\Scripts\activate

echo [2/4] Installing/Verifying Dependencies...
pip install -q -r requirements.txt

REM --- 2. Launch Chrome ---
echo [3/4] Launching Chrome in Debug Mode...
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\chrome-debug"

REM --- 3. Start Backend ---
echo [4/4] Starting Astra Backend Server...
uvicorn main:app --port 8000