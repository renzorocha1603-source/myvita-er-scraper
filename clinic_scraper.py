#!/usr/bin/env python3
"""
MYVITA HYBRID SCRAPER v13 — Free Government Platforms
- ClicSanté: Medical consultation search (free, government-backed)
- TELUS Health Appointment Access: Government-backed FREE system
- JavaScript click for Continue button (bypasses SPA issues)
- Waits for actual page navigation
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
    {"name": "GMF Angus", "lat": 45.5401, "lng": -73.5658, "platform": "clic_sante", "url": "", "city": "Montreal"},
    {"name": "GMF St-Denis", "lat": 45.5264, "lng": -73.5932, "platform": "clic_sante", "url": "", "city": "Montreal"},
    {"name": "Médico-Centre Mont-Royal", "lat": 45.5163, "lng": -73.5786, "platform": "clic_sante", "url": "", "city": "Montreal"},
    {"name": "CM Mieux-Être St-Léonard", "lat": 45.5892, "lng": -73.6014, "platform": "clic_sante", "url": "", "city": "Montreal"},
    {"name": "CM Mieux-Être Levasseur", "lat": 45.5841, "lng": -73.6412, "platform": "clic_sante", "url": "", "city": "Montreal"},
    {"name": "Urgence Saint-Laurent", "lat": 45.5118, "lng": -73.6802, "platform": "clic_sante", "url": "", "city": "Montreal"},
    {"name": "Carrefour Médical Laval", "lat": 45.5684, "lng": -73.7431, "platform": "clic_sante", "url": "", "city": "Laval"},
    {"name": "Medi-Centre Chomedey", "lat": 45.5451, "lng": -73.7483, "platform": "clic_sante", "url": "", "city": "Laval"},
    {"name": "UnionMD Longueuil", "lat": 45.5252, "lng": -73.5135, "platform": "telus_health", "url": "https://appointmentaccess.telushealth.com/", "city": "Longueuil"},
    {"name": "GMF-U Charles-Le Moyne", "lat": 45.5184, "lng": -73.4831, "platform": "telus_health", "url": "https://appointmentaccess.telushealth.com/", "city": "Longueuil"},
    {"name": "Centre Médical Laval", "lat": 45.5521, "lng": -73.7314, "platform": "telus_health", "url": "https://appointmentaccess.telushealth.com/", "city": "Laval"},
    {"name": "GMF Terrebonne", "lat": 45.6982, "lng": -73.6391, "platform": "telus_health", "url": "https://appointmentaccess.telushealth.com/", "city": "Terrebonne"},
    {"name": "GMF des Seigneurs", "lat": 45.7025, "lng": -73.6514, "platform": "telus_health", "url": "https://appointmentaccess.telushealth.com/", "city": "Terrebonne"},
    {"name": "Clinique Sainte-Dorothée", "lat": 45.5312, "lng": -73.8115, "platform": "telus_health", "url": "https://appointmentaccess.telushealth.com/", "city": "Laval"},
    {"name": "Clinique de la Gare", "lat": 45.5582, "lng": -73.9015, "platform": "telus_health", "url": "https://appointmentaccess.telushealth.com/", "city": "Saint-Eustache"},
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
    birth_date = os.getenv("USER_BIRTH_DATE", "1970-01-01")
    birth_year = birth_date.split("-")[0] if "-" in birth_date else "1970"
    return {
        "first_name": os.getenv("USER_FIRST_NAME", "Jean"),
        "last_name": os.getenv("USER_LAST_NAME", "Tremblay"),
        "ramq": os.getenv("USER_RAMQ", "TREJ70010101"),
        "ramq_seq": os.getenv("USER_RAMQ_SEQ", "01"),
        "postal_code": os.getenv("POSTAL_CODE", "H1Y3H1"),
        "email": os.getenv("USER_EMAIL", "jean.tremblay@email.com"),
        "phone": os.getenv("USER_PHONE", "5145550101"),
        "birth_year": birth_year,
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
# 8. CLICSANTÉ SCRAPER
# ================================================================

def scrape_clicsante(profile: dict, user: dict, worker_id: int) -> list:
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
            print("\n📂 Step 1: Loading ClicSanté...")
            human_delay(1000, 3000)
            page.goto("https://portal3.clicsante.ca/", wait_until="networkidle", timeout=60000)
            human_delay(1500, 3000)

            try:
                page.locator("text=Sans frais").first.click(timeout=8000)
                print("   ✅ Popup closed")
            except:
                pass
            human_delay(1000, 2000)

            print(f"\n📂 Step 2: Entering postal code '{postal_code}'...")
            inputs = page.locator("input[type='text']").all()
            if inputs:
                inputs[0].click()
                inputs[0].fill("")
                inputs[0].type(postal_code, delay=random.randint(100, 200))
                print(f"   ✅ Postal code entered")
            human_delay(1000, 2000)
            take_screenshot(page, "cs_01_postal", worker_id)

            print("\n📂 Step 3: Selecting medical service...")
            for keyword in ["Consultation médicale", "Médecine familiale", "Médecin", "Soins de santé", "Urgence mineure"]:
                try:
                    button = page.locator(f"text={keyword}").first
                    if button.count() > 0 and button.is_visible():
                        button.click()
                        print(f"   ✅ Selected: '{keyword}'")
                        human_delay(2000, 4000)
                        break
                except:
                    pass

            print("\n📂 Step 4: Searching...")
            try:
                page.get_by_role("button", name="Search").first.click(timeout=8000)
            except:
                try:
                    page.get_by_role("button", name="Rechercher").first.click(timeout=5000)
                except:
                    page.keyboard.press("Enter")

            human_delay(5000, 8000)
            take_screenshot(page, "cs_02_results", worker_id)

            print("\n📂 Step 5: Extracting booking links...")
            booking_links = page.locator("a[href*='take-appt']").all()
            print(f"   📎 Found {len(booking_links)} booking links")

            for link in booking_links[:5]:
                if KillSwitch.is_active():
                    break
                try:
                    href = link.get_attribute("href") or ""

                    if "annuler" in href.lower() or "cancel" in href.lower():
                        continue

                    clinic_id_match = re.search(r'/(\d+)/take-appt', href)
                    if not clinic_id_match:
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
# 9. TELUS HEALTH — JavaScript click approach
# ================================================================

def scrape_telushealth(profile: dict, user: dict, worker_id: int) -> list:
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
            # STEP 1: Load TELUS Health
            # ═══════════════════════════════════════════════
            print(f"\n📂 STEP 1: Loading TELUS Health...")
            page.goto("https://appointmentaccess.telushealth.com/", wait_until="networkidle", timeout=60000)
            human_delay(3000, 4000)

            try:
                page.keyboard.press("Escape")
                human_delay(500, 1000)
            except:
                pass

            take_screenshot(page, "telus_01_home", worker_id)

            # ═══════════════════════════════════════════════
            # STEP 2: Fill form
            # ═══════════════════════════════════════════════
            print(f"\n📂 STEP 2: Filling form...")

            all_inputs = page.locator("input:visible").all()
            print(f"   📥 {len(all_inputs)} visible inputs")

            filled_count = 0

            for inp in all_inputs:
                try:
                    input_type = (inp.get_attribute("type") or "").lower()
                    if input_type in ["hidden", "submit", "button"]:
                        continue

                    is_disabled = inp.get_attribute("disabled")
                    if is_disabled is not None:
                        continue

                    placeholder = (inp.get_attribute("placeholder") or "").lower()
                    name = (inp.get_attribute("name") or "").lower()
                    aria_label = (inp.get_attribute("aria-label") or "").lower()
                    combined = f"{placeholder} {name} {aria_label}"

                    if any(kw in combined for kw in ["first", "prénom", "prenom", "firstname"]):
                        inp.click()
                        human_delay(300, 500)
                        inp.fill("")
                        inp.type(user["first_name"], delay=120)
                        filled_count += 1
                        print(f"   ✏️ [{filled_count}] First Name: {user['first_name']}")
                        human_delay(800, 1200)

                    elif any(kw in combined for kw in ["last", "nom", "lastname"]):
                        inp.click()
                        human_delay(300, 500)
                        inp.fill("")
                        inp.type(user["last_name"], delay=120)
                        filled_count += 1
                        print(f"   ✏️ [{filled_count}] Last Name: {user['last_name']}")
                        human_delay(800, 1200)

                    elif any(kw in combined for kw in ["ramq", "assurance", "health insurance", "abcd", "maladie", "health card"]):
                        inp.click()
                        human_delay(300, 500)
                        inp.fill("")
                        inp.type(user["ramq"], delay=120)
                        filled_count += 1
                        print(f"   ✏️ [{filled_count}] RAMQ: {user['ramq']}")
                        human_delay(2000, 3000)

                    elif any(kw in combined for kw in ["seq", "sequential", "séquentiel", "sequence"]):
                        inp.click()
                        human_delay(300, 500)
                        inp.fill("")
                        inp.type(user["ramq_seq"], delay=120)
                        filled_count += 1
                        print(f"   ✏️ [{filled_count}] Sequence: {user['ramq_seq']}")
                        human_delay(800, 1200)

                    elif any(kw in combined for kw in ["year", "année", "birth", "naissance"]):
                        inp.click()
                        human_delay(300, 500)
                        inp.fill("")
                        inp.type(user["birth_year"], delay=120)
                        filled_count += 1
                        print(f"   ✏️ [{filled_count}] Birth Year: {user['birth_year']}")
                        human_delay(800, 1200)

                    elif any(kw in combined for kw in ["email", "courriel"]):
                        inp.click()
                        human_delay(300, 500)
                        inp.fill("")
                        inp.type(user["email"], delay=120)
                        filled_count += 1
                        print(f"   ✏️ [{filled_count}] Email: {user['email']}")
                        human_delay(800, 1200)

                    elif any(kw in combined for kw in ["phone", "téléphone", "tel", "mobile"]):
                        inp.click()
                        human_delay(300, 500)
                        inp.fill("")
                        inp.type(user["phone"], delay=120)
                        filled_count += 1
                        print(f"   ✏️ [{filled_count}] Phone: {user['phone']}")
                        human_delay(800, 1200)

                except Exception as e:
                    print(f"   ⚠️ Field error: {e}")

            # Handle selections
            print(f"\n   📋 Handling selections...")
            human_delay(1000, 1500)

            try:
                male_label = page.locator("text=Male").first
                if male_label.count() > 0 and male_label.is_visible():
                    male_label.click()
                    print(f"   ✅ Sex: Male")
                    human_delay(800, 1200)
            except:
                pass

            try:
                fr_label = page.locator("text=Français").first
                if fr_label.count() > 0 and fr_label.is_visible():
                    fr_label.click()
                    print(f"   ✅ Language: Français")
                    human_delay(800, 1200)
            except:
                pass

            try:
                email_label = page.locator("text=Email only").first
                if email_label.count() > 0 and email_label.is_visible():
                    email_label.click()
                    print(f"   ✅ Communication: Email only")
                    human_delay(800, 1200)
            except:
                pass

            try:
                checkboxes = page.locator("input[type='checkbox']").all()
                for cb in checkboxes:
                    if cb.is_visible() and not cb.is_checked():
                        cb.check()
                        print(f"   ✅ Consent checked")
                        human_delay(500, 800)
                        break
            except:
                pass

            human_delay(1500, 2500)
            take_screenshot(page, "telus_02_form_filled", worker_id)

            # ═══════════════════════════════════════════════
            # STEP 3: Click Continue with JavaScript
            # ═══════════════════════════════════════════════
            print(f"\n📂 STEP 3: Clicking Continue (JavaScript)...")

            old_url = page.url

            # Use JavaScript to click the button
            js_click_result = page.evaluate("""
                () => {
                    const buttons = document.querySelectorAll('button');
                    for (const btn of buttons) {
                        if (btn.innerText.includes('Continue') && !btn.disabled) {
                            btn.click();
                            return 'clicked';
                        }
                    }
                    return 'not found';
                }
            """)
            print(f"   JS click result: {js_click_result}")

            # Wait for page to change
            human_delay(4000, 6000)

            new_url = page.url
            print(f"   📍 Old URL: {old_url[:80]}")
            print(f"   📍 New URL: {new_url[:120]}")

            take_screenshot(page, "telus_03_after_continue", worker_id)

            # Check if page advanced
            try:
                body_text = page.locator("body").inner_text()

                if "postal code" in body_text.lower() or "your postal code" in body_text.lower():
                    print(f"   ✅ Advanced to search page!")
                    advanced = True
                elif "Patient identification" in body_text and new_url == old_url:
                    print(f"   ⚠️ Still on form page — trying Enter key")
                    page.keyboard.press("Enter")
                    human_delay(3000, 5000)
                    new_url = page.url
                    body_text = page.locator("body").inner_text()
                    if "postal code" in body_text.lower():
                        print(f"   ✅ Advanced after Enter!")
                    else:
                        print(f"   ❌ Could not advance past form")
                        print(f"   📄 Content: {body_text[:300]}")
                else:
                    print(f"   📄 Content: {body_text[:300]}")
            except:
                pass

            # ═══════════════════════════════════════════════
            # STEP 4: Fill search form
            # ═══════════════════════════════════════════════
            print(f"\n📂 STEP 4: Filling search form...")
            human_delay(2000, 3000)
            take_screenshot(page, "telus_04_search_page", worker_id)

            # Fill postal code
            postal_filled = False
            for selector in [
                "input[placeholder*='G1G']",
                "input[placeholder*='postal']",
                "input[placeholder*='code']",
            ]:
                element = page.locator(selector).first
                if element.count() > 0 and element.is_visible():
                    current_val = element.input_value() or ""
                    if len(current_val) < 3:
                        element.click()
                        human_delay(300, 500)
                        element.fill("")
                        element.type(user["postal_code"], delay=80)
                        postal_filled = True
                        print(f"   ✏️ Postal: {user['postal_code']}")
                    break

            if not postal_filled:
                all_search_inputs = page.locator("input[type='text']:visible, input:not([type]):visible").all()
                for inp in all_search_inputs:
                    try:
                        is_disabled = inp.get_attribute("disabled")
                        if is_disabled is not None:
                            continue
                        current_val = inp.input_value() or ""
                        if len(current_val) < 3:
                            inp.click()
                            human_delay(300, 500)
                            inp.fill("")
                            inp.type(user["postal_code"], delay=80)
                            print(f"   ✏️ Postal (fallback): {user['postal_code']}")
                            break
                    except:
                        pass

            human_delay(1000, 2000)
            take_screenshot(page, "telus_04_search_filled", worker_id)

            # Click Search
            print(f"\n📂 STEP 5: Clicking Search...")
            search_clicked = False

            # Try JavaScript click for Search
            js_search_result = page.evaluate("""
                () => {
                    const buttons = document.querySelectorAll('button');
                    for (const btn of buttons) {
                        const text = btn.innerText.toLowerCase();
                        if ((text.includes('search') || text.includes('rechercher') || text.includes('chercher') || text.includes('trouver')) && !btn.disabled) {
                            btn.click();
                            return 'clicked: ' + btn.innerText;
                        }
                    }
                    return 'not found';
                }
            """)
            print(f"   JS search result: {js_search_result}")

            if "clicked" in str(js_search_result):
                search_clicked = True

            if not search_clicked:
                try:
                    page.keyboard.press("Enter")
                    search_clicked = True
                    print(f"   ✅ Pressed Enter")
                except:
                    pass

            if search_clicked:
                human_delay(6000, 9000)
                take_screenshot(page, "telus_05_results", worker_id)
                current_url = page.url
                print(f"   📍 Results: {current_url[:120]}")

                try:
                    body_text = page.locator("body").inner_text().lower()
                    # Better slot detection
                    real_slot_keywords = ["appointment available", "time slot", "book now", "select time", "choose time"]
                    found_real_slots = [kw for kw in real_slot_keywords if kw in body_text]
                    if found_real_slots:
                        print(f"   🎯 REAL SLOTS FOUND: {found_real_slots}")
                    else:
                        print(f"   📄 Results text: {body_text[:400]}")
                except:
                    pass

            # Save result
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

    user_coords = None
    try:
        response = requests.post(
            GOOGLE_MAPS_PROXY,
            json={"endpoint": "geocode/json", "params": {"address": f"{postal_code}, Quebec, Canada", "region": "ca"}},
            timeout=15
        )
        location = response.json()["results"][0]["geometry"]["location"]
        user_coords = (location["lat"], location["lng"])
        print(f"📍 User location: {user_coords}")
    except Exception as e:
        print(f"⚠️ Geocoding failed: {e}")

    nearby_clinics = []
    for clinic in CLINICS:
        if user_coords:
            distance = haversine(user_coords[0], user_coords[1], clinic["lat"], clinic["lng"])
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

        futures.append(executor.submit(scrape_clicsante, profiles[0], user, 0))
        futures.append(executor.submit(scrape_telushealth, profiles[1], user, 1))

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
    print("║        MYVITA HYBRID SCRAPER v13             ║")
    print("║    FREE Government Platforms Only            ║")
    print("║    ClicSanté + TELUS Health                  ║")
    print(f"║    {MAX_WORKERS} browsers | {RADIUS_KM}km radius | Headless: {HEADLESS}     ║")
    print("╚══════════════════════════════════════════════╝")

    user = get_user_data()
    print(f"\n📋 Patient: {user['first_name']} {user['last_name']}")
    print(f"📋 Postal Code: {user['postal_code']}")
    print(f"📋 RAMQ: {user['ramq'][:4]}****{user['ramq'][-2:]}")
    print(f"📋 Birth Year: {user['birth_year']}")

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
