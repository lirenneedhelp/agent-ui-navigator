from google.genai import types
import asyncio

# ==========================================
# 1. TOOL DEFINITIONS (ReAct Pattern)
# ==========================================
analyze_tool = types.FunctionDeclaration(
    name="analyze_ui",
    description="Call this FIRST before clicking or typing. It draws numbered boxes on the screen and returns an annotated screenshot so you can safely identify the correct element_id.",
    parameters=types.Schema(
        type="OBJECT", 
        properties={
            "thought_process": types.Schema(
                type="STRING", 
                description="Briefly explain what you need to look for on the screen and why you are requesting a new screenshot."
            )
        },
        required=["thought_process"]
    )
)

click_tool = types.FunctionDeclaration(
    name="click_element",
    description="Clicks on a specific UI element based on its numeric ID.",
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "thought_process": types.Schema(
                type="STRING", 
                description="CRITICAL: Briefly state the user's goal, identify the element on the screenshot, and justify why this specific ID is the correct target."
            ),
            "element_id": types.Schema(type="INTEGER")
        },
        required=["thought_process", "element_id"]
    )
)

type_tool = types.FunctionDeclaration(
    name="type_text",
    description="Clicks on a specific input field using its numeric ID and types the text.",
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "thought_process": types.Schema(
                type="STRING", 
                description="CRITICAL: Explain what information you are inputting, identify the correct input field ID from the screenshot, and justify your choice."
            ),
            "element_id": types.Schema(type="INTEGER"),
            "text": types.Schema(type="STRING")
        },
        required=["thought_process", "element_id", "text"]
    )
)

scroll_tool = types.FunctionDeclaration(
    name="scroll_page",
    description="Scrolls the web page 'up' or 'down' to reveal hidden content.",
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "thought_process": types.Schema(
                type="STRING", 
                description="Explain why the current view is insufficient and why scrolling is necessary."
            ),
            "direction": types.Schema(type="STRING", description="'up' or 'down'")
        },
        required=["thought_process", "direction"]
    )
)

finish_tool = types.FunctionDeclaration(
    name="finish_task",
    description="Call this when you have successfully completed the user's requested task.",
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "summary": types.Schema(type="STRING", description="A summary of what you accomplished.")
        },
        required=["summary"]
    )
)

extract_tool = types.FunctionDeclaration(
    name="extract_page_text",
    description="Scrapes all readable text from the current page. Call this when you need to read dynamic data like flight prices, durations, or hotel rates to compare options and find the cheapest/best one.",
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "thought_process": types.Schema(
                type="STRING", 
                description="Explain what specific data (e.g., 'cheapest price') you are looking to extract from the page."
            )
        },
        required=["thought_process"]
    )
)

agent_tools = types.Tool(function_declarations=[analyze_tool, click_tool, type_tool, scroll_tool, extract_tool, finish_tool])

# ==========================================
# 2. PLAYWRIGHT EXECUTION FUNCTIONS
# ==========================================
async def execute_analyze_ui(page):
    """Draws the visual boxes, takes a screenshot, and instantly erases them."""
    elements_map = await page.evaluate("""
        () => {
            let counter = 1;
            let map = {};
            let drawnRects = []; 
            
            let interactables = document.querySelectorAll('button, a, input, select, textarea, [role="button"], [role="tab"], [role="radio"], [role="checkbox"], [role="option"], [role="menuitem"], [role="switch"], [tabindex]:not([tabindex="-1"], svg, [class*="close"], [aria-label*="close"])');
              
            interactables.forEach(el => {
                let rect = el.getBoundingClientRect();
                                       
                if (rect.width <= 5 || rect.height <= 5 || rect.top < 0 || rect.top > window.innerHeight) return; 
                if (rect.top < 80) return;   
                if (rect.left < 250) return; 
                if (el.closest('header, footer, nav, aside')) return; 
                
                let isInside = drawnRects.some(drawn => {
                    let completelyContained = (
                        rect.left >= drawn.left - 5 &&
                        rect.top >= drawn.top - 5 &&
                        rect.right <= drawn.right + 5 &&
                        rect.bottom <= drawn.bottom + 5
                    );
                    let isNotMassive = drawn.width < 800 && drawn.height < 600;
                    return completelyContained && isNotMassive;
                });

                if (isInside) return; 
                
                drawnRects.push({
                    left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom,
                    width: rect.width, height: rect.height
                });
                
                if (rect.width > 0 && rect.height > 0 && rect.top >= 0 && rect.top <= window.innerHeight) {
                    
                    let isSmallItem = rect.width < 60 && rect.height < 60;

                    if (!isSmallItem) {
                        let outline = document.createElement('div');
                        
                        // --- THE FIX: Anchor to the viewport glass ---
                        outline.style.position = 'fixed'; 
                        outline.style.left = rect.left + 'px';
                        outline.style.top = rect.top + 'px';
                        
                        outline.style.width = rect.width + 'px';
                        outline.style.height = rect.height + 'px';
                        outline.style.border = '2px dashed rgba(255, 0, 100, 0.8)'; 
                        outline.style.pointerEvents = 'none'; 
                        outline.style.zIndex = 9999;
                        outline.className = 'ai-som-label'; 
                        document.body.appendChild(outline);
                    }

                    let label = document.createElement('div');
                    label.innerText = counter;
                    
                    // --- THE FIX: Anchor to the viewport glass ---
                    label.style.position = 'fixed'; 
                    
                    if (isSmallItem) {
                        label.style.left = rect.left + 'px';
                        label.style.top = rect.top + 'px';
                        label.style.fontSize = '10px';  
                        label.style.padding = '1px 2px'; 
                        label.style.border = '1px solid #000000'; 
                    } else {
                        // Offset the larger labels slightly, no scroll logic needed
                        label.style.left = (rect.left - 10) + 'px';
                        label.style.top = (rect.top - 10) + 'px';
                        label.style.fontSize = '14px';  
                        label.style.padding = '1px 4px'; 
                        label.style.border = '2px solid #000000'; 
                    }
                    
                    label.style.backgroundColor = '#FFEB3B'; 
                    label.style.color = '#000000'; 
                    label.style.fontFamily = 'Consolas, Monaco, monospace'; 
                    label.style.fontWeight = '900'; 
                    label.style.borderRadius = '3px';
                    label.style.boxShadow = '0px 0px 3px 1px rgba(255, 255, 255, 0.9)'; 
                    label.style.zIndex = 10000;
                    label.style.pointerEvents = 'none';
                    label.className = 'ai-som-label';
                    document.body.appendChild(label);

                    // Math remains unchanged, as Playwright mouse uses viewport coordinates!
                    let centerX = rect.left + (rect.width / 2);
                    let centerY = rect.top + (rect.height / 2);

                    let elementText = el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
                    elementText = elementText.substring(0, 40).replace(/\\n/g, ' ').trim();

                    map[counter] = { 
                        x: centerX, 
                        y: centerY,
                        text: elementText 
                    };
                    counter++;
                }
            });
            return map;
        }
    """)
    
    PATH = "screenshots"
    image_bytes = await page.screenshot(path=f"{PATH}/marked.jpg", type="jpeg", quality=60)
    await page.evaluate("document.querySelectorAll('.ai-som-label').forEach(el => el.remove());")
        
    return image_bytes, elements_map

async def execute_click(page, element_id, elements_map):
    if element_id in elements_map:
        x, y = elements_map[element_id]['x'], elements_map[element_id]['y']
        await page.mouse.move(x, y, steps=10)
        await page.mouse.down()
        await page.mouse.up()
        await asyncio.sleep(1.5)
        return True
    return False

async def execute_type(page, element_id, text, elements_map):
    if element_id not in elements_map:
        print(f"❌ Type Failed: AI tried to type in box {element_id}, but it is not on the screen!")
        return False

    x, y = elements_map[element_id]['x'], elements_map[element_id]['y']
    
    await page.mouse.move(x, y, steps=10)
    await page.mouse.click(x, y, click_count=1)
    await asyncio.sleep(0.5)

    await page.keyboard.press("Control+A") 
    await page.keyboard.press("Meta+A")    
    await page.keyboard.press("Backspace")
    await asyncio.sleep(0.2)

    print(f"⌨️ Typing '{text}' into element {element_id}...")
    await page.keyboard.type(text, delay=100)
    await asyncio.sleep(1.5) 
    return True

async def execute_scroll(page, direction):
    if direction == "down":
        await page.evaluate("window.scrollBy(0, window.innerHeight * 0.8);")
    elif direction == "up":
        await page.evaluate("window.scrollBy(0, -window.innerHeight * 0.8);")
    await asyncio.sleep(1)
    return True

async def execute_extract_text(page):
    """
    Grabs the clean, rendered innerText of the page. 
    This perfectly preserves the visual reading order of prices and flight times!
    """
    import re
    
    text = await page.evaluate("document.body.innerText")
    
    # Clean up excessive blank lines to save token bandwidth
    clean_text = re.sub(r'\n+', '\n', text)
    
    # Return the first 10,000 characters (plenty to cover a full list of flight results)
    return clean_text[:10000]