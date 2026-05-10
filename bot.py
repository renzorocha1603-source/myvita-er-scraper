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

def send_notification(postal_code: str, booking_url: str = None, clinic_names: list = None):
    token = get_user_token()
    if not token:
        print("⚠️ No FCM token — skipping notification")
        return
    try:
        if booking_url and 'clients3.clicsante.ca' in booking_url:
            body = "Touchez pour ouvrir la page de réservation!"
            url = booking_url
        elif clinic_names:
            names_text = ", ".join(clinic_names[:3])
            body = f"{names_text} — Créneaux disponibles! Ouvrez ClicSanté → {postal_code} → Sans frais"
            url = "https://portal3.clicsante.ca/services/blood-test"
        else:
            body = f"Créneaux disponibles près de {postal_code}. Ouvrez ClicSanté → Sans frais → {postal_code}"
            url = "https://portal3.clicsante.ca/services/blood-test"
        
        if len(body) > 250:
            body = body[:247] + "..."
        
        messaging.send(messaging.Message(
            notification=messaging.Notification(title="🎉 Rendez-vous disponible!", body=body),
            data={"url": url, "postal_code": postal_code},
            token=token,
        ))
        print(f"✅ FCM Sent: {body[:100]}")
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
    except Exception as e:
        print(f"❌ Firestore Error: {e}")

# === 5. CLINIC NAME EXTRACTOR ===

def extract_clinic_names(page) -> list:
    clinics = []
    try:
        body = page.inner_text("body")
        lines = body.split('\n')
        clinic_keywords = ['hospital', 'hôpital', 'clinique', 'clinic', 'clsc', 'gmf',
                          'santé', 'sante', 'medical', 'médical', 'cegep', 'cégep',
                          'point de service', 'notre-dame', 'rosemont', 'saint-laurent',
                          'prélèvements', 'prelevements', 'maisonneuve', 'cabrini', 'santa']
        for i, line in enumerate(lines):
            line = line.strip()
            if len(line) < 8 or '~' in line or 'km' in line.lower():
                continue
            if line.lower().startswith(('skip', 'all', 'cancel', 'need', 'login', 'fr', 
                                        'specimens', 'fees', 'establishment', 'availabilities', 
                                        'results', 'service', 'add to')):
                continue
            # Skip lines that look like addresses (start with number, contain "rue", "boul", "avenue", "Montréal", "Québec")
            if re.match(r'^\d+', line) or any(w in line.lower() for w in ['rue ', 'boul', 'avenue', 'montréal', 'québec']):
                continue
            if any(kw in line.lower() for kw in clinic_keywords):
                address = lines[i+1].strip() if i+1 < len(lines) else ""
                if address and ('~' in address or 'km' in address.lower()):
                    address = ""
                clinics.append({'name': line[:100], 'address': address[:100] if address else ""})
        seen = set()
        unique = []
        for c in clinics:
            key = c['name'][:40].lower()
            if key not in seen:
                seen.add(key)
                unique.append(c)
        return unique
    except:
        return []

# === 6. MAIN FUNCTION ===

def check_availability(postal_code_override=None):
    postal_code = postal_code_override or os.getenv("POSTAL_CODE", "H1Y3H1").replace(" ", "")

    print(f"\n{'='*60}")
    print(f"🚀 ClicSanté: {postal_code} @ {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}")

    captured_responses = []

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

        # ★ Intercept ALL API responses
        def on_response(response):
            try:
                url = response.url
                if ('clicsante' in url or 'api' in url) and response.status == 200:
                    ct = response.headers.get('content-type', '')
                    if 'json' in ct:
                        body = response.json()
                        captured_responses.append({'url': url, 'data': body})
                        if any(kw in url for kw in ['availability', 'etablissement', 'establishment', 'serviceTemplate']):
                            print(f"📡 API: {url[:120]}")
            except:
                pass

        page.on("response", on_response)

        try:
            # ── LOAD PAGE ──
            print("📄 Loading ClicSanté...")
            page.goto("https://portal3.clicsante.ca/services/blood-test", 
                     wait_until="networkidle", timeout=45000)
            human_delay(2, 3)

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
            print(f"⌨️  Entering: {postal_code}")
            try:
                page.get_by_placeholder("ex. A1A 1A1").fill(postal_code)
                print("   ✅ Entered")
            except:
                try:
                    page.locator("input[type='text']").first.fill(postal_code)
                    print("   ✅ Entered (fallback)")
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

            # ── WAIT FOR RESULTS + API CALLS ──
            print("⏳ Waiting for results and API responses...")
            human_delay(8, 14)

            # ── TAKE SCREENSHOT OF RESULTS PAGE ──
            screenshot_path = f"debug_results_{postal_code}.png"
            try:
                page.screenshot(path=screenshot_path)
                print(f"📸 Screenshot saved: {screenshot_path}")
            except:
                pass

            # ★ CLAUDE: Click into clinic to trigger more API calls
            print("🏥 Clicking into first clinic to trigger API calls...")
            click_attempts = [
                "article", "[class*='establishment']", "[class*='clinic-card']",
                "[class*='result-item']", ".mat-card", "text=~"
            ]
            for selector in click_attempts:
                try:
                    el = page.locator(selector).first
                    if el.count() > 0 and el.is_visible():
                        el.click(timeout=3000)
                        print(f"   ✅ Clicked via: {selector}")
                        human_delay(3, 5)
                        
                        # Take screenshot after clicking clinic
                        try:
                            page.screenshot(path=f"debug_clinic_{postal_code}.png")
                            print(f"📸 Clinic screenshot saved")
                        except:
                            pass
                        break
                except:
                    continue

            # Try "Prendre RDV" button
            rdv_clicked = False
            for selector in [
                "button:has-text('Prendre RDV')", "button:has-text('Prendre rendez-vous')",
                "a:has-text('Prendre RDV')", "a:has-text('Prendre rendez-vous')",
                "button:has-text('Book')", "button:has-text('Réserver')"
            ]:
                try:
                    el = page.locator(selector).first
                    if el.count() > 0 and el.is_visible():
                        el.click(timeout=5000)
                        print(f"   ✅ Clicked RDV: {selector}")
                        human_delay(3, 5)
                        rdv_clicked = True
                        
                        # ★ CAPTURE URL AFTER CLICKING "PRENDRE RDV"
                        deep_link_url = page.url
                        print(f"   🔗 URL after RDV click: {deep_link_url[:120]}")
                        
                        # Take screenshot of booking page
                        try:
                            page.screenshot(path=f"debug_booking_{postal_code}.png")
                            print(f"📸 Booking page screenshot saved")
                        except:
                            pass
                        break
                except:
                    continue

            print(f"   📡 Captured {len(captured_responses)} API responses")

            # ★ CLAUDE: Parse API responses for establishment IDs
            print("\n🔬 Parsing API responses for clinic data...")
            clinic_deep_links = []
            seen_ids = set()

            for resp in captured_responses:
                data = resp.get('data', {})
                
                # Print raw data structure for debugging
                if 'availability' in resp.get('url', ''):
                    print(f"   📦 Availability API keys: {list(data.keys()) if isinstance(data, dict) else 'LIST'}")
                
                items = data if isinstance(data, list) else data.get('establishments', data.get('data', data.get('results', [])))
                
                if isinstance(items, list):
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        est_id = str(item.get('id') or item.get('establishmentId') or item.get('etablissementId', ''))
                        name = item.get('name') or item.get('nom') or item.get('establishmentName', '')
                        
                        if est_id and est_id not in seen_ids and len(est_id) >= 3:
                            seen_ids.add(est_id)
                            portal_id = str(item.get('portalId', '65252'))
                            deep_link = f"https://clients3.clicsante.ca/{portal_id}/take-appt"
                            params = [f"portalEst={est_id}", f"portalPostalCode={postal_code}", "lang=fr"]
                            deep_link += "?" + "&".join(params)
                            
                            clinic_deep_links.append({
                                'id': est_id, 'name': str(name)[:80], 'url': deep_link
                            })
                            print(f"   🏥 {name[:60]} → {deep_link[:100]}")

            # ★ Extract clinic names from page
            print("\n📋 Extracting clinic names from page...")
            page_clinics = extract_clinic_names(page)
            for i, c in enumerate(page_clinics[:5]):
                print(f"   {i+1}. {c['name']}")

            # ── CHECK BODY ──
            body = page.inner_text("body").lower()
            no_slots = ["aucune disponibilité", "no availability", "aucun rendez-vous", "désolé", "sorry"]
            has_positive = any(x in body for x in ["km", "clinique", "hôpital", "disponible", "available", "à venir"])
            has_negative = any(x in body for x in no_slots)

            print(f"\n📊 Results:")
            print(f"   API deep links: {len(clinic_deep_links)}")
            print(f"   Page clinics: {len(page_clinics)}")
            print(f"   RDV clicked: {rdv_clicked}")
            print(f"   Positive: {has_positive}, Negative: {has_negative}")

            # ★ If we clicked "Prendre RDV" successfully, use the browser URL
            if rdv_clicked and deep_link_url and 'clicsante' in deep_link_url:
                print(f"🎉 Using URL from RDV click: {deep_link_url[:120]}")
                send_notification(postal_code, booking_url=deep_link_url, clinic_names=[c['name'] for c in page_clinics[:3]])
                save_availability(postal_code, True, deep_link_url, "RDV click URL", page_clinics)
                return True

            # If API gave us deep links, use those
            elif clinic_deep_links:
                best = clinic_deep_links[0]
                print(f"🎉 Sending API deep link: {best['url'][:100]}")
                send_notification(postal_code, booking_url=best['url'], clinic_names=[c['name'] for c in clinic_deep_links[:3]])
                save_availability(postal_code, True, best['url'], f"{len(clinic_deep_links)} deep links", clinic_deep_links)
                return True

            # Fallback: clinic names in notification
            elif page_clinics and has_positive and not has_negative:
                names = [c['name'] for c in page_clinics[:3]]
                print(f"🎉 Sending clinic names: {names}")
                send_notification(postal_code, clinic_names=names)
                save_availability(postal_code, True, "", f"{len(page_clinics)} clinics", page_clinics)
                return True

            elif has_negative:
                print("❌ No slots")
                save_availability(postal_code, False, "", "No slots", [])
                return False
            else:
                print("⚠️ Uncertain")
                send_notification(postal_code, clinic_names=[])
                save_availability(postal_code, True, "", "Uncertain", [])
                return True

        except Exception as e:
            print(f"🚨 Error: {e}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            browser.close()


# === 7. MAIN ===

if __name__ == "__main__":
    for code in ["H1Y3H1"]:
        check_availability(code)
        time.sleep(5)
