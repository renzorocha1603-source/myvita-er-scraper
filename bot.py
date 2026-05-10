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

# === 5. DEEP LINK BUILDER ===

def build_clinic_url(portal_id: str, establishment_id: str, postal_code: str,
                     portal_services: str, portal_place: str = "", lang: str = "fr") -> str:
    """
    Build the real ClicSanté deep link from API data.
    Matches the working format: clients3.clicsante.ca/{portalId}/take-appt?portalEst=...&...
    """
    formatted_postal = postal_code[:3] + "+" + postal_code[3:]
    
    url = f"https://clients3.clicsante.ca/{portal_id}/take-appt"
    params = []
    params.append(f"portalEst={establishment_id}")
    params.append(f"portalPostalCode={postal_code[:3]}%20{postal_code[3:]}")
    params.append(f"lang={lang}")
    if portal_services:
        params.append(f"portalServicesUnified={portal_services}")
    if portal_place:
        params.append(f"portalPlace={portal_place}")
    
    return url + "?" + "&".join(params)


def extract_deep_links_from_api(captured_api_responses, postal_code):
    """
    Parse the availabilitiesByGeolocalisation API response
    and extract real clinic booking URLs.
    """
    clinics = []

    for api_response in captured_api_responses:
        url = api_response.get('url', '')
        data = api_response.get('data', {})

        if 'availabilitiesByGeolocalisation' in url:
            print(f"📦 Parsing availability API response...")

            # The API response structure: list of establishments with availability
            items = data if isinstance(data, list) else data.get('establishments', data.get('data', []))

            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        est_id = str(item.get('establishmentId', item.get('id', '')))
                        name = item.get('establishmentName', item.get('name', item.get('nom', '')))
                        address = item.get('address', item.get('adresse', ''))
                        portal_id = str(item.get('portalId', '65252'))
                        portal_place = str(item.get('portalPlaceId', item.get('placeId', '')))
                        services = item.get('servicesUnified', item.get('portalServicesUnified', ''))
                        
                        # Check if this establishment actually has available slots
                        availabilities = item.get('availabilities', item.get('disponibilites', []))
                        has_slots = len(availabilities) > 0 if isinstance(availabilities, list) else bool(availabilities)

                        if est_id and has_slots:
                            clinic_url = build_clinic_url(
                                portal_id=portal_id,
                                establishment_id=est_id,
                                postal_code=postal_code,
                                portal_services=services if isinstance(services, str) else '',
                                portal_place=portal_place
                            )
                            clinics.append({
                                'name': str(name) if name else 'Clinic',
                                'address': str(address) if address else '',
                                'url': clinic_url,
                                'id': est_id,
                                'has_slots': has_slots,
                                'source': 'api_availability'
                            })
                            print(f"   🏥 {name} → {clinic_url[:100]}...")

    # If no clinics with slots from availability API, fall back to any API data
    if not clinics:
        for api_response in captured_api_responses:
            data = api_response.get('data', {})
            items = data if isinstance(data, list) else data.get('establishments', data.get('data', []))

            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        est_id = str(item.get('establishmentId', item.get('id', '')))
                        name = item.get('establishmentName', item.get('name', item.get('nom', '')))
                        portal_id = str(item.get('portalId', '65252'))
                        
                        if est_id:
                            clinic_url = build_clinic_url(
                                portal_id=portal_id,
                                establishment_id=est_id,
                                postal_code=postal_code,
                                portal_services='',
                                portal_place=''
                            )
                            clinics.append({
                                'name': str(name) if name else 'Clinic',
                                'url': clinic_url,
                                'id': est_id,
                                'has_slots': True,
                                'source': 'api_fallback'
                            })

    return clinics


# === 6. MAIN FUNCTION ===

def check_availability(postal_code_override=None):
    """
    Searches ClicSanté, intercepts API responses,
    builds real deep links to clinic booking pages.
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

        # Intercept ALL API responses from ClicSanté backend
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
                        # Only print key APIs to reduce noise
                        if any(kw in url for kw in ['availability', 'etablissement', 'establishment']):
                            print(f"📡 Captured: {url[:100]}")
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

            # Wait for results and API calls to complete
            print("⏳ Waiting for results and API responses...")
            human_delay(10, 16)

            # ★ BUILD DEEP LINKS FROM API DATA
            clinics = extract_deep_links_from_api(captured_api_responses, postal_code)

            # Check page body for availability
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
            print(f"   API clinics with slots: {len(clinics)}")
            print(f"   Positive signals: {has_positive}")
            print(f"   Negative signals: {has_negative}")

            if clinics:
                print(f"\n🎉 {len(clinics)} clinic(s) with deep links!")
                for i, c in enumerate(clinics[:5]):
                    print(f"   {i+1}. {c.get('name', 'Unknown')}")
                    print(f"      {c.get('url', '')[:100]}")

                best_clinic = clinics[0]
                best_url = best_clinic['url']
                best_name = best_clinic.get('name')

                send_notification(postal_code, best_url, best_name)
                save_availability(postal_code, True, best_url,
                                f"{len(clinics)} clinics with deep links", clinics[:10])
                return True, clinics

            elif has_positive and not has_negative:
                # API didn't give us URLs but page shows availability
                # Fall back to results page URL
                results_url = f"https://portal3.clicsante.ca/?serviceId=227&postalCode={postal_code[:3]}+{postal_code[3:]}"
                print(f"⚠️ Using results page fallback: {results_url}")
                send_notification(postal_code, results_url)
                save_availability(postal_code, True, results_url, "Results page fallback", [])
                return True, []

            elif has_negative:
                print(f"❌ No slots available")
                save_availability(postal_code, False, "", "No slots", [])
                return False, []

            else:
                print(f"⚠️ Uncertain — no notification sent")
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
                print(f"    {c.get('url', '')[:100]}")
        print(f"{'─'*40}\n")
        time.sleep(3)
