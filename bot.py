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

# === ZONE MAPPING — All Quebec regions ===
ZONES = {
    "H1Y": "montreal_east", "H1A": "montreal_east", "H1B": "montreal_east",
    "H1C": "montreal_east", "H1H": "montreal_north", "H1J": "montreal_north",
    "H1Z": "montreal_north", "H2X": "montreal_central", "H3A": "montreal_central",
    "H3B": "montreal_central", "H2E": "montreal_north", "H2G": "montreal_north",
    "H2H": "montreal_north", "H2J": "montreal_central", "H2K": "montreal_central",
    "H2L": "montreal_central", "H2M": "montreal_north", "H2N": "montreal_north",
    "H2P": "montreal_north", "H2R": "montreal_north", "H2S": "montreal_north",
    "H2T": "montreal_central", "H2V": "montreal_central", "H2W": "montreal_central",
    "H2Y": "montreal_central", "H3C": "montreal_central", "H3E": "montreal_central",
    "H3G": "montreal_central", "H3H": "montreal_central", "H3J": "montreal_central",
    "H3K": "montreal_central", "H3L": "montreal_north", "H3M": "montreal_north",
    "H3N": "montreal_north", "H3P": "montreal_north", "H3R": "montreal_north",
    "H3S": "montreal_north", "H3T": "montreal_north", "H3V": "montreal_north",
    "H3W": "montreal_north", "H3X": "montreal_north", "H3Y": "montreal_north",
    "H4A": "montreal_west", "H4B": "montreal_west", "H4C": "montreal_west",
    "H4E": "montreal_west", "H4G": "montreal_west", "H4H": "montreal_west",
    "H4J": "montreal_west", "H4K": "montreal_west", "H4L": "montreal_north",
    "H4M": "montreal_north", "H4N": "montreal_north", "H4P": "montreal_west",
    "H4R": "montreal_west", "H4S": "montreal_west", "H4T": "montreal_west",
    "H4V": "montreal_west", "H4W": "montreal_west", "H4X": "montreal_west",
    "H4Y": "montreal_west", "H4Z": "montreal_west",
    "H8N": "montreal_south", "H8P": "montreal_south", "H8R": "montreal_south",
    "H8S": "montreal_south", "H8T": "montreal_south", "H8Y": "montreal_south",
    "H8Z": "montreal_south", "H9A": "montreal_west", "H9B": "montreal_west",
    "H9C": "montreal_west", "H9E": "montreal_west", "H9G": "montreal_west",
    "H9H": "montreal_west", "H9J": "montreal_west", "H9K": "montreal_west",
    "H7A": "laval", "H7B": "laval", "H7C": "laval", "H7E": "laval",
    "H7G": "laval", "H7H": "laval", "H7J": "laval", "H7K": "laval",
    "H7L": "laval", "H7M": "laval", "H7N": "laval", "H7P": "laval",
    "H7R": "laval", "H7S": "laval", "H7T": "laval", "H7V": "laval",
    "H7W": "laval", "H7X": "laval", "H7Y": "laval",
    "J4A": "longueuil", "J4B": "longueuil", "J4C": "longueuil", "J4G": "longueuil",
    "J4H": "longueuil", "J4J": "longueuil", "J4K": "longueuil", "J4L": "longueuil",
    "J4M": "longueuil", "J4N": "longueuil", "J4P": "longueuil", "J4R": "longueuil",
    "J4S": "longueuil", "J4T": "longueuil", "J4V": "longueuil", "J4W": "longueuil",
    "J4X": "longueuil", "J4Y": "longueuil", "J4Z": "longueuil",
    "G1A": "quebec", "G1B": "quebec", "G1C": "quebec", "G1E": "quebec",
    "G1G": "quebec", "G1H": "quebec", "G1J": "quebec", "G1K": "quebec",
    "G1L": "quebec", "G1M": "quebec", "G1N": "quebec", "G1P": "quebec",
    "G1R": "quebec", "G1S": "quebec", "G1T": "quebec", "G1V": "quebec_ste_foy",
    "G1W": "quebec_ste_foy", "G1X": "quebec_ste_foy", "G1Y": "quebec_ste_foy",
    "G2A": "quebec", "G2B": "quebec", "G2C": "quebec", "G2E": "quebec",
    "G2G": "quebec", "G2J": "quebec", "G2K": "quebec", "G2L": "quebec",
    "G2M": "quebec", "G2N": "quebec",
    "J8P": "gatineau", "J8R": "gatineau", "J8T": "gatineau", "J8V": "gatineau",
    "J8W": "gatineau", "J8X": "gatineau", "J8Y": "gatineau", "J8Z": "gatineau",
    "J9A": "gatineau", "J9H": "gatineau", "J9J": "gatineau",
    "J1A": "sherbrooke", "J1C": "sherbrooke", "J1E": "sherbrooke",
    "J1G": "sherbrooke", "J1H": "sherbrooke", "J1J": "sherbrooke",
    "J1K": "sherbrooke", "J1L": "sherbrooke", "J1M": "sherbrooke", "J1N": "sherbrooke",
    "G8T": "trois_rivieres", "G8V": "trois_rivieres", "G8W": "trois_rivieres",
    "G8Y": "trois_rivieres", "G8Z": "trois_rivieres", "G9A": "trois_rivieres",
    "G9B": "trois_rivieres", "G9C": "trois_rivieres",
    "G7A": "saguenay", "G7B": "saguenay", "G7G": "saguenay", "G7H": "saguenay",
    "G7J": "saguenay", "G7K": "saguenay", "G7N": "saguenay", "G7P": "saguenay",
    "G7S": "saguenay", "G7T": "saguenay", "G7X": "saguenay", "G7Y": "saguenay",
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

def get_user_token_for_user(user_id: str):
    """Get FCM token for a specific user."""
    if db is None:
        return None
    try:
        user_doc = db.collection('users').document(user_id).get()
        if user_doc.exists:
            return user_doc.to_dict().get('fcmToken')
    except:
        pass
    return None

def save_to_firestore(postal_code: str, places_found: list):
    """Save results to Firestore using USER-SPECIFIC document paths."""
    if db is None:
        print("⚠️ Firestore not available — skipping")
        return

    fsa = postal_code[:3].upper()
    zone = get_zone(postal_code)
    now = datetime.now()

    # Find the FIRST pending request for this postal code and get its user_id
    user_id = None
    try:
        requests_ref = db.collection("lab_requests") \
            .where("postal_code", "==", postal_code) \
            .where("status", "in", ["pending", "processing", "dispatched"]) \
            .order_by("requested_at", direction="ASCENDING") \
            .limit(1) \
            .stream()
        for req in requests_ref:
            req_data = req.to_dict()
            user_id = req_data.get('user_id', 'system')
            # Update this specific request
            req.reference.update({
                "status": "completed",
                "results": places_found,
                "completed_at": now
            })
            print(f"   ✅ Request updated for user: {user_id}")
            break
    except Exception as e:
        print(f"   ⚠️ Could not find matching request: {e}")

    if not user_id:
        user_id = 'system'
        print(f"   ⚠️ No user_id found — using 'system'")

    data = {
        "service": "blood-test",
        "postal_code": postal_code,
        "fsa": fsa,
        "zone": zone,
        "status": "completed",
        "clinics": places_found,
        "places_found": places_found,
        "slots_found": len(places_found) > 0,
        "user_id": user_id,
        "last_checked": now,
    }

    # ★ Save with USER-SPECIFIC document IDs
    try:
        db.collection("availability").document(f"{postal_code}_{user_id}").set(data)
        db.collection("availability").document(f"{fsa}_{user_id}").set(data)
        print(f"🔥 Saved: availability/{postal_code}_{user_id} + availability/{fsa}_{user_id}")
    except Exception as e:
        print(f"❌ Firestore save failed: {e}")

    return user_id

def send_notification(postal_code: str, places_found: list, user_id: str):
    """Send push notification to a SPECIFIC user."""
    token = get_user_token_for_user(user_id)
    if not token:
        print(f"⚠️ No FCM token for user {user_id} — skipping notification")
        return

    place_names = ", ".join([p.get('name', 'Unknown')[:30] for p in places_found[:3]])
    first_url = places_found[0].get('url', '') if places_found else ''
    title = "🏥 Résultats ClicSanté disponibles!"
    body = f"{len(places_found)} lieux trouvés près de {postal_code}. Ouvre l'app pour voir!"
    data_payload = {
        "url": first_url if first_url else f"https://portal3.clicsante.ca/?postalCode={postal_code.replace(' ', '+')}&serviceId=227",
        "postal_code": postal_code,
        "click_action": "OPEN_APPOINTMENTS",
        "place_count": str(len(places_found)),
    }
    for i, p in enumerate(places_found[:5]):
        data_payload[f"place_{i}_name"] = p.get('name', 'Unknown')[:100]
        data_payload[f"place_{i}_url"] = p.get('url', '')[:500]

    try:
        messaging.send(messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data=data_payload,
            token=token
        ))
        print(f"✅ Notification sent to user {user_id} with {len(places_found)} places")
    except Exception as e:
        print(f"❌ Notification failed: {e}")

def add_to_queue(postal_code: str, user_id: str):
    """Add request to queue with user-specific ID."""
    if db is None:
        return
    try:
        db.collection("lab_requests_queue").document(f"{postal_code}_{user_id}").set({
            "postal_code": postal_code,
            "user_id": user_id,
            "status": "queued",
            "queued_at": datetime.now()
        })
        print(f"   📝 Queued: lab_requests_queue/{postal_code}_{user_id}")
    except Exception as e:
        print(f"   ⚠️ Queue add failed: {e}")

def run_human_browser(profile: dict, postal_code: str, worker_id: int) -> list:
    profile_name = profile.get("name", f"User-{worker_id}")
    stagger_delay = profile.get("delay", worker_id * 15)
    if stagger_delay > 0:
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
            time.sleep(random.uniform(1.5, 3))
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
                    time.sleep(0.3)
                    inputs[0].fill("")
                    time.sleep(0.2)
                    inputs[0].type(postal_code, delay=random.randint(100, 200))
                    print(f"   [{profile_name}] ✅ Postal: {postal_code}")
            except:
                pass
            time.sleep(random.uniform(1.5, 3))
            try:
                page.get_by_role("button", name="Search").first.click(timeout=8000)
            except:
                try:
                    page.get_by_role("button", name="Rechercher").first.click(timeout=5000)
                except:
                    page.keyboard.press("Enter")
            time.sleep(random.uniform(2, 4))
            try:
                page.wait_for_selector("text=km", timeout=30000)
            except:
                pass
            time.sleep(random.uniform(3, 6))

            book_links = page.locator("a[href*='take-appt']").all()
            print(f"   [{profile_name}] 📎 {len(book_links)} booking links")

            for link in book_links:
                try:
                    href = link.get_attribute("href") or ""
                    clinic_id_match = re.search(r'/(\d+)/take-appt', href)
                    if not clinic_id_match:
                        continue
                    clinic_id = clinic_id_match.group(1)
                    url = f"https://clients3.clicsante.ca/{clinic_id}/take-appt"

                    # Try multiple methods to get the clinic name
                    name = link.evaluate("""el => {
                        let parent = el.closest('li, article, div[class*="result"], div[class*="card"], div[class*="item"]');
                        if (!parent) parent = el.closest('div');
                        if (!parent) return '';
                        let headings = parent.querySelectorAll('h1, h2, h3, h4, h5, strong, b, [class*="name"], [class*="title"]');
                        for (let h of headings) {
                            let text = h.innerText?.trim();
                            if (text && text.length > 5 && text.length < 200 && text !== 'Book appt.') return text;
                        }
                        return '';
                    }""")

                    if not name:
                        name = link.evaluate("""el => {
                            let prev = el.previousElementSibling;
                            let count = 0;
                            while (prev && count < 5) {
                                let text = prev.innerText?.trim();
                                if (text && text.length > 5 && text.length < 200 && text !== 'Book appt.') return text;
                                prev = prev.previousElementSibling; count++;
                            }
                            return '';
                        }""")

                    if not name:
                        row_text = link.evaluate("""el => {
                            let row = el.closest('li, tr, [class*="row"], [class*="item"], [class*="result"]');
                            return row ? row.innerText?.trim()?.substring(0, 500) : '';
                        }""")
                        if row_text:
                            for line in row_text.split('\n'):
                                line = line.strip()
                                if line and line != "Book appt." and len(line) > 10 and len(line) < 200:
                                    if "km" not in line.lower():
                                        name = line
                                        break

                    if not name:
                        name = link.evaluate("""el => {
                            let current = el;
                            for (let i = 0; i < 8; i++) {
                                if (!current || !current.parentElement) break;
                                current = current.parentElement;
                                let text = current.innerText?.trim();
                                if (text && text.length > 40 && text.length < 600) {
                                    let lines = text.split('\\n');
                                    for (let line of lines) {
                                        line = line.trim();
                                        if (line && line !== 'Book appt.' && line.length > 10 && line.length < 200 && !line.toLowerCase().includes('km')) {
                                            return line;
                                        }
                                    }
                                }
                            }
                            return '';
                        }""")

                    if not name:
                        name = f"Clinique #{clinic_id}"

                    place = {"name": name[:150], "url": url}
                    if place not in places:
                        places.append(place)
                        print(f"   [{profile_name}]    📍 {name[:80]}")

                    if len(places) >= 5:
                        break
                except:
                    pass

            print(f"   [{profile_name}] ✅ Found {len(places)} places")
        except Exception as e:
            print(f"   [{profile_name}] ❌ Error: {e}")
        finally:
            browser.close()
    return places[:5]


def check_availability():
    postal_code = get_postal_code()
    zone = get_zone(postal_code)

    # Find the pending request to get the user_id
    user_id = None
    if db:
        try:
            requests_ref = db.collection("lab_requests") \
                .where("postal_code", "==", postal_code) \
                .where("status", "in", ["pending", "processing", "dispatched"]) \
                .order_by("requested_at", direction="ASCENDING") \
                .limit(1) \
                .stream()
            for req in requests_ref:
                user_id = req.to_dict().get('user_id', 'system')
                break
        except:
            pass

    if not user_id:
        print(f"\n⚠️ No pending requests for {postal_code} — nothing to do")
        return True

    if is_peak_hours():
        print(f"\n{'='*60}")
        print(f"⏰ PEAK HOURS (8am-10am) — Request queued for user {user_id}")
        add_to_queue(postal_code, user_id)
        return True

    print(f"\n{'='*60}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] 📍 {postal_code} | {zone} | User: {user_id}")
    print(f"🧑 5 human-like browsers...")

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
            except:
                pass

    all_places = all_places[:5]

    print(f"\n{'='*60}")
    print(f"✅ FINAL RESULTS: {len(all_places)} places near {postal_code}")
    for i, p in enumerate(all_places):
        print(f"\n   {i+1}. 📍 {p.get('name')}")
        print(f"      🔗 {p.get('url')}")

    # Save with user-specific paths
    saved_user_id = save_to_firestore(postal_code, all_places)
    send_notification(postal_code, all_places, saved_user_id or user_id)
    print(f"\n🎉 Done! {len(all_places)} choices saved + notification sent to user {saved_user_id or user_id}.")
    return True


if __name__ == "__main__":
    check_availability()
