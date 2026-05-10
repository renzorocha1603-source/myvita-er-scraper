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
    """Get FCM token from Firestore"""
    if db is None:
        return None
    try:
        users_ref = db.collection('users')\
            .order_by('fcmTokenUpdated', direction='DESCENDING')\
            .limit(10)
        docs = users_ref.stream()
        for doc in docs:
            data = doc.to_dict()
            token = data.get('fcmToken')
            if token:
                return token
    except Exception as e:
        print(f"⚠️ Token fetch error: {e}")
    return None

# === 4. NOTIFICATION & DATA SAVING ===

def send_notification(postal_code: str, booking_url: str, clinic_name: str = None):
    token = get_user_token()
    if not token:
        print("⚠️ No FCM token found — skipping notification")
        return
    try:
        body = f"Créneaux trouvés près de {postal_code}. Touchez pour réserver."
        if clinic_name:
            body = f"{clinic_name} — Créneaux disponibles! Touchez pour réserver."

        message = messaging.Message(
            notification=messaging.Notification(
                title="🎉 Rendez-vous disponible!",
                body=body
            ),
            data={"url": booking_url, "postal_code": postal_code},
            token=token,
        )
        messaging.send(message)
        print(f"✅ FCM Notification Sent → {booking_url[:100]}...")
    except Exception as e:
        print(f"❌ FCM Error: {e}")

def save_availability(postal_code: str, has_slots: bool, booking_url: str,
                      details: str, clinics: list = None):
    if db is None:
        return
    zone = get_zone(postal_code)
    now = datetime.now().isoformat()
    data = {
        "service": "blood-test",
        "postal_code": postal_code,
        "zone": zone,
        "slots_found": has_slots,
        "booking_url": booking_url,
        "details": details,
        "clinics": clinics or [],
        "last_checked": now,
    }
    try:
        db.collection("availability").document(zone).set(data)
        db.collection("availability").document(zone)\
            .collection("history").add({
                "slots_found": has_slots,
                "clinics_found": len(clinics) if clinics else 0,
                "checked_at": now
            })
        print(f"🔥 Firestore Updated: {zone} — {len(clinics or [])} clinics")
    except Exception as e:
        print(f"❌ Firestore Error: {e}")

# === 5. CLINIC EXTRACTOR ===

def extract_clinics(page) -> list:
    """
    Extract clinic names and IDs from the SPA results page.
    Uses JavaScript evaluation to read the rendered DOM.
    """
    clinics = []

    try:
        human_delay(1, 2)

        # Strategy: Read establishment cards from the Angular SPA
        card_data = page.evaluate("""() => {
            const results = [];
            // ClicSanté uses Angular — look for establishment cards
            const cards = document.querySelectorAll(
                'app-establishment-card, .establishment-card, [class*="establishment"], article, [class*="result-item"]'
            );
            cards.forEach(card => {
                const text = card.innerText || '';
                const links = card.querySelectorAll('a[href*="clicsante"], a[href*="etablissement"], a[href*="take-appt"]');
                const href = links.length > 0 ? links[0].href : '';
                // Extract establishment ID from data attributes or text
                const dataId = card.getAttribute('data-id') || 
                              card.getAttribute('data-establishment-id') || '';
                results.push({
                    text: text.substring(0, 200),
                    href: href,
                    dataId: dataId
                });
            });
            return results;
        }""")

        if card_data:
            for card in card_data:
                text = card.get('text', '')
                href = card.get('href', '')
                if text and len(text) > 10:
                    # Get first line as clinic name
                    lines = text.strip().split('\n')
                    name = lines[0] if lines else 'Clinic'
                    
                    clinics.append({
                        'name': name[:80],
                        'url': href if href else '',
                        'text': text,
                        'source': 'dom_extraction'
                    })

    except Exception as e:
        print(f"⚠️ Clinic extraction error: {e}")

    print(f"✅ {len(clinics)} clinics extracted from DOM")
    return clinics

# === 6. MAIN FUNCTION ===

def check_availability(postal_code_override=None):
    """
    Searches ClicSanté, captures the results page URL with search done,
    extracts clinic names for the notification.
    The results page URL already has postal code + No fees filter applied.
    """
    postal_code = postal_code_override or \
        os.getenv("POSTAL_CODE", "H1Y3H1").replace(" ", "")

    print(f"\n{'='*60}")
    print(f"🚀 ClicSanté Search: {postal_code} @ {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}")

    captured_api_responses = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = context.new_page()

        # Intercept API responses
        def handle_response(response):
            url = response.url
            if 'api3.clicsante.ca' in url and response.status == 200:
                try:
                    content_type = response.headers.get('content-type', '')
                    if 'json' in content_type:
                        body = response.json()
                        captured_api_responses.append({
                            'url': url,
                            'data': body
                        })
                        print(f"📡 Captured API: {url[:100]}")
                except Exception:
                    pass

        page.on("response", handle_response)

        try:
            print("📄 Loading ClicSanté...")
            page.goto(
                "https://portal3.clicsante.ca/services/blood-test",
                wait_until="networkidle",
                timeout=45000
            )
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
                    human_delay(0.5, 1)
                    break
                except:
                    continue
            if not selected:
                try:
                    page.locator("input[type='radio']").first.click(timeout=5000)
                    human_delay(0.5, 1)
                except:
                    pass

            # Enter postal code
            print(f"⌨️  Entering postal code: {postal_code}")
            for selector in ["input[placeholder*='A1A']", "input[type='text']"]:
                try:
                    field = page.locator(selector).first
                    field.click()
                    field.fill("")
                    field.type(postal_code, delay=80)
                    print(f"✅ Postal code entered")
                    break
                except:
                    continue

            human_delay(0.8, 1.5)

            # Click Search
            print("🔍 Clicking Search...")
            try:
                page.get_by_role("button", name=re.compile(r"Search|Rechercher", re.I)).first.click(timeout=5000)
                print("✅ Search clicked")
            except:
                pass

            # Wait for results
            print("⏳ Waiting for results...")
            human_delay(8, 14)

            # ★ THE KEY: Capture the results page URL — it has postal code + No fees baked in
            results_url = page.url
            print(f"📍 Results URL: {results_url[:120]}...")

            # Extract clinic names from DOM
            clinics = extract_clinics(page)

            # Check page body
            body_text = page.inner_text("body").lower()
            no_slots_signals = [
                "aucune disponibilité", "no availability",
                "aucun rendez-vous", "désolé", "sorry",
                "aucun résultat", "no results"
            ]
            positive_signals = [
                "disponible", "available", "réservation",
                "book", "places", "à venir", "prendre rendez-vous"
            ]

            has_positive = any(w in body_text for w in positive_signals)
            has_negative = any(w in body_text for w in no_slots_signals)

            print(f"\n📊 Results:")
            print(f"   Clinics in DOM: {len(clinics)}")
            print(f"   Positive: {has_positive}, Negative: {has_negative}")

            # Get best clinic name for notification
            best_name = None
            if clinics:
                for c in clinics:
                    name = c.get('name', '')
                    if name and len(name) > 3 and 'skip' not in name.lower():
                        best_name = name
                        break

            if has_positive and not has_negative:
                print(f"🎉 Slots available!")
                send_notification(postal_code, results_url, best_name)
                save_availability(postal_code, True, results_url, 
                                f"Results page with clinics", clinics[:10])
                return True, clinics
            elif has_negative:
                print(f"❌ No slots")
                save_availability(postal_code, False, results_url, "No slots", [])
                return False, []
            else:
                print(f"⚠️ Uncertain — sending results page")
                send_notification(postal_code, results_url, best_name)
                save_availability(postal_code, True, results_url, "Results page", clinics[:10])
                return True, clinics

        except Exception as e:
            print(f"🚨 Error: {e}")
            import traceback
            traceback.print_exc()
            return False, []
        finally:
            browser.close()


# === 7. MAIN ENTRY POINT ===

if __name__ == "__main__":
    test_codes = ["H1Y3H1", "H4L2B5", "H2X1Y7", "G1R2A3", "J8Y3H1"]

    for code in test_codes:
        success, clinics = check_availability(code)
        print(f"\n{'─'*40}")
        print(f"Result for {code}: {'✅ Found' if success else '❌ None'}")
        if clinics:
            for c in clinics[:3]:
                print(f"  - {c.get('name', 'Unknown')}")
        print(f"{'─'*40}\n")
        time.sleep(5)