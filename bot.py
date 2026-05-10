from playwright.sync_api import sync_playwright
import time
import random
import os
import json
import re
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
    elif os.path.exists("firebase-credentials.json"):
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

def human_delay(min_sec=0.8, max_sec=2.5):
    time.sleep(random.uniform(min_sec, max_sec))

def get_zone(postal_code: str) -> str:
    fsa = postal_code[:3].upper()
    return ZONES.get(fsa, f"zone_{fsa}")

def get_user_token():
    if db is None: return None
    try:
        users_ref = db.collection('users').order_by('fcmTokenUpdated', direction='DESCENDING').limit(1)
        docs = users_ref.stream()
        for doc in docs:
            data = doc.to_dict()
            token = data.get('fcmToken')
            if token: return token
    except: pass
    return None

# === 4. NOTIFICATION & DATA SAVING ===

def send_notification(postal_code: str, booking_url: str):
    token = get_user_token()
    if not token: return
    try:
        message = messaging.Message(
            notification=messaging.Notification(
                title="🎉 Rendez-vous disponible!",
                body=f"Créneaux trouvés près de {postal_code}. Touchez pour réserver."
            ),
            data={"url": booking_url},
            token=token,
        )
        messaging.send(message)
        print(f"✅ FCM Notification Sent → {booking_url[:80]}...")
    except Exception as e:
        print(f"❌ FCM Error: {e}")

def save_availability(postal_code: str, has_slots: bool, booking_url: str, details: str):
    if db is None: return
    zone = get_zone(postal_code)
    now = datetime.now().isoformat()
    data = {
        "service": "blood-test",
        "postal_code": postal_code,
        "zone": zone,
        "slots_found": has_slots,
        "booking_url": booking_url,
        "details": details,
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

# === 5. MAIN FUNCTION (Grok's simpler approach) ===

def check_availability(postal_code_override=None):
    postal_code = postal_code_override or os.getenv("POSTAL_CODE", "H1Y3H1").replace(" ", "")
    
    print(f"\n{'='*60}")
    print(f"🚀 ClicSanté Search: {postal_code} @ {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()
        
        try:
            print("📄 Loading ClicSanté...")
            page.goto("https://portal3.clicsante.ca/services/blood-test", 
                     wait_until="networkidle", timeout=45000)
            human_delay(1.5, 3)

            # Dismiss popups
            try:
                page.keyboard.press("Escape")
                human_delay(0.3, 0.5)
            except:
                pass

            # Select "No fees"
            print("🎯 Selecting 'No fees'...")
            selected = False
            for text in ["No fees", "Sans frais"]:
                try:
                    page.get_by_text(text, exact=True).first.click(timeout=5000)
                    selected = True
                    print(f"✅ '{text}' selected")
                    break
                except:
                    continue
            if not selected:
                print("⚠️ Could not select No fees — trying radio buttons")
                try:
                    page.locator("input[type='radio']").first.click(timeout=5000)
                    selected = True
                except:
                    pass

            human_delay(0.5, 1)

            # Enter postal code
            print("⌨️  Entering postal code...")
            try:
                postal_input = page.get_by_placeholder("ex. A1A 1A1")
                postal_input.fill(postal_code)
            except:
                try:
                    page.locator("input[type='text']").first.fill(postal_code)
                except:
                    pass

            human_delay(0.5, 1.5)

            # Click Search
            print("🔍 Clicking Search...")
            try:
                page.get_by_role("button", name=re.compile(r"Search|Rechercher", re.I)).first.click()
            except:
                pass

            # Wait for results
            print("⏳ Waiting for results...")
            human_delay(7, 12)

            # Capture the results page URL — this is what we send
            try:
                page.wait_for_selector("article, .establishment-card, [class*='clinic'], [class*='result']", 
                                     timeout=18000)
            except:
                pass

            results_url = page.url
            print(f"📍 Results URL: {results_url[:120]}...")

            # Check if there are available slots
            body_text = page.inner_text("body").lower()
            no_slots = ["aucune disponibilité", "no availability", "aucun rendez-vous", "désolé", "sorry"]
            has_positive = any(word in body_text for word in 
                          ["disponible", "available", "réservation", "book", "places", "à venir", "availabilities"])
            has_negative = any(word in body_text for word in no_slots)

            if has_positive and not has_negative:
                print("🎉 Slots available on results page!")
                send_notification(postal_code, results_url)
                save_availability(postal_code, True, results_url, "Results page with clinics")
                return True
            elif has_negative:
                print(f"❌ No slots available")
                save_availability(postal_code, False, results_url, "No slots")
                return False
            else:
                print(f"⚠️ Uncertain — sending results page anyway")
                save_availability(postal_code, True, results_url, "Results page (uncertain)")
                return True

        except Exception as e:
            print(f"🚨 Error: {e}")
            return False
        finally:
            browser.close()


if __name__ == "__main__":
    test_codes = ["H1Y3H1", "H4L2B5", "H2X1Y7", "G1R2A3", "J8Y3H1"]
    
    for code in test_codes:
        check_availability(code)
        time.sleep(5)
