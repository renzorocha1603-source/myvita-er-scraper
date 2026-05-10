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
        users_ref = db.collection('users').order_by('fcmTokenUpdated', direction='DESCENDING').limit(10)
        docs = users_ref.stream()
        for doc in docs:
            data = doc.to_dict()
            token = data.get('fcmToken')
            if token: return token
    except: pass
    return None

# === 4. NOTIFICATION & DATA SAVING ===

def send_notification(postal_code: str, booking_url: str, clinic_name: str = None):
    token = get_user_token()
    if not token:
        print("⚠️ No FCM token — skipping notification")
        return
    try:
        body = f"{clinic_name} — Créneaux disponibles! Touchez pour réserver." if clinic_name else f"Créneaux trouvés près de {postal_code}. Touchez pour réserver."
        message = messaging.Message(
            notification=messaging.Notification(title="🎉 Rendez-vous disponible!", body=body),
            data={"url": booking_url, "postal_code": postal_code},
            token=token,
        )
        messaging.send(message)
        print(f"✅ FCM Notification Sent → {booking_url[:120]}...")
    except Exception as e:
        print(f"❌ FCM Error: {e}")

def save_availability(postal_code: str, has_slots: bool, booking_url: str,
                      details: str, clinics: list = None):
    if db is None: return
    zone = get_zone(postal_code)
    now = datetime.now().isoformat()
    data = {
        "service": "blood-test", "postal_code": postal_code, "zone": zone,
        "slots_found": has_slots, "booking_url": booking_url,
        "details": details, "clinics": clinics or [], "last_checked": now,
    }
    try:
        db.collection("availability").document(zone).set(data)
        db.collection("availability").document(zone).collection("history").add({
            "slots_found": has_slots, "clinics_found": len(clinics) if clinics else 0,
            "checked_at": now
        })
        print(f"🔥 Firestore Updated: {zone} — {len(clinics or [])} clinics")
    except Exception as e:
        print(f"❌ Firestore Error: {e}")

# === 5. POPUP BUSTER ===

def dismiss_popups(page):
    """Aggressively dismiss any popups, banners, modals, or overlays."""
    strategies = [
        lambda: page.keyboard.press("Escape"),
        lambda: page.keyboard.press("Escape"),
        lambda: page.locator("[aria-label*='Close'],[aria-label*='close'],[aria-label*='Fermer']").first.click(timeout=1000),
        lambda: page.locator(".close,.modal-close,.popup-close,.btn-close,.cookie-close").first.click(timeout=1000),
        lambda: page.locator("button:has-text('Accept'),button:has-text('Accepter'),button:has-text('OK'),button:has-text('Continue'),button:has-text('Continuer')").first.click(timeout=1000),
        lambda: page.locator("button:has-text('J\\'accepte'),button:has-text('Accepter les cookies')").first.click(timeout=1000),
        lambda: page.mouse.click(10, 10),
        lambda: page.mouse.click(640, 10),
    ]
    for strategy in strategies:
        try:
            strategy()
            time.sleep(0.2)
        except:
            continue
    human_delay(0.3, 0.6)

def try_click_element(page, selectors, timeout=5000):
    """Try multiple selectors to click an element. Returns True if successful."""
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.count() > 0 and el.is_visible():
                el.click(timeout=timeout)
                return True
        except:
            continue
    return False

# === 6. MAIN CLICK-THROUGH FUNCTION ===

def check_availability(postal_code_override=None):
    """
    OPTION B: Full click-through approach.
    Searches ClicSanté, clicks into the first available clinic,
    clicks "Prendre RDV", and captures the deep link URL.
    """
    postal_code = postal_code_override or os.getenv("POSTAL_CODE", "H1Y3H1").replace(" ", "")

    print(f"\n{'='*60}")
    print(f"🚀 ClicSanté Search: {postal_code} @ {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        context = browser.new_context(viewport={"width": 1280, "height": 800},
                                      user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()

        try:
            # ── LOAD PAGE ──
            print("📄 Loading ClicSanté...")
            page.goto("https://portal3.clicsante.ca/services/blood-test", wait_until="networkidle", timeout=45000)
            human_delay(2, 3)
            dismiss_popups(page)

            # ── SELECT "NO FEES" ──
            print("🎯 Selecting 'No fees'...")
            try_click_element(page, [
                "text=No fees", "text=Sans frais",
                "label:has-text('No fees')", "label:has-text('Sans frais')",
                "input[type='radio']", "[role='radio']"
            ])
            human_delay(0.5, 1)
            print("   ✅ Filter selected")

            # ── ENTER POSTAL CODE ──
            print(f"⌨️  Entering postal code: {postal_code}")
            for sel in ["input[placeholder*='A1A']", "input[type='text']"]:
                try:
                    field = page.locator(sel).first
                    field.click(); field.fill(""); field.type(postal_code, delay=80)
                    break
                except: continue
            human_delay(0.5, 1)
            print("   ✅ Postal code entered")

            # ── CLICK SEARCH ──
            print("🔍 Clicking Search...")
            try_click_element(page, ["button:has-text('Search')", "button:has-text('Rechercher')", "button:has-text('Chercher')"])
            human_delay(1, 2)

            # ── WAIT FOR RESULTS ──
            print("⏳ Waiting for results to load...")
            human_delay(6, 10)
            dismiss_popups(page)

            # Check body text for availability
            body_text = page.inner_text("body").lower()
            no_slots = ["aucune disponibilité", "no availability", "aucun rendez-vous", "désolé", "sorry"]
            if any(p in body_text for p in no_slots):
                print("❌ No slots available on results page")
                save_availability(postal_code, False, page.url, "No slots", [])
                return False, []

            print("   ✅ Results loaded — looking for clinics...")

            # ── CLICK FIRST CLINIC CARD ──
            print("🏥 Clicking first clinic...")
            clinic_clicked = try_click_element(page, [
                ".establishment-card",
                "[class*='establishment']",
                "[class*='result-item']",
                "article",
                "a[href*='establishment']",
            ])
            
            if clinic_clicked:
                human_delay(2, 3)
                dismiss_popups(page)
                print("   ✅ Clinic page opened")

                # ── CLICK "PRENDRE RDV" ──
                print("🔘 Clicking 'Prendre RDV'...")
                booking_clicked = try_click_element(page, [
                    "text=Prendre RDV",
                    "text=Prendre rendez-vous",
                    "button:has-text('Prendre RDV')",
                    "button:has-text('Prendre rendez-vous')",
                    "a:has-text('Prendre RDV')",
                    "text=Book appt.",
                    "text=Book appointment",
                    "a[href*='appointment']",
                    "a[href*='rdv']",
                    "[class*='booking']",
                ])

                if booking_clicked:
                    human_delay(3, 5)
                    dismiss_popups(page)
                    deep_link = page.url
                    print(f"   🎉 DEEP LINK: {deep_link[:120]}...")
                    
                    # Get clinic name from page
                    try:
                        clinic_name = page.locator("h1,h2,h3,.clinic-name,.establishment-name").first.inner_text()[:80]
                    except:
                        clinic_name = "Clinic"
                    
                    print(f"   🏥 Clinic: {clinic_name}")
                    send_notification(postal_code, deep_link, clinic_name)
                    save_availability(postal_code, True, deep_link, f"Deep link to {clinic_name}", [{
                        'name': clinic_name, 'url': deep_link, 'source': 'click_through'
                    }])
                    return True, [{'name': clinic_name, 'url': deep_link}]

                else:
                    # Clinic page opened but no booking button — send clinic page URL
                    clinic_url = page.url
                    print(f"   ⚠️ No booking button — sending clinic page URL")
                    send_notification(postal_code, clinic_url)
                    save_availability(postal_code, True, clinic_url, "Clinic page", [])
                    return True, []
            else:
                # Couldn't click a clinic — send results page URL
                results_url = page.url
                print(f"   ⚠️ Couldn't click clinic — sending results page")
                send_notification(postal_code, results_url)
                save_availability(postal_code, True, results_url, "Results page", [])
                return True, []

        except Exception as e:
            print(f"🚨 Error: {e}")
            import traceback; traceback.print_exc()
            return False, []
        finally:
            browser.close()


# === 7. MAIN ENTRY POINT ===

if __name__ == "__main__":
    for code in ["H1Y3H1", "H4L2B5", "H2X1Y7", "G1R2A3", "J8Y3H1"]:
        success, clinics = check_availability(code)
        print(f"\n{'─'*40}")
        print(f"Result for {code}: {'✅ Found' if success else '❌ None'}")
        if clinics:
            for c in clinics[:3]:
                print(f"  - {c.get('name', 'Unknown')}")
                print(f"    {c.get('url', '')[:120]}")
        print(f"{'─'*40}\n")
        time.sleep(3)
