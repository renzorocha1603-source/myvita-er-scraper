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

# === 5. CLINIC EXTRACTOR (Claude's 4 strategies) ===

def extract_clinics(page) -> list:
    """
    Extract all clinic cards from the SPA results page.
    Returns a list of dicts with name, address, and booking URL.
    """
    clinics = []

    try:
        # Wait for clinic cards to appear
        page.wait_for_selector(
            "article, .establishment-card, [class*='clinic'], [class*='establishment'], [class*='result-item']",
            timeout=15000
        )
        human_delay(1, 2)

        # ★ Strategy 1: Direct links inside result cards
        links = page.eval_on_selector_all(
            "a[href*='clicsante'], a[href*='etablissement'], a[href*='clinic'], a[href*='appointment']",
            """elements => elements.map(el => ({
                href: el.href,
                text: el.innerText.trim()
            }))"""
        )

        if links:
            print(f"📎 Found {len(links)} direct clinic links")
            for link in links:
                if link['href'] and len(link['href']) > 30:
                    clinics.append({
                        'name': link['text'] or 'Clinic',
                        'url': link['href'],
                        'source': 'direct_link'
                    })

        # ★ Strategy 2: Read SPA state (Angular/React data)
        api_data = page.evaluate("""() => {
            const results = [];
            if (window.__STORE__) results.push(JSON.stringify(window.__STORE__));
            if (window.__state__) results.push(JSON.stringify(window.__state__));
            const cards = document.querySelectorAll('[data-id], [data-establishment-id], [data-clinic-id]');
            cards.forEach(card => {
                const id = card.dataset.id || card.dataset.establishmentId || card.dataset.clinicId;
                const name = card.querySelector('h2, h3, h4, .name, .title')?.innerText || '';
                const address = card.querySelector('address, .address, [class*="address"]')?.innerText || '';
                if (id) results.push(JSON.stringify({id, name, address}));
            });
            return results;
        }""")

        if api_data:
            print(f"📊 Found {len(api_data)} data items from page state")
            for item in api_data:
                try:
                    data = json.loads(item)
                    if isinstance(data, dict) and data.get('id'):
                        clinic_url = f"https://portal3.clicsante.ca/etablissement/{data['id']}/prise-de-rendez-vous"
                        clinics.append({
                            'name': data.get('name', 'Clinic'),
                            'address': data.get('address', ''),
                            'url': clinic_url,
                            'source': 'data_attribute'
                        })
                except:
                    pass

        # ★ Strategy 3: Button data attributes
        buttons = page.eval_on_selector_all(
            "button, [role='button'], .btn",
            """elements => elements.map(el => ({
                text: el.innerText.trim(),
                onclick: el.getAttribute('onclick') || '',
                dataId: el.dataset.id || el.dataset.establishmentId || '',
                ariaLabel: el.getAttribute('aria-label') || ''
            }))"""
        )

        for btn in buttons:
            text = btn.get('text', '').lower()
            if any(kw in text for kw in ['rendez-vous', 'réserver', 'book', 'appointment', 'prendre']):
                data_id = btn.get('dataId', '')
                if data_id:
                    clinic_url = f"https://portal3.clicsante.ca/etablissement/{data_id}/prise-de-rendez-vous"
                    clinics.append({
                        'name': btn.get('ariaLabel', 'Clinic'),
                        'url': clinic_url,
                        'source': 'button_data_id'
                    })

        # ★ Strategy 4: Text extraction with ID regex
        card_texts = page.eval_on_selector_all(
            "article, .establishment-card, [class*='clinic-card'], [class*='result-item']",
            """elements => elements.map(el => ({
                text: el.innerText.trim(),
                html: el.innerHTML
            }))"""
        )

        if card_texts and not clinics:
            print(f"📋 Extracting {len(card_texts)} clinic cards by text")
            for card in card_texts[:10]:
                text = card.get('text', '')
                if text and len(text) > 10:
                    html = card.get('html', '')
                    id_match = re.search(
                        r'(?:etablissement|establishment|clinic)[/\-_]?(\d+)',
                        html, re.IGNORECASE
                    )
                    if id_match:
                        est_id = id_match.group(1)
                        clinic_url = f"https://portal3.clicsante.ca/etablissement/{est_id}/prise-de-rendez-vous"
                    else:
                        clinic_url = page.url

                    clinics.append({
                        'name': text[:80],
                        'url': clinic_url,
                        'source': 'card_text'
                    })

    except Exception as e:
        print(f"⚠️ Clinic extraction error: {e}")

    # Deduplicate by URL
    seen_urls = set()
    unique_clinics = []
    for clinic in clinics:
        url = clinic.get('url', '')
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique_clinics.append(clinic)

    print(f"✅ {len(unique_clinics)} unique clinics extracted from DOM")
    return unique_clinics

# === 6. MAIN FUNCTION WITH API INTERCEPTION (Claude's core insight) ===

def check_availability_with_intercept(postal_code_override=None):
    """
    Intercepts ClicSanté's SPA API calls to capture real establishment data.
    Falls back to DOM extraction if API interception misses anything.
    Falls back to results page URL as last resort (Grok's approach).
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

        # ★ CLAUDE'S CORE INSIGHT: Intercept API responses from ClicSanté backend
        def handle_response(response):
            url = response.url
            if any(kw in url for kw in [
                'api', 'establishment', 'etablissement', 'availability',
                'disponibilite', 'search', 'clinic', 'services'
            ]):
                try:
                    if response.status == 200:
                        content_type = response.headers.get('content-type', '')
                        if 'json' in content_type:
                            body = response.json()
                            captured_api_responses.append({
                                'url': url,
                                'data': body
                            })
                            print(f"📡 Captured API: {url[:100]}")
                except Exception:
                    pass  # Some responses can't be parsed as JSON

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
            for text in ["No fees", "Sans frais", "Sans frais supplémentaires"]:
                try:
                    page.get_by_text(text, exact=True).first.click(timeout=5000)
                    selected = True
                    print(f"✅ '{text}' selected")
                    human_delay(0.5, 1)
                    break
                except:
                    continue

            if not selected:
                print("⚠️ Trying radio buttons...")
                try:
                    page.locator("input[type='radio']").first.click(timeout=5000)
                    selected = True
                    human_delay(0.5, 1)
                except:
                    pass

            # Enter postal code
            print(f"⌨️  Entering postal code: {postal_code}")
            entered = False
            for selector in [
                "input[placeholder*='A1A']",
                "input[placeholder*='postal']",
                "input[placeholder*='code']",
                "input[type='text']"
            ]:
                try:
                    field = page.locator(selector).first
                    field.click()
                    field.fill("")
                    field.type(postal_code, delay=80)
                    entered = True
                    print(f"✅ Postal code entered via: {selector}")
                    break
                except:
                    continue

            human_delay(0.8, 1.5)

            # Click Search
            print("🔍 Clicking Search...")
            for search_text in ["Search", "Rechercher", "Chercher"]:
                try:
                    page.get_by_role(
                        "button",
                        name=re.compile(search_text, re.I)
                    ).first.click(timeout=5000)
                    print(f"✅ Search clicked: {search_text}")
                    break
                except:
                    continue

            # Wait for SPA to render
            print("⏳ Waiting for results to render...")
            human_delay(8, 14)

            # Try to detect clinic cards
            try:
                page.wait_for_selector(
                    "article, .establishment-card, [class*='clinic'], [class*='result']",
                    timeout=20000
                )
                print("✅ Clinic cards detected")
            except:
                print("⚠️ No clinic cards detected — checking body text")

            # ★ CAPTURE RESULTS PAGE URL (Deep's addition — fallback)
            results_url = page.url
            print(f"📍 Results URL: {results_url[:120]}...")

            # ★ Extract clinics from DOM (Claude's 4 strategies)
            dom_clinics = extract_clinics(page)

            # ★ Parse intercepted API responses (Claude's API interception)
            api_clinics = []
            for api_response in captured_api_responses:
                data = api_response.get('data', {})
                items = data if isinstance(data, list) else \
                        data.get('results', data.get('establishments',
                        data.get('clinics', data.get('data', []))))

                if isinstance(items, list):
                    for item in items[:20]:
                        if isinstance(item, dict):
                            est_id = (item.get('id') or item.get('establishmentId') or
                                     item.get('etablissementId') or item.get('clinicId'))
                            name = (item.get('name') or item.get('nom') or
                                   item.get('title') or 'Clinic')
                            address = (item.get('address') or item.get('adresse') or
                                      item.get('fullAddress') or '')

                            if est_id:
                                clinic_url = f"https://portal3.clicsante.ca/etablissement/{est_id}/prise-de-rendez-vous"
                                api_clinics.append({
                                    'name': str(name),
                                    'address': str(address),
                                    'url': clinic_url,
                                    'id': str(est_id),
                                    'source': 'api_intercept'
                                })

            print(f"📡 API intercepted {len(api_clinics)} clinics")
            print(f"📋 DOM extracted {len(dom_clinics)} clinics")

            # Merge — API results are more reliable
            all_clinics = api_clinics if api_clinics else dom_clinics

            # Check page body for availability signals
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
            has_clinics = len(all_clinics) > 0

            print(f"\n📊 Results Summary:")
            print(f"   Clinics found: {len(all_clinics)}")
            print(f"   Positive signals: {has_positive}")
            print(f"   Negative signals: {has_negative}")

            if has_clinics or (has_positive and not has_negative):
                print(f"\n🎉 {len(all_clinics)} clinic(s) available!")

                for i, clinic in enumerate(all_clinics[:5]):
                    print(f"   {i+1}. {clinic.get('name', 'Unknown')}")
                    print(f"      URL: {clinic.get('url', 'N/A')[:100]}")

                # ★ BEST URL: First clinic's direct URL, or results page as fallback
                best_url = all_clinics[0]['url'] if all_clinics else results_url
                best_name = all_clinics[0].get('name') if all_clinics else None

                send_notification(postal_code, best_url, best_name)
                save_availability(
                    postal_code, True, best_url,
                    f"{len(all_clinics)} clinics found",
                    all_clinics[:10]
                )
                return True, all_clinics

            elif has_negative:
                print(f"❌ No slots available for {postal_code}")
                save_availability(
                    postal_code, False, results_url,
                    "No slots available", []
                )
                return False, []

            else:
                print(f"⚠️ Uncertain result — sending results page as fallback")
                # ★ DEEP'S ADDITION: Grok's approach as last resort
                send_notification(postal_code, results_url)
                save_availability(
                    postal_code, True, results_url,
                    "Results page (fallback)", []
                )
                return True, []

        except Exception as e:
            print(f"🚨 Critical Error: {e}")
            import traceback
            traceback.print_exc()
            return False, []
        finally:
            browser.close()


# === 7. MAIN ENTRY POINT ===

if __name__ == "__main__":
    test_codes = ["H1Y3H1", "H4L2B5", "H2X1Y7", "G1R2A3", "J8Y3H1"]

    for code in test_codes:
        success, clinics = check_availability_with_intercept(code)
        print(f"\n{'─'*40}")
        print(f"Result for {code}: {'✅ Found' if success else '❌ None'}")
        if clinics:
            print(f"First clinic URL: {clinics[0].get('url', 'N/A')}")
        print(f"{'─'*40}\n")
        time.sleep(5)
