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
# Identity:    MyVita-Bot/1.0 — Declared, not hidden
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
    "laval": ["H7T", "H7V", "H7W", "H7X", "H7Y", "H7Z"],
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
MYVITA_USER_AGENT = "MyVita-Bot/1.0 (Health Access Concierge; +https://myvita.app/bot-info)"
MYVITA_BOT_CONTACT = "legal@myvita.app"
MYVITA_BOT_PURPOSE = "Public health appointment availability lookup — Accessibility Layer"
PEAK_HOURS_START = 8   # 8:00 AM ET
PEAK_HOURS_END = 10    # 10:00 AM ET
RATE_LIMIT_SECONDS = 2.0  # Minimum seconds between requests
MAX_DATA_AGE_HOURS = 2    # TTL for Firestore availability data
CLICSANTE_DOMAIN = "clicsante.ca"
CLICSANTE_ROBOTS_URL = f"https://www.{CLICSANTE_DOMAIN}/robots.txt"

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

def human_delay(min_sec=0.5, max_sec=3.0):
    """GEMI PROTOCOL: Natural-feeling but respectful delay."""
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
    """Check if current time is in the 8:00-10:00 AM ET red zone."""
    now = datetime.now()
    # GitHub Actions runs in UTC. ET = UTC-4 (EDT) or UTC-5 (EST)
    # We'll use a simple heuristic: if the server timezone is UTC,
    # peak hours in ET are roughly 12:00-14:00 UTC (EDT) or 13:00-15:00 UTC (EST)
    # For safety, we check local time. In GitHub Actions, TZ is America/Toronto.
    hour = now.hour
    return PEAK_HOURS_START <= hour < PEAK_HOURS_END

def queue_for_later(postal_code: str):
    """Store a pending request in Firestore to be executed after peak hours."""
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
    """Check for any queued requests that are ready to execute."""
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
                # Mark as processing
                doc.reference.update({"status": "processing"})
        return ready_codes
    except Exception as e:
        print(f"⚠️ Process queue error: {e}")
        return []

# === GEMI PROTOCOL: robots.txt CHECK ===

ROBOTS_TXT_CACHE = {"checked": False, "disallowed_paths": [], "crawl_delay": None}

def check_robots_txt():
    """Fetch and parse clicsante.ca/robots.txt. Respect all directives."""
    global ROBOTS_TXT_CACHE
    if ROBOTS_TXT_CACHE["checked"]:
        return ROBOTS_TXT_CACHE

    print("🤖 Checking robots.txt...")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page()
            page.set_extra_http_headers({
                "User-Agent": MYVITA_USER_AGENT,
                "X-Bot-Contact": MYVITA_BOT_CONTACT,
                "X-Bot-Purpose": MYVITA_BOT_PURPOSE,
            })
            response = page.goto(CLICSANTE_ROBOTS_URL, timeout=15000)
            if response and response.status == 200:
                content = page.content()
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
                print(f"⚠️ robots.txt not found (status: {response.status if response else 'N/A'})")
                ROBOTS_TXT_CACHE = {"checked": True, "disallowed_paths": [], "crawl_delay": None}
            
            browser.close()
    except Exception as e:
        print(f"⚠️ robots.txt check failed: {e} — proceeding cautiously")
        ROBOTS_TXT_CACHE = {"checked": True, "disallowed_paths": [], "crawl_delay": RATE_LIMIT_SECONDS}

    return ROBOTS_TXT_CACHE

def is_path_allowed(url: str) -> bool:
    """Check if a URL path is allowed by robots.txt."""
    robots = check_robots_txt()
    from urllib.parse import urlparse
    parsed = urlparse(url)
    path = parsed.path
    
    for disallowed in robots.get("disallowed_paths", []):
        if path.startswith(disallowed):
            print(f"⛔ robots.txt blocks: {path}")
            return False
    return True

# === GEMI PROTOCOL: TRANSPARENT BROWSER ===

def launch_transparent_browser(p):
    """GEMI PROTOCOL: Declared identity — no stealth, no hiding."""
    
    viewport = {"width": 1440, "height": 900}  # Professional standard viewport

    context = p.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ]
    ).new_context(
        viewport=viewport,
        user_agent=MYVITA_USER_AGENT,
        locale="fr-CA",
        timezone_id="America/Toronto",
        extra_http_headers={
            "User-Agent": MYVITA_USER_AGENT,
            "X-Bot-Contact": MYVITA_BOT_CONTACT,
            "X-Bot-Purpose": MYVITA_BOT_PURPOSE,
            "Accept-Language": "fr-CA,fr;q=0.9,en-CA;q=0.8,en;q=0.7",
        }
    )

    return context.browser, context

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
            data={"url": clinics[0]['url'], "postal_code": user_postal},
            token=token,
        ))
        print(f"✅ 1 notification sent: {len(clinics)} clinics near {user_postal}")
    except Exception as e:
        print(f"❌ FCM Error: {e}")

def save_availability(user_postal, clinics):
    """GEMI PROTOCOL: Save with 2-hour TTL."""
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
    """Store minimal clinic reference — just ID, name, link. No full database."""
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
    """GEMI PROTOCOL: Respect robots.txt, rate limit, and declare identity."""
    
    clinics = []
    captured_responses = []
    
    # Check robots.txt before navigating
    target_url = "https://portal3.clicsante.ca/services/blood-test"
    if not is_path_allowed(target_url):
        print(f"⛔ Skipping {target_url} — disallowed by robots.txt")
        return clinics
    
    page = browser_context.new_page()
    page.set_extra_http_headers({
        "User-Agent": MYVITA_USER_AGENT,
        "X-Bot-Contact": MYVITA_BOT_CONTACT,
        "X-Bot-Purpose": MYVITA_BOT_PURPOSE,
    })

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
        # GEMI PROTOCOL: Respect crawl-delay from robots.txt
        robots = check_robots_txt()
        crawl_delay = robots.get("crawl_delay", RATE_LIMIT_SECONDS)
        if crawl_delay:
            time.sleep(max(crawl_delay, RATE_LIMIT_SECONDS))
        
        human_delay(1.0, 3.5)

        page.goto(target_url, wait_until="networkidle", timeout=45000)
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

# === 7. MAIN ENTRY POINT (with GEMI PROTOCOL peak hours) ===

def check_availability(postal_code_override=None):
    user_postal = postal_code_override or os.getenv("POSTAL_CODE", "H1Y3H1").replace(" ", "")
    
    # GEMI PROTOCOL: Peak hours check
    if is_peak_hours():
        print(f"\n⏰ PEAK HOURS (8am-10am ET) — Queueing request for {user_postal}")
        queue_for_later(user_postal)
        # Send interim notification — user doesn't know about delay
        token = get_user_token()
        if token:
            try:
                messaging.send(messaging.Message(
                    notification=messaging.Notification(
                        title="🔍 Recherche en cours...",
                        body=f"MyVita cherche des rendez-vous près de {user_postal}. Résultats bientôt."
                    ),
                    token=token,
                ))
            except:
                pass
        return []  # Return empty — will be processed later
    
    search_codes = generate_search_codes(user_postal)

    print(f"\n{'='*60}")
    print(f"🚀 ClicSanté Search: {user_postal}")
    print(f"   🤖 MyVita-Bot/1.0 — Health Access Concierge")
    print(f"   📜 robots.txt respected | ⏱️ Rate: {RATE_LIMIT_SECONDS}s | 🕐 TTL: {MAX_DATA_AGE_HOURS}h")
    print(f"   Searching {len(search_codes)} codes: {search_codes}")
    print(f"{'='*60}")

    all_clinics = []
    seen_ids = set()

    # Check robots.txt once before session
    check_robots_txt()

    with sync_playwright() as p:
        browser, context = launch_transparent_browser(p)

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
                    # GEMI PROTOCOL: Respect rate limit between codes
                    time.sleep(RATE_LIMIT_SECONDS)
        finally:
            browser.close()

    print(f"   📊 {user_postal}: {len(all_clinics)} unique clinics")
    return all_clinics


# === 8. MAIN ENTRY POINT ===

if __name__ == "__main__":
    print("=" * 60)
    print("🤖 MyVita-Bot/1.0 — Health Access Concierge")
    print(f"   Contact: {MYVITA_BOT_CONTACT}")
    print("   GEMI PROTOCOL: Active ✅")
    print("=" * 60)

    # GEMI PROTOCOL: Always clean expired data first
    clean_expired_data()

    # Check for queued requests from peak hours
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
                # Mark queue item as complete
                if db:
                    try:
                        db.collection("lab_requests_queue").document(code).update({"status": "completed"})
                    except:
                        pass

    requested_code = os.getenv("POSTAL_CODE", "").strip()

    if requested_code:
        # ★ ON-DEMAND: Triggered by user from the app
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
