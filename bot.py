from playwright.sync_api import sync_playwright
import time
import os
import json
import re
import random
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import firebase_admin
from firebase_admin import credentials, messaging, firestore

# === FIREBASE SETUP ===
FIREBASE_CREDENTIALS_JSON = os.getenv("FIREBASE_CREDENTIALS", "")
FIREBASE_CRED_PATH = os.getenv("FIREBASE_CRED_PATH", "firebase-credentials.json")

db = None
if FIREBASE_CREDENTIALS_JSON:
    try:
        cred_dict = json.loads(FIREBASE_CREDENTIALS_JSON)
        cred = credentials.Certificate(cred_dict)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred, {'projectId': 'myvita-app-c5ecd'})
        db = firestore.client()
        print("✅ Firebase initialized (GitHub Secret)")
    except Exception as e:
        print(f"⚠️ Firebase init error from secret: {e}")
elif os.path.exists(FIREBASE_CRED_PATH):
    try:
        cred = credentials.Certificate(FIREBASE_CRED_PATH)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred, {'projectId': 'myvita-app-c5ecd'})
        db = firestore.client()
        print("✅ Firebase initialized (local file)")
    except Exception as e:
        print(f"⚠️ Firebase init error from file: {e}")
else:
    print("⚠️ Firebase credentials not found — notifications & Firestore disabled")

# === ZONE MAPPING ===
ZONES = {
    "H1Y": "montreal_east", "H1A": "montreal_east", "H1B": "montreal_east",
    "H1C": "montreal_east", "H1H": "montreal_north", "H1J": "montreal_north",
    "H1Z": "montreal_north", "H2X": "montreal_central", "H3A": "montreal_central",
    "H3B": "montreal_central", "H7A": "laval", "H7B": "laval", "H7C": "laval",
    "H7L": "laval", "H7M": "laval", "H7N": "laval", "H7P": "laval",
    "H7R": "laval", "H7S": "laval", "H7T": "laval", "H7V": "laval",
    "H7W": "laval", "H7X": "laval", "H7Y": "laval",
    "J4A": "longueuil", "J4B": "longueuil", "J4K": "longueuil", "J4L": "longueuil",
    "G1A": "quebec", "G1R": "quebec", "G1S": "quebec", "G1V": "quebec_ste_foy",
    "J8P": "gatineau", "J8Y": "gatineau", "J8Z": "gatineau",
    "J1H": "sherbrooke", "J1K": "sherbrooke",
    "G8Z": "trois_rivieres", "G9A": "trois_rivieres",
    "G7H": "saguenay", "G7X": "saguenay",
}

# === 5 DIFFERENT HUMAN FINGERPRINTS ===
BROWSER_PROFILES = [
    {
        "name": "User-1-Chrome-Win",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "viewport": {"width": 1366, "height": 768},
        "locale": "fr-CA",
        "timezone": "America/Montreal",
        "delay": 0,
    },
    {
        "name": "User-2-Safari-Mac",
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
        "viewport": {"width": 1440, "height": 900},
        "locale": "fr-CA",
        "timezone": "America/Montreal",
        "delay": 15,
    },
    {
        "name": "User-3-Firefox-Win",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "viewport": {"width": 1536, "height": 864},
        "locale": "en-CA",
        "timezone": "America/Toronto",
        "delay": 30,
    },
    {
        "name": "User-4-Chrome-Linux",
        "user_agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "viewport": {"width": 1280, "height": 720},
        "locale": "fr-CA",
        "timezone": "America/Montreal",
        "delay": 45,
    },
    {
        "name": "User-5-iPhone",
        "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
        "viewport": {"width": 390, "height": 844},
        "locale": "fr-CA",
        "timezone": "America/Montreal",
        "is_mobile": True,
        "delay": 60,
    },
]

def get_zone(postal_code: str) -> str:
    fsa = postal_code[:3].upper()
    return ZONES.get(fsa, f"zone_{fsa}")

def get_postal_code():
    postal = os.getenv("POSTAL_CODE", "").replace(" ", "").strip()
    if postal and len(postal) >= 3:
        return postal
    return "H1Y3H1"

def is_peak_hours() -> bool:
    now = datetime.now()
    hour = now.hour
    return 8 <= hour < 10

def get_user_token():
    if db is None:
        return None
    try:
        users_ref = db.collection('users').order_by('fcmTokenUpdated', direction='DESCENDING').limit(1)
        for doc in users_ref.stream():
            token = doc.to_dict().get('fcmToken')
            if token:
                return token
    except:
        pass
    return None

def save_to_firestore(postal_code: str, places_found: list):
    if db is None:
        print("⚠️ Firestore not available — skipping")
        return
    fsa = postal_code[:3].upper()
    zone = get_zone(postal_code)
    now = datetime.now()
    data = {
        "service": "blood-test", "postal_code": postal_code, "fsa": fsa,
        "zone": zone, "status": "completed", "clinics": places_found,
        "places_found": places_found, "slots_found": len(places_found) > 0,
        "last_checked": now,
    }
    try:
        db.collection("availability").document(postal_code).set(data)
        db.collection("availability").document(fsa).set(data)
        print(f"🔥 Saved to Firestore: availability/{postal_code} + availability/{fsa}")
        requests_ref = db.collection("lab_requests").where("postal_code", "==", fsa).where("status", "in", ["pending", "processing", "dispatched"]).stream()
        for req in requests_ref:
            req.reference.update({"status": "completed", "results": places_found, "completed_at": now})
    except Exception as e:
        print(f"❌ Firestore save failed: {e}")

def send_notification(postal_code: str, places_found: list):
    token = get_user_token()
    if not token:
        print("⚠️ No FCM token found — skipping notification")
        return
    place_names = ", ".join([p.get('name', 'Unknown')[:30] for p in places_found[:3]])
    first_url = places_found[0].get('url', '') if places_found else ''
    title = "🏥 Résultats ClicSanté disponibles!"
    body = f"{len(places_found)} lieux trouvés près de {postal_code}. Ouvre l'app pour voir!"
    data_payload = {
        "url": first_url if first_url else f"https://portal3.clicsante.ca/?postalCode={postal_code.replace(' ', '+')}&serviceId=227",
        "postal_code": postal_code, "click_action": "OPEN_APPOINTMENTS", "place_count": str(len(places_found)),
    }
    for i, p in enumerate(places_found[:5]):
        data_payload[f"place_{i}_name"] = p.get('name', 'Unknown')[:100]
        data_payload[f"place_{i}_url"] = p.get('url', '')[:500]
    try:
        messaging.send(messaging.Message(notification=messaging.Notification(title=title, body=body), data=data_payload, token=token))
        print(f"✅ 1 notification sent with {len(places_found)} places")
    except Exception as e:
        print(f"❌ Notification failed: {e}")

def add_to_queue(postal_code: str):
    if db is None: return
    try:
        db.collection("lab_requests_queue").document(postal_code).set({"postal_code": postal_code, "status": "queued", "queued_at": datetime.now()})
        print(f"⏳ Added to queue: {postal_code}")
    except Exception as e:
        print(f"❌ Queue failed: {e}")


# ══════════════════════════════════════════════════════════════
# ★ SINGLE WORKER — DEBUG MODE: Dump HTML + extract cards ★
# ══════════════════════════════════════════════════════════════

def run_human_browser(profile: dict, postal_code: str, worker_id: int) -> list:
    profile_name = profile.get("name", f"User-{worker_id}")
    stagger_delay = profile.get("delay", worker_id * 15)

    if stagger_delay > 0:
        print(f"   [{profile_name}] ⏳ Waiting {stagger_delay}s...")
        time.sleep(stagger_delay)

    print(f"\n   [{profile_name}] 🧑 Starting...")
    places = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport=profile.get("viewport", {"width": 1280, "height": 720}),
            user_agent=profile.get("user_agent"),
            locale=profile.get("locale", "fr-CA"),
            timezone_id=profile.get("timezone", "America/Montreal"),
            is_mobile=profile.get("is_mobile", False),
            has_touch=profile.get("has_touch", False),
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()

        try:
            time.sleep(random.uniform(1, 3))
            page.goto("https://portal3.clicsante.ca/services/blood-test", wait_until="networkidle", timeout=60000)
            print(f"   [{profile_name}] 📄 Page loaded")
            time.sleep(random.uniform(1.5, 3))
            time.sleep(random.uniform(0.5, 1.5))
            try:
                page.locator("text=Sans frais").first.click(timeout=8000)
                print(f"   [{profile_name}] ✅ Sans frais")
            except:
                try:
                    page.locator("text=No fees").first.click(timeout=5000)
                except:
                    pass
            time.sleep(random.uniform(1, 2))
            try:
                inputs = page.locator("input[type='text']").all()
                if inputs:
                    inputs[0].click()
                    time.sleep(random.uniform(0.3, 0.8))
                    inputs[0].fill("")
                    time.sleep(random.uniform(0.2, 0.5))
                    inputs[0].type(postal_code, delay=random.randint(100, 200))
                    print(f"   [{profile_name}] ✅ Postal: {postal_code}")
            except:
                pass
            time.sleep(random.uniform(1.5, 3))
            try:
                page.get_by_role("button", name="Search").first.click(timeout=8000)
                print(f"   [{profile_name}] ✅ Search")
            except:
                try:
                    page.get_by_role("button", name="Rechercher").first.click(timeout=5000)
                except:
                    page.keyboard.press("Enter")
            time.sleep(random.uniform(2, 4))
            print(f"   [{profile_name}] ⏳ Waiting for results...")
            try:
                page.wait_for_selector("text=km", timeout=30000)
                print(f"   [{profile_name}] ✅ Results loaded")
            except:
                print(f"   [{profile_name}] ⚠️ Waiting anyway...")
            time.sleep(random.uniform(3, 6))

            # ══════════════════════════════════════════════════
            # ★ DEBUG: Save full page HTML ★
            # ══════════════════════════════════════════════════
            print(f"   [{profile_name}] 📁 Saving full page HTML for debugging...")
            try:
                html = page.content()
                filename = f"clicsante_debug_{profile_name.replace(' ', '_').replace('-', '_')}.html"
                with open(filename, "w", encoding="utf-8") as f:
                    f.write(html)
                print(f"   [{profile_name}] 📁 Saved: {filename} ({len(html)} chars)")
            except Exception as e:
                print(f"   [{profile_name}] ⚠️ HTML save failed: {e}")

            # ══════════════════════════════════════════════════
            # ★ EXTRACT: Find booking links and their parent cards ★
            # ══════════════════════════════════════════════════
            print(f"   [{profile_name}] 🔍 Extracting clinic data...")

            book_links = page.locator("a[href*='take-appt']").all()
            print(f"   [{profile_name}] 📎 Found {len(book_links)} 'Book appt.' links")

            for i, link in enumerate(book_links):
                try:
                    href = link.get_attribute("href") or ""
                    clinic_id_match = re.search(r'/(\d+)/take-appt', href)
                    if not clinic_id_match:
                        continue
                    clinic_id = clinic_id_match.group(1)
                    url = f"https://clients3.clicsante.ca/{clinic_id}/take-appt"

                    # ══════════════════════════════════════════
                    # ★ METHOD 1: Get parent card/row text ★
                    # ══════════════════════════════════════════
                    card_info = link.evaluate("""el => {
                        let current = el;
                        let result = { tagPath: [], texts: [] };
                        
                        // Walk up to find the card container
                        for (let level = 0; level < 10; level++) {
                            if (!current || !current.parentElement) break;
                            current = current.parentElement;
                            let tag = current.tagName;
                            let cls = current.className || '';
                            let id = current.id || '';
                            result.tagPath.push(tag + (cls ? '.' + cls.split(' ')[0] : '') + (id ? '#' + id : ''));
                            
                            let text = current.innerText?.trim();
                            if (text && text.length > 40 && text.length < 800) {
                                result.texts.push({
                                    level: level,
                                    length: text.length,
                                    text: text.substring(0, 400),
                                    tagPath: [...result.tagPath]
                                });
                            }
                        }
                        return result;
                    }""")

                    # ══════════════════════════════════════════
                    # ★ METHOD 2: Find heading/name near the link ★
                    # ══════════════════════════════════════════
                    nearby_heading = link.evaluate("""el => {
                        // Look for h2, h3, h4, strong tags in the same container
                        let parent = el.closest('li, article, div[class*="result"], div[class*="card"], div[class*="item"]');
                        if (!parent) parent = el.closest('div');
                        if (!parent) return '';
                        
                        let headings = parent.querySelectorAll('h1, h2, h3, h4, h5, strong, b, [class*="name"], [class*="title"], [class*="heading"]');
                        for (let h of headings) {
                            let text = h.innerText?.trim();
                            if (text && text.length > 5 && text.length < 200 && text !== 'Book appt.') {
                                return text;
                            }
                        }
                        return '';
                    }""")

                    # ══════════════════════════════════════════
                    # ★ METHOD 3: Get ALL text in the row/li ★
                    # ══════════════════════════════════════════
                    row_text = link.evaluate("""el => {
                        let row = el.closest('li, tr, [class*="row"], [class*="item"], [class*="result"]');
                        if (row) return row.innerText?.trim()?.substring(0, 500);
                        return '';
                    }""")

                    # ══════════════════════════════════════════
                    # ★ METHOD 4: Get previous sibling text ★
                    # ══════════════════════════════════════════
                    prev_text = link.evaluate("""el => {
                        let prev = el.previousElementSibling;
                        let count = 0;
                        while (prev && count < 5) {
                            let text = prev.innerText?.trim();
                            if (text && text.length > 5 && text.length < 200 && text !== 'Book appt.') {
                                return text;
                            }
                            prev = prev.previousElementSibling;
                            count++;
                        }
                        return '';
                    }""")

                    # ══════════════════════════════════════════
                    # ★ DEBUG: Print all extracted info ★
                    # ══════════════════════════════════════════
                    print(f"\n   [{profile_name}] 📦 Link #{i+1} (ID: {clinic_id})")
                    print(f"   [{profile_name}]    🔗 URL: {url}")
                    print(f"   [{profile_name}]    📝 Nearby heading: {nearby_heading[:120] if nearby_heading else 'NONE'}")
                    print(f"   [{profile_name}]    📝 Previous sibling: {prev_text[:120] if prev_text else 'NONE'}")
                    print(f"   [{profile_name}]    📝 Row text (first 200): {row_text[:200] if row_text else 'NONE'}")

                    if card_info and card_info.get('texts'):
                        print(f"   [{profile_name}]    📦 Container texts found at levels:")
                        for t in card_info['texts'][:3]:
                            print(f"   [{profile_name}]       Level {t['level']} ({t['length']} chars): {t['text'][:150]}")

                    # ══════════════════════════════════════════
                    # ★ PICK THE BEST NAME ★
                    # ══════════════════════════════════════════
                    name = ""

                    # Priority 1: Nearby heading
                    if nearby_heading and len(nearby_heading) > 5:
                        name = nearby_heading[:150]

                    # Priority 2: Previous sibling
                    if not name and prev_text and len(prev_text) > 5:
                        name = prev_text[:150]

                    # Priority 3: First meaningful line from row text
                    if not name and row_text:
                        lines = row_text.split('\n')
                        for line in lines:
                            line = line.strip()
                            if line and line != "Book appt." and len(line) > 10 and len(line) < 200:
                                if "km" not in line.lower() and "book" not in line.lower():
                                    name = line[:150]
                                    break

                    # Priority 4: First meaningful text from container
                    if not name and card_info and card_info.get('texts'):
                        for t in card_info['texts']:
                            text = t['text']
                            lines = text.split('\n')
                            for line in lines:
                                line = line.strip()
                                if line and line != "Book appt." and len(line) > 10 and len(line) < 200:
                                    if "km" not in line.lower() and "book" not in line.lower():
                                        name = line[:150]
                                        break
                            if name:
                                break

                    # Fallback
                    if not name:
                        name = f"Clinique #{clinic_id}"

                    print(f"   [{profile_name}]    ✅ SELECTED NAME: {name[:120]}")

                    place = {"name": name[:150], "url": url}
                    if place not in places:
                        places.append(place)

                    if len(places) >= 5:
                        break

                except Exception as e:
                    print(f"   [{profile_name}]    ❌ Link #{i+1} error: {e}")

            print(f"\n   [{profile_name}] ✅ Found {len(places)} places with debug info")

        except Exception as e:
            print(f"   [{profile_name}] ❌ Error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            browser.close()

    return places[:5]


def check_availability():
    postal_code = get_postal_code()
    zone = get_zone(postal_code)

    if is_peak_hours():
        print(f"\n{'='*60}")
        print(f"⏰ PEAK HOURS (8am-10am) — Request queued")
        print(f"{'='*60}")
        add_to_queue(postal_code)
        return True

    print(f"\n{'='*60}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] 📍 {postal_code} | {zone}")
    print(f"🧑 5 human-like browsers (DEBUG MODE — saving HTML)...")
    print(f"{'='*60}")

    all_places = []
    seen_names = set()

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(run_human_browser, profile, postal_code, i): i for i, profile in enumerate(BROWSER_PROFILES)}
        for future in as_completed(futures):
            try:
                places = future.result()
                for p in places:
                    if p['name'] not in seen_names:
                        seen_names.add(p['name'])
                        all_places.append(p)
            except Exception as e:
                print(f"   ❌ Worker failed: {e}")

    all_places = all_places[:5]

    print(f"\n{'='*60}")
    print(f"✅ FINAL RESULTS: {len(all_places)} places near {postal_code}")
    print(f"{'='*60}")
    for i, p in enumerate(all_places):
        print(f"\n   {i+1}. 📍 {p.get('name')}")
        print(f"      🔗 {p.get('url')}")

    save_to_firestore(postal_code, all_places)
    send_notification(postal_code, all_places)
    print(f"\n🎉 Done! {len(all_places)} choices saved + 1 notification sent.")
    print(f"📁 HTML debug files saved as clicsante_debug_*.html")
    return True


if __name__ == "__main__":
    check_availability()
