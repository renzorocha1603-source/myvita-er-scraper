from playwright.sync_api import sync_playwright, TimeoutError
import time
import random
import os
import json
import re
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, messaging, firestore

# ══════════════════════════════════════════════════════════════
# 1. CONFIGURATION
# ══════════════════════════════════════════════════════════════

POSTAL_CODES = {
    # Montreal
    "H1Y": "montreal_east", "H1A": "montreal_east", "H1B": "montreal_east",
    "H1C": "montreal_east", "H1H": "montreal_north", "H1J": "montreal_north",
    "H2X": "montreal_central", "H3A": "montreal_central", "H3B": "montreal_central",
    "H4L": "montreal_north", "H4M": "montreal_north", "H1Z": "montreal_north",
    "H2E": "montreal_north", "H2G": "montreal_north", "H2H": "montreal_north",
    "H3N": "montreal_north", "H3L": "montreal_north",
    # Quebec City
    "G1R": "quebec_central", "G1S": "quebec_central", "G1V": "quebec_ste_foy",
    "G1K": "quebec_central", "G1L": "quebec_central",
    # Gatineau
    "J8Y": "gatineau_hull", "J8Z": "gatineau_aylmer", "J8X": "gatineau_hull",
    # Sherbrooke
    "J1H": "sherbrooke", "J1K": "sherbrooke", "J1L": "sherbrooke",
    # Laval
    "H7T": "laval", "H7V": "laval", "H7W": "laval", "H7X": "laval",
    # Longueuil
    "J4K": "longueuil", "J4L": "longueuil", "J4M": "longueuil",
    # Trois-Rivières
    "G8Z": "trois_rivieres", "G9A": "trois_rivieres",
    # Saguenay
    "G7H": "saguenay", "G7X": "saguenay",
}

# Google Maps Proxy (same as Flutter app uses)
GOOGLE_MAPS_PROXY = "https://us-central1-myvita-app-c5ecd.cloudfunctions.net/googleMapsProxy"

# Search radius tiers (km)
RADIUS_TIERS = [15, 30]

# Cooldown between full search cycles (minutes)
COOLDOWN_MINUTES = 45

# Max days ahead to search for appointments
MAX_DAYS_AHEAD = 10

# Cardinal directions with lat/lng offsets
# 1 degree lat ≈ 111km, 1 degree lng ≈ 82km at Montreal latitude
CARDINAL_DIRECTIONS = {
    "N":  {"offset": (0.135, 0),      "label": "North"},
    "S":  {"offset": (-0.135, 0),     "label": "South"},
    "E":  {"offset": (0, 0.183),      "label": "East"},
    "W":  {"offset": (0, -0.183),     "label": "West"},
    "NE": {"offset": (0.095, 0.129),  "label": "Northeast"},
    "NW": {"offset": (0.095, -0.129), "label": "Northwest"},
    "SE": {"offset": (-0.095, 0.129), "label": "Southeast"},
    "SW": {"offset": (-0.095, -0.129),"label": "Southwest"},
}

# ══════════════════════════════════════════════════════════════
# 2. KILL SWITCH — Global flag shared across all handlers
# ══════════════════════════════════════════════════════════════

class KillSwitch:
    """Shared kill switch — any handler can set it, all check it before every action"""
    _active = False
    _found_appointment = None

    @classmethod
    def activate(cls, details: dict):
        cls._active = True
        cls._found_appointment = details
        print("\n🛑 ══════════════════════════════════════════")
        print(f"🛑 KILL SWITCH ACTIVATED")
        print(f"🛑 Clinic: {details.get('clinic_name', 'Unknown')}")
        print(f"🛑 Platform: {details.get('platform', 'Unknown')}")
        print(f"🛑 Booking URL: {details.get('booking_url', 'N/A')}")
        print(f"🛑 ══════════════════════════════════════════\n")

    @classmethod
    def is_active(cls) -> bool:
        return cls._active

    @classmethod
    def reset(cls):
        cls._active = False
        cls._found_appointment = None
        print("🔄 Kill switch reset — ready for new search")


# ══════════════════════════════════════════════════════════════
# 3. FIREBASE SETUP
# ══════════════════════════════════════════════════════════════

db = None

def init_firebase():
    global db
    try:
        creds_json = os.getenv("FIREBASE_CREDENTIALS")
        if creds_json:
            cred_dict = json.loads(creds_json)
            cred = credentials.Certificate(cred_dict)
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred, {'projectId': 'myvita-app-c5ecd'})
            db = firestore.client()
            print("✅ Firebase & Firestore initialized (via GitHub Secret)")
        elif os.path.exists("firebase-credentials.json"):
            cred = credentials.Certificate("firebase-credentials.json")
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred, {'projectId': 'myvita-app-c5ecd'})
            db = firestore.client()
            print("✅ Firebase & Firestore initialized (via local file)")
        else:
            print("⚠️ No Firebase credentials found — running without Firestore")
    except Exception as e:
        print(f"⚠️ Firebase Init Error: {e}")

init_firebase()


# ══════════════════════════════════════════════════════════════
# 4. USER DATA
# ══════════════════════════════════════════════════════════════

def get_user_data():
    """Fetch user data from environment variables (production: from Firestore)"""
    return {
        "first_name": os.getenv("USER_FIRST_NAME", "Jean"),
        "last_name": os.getenv("USER_LAST_NAME", "Tremblay"),
        "ramq": os.getenv("USER_RAMQ", "TREJ6501011234"),
        "ramq_seq": os.getenv("USER_RAMQ_SEQ", "01"),
        "birth_year": os.getenv("USER_BIRTH_YEAR", "1965"),
        "sex": os.getenv("USER_SEX", "M"),
        "email": os.getenv("USER_EMAIL", "user@example.com"),
        "phone": os.getenv("USER_PHONE", "5145551234"),
        "postal_code": os.getenv("POSTAL_CODE", "H1Y3H1"),
        "language": os.getenv("USER_LANGUAGE", "fr"),
    }


# ══════════════════════════════════════════════════════════════
# 5. UTILITY FUNCTIONS
# ══════════════════════════════════════════════════════════════

def human_delay(min_ms=500, max_ms=1500):
    """Random delay to simulate human behavior"""
    time.sleep(random.uniform(min_ms, max_ms) / 1000)

def get_zone(postal_code: str) -> str:
    """Get zone name from postal code FSA"""
    fsa = postal_code[:3].upper()
    return POSTAL_CODES.get(fsa, f"zone_{fsa}")

def get_user_token():
    """Get most recent FCM token from Firestore"""
    if db is None:
        return None
    try:
        users_ref = db.collection('users')\
            .order_by('fcmTokenUpdated', direction='DESCENDING')\
            .limit(1)
        docs = users_ref.stream()
        for doc in docs:
            data = doc.to_dict()
            token = data.get('fcmToken')
            if token:
                print(f"📱 FCM token found in Firestore")
                return token
        print("⚠️ No FCM token found in Firestore")
        return None
    except Exception as e:
        print(f"❌ Error reading FCM token: {e}")
        return None


# ══════════════════════════════════════════════════════════════
# 6. NOTIFICATION & DATA SAVING
# ══════════════════════════════════════════════════════════════

def save_availability(clinic_name: str, postal_code: str, platform: str,
                      has_slots: bool, booking_url: str, slot_details: str):
    """Save availability check result to Firestore"""
    if db is None:
        return
    zone = get_zone(postal_code)
    now = datetime.now()
    data = {
        "platform": platform,
        "postal_code": postal_code,
        "clinic_name": clinic_name,
        "zone": zone,
        "slots_found": has_slots,
        "booking_url": booking_url,
        "slot_details": slot_details,
        "last_checked": now,
    }
    try:
        doc_id = f"{platform}_{zone}_{clinic_name.replace(' ', '_')[:60]}"
        db.collection("availability").document(doc_id).set(data)
        db.collection("availability").document(doc_id)\
            .collection("history").add({
                "slots_found": has_slots,
                "checked_at": now,
            })
        print(f"🔥 Firestore Updated: {doc_id}")
    except Exception as e:
        print(f"❌ Firestore Error: {e}")

def send_notification(clinic_name: str, postal_code: str, platform: str, booking_url: str):
    """Send FCM push notification for found appointment"""
    token = get_user_token()
    if not token:
        print("⚠️ No FCM token — notification skipped")
        return
    try:
        message = messaging.Message(
            notification=messaging.Notification(
                title="🎉 Rendez-vous trouvé!",
                body=f"{clinic_name} près de {postal_code}. Touchez pour réserver."
            ),
            data={
                "url": booking_url,
                "platform": platform,
                "clinic": clinic_name,
                "postal": postal_code,
            },
            token=token,
        )
        messaging.send(message)
        print("✅ FCM Notification Sent")
    except Exception as e:
        print(f"❌ FCM Error: {e}")

def save_clinic_cache(postal_code: str, clinics: list):
    """Cache discovered clinics in Firestore (48h TTL)"""
    if db is None:
        return
    try:
        db.collection("clinic_cache").document(postal_code[:3]).set({
            "clinics": clinics,
            "cached_at": datetime.now(),
            "ttl_hours": 48,
        })
        print(f"📦 Clinics cached for postal prefix: {postal_code[:3]}")
    except Exception as e:
        print(f"⚠️ Cache save error: {e}")

def get_clinic_cache(postal_code: str) -> list:
    """Get cached clinics if less than 48h old"""
    if db is None:
        return []
    try:
        cache_doc = db.collection("clinic_cache").document(postal_code[:3]).get()
        if cache_doc.exists:
            cache_data = cache_doc.to_dict()
            cache_time = cache_data.get("cached_at")
            if cache_time:
                age_hours = (datetime.now() - cache_time.replace(tzinfo=None)).total_seconds() / 3600
                if age_hours < 48:
                    print(f"✅ Using cached clinics ({age_hours:.1f}h old)")
                    return cache_data.get("clinics", [])
    except Exception as e:
        print(f"⚠️ Cache read error: {e}")
    return []


# ══════════════════════════════════════════════════════════════
# 7. BROWSER SETUP
# ══════════════════════════════════════════════════════════════

def launch_stealth_browser(p, headless=True):
    """Launch Playwright browser with stealth settings"""
    browser = p.chromium.launch(
        headless=headless,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
        ]
    )
    context = browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"
    )
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return browser, context


# ══════════════════════════════════════════════════════════════
# 8. CLINIC DISCOVERY — Google Maps Places API (via proxy)
# ══════════════════════════════════════════════════════════════

def geocode_postal_code(postal_code: str) -> dict:
    """Convert postal code to lat/lng using existing Google Maps proxy"""
    import requests as req
    try:
        response = req.post(
            GOOGLE_MAPS_PROXY,
            json={
                "endpoint": "geocode/json",
                "params": {
                    "address": f"{postal_code}, Quebec, Canada",
                    "region": "ca",
                }
            },
            timeout=15
        )
        data = response.json()
        results = data.get("results", [])
        if results:
            location = results[0]["geometry"]["location"]
            return {"lat": location["lat"], "lng": location["lng"]}
    except Exception as e:
        print(f"⚠️ Geocode error: {e}")
    return None


def get_cardinal_coordinates(base_lat: float, base_lng: float,
                              direction: str, radius_km: int) -> dict:
    """Calculate coordinates in a cardinal direction at given radius"""
    # Scale offset by radius (base offsets are for ~15km)
    scale = radius_km / 15.0
    offsets = CARDINAL_DIRECTIONS.get(direction, {}).get("offset", (0, 0))
    return {
        "lat": base_lat + (offsets[0] * scale),
        "lng": base_lng + (offsets[1] * scale),
    }


def discover_clinics_near(postal_code: str, radius_km: int = 15) -> list:
    """
    Use Google Maps Places API (via existing proxy) to find free public clinics.
    Gemi's recommendation: Places API for verified data, not scraping.
    Returns list of {"name", "place_id", "address", "website", "phone", "rating"}
    Cached in Firestore for 48 hours.
    """
    print(f"\n🔍 Discovering clinics near {postal_code} ({radius_km}km radius)...")

    # Check Firestore cache first
    cached = get_clinic_cache(postal_code)
    if cached:
        return cached

    # Geocode postal code
    coords = geocode_postal_code(postal_code)
    if not coords:
        print("❌ Could not geocode postal code")
        return []

    lat, lng = coords["lat"], coords["lng"]
    all_clinics = []

    # Search terms for free public clinics (no private, no specialists)
    search_terms = [
        "GMF clinique médicale sans rendez-vous",
        "CLSC centre santé",
        "clinique médicale publique",
        "centre médical sans rendez-vous",
    ]

    for term in search_terms:
        if KillSwitch.is_active():
            break

        print(f"   Searching: '{term}'...")

        try:
            import requests as req
            response = req.post(
                GOOGLE_MAPS_PROXY,
                json={
                    "endpoint": "place/nearbysearch/json",
                    "params": {
                        "location": f"{lat},{lng}",
                        "radius": radius_km * 1000,  # Convert km to meters
                        "keyword": term,
                        "type": "doctor|health",
                        "language": "fr",
                    }
                },
                timeout=15
            )
            data = response.json()
            results = data.get("results", [])
            print(f"      Found {len(results)} raw results")

            for place in results:
                if KillSwitch.is_active():
                    break

                name = place.get("name", "")
                name_lower = name.lower()

                # ── FILTER: Exclude non-clinic businesses ──
                skip_keywords = [
                    "dentiste", "dentist", "dentaire", "orthodontiste",
                    "pharmacie", "pharmacy", "jean coutu", "pharmaprix",
                    "uniprix", "familiprix", "brunet", "proxim",
                    "physio", "physiothérapie", "chiro", "chiropractique",
                    "optométriste", "opticien", "optique", "lunetterie",
                    "vétérinaire", "veterinary", "animal",
                    "psychologue", "psychiatrist", "psychiatre",
                    "chirurgie esthétique", "cosmétique", "laser", "épilation",
                    "podiatre", "podiatrist", "pédicure",
                    "acupuncture", "ostéopathe", "naturopathe", "massothérapie",
                    "radiologie", "imagerie médicale", "échographie",
                    "laboratoire", "prise de sang", "analyse",
                    "soins à domicile", "home care",
                    "résidence", "chslD", "centre d'hébergement",
                    "spa", "bien-être", "yoga",
                ]
                if any(kw in name_lower for kw in skip_keywords):
                    continue

                # ── FILTER: Must be clearly a free public medical clinic ──
                clinic_keywords = [
                    "clinique", "clinic", "gmf", "clsc",
                    "médical", "medical", "centre de santé",
                    "médecin", "doctor", "santé", "health",
                    "hôpital", "hospital",
                ]
                if not any(kw in name_lower for kw in clinic_keywords):
                    continue

                # Skip duplicates by place_id
                place_id = place.get("place_id")
                if any(c.get("place_id") == place_id for c in all_clinics):
                    continue

                # Get detailed info (website, phone)
                website = None
                phone = None
                try:
                    details_response = req.post(
                        GOOGLE_MAPS_PROXY,
                        json={
                            "endpoint": "place/details/json",
                            "params": {
                                "place_id": place_id,
                                "fields": "website,formatted_phone_number,opening_hours,rating,user_ratings_total",
                                "language": "fr",
                            }
                        },
                        timeout=10
                    )
                    details_data = details_response.json()
                    details = details_data.get("result", {})
                    website = details.get("website")
                    phone = details.get("formatted_phone_number")
                except:
                    pass

                clinic = {
                    "name": name,
                    "place_id": place_id,
                    "address": place.get("vicinity", ""),
                    "rating": place.get("rating", 0),
                    "total_ratings": place.get("user_ratings_total", 0),
                    "website": website,
                    "phone": phone,
                }

                all_clinics.append(clinic)
                print(f"   ✅ {name}" + (f" | 🌐 {website}" if website else ""))

        except Exception as e:
            print(f"      ⚠️ Search error for '{term}': {e}")
            continue

    # Remove duplicates by name similarity
    unique_clinics = []
    seen_names = set()
    for clinic in all_clinics:
        name_key = clinic["name"].lower().strip()[:30]
        if name_key not in seen_names:
            seen_names.add(name_key)
            unique_clinics.append(clinic)

    print(f"\n✅ Discovered {len(unique_clinics)} unique free clinics")
    
    # Cache results
    if unique_clinics:
        save_clinic_cache(postal_code, unique_clinics)

    return unique_clinics


# ══════════════════════════════════════════════════════════════
# 9. PLATFORM DETECTION — Visit clinic website, find booking link
# ══════════════════════════════════════════════════════════════

# Gemi's weighted priority: direct platform URLs first, then text search
BOOKING_LINK_SELECTORS = [
    # ── TIER 1: Direct platform URLs (highest priority) ──
    "a[href*='pomelo.health']",
    "a[href*='bonjour-sante.ca']",
    "a[href*='clicsante.ca']",
    "a[href*='rvsq.gouv.qc.ca']",
    
    # ── TIER 2: Booking path patterns ──
    "a[href*='rendez-vous']",
    "a[href*='rdv']",
    "a[href*='booking']",
    "a[href*='appointment']",
    "a[href*='reservation']",
    
    # ── TIER 3: Text-based buttons/links ──
    "a:has-text('Prendre rendez-vous')",
    "a:has-text('Prendre RDV')",
    "a:has-text('Rendez-vous en ligne')",
    "a:has-text('Book appointment')",
    "a:has-text('Book online')",
    "button:has-text('Prendre rendez-vous')",
    "button:has-text('Rendez-vous en ligne')",
    "button:has-text('Book appointment')",
    
    # ── TIER 4: Contact page fallback (Gemi's suggestion) ──
    "a:has-text('Nous joindre')",
    "a:has-text('Contact')",
    "a:has-text('Contactez-nous')",
]


def detect_platform_from_url(url: str) -> str:
    """
    Detect booking platform from URL.
    ONLY returns pomelo or bonjour_sante (free tier).
    ClicSanté/RVSQ is skipped.
    """
    url_lower = url.lower()
    
    if "pomelo.health" in url_lower or "telus" in url_lower:
        return "pomelo"
    elif "bonjour-sante.ca" in url_lower:
        return "bonjour_sante"
    elif "clicsante.ca" in url_lower or "rvsq.gouv.qc.ca" in url_lower:
        return "clicsante_skip"  # Skipped for free tier
    else:
        return "unknown"


def find_booking_link(page, clinic_name: str) -> dict:
    """
    Find and click the booking link on a clinic website.
    Returns {"platform": str, "booking_url": str}
    
    Gemi's approach: weighted priority — try direct platform URLs first,
    then text patterns, then contact page fallback.
    """
    print(f"   🔗 Searching booking link on {clinic_name}...")
    
    for i, selector in enumerate(BOOKING_LINK_SELECTORS):
        if KillSwitch.is_active():
            return {"platform": "kill_switch", "booking_url": ""}
        
        try:
            element = page.locator(selector).first
            if element.count() == 0 or not element.is_visible():
                continue
            
            href = element.get_attribute("href") or ""
            text = element.inner_text()[:50] if element.count() > 0 else ""
            print(f"      Trying: {selector[:60]}... → href={href[:80]}")
            
            # Gemi's safety check: if href already contains a platform URL, use it directly
            if any(p in href.lower() for p in ["pomelo.health", "bonjour-sante.ca", "clicsante.ca", "rvsq"]):
                platform = detect_platform_from_url(href)
                print(f"      🎯 Direct platform link found: {platform}")
                return {"platform": platform, "booking_url": href}
            
            # Otherwise click and check redirect
            element.click(timeout=5000)
            human_delay(2000, 4000)
            
            # Check if a new tab opened
            if len(page.context.pages) > 1:
                new_page = page.context.pages[-1]
                new_page.bring_to_front()
                human_delay(1500, 2500)
                platform = detect_platform_from_url(new_page.url)
                booking_url = new_page.url
                new_page.close()
                return {"platform": platform, "booking_url": booking_url}
            
            # Check current page URL
            platform = detect_platform_from_url(page.url)
            if platform != "unknown":
                return {"platform": platform, "booking_url": page.url}
            
            # Go back if we navigated away
            try:
                page.go_back()
                human_delay(500, 1000)
            except:
                pass
                
        except Exception as e:
            continue
    
    # ── Gemi's Fallback: Check "Nous joindre" / Contact page ──
    print(f"      ⚠️ No booking link found. Trying Contact page...")
    try:
        contact_link = page.locator("a:has-text('Nous joindre'), a:has-text('Contact')").first
        if contact_link.count() > 0:
            contact_link.click(timeout=5000)
            human_delay(2000, 3000)
            
            # Re-scan the contact page for booking links
            for selector in BOOKING_LINK_SELECTORS[:6]:  # Tier 1 & 2 only
                try:
                    element = page.locator(selector).first
                    if element.count() > 0:
                        href = element.get_attribute("href") or ""
                        platform = detect_platform_from_url(href)
                        if platform != "unknown":
                            print(f"      🎯 Found on contact page: {platform}")
                            return {"platform": platform, "booking_url": href}
                except:
                    continue
    except:
        pass
    
    print(f"      ❌ No booking link found")
    return {"platform": "unknown", "booking_url": ""}


def visit_clinic_and_detect_platform(clinic: dict) -> dict:
    """
    Visit a clinic's website, find booking button, detect platform.
    Returns clinic dict with platform and booking_url added.
    """
    headless = os.getenv("HEADLESS", "true").lower() != "false"
    
    if KillSwitch.is_active():
        return {**clinic, "platform": "kill_switch", "booking_url": ""}
    
    website = clinic.get("website")
    if not website:
        print(f"   ⛔ {clinic['name']}: No website — skipping")
        return {**clinic, "platform": "no_website", "booking_url": ""}
    
    print(f"\n   🏥 Visiting: {clinic['name']}")
    print(f"      🌐 {website}")
    
    with sync_playwright() as p:
        browser, context = launch_stealth_browser(p, headless=headless)
        page = context.new_page()
        
        try:
            page.goto(website, wait_until="domcontentloaded", timeout=30000)
            human_delay(2000, 3000)
            
            # Dismiss popups/cookies
            try:
                page.keyboard.press("Escape")
                human_delay(300, 500)
            except:
                pass
            
            result = find_booking_link(page, clinic["name"])
            
            return {
                **clinic,
                "platform": result["platform"],
                "booking_url": result["booking_url"],
            }
            
        except Exception as e:
            print(f"      🚨 Error visiting {clinic['name']}: {e}")
            return {**clinic, "platform": "error", "booking_url": ""}
        finally:
            browser.close()


# ══════════════════════════════════════════════════════════════
# 10. POMELO BY TELUS — 4-STEP FORM HANDLER
# ══════════════════════════════════════════════════════════════

def fill_pomelo_page1_identification(page, user: dict):
    """Page 1: First Name, Last Name, RAMQ, Sequence, Birth Year, Sex"""
    print("      📝 Page 1 — Identification...")
    if KillSwitch.is_active(): return False

    try:
        # First Name
        page.locator("input[name='firstName'], input[aria-label*='Prénom'], #firstName").first.fill(user["first_name"])
        human_delay(300, 600)
    except: pass

    try:
        # Last Name
        page.locator("input[name='lastName'], input[aria-label*='Nom de famille'], #lastName").first.fill(user["last_name"])
        human_delay(300, 600)
    except: pass

    try:
        # RAMQ Number
        page.locator("input[name*='ramq'], input[name*='assurance'], input[aria-label*='RAMQ']").first.fill(user["ramq"])
        human_delay(300, 600)
    except: pass

    try:
        # Sequence Number
        page.locator("input[name*='sequence'], input[name*='seq']").first.fill(user["ramq_seq"])
        human_delay(300, 600)
    except: pass

    try:
        # Year of Birth
        page.locator("input[name*='birth'], input[name*='year'], input[name*='annee']").first.fill(user["birth_year"])
        human_delay(300, 600)
    except: pass

    try:
        # Sex
        if user["sex"].upper() == "M":
            page.locator("input[value='M'], input[value='male'], label:has-text('Masculin')").first.click()
        else:
            page.locator("input[value='F'], input[value='female'], label:has-text('Féminin')").first.click()
        human_delay(500, 800)
    except:
        # Try clicking the radio/label directly
        try:
            if user["sex"].upper() == "M":
                page.get_by_text(re.compile(r"Masculin|Homme", re.I)).first.click()
            else:
                page.get_by_text(re.compile(r"Féminin|Femme", re.I)).first.click()
            human_delay(500, 800)
        except:
            pass

    # Click Continue/Next
    try:
        page.get_by_role("button", name=re.compile(r"Continuer|Suivant|Next|Submit", re.I)).first.click()
        human_delay(2000, 3000)
    except:
        pass
    return True


def fill_pomelo_page2_contact(page, user: dict):
    """Page 2: Email, Phone, Language"""
    print("      📧 Page 2 — Contact...")
    if KillSwitch.is_active(): return False

    try:
        page.locator("input[type='email'], input[name*='email'], input[name*='courriel']").first.fill(user["email"])
        human_delay(300, 600)
    except: pass

    try:
        page.locator("input[type='tel'], input[name*='phone'], input[name*='tel']").first.fill(user["phone"])
        human_delay(300, 600)
    except: pass

    try:
        page.get_by_role("button", name=re.compile(r"Continuer|Suivant|Next", re.I)).first.click()
        human_delay(2000, 3000)
    except: pass
    return True


def fill_pomelo_page3_consent(page):
    """Page 3: Privacy policy consent"""
    print("      ✅ Page 3 — Consent...")
    if KillSwitch.is_active(): return False

    try:
        # Check consent checkbox
        page.locator("input[type='checkbox']").first.check()
        human_delay(500, 1000)
    except:
        try:
            page.get_by_label(re.compile(r"J'accepte|I accept|Consentement", re.I)).first.check()
            human_delay(500, 1000)
        except:
            pass

    try:
        page.get_by_role("button", name=re.compile(r"Continuer|Suivant|Next", re.I)).first.click()
        human_delay(2000, 3000)
    except: pass
    return True


def fill_pomelo_page4_search(page, postal_code: str):
    """Page 4: Postal Code, Date, Reason"""
    print(f"      🔍 Page 4 — Search (postal: {postal_code})...")
    if KillSwitch.is_active(): return False

    # Target date: today to MAX_DAYS_AHEAD
    target_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        postal_input = page.locator(
            "input[name*='postal'], input[name*='code'], "
            "input[placeholder*='A1A'], input[aria-label*='postal']"
        ).first
        postal_input.click()
        postal_input.fill(postal_code)
        human_delay(500, 1000)
    except: pass

    try:
        date_input = page.locator("input[type='date'], input[name*='date']").first
        date_input.fill(target_date)
        human_delay(300, 500)
    except: pass

    try:
        page.get_by_role("button", name=re.compile(r"Rechercher|Search|Chercher", re.I)).first.click()
        human_delay(3000, 5000)
    except: pass
    return True


def verify_pomelo_calendar(page, clinic_name: str) -> tuple:
    """Check Pomelo calendar for available slots. Returns (has_slots, details)."""
    print("      📅 Checking Pomelo calendar...")
    if KillSwitch.is_active(): return False, "Kill switch"
    
    human_delay(2000, 3000)
    body_text = page.inner_text("body").lower()

    no_slot_phrases = [
        "aucune disponibilité", "no availability",
        "aucun rendez-vous", "no appointments",
        "complet", "full", "désolé", "sorry",
    ]
    for phrase in no_slot_phrases:
        if phrase in body_text:
            return False, phrase

    positive_phrases = ["disponible", "available", "sélectionner", "select"]
    has_positive = any(p in body_text for p in positive_phrases)

    try:
        clickable = page.locator(
            "[class*='available'], [class*='disponible'], "
            "td:not([class*='disabled']):not([class*='complet']), "
            "button[class*='day']:not([disabled])"
        )
        count = clickable.count()
        if count > 0 and has_positive:
            return True, f"{count} clickable dates"
    except:
        pass

    return has_positive, "positive indicators" if has_positive else "no clear slots"


def scrape_pomelo_clinic(clinic: dict, user: dict) -> dict:
    """Complete Pomelo 4-step scrape. Returns {"found": bool, "details": str, "booking_url": str}"""
    headless = os.getenv("HEADLESS", "true").lower() != "false"
    booking_url = clinic.get("booking_url", "")
    
    print(f"\n   🔴 POMELO HANDLER: {clinic['name']}")
    
    if KillSwitch.is_active():
        return {"found": False, "details": "Kill switch active", "booking_url": ""}

    with sync_playwright() as p:
        browser, context = launch_stealth_browser(p, headless=headless)
        page = context.new_page()

        try:
            page.goto(booking_url, wait_until="domcontentloaded", timeout=60000)
            human_delay(2000, 3000)

            if KillSwitch.is_active():
                return {"found": False, "details": "Kill switch", "booking_url": ""}

            fill_pomelo_page1_identification(page, user)
            if KillSwitch.is_active():
                return {"found": False, "details": "Kill switch", "booking_url": ""}

            fill_pomelo_page2_contact(page, user)
            if KillSwitch.is_active():
                return {"found": False, "details": "Kill switch", "booking_url": ""}

            fill_pomelo_page3_consent(page)
            if KillSwitch.is_active():
                return {"found": False, "details": "Kill switch", "booking_url": ""}

            fill_pomelo_page4_search(page, user["postal_code"])
            if KillSwitch.is_active():
                return {"found": False, "details": "Kill switch", "booking_url": ""}

            has_slots, details = verify_pomelo_calendar(page, clinic["name"])

            if has_slots:
                print(f"      🎉 SLOTS FOUND! ({details})")
                return {"found": True, "details": details, "booking_url": page.url}
            else:
                print(f"      ❌ No slots ({details})")
                return {"found": False, "details": details, "booking_url": ""}

        except Exception as e:
            print(f"      🚨 Pomelo error: {e}")
            return {"found": False, "details": str(e), "booking_url": ""}
        finally:
            browser.close()


# ══════════════════════════════════════════════════════════════
# 11. BONJOUR SANTÉ — 3-STEP FORM HANDLER
# ══════════════════════════════════════════════════════════════

def fill_bonjoursante_page1(page, user: dict):
    """Page 1: RAMQ + Postal Code + Distance"""
    print("      📝 Bonjour Santé — Page 1 (RAMQ + Postal)...")
    if KillSwitch.is_active(): return False

    try:
        page.locator("input[name*='ramq'], input[name*='assurance']").first.fill(user["ramq"])
        human_delay(400, 800)
    except: pass

    try:
        page.locator("input[name*='postal'], input[name*='code']").first.fill(user["postal_code"])
        human_delay(400, 800)
    except: pass

    try:
        page.get_by_role("button", name=re.compile(r"Continuer|Suivant|Next|Rechercher", re.I)).first.click()
        human_delay(2000, 3000)
    except: pass
    return True


def fill_bonjoursante_page2(page, user: dict):
    """Page 2: First Name + Last Name + Sequence + Consent"""
    print("      📝 Bonjour Santé — Page 2 (Patient Info)...")
    if KillSwitch.is_active(): return False

    try:
        page.locator("input[name*='firstName'], input[name*='prenom']").first.fill(user["first_name"])
        human_delay(300, 600)
    except: pass

    try:
        page.locator("input[name*='lastName'], input[name*='nom']").first.fill(user["last_name"])
        human_delay(300, 600)
    except: pass

    try:
        page.locator("input[name*='sequence'], input[name*='seq']").first.fill(user["ramq_seq"])
        human_delay(300, 600)
    except: pass

    try:
        page.locator("input[type='checkbox']").first.check()
        human_delay(500, 1000)
    except: pass

    try:
        page.get_by_role("button", name=re.compile(r"Continuer|Suivant|Next", re.I)).first.click()
        human_delay(2000, 3000)
    except: pass
    return True


def verify_bonjoursante_results(page, clinic_name: str) -> tuple:
    """Check Bonjour Santé results page for available appointments."""
    print("      📅 Checking Bonjour Santé results...")
    if KillSwitch.is_active(): return False, "Kill switch"
    
    human_delay(2000, 3000)
    body_text = page.inner_text("body").lower()

    no_slot_phrases = [
        "aucune disponibilité", "no availability",
        "aucun rendez-vous", "no appointments",
        "complet", "full", "désolé", "sorry",
    ]
    for phrase in no_slot_phrases:
        if phrase in body_text:
            return False, phrase

    positive_phrases = ["disponible", "available", "réserver", "book", "choisir"]
    has_positive = any(p in body_text for p in positive_phrases)

    return has_positive, "positive indicators" if has_positive else "no clear slots"


def scrape_bonjoursante_clinic(clinic: dict, user: dict) -> dict:
    """Complete Bonjour Santé 3-step scrape."""
    headless = os.getenv("HEADLESS", "true").lower() != "false"
    booking_url = clinic.get("booking_url", "")
    
    print(f"\n   🟠 BONJOUR SANTÉ HANDLER: {clinic['name']}")
    
    if KillSwitch.is_active():
        return {"found": False, "details": "Kill switch active", "booking_url": ""}

    with sync_playwright() as p:
        browser, context = launch_stealth_browser(p, headless=headless)
        page = context.new_page()

        try:
            page.goto(booking_url, wait_until="domcontentloaded", timeout=60000)
            human_delay(2000, 3000)

            if KillSwitch.is_active():
                return {"found": False, "details": "Kill switch", "booking_url": ""}

            fill_bonjoursante_page1(page, user)
            if KillSwitch.is_active():
                return {"found": False, "details": "Kill switch", "booking_url": ""}

            fill_bonjoursante_page2(page, user)
            if KillSwitch.is_active():
                return {"found": False, "details": "Kill switch", "booking_url": ""}

            has_slots, details = verify_bonjoursante_results(page, clinic["name"])

            if has_slots:
                print(f"      🎉 SLOTS FOUND! ({details})")
                return {"found": True, "details": details, "booking_url": page.url}
            else:
                print(f"      ❌ No slots ({details})")
                return {"found": False, "details": details, "booking_url": ""}

        except Exception as e:
            print(f"      🚨 Bonjour Santé error: {e}")
            return {"found": False, "details": str(e), "booking_url": ""}
        finally:
            browser.close()


# ══════════════════════════════════════════════════════════════
# 12. UNIFIED CLINIC SCRAPER DISPATCHER
# ══════════════════════════════════════════════════════════════

def route_and_scrape_clinic(clinic: dict, user: dict) -> dict:
    """
    Route clinic to correct handler based on detected platform.
    ONLY handles pomelo and bonjour_sante (free tier).
    ClicSanté/RVSQ is skipped.
    """
    platform = clinic.get("platform", "unknown")
    
    if platform == "kill_switch":
        return {"found": False, "details": "Kill switch", "booking_url": ""}
    
    if platform == "pomelo":
        return scrape_pomelo_clinic(clinic, user)
    elif platform == "bonjour_sante":
        return scrape_bonjoursante_clinic(clinic, user)
    elif platform == "clicsante_skip":
        print(f"   ⏭️ {clinic['name']}: ClicSanté/RVSQ — skipped (free tier)")
        return {"found": False, "details": "clicsante_skipped", "booking_url": ""}
    elif platform in ("unknown", "error", "no_website"):
        print(f"   ⏭️ {clinic['name']}: {platform} — skipped")
        return {"found": False, "details": platform, "booking_url": ""}
    else:
        print(f"   ⏭️ {clinic['name']}: Unknown platform '{platform}' — skipped")
        return {"found": False, "details": f"unknown_platform_{platform}", "booking_url": ""}


# ══════════════════════════════════════════════════════════════
# 13. MAIN SEARCH ORCHESTRATOR
# ══════════════════════════════════════════════════════════════

def search_clinics_in_zone(user: dict, radius_km: int) -> bool:
    """
    Discover clinics near user's postal code, detect platforms,
    and scrape each one. Returns True if appointment found.
    """
    postal = user["postal_code"]
    
    # Step 1: Discover clinics
    clinics = discover_clinics_near(postal, radius_km)
    
    if not clinics:
        print(f"   No clinics found in {radius_km}km radius")
        return False
    
    # Step 2: Visit each clinic, detect platform
    detected_clinics = []
    for clinic in clinics:
        if KillSwitch.is_active():
            return True
        
        result = visit_clinic_and_detect_platform(clinic)
        detected_clinics.append(result)
    
    # Step 3: Route to handlers and scrape
    for clinic in detected_clinics:
        if KillSwitch.is_active():
            return True
        
        result = route_and_scrape_clinic(clinic, user)
        
        if result["found"]:
            KillSwitch.activate({
                "clinic_name": clinic["name"],
                "platform": clinic.get("platform"),
                "booking_url": result["booking_url"],
                "details": result["details"],
            })
            send_notification(
                clinic["name"],
                postal,
                clinic.get("platform", "unknown"),
                result["booking_url"]
            )
            save_availability(
                clinic["name"], postal,
                clinic.get("platform", "unknown"),
                True, result["booking_url"], result["details"]
            )
            return True
        else:
            save_availability(
                clinic["name"], postal,
                clinic.get("platform", "unknown"),
                False, "", result["details"]
            )
    
    return False


def run_full_search(user_postal: str = None):
    """
    Full tiered search: 15km → 30km → cooldown → repeat.
    Searches ALL cardinal points within each tier.
    Only targets Pomelo and Bonjour Santé (free tier clinics).
    """
    if user_postal is None:
        user_postal = os.getenv("POSTAL_CODE", "H1Y3H1")
    
    user = get_user_data()
    user["postal_code"] = user_postal
    
    KillSwitch.reset()
    max_date = (datetime.now() + timedelta(days=MAX_DAYS_AHEAD)).strftime("%Y-%m-%d")
    
    print(f"\n{'='*60}")
    print(f"🚀 MYVITA CLINIC SCRAPER — Free Tier")
    print(f"   Postal Code: {user_postal}")
    print(f"   Max Date: {max_date} (under {MAX_DAYS_AHEAD} days)")
    print(f"   Tiers: {RADIUS_TIERS} km")
    print(f"   Cooldown: {COOLDOWN_MINUTES} min between cycles")
    print(f"   Platforms: Pomelo by Telus + Bonjour Santé ONLY")
    print(f"   ClicSanté/RVSQ: SKIPPED")
    print(f"{'='*60}\n")
    
    cycle = 0
    
    while not KillSwitch.is_active():
        cycle += 1
        print(f"\n🔄 CYCLE {cycle} — {'='*40}")
        
        for radius in RADIUS_TIERS:
            if KillSwitch.is_active():
                break
            print(f"\n🔵 TIER: {radius}km radius")
            
            found = search_clinics_in_zone(user, radius)
            
            if found:
                print(f"\n{'='*60}")
                print(f"🎉 APPOINTMENT FOUND!")
                print(f"   Clinic: {KillSwitch._found_appointment.get('clinic_name')}")
                print(f"   Platform: {KillSwitch._found_appointment.get('platform')}")
                print(f"   URL: {KillSwitch._found_appointment.get('booking_url')}")
                print(f"{'='*60}")
                return KillSwitch._found_appointment
        
        if not KillSwitch.is_active():
            print(f"\n😴 No appointments found in cycle {cycle}.")
            print(f"   Cooling down for {COOLDOWN_MINUTES} minutes...")
            print(f"   Next cycle at: {(datetime.now() + timedelta(minutes=COOLDOWN_MINUTES)).strftime('%H:%M:%S')}")
            time.sleep(COOLDOWN_MINUTES * 60)
    
    return None


# ══════════════════════════════════════════════════════════════
# 14. MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("╔══════════════════════════════════════════════════════╗")
    print("║        MYVITA CLINIC APPOINTMENT SCRAPER            ║")
    print("║        Free Tier — Pomelo + Bonjour Santé           ║")
    print("╚══════════════════════════════════════════════════════╝")
    
    postal = os.getenv("POSTAL_CODE", "H1Y3H1")
    run_full_search(user_postal=postal)
