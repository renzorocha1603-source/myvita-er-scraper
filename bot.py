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

def launch_stealth_browser(p, headless=True):
    browser = p.chromium.launch(
        headless=headless, 
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
    )
    context = browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return browser, context

def debug_page_state(page, step_name):
    """Save screenshot and print visible text for debugging"""
    try:
        page.screenshot(path=f"debug_{step_name}.png")
        body_text = page.inner_text("body")
        print(f"\n📸 DEBUG [{step_name}] - Page text preview:")
        print(body_text[:400])
        print("...")
    except:
        pass

def try_select_free_filter(page):
    """
    Select 'No fees' / 'Sans frais' option.
    This is REQUIRED — Clic Santé blocks search until a filter is selected.
    Uses exact matching to avoid selecting 'Fees and no fees'.
    """
    strategies = [
        # Strategy 1: Exact text "No fees" (exact match avoids "Fees and no fees")
        lambda: page.get_by_text("No fees", exact=True).first,
        # Strategy 2: French exact text
        lambda: page.get_by_text("Sans frais", exact=True).first,
        # Strategy 3: Label containing exactly "No fees"
        lambda: page.locator("label").filter(has_text=re.compile(r"^No fees$")).first,
        # Strategy 4: Label containing exactly "Sans frais"
        lambda: page.locator("label").filter(has_text=re.compile(r"^Sans frais$")).first,
        # Strategy 5: First radio button (usually "No fees" is first)
        lambda: page.locator("input[type='radio']").first,
        # Strategy 6: Click the first toggle/switch option
        lambda: page.locator("[role='radio']").first,
    ]

    for i, strategy in enumerate(strategies):
        try:
            element = strategy()
            if element and element.count() > 0 and element.is_visible():
                element.click(timeout=3000)
                human_delay(500, 1000)
                print(f"✅ 'No fees' selected (strategy {i+1})")
                return True
        except Exception as e:
            print(f"   Strategy {i+1} failed: {e}")
            continue

    print("❌ CRITICAL: Could not select 'No fees' — search will fail")
    return False

def check_for_real_slots(page):
    """Check multiple indicators for available slots"""
    body_text = page.inner_text("body")
    text_lower = body_text.lower()
    current_url = page.url
    print(f"   Current URL: {current_url[:120]}")

    # Negative indicators
    no_slot_phrases = [
        "aucune disponibilité", "no availability", "aucun rendez-vous",
        "no appointments", "désolé", "sorry", "complet", "full",
        "aucun résultat", "no results", "please select an option"
    ]
    for phrase in no_slot_phrases:
        if phrase in text_lower:
            return False, f"No slots ({phrase})"

    # Positive indicators
    found_date = re.search(
        r"(\d{1,2}\s+(janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre|jan|fév|mar|avr|mai|juin|juil|aoû|sep|oct|nov|déc))",
        text_lower
    )
    if found_date:
        return True, f"Date found: {found_date.group()}"

    if "prochain rendez-vous" in text_lower or "next appointment" in text_lower:
        return True, "Next appointment available"

    if "disponible" in text_lower or "available" in text_lower:
        return True, "Availability indicated"

    return False, "No clear indicators"

def check_availability():
    postal_code = get_postal_code()
    service_url = get_service_url()
    headless = os.getenv("HEADLESS", "true").lower() != "false"

    print(f"🚀 Starting Search: {postal_code} @ {datetime.now().strftime('%H:%M:%S')}")

    with sync_playwright() as p:
        browser, context = launch_stealth_browser(p, headless=headless)
        page = context.new_page()

        try:
            # 1. Open Site
            print("📄 Loading page...")
            page.goto(service_url, wait_until="domcontentloaded", timeout=60000)
            human_delay(1500, 2500)

            # 2. Close any popups/modals
            try:
                page.keyboard.press("Escape")
                human_delay(300, 500)
            except:
                pass

            # 3. SELECT "NO FEES" FILTER — REQUIRED!
            print("🎯 Selecting 'No fees' filter...")
            filter_applied = try_select_free_filter(page)
            
            if not filter_applied:
                # Try one more time after a short wait (page might still be loading)
                human_delay(1000, 2000)
                filter_applied = try_select_free_filter(page)
            
            human_delay(500, 1000)

            # 4. Enter Postal Code
            print("⌨️  Entering postal code...")
            postal_selectors = [
                "input[placeholder*='A1A']",
                "input[placeholder*='postal']",
                "input[autocomplete='postal-code']",
                "input[type='text']",
            ]

            postal_box = None
            for selector in postal_selectors:
                try:
                    postal_box = page.locator(selector).first
                    if postal_box.is_visible():
                        print(f"   Found input: {selector}")
                        break
                except:
                    continue

            if postal_box:
                postal_box.click()
                human_delay(200, 400)
                postal_box.fill("")
                human_delay(100, 200)
                for char in postal_code:
                    postal_box.type(char, delay=random.randint(50, 150))
                human_delay(400, 800)
                print(f"   Entered: {postal_code}")
            else:
                print("   ❌ Could not find postal input")

            # 5. Click Search Button
            human_delay(500, 1000)
            search_btn = page.get_by_role("button", name=re.compile(r"Search|Rechercher|Chercher", re.I))
            if search_btn.count() > 0 and search_btn.first.is_visible():
                search_btn.first.click()
                print("   Clicked Search button")

            # 6. Wait for Results
            print("⏳ Waiting for results...")
            human_delay(3000, 5000)

            try:
                page.wait_for_selector(
                    ".establishment-card, .results-list, .no-results, [class*='result'], [class*='clinic']",
                    timeout=20000
                )
                human_delay(2000, 3000)
            except:
                print("   ⚠️ Results container not found — checking anyway")

            debug_page_state(page, "results")

            # 7. Analyze Results
            has_slots, detail = check_for_real_slots(page)

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
            import traceback
            traceback.print_exc()
            return False
        finally:
            if headless:
                browser.close()
            else:
                print("🔍 Browser left open for debugging — close manually")
                input("Press Enter to close browser...")
                browser.close()

if __name__ == "__main__":
    check_availability()