#!/usr/bin/env python3
"""
MYVITA UNIFIED SCRAPER — FINAL WITH DEBUG SCREENSHOTS
- Takes screenshots at EVERY step
- Saves to /tmp/myvita_debug/ with timestamps
- Detailed logging of what it sees on each page
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
DEBUG_DIR = "/tmp/myvita_debug"

# Create debug directory
os.makedirs(DEBUG_DIR, exist_ok=True)
print(f"📸 Screenshots will be saved to: {DEBUG_DIR}")

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
]

# ================================================================
# 5. SCREENSHOT HELPER
# ================================================================

def take_screenshot(page, step_name: str, worker_id: int = 0):
    """Take a screenshot and save it with timestamp"""
    try:
        timestamp = datetime.now().strftime("%H%M%S")
        filename = f"worker{worker_id}_{timestamp}_{step_name}.png"
        filepath = os.path.join(DEBUG_DIR, filename)
        page.screenshot(path=filepath, full_page=True)
        
        # Also dump page text content
        text_filename = f"worker{worker_id}_{timestamp}_{step_name}.txt"
        text_filepath = os.path.join(DEBUG_DIR, text_filename)
        try:
            page_text = page.locator("body").inner_text()[:2000]  # First 2000 chars
            with open(text_filepath, 'w', encoding='utf-8') as f:
                f.write(f"URL: {page.url}\n")
                f.write(f"Title: {page.title()}\n")
                f.write(f"---PAGE TEXT---\n{page_text}")
        except:
            pass
            
        print(f"   📸 Screenshot: {filename}")
        return True
    except Exception as e:
        print(f"   ⚠️ Screenshot failed: {e}")
        return False

def log_page_state(page, step_name: str, worker_id: int = 0):
    """Log what's visible on the page"""
    try:
        url = page.url
        title = page.title()
        body_text = page.locator("body").inner_text()[:500]
        
        # Check for key elements
        buttons = page.locator("button").all()
        button_texts = [b.inner_text()[:50] for b in buttons[:10] if b.is_visible()]
        
        inputs = page.locator("input").all()
        input_count = len([i for i in inputs if i.is_visible()])
        
        links = page.locator("a").all()
        link_count = len([l for l in links if l.is_visible()])
        
        print(f"\n   🔍 [{step_name}] Worker {worker_id}")
        print(f"   📍 URL: {url[:100]}")
        print(f"   📝 Title: {title[:80]}")
        print(f"   🔘 Visible buttons ({len(button_texts)}): {button_texts[:5]}")
        print(f"   📥 Visible inputs: {input_count}")
        print(f"   🔗 Visible links: {link_count}")
        print(f"   📄 Body preview: {body_text[:200]}")
        
        # Check for key French/English phrases
        keywords = ["rendez-vous", "appointment", "disponible", "available", 
                   "complet", "full", "aucun", "sorry", "désolé",
                   "créneau", "slot", "réserver", "book", "continuer", "continue"]
        found_keywords = [kw for kw in keywords if kw in body_text.lower()]
        if found_keywords:
            print(f"   🔑 Keywords found: {found_keywords}")
            
    except Exception as e:
        print(f"   ⚠️ Log failed: {e}")

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
            cls._found_slots.append(details)
            print(f"\n   🎯 SLOT FOUND! Total: {len(cls._found_slots)}")
            print(f"   📋 Details: {json.dumps(details, indent=2, ensure_ascii=False)}")
            if len(cls._found_slots) >= 3:
                cls._active = True
                print("   🛑 KILL SWITCH ACTIVATED — 3 slots found!")
    
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
# 7. UTILITIES
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
        "first_name": os.getenv("USER_FIRST_NAME", "Jean"),
        "last_name": os.getenv("USER_LAST_NAME", "Tremblay"),
        "ramq": os.getenv("USER_RAMQ", "TREJ70010101"),
        "ramq_seq": os.getenv("USER_RAMQ_SEQ", "01"),
        "birth_date": os.getenv("USER_BIRTH_DATE", "1970-01-01"),
        "sex": os.getenv("USER_SEX", "M"),
        "email": os.getenv("USER_EMAIL", "jean.tremblay@email.com"),
        "phone": os.getenv("USER_PHONE", "5145550101"),
        "postal_code": os.getenv("POSTAL_CODE", "H1Y3H1"),
    }

def check_page_for_slots(page) -> bool:
    content = page.content().lower()
    has_slot = any(kw in content for kw in ["disponible", "available", "creneau", "plage horaire", "reserver", "confirmer", "choisir", "select"])
    no_slot = any(kw in content for kw in ["aucun rendez-vous", "no appointment", "desole", "sorry", "complet", "full", "impossible", "aucune disponibilité"])
    result = has_slot and not no_slot
    if has_slot:
        print(f"   ✅ Slot indicators found in page content")
    if no_slot:
        print(f"   ❌ No-slot indicators found in page content")
    return result

def fill_field(page, selectors: list, value: str):
    for sel in selectors:
        el = page.locator(sel).first
        if el.count() > 0:
            try:
                el.fill(value)
                print(f"   ✏️ Filled field '{sel}' with '{value}'")
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
                print(f"   👆 Clicked button: '{text}'")
                return True
            except:
                pass
    return False

# ================================================================
# 8. FIRESTORE HELPERS
# ================================================================

def is_request_cancelled(request_id: str) -> bool:
    if db is None or not request_id:
        return False
    try:
        doc = db.collection(REQUEST_COLLECTION).document(request_id).get()
        if doc.exists:
            status = doc.to_dict().get("status", "")
            return status in ["cancelled", "completed"]
    except:
        pass
    return False

def get_or_create_request(user: dict) -> str:
    if db is None:
        return ""
    
    request_id = os.getenv("REQUEST_ID", "")
    postal = user["postal_code"][:3].upper()
    
    try:
        if request_id:
            doc = db.collection(REQUEST_COLLECTION).document(request_id).get()
            if doc.exists:
                print(f"📝 Using existing: {request_id[:8]}")
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
            "birth_date": user["birth_date"],
            "gender": user["sex"],
            "created_at": firestore.SERVER_TIMESTAMP,
        }
        
        print(f"   🔍 Creating document...")
        result = db.collection(REQUEST_COLLECTION).add(new_data)
        doc_id = result[1].id
        print(f"   📄 Doc ID: {doc_id}")
        return doc_id
        
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return ""

def save_results(request_id: str, slots: list, user: dict):
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
                    "message": "No slots found — use MiniClaw",
                    "completed_at": datetime.now().isoformat(),
                },
                "scraper_status": "completed",
                "updated_at": firestore.SERVER_TIMESTAMP,
            })
            print(f"😴 No slots — {request_id[:8]} stays pending")
    except Exception as e:
        print(f"❌ Save failed: {e}")

# ================================================================
# 9. CLICSANTÉ
# ================================================================

def scrape_clicsante(profile: dict, user: dict, worker_id: int) -> list:
    found = []
    postal_code = user["postal_code"]
    profile_name = profile.get("name", f"Worker-{worker_id}")
    stagger = profile.get("delay", 0)
    if stagger > 0: time.sleep(stagger)
    if KillSwitch.is_active(): return []
    
    print(f"\n{'='*60}")
    print(f"🔵 WORKER {worker_id}: ClicSanté — {profile_name}")
    print(f"{'='*60}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            viewport=profile.get("viewport", {"width": 1280, "height": 720}),
            user_agent=profile.get("user_agent"),
            locale=profile.get("locale", "fr-CA"),
            timezone_id=profile.get("timezone", "America/Montreal")
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()
        
        try:
            # Step 1: Load ClicSanté
            print("\n📂 Step 1: Loading ClicSanté portal...")
            page.goto("https://portal3.clicsante.ca/", wait_until="networkidle", timeout=60000)
            human_delay(1000, 2000)
            log_page_state(page, "clicsante_home", worker_id)
            take_screenshot(page, "01_homepage", worker_id)
            
            # Step 2: Close popup
            print("\n📂 Step 2: Closing popup...")
            try: 
                page.locator("text=Sans frais").first.click(timeout=8000)
                print("   ✅ Popup closed")
            except: 
                print("   ℹ️ No popup found")
            human_delay(500, 1000)
            
            # Step 3: Enter postal code
            print(f"\n📂 Step 3: Entering postal code '{postal_code}'...")
            inputs = page.locator("input[type='text']").all()
            print(f"   📥 Found {len(inputs)} text inputs")
            if inputs:
                inputs[0].click()
                inputs[0].fill("")
                inputs[0].type(postal_code, delay=random.randint(100, 200))
                print(f"   ✅ Postal code entered")
            take_screenshot(page, "02_postal_entered", worker_id)
            
            human_delay(1000, 2000)
            
            # Step 4: Search
            print("\n📂 Step 4: Clicking search...")
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
            human_delay(3000, 5000)
            log_page_state(page, "clicsante_results", worker_id)
            take_screenshot(page, "03_search_results", worker_id)
            
            # Step 5: Find appointment links
            print("\n📂 Step 5: Looking for appointment links...")
            links = page.locator("a[href*='take-appt']").all()
            print(f"   🔗 Found {len(links)} appointment links")
            
            for i, link in enumerate(links[:5]):
                if KillSwitch.is_active(): break
                try:
                    href = link.get_attribute("href") or ""
                    m = re.search(r'/(\d+)/take-appt', href)
                    if not m: continue
                    
                    url = f"https://clients3.clicsante.ca/{m.group(1)}/take-appt"
                    name = link.evaluate("""el => { 
                        let p = el.closest('li, article, div'); 
                        if (!p) return ''; 
                        for (let h of p.querySelectorAll('h1,h2,h3,h4,strong,b')) { 
                            let t = h.innerText?.trim(); 
                            if (t && t.length > 5 && t.length < 200) return t; 
                        } 
                        return ''; 
                    }""") or f"Clinique #{m.group(1)}"
                    
                    print(f"\n   🏥 Clinic {i+1}: {name[:80]}")
                    print(f"   🔗 URL: {url}")
                    
                    found.append({"name": name[:150], "platform": "clicsante", "url": url})
                    KillSwitch.add_slot(found[-1])
                except Exception as e:
                    print(f"   ⚠️ Error extracting link: {e}")
                    
        except Exception as e:
            print(f"   ❌ ClicSanté error: {e}")
            take_screenshot(page, "99_error", worker_id)
        finally:
            print(f"\n🔵 Worker {worker_id} done — found {len(found)} slots")
            browser.close()
    return found

# ================================================================
# 10. BONJOUR SANTÉ (with debug)
# ================================================================

def scrape_bonjoursante(profile: dict, clinic: dict, user: dict, worker_id: int) -> list:
    found = []
    clinic_name = clinic.get("name", "Unknown")
    stagger = profile.get("delay", 0)
    if stagger > 0: time.sleep(stagger)
    if KillSwitch.is_active(): return []
    
    print(f"\n{'='*60}")
    print(f"🟢 WORKER {worker_id}: Bonjour Santé — {clinic_name}")
    print(f"{'='*60}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            viewport=profile.get("viewport", {"width": 1280, "height": 720}),
            user_agent=profile.get("user_agent"),
            locale=profile.get("locale", "fr-CA"),
            timezone_id=profile.get("timezone", "America/Montreal")
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()
        
        try:
            # Step 1: Load clinic page
            print(f"\n📂 Step 1: Loading {clinic['website']}...")
            page.goto(clinic["website"], wait_until="domcontentloaded", timeout=30000)
            human_delay(1000, 2000)
            log_page_state(page, "bonjoursante_clinic", worker_id)
            take_screenshot(page, "01_clinic_page", worker_id)
            
            # Step 2: Click "Prendre rendez-vous"
            print("\n📂 Step 2: Looking for 'Prendre rendez-vous' button...")
            rdv_btn = page.get_by_text("Prendre rendez-vous", exact=False).first
            if rdv_btn.count() > 0:
                rdv_btn.click()
                print("   ✅ Clicked 'Prendre rendez-vous'")
                human_delay(2000, 4000)
                log_page_state(page, "bonjoursante_after_rdv", worker_id)
                take_screenshot(page, "02_after_rdv_click", worker_id)
            else:
                print("   ❌ No 'Prendre rendez-vous' button found")
                return []
            
            # Step 3: Select service type
            print("\n📂 Step 3: Selecting service type...")
            for s in ["Urgence mineure", "Medecin de famille ou urgence mineure", "Consultation rapide", "Suivi"]:
                btn = page.get_by_text(s, exact=False).first
                if btn.count() > 0:
                    btn.click()
                    print(f"   ✅ Selected: {s}")
                    human_delay(2000, 4000)
                    break
            take_screenshot(page, "03_service_selected", worker_id)
            
            # Step 4: Fill form
            print("\n📂 Step 4: Filling patient form...")
            fill_field(page, ["input[name*='ramq']", "input[placeholder*='ABCD']"], user["ramq"])
            fill_field(page, ["input[name*='seq']", "input[placeholder*='00']"], user["ramq_seq"])
            fill_field(page, ["input[name*='firstName']", "input[name*='prenom']", "input[placeholder*='Prenom']"], user["first_name"])
            fill_field(page, ["input[name*='lastName']", "input[name*='nom']", "input[placeholder*='Nom']"], user["last_name"])
            human_delay(500, 1000)
            
            cb = page.locator("input[type='checkbox']").first
            if cb.count() > 0:
                try: cb.check(); print("   ✅ Checked consent box")
                except: pass
            
            click_button(page, ["Continuer", "Suivant", "Next"])
            human_delay(2000, 4000)
            log_page_state(page, "bonjoursante_form", worker_id)
            take_screenshot(page, "04_form_filled", worker_id)
            
            # Step 5: Search for slots
            print(f"\n📂 Step 5: Searching {SEARCH_DAYS} days for slots...")
            fill_field(page, ["input[name*='postal']", "input[placeholder*='code postal']"], user["postal_code"])
            
            for d in range(SEARCH_DAYS):
                if KillSwitch.is_active(): break
                td = datetime.now() + timedelta(days=d)
                ds = td.strftime("%Y-%m-%d")
                print(f"\n   📅 Day {d+1}/{SEARCH_DAYS}: {ds}")
                
                di = page.locator("input[type='date']").first
                if di.count() > 0:
                    try: di.fill(ds); human_delay(500, 1000)
                    except: pass
                
                if click_button(page, ["Rechercher", "Search", "Chercher"]):
                    human_delay(2000, 4000)
                
                take_screenshot(page, f"05_day{d+1}_{ds}", worker_id)
                log_page_state(page, f"day_{d+1}", worker_id)
                
                if check_page_for_slots(page):
                    found.append({
                        "clinic_name": clinic_name, 
                        "platform": "bonjour_sante", 
                        "date": td.strftime("%d/%m/%Y"), 
                        "url": page.url, 
                        "city": clinic.get("city", "")
                    })
                    KillSwitch.add_slot(found[-1])
                    return found
                    
        except Exception as e:
            print(f"   ❌ Bonjour Santé error: {e}")
            take_screenshot(page, "99_error", worker_id)
        finally:
            print(f"\n🟢 Worker {worker_id} done — found {len(found)} slots")
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
        print(f"📍 User location: {user_coords}")
    except Exception as e:
        print(f"⚠️ Geocoding failed: {e}")
    
    nearby = []
    for clinic in CLINICS_DATABASE:
        if user_coords:
            d = haversine(user_coords[0], user_coords[1], clinic["lat"], clinic["lng"])
            if d <= RADIUS_KM: 
                clinic["distance"] = round(d, 1)
                nearby.append(clinic)
        else: 
            nearby.append(clinic)
    
    print(f"\n📍 {len(nearby)} clinics within {RADIUS_KM}km")
    for c in nearby:
        print(f"   🏥 {c['name'][:50]} — {c.get('distance', '?')}km — {c['platform']}")
    
    print(f"\n🚀 Launching {MAX_WORKERS} browsers...")
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = []
        profiles = BROWSER_PROFILES[:MAX_WORKERS]
        
        # Worker 0: ClicSanté
        futures.append(executor.submit(scrape_clicsante, profiles[0], user, 0))
        
        # Workers 1-4: Clinics
        idx = 0
        for i, profile in enumerate(profiles[1:], 1):
            if idx < len(nearby):
                clinic = nearby[idx]
                idx += 1
                if clinic["platform"] == "bonjour_sante":
                    futures.append(executor.submit(scrape_bonjoursante, profile, clinic, user, i))
                elif clinic["platform"] == "telus_sante":
                    futures.append(executor.submit(scrape_bonjoursante, profile, clinic, user, i))  # Using same scraper for now
        
        for future in as_completed(futures):
            if KillSwitch.is_active(): 
                print("\n🛑 Kill switch active — cancelling remaining futures")
                break
            try: 
                result = future.result(timeout=600)
                all_slots.extend(result)
            except Exception as e:
                print(f"   ⚠️ Future failed: {e}")
    
    return all_slots

# ================================================================
# 12. MAIN
# ================================================================

def main():
    print("╔══════════════════════════════════════════════╗")
    print("║   MYVITA UNIFIED SCRAPER — DEBUG MODE        ║")
    print(f"║   {MAX_WORKERS} browsers | {SEARCH_DAYS}-day | Headless: {HEADLESS}   ║")
    print(f"║   Screenshots: {DEBUG_DIR}")

    print("╚══════════════════════════════════════════════╝")
    
    user = get_user_data()
    print(f"\n📋 User: {user['first_name']} {user['last_name']}")
    print(f"📋 Postal Code: {user['postal_code']}")
    print(f"📋 RAMQ: {user['ramq'][:4]}****")
    
    request_id = get_or_create_request(user)
    
    if request_id and is_request_cancelled(request_id):
        print("🛑 Request cancelled — stopping")
        return
    
    KillSwitch.reset()
    start = time.time()
    slots = search_all_platforms(user)
    elapsed = time.time() - start
    
    if request_id:
        save_results(request_id, slots, user)
    
    print(f"\n{'='*60}")
    print(f"📊 FINAL RESULTS — {elapsed:.0f} seconds")
    print(f"{'='*60}")
    
    if slots:
        print(f"\n🎉 FOUND {len(slots)} SLOTS:")
        for i, s in enumerate(slots):
            print(f"   {i+1}. {s.get('clinic_name', s.get('name', '?'))}")
            print(f"      Platform: {s.get('platform', '?')}")
            print(f"      URL: {s.get('url', '?')[:100]}")
    else:
        print(f"\n😴 No slots found")
    
    print(f"\n📸 Screenshots saved to: {DEBUG_DIR}")
    print(f"📸 Files: {len(os.listdir(DEBUG_DIR))} files")
    print("\n✅ Done")

if __name__ == "__main__":
    main()
