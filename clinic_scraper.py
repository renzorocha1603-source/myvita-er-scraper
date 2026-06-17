#!/usr/bin/env python3
"""
MYVITA HYBRID SCRAPER v6 — Full Flow with Form Fix
- Bonjour Santé: Complete form filling + booking page extraction
- ClicSanté: Medical consultation search with booking links
- TELUS Santé: Deeplink extraction + form filling
- Kill switch after 5 slots
- Screenshots at every step for debugging
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
# 3. BROWSER PROFILES — 5 different human fingerprints
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
# 4. ALL CLINICS DATABASE
# ================================================================

CLINICS = [
    # === BONJOUR SANTÉ ===
    {"name": "Médico-Centre Mont-Royal", "lat": 45.5163, "lng": -73.5786, "platform": "bonjour_sante", "url": "https://bonjour-sante.ca/uno/clinique/montroyal", "city": "Montreal"},
    {"name": "GMF Angus", "lat": 45.5401, "lng": -73.5658, "platform": "bonjour_sante", "url": "https://bonjour-sante.ca/uno/clinique/angus", "city": "Montreal"},
    {"name": "GMF St-Denis", "lat": 45.5264, "lng": -73.5932, "platform": "bonjour_sante", "url": "https://bonjour-sante.ca/uno/clinique/stdenis", "city": "Montreal"},
    {"name": "Urgence Saint-Laurent", "lat": 45.5118, "lng": -73.6802, "platform": "bonjour_sante", "url": "https://bonjour-sante.ca/uno/clinique/cusl", "city": "Montreal"},
    {"name": "CM Mieux-Être Levasseur", "lat": 45.5841, "lng": -73.6412, "platform": "bonjour_sante", "url": "https://bonjour-sante.ca/uno/clinique/levasseur", "city": "Montreal"},
    {"name": "CM Mieux-Être St-Léonard", "lat": 45.5892, "lng": -73.6014, "platform": "bonjour_sante", "url": "https://bonjour-sante.ca/uno/clinique/mieuxetre", "city": "Montreal"},
    {"name": "Medi-Centre Chomedey", "lat": 45.5451, "lng": -73.7483, "platform": "bonjour_sante", "url": "https://bonjour-sante.ca/uno/clinique/medicentrechomedey", "city": "Laval"},
    {"name": "Carrefour Médical Laval", "lat": 45.5684, "lng": -73.7431, "platform": "bonjour_sante", "url": "https://bonjour-sante.ca/uno/clinique/lecarrefour", "city": "Laval"},
    {"name": "UnionMD Longueuil", "lat": 45.5252, "lng": -73.5135, "platform": "bonjour_sante", "url": "https://bonjour-sante.ca/uno/clinique/unionmdlongueuil", "city": "Longueuil"},
    {"name": "GMF Terrebonne", "lat": 45.6982, "lng": -73.6391, "platform": "bonjour_sante", "url": "https://bonjour-sante.ca/uno/clinique/cmterrebonne", "city": "Terrebonne"},

    # === TELUS SANTÉ (POMELO) ===
    {"name": "GMF-U Charles-Le Moyne", "lat": 45.5184, "lng": -73.4831, "platform": "telus_sante", "url": "https://qc.pomelo.health/gmfucharleslemoyne", "city": "Longueuil"},
    {"name": "Centre Médical Laval", "lat": 45.5521, "lng": -73.7314, "platform": "telus_sante", "url": "https://qc.pomelo.health/centremedicallaval", "city": "Laval"},
    {"name": "Clinique Sainte-Dorothée", "lat": 45.5312, "lng": -73.8115, "platform": "telus_sante", "url": "https://pomelo.health/cliniquemedicalesaintedorothee", "city": "Laval"},
    {"name": "Clinique de la Gare", "lat": 45.5582, "lng": -73.9015, "platform": "telus_sante", "url": "https://qc.pomelo.health/cliniquemedicaledelagare", "city": "Saint-Eustache"},
    {"name": "GMF des Seigneurs", "lat": 45.7025, "lng": -73.6514, "platform": "telus_sante", "url": "https://qc.pomelo.health/gmfdesseigneurs", "city": "Terrebonne"},
]

GOOGLE_MAPS_PROXY = "https://us-central1-myvita-app-c5ecd.cloudfunctions.net/googleMapsProxy"

# ================================================================
# 5. HELPER FUNCTIONS
# ================================================================

def take_screenshot(page, step_name: str, worker_id: int = 0):
    """Save a screenshot with timestamp for debugging"""
    try:
        timestamp = datetime.now().strftime("%H%M%S")
        filename = f"worker{worker_id}_{timestamp}_{step_name}.png"
        filepath = os.path.join(DEBUG_DIR, filename)
        page.screenshot(path=filepath, full_page=True)
        print(f"   📸 Screenshot saved: {filename}")
    except Exception as e:
        print(f"   ⚠️ Screenshot failed: {e}")


def human_delay(min_ms: int = 300, max_ms: int = 1000):
    """Random delay to simulate human behavior"""
    time.sleep(random.uniform(min_ms, max_ms) / 1000)


def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Calculate distance between two GPS coordinates in km"""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def get_user_data() -> dict:
    """Get user data from environment variables"""
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
# 6. KILL SWITCH — Stops all workers after 5 slots found
# ================================================================

class KillSwitch:
    """Thread-safe kill switch that stops all workers after finding 5 slots"""
    _active = False
    _found_slots = []
    _lock = threading.Lock()

    @classmethod
    def add_slot(cls, details: dict):
        """Add a found slot and check if kill switch should activate"""
        with cls._lock:
            # Avoid duplicate URLs
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
        """Check if kill switch is active"""
        with cls._lock:
            return cls._active

    @classmethod
    def get_results(cls) -> list:
        """Get all found slots"""
        with cls._lock:
            return list(cls._found_slots)

    @classmethod
    def reset(cls):
        """Reset the kill switch for a new search"""
        with cls._lock:
            cls._active = False
            cls._found_slots = []


# ================================================================
# 7. FIRESTORE HELPERS
# ================================================================

def save_to_firestore(postal_code: str, slots: list):
    """Save results to Firestore"""
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
    """Get existing request or create a new one in Firestore"""
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
    ClicSanté scraper for medical consultations.
    Searches by postal code, selects medical service, extracts booking links.
    """
    found = []
    postal_code = user["postal_code"]
    profile_name = profile.get("name", f"Worker-{worker_id}")
    stagger_delay = profile.get("delay", 0)

    if stagger_delay > 0:
        time.sleep(stagger_delay)

    if KillSwitch.is_active():
        print(f"   🛑 Kill switch active — skipping ClicSanté")
        return []

    print(f"\n{'='*60}")
    print(f"🔵 WORKER {worker_id}: ClicSanté Medical — {profile_name}")
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
            print("\n📂 Step 1: Loading ClicSanté portal...")
            human_delay(1000, 3000)
            page.goto("https://portal3.clicsante.ca/", wait_until="networkidle", timeout=60000)
            human_delay(1500, 3000)

            # Close popup
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
            take_screenshot(page, "clicsante_postal", worker_id)

            # Step 3: Try selecting medical service
            print("\n📂 Step 3: Selecting medical service...")
            service_selected = False
            medical_keywords = [
                "Médecine familiale",
                "Consultation médicale",
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
                        print(f"   ✅ Selected service: '{keyword}'")
                        human_delay(2000, 4000)
                        break
                except:
                    pass

            if not service_selected:
                print("   ⚠️ Could not select medical service — searching with default")

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

            human_delay(4000, 8000)
            take_screenshot(page, "clicsante_results", worker_id)

            # Step 5: Extract booking links
            print("\n📂 Step 5: Extracting booking links...")
            booking_links = page.locator("a[href*='take-appt']").all()
            print(f"   📎 Found {len(booking_links)} booking links")

            for link in booking_links[:5]:
                if KillSwitch.is_active():
                    break

                try:
                    href = link.get_attribute("href") or ""
                    clinic_id_match = re.search(r'/(\d+)/take-appt', href)
                    if not clinic_id_match:
                        continue

                    clinic_id = clinic_id_match.group(1)
                    booking_url = f"https://clients3.clicsante.ca/{clinic_id}/take-appt"

                    # Extract clinic name using JavaScript
                    clinic_name = link.evaluate("""
                        () => {
                            let parent = this.closest('li, article, div[class*="result"], div[class*="card"]');
                            if (!parent) parent = this.closest('div');
                            if (!parent) return '';
                            let headings = parent.querySelectorAll('h1, h2, h3, h4, strong, b, [class*="name"], [class*="title"]');
                            for (let h of headings) {
                                let text = h.innerText?.trim();
                                if (text && text.length > 5 && text.length < 200 && text !== 'Book appt.') return text;
                            }
                            return '';
                        }
                    """)

                    if not clinic_name:
                        clinic_name = f"Clinique ClicSanté #{clinic_id}"

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
                        print(f"   🔗 {booking_url}")

                except Exception as e:
                    print(f"   ⚠️ Error extracting link: {e}")

        except Exception as e:
            print(f"   ❌ ClicSanté error: {e}")
            traceback.print_exc()
            take_screenshot(page, "clicsante_error", worker_id)
        finally:
            browser.close()

    print(f"\n🔵 Worker {worker_id} finished — Found {len(found)} slots")
    return found


# ================================================================
# 9. BONJOUR SANTÉ SCRAPER — Full flow with form completion
# ================================================================

def scrape_bonjoursante(profile: dict, clinic: dict, user: dict, worker_id: int) -> list:
    """
    Bonjour Santé scraper with complete form filling.
    Flow: Clinic page → Click service → Fill RAMQ form → Booking page → Search
    """
    found = []
    clinic_name = clinic.get("name", "Unknown")
    clinic_url = clinic.get("url", "")
    stagger_delay = profile.get("delay", 0)

    if stagger_delay > 0:
        time.sleep(stagger_delay)

    if KillSwitch.is_active():
        print(f"   🛑 Kill switch active — skipping {clinic_name}")
        return []

    print(f"\n{'='*60}")
    print(f"🟢 WORKER {worker_id}: Bonjour Santé — {clinic_name}")
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
            # STEP 1: Load clinic page and click service button
            # ═══════════════════════════════════════════════
            print(f"\n📂 STEP 1: Loading clinic page...")
            print(f"   🌐 URL: {clinic_url}")
            
            try:
                page.goto(clinic_url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                print(f"   ⚠️ Timeout loading clinic page: {e}")
                print(f"   🔄 Retrying with longer timeout...")
                try:
                    page.goto(clinic_url, wait_until="load", timeout=60000)
                except:
                    print(f"   ❌ Could not load clinic page — skipping")
                    return found
            
            human_delay(1500, 2500)
            take_screenshot(page, "01_clinic_page", worker_id)

            print(f"\n📂 STEP 2: Clicking service button...")
            service_clicked = False
            service_button_texts = [
                "Médecin de famille ou urgence mineure",
                "Urgence mineure",
                "Consultation rapide",
                "Suivi avec mon médecin",
                "Dans ma clinique",
                "Consultation sans rendez-vous",
                "Sans rendez-vous",
            ]

            for button_text in service_button_texts:
                if service_clicked:
                    break
                try:
                    button = page.locator(f"text={button_text}").first
                    if button.count() > 0 and button.is_visible():
                        print(f"   👆 Clicking: '{button_text}'")
                        button.click()
                        service_clicked = True
                        human_delay(3000, 5000)
                        break
                except:
                    pass

            # Fallback: try generic search
            if not service_clicked:
                print(f"   ⚠️ No service button found — trying generic search page")
                try:
                    page.goto("https://bonjour-sante.ca/uno/hubidentificationpatient", wait_until="domcontentloaded", timeout=30000)
                except:
                    pass
                human_delay(2000, 3000)

            take_screenshot(page, "02_after_service_click", worker_id)
            current_url = page.url
            print(f"   📍 Current URL: {current_url[:120]}")

            # ═══════════════════════════════════════════════
            # STEP 3: Fill RAMQ identification form
            # ═══════════════════════════════════════════════
            if "hubidentificationpatient" in current_url or "identification" in current_url.lower():
                print(f"\n📂 STEP 3: Filling RAMQ identification form...")

                # Fill ALL visible text inputs with appropriate values
                all_text_inputs = page.locator("input[type='text']:visible, input:not([type]):visible").all()
                print(f"   📥 Found {len(all_text_inputs)} visible text inputs")
                
                for i, inp in enumerate(all_text_inputs):
                    try:
                        placeholder = (inp.get_attribute("placeholder") or "").lower()
                        name = (inp.get_attribute("name") or "").lower()
                        formcontrol = (inp.get_attribute("formcontrolname") or "").lower()
                        label = ""
                        
                        # Try to find associated label
                        try:
                            label_element = inp.evaluate("""
                                el => {
                                    let id = el.getAttribute('id');
                                    if (id) {
                                        let label = document.querySelector(`label[for="${id}"]`);
                                        if (label) return label.innerText.toLowerCase();
                                    }
                                    return '';
                                }
                            """)
                            if label_element:
                                label = label_element
                        except:
                            pass
                        
                        combined = f"{placeholder} {name} {formcontrol} {label}"
                        
                        # Determine what to fill based on field hints
                        if any(kw in combined for kw in ["ramq", "assurance", "maladie", "abcd"]):
                            inp.click()
                            inp.fill("")
                            inp.type(user["ramq"], delay=80)
                            print(f"   ✏️ Input {i}: RAMQ = {user['ramq']}")
                        elif any(kw in combined for kw in ["seq", "séquentiel", "sequentiel", "00"]):
                            inp.click()
                            inp.fill("")
                            inp.type(user["ramq_seq"], delay=80)
                            print(f"   ✏️ Input {i}: Sequence = {user['ramq_seq']}")
                        elif any(kw in combined for kw in ["prénom", "prenom", "first", "firstName"]):
                            inp.click()
                            inp.fill("")
                            inp.type(user["first_name"], delay=80)
                            print(f"   ✏️ Input {i}: First Name = {user['first_name']}")
                        elif any(kw in combined for kw in ["nom", "lastname", "last"]):
                            inp.click()
                            inp.fill("")
                            inp.type(user["last_name"], delay=80)
                            print(f"   ✏️ Input {i}: Last Name = {user['last_name']}")
                        elif any(kw in combined for kw in ["email", "courriel"]):
                            inp.click()
                            inp.fill("")
                            inp.type(user["email"], delay=80)
                            print(f"   ✏️ Input {i}: Email = {user['email']}")
                        elif any(kw in combined for kw in ["téléphone", "telephone", "phone", "tel"]):
                            inp.click()
                            inp.fill("")
                            inp.type(user["phone"], delay=80)
                            print(f"   ✏️ Input {i}: Phone = {user['phone']}")
                        elif any(kw in combined for kw in ["postal", "code"]):
                            inp.click()
                            inp.fill("")
                            inp.type(user["postal_code"], delay=80)
                            print(f"   ✏️ Input {i}: Postal = {user['postal_code']}")
                        else:
                            # Unknown field — skip
                            print(f"   ℹ️ Input {i}: Unknown field (placeholder='{placeholder}', name='{name}') — skipping")
                    except Exception as e:
                        print(f"   ⚠️ Error filling input {i}: {e}")

                human_delay(500, 1000)

                # Check consent checkbox
                try:
                    checkboxes = page.locator("input[type='checkbox']").all()
                    for checkbox in checkboxes:
                        if checkbox.is_visible() and not checkbox.is_checked():
                            checkbox.check()
                            print(f"   ✅ Consent checkbox checked")
                            break
                except:
                    pass

                take_screenshot(page, "03_form_filled", worker_id)

                # ═══════════════════════════════════════════════
                # STEP 4: Click Continue button
                # ═══════════════════════════════════════════════
                print(f"\n📂 STEP 4: Clicking Continue...")
                continue_clicked = False

                # Method 1: Click by data-test attribute
                try:
                    btn = page.locator("[data-test='continueButton']").first
                    if btn.count() > 0:
                        is_disabled = btn.get_attribute("disabled")
                        if is_disabled is None:
                            btn.click()
                            continue_clicked = True
                            print(f"   ✅ Clicked Continue (data-test)")
                        else:
                            print(f"   ⚠️ Continue button is DISABLED — form may be incomplete")
                            # Print page state for debugging
                            try:
                                body_text = page.locator("body").inner_text()
                                print(f"   📄 Page text (first 500 chars): {body_text[:500]}")
                            except:
                                pass
                except Exception as e:
                    print(f"   ⚠️ Method 1 failed: {e}")

                # Method 2: Click by button text
                if not continue_clicked:
                    try:
                        btn = page.locator("button:has-text('Continuer')").first
                        if btn.count() > 0 and btn.is_visible():
                            is_disabled = btn.get_attribute("disabled")
                            if is_disabled is None:
                                btn.click()
                                continue_clicked = True
                                print(f"   ✅ Clicked Continue (text)")
                            else:
                                print(f"   ⚠️ Continue button (text) is DISABLED")
                    except Exception as e:
                        print(f"   ⚠️ Method 2 failed: {e}")

                # Method 3: Try pressing Enter
                if not continue_clicked:
                    try:
                        page.keyboard.press("Enter")
                        human_delay(2000, 3000)
                        new_url = page.url
                        if new_url != current_url:
                            continue_clicked = True
                            print(f"   ✅ Pressed Enter — page navigated")
                        else:
                            print(f"   ⚠️ Enter did not navigate")
                    except Exception as e:
                        print(f"   ⚠️ Method 3 failed: {e}")

                if continue_clicked:
                    human_delay(3000, 5000)
                    take_screenshot(page, "04_after_continue", worker_id)
                    current_url = page.url
                    print(f"   📍 New URL: {current_url[:120]}")
                else:
                    print(f"   ❌ Could not click Continue — saving current URL anyway")
            else:
                print(f"   ℹ️ Not on identification page — skipping form")

            # ═══════════════════════════════════════════════
            # STEP 5: On booking page — set up search
            # ═══════════════════════════════════════════════
            if "hubidentificationpatient" not in current_url:
                print(f"\n📂 STEP 5: Setting up search on booking page...")

                # Try selecting "Consultation rapide" or similar
                for service_text in ["Consultation rapide", "Urgence mineure", "Médecin de famille"]:
                    try:
                        service_button = page.locator(f"text={service_text}").first
                        if service_button.count() > 0 and service_button.is_visible():
                            service_button.click()
                            print(f"   ✅ Selected: '{service_text}'")
                            human_delay(1000, 2000)
                            break
                    except:
                        pass

                # Enter postal code
                postal_selectors = [
                    "input[name*='postal']",
                    "input[placeholder*='code postal']",
                    "input[placeholder*='A0A']",
                    "input[formcontrolname*='postal']",
                ]
                for selector in postal_selectors:
                    element = page.locator(selector).first
                    if element.count() > 0 and element.is_visible():
                        element.click()
                        element.fill("")
                        element.type(user["postal_code"], delay=100)
                        print(f"   ✏️ Postal code filled: {user['postal_code']}")
                        break

                # Set distance to 50km
                try:
                    distance_buttons = page.locator("text=50").all()
                    for db in distance_buttons:
                        if db.is_visible():
                            db.click()
                            print(f"   ✅ Distance set to 50km")
                            break
                except:
                    pass

                take_screenshot(page, "05_booking_setup", worker_id)

                # Click Search
                print(f"\n📂 STEP 6: Clicking Search...")
                try:
                    search_button = page.get_by_role("button", name=re.compile("Rechercher|Search|Chercher", re.IGNORECASE)).first
                    if search_button.count() > 0 and search_button.is_visible():
                        is_disabled = search_button.get_attribute("disabled")
                        if is_disabled is None:
                            search_button.click()
                            print(f"   👆 Clicked 'Rechercher'")
                            human_delay(4000, 6000)
                            take_screenshot(page, "06_search_results", worker_id)
                            current_url = page.url
                            print(f"   📍 Results URL: {current_url[:120]}")
                        else:
                            print(f"   ⚠️ Search button is DISABLED")
                    else:
                        print(f"   ⚠️ Search button not found")
                except Exception as e:
                    print(f"   ⚠️ Error clicking Search: {e}")

            # ═══════════════════════════════════════════════
            # STEP 7: Save the result
            # ═══════════════════════════════════════════════
            place = {
                "name": clinic_name,
                "platform": "bonjour_sante",
                "url": page.url,
                "city": clinic.get("city", ""),
            }
            found.append(place)
            KillSwitch.add_slot(place)

        except Exception as e:
            print(f"   ❌ Bonjour Santé error: {e}")
            traceback.print_exc()
            take_screenshot(page, "99_error", worker_id)
        finally:
            browser.close()

    print(f"\n🟢 Worker {worker_id} finished — Found {len(found)} slots")
    return found


# ================================================================
# 10. TELUS SANTÉ (POMELO) SCRAPER — Deeplink extraction
# ================================================================

def scrape_telussante(profile: dict, clinic: dict, user: dict, worker_id: int) -> list:
    """
    TELUS Santé (Pomelo) scraper.
    Loads clinic page, clicks service button, captures redirect URL.
    """
    found = []
    clinic_name = clinic.get("name", "Unknown")
    clinic_url = clinic.get("url", "")
    stagger_delay = profile.get("delay", 0)

    if stagger_delay > 0:
        time.sleep(stagger_delay)

    if KillSwitch.is_active():
        print(f"   🛑 Kill switch active — skipping {clinic_name}")
        return []

    print(f"\n{'='*60}")
    print(f"🟣 WORKER {worker_id}: TELUS Santé — {clinic_name}")
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
            # Load clinic page
            print(f"\n📂 Loading: {clinic_url}")
            page.goto(clinic_url, wait_until="domcontentloaded", timeout=30000)
            human_delay(1500, 2500)
            take_screenshot(page, "01_clinic_page", worker_id)

            old_url = page.url

            # Click service button
            print(f"\n📂 Clicking service button...")
            service_texts = [
                "Prendre un rendez-vous",
                "Prendre rendez-vous",
                "Réserver",
                "Consultation",
                "Voir les disponibilités",
                "Prendre rendez-vous en ligne",
            ]

            for button_text in service_texts:
                try:
                    button = page.locator(f"text={button_text}").first
                    if button.count() > 0 and button.is_visible():
                        print(f"   👆 Clicking: '{button_text}'")
                        button.click()
                        human_delay(3000, 5000)
                        break
                except:
                    pass

            new_url = page.url
            if new_url != old_url:
                print(f"   🔗 Redirect detected!")
                print(f"   📍 Old: {old_url[:100]}")
                print(f"   📍 New: {new_url[:120]}")

            take_screenshot(page, "02_after_click", worker_id)

            # Try filling RAMQ if form is visible
            print(f"\n📂 Checking for RAMQ form...")
            ramq_selectors = [
                "input[placeholder*='ABCD']",
                "input[name*='ramq']",
            ]
            for selector in ramq_selectors:
                element = page.locator(selector).first
                if element.count() > 0 and element.is_visible():
                    element.click()
                    element.fill("")
                    element.type(user["ramq"], delay=100)
                    print(f"   ✏️ RAMQ filled: {user['ramq']}")
                    break

            # Save result
            place = {
                "name": clinic_name,
                "platform": "telus_sante",
                "url": page.url,
                "city": clinic.get("city", ""),
            }
            found.append(place)
            KillSwitch.add_slot(place)

        except Exception as e:
            print(f"   ❌ TELUS Santé error: {e}")
            traceback.print_exc()
            take_screenshot(page, "99_error", worker_id)
        finally:
            browser.close()

    print(f"\n🟣 Worker {worker_id} finished — Found {len(found)} slots")
    return found


# ================================================================
# 11. MAIN SEARCH COORDINATOR
# ================================================================

def search_all_platforms(user: dict) -> list:
    """
    Main search coordinator.
    Launches 5 parallel browsers: 1 for ClicSanté, 4 for nearby clinics.
    """
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

        # Workers 1-4: Nearby clinics
        for i, profile in enumerate(profiles[1:], 1):
            clinic_index = i - 1
            if clinic_index < len(nearby_clinics):
                clinic = nearby_clinics[clinic_index]
                if clinic["platform"] == "bonjour_sante":
                    futures.append(executor.submit(scrape_bonjoursante, profile, clinic, user, i))
                elif clinic["platform"] == "telus_sante":
                    futures.append(executor.submit(scrape_telussante, profile, clinic, user, i))

        # Wait for all futures
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
# 12. MAIN ENTRY POINT
# ================================================================

def main():
    """Main entry point for the scraper"""
    print("╔══════════════════════════════════════════════╗")
    print("║        MYVITA HYBRID SCRAPER v6              ║")
    print("║    ClicSanté + Bonjour Santé + TELUS         ║")
    print(f"║    {MAX_WORKERS} browsers | {RADIUS_KM}km radius | Headless: {HEADLESS}     ║")
    print("╚══════════════════════════════════════════════╝")

    # Get user data
    user = get_user_data()
    print(f"\n📋 Patient: {user['first_name']} {user['last_name']}")
    print(f"📋 Postal Code: {user['postal_code']}")
    print(f"📋 RAMQ: {user['ramq'][:4]}****{user['ramq'][-2:]}")

    # Create request in Firestore
    request_id = get_or_create_request(user)
    if request_id:
        print(f"📄 Request ID: {request_id}")

    # Reset kill switch
    KillSwitch.reset()

    # Run search
    start_time = time.time()
    slots = search_all_platforms(user)
    elapsed_time = time.time() - start_time

    # Save results
    save_to_firestore(user["postal_code"], slots)

    # Print summary
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
        print(f"\n😴 No slots found — try again later or use MiniClaw")

    # Screenshot summary
    screenshot_count = len(os.listdir(DEBUG_DIR)) if os.path.exists(DEBUG_DIR) else 0
    print(f"\n📸 Screenshots saved: {screenshot_count} files in {DEBUG_DIR}")
    print("\n✅ Scraper finished successfully!")


if __name__ == "__main__":
    main()
