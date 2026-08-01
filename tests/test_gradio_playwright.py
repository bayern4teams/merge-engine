import re
from playwright.sync_api import Page, expect

def test_gradio_ui_loads(page: Page):
    # Navigate to the local Gradio app
    page.goto("http://127.0.0.1:7860")
    
    # Wait for the main container to load
    page.wait_for_selector(".gradio-container")
    
    # Verify the title/header exists
    expect(page.locator("text=Merge Engine")).to_be_visible()
    
    # Check if the Setup and Pipeline tabs exist by role
    expect(page.get_by_role("tab", name="⚙️ Configuration Setup")).to_be_visible()
    expect(page.get_by_role("tab", name="🚀 Merge Pipeline")).to_be_visible()

    # Navigate to Pipeline tab
    page.get_by_role("tab", name="🚀 Merge Pipeline").click()
    
    # Verify interface exists (check for the text input)
    expect(page.get_by_role("textbox", name="Your Request")).to_be_visible()
