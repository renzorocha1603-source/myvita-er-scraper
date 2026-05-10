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
        print(f"✅ FCM Notification Sent → {booking_url[:120]}...")
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

# === 5. MAIN FUNCTION ===

def check_availability(postal_code_override=None):
    """
    ClicSanté scraper — captures API responses, builds deep links.
    Uses the availabilitiesByGeolocalisation API endpoint.
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

        # Intercept ALL API responses
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
                        print(f"📡 Captured: {url[:120]}")
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
            print("⏳ Waiting for results and API responses...")
            human_delay(10, 16)

            # ★ PARSE THE AVAILABILITY API RESPONSE
            clinics = []
            availability_data = None

            for api_response in captured_api_responses:
                url = api_response.get('url', '')
                data = api_response.get('data', {})

                if 'availabilitiesByGeolocalisation' in url:
                    availability_data = data
                    print(f"\n📦 AVAILABILITY API FOUND")
                    print(f"   Type: {type(data).__name__}")
                    
                    if isinstance(data, dict):
                        print(f"   Top-level keys: {list(data.keys())[:10]}")
                        # Print first 2000 chars of the raw JSON
                        raw_json = json.dumps(data, indent=2)
                        print(f"   RAW JSON (first 2000 chars):")
                        print(raw_json[:2000])
                        print("   ...")
                        
                        # Try to find establishments list
                        items = None
                        for key in ['establishments', 'data', 'results', 'items', 'clinics', 'etablissements']:
                            if key in data:
                                items = data[key]
                                print(f"   Found '{key}' with {len(items) if isinstance(items, list) else 'non-list'} items")
                                break
                        
                        if items is None and isinstance(data, list):
                            items = data
                        
                        if isinstance(items, list) and len(items) > 0:
                            print(f"   First item keys: {list(items[0].keys()) if isinstance(items[0], dict) else 'not dict'}")
                            print(f"   First item sample: {json.dumps(items[0], indent=2)[:500]}")
                            
                            # Parse each establishment
                            for item in items[:20]:
                                if isinstance(item, dict):
                                    est_id = str(item.get('establishmentId', item.get('id', item.get('etablissementId', ''))))
                                    name = item.get('establishmentName', item.get('name', item.get('nom', '')))
                                    address = item.get('address', item.get('adresse', ''))
                                    portal_id = str(item.get('portalId', '65252'))
                                    portal_place = str(item.get('portalPlaceId', item.get('placeId', item.get('portalPlace', ''))))
                                    services = item.get('servicesUnified', item.get('portalServicesUnified', ''))
                                    
                                    # Check for actual availability
                                    avail = item.get('availabilities', item.get('disponibilites', []))
                                    has_slots = len(avail) > 0 if isinstance(avail, list) else bool(avail)
                                    
                                    if est_id:
                                        # Build URL like the working one
                                        formatted_postal = postal_code[:3] + "+" + postal_code[3:]
                                        clinic_url = f"https://clients3.clicsante.ca/{portal_id}/take-appt"
                                        params = [
                                            f"portalEst={est_id}",
                                            f"portalPostalCode={postal_code[:3]}%20{postal_code[3:]}",
                                            f"lang=fr",
                                        ]
                                        if services:
                                            params.append(f"portalServicesUnified={services if isinstance(services, str) else ','.join(map(str, services))}")
                                        if portal_place:
                                            params.append(f"portalPlace={portal_place}")
                                        
                                        clinic_url += "?" + "&".join(params)
                                        
                                        clinics.append({
                                            'name': str(name) if name else 'Clinic',
                                            'address': str(address) if address else '',
                                            'url': clinic_url,
                                            'id': est_id,
                                            'has_slots': has_slots,
                                            'source': 'api_availability'
                                        })
                    elif isinstance(data, list):
                        print(f"   List with {len(data)} items")
                        if len(data) > 0 and isinstance(data[0], dict):
                            print(f"   First item keys: {list(data[0].keys())}")
                            print(f"   First item: {json.dumps(data[0], indent=2)[:500]}")

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
            print(f"   Clinics with deep links: {len(clinics)}")
            print(f"   Positive signals: {has_positive}")
            print(f"   Negative signals: {has_negative}")

            if clinics:
                print(f"\n🎉 {len(clinics)} clinic(s) with deep links!")
                for i, c in enumerate(clinics[:5]):
                    print(f"   {i+1}. {c.get('name', 'Unknown')}")
                    print(f"      {c.get('url', '')[:120]}")

                best_clinic = clinics[0]
                send_notification(postal_code, best_clinic['url'], best_clinic.get('name'))
                save_availability(postal_code, True, best_clinic['url'],
                                f"{len(clinics)} clinics", clinics[:10])
                return True, clinics

            elif has_positive and not has_negative:
                # Solid fallback: send the search URL we know works
                results_url = f"https://portal3.clicsante.ca/?serviceId=227&postalCode={postal_code[:3]}+{postal_code[3:]}"
                print(f"⚠️ Using results page: {results_url}")
                send_notification(postal_code, results_url)
                save_availability(postal_code, True, results_url, "Results page", [])
                return True, []

            elif has_negative:
                print(f"❌ No slots")
                save_availability(postal_code, False, "", "No slots", [])
                return False, []

            else:
                print(f"⚠️ Uncertain")
                save_availability(postal_code, False, "", "Uncertain", [])
                return False, []

        except Exception as e:
            print(f"🚨 Error: {e}")
            import traceback
            traceback.print_exc()
            return False, []
        finally:
            browser.close()


# === 7. MAIN ENTRY POINT ===

if __name__ == "__main__":
    test_codes = ["H1Y3H1"]
    
    for code in test_codes:
        success, clinics = check_availability(code)
        print(f"\n{'─'*40}")
        print(f"Result for {code}: {'✅ Found' if success else '❌ None'}")
        if clinics:
            for c in clinics[:3]:
                print(f"  - {c.get('name', 'Unknown')}")
                print(f"    {c.get('url', '')[:120]}")
        print(f"{'─'*40}\n")
        time.sleep(3)
