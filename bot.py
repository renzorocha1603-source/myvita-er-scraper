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

# === 2. FIREBASE SETUP (via GitHub Secret) ===
db = None
try:
    creds_json = os.getenv("FIREBASE_CREDENTIALS")
    if creds_json:
        cred_dict = json.loads(creds_json)
        cred = credentials.Certificate(cred_dict)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred, {'projectId': 'myvita-app-c5ecd'})
        db = firestore.client()
        print("✅ Firebase & Firestore initialized (via GitHub Secret)")
    else:
        if os.path.exists("firebase-credentials.json"):
            cred = credentials.Certificate("firebase-credentials.json")
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred, {'projectId': 'myvita-app-c5ecd'})
            db = firestore.client()
            print("✅ Firebase & Firestore initialized (via local file)")
        else:
            print("⚠️ No Firebase credentials found — running without Firestore")
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
    """Get FCM token from Firestore (most recent user)"""
    if db is None:
        print("❌ Firestore not connected")
        return None
    
    try:
        users_ref = db.collection('users').order_by('fcmTokenUpdated', direction='DESCENDING').limit(1)
        docs = users_ref.stream()
        for doc in docs:
            data = doc.to_dict()
            token = data.get('fcmToken')
            if token:
                print(f"📱 Found FCM token from Firestore")
                return token
        print("⚠️ No FCM token found in Firestore")
        return None
    except Exception as e:
        print(f"❌ Error reading FCM token: {e}")
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
    if not token or not slots_found:
        if not token:
            print("⚠️ No FCM token — notification skipped")
        return

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
    REQUIRED — Clic Santé blocks search until a filter is selected.
    """
    strategies = [
        lambda: page.get_by_text("No fees", exact=True).first,
        lambda: page.get_by_text("Sans frais", exact=True).first,
        lambda: page.locator("label").filter(has_text=re.compile(r"^No fees$")).first,
        lambda: page.locator("label").filter(has_text=re.compile(r"^Sans frais$")).first,
        lambda: page.locator("input[type='radio']").first,
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

def verify_real_slots(page):
    """
    ★ DEEP CALENDAR VERIFICATION ★
    Clicks through: Results → "Prendre RDV" → Calendar → checks for real dates.
    Returns (has_real_slots, details_string, booking_url_or_none)
    """
    body_text = page.inner_text("body")
    text_lower = body_text.lower()

    # Quick negative check on results page
    no_slot_phrases = [
        "aucune disponibilité", "no availability", "aucun rendez-vous",
        "no appointments", "désolé", "sorry",
        "aucun résultat", "no results",
    ]
    for phrase in no_slot_phrases:
        if phrase in text_lower:
            return False, f"No slots ({phrase})", None

    # Find clinic cards on results page
    clinic_selectors = [
        ".establishment-card",
        "[class*='establishment']",
        "[class*='result-item']",
        "a[href*='establishment']",
        ".clinic-item",
    ]

    for selector in clinic_selectors:
        try:
            clinics = page.locator(selector)
            count = clinics.count()
            if count == 0:
                continue

            # Try each clinic
            for i in range(min(count, 5)):
                try:
                    clinic = clinics.nth(i)
                    if not clinic.is_visible():
                        continue

                    clinic_name = clinic.inner_text()[:50]
                    print(f"   🏥 Checking clinic #{i+1}: {clinic_name}...")
                    clinic.click(timeout=5000)
                    human_delay(2000, 3000)

                    # Look for "Prendre RDV" button on clinic detail page
                    booking_button_selectors = [
                        "text=Prendre RDV",
                        "text=Prendre rendez-vous",
                        "text=Book appointment",
                        "a[href*='booking']",
                        "a[href*='appointment']",
                        "button[class*='book']",
                        "[class*='booking']",
                    ]

                    booking_btn = None
                    for btn_sel in booking_button_selectors:
                        try:
                            btn = page.locator(btn_sel).first
                            if btn.count() > 0 and btn.is_visible():
                                booking_btn = btn
                                break
                        except:
                            continue

                    if booking_btn:
                        print("      → Clicking 'Prendre RDV'...")
                        booking_btn.click(timeout=5000)
                        human_delay(3000, 4000)

                        # ★ NOW WE'RE ON THE CALENDAR PAGE ★
                        calendar_url = page.url
                        calendar_text = page.inner_text("body")
                        calendar_lower = calendar_text.lower()

                        # Check if we landed on a real calendar
                        cal_indicators = ["mai", "juin", "juillet", "janvier", "février", "mars", "avril",
                                         "may", "june", "july", "january", "february", "march", "april",
                                         "lun", "mar", "mer", "jeu", "ven", "sam", "dim",
                                         "mon", "tue", "wed", "thu", "fri", "sat", "sun"]

                        is_calendar = any(ind in calendar_lower for ind in cal_indicators)

                        if is_calendar:
                            # Count "Complet" occurrences
                            complet_count = calendar_lower.count("complet")
                            
                            # Look for positive indicators
                            positive_indicators = [
                                "disponible", "available", "ouvert", "open",
                                "à venir", "coming soon",
                            ]
                            has_positive = any(ind in calendar_lower for ind in positive_indicators)

                            # Try to find clickable date elements (not "Complet")
                            date_selectors = [
                                "[class*='available']",
                                "[class*='disponible']",
                                "[class*='open']",
                                "button[class*='day']:not([disabled])",
                                "td:not([class*='complet']):not([class*='full'])",
                            ]

                            clickable_dates = 0
                            for date_sel in date_selectors:
                                try:
                                    dates = page.locator(date_sel)
                                    if dates.count() > 0:
                                        clickable_dates = dates.count()
                                        break
                                except:
                                    continue

                            print(f"      📅 Calendar: Complet count={complet_count}, Positive indicators={has_positive}, Clickable dates={clickable_dates}")

                            # REAL slot = not everything is "Complet" AND we found clickable dates OR positive indicators
                            if clickable_dates > 0 or (has_positive and complet_count < 20):
                                print(f"      🎉 REAL SLOTS FOUND!")
                                return True, f"Calendar has open dates (Complet count: {complet_count}, Clickable: {clickable_dates})", calendar_url
                            else:
                                print(f"      ❌ Calendar all full — no real slots")
                                # Go back and try next clinic
                                page.go_back()
                                human_delay(1000, 2000)
                                continue
                        else:
                            print(f"      ⚠️ Did not land on calendar — skipping")
                            page.go_back()
                            human_delay(500, 1000)
                            continue
                    else:
                        # No booking button found
                        page.go_back()
                        human_delay(500, 1000)
                        continue

                except Exception as e:
                    print(f"      ⚠️ Error checking clinic: {e}")
                    try:
                        page.go_back()
                        human_delay(500, 1000)
                    except:
                        pass
                    continue

        except Exception as e:
            print(f"   ⚠️ Clinic selector error: {e}")
            continue

    # Fallback: old logic for backwards compatibility
    if "availabilities" in text_lower or "disponibilités" in text_lower:
        return True, "Availabilities shown (fallback)", page.url

    return False, "No real slots found", None

def check_availability(postal_code_override=None):
    postal_code = postal_code_override if postal_code_override else get_postal_code()
    service_url = get_service_url()
    headless = os.getenv("HEADLESS", "true").lower() != "false"

    print(f"🚀 Starting Search: {postal_code} @ {datetime.now().strftime('%H:%M:%S')}")

    with sync_playwright() as p:
        browser, context = launch_stealth_browser(p, headless=headless)
        page = context.new_page()

        try:
            print("📄 Loading page...")
            page.goto(service_url, wait_until="domcontentloaded", timeout=60000)
            human_delay(1500, 2500)

            try:
                page.keyboard.press("Escape")
                human_delay(300, 500)
            except:
                pass

            print("🎯 Selecting 'No fees' filter...")
            filter_applied = try_select_free_filter(page)

            if not filter_applied:
                human_delay(1000, 2000)
                filter_applied = try_select_free_filter(page)

            human_delay(500, 1000)

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

            human_delay(500, 1000)
            search_btn = page.get_by_role("button", name=re.compile(r"Search|Rechercher|Chercher", re.I))
            if search_btn.count() > 0 and search_btn.first.is_visible():
                search_btn.first.click()
                print("   Clicked Search button")

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

            print("🔍 Verifying real slots (deep calendar check)...")
            has_slots, detail, calendar_url = verify_real_slots(page)

            booking_url = calendar_url if calendar_url else page.url

            if has_slots:
                print(f"🎉 SUCCESS: Real free slots found! ({detail})")
                send_notification(postal_code, booking_url, True)
                save_availability(postal_code, True, booking_url, detail)
            else:
                print(f"❌ STATUS: No free slots found ({detail})")
                save_availability(postal_code, False, booking_url, detail)

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
    postal_codes = ["H1Y3H1", "H4L2B5", "H2X1Y7", "G1R2A3", "J8Y3H1"]
    
    for postal in postal_codes:
        print(f"\n{'='*50}")
        print(f"🔍 Searching: {postal}")
        print(f"{'='*50}")
        check_availability(postal_code_override=postal)
        time.sleep(3)
