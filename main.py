import asyncio
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types
from playwright.async_api import async_playwright

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
                Your Name is Astra. You are an advanced Visual Co-Pilot. You have direct control over the user's active browser tab.
                
                1. Always call analyze_ui FIRST before you perform any tool calls.
                2. Use click_element and type_text to navigate. 
                
                COMPLEX UI NAVIGATION & ANTI-BATCHING RULE:
                Modern travel websites use "fake inputs" for city and date selectors. If you see a box that says "Leaving from", "Going to", or "Destination", DO NOT attempt to use the type_text tool on it directly. It is a button, not a text field.

                Furthermore, you are STRICTLY FORBIDDEN from batching multiple tools together in a single turn when interacting with these elements. You MUST execute this exact sequence, stopping and waiting for a tool response after EACH step:

                1. Call click_element on the fake search box. STOP AND WAIT. (This triggers the dropdown modal).
                2. Call analyze_ui to refresh your vision and see the new modal. STOP AND WAIT.
                3. Find the ID of the TRUE text input field inside the new modal, and call type_text on that new ID. STOP AND WAIT.
                4. Call analyze_ui again to see the autocomplete dropdown list. STOP AND WAIT.
                5. Call click_element on the correct city from the dropdown.
                
                🛑 ANTI-LOOP & ERROR RECOVERY PROTOCOL (CRITICAL!):
                - If you call a tool and receive a "ai_interrupted" or "failed" status, DO NOT attempt to call the exact same tool with the exact same ID again.
                - If the page behaves unexpectedly, or you cannot find the element you need after calling analyze_ui, STOP IMMEDIATELY. 
                - Do NOT guess IDs. Do NOT spam tool calls. 
                - If you are stuck, simply speak to the user using audio, explain what is blocking you (e.g., "I can't find the search button"), and ask them how they want to proceed.
                                     
                If the user says "stop", "wait", or interrupts you, IMMEDIATELY halt your current plan, do not call any tools, and ask the user what they would like to do next.                 
                """)
            ])
        )
        
        try:
            async with self.client.aio.live.connect(model="gemini-live-2.5-flash-native-audio", config=config) as self.session:
                print("🟢 Gemini Live API Connected. Waiting for voice commands...")

                ext_task = asyncio.create_task(self.listen_to_extension())
                gemini_task = asyncio.create_task(self.listen_to_gemini())

                # If either the extension drops OR Gemini drops, kill the session
                await asyncio.wait(
                    [ext_task, gemini_task], 
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                print("🛑 Session ended. Shutting down active tasks...")
                ext_task.cancel()
                gemini_task.cancel()
                
        finally:
            # Prevent the memory leak! Close Playwright gracefully.
            if self.playwright:
                await self.playwright.stop()
                print("🧹 Playwright instance cleaned up.")

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
                    
                    # 1. HARDWARE KILL SWITCH (Modified: Do NOT cancel mouse)
                    tool_cancellation = getattr(response, 'tool_call_cancellation', None)
                    if tool_cancellation:
                        print(f"🛑 AI attempted to cancel tool! (Ignored to protect physical mouse execution)")
                        await self.ws.send_json({"status": "ai_interrupted"}) 
                        continue

                    server_content = getattr(response, 'server_content', None)
                    
                    # 2. AUDIO KILL SWITCH (Modified: Do NOT cancel mouse)
                    if server_content and getattr(server_content, 'interrupted', False):
                        print("🛑 Audio Barge-in detected! Flushing audio queue, but letting mouse finish...")
                        await self.ws.send_json({"status": "ai_interrupted"})
                        continue 
                    
                    # 3. Audio Streaming
                    if server_content and server_content.model_turn:
                        for part in server_content.model_turn.parts:
                            if part.inline_data and part.inline_data.data:
                                await self.ws.send_bytes(part.inline_data.data)

                    # 4. BACKGROUND TOOL EXECUTION (The Execution Lock)
                    tool_call = getattr(response, 'tool_call', None)
                    if tool_call:
                        # THE FIX: If a tool is currently running, REJECT overlapping calls
                        if self.active_tool_task and not self.active_tool_task.done():
                            print("⚠️ Gemini is impatient! Rejecting overlapping call to protect current execution.")
                            busy_responses = []
                            for fc in tool_call.function_calls:
                                busy_responses.append(types.FunctionResponse(
                                    name=fc.name,
                                    id=fc.id,
                                    response={"status": "failed", "error": "SYSTEM WARNING: I am currently executing your previous command. Wait for the screenshot before sending new commands."}
                                ))
                            await self.session.send_tool_response(function_responses=busy_responses)
                            continue
                        
                        # Otherwise, safely start the hardware execution
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
                        if success:
                            # Remind the AI that the page changed!
                            response_data = {"status": "success", "message": "Click successful. The page UI has likely changed. You MUST call analyze_ui to refresh your vision before clicking anything else."}
                        else: 
                            response_data = {"status": "failed", "error": f"CRITICAL ERROR: Box ID {target_id} is not valid or no longer on screen. You are acting on stale visual data. You MUST call analyze_ui immediately to get fresh IDs."}

                    elif fc.name == "type_text":
                        target_id = str(int(args_dict["element_id"]))
                        success = await execute_type(self.page, target_id, args_dict["text"], self.elements_map)
                        if success:
                            response_data = {"status": "success", "message": "Text typed successfully. The page UI has likely changed. Call analyze_ui to see the new autocomplete dropdowns."}
                        else: 
                            response_data = {"status": "failed", "error": f"CRITICAL ERROR: Box ID {target_id} is not valid. The UI has changed. Call analyze_ui to refresh your vision."}

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
            async def cleanup_and_abort():
                # 1. Force Gemini to drop the current thought process
                abort_responses = []
                for fc in tc.function_calls:
                    abort_responses.append(types.FunctionResponse(
                        name=fc.name,
                        id=fc.id,
                        response={"status": "ai_interrupted", "message": "CRITICAL: User interrupted the action. STOP your current task immediately and listen to the new voice command."}
                    ))
                
                if abort_responses:
                    try:
                        await self.session.send_tool_response(function_responses=abort_responses)
                    except Exception as e:
                        pass # Ignore if session is already closed
                        
                # 2. Wipe any yellow boxes that got stuck on the screen if analyze_ui was killed
                try:
                    await self.page.evaluate("document.querySelectorAll('.ai-som-label').forEach(el => el.remove());")
                except Exception:
                    pass

            # Fire and forget the cleanup task
            asyncio.create_task(cleanup_and_abort())
            return # Safely exit without sending a tool response, the turn is dead.

@app.websocket("/ws/stream")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("🔌 Voice Extension Connected!")
    
    agent = HybridAgentSession(websocket=websocket, genai_client=client)
    await agent.run()