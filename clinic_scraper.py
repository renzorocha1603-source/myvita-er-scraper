#!/usr/bin/env python3
"""
MYVITA HYBRID SCRAPER v10 — Free Government Platforms
- ClicSanté: Medical consultation search (free, government-backed)
- TELUS Health Appointment Access: Government-backed FREE system
- NO Bonjour Santé (paywall)
- Kill switch after 5 slots
- Screenshots for debugging
"""

from playwright.sync_api import sync_playwright
import time
import os
import json
import re
import random
import math
import threading
import traceback
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import firebase_admin
from firebase_admin import credentials, firestore

# ================================================================
# 1. FIREBASE SETUP
# ================================================================

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
        print("✅ Firebase initialized")
    except Exception as e:
        print(f"⚠️ Firebase error: {e}")
elif os.path.exists(FIREBASE_CRED_PATH):
    try:
        cred = credentials.Certificate(FIREBASE_CRED_PATH)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred, {'projectId': 'myvita-app-c5ecd'})
        db = firestore.client()
        print("✅ Firebase initialized (local file)")
    except Exception as e:
        print(f"⚠️ Firebase error from file: {e}")
else:
    print("⚠️ Firebase credentials not found")

# ================================================================
# 2. CONFIGURATION
# ================================================================

HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
MAX_WORKERS = 5
RADIUS_KM = 50
REQUEST_COLLECTION = "concierge_requests"
DEBUG_DIR = "/tmp/myvita_debug"

os.makedirs(DEBUG_DIR, exist_ok=True)
print(f"📸 Screenshots will be saved to: {DEBUG_DIR}")

# ================================================================
# 3. BROWSER PROFILES
# ================================================================

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

# ================================================================
# 4. CLINICS DATABASE
# ================================================================

CLINICS = [
    {"name": "Médico-Centre Mont-Royal", "lat": 45.5163, "lng": -73.5786, "platform": "clic_sante", "url": "", "city": "Montreal"},
    {"name": "GMF Angus", "lat": 45.5401, "lng": -73.5658, "platform": "clic_sante", "url": "", "city": "Montreal"},
    {"name": "GMF St-Denis", "lat": 45.5264, "lng": -73.5932, "platform": "clic_sante", "url": "", "city": "Montreal"},
    {"name": "Urgence Saint-Laurent", "lat": 45.5118, "lng": -73.6802, "platform": "clic_sante", "url": "", "city": "Montreal"},
    {"name": "CM Mieux-Être Levasseur", "lat": 45.5841, "lng": -73.6412, "platform": "clic_sante", "url": "", "city": "Montreal"},
    {"name": "CM Mieux-Être St-Léonard", "lat": 45.5892, "lng": -73.6014, "platform": "clic_sante", "url": "", "city": "Montreal"},
    {"name": "Medi-Centre Chomedey", "lat": 45.5451, "lng": -73.7483, "platform": "clic_sante", "url": "", "city": "Laval"},
    {"name": "Carrefour Médical Laval", "lat": 45.5684, "lng": -73.7431, "platform": "clic_sante", "url": "", "city": "Laval"},
    {"name": "UnionMD Longueuil", "lat": 45.5252, "lng": -73.5135, "platform": "telus_health", "url": "https://appointmentaccess.telushealth.com/", "city": "Longueuil"},
    {"name": "GMF Terrebonne", "lat": 45.6982, "lng": -73.6391, "platform": "telus_health", "url": "https://appointmentaccess.telushealth.com/", "city": "Terrebonne"},
    {"name": "GMF-U Charles-Le Moyne", "lat": 45.5184, "lng": -73.4831, "platform": "telus_health", "url": "https://appointmentaccess.telushealth.com/", "city": "Longueuil"},
    {"name": "Centre Médical Laval", "lat": 45.5521, "lng": -73.7314, "platform": "telus_health", "url": "https://appointmentaccess.telushealth.com/", "city": "Laval"},
    {"name": "Clinique Sainte-Dorothée", "lat": 45.5312, "lng": -73.8115, "platform": "telus_health", "url": "https://appointmentaccess.telushealth.com/", "city": "Laval"},
    {"name": "Clinique de la Gare", "lat": 45.5582, "lng": -73.9015, "platform": "telus_health", "url": "https://appointmentaccess.telushealth.com/", "city": "Saint-Eustache"},
    {"name": "GMF des Seigneurs", "lat": 45.7025, "lng": -73.6514, "platform": "telus_health", "url": "https://appointmentaccess.telushealth.com/", "city": "Terrebonne"},
]

GOOGLE_MAPS_PROXY = "https://us-central1-myvita-app-c5ecd.cloudfunctions.net/googleMapsProxy"

# ================================================================
# 5. HELPER FUNCTIONS
# ================================================================

def take_screenshot(page, step_name: str, worker_id: int = 0):
    try:
        timestamp = datetime.now().strftime("%H%M%S")
        filename = f"worker{worker_id}_{timestamp}_{step_name}.png"
        filepath = os.path.join(DEBUG_DIR, filename)
        page.screenshot(path=filepath, full_page=True)
        print(f"   📸 Screenshot saved: {filename}")
    except Exception as e:
        print(f"   ⚠️ Screenshot failed: {e}")


def human_delay(min_ms: int = 300, max_ms: int = 1000):
    time.sleep(random.uniform(min_ms, max_ms) / 1000)


def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def get_user_data() -> dict:
    return {
        "first_name": os.getenv("USER_FIRST_NAME", "Jean"),
        "last_name": os.getenv("USER_LAST_NAME", "Tremblay"),
        "ramq": os.getenv("USER_RAMQ", "TREJ70010101"),
        "ramq_seq": os.getenv("USER_RAMQ_SEQ", "01"),
        "postal_code": os.getenv("POSTAL_CODE", "H1Y3H1"),
        "email": os.getenv("USER_EMAIL", "jean.tremblay@email.com"),
        "phone": os.getenv("USER_PHONE", "5145550101"),
    }


# ================================================================
# 6. KILL SWITCH
# ================================================================

class KillSwitch:
    _active = False
    _found_slots = []
    _lock = threading.Lock()

    @classmethod
    def add_slot(cls, details: dict):
        with cls._lock:
            existing_urls = [s.get('url', '') for s in cls._found_slots]
            if details.get('url', '') in existing_urls:
                return
            cls._found_slots.append(details)
            print(f"\n   🎯 SLOT #{len(cls._found_slots)} FOUND!")
            print(f"   📋 Name: {details.get('name', 'Unknown')}")
            print(f"   🔗 URL: {details.get('url', '')[:120]}")
            if len(cls._found_slots) >= 5:
                cls._active = True
                print("\n   🛑 KILL SWITCH ACTIVATED — 5 slots found! Stopping all workers...")

    @classmethod
    def is_active(cls) -> bool:
        with cls._lock:
            return cls._active

    @classmethod
    def get_results(cls) -> list:
        with cls._lock:
            return list(cls._found_slots)

    @classmethod
    def reset(cls):
        with cls._lock:
            cls._active = False
            cls._found_slots = []


# ================================================================
# 7. FIRESTORE HELPERS
# ================================================================

def save_to_firestore(postal_code: str, slots: list):
    if db is None:
        print("⚠️ Firestore not available — skipping save")
        return
    try:
        data = {
            "postal_code": postal_code,
            "status": "completed",
            "clinics": slots,
            "slots_found": len(slots) > 0,
            "last_checked": datetime.now(),
            "updated_at": firestore.SERVER_TIMESTAMP,
        }
        db.collection("availability").document(postal_code).set(data)
        print(f"✅ Saved {len(slots)} slots to Firestore availability/{postal_code}")
    except Exception as e:
        print(f"❌ Firestore save failed: {e}")


def get_or_create_request(user: dict) -> str:
    if db is None:
        return ""
    request_id = os.getenv("REQUEST_ID", "")
    postal = user["postal_code"][:3].upper()
    try:
        if request_id:
            doc = db.collection(REQUEST_COLLECTION).document(request_id).get()
            if doc.exists:
                print(f"📝 Using existing request: {request_id[:8]}...")
                return request_id
        new_data = {
            "status": "scraper_running",
            "scraper_status": "running",
            "postal_code": postal,
            "first_name": user["first_name"],
            "last_name": user["last_name"],
            "email": user["email"],
            "phone": user["phone"],
            "ramq": user["ramq"],
            "ramq_seq": user["ramq_seq"],
            "created_at": firestore.SERVER_TIMESTAMP,
        }
        result = db.collection(REQUEST_COLLECTION).add(new_data)
        doc_id = result[1].id
        print(f"📄 Created request: {doc_id}")
        return doc_id
    except Exception as e:
        print(f"❌ Error creating request: {e}")
        return ""


# ================================================================
# 8. CLICSANTÉ SCRAPER — Medical consultation search
# ================================================================

def scrape_clicsante(profile: dict, user: dict, worker_id: int) -> list:
    """
    ClicSanté scraper for FREE medical consultations.
    Government-backed platform.
    """
    found = []
    postal_code = user["postal_code"]
    stagger_delay = profile.get("delay", 0)
    if stagger_delay > 0:
        time.sleep(stagger_delay)
    if KillSwitch.is_active():
        return []

    print(f"\n{'='*60}")
    print(f"🔵 WORKER {worker_id}: ClicSanté — {profile['name']}")
    print(f"{'='*60}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            viewport=profile.get("viewport", {"width": 1280, "height": 720}),
            user_agent=profile.get("user_agent"),
            locale=profile.get("locale", "fr-CA"),
            timezone_id=profile.get("timezone", "America/Montreal"),
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()

        try:
            # Step 1: Load ClicSanté
            print("\n📂 Step 1: Loading ClicSanté...")
            human_delay(1000, 3000)
            page.goto("https://portal3.clicsante.ca/", wait_until="networkidle", timeout=60000)
            human_delay(1500, 3000)

            # Close "Sans frais" popup
            try:
                page.locator("text=Sans frais").first.click(timeout=8000)
                print("   ✅ Popup closed")
            except:
                pass
            human_delay(1000, 2000)

            # Step 2: Enter postal code
            print(f"\n📂 Step 2: Entering postal code '{postal_code}'...")
            inputs = page.locator("input[type='text']").all()
            if inputs:
                inputs[0].click()
                inputs[0].fill("")
                inputs[0].type(postal_code, delay=random.randint(100, 200))
                print(f"   ✅ Postal code entered")
            human_delay(1000, 2000)
            take_screenshot(page, "cs_01_postal", worker_id)

            # Step 3: Select medical service
            print("\n📂 Step 3: Selecting medical service...")
            service_selected = False
            medical_keywords = [
                "Consultation médicale",
                "Médecine familiale",
                "Médecin",
                "Soins de santé",
                "Urgence mineure",
            ]
            for keyword in medical_keywords:
                if service_selected:
                    break
                try:
                    button = page.locator(f"text={keyword}").first
                    if button.count() > 0 and button.is_visible():
                        button.click()
                        service_selected = True
                        print(f"   ✅ Selected: '{keyword}'")
                        human_delay(2000, 4000)
                        break
                except:
                    pass

            if not service_selected:
                print("   ⚠️ Could not select medical service — using default")

            # Step 4: Click Search
            print("\n📂 Step 4: Searching...")
            try:
                page.get_by_role("button", name="Search").first.click(timeout=8000)
                print("   ✅ Clicked 'Search'")
            except:
                try:
                    page.get_by_role("button", name="Rechercher").first.click(timeout=5000)
                    print("   ✅ Clicked 'Rechercher'")
                except:
                    page.keyboard.press("Enter")
                    print("   ✅ Pressed Enter")

            human_delay(5000, 8000)
            take_screenshot(page, "cs_02_results", worker_id)

            # Step 5: Extract booking links
            print("\n📂 Step 5: Extracting booking links...")

            # Try multiple methods to find booking links
            booking_links = page.locator("a[href*='take-appt']").all()

            if len(booking_links) == 0:
                # Try alternative: look for any link with "appt" or "rendez-vous"
                booking_links = page.locator("a[href*='appt'], a[href*='rendez-vous']").all()

            if len(booking_links) == 0:
                # Try clicking on clinic names to get to booking page
                print("   🔄 No direct links found — trying to click clinic names...")
                clinic_cards = page.locator("a, button, [class*='clinic'], [class*='result'], [class*='card']").all()
                for card in clinic_cards[:5]:
                    try:
                        text = (card.inner_text() or "").lower()
                        if any(kw in text for kw in ["clinique", "clinic", "médical", "medical", "gmf", "clsc"]):
                            card.click()
                            human_delay(3000, 5000)
                            new_url = page.url
                            if "take-appt" in new_url or "rendez-vous" in new_url:
                                found.append({
                                    "name": text.split('\n')[0][:150] if text else "Clinique",
                                    "platform": "clicsante",
                                    "url": new_url,
                                    "city": "Various",
                                })
                                KillSwitch.add_slot(found[-1])
                                print(f"   📍 Found: {text[:80]}")
                                page.go_back()
                                human_delay(1000, 2000)
                    except:
                        pass

            print(f"   📎 Found {len(booking_links)} booking links")

            for link in booking_links[:5]:
                if KillSwitch.is_active():
                    break
                try:
                    href = link.get_attribute("href") or ""
                    clinic_id_match = re.search(r'/(\d+)/take-appt', href)
                    if not clinic_id_match:
                        if "rendez-vous" in href or "appt" in href:
                            place = {
                                "name": "Clinique ClicSanté",
                                "platform": "clicsante",
                                "url": href,
                                "city": "Various",
                            }
                            if place not in found:
                                found.append(place)
                                KillSwitch.add_slot(place)
                                print(f"   📍 Found booking link")
                        continue

                    clinic_id = clinic_id_match.group(1)
                    booking_url = f"https://clients3.clicsante.ca/{clinic_id}/take-appt"

                    clinic_name = link.evaluate("""
                        () => {
                            let parent = this.closest('li, article, div[class*="result"], div[class*="card"]');
                            if (!parent) parent = this.closest('div');
                            if (!parent) return '';
                            let headings = parent.querySelectorAll('h1, h2, h3, h4, strong, b, [class*="name"], [class*="title"]');
                            for (let h of headings) {
                                let text = h.innerText?.trim();
                                if (text && text.length > 5 && text.length < 200) return text;
                            }
                            let text = parent.innerText?.trim();
                            if (text) return text.split('\\n')[0].substring(0, 150);
                            return '';
                        }
                    """) or f"Clinique ClicSanté #{clinic_id}"

                    place = {
                        "name": clinic_name[:150],
                        "platform": "clicsante",
                        "url": booking_url,
                        "city": "Various",
                    }
                    if place not in found:
                        found.append(place)
                        KillSwitch.add_slot(place)
                        print(f"   📍 {clinic_name[:80]}")

                except Exception as e:
                    print(f"   ⚠️ Error: {e}")

        except Exception as e:
            print(f"   ❌ ClicSanté error: {e}")
            traceback.print_exc()
            take_screenshot(page, "cs_99_error", worker_id)
        finally:
            browser.close()

    print(f"\n🔵 Worker {worker_id} finished — Found {len(found)} slots")
    return found


# ================================================================
# 9. TELUS HEALTH APPOINTMENT ACCESS — Government-backed FREE system
# ================================================================

def scrape_telushealth(profile: dict, user: dict, worker_id: int) -> list:
    """
    TELUS Health Appointment Access — Government-backed FREE system.
    Fills patient form, searches for appointments.
    """
    found = []
    stagger_delay = profile.get("delay", 0)

    if stagger_delay > 0:
        time.sleep(stagger_delay)

    if KillSwitch.is_active():
        return []

    print(f"\n{'='*60}")
    print(f"🟣 WORKER {worker_id}: TELUS Health — {profile['name']}")
    print(f"{'='*60}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            viewport=profile.get("viewport", {"width": 1280, "height": 720}),
            user_agent=profile.get("user_agent"),
            locale=profile.get("locale", "fr-CA"),
            timezone_id=profile.get("timezone", "America/Montreal"),
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()

        try:
            # ═══════════════════════════════════════════════
            # STEP 1: Load TELUS Health Appointment Access
            # ═══════════════════════════════════════════════
            print(f"\n📂 STEP 1: Loading TELUS Health Appointment Access...")
            page.goto("https://appointmentaccess.telushealth.com/", wait_until="networkidle", timeout=60000)
            human_delay(2000, 3000)

            # Close measles popup if present
            try:
                page.keyboard.press("Escape")
                human_delay(500, 1000)
            except:
                pass

            take_screenshot(page, "telus_01_home", worker_id)

            # Print page text for debugging
            try:
                body_text = page.locator("body").inner_text()
                print(f"   📄 Page content: {body_text[:400]}")
            except:
                pass

            # ═══════════════════════════════════════════════
            # STEP 2: Fill patient identification form
            # ═══════════════════════════════════════════════
            print(f"\n📂 STEP 2: Filling patient form...")

            all_inputs = page.locator("input:visible").all()
            print(f"   📥 Found {len(all_inputs)} visible inputs")

            fields_filled = {
                "first_name": False,
                "last_name": False,
                "ramq": False,
                "seq": False,
                "birth_year": False,
                "email": False,
                "phone": False,
            }

            for inp in all_inputs:
                try:
                    input_type = (inp.get_attribute("type") or "").lower()
                    placeholder = (inp.get_attribute("placeholder") or "").lower()
                    name = (inp.get_attribute("name") or "").lower()
                    aria_label = (inp.get_attribute("aria-label") or "").lower()
                    autocomplete = (inp.get_attribute("autocomplete") or "").lower()
                    combined = f"{placeholder} {name} {aria_label} {autocomplete}"

                    # Skip non-text inputs
                    if input_type in ["checkbox", "radio", "submit", "button", "hidden"]:
                        continue

                    if not fields_filled["first_name"] and any(kw in combined for kw in ["first", "prénom", "prenom", "firstname", "given"]):
                        inp.click()
                        inp.fill("")
                        inp.type(user["first_name"], delay=80)
                        fields_filled["first_name"] = True
                        print(f"   ✏️ First Name: {user['first_name']}")

                    elif not fields_filled["last_name"] and any(kw in combined for kw in ["last", "nom", "lastname", "family", "surname"]):
                        inp.click()
                        inp.fill("")
                        inp.type(user["last_name"], delay=80)
                        fields_filled["last_name"] = True
                        print(f"   ✏️ Last Name: {user['last_name']}")

                    elif not fields_filled["ramq"] and any(kw in combined for kw in ["ramq", "assurance", "health insurance", "abcd", "maladie", "health card"]):
                        inp.click()
                        inp.fill("")
                        inp.type(user["ramq"], delay=80)
                        fields_filled["ramq"] = True
                        print(f"   ✏️ RAMQ: {user['ramq']}")

                    elif not fields_filled["seq"] and any(kw in combined for kw in ["seq", "sequential", "séquentiel", "00", "sequence"]):
                        inp.click()
                        inp.fill("")
                        inp.type(user["ramq_seq"], delay=80)
                        fields_filled["seq"] = True
                        print(f"   ✏️ Sequence: {user['ramq_seq']}")

                    elif not fields_filled["birth_year"] and any(kw in combined for kw in ["year", "année", "birth", "naissance", "yyyy"]):
                        inp.click()
                        inp.fill("")
                        inp.type("1970", delay=80)
                        fields_filled["birth_year"] = True
                        print(f"   ✏️ Year of Birth: 1970")

                    elif not fields_filled["email"] and any(kw in combined for kw in ["email", "courriel", "e-mail"]):
                        inp.click()
                        inp.fill("")
                        inp.type(user["email"], delay=80)
                        fields_filled["email"] = True
                        print(f"   ✏️ Email: {user['email']}")

                    elif not fields_filled["phone"] and any(kw in combined for kw in ["phone", "téléphone", "tel", "mobile"]):
                        inp.click()
                        inp.fill("")
                        inp.type(user["phone"], delay=80)
                        fields_filled["phone"] = True
                        print(f"   ✏️ Phone: {user['phone']}")

                except Exception as e:
                    print(f"   ⚠️ Error filling field: {e}")

            # Fill any remaining unfilled fields
            for inp in all_inputs:
                try:
                    input_type = (inp.get_attribute("type") or "").lower()
                    if input_type in ["checkbox", "radio", "submit", "button", "hidden"]:
                        continue
                    current_val = inp.input_value() or ""
                    if len(current_val) == 0:
                        if not fields_filled["first_name"]:
                            inp.click()
                            inp.fill("")
                            inp.type(user["first_name"], delay=80)
                            fields_filled["first_name"] = True
                            print(f"   ✏️ First Name (fallback): {user['first_name']}")
                        elif not fields_filled["last_name"]:
                            inp.click()
                            inp.fill("")
                            inp.type(user["last_name"], delay=80)
                            fields_filled["last_name"] = True
                            print(f"   ✏️ Last Name (fallback): {user['last_name']}")
                        elif not fields_filled["ramq"]:
                            inp.click()
                            inp.fill("")
                            inp.type(user["ramq"], delay=80)
                            fields_filled["ramq"] = True
                            print(f"   ✏️ RAMQ (fallback): {user['ramq']}")
                except:
                    pass

            # Select sex (Male)
            try:
                male_btn = page.locator("text=Male").first
                if male_btn.count() > 0 and male_btn.is_visible():
                    male_btn.click()
                    print(f"   ✅ Sex: Male")
                    human_delay(300, 500)
            except:
                pass

            # Select language (Français)
            try:
                fr_btn = page.locator("text=Français").first
                if fr_btn.count() > 0 and fr_btn.is_visible():
                    fr_btn.click()
                    print(f"   ✅ Language: Français")
                    human_delay(300, 500)
            except:
                pass

            # Select communication preference (Email only)
            try:
                email_only_btn = page.locator("text=Email only").first
                if email_only_btn.count() > 0 and email_only_btn.is_visible():
                    email_only_btn.click()
                    print(f"   ✅ Communication: Email only")
                    human_delay(300, 500)
            except:
                pass

            # Check consent checkbox
            try:
                checkboxes = page.locator("input[type='checkbox']").all()
                for checkbox in checkboxes:
                    if checkbox.is_visible() and not checkbox.is_checked():
                        checkbox.check()
                        print(f"   ✅ Consent checked")
                        break
            except:
                pass

            human_delay(500, 1000)
            take_screenshot(page, "telus_02_form_filled", worker_id)

            # ═══════════════════════════════════════════════
            # STEP 3: Click Continue
            # ═══════════════════════════════════════════════
            print(f"\n📂 STEP 3: Clicking Continue...")
            continue_clicked = False

            try:
                btn = page.locator("button:has-text('Continue')").first
                if btn.count() > 0 and btn.is_visible():
                    is_disabled = btn.get_attribute("disabled")
                    if is_disabled is None:
                        btn.click()
                        continue_clicked = True
                        print(f"   ✅ Clicked Continue")
                    else:
                        print(f"   ⚠️ Continue is DISABLED — form may be incomplete")
                        print(f"   📋 Fields filled: {fields_filled}")
                        try:
                            body_text = page.locator("body").inner_text()
                            print(f"   📄 Page text: {body_text[:400]}")
                        except:
                            pass
            except:
                pass

            if not continue_clicked:
                try:
                    page.keyboard.press("Enter")
                    human_delay(2000, 3000)
                    continue_clicked = True
                    print(f"   ✅ Pressed Enter")
                except:
                    pass

            if continue_clicked:
                human_delay(3000, 5000)
                take_screenshot(page, "telus_03_after_continue", worker_id)
                current_url = page.url
                print(f"   📍 URL: {current_url[:120]}")

                # Print next page content
                try:
                    body_text = page.locator("body").inner_text()
                    print(f"   📄 Next page: {body_text[:400]}")
                except:
                    pass

                # ═══════════════════════════════════════════════
                # STEP 4: Fill appointment search form
                # ═══════════════════════════════════════════════
                print(f"\n📂 STEP 4: Filling appointment search form...")

                human_delay(2000, 3000)
                take_screenshot(page, "telus_04_search_page", worker_id)

                # Print page content
                try:
                    body_text = page.locator("body").inner_text()
                    print(f"   📄 Search page: {body_text[:500]}")
                except:
                    pass

                # 1. Fill postal code
                print(f"   📋 Filling postal code: {user['postal_code']}...")
                postal_filled = False
                for selector in [
                    "input[placeholder*='G1G']",
                    "input[placeholder*='postal']",
                    "input[placeholder*='code']",
                    "input[name*='postal']",
                ]:
                    element = page.locator(selector).first
                    if element.count() > 0 and element.is_visible():
                        element.click()
                        element.fill("")
                        element.type(user["postal_code"], delay=80)
                        postal_filled = True
                        print(f"   ✏️ Postal code: {user['postal_code']}")
                        break

                if not postal_filled:
                    all_inputs = page.locator("input[type='text']:visible, input:not([type]):visible").all()
                    for inp in all_inputs:
                        try:
                            current_val = inp.input_value() or ""
                            if len(current_val) < 3:
                                inp.click()
                                inp.fill("")
                                inp.type(user["postal_code"], delay=80)
                                postal_filled = True
                                print(f"   ✏️ Postal code (fallback): {user['postal_code']}")
                                break
                        except:
                            pass

                # 2. Select "Urgent Consultation" from dropdown
                print(f"   📋 Selecting reason: Urgent Consultation...")
                try:
                    dropdown = page.locator("select, [role='combobox'], [role='listbox']").first
                    if dropdown.count() > 0 and dropdown.is_visible():
                        dropdown.click()
                        human_delay(500, 1000)
                        urgent_option = page.locator("option:has-text('Urgent'), text=Urgent Consultation").first
                        if urgent_option.count() > 0 and urgent_option.is_visible():
                            urgent_option.click()
                            print(f"   ✅ Selected: Urgent Consultation")
                    else:
                        reason_btn = page.locator("text=Urgent Consultation").first
                        if reason_btn.count() > 0 and reason_btn.is_visible():
                            reason_btn.click()
                            print(f"   ✅ Clicked: Urgent Consultation")
                except Exception as e:
                    print(f"   ⚠️ Reason selection error: {e}")

                # 3. Set radius to 50km
                print(f"   📋 Setting radius to 50km...")
                try:
                    radius_selectors = page.locator("select, [role='combobox']").all()
                    for sel in radius_selectors:
                        try:
                            sel.click()
                            human_delay(300, 500)
                            option_50 = page.locator("option[value='50'], text=50 km, text=50").first
                            if option_50.count() > 0 and option_50.is_visible():
                                option_50.click()
                                print(f"   ✅ Radius: 50km")
                                break
                        except:
                            pass
                except Exception as e:
                    print(f"   ⚠️ Radius error: {e}")

                human_delay(500, 1000)
                take_screenshot(page, "telus_04_search_filled", worker_id)

                # 4. Click Search button
                print(f"\n📂 STEP 5: Clicking Search...")
                search_clicked = False

                for btn_text in ["Search", "Rechercher", "Chercher", "Trouver"]:
                    if search_clicked:
                        break
                    try:
                        btn = page.locator(f"button:has-text('{btn_text}')").first
                        if btn.count() > 0 and btn.is_visible():
                            is_disabled = btn.get_attribute("disabled")
                            if is_disabled is None:
                                btn.click()
                                search_clicked = True
                                print(f"   ✅ Clicked '{btn_text}'")
                            else:
                                print(f"   ⚠️ '{btn_text}' is DISABLED")
                    except:
                        pass

                if not search_clicked:
                    try:
                        page.keyboard.press("Enter")
                        search_clicked = True
                        print(f"   ✅ Pressed Enter")
                    except:
                        pass

                if search_clicked:
                    human_delay(5000, 8000)
                    take_screenshot(page, "telus_05_results", worker_id)
                    current_url = page.url
                    print(f"   📍 Results URL: {current_url[:120]}")

                    # Check for available appointments
                    try:
                        body_text = page.locator("body").inner_text().lower()
                        slot_keywords = ["disponible", "available", "créneau", "plage", "horaire", "réserver", "book", "select"]
                        found_slots = [kw for kw in slot_keywords if kw in body_text]
                        if found_slots:
                            print(f"   🎯 SLOT INDICATORS: {found_slots}")
                        else:
                            print(f"   😴 No slot indicators found")
                            print(f"   📄 Results: {body_text[:400]}")
                    except:
                        pass

            # ═══════════════════════════════════════════════
            # STEP 6: Save result
            # ═══════════════════════════════════════════════
            place = {
                "name": "TELUS Health Appointment Access",
                "platform": "telus_health",
                "url": page.url,
                "city": "Quebec",
            }
            found.append(place)
            KillSwitch.add_slot(place)

        except Exception as e:
            print(f"   ❌ TELUS Health error: {e}")
            traceback.print_exc()
            take_screenshot(page, "telus_99_error", worker_id)
        finally:
            browser.close()

    print(f"\n🟣 Worker {worker_id} finished — Found {len(found)} slots")
    return found


# ================================================================
# 10. MAIN SEARCH COORDINATOR
# ================================================================

def search_all_platforms(user: dict) -> list:
    import requests

    all_slots = []
    postal_code = user["postal_code"]

    # Get user GPS coordinates
    user_coords = None
    try:
        response = requests.post(
            GOOGLE_MAPS_PROXY,
            json={
                "endpoint": "geocode/json",
                "params": {
                    "address": f"{postal_code}, Quebec, Canada",
                    "region": "ca"
                }
            },
            timeout=15
        )
        location = response.json()["results"][0]["geometry"]["location"]
        user_coords = (location["lat"], location["lng"])
        print(f"📍 User location: {user_coords}")
    except Exception as e:
        print(f"⚠️ Geocoding failed: {e}")

    # Sort clinics by distance
    nearby_clinics = []
    for clinic in CLINICS:
        if user_coords:
            distance = haversine(
                user_coords[0], user_coords[1],
                clinic["lat"], clinic["lng"]
            )
            if distance <= RADIUS_KM:
                clinic["distance"] = round(distance, 1)
                nearby_clinics.append(clinic)
        else:
            nearby_clinics.append(clinic)

    nearby_clinics.sort(key=lambda x: x.get("distance", 999))

    print(f"\n📍 {len(nearby_clinics)} clinics within {RADIUS_KM}km:")
    for clinic in nearby_clinics:
        print(f"   🏥 {clinic['name'][:40]} — {clinic.get('distance', '?')}km — {clinic['platform']}")

    print(f"\n🚀 Launching {MAX_WORKERS} parallel browsers...")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = []
        profiles = BROWSER_PROFILES[:MAX_WORKERS]

        # Worker 0: ClicSanté
        futures.append(executor.submit(scrape_clicsante, profiles[0], user, 0))

        # Worker 1: TELUS Health
        futures.append(executor.submit(scrape_telushealth, profiles[1], user, 1))

        # Workers 2-4: More TELUS Health instances with different profiles
        for i in range(2, MAX_WORKERS):
            profile_index = i
            if profile_index < len(profiles):
                futures.append(executor.submit(scrape_telushealth, profiles[profile_index], user, i))

        for future in as_completed(futures):
            if KillSwitch.is_active():
                print("\n🛑 Kill switch active — cancelling remaining workers...")
                break
            try:
                result = future.result(timeout=180)
                all_slots.extend(result)
            except Exception as e:
                print(f"   ⚠️ Worker future failed: {e}")

    return all_slots


# ================================================================
# 11. MAIN ENTRY POINT
# ================================================================

def main():
    print("╔══════════════════════════════════════════════╗")
    print("║        MYVITA HYBRID SCRAPER v10             ║")
    print("║    FREE Government Platforms Only            ║")
    print("║    ClicSanté + TELUS Health                  ║")
    print(f"║    {MAX_WORKERS} browsers | {RADIUS_KM}km radius | Headless: {HEADLESS}     ║")
    print("╚══════════════════════════════════════════════╝")

    user = get_user_data()
    print(f"\n📋 Patient: {user['first_name']} {user['last_name']}")
    print(f"📋 Postal Code: {user['postal_code']}")
    print(f"📋 RAMQ: {user['ramq'][:4]}****{user['ramq'][-2:]}")

    request_id = get_or_create_request(user)
    if request_id:
        print(f"📄 Request ID: {request_id}")

    KillSwitch.reset()
    start_time = time.time()
    slots = search_all_platforms(user)
    elapsed_time = time.time() - start_time

    save_to_firestore(user["postal_code"], slots)

    print(f"\n{'='*60}")
    print(f"📊 FINAL RESULTS — {elapsed_time:.0f} seconds")
    print(f"{'='*60}")

    if slots:
        print(f"\n🎉 FOUND {len(slots)} SLOTS:")
        for i, slot in enumerate(slots):
            print(f"\n   {i+1}. 📍 {slot.get('name', 'Unknown')}")
            print(f"      🏥 Platform: {slot.get('platform', 'Unknown')}")
            print(f"      🌆 City: {slot.get('city', 'Unknown')}")
            print(f"      🔗 URL: {slot.get('url', '')[:120]}")
    else:
        print(f"\n😴 No slots found")

    screenshot_count = len(os.listdir(DEBUG_DIR)) if os.path.exists(DEBUG_DIR) else 0
    print(f"\n📸 Screenshots saved: {screenshot_count} files in {DEBUG_DIR}")
    print("\n✅ Scraper finished successfully!")


if __name__ == "__main__":
    main()
