import asyncio
import base64
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types
from playwright.async_api import async_playwright

# THE FIX: Imported the missing scroll and extract tools
from web_agent import agent_tools, execute_click, execute_type, execute_analyze_ui, execute_scroll, execute_extract_text 

load_dotenv()
client = genai.Client()
app = FastAPI()

class HybridAgentSession:
    def __init__(self, websocket: WebSocket, genai_client: genai.Client):
        self.ws = websocket
        self.client = genai_client
        self.session = None  
        self.playwright = None
        self.browser = None
        self.page = None
        
        self.elements_map = {} 
        self.active_tool_task = None

    async def setup_browser(self):
        self.playwright = await async_playwright().start()
        try:
            self.browser = await self.playwright.chromium.connect_over_cdp("http://localhost:9222")
            context = self.browser.contexts[0]
            self.page = context.pages[0] 
            print(f"🌐 Connected to live browser tab: {await self.page.title()}")
        except Exception as e:
            print("🚨 ERROR: Could not connect to Chrome. Is it running in debug mode on port 9222?")
            raise e

    async def run(self):
        await self.setup_browser()

        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            tools=[agent_tools],
            system_instruction=types.Content(parts=[
                types.Part.from_text(text="""
                You are an advanced Visual Co-Pilot. You have direct control over the user's active browser tab.
                
                1. Always call analyze_ui FIRST to get the screen context.
                2. Use click_element and type_text to navigate. 
                
                COMPLEX UI NAVIGATION & ANTI-BATCHING RULE:
                Modern travel websites use "fake inputs" for city and date selectors. If you see a box that says "Leaving from", "Going to", or "Destination", DO NOT attempt to use the type_text tool on it directly. It is a button, not a text field.

                Furthermore, you are STRICTLY FORBIDDEN from batching multiple tools together in a single turn when interacting with these elements. You MUST execute this exact sequence, stopping and waiting for a tool response after EACH step:

                1. Call click_element on the fake search box. STOP AND WAIT. (This triggers the dropdown modal).
                2. Call analyze_ui to refresh your vision and see the new modal. STOP AND WAIT.
                3. Find the ID of the TRUE text input field inside the new modal, and call type_text on that new ID. STOP AND WAIT.
                4. Call analyze_ui again to see the autocomplete dropdown list. STOP AND WAIT.
                5. Call click_element on the correct city from the dropdown.
                """)
            ])
        )
        
        async with self.client.aio.live.connect(model="gemini-live-2.5-flash-native-audio", config=config) as self.session:
            print("🟢 Gemini Live API Connected. Waiting for voice commands...")

            tasks = asyncio.gather(
                self.listen_to_extension(),
                self.listen_to_gemini()
            )
            await tasks

    async def listen_to_extension(self):
        try:
            while True:
                message = await self.ws.receive()
                if "bytes" in message:
                    await self.session.send_realtime_input(
                        media={"mime_type": "audio/pcm;rate=16000", "data": message["bytes"]}
                    )
        except WebSocketDisconnect:
            print("🔴 Extension Walkie-Talkie disconnected.")

    async def listen_to_gemini(self):
        try:
            while True:
                async for response in self.session.receive():
                    
                    # 1. HARDWARE KILL SWITCH: Tool Call Cancellation
                    tool_cancellation = getattr(response, 'tool_call_cancellation', None)
                    if tool_cancellation:
                        print(f"🛑 AI cancelled pending tool calls!")
                        await self.ws.send_json({"status": "ai_interrupted"}) # Flush audio just in case
                        
                        if self.active_tool_task and not self.active_tool_task.done():
                            self.active_tool_task.cancel()
                            print("🛑 Aborted ongoing Playwright action due to AI tool cancellation!")
                        continue

                    server_content = getattr(response, 'server_content', None)
                    
                    # 2. AUDIO KILL SWITCH: Speech Interruption
                    if server_content and getattr(server_content, 'interrupted', False):
                        print("🛑 AI Interrupted by user! Flushing audio queue...")
                        await self.ws.send_json({"status": "ai_interrupted"})
                        
                        if self.active_tool_task and not self.active_tool_task.done():
                            self.active_tool_task.cancel()
                            print("🛑 Aborted ongoing Playwright action due to speech interruption!")
                        continue 
                    
                    # 3. Audio Streaming
                    if server_content and server_content.model_turn:
                        for part in server_content.model_turn.parts:
                            if part.inline_data and part.inline_data.data:
                                await self.ws.send_bytes(part.inline_data.data)

                    # 4. BACKGROUND TOOL EXECUTION
                    tool_call = getattr(response, 'tool_call', None)
                    if tool_call:
                        # Only start a new task if we aren't already running one, or cancel the old one safely
                        if self.active_tool_task and not self.active_tool_task.done():
                             self.active_tool_task.cancel()
                        
                        self.active_tool_task = asyncio.create_task(self.execute_tool_call(tool_call))
                                
        except Exception as e:
            print(f"Agent listen loop error: {e}")

    async def execute_tool_call(self, tc):
        try:
            successful_responses = []

            for fc in tc.function_calls:
                print(f"\n🤖 AI executing hardware command: {fc.name}")
                args_dict = type(fc.args).to_dict(fc.args) if hasattr(fc.args, 'to_dict') else dict(fc.args) if fc.args else {}
                response_data = {"status": "success"}

                try:
                    if fc.name == "analyze_ui":
                        annotated_image_bytes, self.elements_map = await execute_analyze_ui(self.page)
                        clean_semantic_map = {str(k): v["text"] for k, v in self.elements_map.items()}
                        response_data["active_clickable_ids"] = clean_semantic_map
                        await self.session.send_realtime_input(media={"mime_type": "image/jpeg", "data": annotated_image_bytes})
                        print("📸 Sent ANNOTATED browser screenshot to Gemini.")

                    elif fc.name == "click_element":
                        target_id = str(int(args_dict["element_id"]))
                        success = await execute_click(self.page, target_id, self.elements_map)
                        if not success: response_data = {"status": "failed", "error": "ID not found"}

                    elif fc.name == "type_text":
                        target_id = str(int(args_dict["element_id"]))
                        success = await execute_type(self.page, target_id, args_dict["text"], self.elements_map)
                        if not success: response_data = {"status": "failed", "error": "ID not found"}

                    elif fc.name == "scroll_page":
                        await execute_scroll(self.page, args_dict["direction"])

                    elif fc.name == "extract_page_text":
                        extracted_text = await execute_extract_text(self.page)
                        response_data["extracted_text"] = extracted_text

                    await self.page.wait_for_timeout(1000)

                    if fc.name != "analyze_ui":
                        screenshot_bytes = await self.page.screenshot(type="jpeg", quality=60)
                        await self.session.send_realtime_input(media={"mime_type": "image/jpeg", "data": screenshot_bytes})
                        print("📸 Sent fresh browser screenshot to Gemini.")

                except asyncio.CancelledError:
                    # Reraise it so the outer block catches it!
                    raise
                except Exception as e:
                    print(f"⚠️ Playwright execution failed: {e}")
                    response_data = {"status": "failed", "error": str(e)}

                successful_responses.append(types.FunctionResponse(
                    name=fc.name,
                    id=fc.id, 
                    response=response_data
                ))

            if successful_responses:
                await self.session.send_tool_response(function_responses=successful_responses)

        # --- NEW: Catch the Kill Switch ---
        except asyncio.CancelledError:
            print("🛑 Playwright task was killed mid-execution by user interruption.")
            return # Safely exit without sending a tool response, the turn is dead.

@app.websocket("/ws/stream")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("🔌 Voice Extension Connected!")
    
    agent = HybridAgentSession(websocket=websocket, genai_client=client)
    await agent.run()