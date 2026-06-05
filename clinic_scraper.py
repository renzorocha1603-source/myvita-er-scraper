#!/usr/bin/env python3
"""
MYVITA UNIFIED SCRAPER — FIRESTORE UPSERT
- Creates request document if it doesn't exist
- Searches by postal code if REQUEST_ID not found
- ClicSanté + Bonjour Santé + TELUS Santé
- 5 parallel headless browsers
"""

from playwright.sync_api import sync_playwright
import time
import random
import os
import re
import math
import json
import threading
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

# ================================================================
# 2. CONFIGURATION
# ================================================================

HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "5"))
SEARCH_DAYS = 7
RADIUS_KM = 50
REQUEST_COLLECTION = "concierge_requests"

# ================================================================
# 3. BROWSER PROFILES
# ================================================================

BROWSER_PROFILES = [
    {"name": "User-1-Chrome-Win", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36", "viewport": {"width": 1366, "height": 768}, "locale": "fr-CA", "timezone": "America/Montreal", "delay": 0},
    {"name": "User-2-Safari-Mac", "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.2 Safari/605.1.15", "viewport": {"width": 1440, "height": 900}, "locale": "fr-CA", "timezone": "America/Montreal", "delay": 15},
    {"name": "User-3-Firefox-Win", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0", "viewport": {"width": 1536, "height": 864}, "locale": "en-CA", "timezone": "America/Toronto", "delay": 30},
    {"name": "User-4-Chrome-Linux", "user_agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 720}, "locale": "fr-CA", "timezone": "America/Montreal", "delay": 45},
    {"name": "User-5-iPhone", "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 Version/17.1 Mobile/15E148 Safari/604.1", "viewport": {"width": 390, "height": 844}, "locale": "fr-CA", "timezone": "America/Montreal", "is_mobile": True, "delay": 60},
]

GOOGLE_MAPS_PROXY = "https://us-central1-myvita-app-c5ecd.cloudfunctions.net/googleMapsProxy"

# ================================================================
# 4. CLINICS DATABASE
# ================================================================

CLINICS_DATABASE = [
    {"name": "GMF Clinique medicale Angus", "lat": 45.5401, "lng": -73.5658, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/angus", "city": "Montreal"},
    {"name": "Clinique Medico-Centre Mont-Royal", "lat": 45.5163, "lng": -73.5786, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/montroyal", "city": "Montreal"},
    {"name": "Centre Medical Mieux-Etre Levasseur", "lat": 45.5841, "lng": -73.6412, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/levasseur", "city": "Montreal"},
    {"name": "Centre D'Urgence Saint-Laurent", "lat": 45.5118, "lng": -73.6802, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/cusl", "city": "Montreal"},
    {"name": "GMF Clinique Medicale St-Denis", "lat": 45.5264, "lng": -73.5932, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/stdenis", "city": "Montreal"},
    {"name": "Centre Medical Mieux-Etre Lasalle", "lat": 45.4312, "lng": -73.6248, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/cmmelasalle", "city": "Montreal"},
    {"name": "GMF A-R Centre medical Mieux-Etre St-Leonard", "lat": 45.5892, "lng": -73.6014, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/mieuxetre", "city": "Montreal"},
    {"name": "GMF Medi-Centre Chomedey", "lat": 45.5451, "lng": -73.7483, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/medicentrechomedey", "city": "Laval"},
    {"name": "GMF Le Carrefour Medical Laval", "lat": 45.5684, "lng": -73.7431, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/lecarrefour", "city": "Laval"},
    {"name": "Super-Clinique Polyclinique Medicale Fabreville", "lat": 45.5925, "lng": -73.7912, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/fabreville", "city": "Laval"},
    {"name": "Clinique Medicale Saint-Francois", "lat": 45.5781, "lng": -73.6542, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/stfrancois", "city": "Laval"},
    {"name": "GMF-R Centre Medical Laval", "lat": 45.5985, "lng": -73.6712, "platform": "bonjour_sante", "website": "https://www.lavalensante.com/", "city": "Laval"},
    {"name": "GMF-R Concorde", "lat": 45.5721, "lng": -73.6914, "platform": "bonjour_sante", "website": "https://www.lavalensante.com/", "city": "Laval"},
    {"name": "Clinique medicale privee Longueuil UnionMD", "lat": 45.5252, "lng": -73.5135, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/unionmdlongueuil", "city": "Longueuil"},
    {"name": "GMF-R Clinique Medicale Longueuil-Ouest", "lat": 45.5314, "lng": -73.5248, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/longueuilouest", "city": "Longueuil"},
    {"name": "GMF Dix30 Brossard", "lat": 45.4428, "lng": -73.4412, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/gmfdix30", "city": "Brossard"},
    {"name": "Clinique Sans Rendez-Vous Dix30", "lat": 45.4428, "lng": -73.4412, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/csansrendezvousdix30brossard", "city": "Brossard"},
    {"name": "GMF Clinique Medicale Terrebonne", "lat": 45.6982, "lng": -73.6391, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/cmterrebonne", "city": "Terrebonne"},
    {"name": "GMF du Grand Saint-Jerome", "lat": 45.8321, "lng": -73.9915, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/santhippolyte", "city": "Saint-Jerome"},
    {"name": "GMF Clinique Medicale Rosemere", "lat": 45.6382, "lng": -73.7915, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/rosemere", "city": "Rosemere"},
    {"name": "GMF Clinique Medicale Lorraine", "lat": 45.6512, "lng": -73.7814, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/cmlorraine", "city": "Lorraine"},
    {"name": "GMF-R Vaudreuil-Dorion", "lat": 45.3982, "lng": -74.0321, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/vaudreuildorion", "city": "Vaudreuil-Dorion"},
    {"name": "GMF-U de Maizerets", "lat": 46.8361, "lng": -71.2294, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/", "city": "Quebec"},
    {"name": "GMF-U Laurier", "lat": 46.7728, "lng": -71.2852, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/", "city": "Quebec"},
    {"name": "GMF-U Quatre-Bourgeois", "lat": 46.7794, "lng": -71.3021, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/", "city": "Quebec"},
    {"name": "GMF Clinique Medicale Sainte-Dorothee", "lat": 45.5312, "lng": -73.8115, "platform": "telus_sante", "website": "https://pomelo.health/cliniquemedicalesaintedorothee", "city": "Laval"},
    {"name": "GMF-U Charles-Le Moyne", "lat": 45.5184, "lng": -73.4831, "platform": "telus_sante", "website": "https://qc.pomelo.health/gmfucharleslemoyne", "city": "Longueuil"},
    {"name": "GMF Centre Medical Laval", "lat": 45.5521, "lng": -73.7314, "platform": "telus_sante", "website": "https://qc.pomelo.health/centremedicallaval", "city": "Laval"},
    {"name": "GMF Clinique Medicale de la Gare", "lat": 45.5582, "lng": -73.9015, "platform": "telus_sante", "website": "https://qc.pomelo.health/cliniquemedicaledelagare", "city": "Saint-Eustache"},
    {"name": "GMF Clinique Medicale Saint-Luc", "lat": 45.3512, "lng": -73.2842, "platform": "telus_sante", "website": "https://qc.pomelo.health/cliniquemedicalesaintluc", "city": "Saint-Jean-sur-Richelieu"},
    {"name": "GMF des Seigneurs Terrebonne", "lat": 45.7025, "lng": -73.6514, "platform": "telus_sante", "website": "https://qc.pomelo.health/gmfdesseigneurs", "city": "Terrebonne"},
    {"name": "GMF L'Assomption", "lat": 45.8312, "lng": -73.4215, "platform": "telus_sante", "website": "https://qc.pomelo.health/", "city": "L'Assomption"},
]

# ================================================================
# 5. KILL SWITCH
# ================================================================

class KillSwitch:
    _active = False
    _found_slots = []
    _lock = threading.Lock()
    
    @classmethod
    def add_slot(cls, details: dict):
        with cls._lock:
            cls._found_slots.append(details)
            if len(cls._found_slots) >= 3:
                cls._active = True
    
    @classmethod
    def is_active(cls) -> bool:
        with cls._lock:
            return cls._active
    
    @classmethod
    def get_results(cls):
        with cls._lock:
            return list(cls._found_slots)
    
    @classmethod
    def reset(cls):
        with cls._lock:
            cls._active = False
            cls._found_slots = []

# ================================================================
# 6. UTILITIES
# ================================================================

def haversine(lat1, lng1, lat2, lng2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def human_delay(min_ms=300, max_ms=1000):
    time.sleep(random.uniform(min_ms, max_ms) / 1000)

def get_user_data():
    return {
        "first_name": os.getenv("USER_FIRST_NAME", ""),
        "last_name": os.getenv("USER_LAST_NAME", ""),
        "ramq": os.getenv("USER_RAMQ", ""),
        "ramq_seq": os.getenv("USER_RAMQ_SEQ", "01"),
        "birth_date": os.getenv("USER_BIRTH_DATE", ""),
        "sex": os.getenv("USER_SEX", "M"),
        "email": os.getenv("USER_EMAIL", ""),
        "phone": os.getenv("USER_PHONE", ""),
        "postal_code": os.getenv("POSTAL_CODE", "H1Y3H1"),
    }

def check_page_for_slots(page) -> bool:
    content = page.content().lower()
    has_slot = any(kw in content for kw in ["disponible", "available", "creneau", "plage horaire", "reserver", "confirmer"])
    no_slot = any(kw in content for kw in ["aucun rendez-vous", "no appointment", "desole", "sorry", "complet", "full", "impossible"])
    return has_slot and not no_slot

def fill_field(page, selectors: list, value: str):
    for sel in selectors:
        el = page.locator(sel).first
        if el.count() > 0:
            try:
                el.fill(value)
                return True
            except:
                pass
    return False

def click_button(page, texts: list):
    for text in texts:
        btn = page.get_by_role("button", name=re.compile(text, re.I)).first
        if btn.count() > 0:
            try:
                btn.click()
                return True
            except:
                pass
    return False

# ================================================================
# 7. FIRESTORE — UPSERT (create if not exists)
# ================================================================

def get_or_create_request(user: dict) -> str:
    """Find pending request or create a new one"""
    if db is None:
        return os.getenv("REQUEST_ID", "")
    
    request_id = os.getenv("REQUEST_ID", "")
    postal = user["postal_code"][:3].upper()
    
    try:
        # First check if the passed ID exists
        if request_id:
            doc = db.collection(REQUEST_COLLECTION).document(request_id).get()
            if doc.exists:
                print(f"📝 Found request: {request_id[:8]}")
                return request_id
        
        # Search by postal code for pending requests
        docs = db.collection(REQUEST_COLLECTION)\
            .where("postal_code", ">=", postal)\
            .where("postal_code", "<=", postal + "z")\
            .where("status", "in", ["pending", "scraper_running"])\
            .order_by("postal_code")\
            .order_by("created_at", direction="DESCENDING")\
            .limit(1)\
            .stream()
        
        for doc in docs:
            print(f"📝 Found pending: {doc.id[:8]}")
            return doc.id
        
        # Create new request document
        if request_id:
            db.collection(REQUEST_COLLECTION).document(request_id).set({
                "status": "scraper_running",
                "scraper_status": "running",
                "postal_code": postal,
                "first_name": user["first_name"],
                "last_name": user["last_name"],
                "email": user["email"],
                "phone": user["phone"],
                "ramq": user["ramq"],
                "ramq_seq": user["ramq_seq"],
                "birth_date": user["birth_date"],
                "sex": user["sex"],
                "created_at": firestore.SERVER_TIMESTAMP,
                "updated_at": firestore.SERVER_TIMESTAMP,
            })
            print(f"📝 Created request: {request_id[:8]}")
            return request_id
        
        # No ID provided, create anonymous
        new_ref = db.collection(REQUEST_COLLECTION).document()
        new_ref.set({
            "status": "scraper_running",
            "scraper_status": "running",
            "postal_code": postal,
            "first_name": user["first_name"],
            "last_name": user["last_name"],
            "created_at": firestore.SERVER_TIMESTAMP,
        })
        print(f"📝 Created anonymous request: {new_ref.id[:8]}")
        return new_ref.id
        
    except Exception as e:
        print(f"⚠️ Firestore error: {e}")
        return request_id

def save_results(request_id: str, slots: list, user: dict):
    """Save scraper results to Firestore"""
    if db is None or not request_id:
        return
    try:
        doc_ref = db.collection(REQUEST_COLLECTION).document(request_id)
        if slots:
            doc_ref.update({
                "status": "scraper_completed",
                "scraper_result": {
                    "found": True,
                    "slots": slots,
                    "completed_at": datetime.now().isoformat(),
                },
                "scraper_status": "completed",
                "updated_at": firestore.SERVER_TIMESTAMP,
            })
            print(f"✅ Saved {len(slots)} slots to {request_id[:8]}")
        else:
            doc_ref.update({
                "status": "pending",
                "scraper_result": {
                    "found": False,
                    "slots": [],
                    "completed_at": datetime.now().isoformat(),
                },
                "scraper_status": "completed",
                "updated_at": firestore.SERVER_TIMESTAMP,
            })
            print(f"😴 No slots — request {request_id[:8]} stays pending")
    except Exception as e:
        print(f"❌ Save failed: {e}")

# ================================================================
# 8. CLICSANTÉ
# ================================================================

def scrape_clicsante(profile: dict, user: dict, worker_id: int) -> list:
    found = []
    postal_code = user["postal_code"]
    profile_name = profile.get("name", f"Worker-{worker_id}")
    stagger = profile.get("delay", 0)
    if stagger > 0:
        time.sleep(stagger)
    
    if KillSwitch.is_active():
        return []
    
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
            page.goto("https://portal3.clicsante.ca/", wait_until="networkidle", timeout=60000)
            human_delay(1000, 2000)
            
            try:
                page.locator("text=Sans frais").first.click(timeout=8000)
            except:
                pass
            human_delay(500, 1000)
            
            inputs = page.locator("input[type='text']").all()
            if inputs:
                inputs[0].click()
                inputs[0].fill("")
                inputs[0].type(postal_code, delay=random.randint(100, 200))
            human_delay(1000, 2000)
            
            try:
                page.get_by_role("button", name="Search").first.click(timeout=8000)
            except:
                try:
                    page.get_by_role("button", name="Rechercher").first.click(timeout=5000)
                except:
                    page.keyboard.press("Enter")
            human_delay(3000, 5000)
            
            book_links = page.locator("a[href*='take-appt']").all()
            
            for link in book_links[:5]:
                if KillSwitch.is_active():
                    break
                try:
                    href = link.get_attribute("href") or ""
                    clinic_id_match = re.search(r'/(\d+)/take-appt', href)
                    if not clinic_id_match:
                        continue
                    clinic_id = clinic_id_match.group(1)
                    url = f"https://clients3.clicsante.ca/{clinic_id}/take-appt"
                    
                    name = link.evaluate("""el => {
                        let parent = el.closest('li, article, div');
                        if (!parent) return '';
                        let headings = parent.querySelectorAll('h1, h2, h3, h4, strong, b');
                        for (let h of headings) {
                            let text = h.innerText?.trim();
                            if (text && text.length > 5 && text.length < 200) return text;
                        }
                        return '';
                    }""") or f"Clinique #{clinic_id}"
                    
                    found.append({"name": name[:150], "platform": "clicsante", "url": url})
                    KillSwitch.add_slot(found[-1])
                except:
                    pass
        
        except Exception as e:
            pass
        finally:
            browser.close()
    
    return found

# ================================================================
# 9. BONJOUR SANTÉ
# ================================================================

def scrape_bonjoursante(profile: dict, clinic: dict, user: dict, worker_id: int) -> list:
    found = []
    clinic_name = clinic.get("name", "Unknown")
    profile_name = profile.get("name", f"Worker-{worker_id}")
    stagger = profile.get("delay", 0)
    if stagger > 0:
        time.sleep(stagger)
    
    if KillSwitch.is_active():
        return []
    
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
            page.goto(clinic["website"], wait_until="domcontentloaded", timeout=30000)
            human_delay(1000, 2000)
            
            rdv_btn = page.get_by_text("Prendre rendez-vous", exact=False).first
            if rdv_btn.count() > 0:
                rdv_btn.click()
                human_delay(2000, 4000)
            else:
                return []
            
            for service in ["Urgence mineure", "Medecin de famille ou urgence mineure", "Consultation rapide", "Suivi"]:
                btn = page.get_by_text(service, exact=False).first
                if btn.count() > 0:
                    btn.click()
                    human_delay(2000, 4000)
                    break
            
            fill_field(page, ["input[name*='ramq']", "input[placeholder*='ABCD']"], user["ramq"])
            fill_field(page, ["input[name*='seq']", "input[placeholder*='00']"], user["ramq_seq"])
            fill_field(page, ["input[name*='firstName']", "input[name*='prenom']", "input[placeholder*='Prenom']"], user["first_name"])
            fill_field(page, ["input[name*='lastName']", "input[name*='nom']", "input[placeholder*='Nom']"], user["last_name"])
            human_delay(500, 1000)
            
            cb = page.locator("input[type='checkbox']").first
            if cb.count() > 0:
                try:
                    cb.check()
                except:
                    pass
            
            click_button(page, ["Continuer", "Suivant", "Next"])
            human_delay(2000, 4000)
            
            fill_field(page, ["input[name*='postal']", "input[placeholder*='code postal']"], user["postal_code"])
            
            for day_offset in range(SEARCH_DAYS):
                if KillSwitch.is_active():
                    break
                
                target_date = datetime.now() + timedelta(days=day_offset)
                date_str = target_date.strftime("%Y-%m-%d")
                
                date_input = page.locator("input[type='date']").first
                if date_input.count() > 0:
                    try:
                        date_input.fill(date_str)
                        human_delay(500, 1000)
                    except:
                        pass
                
                if click_button(page, ["Rechercher", "Search", "Chercher"]):
                    human_delay(2000, 4000)
                
                if check_page_for_slots(page):
                    found.append({"clinic_name": clinic_name, "platform": "bonjour_sante", "date": target_date.strftime("%d/%m/%Y"), "url": page.url, "city": clinic.get("city", "")})
                    KillSwitch.add_slot(found[-1])
                    return found
                
                for cycle in range(3):
                    if KillSwitch.is_active():
                        break
                    page.reload(wait_until="domcontentloaded")
                    human_delay(5000, 8000)
                    if check_page_for_slots(page):
                        found.append({"clinic_name": clinic_name, "platform": "bonjour_sante", "date": target_date.strftime("%d/%m/%Y"), "url": page.url, "city": clinic.get("city", "")})
                        KillSwitch.add_slot(found[-1])
                        return found
        
        except:
            pass
        finally:
            browser.close()
    
    return found

# ================================================================
# 10. TELUS SANTÉ
# ================================================================

def scrape_telussante(profile: dict, clinic: dict, user: dict, worker_id: int) -> list:
    found = []
    clinic_name = clinic.get("name", "Unknown")
    profile_name = profile.get("name", f"Worker-{worker_id}")
    stagger = profile.get("delay", 0)
    if stagger > 0:
        time.sleep(stagger)
    
    if KillSwitch.is_active():
        return []
    
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
            page.goto(clinic["website"], wait_until="domcontentloaded", timeout=30000)
            human_delay(1000, 2000)
            
            rdv_btn = page.get_by_text("Prendre rendez-vous", exact=False).first
            if rdv_btn.count() > 0:
                rdv_btn.click()
                human_delay(2000, 4000)
            
            fill_field(page, ["input[placeholder*='Prenom']", "input[name*='firstName']"], user["first_name"])
            fill_field(page, ["input[placeholder*='Nom']", "input[name*='lastName']"], user["last_name"])
            fill_field(page, ["input[placeholder*='ABCD']", "input[name*='ramq']"], user["ramq"])
            fill_field(page, ["input[placeholder*='00']", "input[name*='seq']"], user["ramq_seq"])
            
            email_el = page.locator("input[type='email']").first
            if email_el.count() > 0:
                email_el.fill(user["email"])
            
            phone_el = page.locator("input[type='tel'], input[name*='phone']").first
            if phone_el.count() > 0:
                phone_el.fill(user["phone"])
            
            cb = page.locator("input[type='checkbox']").first
            if cb.count() > 0:
                try:
                    cb.check()
                except:
                    pass
            
            click_button(page, ["Continuer", "Suivant"])
            human_delay(2000, 4000)
            
            fill_field(page, ["input[name*='postal']", "input[placeholder*='code postal']"], user["postal_code"])
            
            for day_offset in range(SEARCH_DAYS):
                if KillSwitch.is_active():
                    break
                
                target_date = datetime.now() + timedelta(days=day_offset)
                date_str = target_date.strftime("%Y-%m-%d")
                
                date_input = page.locator("input[type='date']").first
                if date_input.count() > 0:
                    try:
                        date_input.fill(date_str)
                        human_delay(500, 1000)
                    except:
                        pass
                
                if click_button(page, ["Rechercher", "Search"]):
                    human_delay(2000, 4000)
                
                if check_page_for_slots(page):
                    found.append({"clinic_name": clinic_name, "platform": "telus_sante", "date": target_date.strftime("%d/%m/%Y"), "url": page.url, "city": clinic.get("city", "")})
                    KillSwitch.add_slot(found[-1])
                    return found
                
                for cycle in range(3):
                    if KillSwitch.is_active():
                        break
                    page.reload(wait_until="domcontentloaded")
                    human_delay(5000, 8000)
                    if check_page_for_slots(page):
                        found.append({"clinic_name": clinic_name, "platform": "telus_sante", "date": target_date.strftime("%d/%m/%Y"), "url": page.url, "city": clinic.get("city", "")})
                        KillSwitch.add_slot(found[-1])
                        return found
        
        except:
            pass
        finally:
            browser.close()
    
    return found

# ================================================================
# 11. MAIN SEARCH
# ================================================================

def search_all_platforms(user: dict) -> list:
    import requests
    
    all_slots = []
    user_coords = None
    
    try:
        r = requests.post(GOOGLE_MAPS_PROXY, json={"endpoint": "geocode/json", "params": {"address": f"{user['postal_code']}, Quebec, Canada", "region": "ca"}}, timeout=15)
        loc = r.json()["results"][0]["geometry"]["location"]
        user_coords = (loc["lat"], loc["lng"])
    except:
        pass
    
    nearby = []
    for clinic in CLINICS_DATABASE:
        if user_coords:
            dist = haversine(user_coords[0], user_coords[1], clinic["lat"], clinic["lng"])
            if dist <= RADIUS_KM:
                clinic["distance"] = round(dist, 1)
                nearby.append(clinic)
        else:
            nearby.append(clinic)
    
    print(f"📍 {len(nearby)} clinics within {RADIUS_KM}km | {MAX_WORKERS} browsers | {SEARCH_DAYS}-day")
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = []
        profiles = BROWSER_PROFILES[:MAX_WORKERS]
        
        futures.append(executor.submit(scrape_clicsante, profiles[0], user, 0))
        
        idx = 0
        for i, profile in enumerate(profiles[1:], 1):
            if idx < len(nearby):
                clinic = nearby[idx]
                idx += 1
                if clinic["platform"] == "bonjour_sante":
                    futures.append(executor.submit(scrape_bonjoursante, profile, clinic, user, i))
                elif clinic["platform"] == "telus_sante":
                    futures.append(executor.submit(scrape_telussante, profile, clinic, user, i))
        
        for future in as_completed(futures):
            if KillSwitch.is_active():
                break
            try:
                result = future.result(timeout=600)
                all_slots.extend(result)
            except:
                pass
    
    return all_slots

# ================================================================
# 12. MAIN
# ================================================================

def main():
    print("╔══════════════════════════════════════════════╗")
    print("║   MYVITA UNIFIED SCRAPER                     ║")
    print(f"║   {MAX_WORKERS} browsers | {SEARCH_DAYS}-day | Headless: {HEADLESS}   ║")
    print("╚══════════════════════════════════════════════╝")
    
    user = get_user_data()
    print(f"📋 {user['first_name']} {user['last_name']} | {user['postal_code']}")
    
    request_id = get_or_create_request(user)
    
    KillSwitch.reset()
    start = time.time()
    slots = search_all_platforms(user)
    elapsed = time.time() - start
    
    if request_id:
        save_results(request_id, slots, user)
    
    if slots:
        print(f"\n🎉 FOUND {len(slots)} SLOTS in {elapsed:.0f}s:")
        for s in slots:
            print(f"   ✅ {s.get('clinic_name', s.get('name', '?'))} — {s.get('platform', '?')}")
    else:
        print(f"\n😴 No slots in {elapsed:.0f}s — escalate to MiniClaw")
    
    print("\n✅ Done")

if __name__ == "__main__":
    main()
