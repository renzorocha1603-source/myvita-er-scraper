from playwright.sync_api import sync_playwright
import time
import random
import os
import json
import re
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, messaging, firestore

# ==================== CONFIG ====================
ZONES = { ... }  # Keep your zones dictionary

# === Firebase Setup (keep your existing code) ===
db = None
# ... (your firebase init code) ...

# ==================== UTILITIES ====================
def human_delay(min_sec=0.8, max_sec=2.5):
    time.sleep(random.uniform(min_sec, max_sec))

def get_zone(postal_code: str) -> str:
    fsa = postal_code[:3].upper()
    return ZONES.get(fsa, f"zone_{fsa}")

# Keep your send_notification and save_availability functions

# ==================== MAIN FUNCTION ====================
def check_availability(postal_code_override=None):
    postal_code = postal_code_override or os.getenv("POSTAL_CODE", "H1Y3H1").replace(" ", "")
    
    print(f"🚀 Starting Clic Santé check for {postal_code}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        try:
            page.goto("https://portal3.clicsante.ca/services/blood-test", 
                     wait_until="networkidle", timeout=45000)

            # Select No fees
            for text in ["No fees", "Sans frais"]:
                try:
                    page.get_by_text(text, exact=True).click(timeout=5000)
                    break
                except:
                    continue

            # Enter postal code
            try:
                postal_input = page.get_by_placeholder("ex. A1A 1A1")
                postal_input.fill(postal_code)
            except:
                page.locator("input[type='text']").first.fill(postal_code)

            human_delay(1, 2)

            # Click Search
            page.get_by_role("button", name=re.compile(r"Search|Rechercher", re.I)).first.click()

            print("⏳ Waiting for clinics list (results page)...")
            human_delay(7, 12)  # Important wait

            # Ensure results loaded
            try:
                page.wait_for_selector("article, .establishment-card, [class*='clinic'], [class*='result']", 
                                     timeout=18000)
            except:
                pass

            # === THIS IS THE KEY URL ===
            results_url = page.url
            print(f"📍 Results Page URL captured: {results_url[:130]}...")

            # Take screenshot for debugging
            try:
                page.screenshot(path=f"results_{postal_code}_{datetime.now().strftime('%H%M')}.png")
            except:
                pass

            # Check if there are visible clinics / availability
            body_text = page.inner_text("body").lower()
            has_slots = any(word in body_text for word in 
                          ["disponible", "available", "réservation", "book", "places", "à venir"])

            if has_slots:
                print("🎉 Slots appear to be available on results page!")
                send_notification(postal_code, results_url, True)
                save_availability(postal_code, True, results_url, "Results page with clinics listed")
            else:
                print("❌ No obvious slots on results page")
                save_availability(postal_code, False, results_url, "No slots visible")

            return has_slots

        except Exception as e:
            print(f"Error: {e}")
            return False
        finally:
            browser.close()


if __name__ == "__main__":
    test_codes = ["H1Y3H1"]   # Add more if you want
    
    for code in test_codes:
        check_availability(code)
        time.sleep(5)
