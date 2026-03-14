import asyncio
import os
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket
from playwright.async_api import async_playwright, Page
from google import genai
from google.genai import types

from web_agent import agent_tools, execute_analyze_ui, execute_click, execute_extract_text, execute_type, execute_scroll
from playwright_stealth import Stealth 

load_dotenv()
client = genai.Client()
app = FastAPI()
PATH = "screenshots"
url_page = "https://sg.trip.com/?locale=en-sg"
os.makedirs(PATH, exist_ok=True)


class LiveAgentSession:
    """
    An Object-Oriented State Machine to manage the Gemini Live connection,
    the Playwright browser context, and the audio streams concurrently.
    """
    def __init__(self, websocket: WebSocket, page: Page, genai_client: genai.Client):
        self.ws = websocket
        self.page = page
        self.client = genai_client
        self.session = None  # Will hold the active Gemini Live session
        
        # --- STATE MANAGEMENT ---
        self.page_lock = asyncio.Lock()
        self.current_elements_map = {}
        self.active_tool_task = None

    async def run(self):
        """Initializes the connection and starts the dual-stream event loops."""
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            tools=[agent_tools],
            system_instruction=types.Content(parts=[
                types.Part.from_text(text="""
                You are an advanced Visual Co-Pilot and UI Navigator. Your core mission is to execute tasks on the screen on behalf of the user.

                CRITICAL MANDATE (ANTI-LAZINESS): You are the user's hands. NEVER instruct, ask, or expect the user to manually click, type, or navigate the website themselves. You must execute ALL physical actions for them using your tools. 

                INFORMATION GATHERING: If a form requires personal details or search parameters you do not have, DO NOT tell the user to fill the form. Instead, ask the user a conversational question to gather the missing data (e.g., "What city are you flying to?" or "What is your departure date?"), and then YOU must use the type_text tool to input their answer into the form.

                ACCESSIBILITY RULE: Adopt a 'Guided Interview' approach. Ask ONE simple, conversational question at a time to gather necessary form data. Do not overwhelm the user with a massive list of questions.

                TOOL RULES:
                1. Always call analyze_ui FIRST to get the latest screen context before taking any action. This ensures you understand the current layout and element IDs.
                2. Use the click_element tool to click on buttons, links, or dropdowns, using the exact element ID from the latest analyze_ui.
                3. Use the type_text tool to fill out forms using the exact element ID from the latest analyze_ui.
                4. If you need to see more of the page, use the scroll_page tool to scroll up or down.
                5. Use the extract_page_text tool when asked to find the cheapest option, compare prices, or read detailed search results. ONCE you have extracted the text and found the best option, you MUST call analyze_ui to visually locate the exact ID of the 'Select' or 'Book' button corresponding to that choice.
                
                AMBIGUITY RULE: If you type a search term (like a city) and the website provides a dropdown with multiple ambiguous options (e.g., multiple airports for one city), DO NOT GUESS. You must instantly stop, call finish_task, and ask the user which specific option they want. Never click randomly.

                CRITICAL INTERRUPTION RULE: You are interacting with the user in real-time. If the user interrupts you, tells you to stop, or changes their mind mid-task, IMMEDIATELY abandon your current plan and UI navigation. Do not finish filling out the current form. Listen to the new instruction, call analyze_ui to re-orient yourself, and execute the new request.
                
                STATE RESET RULE (GOAL VS. PLAN): You have a persistent memory of the user's overarching goals. If the user interrupts you to modify a detail (e.g., "Actually, fly to Bangkok instead"), RETAIN the primary goal (booking a flight) but IMMEDIATELY TRASH your previous sequence of planned clicks. The UI might have changed while you were interrupted. You must call analyze_ui to look at the current screen state and formulate a brand new click-path to achieve the modified goal.                  
                                                        
                STRICT VISUAL GROUNDING RULE: You are strictly forbidden from guessing element IDs. You may ONLY click or type into an ID number if you can physically see that exact number in a yellow box on the MOST RECENT screenshot. If you cannot find the right box, do not guess. Call the analyze_ui tool again to refresh your vision, or ask the user for clarification.
                
                DEMO BOUNDARY RULE: Your objective is strictly to find flights and reach the passenger details/checkout page. DO NOT under any circumstances attempt to input payment details or finalize the booking. When you reach the payment page, call finish_task and explicitly tell the user: "I have prepared your itinerary. You can now take over to enter your payment details and finalize the booking."
                                     
                CALENDAR HANDOFF RULE: Calendars are complex visual grids. When you need to select a travel date, DO NOT attempt to visually guess or click on the tiny calendar days. Instead, call the finish_task tool and say EXACTLY this: "I have opened the calendar. Please click on your specific departure and return dates, and tell me when you are ready to continue." Wait in silence for the user to select the dates and speak their next instruction.
                """)
            ])
        )

        async with self.client.aio.live.connect(model="gemini-live-2.5-flash-native-audio", config=config) as self.session:
            print("🟢 Gemini Live API Connected. Generating initial screen context...")
            await self.send_screen_context()

            # Run both listeners concurrently
            tasks = asyncio.gather(
                self.listen_to_user(),
                self.listen_to_gemini()
            )
            await tasks

    async def send_screen_context(self):
        """Safely captures a clean screenshot and streams it to the AI."""
        filename = f"{PATH}/clean.jpg"
        async with self.page_lock: 
            await self.page.screenshot(path=filename, type="jpeg", quality=40)

        with open(filename, "rb") as f:
            image_bytes = f.read()
        
        await self.session.send_realtime_input(
            media={"mime_type": "image/jpeg", "data": image_bytes}
        )
        print("📸 Sent updated screen context to AI.")

    async def listen_to_user(self):
        """Continuously pulls PCM audio from the frontend WebSocket and streams to Gemini."""
        try:
            while True:
                data = await self.ws.receive_bytes()
                await self.session.send_realtime_input(
                    media={"mime_type": "audio/pcm;rate=16000", "data": data}
                )
        except Exception as e:
            print("User disconnected.")
            await self.session.close()

    async def listen_to_gemini(self):
        """Processes AI audio responses, interruptions, and tool calls."""
        try:
            while True:
                async for response in self.session.receive():
                    server_content = getattr(response, 'server_content', None)
                    
                    # 1. HANDLE BARGE-IN INTERRUPTION
                    if server_content and getattr(server_content, 'interrupted', False):
                        print("🚨 AI INTERRUPTED BY USER!")
                        await self.ws.send_text('{"status": "ai_interrupted"}')
                        
                        if self.active_tool_task and not self.active_tool_task.done():
                            self.active_tool_task.cancel()
                        continue
                    
                    # 2. STREAM AI AUDIO TO BROWSER
                    if server_content and server_content.model_turn:
                        for part in server_content.model_turn.parts:
                            if part.inline_data and part.inline_data.data:
                                await self.ws.send_bytes(part.inline_data.data)

                                asyncio.create_task(self.page.evaluate("if(window.pulseAiOrb) window.pulseAiOrb()"))

                    # 3. HANDLE ASYNCHRONOUS TOOL CALLS
                    tool_call = getattr(response, 'tool_call', None)
                    if tool_call:
                        if self.active_tool_task and not self.active_tool_task.done():
                            self.active_tool_task.cancel()
                            
                        # Spawn the execution method in the background
                        self.active_tool_task = asyncio.create_task(self.execute_tool_call(tool_call))
                                
        except Exception as e:
            print(f"Agent listen loop error: {e}")

    async def execute_tool_call(self, tc):
        """The isolated execution environment for Playwright actions."""
        try:
            successful_responses = []

            for fc in tc.function_calls:
                print(f"🤖 AI called tool: {fc.name}")
                action_success = False
                requires_new_context = False 

                try:
                    if fc.name == "analyze_ui":
                        async with self.page_lock:
                            img_bytes, self.current_elements_map = await asyncio.wait_for(execute_analyze_ui(self.page), timeout=8.0)
                        
                        await self.session.send_realtime_input(media={"mime_type": "image/jpeg", "data": img_bytes})
                        action_success = True
                        
                    elif fc.name == "click_element":
                        eid = str(int(fc.args["element_id"]))
                        async with self.page_lock:
                            action_success = await asyncio.wait_for(execute_click(self.page, eid, self.current_elements_map), timeout=8.0)
                        requires_new_context = True 
                        
                    elif fc.name == "type_text":
                        eid = str(int(fc.args["element_id"]))
                        async with self.page_lock:
                            action_success = await asyncio.wait_for(execute_type(self.page, eid, fc.args["text"], self.current_elements_map), timeout=8.0)
                        requires_new_context = True 
                        
                    elif fc.name == "scroll_page":
                        async with self.page_lock:
                            action_success = await asyncio.wait_for(execute_scroll(self.page, fc.args["direction"]), timeout=8.0)
                        requires_new_context = True 
                    
                    elif fc.name == "extract_page_text":
                        async with self.page_lock:
                            # We store it in a variable so we can inject it into the response below!
                            extracted_text = await asyncio.wait_for(execute_extract_text(self.page), timeout=8.0)
                        action_success = True
                        requires_new_context = False # No need to take a new screenshot just for reading text

                    elif fc.name == "finish_task":
                        summary = fc.args["summary"]
                        print(f"\n✅ TASK COMPLETE: {summary}\n")
                        action_success = True
                    
                    await asyncio.sleep(0.1)

                except asyncio.TimeoutError:
                    print(f"⚠️ Tool {fc.name} timed out!")
                    action_success = False
                except asyncio.CancelledError:
                    raise # Kick this up to the outer try/except block
                except Exception as e:
                    print(f"⚠️ Tool {fc.name} encountered an error: {e}")
                    action_success = False

                # Format the response
                response_data = {"status": "success" if action_success else "failed"}

                if fc.name == "extract_page_text" and action_success:
                    response_data["extracted_text"] = extracted_text

                # Instead of just sending a list of numbers, we send a map of { "ID": "Text" }
                elif fc.name == "analyze_ui" and action_success:
                    semantic_map = {}
                    for k, v in self.current_elements_map.items():
                        # Only include the text if it actually found some
                        if v.get("text"):
                            semantic_map[str(k)] = v["text"]
                        else:
                            semantic_map[str(k)] = "Unknown Element"
                            
                    response_data["active_clickable_ids"] = semantic_map
                # ----------------------------------------------

                successful_responses.append(types.FunctionResponse(
                        name=fc.name,
                        id=fc.id, 
                        response=response_data
                ))
                
                # Update vision if the DOM mutated
                if action_success and requires_new_context:
                    self.current_elements_map = {} 
                    await asyncio.sleep(1) 
                    await self.send_screen_context()

            # Batch send responses
            if successful_responses:
                await self.session.send_tool_response(function_responses=successful_responses)

        except asyncio.CancelledError:
            print("\n🛑 TASK CANCELLED: User interrupted mid-action!\n")
            responses = []
            for fc in tc.function_calls:
                responses.append(types.FunctionResponse(
                    name=fc.name,
                    id=fc.id,
                    response={
                        "status": "fatal_interrupt", 
                        "system_directive": "USER HAS INTERRUPTED YOU MID-ACTION. YOU MUST YIELD YOUR TURN IMMEDIATELY. DO NOT ATTEMPT TO RECOVER. DO NOT CALL ANY MORE TOOLS. WAIT IN SILENCE FOR THE USER TO FINISH SPEAKING THEIR NEW INSTRUCTION."
                    }
                ))
            
            asyncio.create_task(self.session.send_tool_response(function_responses=responses))
            self.current_elements_map = {}
        
        except Exception as e:
            print(f"⚠️ Tool task error: {e}")


# ==========================================
# FASTAPI ENTRY POINT
# ==========================================
@app.websocket("/ws/stream")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("🎤 Frontend connected. Booting up browser...")

    async with Stealth().use_async(async_playwright()) as p:
        # NOTE: Verify this path matches your machine
        brave_path = "C:\\Program Files\\BraveSoftware\\Brave-Browser\\Application\\brave.exe"

        browser = await p.chromium.launch(
            executable_path=brave_path, 
            headless=False,
        )
        page = await browser.new_page(viewport={"width": 1280, "height": 720})
        
        print(f"🌐 Navigating to {url_page}...")
        await page.goto(url_page)

        print("🪄 Injecting AI Copilot Widget into the webpage...")
        await page.evaluate("""
            const orb = document.createElement('div');
            orb.id = 'ai-copilot-orb';
            // Styling it to float in the bottom-right corner over all content
            orb.style.cssText = 'position: fixed; bottom: 40px; right: 40px; width: 60px; height: 60px; border-radius: 50%; background: linear-gradient(135deg, #e8eaed, #dadce0); box-shadow: 0 0 20px rgba(0,0,0,0.2); z-index: 2147483647; transition: transform 0.1s linear, box-shadow 0.1s linear, background 0.3s ease; pointer-events: none;';
            document.body.appendChild(orb);

            // A self-resetting animation function we can trigger from Python!
            let orbTimeout;
            window.pulseAiOrb = function() {
                const el = document.getElementById('ai-copilot-orb');
                if (!el) return;
                
                // Turn on the Blue Gemini Glow
                el.style.transform = 'scale(1.3)';
                el.style.background = 'linear-gradient(135deg, #4285f4, #8ab4f8)';
                el.style.boxShadow = '0 0 30px rgba(66, 133, 244, 0.9)';
                
                // Automatically reset to idle after 300ms of silence
                clearTimeout(orbTimeout);
                orbTimeout = setTimeout(() => {
                    el.style.transform = 'scale(1)';
                    el.style.background = 'linear-gradient(135deg, #e8eaed, #dadce0)';
                    el.style.boxShadow = '0 0 20px rgba(0,0,0,0.2)';
                }, 300);
            };
        """)


        await asyncio.sleep(2) 

        # Instantiate our new State Machine and run it
        agent = LiveAgentSession(websocket=websocket, page=page, genai_client=client)
        await agent.run()