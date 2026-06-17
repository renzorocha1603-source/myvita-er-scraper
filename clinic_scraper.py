#!/usr/bin/env python3
"""
MYVITA HYBRID SCRAPER v4 — All 3 platforms, deeplink extraction
- ClicSanté: medical consultation search → extract booking links
- Bonjour Santé: click service button → capture redirect URL
- TELUS Santé (Pomelo): click service button → capture redirect URL
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
from firebase_admin import credentials, messaging, firestore

# ================================================================
# 1. FIREBASE SETUP
# ================================================================

FIREBASE_CREDENTIALS_JSON = os.getenv("FIREBASE_CREDENTIALS", "")
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
        print(f"⚠️ Firebase: {e}")

# ================================================================
# 2. CONFIGURATION
# ================================================================

HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
MAX_WORKERS = 5
RADIUS_KM = 50
REQUEST_COLLECTION = "concierge_requests"
DEBUG_DIR = "/tmp/myvita_debug"
os.makedirs(DEBUG_DIR, exist_ok=True)

# ================================================================
# 3. BROWSER PROFILES
# ================================================================

BROWSER_PROFILES = [
    {"name": "W1-Chrome", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36", "viewport": {"width": 1366, "height": 768}, "locale": "fr-CA", "timezone": "America/Montreal", "delay": 0},
    {"name": "W2-Safari", "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.2 Safari/605.1.15", "viewport": {"width": 1440, "height": 900}, "locale": "fr-CA", "timezone": "America/Montreal", "delay": 15},
    {"name": "W3-Firefox", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0", "viewport": {"width": 1536, "height": 864}, "locale": "en-CA", "timezone": "America/Toronto", "delay": 30},
    {"name": "W4-Chrome", "user_agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36", "viewport": {"width": 1280, "height": 720}, "locale": "fr-CA", "timezone": "America/Montreal", "delay": 45},
    {"name": "W5-iPhone", "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 Version/17.1 Mobile/15E148 Safari/604.1", "viewport": {"width": 390, "height": 844}, "locale": "fr-CA", "timezone": "America/Montreal", "is_mobile": True, "delay": 60},
]

# ================================================================
# 4. ALL CLINICS — Bonjour Santé + TELUS Santé (Pomelo)
# ================================================================

CLINICS = [
    # === BONJOUR SANTÉ ===
    {"name": "GMF Angus", "lat": 45.5401, "lng": -73.5658, "platform": "bonjour_sante", "url": "https://bonjour-sante.ca/uno/clinique/angus", "city": "Montreal"},
    {"name": "Médico-Centre Mont-Royal", "lat": 45.5163, "lng": -73.5786, "platform": "bonjour_sante", "url": "https://bonjour-sante.ca/uno/clinique/montroyal", "city": "Montreal"},
    {"name": "CM Mieux-Être Levasseur", "lat": 45.5841, "lng": -73.6412, "platform": "bonjour_sante", "url": "https://bonjour-sante.ca/uno/clinique/levasseur", "city": "Montreal"},
    {"name": "Urgence Saint-Laurent", "lat": 45.5118, "lng": -73.6802, "platform": "bonjour_sante", "url": "https://bonjour-sante.ca/uno/clinique/cusl", "city": "Montreal"},
    {"name": "GMF St-Denis", "lat": 45.5264, "lng": -73.5932, "platform": "bonjour_sante", "url": "https://bonjour-sante.ca/uno/clinique/stdenis", "city": "Montreal"},
    {"name": "CM Mieux-Être St-Léonard", "lat": 45.5892, "lng": -73.6014, "platform": "bonjour_sante", "url": "https://bonjour-sante.ca/uno/clinique/mieuxetre", "city": "Montreal"},
    {"name": "Medi-Centre Chomedey", "lat": 45.5451, "lng": -73.7483, "platform": "bonjour_sante", "url": "https://bonjour-sante.ca/uno/clinique/medicentrechomedey", "city": "Laval"},
    {"name": "Carrefour Médical Laval", "lat": 45.5684, "lng": -73.7431, "platform": "bonjour_sante", "url": "https://bonjour-sante.ca/uno/clinique/lecarrefour", "city": "Laval"},
    {"name": "UnionMD Longueuil", "lat": 45.5252, "lng": -73.5135, "platform": "bonjour_sante", "url": "https://bonjour-sante.ca/uno/clinique/unionmdlongueuil", "city": "Longueuil"},
    {"name": "GMF Terrebonne", "lat": 45.6982, "lng": -73.6391, "platform": "bonjour_sante", "url": "https://bonjour-sante.ca/uno/clinique/cmterrebonne", "city": "Terrebonne"},
    
    # === TELUS SANTÉ (POMELO) ===
    {"name": "Clinique Sainte-Dorothée", "lat": 45.5312, "lng": -73.8115, "platform": "telus_sante", "url": "https://pomelo.health/cliniquemedicalesaintedorothee", "city": "Laval"},
    {"name": "GMF-U Charles-Le Moyne", "lat": 45.5184, "lng": -73.4831, "platform": "telus_sante", "url": "https://qc.pomelo.health/gmfucharleslemoyne", "city": "Longueuil"},
    {"name": "Centre Médical Laval", "lat": 45.5521, "lng": -73.7314, "platform": "telus_sante", "url": "https://qc.pomelo.health/centremedicallaval", "city": "Laval"},
    {"name": "Clinique de la Gare", "lat": 45.5582, "lng": -73.9015, "platform": "telus_sante", "url": "https://qc.pomelo.health/cliniquemedicaledelagare", "city": "Saint-Eustache"},
    {"name": "GMF des Seigneurs", "lat": 45.7025, "lng": -73.6514, "platform": "telus_sante", "url": "https://qc.pomelo.health/gmfdesseigneurs", "city": "Terrebonne"},
]

# ================================================================
# 5. HELPERS
# ================================================================

def ss(page, name, wid=0):
    try:
        ts = datetime.now().strftime("%H%M%S")
        page.screenshot(path=f"{DEBUG_DIR}/w{wid}_{ts}_{name}.png", full_page=True)
    except: pass

def hdelay(min_ms=300, max_ms=1000):
    time.sleep(random.uniform(min_ms, max_ms)/1000)

def haversine(lat1, lng1, lat2, lng2):
    R = 6371
    dlat = math.radians(lat2-lat1)
    dlng = math.radians(lng2-lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlng/2)**2
    return R*2*math.atan2(math.sqrt(a), math.sqrt(1-a))

def get_user():
    return {
        "first_name": os.getenv("USER_FIRST_NAME", "Jean"),
        "last_name": os.getenv("USER_LAST_NAME", "Tremblay"),
        "ramq": os.getenv("USER_RAMQ", "TREJ70010101"),
        "ramq_seq": os.getenv("USER_RAMQ_SEQ", "01"),
        "postal_code": os.getenv("POSTAL_CODE", "H1Y3H1"),
        "email": os.getenv("USER_EMAIL", "jean@test.com"),
        "phone": os.getenv("USER_PHONE", "5145550101"),
    }

# ================================================================
# 6. KILL SWITCH
# ================================================================

class KS:
    _active=False; _slots=[]; _lock=threading.Lock()
    @classmethod
    def add(cls, d):
        with cls._lock:
            # Avoid duplicates
            urls = [s.get('url','') for s in cls._slots]
            if d.get('url','') in urls: return
            cls._slots.append(d)
            print(f"\n   🎯 SLOT #{len(cls._slots)}: {d.get('name','?')}")
            print(f"   🔗 {d.get('url','')[:120]}")
            if len(cls._slots)>=5: cls._active=True; print("   🛑 KILL SWITCH — 5 slots!")
    @classmethod
    def on(cls): 
        with cls._lock: return cls._active
    @classmethod
    def get(cls):
        with cls._lock: return list(cls._slots)
    @classmethod
    def reset(cls):
        with cls._lock: cls._active=False; cls._slots=[]

# ================================================================
# 7. FIRESTORE
# ================================================================

def save_to_firestore(postal_code, slots):
    if db is None: return
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
        print(f"✅ Saved {len(slots)} slots to Firestore")
    except Exception as e:
        print(f"❌ Firestore: {e}")

# ================================================================
# 8. CLICSANTÉ — Medical consultation search
# ================================================================

def scrape_clicsante(profile, user, wid):
    found = []
    pc = user["postal_code"]
    if profile.get("delay",0) > 0: time.sleep(profile["delay"])
    if KS.on(): return []
    
    print(f"\n🔵 W{wid}: ClicSanté Medical")
    
    with sync_playwright() as p:
        b = p.chromium.launch(headless=HEADLESS)
        ctx = b.new_context(viewport=profile.get("viewport",{"width":1280,"height":720}), user_agent=profile["user_agent"], locale=profile.get("locale","fr-CA"), timezone_id=profile.get("timezone","America/Montreal"))
        ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        pg = ctx.new_page()
        
        try:
            hdelay(1000,3000)
            pg.goto("https://portal3.clicsante.ca/", wait_until="networkidle", timeout=60000)
            hdelay(1500,3000)
            try: pg.locator("text=Sans frais").first.click(timeout=8000)
            except: pass
            hdelay(1000,2000)
            
            # Postal code
            ins = pg.locator("input[type='text']").all()
            if ins: ins[0].click(); ins[0].fill(""); ins[0].type(pc, delay=random.randint(100,200))
            hdelay(1000,2000)
            ss(pg, "clicsante_postal", wid)
            
            # Try selecting medical service
            for kw in ["Médecine familiale","Consultation médicale","Médecin","Soins de santé","Urgence mineure"]:
                try:
                    btn = pg.locator(f"text={kw}").first
                    if btn.count()>0 and btn.is_visible():
                        btn.click()
                        print(f"   ✅ Service: {kw}")
                        hdelay(2000,4000)
                        break
                except: pass
            
            # Search
            try: pg.get_by_role("button",name="Search").first.click(timeout=8000)
            except:
                try: pg.get_by_role("button",name="Rechercher").first.click(timeout=5000)
                except: pg.keyboard.press("Enter")
            hdelay(4000,8000)
            ss(pg, "clicsante_results", wid)
            
            # Extract booking links
            links = pg.locator("a[href*='take-appt']").all()
            print(f"   📎 {len(links)} links")
            
            for lk in links[:5]:
                if KS.on(): break
                try:
                    href = lk.get_attribute("href") or ""
                    m = re.search(r'/(\d+)/take-appt', href)
                    if not m: continue
                    url = f"https://clients3.clicsante.ca/{m.group(1)}/take-appt"
                    name = lk.evaluate("""el=>{let p=el.closest('li,article,div[class*="result"],div[class*="card"]');if(!p)p=el.closest('div');if(!p)return'';let hs=p.querySelectorAll('h1,h2,h3,h4,strong,b,[class*="name"]');for(let h of hs){let t=h.innerText?.trim();if(t&&t.length>5&&t.length<200)return t}return''}""") or f"ClicSanté #{m.group(1)}"
                    place = {"name":name[:150],"platform":"clicsante","url":url,"city":"Various"}
                    found.append(place)
                    KS.add(place)
                    print(f"   📍 {name[:80]}")
                except: pass
                
        except Exception as e:
            print(f"   ❌ {e}")
            traceback.print_exc()
        finally:
            b.close()
    
    print(f"🔵 W{wid}: {len(found)} found")
    return found

# ================================================================
# 9. BONJOUR SANTÉ + TELUS SANTÉ — Deeplink extraction
# ================================================================

def scrape_clinic_deeplink(profile, clinic, user, wid):
    """
    Universal deeplink extractor for Bonjour Santé + TELUS Santé
    1. Load clinic page
    2. Click service button
    3. Capture the redirect URL
    """
    found = []
    cname = clinic.get("name", "Unknown")
    platform = clinic.get("platform", "unknown")
    if profile.get("delay", 0) > 0: time.sleep(profile["delay"])
    if KS.on(): return []
    
    icon = "🟢" if platform == "bonjour_sante" else "🟣"
    print(f"\n{icon} W{wid}: {cname} ({platform})")
    
    with sync_playwright() as p:
        b = p.chromium.launch(headless=HEADLESS)
        ctx = b.new_context(viewport=profile.get("viewport",{"width":1280,"height":720}), user_agent=profile["user_agent"], locale=profile.get("locale","fr-CA"), timezone_id=profile.get("timezone","America/Montreal"))
        ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        pg = ctx.new_page()
        
        try:
            # Load clinic page
            print(f"   📂 {clinic['url']}")
            pg.goto(clinic["url"], wait_until="domcontentloaded", timeout=30000)
            hdelay(1500, 2500)
            ss(pg, "01_clinic", wid)
            
            # ★ CLICK SERVICE BUTTON — capture the redirect URL
            service_clicked = False
            redirect_url = None
            
            # Service buttons to try (French + English)
            service_texts = [
                # Bonjour Santé buttons
                "Médecin de famille ou urgence mineure",
                "Urgence mineure",
                "Consultation rapide", 
                "Suivi avec mon médecin",
                "Dans ma clinique",
                "Consultation sans rendez-vous",
                "Sans rendez-vous",
                "Prendre rendez-vous",
                # TELUS Santé buttons
                "Prendre un rendez-vous",
                "Réserver",
                "Consultation",
                "Voir les disponibilités",
            ]
            
            for txt in service_texts:
                if service_clicked: break
                try:
                    btn = pg.locator(f"text={txt}").first
                    if btn.count() > 0 and btn.is_visible():
                        old_url = pg.url
                        print(f"   👆 Clicking: '{txt}'")
                        btn.click()
                        hdelay(3000, 5000)
                        
                        new_url = pg.url
                        if new_url != old_url and new_url != clinic["url"]:
                            redirect_url = new_url
                            print(f"   🔗 REDIRECT: {new_url[:120]}")
                        else:
                            # Check for new windows/tabs
                            pages = ctx.pages
                            if len(pages) > 1:
                                redirect_url = pages[-1].url
                                print(f"   🔗 NEW TAB: {redirect_url[:120]}")
                        
                        service_clicked = True
                        ss(pg, "02_after_click", wid)
                        break
                except: pass
            
            # If we got a redirect URL, save it
            if redirect_url:
                place = {
                    "name": cname,
                    "platform": platform,
                    "url": redirect_url,
                    "city": clinic.get("city", ""),
                }
                found.append(place)
                KS.add(place)
            else:
                print(f"   ⚠️ No redirect — trying fallback button scan")
                # Fallback: click any visible button with relevant text
                all_btns = pg.locator("button, a").all()
                for btn in all_btns:
                    if service_clicked: break
                    try:
                        txt = (btn.inner_text() or "").lower()
                        if any(kw in txt for kw in ["rendez-vous","médecin","urgence","consultation","suivi","réserver","book"]):
                            old_url = pg.url
                            btn.click()
                            hdelay(3000, 5000)
                            new_url = pg.url
                            if new_url != old_url:
                                redirect_url = new_url
                                print(f"   🔗 Fallback redirect: {redirect_url[:120]}")
                                service_clicked = True
                                place = {
                                    "name": cname,
                                    "platform": platform,
                                    "url": redirect_url,
                                    "city": clinic.get("city", ""),
                                }
                                found.append(place)
                                KS.add(place)
                                break
                    except: pass
                    
        except Exception as e:
            print(f"   ❌ {e}")
            ss(pg, "99_error", wid)
        finally:
            b.close()
    
    print(f"{icon} W{wid}: {len(found)} found")
    return found

# ================================================================
# 10. MAIN SEARCH
# ================================================================

def search_all(user):
    all_slots = []
    pc = user["postal_code"]
    
    # Get user coordinates for distance sorting
    user_coords = None
    try:
        import requests
        r = requests.post(
            "https://us-central1-myvita-app-c5ecd.cloudfunctions.net/googleMapsProxy",
            json={"endpoint": "geocode/json", "params": {"address": f"{pc}, Quebec, Canada", "region": "ca"}},
            timeout=15
        )
        loc = r.json()["results"][0]["geometry"]["location"]
        user_coords = (loc["lat"], loc["lng"])
        print(f"📍 User: {user_coords}")
    except:
        print("⚠️ Geocoding failed")
    
    # Sort clinics by distance
    nearby = []
    for clinic in CLINICS:
        if user_coords:
            d = haversine(user_coords[0], user_coords[1], clinic["lat"], clinic["lng"])
            if d <= RADIUS_KM:
                clinic["distance"] = round(d, 1)
                nearby.append(clinic)
        else:
            nearby.append(clinic)
    
    nearby.sort(key=lambda x: x.get("distance", 999))
    
    print(f"\n📍 {len(nearby)} clinics within {RADIUS_KM}km:")
    for c in nearby:
        print(f"   🏥 {c['name'][:40]} — {c.get('distance','?')}km — {c['platform']}")
    
    print(f"\n🚀 {MAX_WORKERS} parallel browsers...")
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = []
        profiles = BROWSER_PROFILES[:MAX_WORKERS]
        
        # Worker 0: ClicSanté
        futures.append(executor.submit(scrape_clicsante, profiles[0], user, 0))
        
        # Workers 1-4: Clinics (Bonjour Santé + TELUS)
        for i, profile in enumerate(profiles[1:], 1):
            if i-1 < len(nearby):
                clinic = nearby[i-1]
                futures.append(executor.submit(scrape_clinic_deeplink, profile, clinic, user, i))
        
        for future in as_completed(futures):
            if KS.on(): break
            try:
                result = future.result(timeout=120)
                all_slots.extend(result)
            except Exception as e:
                print(f"   ⚠️ Future failed: {e}")
    
    return all_slots

# ================================================================
# 11. MAIN
# ================================================================

def main():
    print("╔══════════════════════════════════════╗")
    print("║   MYVITA HYBRID SCRAPER v4          ║")
    print("║   ClicSanté + Bonjour + TELUS       ║")
    print(f"║   {MAX_WORKERS} browsers | Headless: {HEADLESS}     ║")
    print("╚══════════════════════════════════════╝")
    
    user = get_user()
    print(f"\n📋 {user['first_name']} {user['last_name']} | {user['postal_code']}")
    
    KS.reset()
    start = time.time()
    slots = search_all(user)
    elapsed = time.time() - start
    
    save_to_firestore(user["postal_code"], slots)
    
    print(f"\n{'='*60}")
    print(f"📊 RESULTS — {elapsed:.0f}s")
    print(f"{'='*60}")
    
    if slots:
        print(f"\n🎉 {len(slots)} SLOTS:")
        for i, s in enumerate(slots):
            print(f"   {i+1}. {s['name']}")
            print(f"      Platform: {s['platform']}")
            print(f"      URL: {s['url'][:100]}")
    else:
        print(f"\n😴 No slots found")
    
    print(f"\n📸 Screenshots: {DEBUG_DIR}")
    print("✅ Done")

if __name__ == "__main__":
    main()
