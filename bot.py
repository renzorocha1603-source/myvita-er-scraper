from playwright.sync_api import sync_playwright
import time
import random
import os
import json
import re
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, messaging, firestore

# === 1. CONFIGURATION ===

ZONE_GROUPS = {
    "montreal_east": ["H1Y", "H1A", "H1B", "H1C", "H1Z", "H1W", "H1V"],
    "montreal_north": ["H1H", "H1J", "H4L", "H4M", "H2E", "H2G", "H2H", "H3N", "H3L"],
    "montreal_central": ["H2X", "H3A", "H3B", "H2Y", "H3C", "H3G", "H3H", "H2Z"],
    "montreal_west": ["H3Z", "H4A", "H4B", "H4C", "H4V", "H4W"],
    "montreal_south": ["H3E", "H3J", "H3K", "H4E", "H4G", "H4H", "H4J", "H4K"],
    "laval": ["H7T", "H7V", "H7W", "H7X", "H7Y", "H7Z"],
    "longueuil": ["J4K", "J4L", "J4M", "J4N", "J4P", "J4R"],
    "quebec_central": ["G1R", "G1S", "G1K", "G1L", "G1M", "G1N"],
    "quebec_ste_foy": ["G1V", "G1W", "G1X", "G1Y"],
    "gatineau_hull": ["J8Y", "J8Z", "J8X", "J8V", "J8W"],
    "sherbrooke": ["J1H", "J1K", "J1L", "J1M", "J1N"],
    "trois_rivieres": ["G8Z", "G9A", "G9B", "G9C"],
    "saguenay": ["G7H", "G7X", "G7Y", "G7Z"],
}

FSA_TO_ZONE = {}
for zone, fsas in ZONE_GROUPS.items():
    for fsa in fsas:
        FSA_TO_ZONE[fsa] = zone

def get_zone_group(postal_code: str) -> list:
    fsa = postal_code[:3].upper()
    zone = FSA_TO_ZONE.get(fsa)
    if zone:
        return ZONE_GROUPS.get(zone, [fsa])
    return [fsa]

def generate_search_codes(postal_code: str) -> list:
    fsas = get_zone_group(postal_code)
    suffix = postal_code[3:]
    codes = [postal_code]
    for fsa in fsas[:3]:
        candidate = f"{fsa}{suffix}"
        if candidate not in codes:
            codes.append(candidate)
    return codes[:4]

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
        print("✅ Firebase initialized")
    elif os.path.exists("firebase-credentials.json"):
        cred = credentials.Certificate("firebase-credentials.json")
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred, {'projectId': 'myvita-app-c5ecd'})
        db = firestore.client()
        print("✅ Firebase initialized (local)")
    else:
        print("⚠️ No Firebase credentials")
except Exception as e:
    print(f"⚠️ Firebase Init Error: {e}")

# === 3. UTILITIES ===

def human_delay(min_sec=0.8, max_sec=2.5):
    time.sleep(random.uniform(min_sec, max_sec))

def get_user_token():
    if db is None: return None
    try:
        docs = db.collection('users').order_by('fcmTokenUpdated', direction='DESCENDING').limit(10).stream()
        for doc in docs:
            token = doc.to_dict().get('fcmToken')
            if token: return token
    except: pass
    return None

# === 4. NOTIFICATION ===

def send_final_notification(user_postal: str, all_results: dict):
    """Send ONE notification after all postal codes are searched."""
    token = get_user_token()
    if not token:
        print("⚠️ No FCM token")
        return

    # Flatten all clinics from all codes
    all_clinics = []
    seen_ids = set()
    for code, clinics in all_results.items():
        for clinic in clinics:
            if clinic['id'] not in seen_ids:
                seen_ids.add(clinic['id'])
                all_clinics.append(clinic)

    if not all_clinics:
        print("⚠️ No clinics to notify")
        return

    top = all_clinics[:5]
    lines = [f"🎉 {len(all_clinics)} créneaux près de {user_postal}:"]
    for i, c in enumerate(top):
        name = c.get('name', 'Clinic')[:50]
        lines.append(f"{i+1}. {name}")

    body = "\n".join(lines)
    if len(body) > 250:
        body = body[:247] + "..."
    best_url = top[0].get('url', 'https://portal3.clicsante.ca/services/blood-test')

    try:
        messaging.send(messaging.Message(
            notification=messaging.Notification(title="🎉 Rendez-vous disponibles!", body=body),
            data={"url": best_url, "postal_code": user_postal},
            token=token,
        ))
        print(f"✅ ONE notification sent: {len(all_clinics)} total clinics, {len(top)} shown")
    except Exception as e:
        print(f"❌ FCM Error: {e}")

def save_availability(user_postal, clinics):
    if db is None: return
    zone = user_postal[:3].upper()
    now = datetime.now().isoformat()
    try:
        db.collection("availability").document(user_postal).set({
            "service": "blood-test", "postal_code": user_postal,
            "zone": zone, "slots_found": len(clinics) > 0,
            "booking_url": clinics[0]['url'] if clinics else "",
            "details": f"{len(clinics)} clinics found",
            "clinics": clinics[:20], "last_checked": now,
        })
    except Exception as e:
        print(f"❌ Firestore Error: {e}")

# === 5. SINGLE CODE SEARCH ===

def search_single_code(postal_code: str, browser_context) -> list:
    clinics = []
    captured_responses = []
    page = browser_context.new_page()

    def on_response(response):
        try:
            url = response.url
            if ('clicsante' in url or 'api' in url) and response.status == 200:
                ct = response.headers.get('content-type', '')
                if 'json' in ct:
                    body = response.json()
                    captured_responses.append({'url': url, 'data': body})
        except:
            pass

    page.on("response", on_response)

    try:
        page.goto("https://portal3.clicsante.ca/services/blood-test", 
                 wait_until="networkidle", timeout=45000)
        human_delay(1.5, 3)

        try:
            page.keyboard.press("Escape")
            time.sleep(0.3)
        except:
            pass

        for txt in ["No fees", "Sans frais"]:
            try:
                page.get_by_text(txt, exact=True).click(timeout=5000)
                break
            except:
                continue
        human_delay(0.5, 1)

        try:
            page.get_by_placeholder("ex. A1A 1A1").fill(postal_code)
        except:
            try:
                page.locator("input[type='text']").first.fill(postal_code)
            except:
                pass
        human_delay(0.5, 1)

        for btn_text in ["Search", "Rechercher", "Chercher"]:
            try:
                page.get_by_role("button", name=re.compile(btn_text, re.I)).first.click(timeout=5000)
                break
            except:
                continue

        human_delay(8, 12)

        try:
            el = page.locator("text=~").first
            if el.count() > 0 and el.is_visible():
                el.click(timeout=3000)
                human_delay(2, 4)
        except:
            pass

        # Parse API responses
        seen_ids = set()
        for resp in captured_responses:
            data = resp.get('data', {})
            items = data if isinstance(data, list) else data.get('establishments', data.get('data', data.get('results', [])))

            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    
                    # ★ Only process items with a 'name' field (real establishments, not service templates)
                    name = item.get('name', '')
                    if not name:
                        continue
                    
                    est_id = str(item.get('id', ''))
                    if not est_id or len(est_id) < 3:
                        continue
                    
                    if est_id in seen_ids:
                        continue
                    
                    seen_ids.add(est_id)

                    address = item.get('address', '')
                    public_url = item.get('public_url', '')
                    phone = item.get('phone', '')
                    establishment_type = item.get('establishment_type', '')

                    # ★ Build URL — prefer public_url from API
                    if public_url:
                        deep_link = public_url
                    else:
                        deep_link = f"https://clients3.clicsante.ca/65252/take-appt?portalEst={est_id}&portalPostalCode={postal_code}&lang=fr"

                    clinics.append({
                        'id': est_id,
                        'name': str(name)[:80],
                        'address': str(address)[:120] if address else '',
                        'phone': str(phone) if phone else '',
                        'url': deep_link,
                        'type': str(establishment_type) if establishment_type else '',
                        'source': 'api_intercept'
                    })

    except Exception as e:
        print(f"   ⚠️ Error: {e}")
    finally:
        page.close()

    return clinics

# === 6. MAIN ===

def check_availability(postal_code_override=None):
    """Collect results for ONE postal code. Returns list of clinics."""
    user_postal = postal_code_override or os.getenv("POSTAL_CODE", "H1Y3H1").replace(" ", "")
    search_codes = generate_search_codes(user_postal)

    print(f"\n{'='*60}")
    print(f"🚀 ClicSanté Search: {user_postal}")
    print(f"   Searching {len(search_codes)} codes: {search_codes}")
    print(f"{'='*60}")

    all_clinics = []
    seen_ids = set()

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

        try:
            for i, code in enumerate(search_codes):
                print(f"\n🔍 [{i+1}/{len(search_codes)}] {code}")
                clinics = search_single_code(code, context)

                new_count = 0
                for clinic in clinics:
                    if clinic['id'] not in seen_ids:
                        seen_ids.add(clinic['id'])
                        all_clinics.append(clinic)
                        new_count += 1

                print(f"   ✅ {len(clinics)} found, {new_count} new (total: {len(all_clinics)})")

                if i < len(search_codes) - 1:
                    human_delay(2, 4)
        finally:
            browser.close()

    print(f"   📊 {user_postal}: {len(all_clinics)} unique clinics")
    return all_clinics


# === 7. MAIN ENTRY POINT ===
# Collects ALL results first, then sends ONE notification at the end

if __name__ == "__main__":
    test_codes = ["H1Y3H1", "H4L2B5", "H2X1Y7", "G1R2A3", "J8Y3H1"]
    
    all_results = {}
    
    for code in test_codes:
        clinics = check_availability(code)
        all_results[code] = clinics
        if clinics:
            for i, c in enumerate(clinics[:3]):
                extra = f" — {c['address'][:60]}" if c.get('address') else ""
                print(f"      {i+1}. {c['name'][:60]}{extra}")
        time.sleep(5)
    
    # ── COLLECT ALL, SEND ONE ──
    # Flatten and deduplicate across all postal codes
    all_clinics = []
    seen_all = set()
    for code, clinics in all_results.items():
        for clinic in clinics:
            if clinic['id'] not in seen_all:
                seen_all.add(clinic['id'])
                all_clinics.append(clinic)
    
    print(f"\n{'='*60}")
    print(f"🏁 FINAL: {len(all_clinics)} unique clinics across all codes")
    
    if all_clinics:
        for i, c in enumerate(all_clinics[:10]):
            extra = f" — {c['address'][:60]}" if c.get('address') else ""
            print(f"   {i+1}. {c['name'][:60]}{extra}")
        
        # Send ONE notification with all results
        token = get_user_token()
        if token:
            top = all_clinics[:5]
            lines = [f"🎉 {len(all_clinics)} créneaux disponibles:"]
            for i, c in enumerate(top):
                name = c.get('name', 'Clinic')[:50]
                lines.append(f"{i+1}. {name}")
            body = "\n".join(lines)
            if len(body) > 250:
                body = body[:247] + "..."
            best_url = top[0].get('url', 'https://portal3.clicsante.ca/services/blood-test')
            
            try:
                messaging.send(messaging.Message(
                    notification=messaging.Notification(title="🎉 Rendez-vous disponibles!", body=body),
                    data={"url": best_url, "postal_code": test_codes[0]},
                    token=token,
                ))
                print(f"\n✅ ONE notification sent with {len(top)} clinics shown")
            except Exception as e:
                print(f"❌ FCM Error: {e}")
        
        save_availability(test_codes[0], all_clinics)
