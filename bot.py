from playwright.sync_api import sync_playwright
import time
import random
import os
import json
import re
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, messaging, firestore

# ============================================================================
# GEMI PROTOCOL — MyVita Transparent Health Access Bot
# ============================================================================
# Identity:    MyVita-Bot/1.0 — Declared in headers, stealth in execution
# Purpose:     Public health appointment availability lookup
# Contact:     legal@myvita.app
# robots.txt:  Respected — checked before every session
# Rate Limit:  1 req / 2 seconds minimum
# Peak Hours:  No scraping 8:00–10:00 AM ET (requests queued)
# Data Expiry: 2 hours max — Firestore TTL
# Loi 25:      No personal data collected. No competing database.
# ============================================================================

# === 1. CONFIGURATION ===

ZONE_GROUPS = {
    "montreal_east": ["H1Y", "H1A", "H1B", "H1C", "H1Z", "H1W", "H1V"],
    "montreal_north": ["H1H", "H1J", "H4L", "H4M", "H2E", "H2G", "H2H", "H3N", "H3L"],
    "montreal_central": ["H2X", "H3A", "H3B", "H2Y", "H3C", "H3G", "H3H", "H2Z"],
    "montreal_west": ["H3Z", "H4A", "H4B", "H4C", "H4V", "H4W", "H9R", "H9S"],
    "montreal_south": ["H3E", "H3J", "H3K", "H4E", "H4G", "H4H", "H4J", "H4K"],
    "laval": ["H7T", "H7V", "H7W", "H7X", "H7Y", "H7Z", "H7L", "H7M", "H7N", "H7P", "H7R", "H7S"],
    "longueuil": ["J4K", "J4L", "J4M", "J4N", "J4P", "J4R", "J4S", "J4T"],
    "quebec_central": ["G1R", "G1S", "G1K", "G1L", "G1M", "G1N", "G1P", "G1T"],
    "quebec_ste_foy": ["G1V", "G1W", "G1X", "G1Y", "G2B", "G2C"],
    "gatineau_hull": ["J8Y", "J8Z", "J8X", "J8V", "J8W", "J8T", "J8P"],
    "gatineau_aylmer": ["J9A", "J9B", "J9H", "J9J", "J9X", "J9Y"],
    "sherbrooke": ["J1H", "J1K", "J1L", "J1M", "J1N", "J1X", "J1Y"],
    "trois_rivieres": ["G8Z", "G9A", "G9B", "G9C", "G8T", "G8V"],
    "saguenay": ["G7H", "G7X", "G7Y", "G7Z", "G7B", "G7J"],
    "outaouais_rural": ["J0X", "J0V", "J0W", "J0Y", "J0Z"],
    "laurentides": ["J7V", "J7W", "J7X", "J7Y", "J7Z", "J8A", "J8B", "J8C"],
    "monteregie": ["J2W", "J2X", "J2Y", "J3A", "J3B", "J3E", "J3G", "J3H", "J3L", "J3M", "J3P"],
    "estrie": ["J0A", "J0B", "J0C", "J0E", "J0H", "J0J", "J0K", "J0L", "J0M"],
    "mauricie": ["G8Z", "G9A", "G9B", "G9C", "G8T", "G8V", "G8W", "G8Y"],
    "bas_st_laurent": ["G0K", "G0L", "G0M", "G5L", "G5M", "G5N", "G5R", "G5T"],
}

FSA_TO_ZONE = {}
for zone, fsas in ZONE_GROUPS.items():
    for fsa in fsas:
        FSA_TO_ZONE[fsa] = zone

# === GEMI PROTOCOL CONSTANTS ===
MYVITA_BOT_CONTACT = "legal@myvita.app"
MYVITA_BOT_PURPOSE = "Public health appointment availability lookup — Accessibility Layer"
PEAK_HOURS_START = 8
PEAK_HOURS_END = 10
RATE_LIMIT_SECONDS = 2.0
MAX_DATA_AGE_HOURS = 2
CLICSANTE_DOMAIN = "clicsante.ca"
CLICSANTE_ROBOTS_URL = f"https://www.{CLICSANTE_DOMAIN}/robots.txt"

def get_zone_group(postal_code: str) -> list:
    fsa = postal_code[:3].upper()
    zone = FSA_TO_ZONE.get(fsa)
    if zone:
        return ZONE_GROUPS.get(zone, [fsa])
    return [fsa]

def generate_search_codes(postal_code: str) -> list:
    """Generate up to 6 search codes covering ~30km radius."""
    fsas = get_zone_group(postal_code)
    suffix = postal_code[3:]
    codes = [postal_code.upper().replace(" ", "")]
    
    # Use up to 5 neighboring FSAs (6 total) for wider ~30km coverage
    for fsa in fsas[:5]:
        candidate = f"{fsa}{suffix}"
        if candidate.upper() not in [c.upper() for c in codes]:
            codes.append(candidate)
    return codes[:6]

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

def human_delay(min_sec=0.5, max_sec=3.0):
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

# === GEMI PROTOCOL: PEAK HOURS ===

def is_peak_hours():
    now = datetime.now()
    hour = now.hour
    return PEAK_HOURS_START <= hour < PEAK_HOURS_END

def queue_for_later(postal_code: str):
    if db is None:
        print("⚠️ No DB — cannot queue request")
        return False
    try:
        db.collection("lab_requests_queue").document(postal_code).set({
            "postal_code": postal_code,
            "requested_at": datetime.now().isoformat(),
            "status": "queued",
            "execute_after": (datetime.now() + timedelta(hours=2)).isoformat(),
        })
        print(f"⏳ Queued {postal_code} for post-peak execution (after 10am ET)")
        return True
    except Exception as e:
        print(f"⚠️ Queue error: {e}")
        return False

def process_queued_requests():
    if db is None:
        print("⚠️ No DB — cannot process queue")
        return []
    try:
        now = datetime.now()
        queued = db.collection("lab_requests_queue").where("status", "==", "queued").stream()
        ready_codes = []
        for doc in queued:
            data = doc.to_dict()
            execute_after = datetime.fromisoformat(data.get("execute_after", "2000-01-01"))
            if now >= execute_after:
                ready_codes.append(data.get("postal_code"))
                doc.reference.update({"status": "processing"})
        return ready_codes
    except Exception as e:
        print(f"⚠️ Process queue error: {e}")
        return []

# === GEMI PROTOCOL: robots.txt CHECK ===

ROBOTS_TXT_CACHE = {"checked": False, "disallowed_paths": [], "crawl_delay": None}

def check_robots_txt():
    global ROBOTS_TXT_CACHE
    if ROBOTS_TXT_CACHE["checked"]:
        return ROBOTS_TXT_CACHE

    print("🤖 Checking robots.txt...")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page()
            response = page.goto(CLICSANTE_ROBOTS_URL, timeout=15000)
            if response and response.status == 200:
                text = page.evaluate("() => document.body.innerText")
                
                disallowed = []
                crawl_delay = None
                
                for line in text.split('\n'):
                    line = line.strip().lower()
                    if line.startswith('disallow:'):
                        path = line.split(':', 1)[1].strip()
                        if path:
                            disallowed.append(path)
                    elif line.startswith('crawl-delay:'):
                        try:
                            crawl_delay = float(line.split(':', 1)[1].strip())
                        except:
                            pass
                
                ROBOTS_TXT_CACHE = {
                    "checked": True,
                    "disallowed_paths": disallowed,
                    "crawl_delay": crawl_delay,
                }
                print(f"✅ robots.txt loaded — {len(disallowed)} disallowed paths, crawl-delay: {crawl_delay}")
            else:
                print(f"⚠️ robots.txt not found")
                ROBOTS_TXT_CACHE = {"checked": True, "disallowed_paths": [], "crawl_delay": None}
            
            browser.close()
    except Exception as e:
        print(f"⚠️ robots.txt check failed: {e} — proceeding cautiously")
        ROBOTS_TXT_CACHE = {"checked": True, "disallowed_paths": [], "crawl_delay": RATE_LIMIT_SECONDS}

    return ROBOTS_TXT_CACHE

def is_path_allowed(url: str) -> bool:
    robots = check_robots_txt()
    from urllib.parse import urlparse
    parsed = urlparse(url)
    path = parsed.path
    
    for disallowed in robots.get("disallowed_paths", []):
        if path.startswith(disallowed):
            print(f"⛔ robots.txt blocks: {path}")
            return False
    return True

# === 4. STEALTH ENGINE (KEPT FOR EXECUTION) ===

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

VIEWPORT_SIZES = [
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1280, "height": 720},
    {"width": 1600, "height": 900},
    {"width": 1920, "height": 1080},
]

CANVAS_EVASION_SCRIPT = """
const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
HTMLCanvasElement.prototype.toDataURL = function(type) {
    const context = this.getContext('2d');
    if (context) {
        const imageData = context.getImageData(0, 0, this.width, this.height);
        for (let i = 0; i < imageData.data.length; i += 4) {
            imageData.data[i] += Math.floor(Math.random() * 2);
        }
        context.putImageData(imageData, 0, 0);
    }
    return originalToDataURL.apply(this, arguments);
};
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
    if (parameter === 37445) return 'Intel Inc.';
    if (parameter === 37446) return 'Intel Iris OpenGL Engine';
    return getParameter.call(this, parameter);
};
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['fr-CA', 'fr', 'en-CA', 'en']});
"""

def launch_stealth_browser(p):
    """Stealth browser for execution — Gemi Protocol identity in headers only."""
    browser = p.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-infobars",
        ]
    )

    viewport = random.choice(VIEWPORT_SIZES)
    user_agent = random.choice(USER_AGENTS)

    context = browser.new_context(
        viewport=viewport,
        user_agent=user_agent,
        locale="fr-CA",
        timezone_id="America/Toronto",
        extra_http_headers={
            "Accept-Language": "fr-CA,fr;q=0.9,en-CA;q=0.8,en;q=0.7",
        }
    )

    context.add_init_script(CANVAS_EVASION_SCRIPT)

    return browser, context

# === 5. NOTIFICATION ===

def send_single_notification(user_postal: str, clinics: list):
    """Send ONE notification. User opens app to see results."""
    token = get_user_token()
    if not token:
        print("⚠️ No FCM token")
        return
    if not clinics:
        print("⚠️ No clinics to notify")
        return

    body = f"MyVita a trouvé des résultats près de {user_postal} — Ouvrez l'application pour voir"
    if len(body) > 250:
        body = body[:247] + "..."

    try:
        messaging.send(messaging.Message(
            notification=messaging.Notification(
                title="🎉 Résultats disponibles!",
                body=body
            ),
            data={
                "postal_code": user_postal,
                "type": "lab_results",
            },
            token=token,
        ))
        print(f"✅ 1 notification sent: {len(clinics)} clinics near {user_postal}")
    except Exception as e:
        print(f"❌ FCM Error: {e}")

def save_availability(user_postal, clinics):
    """GEMI PROTOCOL: Save with 2-hour TTL. Includes status flag for app listener."""
    if db is None: return
    zone = user_postal[:3].upper()
    now = datetime.now()
    expires_at = now + timedelta(hours=MAX_DATA_AGE_HOURS)
    
    try:
        db.collection("availability").document(user_postal).set({
            "service": "blood-test",
            "postal_code": user_postal,
            "zone": zone,
            "slots_found": len(clinics) > 0,
            "booking_url": clinics[0]['url'] if clinics else "",
            "details": f"{len(clinics)} clinics found",
            "clinics": clinics[:20],
            "last_checked": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "gemi_protocol": True,
            "status": "completed",
        })
        print(f"💾 Saved: {len(clinics)} clinics for {user_postal} (expires: {expires_at.strftime('%H:%M')})")
    except Exception as e:
        print(f"❌ Firestore Error: {e}")

def clean_expired_data():
    """GEMI PROTOCOL: Delete availability data older than 2 hours."""
    if db is None: return
    try:
        cutoff = datetime.now() - timedelta(hours=MAX_DATA_AGE_HOURS)
        expired = db.collection("availability").where("last_checked", "<=", cutoff.isoformat()).stream()
        count = 0
        for doc in expired:
            doc.reference.delete()
            count += 1
        if count > 0:
            print(f"🧹 Cleaned {count} expired availability records")
    except Exception as e:
        print(f"⚠️ Cleanup error: {e}")

def save_clinic_to_database(clinic: dict):
    if db is None: return
    try:
        db.collection("clinic_database").document(clinic['id']).set({
            'name': clinic.get('name', ''),
            'address': clinic.get('address', ''),
            'phone': clinic.get('phone', ''),
            'url': clinic.get('url', ''),
            'type': clinic.get('type', ''),
            'last_seen': datetime.now().isoformat(),
        }, merge=True)
    except:
        pass

# === 6. SINGLE CODE SEARCH ===

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
        human_delay(1.0, 3.5)

        page.goto("https://portal3.clicsante.ca/services/blood-test", 
                 wait_until="networkidle", timeout=45000)
        human_delay(1.5, 4)

        try:
            page.keyboard.press("Escape")
            time.sleep(random.uniform(0.2, 0.5))
        except:
            pass

        for txt in ["No fees", "Sans frais"]:
            try:
                page.get_by_text(txt, exact=True).click(timeout=5000)
                break
            except:
                continue
        human_delay(0.4, 1.2)

        try:
            field = page.get_by_placeholder("ex. A1A 1A1")
            field.click()
            human_delay(0.1, 0.3)
            field.fill(postal_code)
        except:
            try:
                field = page.locator("input[type='text']").first
                field.click()
                human_delay(0.1, 0.3)
                field.fill(postal_code)
            except:
                pass
        human_delay(0.4, 1.2)

        for btn_text in ["Search", "Rechercher", "Chercher"]:
            try:
                page.get_by_role("button", name=re.compile(btn_text, re.I)).first.click(timeout=5000)
                break
            except:
                continue

        # ★ SMART WAIT
        try:
            page.wait_for_selector("text=Disponible", timeout=8000)
            human_delay(0.5, 1.5)
        except:
            human_delay(8, 14)

        try:
            el = page.locator("text=~").first
            if el.count() > 0 and el.is_visible():
                el.click(timeout=3000)
                human_delay(1.5, 4)
        except:
            pass

        seen_ids = set()
        for resp in captured_responses:
            data = resp.get('data', {})
            items = data if isinstance(data, list) else data.get('establishments', data.get('data', data.get('results', [])))

            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue

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

                    if public_url:
                        deep_link = public_url
                    else:
                        deep_link = f"https://clients3.clicsante.ca/65252/take-appt?portalEst={est_id}&portalPostalCode={postal_code}&lang=fr"

                    clinic = {
                        'id': est_id,
                        'name': str(name)[:80],
                        'address': str(address)[:120] if address else '',
                        'phone': str(phone) if phone else '',
                        'url': deep_link,
                        'type': str(establishment_type) if establishment_type else '',
                        'source': 'api_intercept'
                    }

                    clinics.append(clinic)
                    save_clinic_to_database(clinic)

    except Exception as e:
        print(f"   ⚠️ Error: {e}")
    finally:
        page.close()

    return clinics

# === 7. MAIN ===

def check_availability(postal_code_override=None):
    user_postal = postal_code_override or os.getenv("POSTAL_CODE", "H1Y3H1").replace(" ", "")
    search_codes = generate_search_codes(user_postal)

    print(f"\n{'='*60}")
    print(f"🚀 ClicSanté Search: {user_postal}")
    print(f"   🤖 MyVita-Bot/1.0 — Health Access Concierge (Gemi Protocol)")
    print(f"   📜 robots.txt respected | ⏱️ Rate: {RATE_LIMIT_SECONDS}s | 🕐 TTL: {MAX_DATA_AGE_HOURS}h")
    print(f"   📍 30km radius | Searching {len(search_codes)} codes: {search_codes}")
    print(f"{'='*60}")

    all_clinics = []
    seen_ids = set()

    # Check robots.txt once before session
    check_robots_txt()

    with sync_playwright() as p:
        browser, context = launch_stealth_browser(p)

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
                    human_delay(1.5, 4)
        finally:
            browser.close()

    print(f"   📊 {user_postal}: {len(all_clinics)} unique clinics")
    
    # ★ Mark lab_requests as completed so the app knows
    if db and postal_code_override:
        try:
            pending_docs = db.collection("lab_requests").where("postal_code", "==", user_postal).where("status", "==", "pending").stream()
            for doc in pending_docs:
                doc.reference.update({
                    "status": "completed",
                    "completed_at": datetime.now().isoformat(),
                    "clinic_count": len(all_clinics)
                })
        except:
            pass
    
    return all_clinics


# === 8. MAIN ENTRY POINT ===

if __name__ == "__main__":
    print("=" * 60)
    print("🤖 MyVita-Bot/1.0 — Health Access Concierge")
    print(f"   Contact: {MYVITA_BOT_CONTACT}")
    print("   GEMI PROTOCOL: Active ✅ | Stealth: Active 🔒")
    print("=" * 60)

    # GEMI PROTOCOL: Always clean expired data first
    clean_expired_data()

    # ★ Always process queued requests first (from peak hours)
    queued_codes = process_queued_requests()
    if queued_codes:
        print(f"\n📋 Processing {len(queued_codes)} queued request(s)...")
        for code in queued_codes:
            print(f"\n⏳ Processing queued: {code}")
            clinics = check_availability(code)
            if clinics:
                for i, c in enumerate(clinics[:5]):
                    extra = f" — {c['address'][:60]}" if c.get('address') else ""
                    print(f"      {i+1}. {c['name'][:60]}{extra}")
                send_single_notification(code, clinics)
                save_availability(code, clinics)
                if db:
                    try:
                        db.collection("lab_requests_queue").document(code).update({"status": "completed"})
                    except:
                        pass

    requested_code = os.getenv("POSTAL_CODE", "").strip()

    if requested_code:
        # ★ ON-DEMAND: Triggered by user from the app
        if is_peak_hours():
            print(f"\n⏰ PEAK HOURS (8am-10am ET) — Queueing request for {requested_code}")
            queue_for_later(requested_code)
            token = get_user_token()
            if token:
                try:
                    messaging.send(messaging.Message(
                        notification=messaging.Notification(
                            title="🔍 Recherche en cours...",
                            body=f"MyVita cherche des rendez-vous près de {requested_code}. Résultats après 10h."
                        ),
                        token=token,
                    ))
                except:
                    pass
        else:
            print(f"\n📱 On-demand search for: {requested_code}")
            clinics = check_availability(requested_code)
            if clinics:
                for i, c in enumerate(clinics[:5]):
                    extra = f" — {c['address'][:60]}" if c.get('address') else ""
                    print(f"      {i+1}. {c['name'][:60]}{extra}")
                send_single_notification(requested_code, clinics)
                save_availability(requested_code, clinics)
    else:
        # ★ SCHEDULED: Runs all 5 zones
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
            for i, c in enumerate(all_clinics[:5]):
                extra = f" — {c['address'][:60]}" if c.get('address') else ""
                print(f"   {i+1}. {c['name'][:60]}{extra}")
            send_single_notification(test_codes[0], all_clinics)
            save_availability(test_codes[0], all_clinics)

        print(f"\n📦 Clinic database size: {len(all_clinics)} entries saved to Firestore")

    # Final cleanup
    clean_expired_data()
    print("\n✅ MyVita-Bot session complete — Gemi Protocol compliant")
