from playwright.sync_api import sync_playwright, TimeoutError
import time
import random
import os
import json
import re
import requests
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, messaging, firestore

# === 1. CONFIGURATION & MAPPING ===
ZONES = {
    "H1Y": "montreal_east", "H1A": "montreal_east", "H1B": "montreal_east",
    "H1C": "montreal_east", "H1H": "montreal_north", "H1J": "montreal_north",
    "H2X": "montreal_central", "H3A": "montreal_central", "H3B": "montreal_central",
    "H4L": "montreal_north", "H4M": "montreal_north",
    "G1R": "quebec_central", "G1S": "quebec_central", "G1V": "quebec_ste_foy",
    "J8Y": "gatineau_hull", "J8Z": "gatineau_aylmer",
    "J1H": "sherbrooke", "J1K": "sherbrooke",
}

# === 2. FIREBASE SETUP ===
FIREBASE_CRED = os.getenv("FIREBASE_CRED_PATH", "firebase-credentials.json")

db = None
if os.path.exists(FIREBASE_CRED):
    try:
        cred = credentials.Certificate(FIREBASE_CRED)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred, {'projectId': 'myvita-app-c5ecd'})
        db = firestore.client()
        print("✅ Firebase & Firestore initialized")
    except Exception as e:
        print(f"⚠️ Firebase Init Error: {e}")

# === 3. UTILITY FUNCTIONS ===

def human_delay(min_ms=500, max_ms=1500):
    time.sleep(random.uniform(min_ms, max_ms) / 1000)

def get_zone(postal_code: str) -> str:
    fsa = postal_code[:3].upper()
    return ZONES.get(fsa, f"zone_{fsa}")

def get_postal_code():
    postal = os.getenv("POSTAL_CODE", "").replace(" ", "").strip()
    return postal if (postal and len(postal) >= 3) else "H1Y3H1"

def get_service_url():
    service = os.getenv("SERVICE", "blood-test")
    return f"https://portal3.clicsante.ca/services/{service}"

def get_user_token():
    token_file = "user_fcm_token.txt"
    if os.path.exists(token_file):
        with open(token_file, "r") as f:
            return f.read().strip()
    return None

# === 4. NOTIFICATION & DATA SAVING ===

def save_availability(postal_code: str, has_slots: bool, booking_url: str, slot_details: str):
    if db is None: return
    
    zone = get_zone(postal_code)
    now = datetime.now().isoformat()
    data = {
        "service": os.getenv("SERVICE", "blood-test"),
        "postal_code": postal_code,
        "zone": zone,
        "slots_found": has_slots,
        "booking_url": booking_url,
        "slot_details": slot_details,
        "last_checked": now,
    }
    
    try:
        db.collection("availability").document(zone).set(data)
        db.collection("availability").document(zone).collection("history").add({
            "slots_found": has_slots,
            "checked_at": now
        })
        print(f"🔥 Firestore Updated: {zone}")
    except Exception as e:
        print(f"❌ Firestore Error: {e}")

def send_notification(postal_code: str, booking_url: str, slots_found: bool):
    token = get_user_token()
    if not token or not slots_found: return
    
    try:
        message = messaging.Message(
            notification=messaging.Notification(
                title="🎉 Free Appointment Found!",
                body=f"Free slots detected near {postal_code}. Tap to book."
            ),
            data={"url": booking_url},
            token=token,
        )
        messaging.send(message)
        print("✅ FCM Notification Sent")
    except Exception as e:
        print(f"❌ FCM Error: {e}")

# === 5. SCRAPING ENGINE ===

def launch_stealth_browser(p):
    browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
    context = browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    # Mask automation flags
    context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return browser, context

def try_select_free_filter(page):
    """Selects ONLY the 'Without fees' button using exact regex matching."""
    try:
        # Close any blocking modals first
        close_btn = page.locator("button[aria-label='Close'], .modal-close, .close-button").first
        if close_btn.is_visible():
            close_btn.click()
            human_delay(300, 600)

        # Target ONLY 'Sans frais' or 'Without fees' (No partial matches like 'With and without')
        free_filter = page.locator("button, label, span").filter(
            has_text=re.compile(r"^(Sans frais|Without fees)$", re.IGNORECASE)
        ).first
        
        free_filter.wait_for(state="visible", timeout=8000)
        free_filter.click()
        print("✅ Filter Applied: Sans frais (Uniquely)")
        return True
    except Exception:
        print("⚠️ 'Without fees' filter not found/applied")
        return False

def check_for_real_slots(page_text: str):
    text_lower = page_text.lower()
    
    # Negative indicators
    if any(p in text_lower for p in ["aucune disponibilité", "no availability", "aucun rendez-vous"]):
        return False, "Full"
    
    # Positive indicators (Dates or 'Available')
    found_date = re.search(r"(\d{1,2}\s+(jan|fév|mar|avr|mai|juin|juil|aoû|sep|oct|nov|déc))", text_lower)
    if found_date or "prochain rendez-vous" in text_lower or "next appointment" in text_lower:
        return True, found_date.group() if found_date else "Available"
    
    return False, "Not Found"

def check_availability():
    postal_code = get_postal_code()
    service_url = get_service_url()

    print(f"🚀 Starting Search: {postal_code} @ {datetime.now().strftime('%H:%M:%S')}")

    with sync_playwright() as p:
        browser, context = launch_stealth_browser(p)
        page = context.new_page()

        try:
            # 1. Open Site
            page.goto(service_url, wait_until="domcontentloaded", timeout=60000)
            human_delay(1000, 2000)

            # 2. Apply "Free Only" Filter
            try_select_free_filter(page)
            human_delay()

            # 3. Handle Postal Code Input
            # Target specifically by label or placeholder
            postal_box = page.locator("input[placeholder*='A1A'], input[autocomplete='postal-code']").first
            if not postal_box.is_visible():
                postal_box = page.locator("input[type='text']").first
            
            postal_box.click()
            postal_box.fill("") # Clear it
            for char in postal_code:
                postal_box.type(char, delay=random.randint(50, 150))
            
            human_delay(400, 800)
            page.keyboard.press("Enter")

            # 4. Force Click Search Button (Fallback)
            search_btn = page.get_by_role("button", name=re.compile(r"Search|Rechercher", re.I))
            if search_btn.count() > 0 and search_btn.first.is_visible():
                search_btn.first.click()

            # 5. Wait for Result Rendering
            print("⏳ Waiting for list to refresh...")
            try:
                page.wait_for_selector(".establishment-card, .results-list, .no-results", timeout=20000)
                human_delay(2000, 3000) # Wait for dates to populate
            except:
                print("⚠️ Results timeout - checking page text anyway")

            # 6. Analyze Final Page State
            body_text = page.inner_text("body")
            has_slots, detail = check_for_real_slots(body_text)

            if has_slots:
                print(f"🎉 SUCCESS: Found free slots! ({detail})")
                send_notification(postal_code, page.url, True)
                save_availability(postal_code, True, page.url, detail)
            else:
                print(f"❌ STATUS: No free slots found ({detail})")
                save_availability(postal_code, False, page.url, detail)

            return has_slots

        except Exception as e:
            print(f"🚨 Script Error: {e}")
            return False
        finally:
            browser.close()

if __name__ == "__main__":
    check_availability()
