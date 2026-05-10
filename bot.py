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
        print("✅ Firebase initialized (via GitHub Secret)")
    elif os.path.exists("firebase-credentials.json"):
        cred = credentials.Certificate("firebase-credentials.json")
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred, {'projectId': 'myvita-app-c5ecd'})
        db = firestore.client()
        print("✅ Firebase initialized (via local file)")
    else:
        print("⚠️ No Firebase credentials — running without Firestore")
except Exception as e:
    print(f"⚠️ Firebase Init Error: {e}")

# === 3. UTILITIES ===

def human_delay(min_sec=0.8, max_sec=2.5):
    time.sleep(random.uniform(min_sec, max_sec))

def get_zone(postal_code: str) -> str:
    return ZONES.get(postal_code[:3].upper(), f"zone_{postal_code[:3].upper()}")

def get_user_token():
    if db is None: return None
    try:
        docs = db.collection('users').order_by('fcmTokenUpdated', direction='DESCENDING').limit(10).stream()
        for doc in docs:
            token = doc.to_dict().get('fcmToken')
            if token: return token
    except: pass
    return None

# === 4. NOTIFICATION & FIRESTORE ===

def send_notification(postal_code: str, booking_url: str, clinic_name: str = None):
    token = get_user_token()
    if not token:
        print("⚠️ No FCM token — skipping notification")
        return
    try:
        body = f"Créneaux trouvés près de {postal_code}. Touchez pour réserver."
        if clinic_name:
            body = f"{clinic_name} — Créneaux disponibles! Touchez pour réserver."
        messaging.send(messaging.Message(
            notification=messaging.Notification(title="🎉 Rendez-vous disponible!", body=body),
            data={"url": booking_url, "postal_code": postal_code},
            token=token,
        ))
        print(f"✅ FCM Sent → {booking_url[:100]}")
    except Exception as e:
        print(f"❌ FCM Error: {e}")

def save_availability(postal_code, has_slots, booking_url, details, clinics=None):
    if db is None: return
    zone = get_zone(postal_code)
    now = datetime.now().isoformat()
    try:
        db.collection("availability").document(zone).set({
            "service": "blood-test", "postal_code": postal_code,
            "zone": zone, "slots_found": has_slots,
            "booking_url": booking_url, "details": details,
            "clinics": clinics or [], "last_checked": now,
        })
        db.collection("availability").document(zone).collection("history").add({
            "slots_found": has_slots, "clinics_found": len(clinics or []), "checked_at": now
        })
        print(f"🔥 Firestore: {zone}")
    except Exception as e:
        print(f"❌ Firestore Error: {e}")

# === 5. MAIN FUNCTION (Grok's simple approach) ===

def check_availability(postal_code_override=None):
    postal_code = postal_code_override or os.getenv("POSTAL_CODE", "H1Y3H1").replace(" ", "")

    print(f"\n{'='*60}")
    print(f"🚀 ClicSanté: {postal_code} @ {datetime.now().strftime('%H:%M:%S')}")
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
            # ── LOAD PAGE ──
            print("📄 Loading ClicSanté...")
            page.goto("https://portal3.clicsante.ca/services/blood-test", 
                     wait_until="networkidle", timeout=45000)
            human_delay(1.5, 3)

            # Dismiss popups
            try:
                page.keyboard.press("Escape")
                time.sleep(0.3)
            except:
                pass

            # ── SELECT "NO FEES" ──
            print("🎯 Selecting 'No fees'...")
            for txt in ["No fees", "Sans frais"]:
                try:
                    page.get_by_text(txt, exact=True).click(timeout=5000)
                    print(f"✅ Selected: {txt}")
                    break
                except:
                    continue
            human_delay(0.5, 1)

            # ── ENTER POSTAL CODE ──
            print(f"⌨️  Entering postal code: {postal_code}")
            try:
                input_field = page.get_by_placeholder("ex. A1A 1A1")
                input_field.fill(postal_code)
                print("   ✅ Entered via placeholder")
            except:
                try:
                    page.locator("input[type='text']").first.fill(postal_code)
                    print("   ✅ Entered via text input")
                except:
                    pass
            human_delay(0.5, 1)

            # ── CLICK SEARCH ──
            print("🔍 Clicking Search...")
            for btn_text in ["Search", "Rechercher", "Chercher"]:
                try:
                    page.get_by_role("button", name=re.compile(btn_text, re.I)).first.click(timeout=5000)
                    print(f"   ✅ Clicked: {btn_text}")
                    break
                except:
                    continue

            # ── WAIT FOR RESULTS ──
            print("⏳ Waiting for results...")
            human_delay(7, 12)

            # Try to detect clinic cards
            try:
                page.wait_for_selector("article, .establishment-card, [class*='clinic'], text=~", timeout=20000)
                print("   ✅ Clinic cards detected")
            except:
                print("   ⚠️ No cards detected by selector")

            # ── CAPTURE RESULTS PAGE URL ──
            results_url = page.url
            print(f"📍 Results URL: {results_url[:120]}...")

            # ── CHECK BODY FOR AVAILABILITY ──
            body = page.inner_text("body").lower()
            
            no_slots = ["aucune disponibilité", "no availability", "aucun rendez-vous", "désolé", "sorry"]
            has_positive = any(x in body for x in ["km", "clinique", "hôpital", "clsc", "gmf", "disponible", "available", "à venir"])
            has_negative = any(x in body for x in no_slots)

            print(f"   Positive signals: {has_positive}")
            print(f"   Negative signals: {has_negative}")

            # ── TAKE SCREENSHOT FOR DEBUG ──
            try:
                page.screenshot(path=f"debug_results_{postal_code}.png")
            except:
                pass

            # ── SEND RESULTS ──
            if has_positive and not has_negative:
                print("🎉 Slots available! Sending results page URL")
                send_notification(postal_code, results_url, "Cliniques disponibles")
                save_availability(postal_code, True, results_url, "Results page with clinics")
                return True
            elif has_negative:
                print("❌ No slots available")
                save_availability(postal_code, False, results_url, "No slots")
                return False
            else:
                print("⚠️ Uncertain — sending results page anyway")
                send_notification(postal_code, results_url)
                save_availability(postal_code, True, results_url, "Results page (uncertain)")
                return True

        except Exception as e:
            print(f"🚨 Error: {e}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            browser.close()


# === 6. MAIN ENTRY POINT ===

if __name__ == "__main__":
    for code in ["H1Y3H1"]:
        check_availability(code)
        time.sleep(5)
