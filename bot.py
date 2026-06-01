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
# Check for GitHub Secret first (FIREBASE_CREDENTIALS = JSON string)
# Then fall back to local file (FIREBASE_CRED_PATH)
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
    # Montreal
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
    # Laval
    "H7A": "laval", "H7B": "laval", "H7C": "laval", "H7E": "laval",
    "H7G": "laval", "H7H": "laval", "H7J": "laval", "H7K": "laval",
    "H7L": "laval", "H7M": "laval", "H7N": "laval", "H7P": "laval",
    "H7R": "laval", "H7S": "laval", "H7T": "laval", "H7V": "laval",
    "H7W": "laval", "H7X": "laval", "H7Y": "laval",
    # Longueuil / Rive-Sud
    "J4A": "longueuil", "J4B": "longueuil", "J4C": "longueuil", "J4G": "longueuil",
    "J4H": "longueuil", "J4J": "longueuil", "J4K": "longueuil", "J4L": "longueuil",
    "J4M": "longueuil", "J4N": "longueuil", "J4P": "longueuil", "J4R": "longueuil",
    "J4S": "longueuil", "J4T": "longueuil", "J4V": "longueuil", "J4W": "longueuil",
    "J4X": "longueuil", "J4Y": "longueuil", "J4Z": "longueuil",
    # Quebec City
    "G1A": "quebec", "G1B": "quebec", "G1C": "quebec", "G1E": "quebec",
    "G1G": "quebec", "G1H": "quebec", "G1J": "quebec", "G1K": "quebec",
    "G1L": "quebec", "G1M": "quebec", "G1N": "quebec", "G1P": "quebec",
    "G1R": "quebec", "G1S": "quebec", "G1T": "quebec", "G1V": "quebec_ste_foy",
    "G1W": "quebec_ste_foy", "G1X": "quebec_ste_foy", "G1Y": "quebec_ste_foy",
    "G2A": "quebec", "G2B": "quebec", "G2C": "quebec", "G2E": "quebec",
    "G2G": "quebec", "G2J": "quebec", "G2K": "quebec", "G2L": "quebec",
    "G2M": "quebec", "G2N": "quebec",
    # Gatineau
    "J8P": "gatineau", "J8R": "gatineau", "J8T": "gatineau", "J8V": "gatineau",
    "J8W": "gatineau", "J8X": "gatineau", "J8Y": "gatineau", "J8Z": "gatineau",
    "J9A": "gatineau", "J9H": "gatineau", "J9J": "gatineau",
    # Sherbrooke
    "J1A": "sherbrooke", "J1C": "sherbrooke", "J1E": "sherbrooke",
    "J1G": "sherbrooke", "J1H": "sherbrooke", "J1J": "sherbrooke",
    "J1K": "sherbrooke", "J1L": "sherbrooke", "J1M": "sherbrooke",
    "J1N": "sherbrooke",
    # Trois-Rivières
    "G8T": "trois_rivieres", "G8V": "trois_rivieres", "G8W": "trois_rivieres",
    "G8Y": "trois_rivieres", "G8Z": "trois_rivieres", "G9A": "trois_rivieres",
    "G9B": "trois_rivieres", "G9C": "trois_rivieres",
    # Saguenay
    "G7A": "saguenay", "G7B": "saguenay", "G7G": "saguenay", "G7H": "saguenay",
    "G7J": "saguenay", "G7K": "saguenay", "G7N": "saguenay", "G7P": "saguenay",
    "G7S": "saguenay", "G7T": "saguenay", "G7X": "saguenay", "G7Y": "saguenay",
    # Other regions
    "G5A": "rimouski", "G5L": "rimouski", "G5M": "rimouski",
    "G3A": "portneuf", "G3C": "portneuf", "G4A": "charlevoix",
    "G6A": "chaudiere", "G6B": "chaudiere", "G6C": "chaudiere",
    "J0A": "centre_quebec", "J0B": "estrie", "J0C": "centre_quebec",
    "J0E": "monteregie", "J0G": "centre_quebec", "J0H": "monteregie",
    "J0J": "monteregie", "J0K": "lanaudiere", "J0L": "monteregie",
    "J0M": "monteregie", "J0N": "laurentides", "J0P": "laurentides",
    "J0R": "laurentides", "J0S": "centre_quebec", "J0T": "laurentides",
    "J0V": "laurentides", "J0W": "outaouais", "J0X": "outaouais",
    "J0Y": "abitibi", "J0Z": "abitibi",
    "J2A": "centre_quebec", "J2B": "centre_quebec", "J2C": "centre_quebec",
    "J2E": "centre_quebec", "J2G": "monteregie", "J2H": "monteregie",
    "J2J": "monteregie", "J2K": "monteregie", "J2L": "monteregie",
    "J2M": "monteregie", "J2N": "monteregie", "J2P": "monteregie",
    "J2R": "monteregie", "J2S": "monteregie", "J2T": "monteregie",
    "J2W": "monteregie", "J2X": "monteregie", "J2Y": "monteregie",
    "J3A": "monteregie", "J3B": "monteregie", "J3E": "monteregie",
    "J3G": "monteregie", "J3H": "monteregie", "J3J": "monteregie",
    "J3L": "monteregie", "J3M": "monteregie", "J3N": "monteregie",
    "J3P": "monteregie", "J3R": "monteregie", "J3T": "monteregie",
    "J3V": "monteregie", "J3X": "monteregie", "J3Y": "monteregie",
    "J5A": "monteregie", "J5B": "monteregie", "J5C": "monteregie",
    "J5J": "laurentides", "J5K": "monteregie", "J5L": "laurentides",
    "J5M": "lanaudiere", "J5N": "lanaudiere", "J5R": "monteregie",
    "J5T": "lanaudiere", "J5V": "lanaudiere", "J5W": "lanaudiere",
    "J5X": "lanaudiere", "J5Y": "lanaudiere", "J5Z": "lanaudiere",
    "J6A": "lanaudiere", "J6B": "lanaudiere", "J6E": "lanaudiere",
    "J6J": "lanaudiere", "J6K": "lanaudiere", "J6N": "lanaudiere",
    "J6R": "lanaudiere", "J6S": "lanaudiere", "J6T": "lanaudiere",
    "J6V": "lanaudiere", "J6W": "lanaudiere", "J6X": "lanaudiere",
    "J6Y": "lanaudiere", "J6Z": "lanaudiere",
    "J7A": "laurentides", "J7B": "laurentides", "J7C": "laurentides",
    "J7E": "laurentides", "J7G": "laurentides", "J7H": "laurentides",
    "J7J": "laurentides", "J7K": "lanaudiere", "J7L": "lanaudiere",
    "J7M": "lanaudiere", "J7N": "laurentides", "J7P": "laurentides",
    "J7R": "laurentides", "J7S": "laurentides", "J7T": "laurentides",
    "J7V": "laurentides", "J7W": "laurentides", "J7X": "laurentides",
    "J7Y": "laurentides", "J7Z": "laurentides",
    "G0A": "quebec_region", "G0C": "gaspesie", "G0E": "gaspesie",
    "G0G": "cote_nord", "G0H": "cote_nord", "G0J": "gaspesie",
    "G0K": "bas_st_laurent", "G0L": "bas_st_laurent", "G0M": "estrie",
    "G0N": "chaudiere", "G0P": "centre_quebec", "G0R": "bas_st_laurent",
    "G0S": "chaudiere", "G0T": "cote_nord", "G0V": "saguenay",
    "G0W": "saguenay", "G0X": "mauricie", "G0Y": "estrie", "G0Z": "centre_quebec",
    "G4R": "cote_nord", "G4S": "cote_nord", "G4T": "gaspesie",
    "G4V": "gaspesie", "G4W": "gaspesie", "G4X": "gaspesie",
    "G4Y": "gaspesie", "G4Z": "gaspesie",
    "G5B": "gaspesie", "G5C": "gaspesie", "G5E": "gaspesie",
    "G5G": "gaspesie", "G5H": "gaspesie", "G5J": "gaspesie",
    "G5K": "gaspesie", "G5N": "gaspesie", "G5P": "gaspesie",
    "G5R": "gaspesie", "G5S": "gaspesie", "G5T": "bas_st_laurent",
    "G5V": "bas_st_laurent", "G5X": "bas_st_laurent", "G5Y": "bas_st_laurent",
    "G5Z": "bas_st_laurent",
    "G6E": "chaudiere", "G6G": "chaudiere", "G6H": "chaudiere",
    "G6J": "chaudiere", "G6K": "chaudiere", "G6L": "chaudiere",
    "G6P": "chaudiere", "G6R": "chaudiere", "G6S": "chaudiere",
    "G6T": "chaudiere", "G6V": "chaudiere", "G6W": "chaudiere",
    "G6X": "chaudiere", "G6Y": "chaudiere", "G6Z": "chaudiere",
    "G8A": "saguenay", "G8B": "saguenay", "G8C": "saguenay",
    "G8E": "saguenay", "G8G": "saguenay", "G8H": "saguenay",
    "G8J": "saguenay", "G8K": "saguenay", "G8L": "saguenay",
    "G8M": "saguenay", "G8N": "saguenay", "G8P": "abitibi",
    "G8R": "abitibi", "G8S": "mauricie", "G8T": "mauricie",
    "G9H": "mauricie", "G9J": "mauricie", "G9K": "mauricie",
    "G9L": "mauricie", "G9M": "mauricie", "G9N": "mauricie",
    "G9P": "mauricie", "G9R": "mauricie", "G9S": "mauricie",
    "G9T": "mauricie", "G9X": "mauricie",
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
    """Check if current time is peak hours (8am-10am Quebec time)"""
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
    """Save to Firestore so the appointments page can display results"""
    if db is None:
        print("⚠️ Firestore not available — skipping")
        return

    zone = get_zone(postal_code)
    now = datetime.now()

    data = {
        "service": "blood-test",
        "postal_code": postal_code,
        "zone": zone,
        "status": "completed",
        "clinics": places_found,
        "places_found": places_found,
        "slots_found": len(places_found) > 0,
        "last_checked": now,
    }

    try:
        db.collection("availability").document(zone).set(data)
        print(f"🔥 Saved to Firestore: availability/{zone} with {len(places_found)} clinics")

        # Update pending lab_requests
        requests_ref = db.collection("lab_requests").where("postal_code", "==", postal_code[:3]).where("status", "in", ["pending", "processing", "dispatched"]).stream()
        for req in requests_ref:
            req.reference.update({
                "status": "completed",
                "results": places_found,
                "completed_at": now,
            })
            print(f"📝 Updated lab_request: {req.id}")

    except Exception as e:
        print(f"❌ Firestore save failed: {e}")

def send_notification(postal_code: str, places_found: list):
    """Send ONE push notification with all 5 links"""
    token = get_user_token()
    if not token:
        print("⚠️ No FCM token found — skipping notification")
        return

    place_names = ", ".join([p.get('name', 'Unknown')[:30] for p in places_found[:3]])
    first_url = places_found[0].get('direct_url', '') if places_found else ''

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
        data_payload[f"place_{i}_url"] = p.get('direct_url', '')[:500]
        if p.get('address'):
            data_payload[f"place_{i}_address"] = p.get('address', '')[:200]
        if p.get('distance'):
            data_payload[f"place_{i}_distance"] = str(p.get('distance', ''))

    try:
        messaging.send(messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data=data_payload,
            token=token,
        ))
        print(f"✅ 1 notification sent with {len(places_found)} places")
    except Exception as e:
        print(f"❌ Notification failed: {e}")

def add_to_queue(postal_code: str):
    """Add request to queue during peak hours"""
    if db is None:
        return
    try:
        db.collection("lab_requests_queue").document(postal_code).set({
            "postal_code": postal_code,
            "status": "queued",
            "queued_at": datetime.now(),
        })
        print(f"⏳ Added to queue: {postal_code} (will process after 10am)")
    except Exception as e:
        print(f"❌ Queue failed: {e}")


# ══════════════════════════════════════════════════════════════
# ★ SINGLE WORKER — Staggered, extracts names + IDs ★
# ══════════════════════════════════════════════════════════════

def run_human_browser(profile: dict, postal_code: str, worker_id: int) -> list:
    """Navigate ClicSanté, extract clinic names AND IDs"""
    profile_name = profile.get("name", f"User-{worker_id}")
    stagger_delay = profile.get("delay", worker_id * 15)

    if stagger_delay > 0:
        print(f"   [{profile_name}] ⏳ Waiting {stagger_delay}s (staggered start)...")
        time.sleep(stagger_delay)

    print(f"\n   [{profile_name}] 🧑 Starting human-like search...")

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

            page.goto("https://portal3.clicsante.ca/services/blood-test",
                     wait_until="networkidle", timeout=60000)
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

            # ★ EXTRACT CLINIC NAMES + IDs ★
            print(f"   [{profile_name}] 🔍 Extracting clinics...")

            book_links = page.locator("a[href*='take-appt']").all()
            print(f"   [{profile_name}] 📎 {len(book_links)} booking links")

            for link in book_links:
                try:
                    href = link.get_attribute("href") or ""
                    clinic_id = re.search(r'/(\d+)/take-appt', href)
                    if not clinic_id:
                        continue

                    clinic_id = clinic_id.group(1)
                    name = ""

                    # Try to get name from parent container
                    try:
                        parent = link.evaluate("el => el.closest('div, li, article, section')?.innerText")
                        if parent:
                            lines = parent.strip().split('\n')
                            for line in lines:
                                line = line.strip()
                                if line and line != "Book appt." and len(line) > 5:
                                    if any(kw in line.lower() for kw in ["clsc", "clinique", "hopital", "hôpital", "pharmacie", "gmf", "familiprix", "jean coutu", "pharmaprix", "uniprix", "brunet", "centre", "point de service", "prélèvement", "laboratoire", "santé"]):
                                        name = line[:120]
                                        break
                            if not name:
                                for line in lines:
                                    line = line.strip()
                                    if line and len(line) > 10 and line != "Book appt." and "km" not in line.lower():
                                        name = line[:120]
                                        break
                    except:
                        pass

                    if not name:
                        try:
                            prev = link.evaluate("el => el.previousElementSibling?.innerText")
                            if prev and len(prev.strip()) > 5:
                                name = prev.strip()[:120]
                        except:
                            pass

                    if not name:
                        try:
                            grandparent = link.evaluate("el => el.closest('div, li')?.parentElement?.innerText")
                            if grandparent:
                                lines = grandparent.strip().split('\n')
                                for line in lines:
                                    line = line.strip()
                                    if line and len(line) > 15 and "km" in line.lower():
                                        name = line[:120]
                                        break
                        except:
                            pass

                    if not name:
                        name = f"Clinique #{clinic_id}"

                    place = {
                        "name": name[:120],
                        "id": clinic_id,
                        "direct_url": f"https://clients3.clicsante.ca/{clinic_id}/take-appt",
                    }

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


# ══════════════════════════════════════════════════════════════
# ★ MAIN
# ══════════════════════════════════════════════════════════════

def check_availability():
    postal_code = get_postal_code()
    zone = get_zone(postal_code)

    # ★ PEAK HOURS CHECK ★
    if is_peak_hours():
        print(f"\n{'='*60}")
        print(f"⏰ PEAK HOURS (8am-10am) — Request queued")
        print(f"   Will process after 10:01am to respect government systems")
        print(f"{'='*60}")
        add_to_queue(postal_code)
        return True

    print(f"\n{'='*60}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] 📍 {postal_code} | {zone}")
    print(f"🧑 5 human-like browsers (staggered starts, 15s apart)...")
    print(f"{'='*60}")

    all_places = []
    seen_ids = set()

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(run_human_browser, profile, postal_code, i): i
            for i, profile in enumerate(BROWSER_PROFILES)
        }
        for future in as_completed(futures):
            try:
                places = future.result()
                for p in places:
                    pid = p.get('id', p['name'])
                    if pid not in seen_ids:
                        seen_ids.add(pid)
                        all_places.append(p)
            except Exception as e:
                print(f"   ❌ Worker failed: {e}")

    all_places = all_places[:5]

    print(f"\n{'='*60}")
    print(f"✅ FINAL RESULTS: {len(all_places)} places near {postal_code}")
    print(f"{'='*60}")
    for i, p in enumerate(all_places):
        print(f"\n   {i+1}. 📍 {p.get('name')}")
        print(f"      🔗 {p.get('direct_url')}")

    save_to_firestore(postal_code, all_places)
    send_notification(postal_code, all_places)

    print(f"\n🎉 Done! {len(all_places)} choices saved + 1 notification sent.")
    return True


if __name__ == "__main__":
    check_availability()
