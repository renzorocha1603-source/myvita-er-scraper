from playwright.sync_api import sync_playwright, TimeoutError
import time
import random
import os
import json
import re
import math
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, messaging, firestore

# ══════════════════════════════════════════════════════════════
# 1. CONFIGURATION
# ══════════════════════════════════════════════════════════════

POSTAL_CODES = {
    "H1Y": "montreal_east", "H1A": "montreal_east", "H1B": "montreal_east",
    "H1C": "montreal_east", "H1H": "montreal_north", "H1J": "montreal_north",
    "H2X": "montreal_central", "H3A": "montreal_central", "H3B": "montreal_central",
    "H4L": "montreal_north", "H4M": "montreal_north", "H1Z": "montreal_north",
    "H2E": "montreal_north", "H2G": "montreal_north", "H2H": "montreal_north",
    "H3N": "montreal_north", "H3L": "montreal_north",
    "G1R": "quebec_central", "G1S": "quebec_central", "G1V": "quebec_ste_foy",
    "G1K": "quebec_central", "G1L": "quebec_central",
    "J8Y": "gatineau_hull", "J8Z": "gatineau_aylmer", "J8X": "gatineau_hull",
    "J1H": "sherbrooke", "J1K": "sherbrooke", "J1L": "sherbrooke",
    "H7T": "laval", "H7V": "laval", "H7W": "laval", "H7X": "laval",
    "J4K": "longueuil", "J4L": "longueuil", "J4M": "longueuil",
    "G8Z": "trois_rivieres", "G9A": "trois_rivieres",
    "G7H": "saguenay", "G7X": "saguenay",
}

GOOGLE_MAPS_PROXY = "https://us-central1-myvita-app-c5ecd.cloudfunctions.net/googleMapsProxy"
RADIUS_TIERS = [15, 30, 50]
MAX_DAYS_AHEAD = 10

# ══════════════════════════════════════════════════════════════
# 2. KILL SWITCH
# ══════════════════════════════════════════════════════════════

class KillSwitch:
    _active = False
    _found_appointment = None

    @classmethod
    def activate(cls, details: dict):
        cls._active = True
        cls._found_appointment = details
        print(f"\n🛑 KILL SWITCH: {details.get('clinic_name')} | {details.get('platform')}")

    @classmethod
    def is_active(cls) -> bool:
        return cls._active

    @classmethod
    def reset(cls):
        cls._active = False
        cls._found_appointment = None
        print("🔄 Kill switch reset")


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
            print("✅ Firebase initialized (GitHub Secret)")
    except Exception as e:
        print(f"⚠️ Firebase Init Error: {e}")

init_firebase()


# ══════════════════════════════════════════════════════════════
# 4. USER DATA
# ══════════════════════════════════════════════════════════════

def get_user_data():
    return {
        "first_name": os.getenv("USER_FIRST_NAME", "Jean"),
        "last_name": os.getenv("USER_LAST_NAME", "Tremblay"),
        "ramq": os.getenv("USER_RAMQ", "TREJ6501011234"),
        "ramq_seq": os.getenv("USER_RAMQ_SEQ", "01"),
        "birth_date": os.getenv("USER_BIRTH_DATE", "1965-01-15"),
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
    time.sleep(random.uniform(min_ms, max_ms) / 1000)

def get_zone(postal_code: str) -> str:
    return POSTAL_CODES.get(postal_code[:3].upper(), f"zone_{postal_code[:3]}")

def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def geocode_postal_code(postal_code: str) -> dict:
    import requests as req
    try:
        response = req.post(GOOGLE_MAPS_PROXY, json={
            "endpoint": "geocode/json",
            "params": {"address": f"{postal_code}, Quebec, Canada", "region": "ca"}
        }, timeout=15)
        data = response.json()
        results = data.get("results", [])
        if results:
            loc = results[0]["geometry"]["location"]
            return {"lat": loc["lat"], "lng": loc["lng"]}
    except Exception as e:
        print(f"⚠️ Geocode error: {e}")
    return None

def get_user_token():
    if db is None: return None
    try:
        users_ref = db.collection('users').order_by('fcmTokenUpdated', direction='DESCENDING').limit(1)
        for doc in users_ref.stream():
            token = doc.to_dict().get('fcmToken')
            if token: return token
    except: pass
    return None


# ══════════════════════════════════════════════════════════════
# 6. NOTIFICATION & DATA SAVING
# ══════════════════════════════════════════════════════════════

def save_availability(clinic_name, postal_code, platform, has_slots, booking_url, slot_details):
    if db is None: return
    zone = get_zone(postal_code)
    now = datetime.now()
    doc_id = f"{platform}_{zone}_{clinic_name.replace(' ', '_')[:60]}"
    try:
        db.collection("availability").document(doc_id).set({
            "platform": platform, "postal_code": postal_code,
            "clinic_name": clinic_name, "zone": zone,
            "slots_found": has_slots, "booking_url": booking_url,
            "slot_details": slot_details, "last_checked": now,
        })
        print(f"🔥 Firestore: {doc_id}")
    except Exception as e:
        print(f"❌ Firestore Error: {e}")

def update_clinic_request(request_id, status, result=None):
    if db is None or not request_id:
        return
    try:
        update_data = {
            "status": status,
            "updated_at": firestore.SERVER_TIMESTAMP
        }
        if result:
            update_data["scraper_result"] = result
            if result.get("found") == True:
                update_data["result_summary"] = f"Found: {result.get('clinic_name', 'Unknown')} via {result.get('platform', 'Unknown')}"
            else:
                update_data["result_summary"] = "No appointments found"
        if status == "completed":
            update_data["completed_at"] = firestore.SERVER_TIMESTAMP

        db.collection("clinic_requests").document(request_id).update(update_data)
        print(f"📝 Updated clinic_requests/{request_id}: status={status}")
    except Exception as e:
        print(f"❌ Failed to update clinic request: {e}")

def send_notification(clinic_name, postal_code, platform, booking_url):
    token = get_user_token()
    if not token: return
    try:
        messaging.send(messaging.Message(
            notification=messaging.Notification(
                title="🎉 Rendez-vous trouvé!",
                body=f"{clinic_name} près de {postal_code}. Touchez pour réserver."
            ),
            data={
                "url": booking_url,
                "platform": platform,
                "clinic": clinic_name,
                "postal": postal_code,
                "click_action": "OPEN_BOOKING"
            },
            token=token,
        ))
        print("✅ Notification Sent (slot found)")
    except Exception as e:
        print(f"❌ FCM Error: {e}")

def send_no_slots_notification(postal_code, language):
    token = get_user_token()
    if not token: return
    try:
        title = "😴 Aucun rendez-vous trouvé" if language == "fr" else "😴 No appointments found"
        body = "Notre concierge humain peut vous aider. Ouvrez l'application pour continuer." if language == "fr" else "Our human concierge can help. Open the app to continue."
        messaging.send(messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body
            ),
            data={
                "click_action": "OPEN_CONCIERGE",
                "postal": postal_code
            },
            token=token,
        ))
        print("✅ No-slots notification sent")
    except Exception as e:
        print(f"❌ FCM Error: {e}")


# ══════════════════════════════════════════════════════════════
# 7. BROWSER SETUP
# ══════════════════════════════════════════════════════════════

def launch_stealth_browser(p, headless=True):
    browser = p.chromium.launch(headless=headless, args=[
        "--no-sandbox", "--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"
    ])
    context = browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
    )
    context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return browser, context


# ══════════════════════════════════════════════════════════════
# 8. CLINIC MAP WITH COORDINATES — 78 clinics
# ══════════════════════════════════════════════════════════════

CLINICS = [
    # Montreal East
    {"name": "GMF-R Cité Médicale Villeray", "lat": 45.5463, "lng": -73.6214, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous"},
    {"name": "Centre Médical Mieux-Être (succursale Levasseur)", "lat": 45.5841, "lng": -73.6412, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/levasseur"},
    {"name": "Clinique Médico-Centre Mont-Royal", "lat": 45.5163, "lng": -73.5786, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/montroyal"},
    {"name": "GMF Médi-Centre Chomedey", "lat": 45.5451, "lng": -73.7483, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/medicentrechomedey"},
    {"name": "Polyclinique du cœur-de-l'île GMF-R Jarry-Lajeunesse", "lat": 45.5442, "lng": -73.6256, "platform": "no_online_booking", "booking_url": ""},
    {"name": "CLSC du Plateau-Mont-Royal", "lat": 45.5195, "lng": -73.5781, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous"},
    {"name": "CLSC de Dorval-Lachine", "lat": 45.4385, "lng": -73.6841, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous"},
    {"name": "UnionMD - Clinique Médicale Privée Montreal", "lat": 45.5032, "lng": -73.5721, "platform": "other", "booking_url": ""},
    {"name": "CLSC de Saint-Henri (Montréal)", "lat": 45.4775, "lng": -73.5856, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous"},
    {"name": "CLSC Métro (Montréal)", "lat": 45.4931, "lng": -73.5802, "platform": "clicsante", "booking_url": "https://portal3.clicsante.ca/"},
    {"name": "CLSC de Hochelaga-Maisonneuve", "lat": 45.5422, "lng": -73.5397, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous"},
    {"name": "Centre D'Urgence Saint-Laurent (GMF)", "lat": 45.5118, "lng": -73.6802, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/cusl"},
    {"name": "GMF A-R Clinique médicale Angus (Montréal)", "lat": 45.5401, "lng": -73.5658, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/angus"},
    # Montreal Central
    {"name": "GMF Clinique Médicale St-Denis (Montréal)", "lat": 45.5264, "lng": -73.5932, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/stdenis"},
    # Anjou
    {"name": "GMF Centre Médical Mieux-Être (Succursale Anjou)", "lat": 45.6031, "lng": -73.5518, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous"},
    # Mieux-Être network
    {"name": "Centre Médical Mieux-Être - Lasalle", "lat": 45.4312, "lng": -73.6248, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/cmmelasalle"},
    {"name": "Centre Médical Mieux-Être - Henri-Bourassa", "lat": 45.6421, "lng": -73.6105, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/cmmehenribourassa"},
    {"name": "GMF A-R Centre médical Mieux-Être – St-Léonard", "lat": 45.5892, "lng": -73.6014, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/mieuxetre"},
    # Laval
    {"name": "GMF Le Carrefour Médical (Laval)", "lat": 45.5684, "lng": -73.7431, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/lecarrefour"},
    {"name": "GMF Clinique Médicale Sainte-Dorothée (Laval)", "lat": 45.5312, "lng": -73.8115, "platform": "pomelo", "booking_url": "https://pomelo.health/cliniquemedicalesaintedorothee"},
    {"name": "GMF Polyclinique Concorde (Laval)", "lat": 45.5615, "lng": -73.7082, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous"},
    {"name": "CLSC de Laval-des-Rapides", "lat": 45.5492, "lng": -73.7124, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous"},
    {"name": "Super-Clinique Polyclinique Médicale Fabreville (GMF)", "lat": 45.5925, "lng": -73.7912, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/fabreville"},
    {"name": "Clinique Médicale Saint-François (GMF)", "lat": 45.5781, "lng": -73.6542, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/stfrancois"},
    {"name": "CLSC Idola-Saint-Jean", "lat": 45.5652, "lng": -73.6931, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous"},
    {"name": "CLSC des Mille-Îles", "lat": 45.6315, "lng": -73.6212, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous"},
    {"name": "CLSC de l'Ouest-de-l'Île", "lat": 45.4523, "lng": -73.8321, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous"},
    {"name": "CLSC de Sainte-Rose", "lat": 45.6121, "lng": -73.7824, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous"},
    {"name": "CLSC du Ruisseau-Papineau", "lat": 45.5794, "lng": -73.7251, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous"},
    {"name": "GMF Centre Médical Laval", "lat": 45.5521, "lng": -73.7314, "platform": "pomelo", "booking_url": "https://qc.pomelo.health/centremedicallaval"},
    # Longueuil / Rive-Sud
    {"name": "Clinique médicale privée Longueuil - Rive-Sud - UnionMD", "lat": 45.5252, "lng": -73.5135, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/unionmdlongueuil"},
    {"name": "GMF-R Clinique Médicale Longueuil-Ouest", "lat": 45.5314, "lng": -73.5248, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/longueuilouest"},
    {"name": "CLSC de Longueuil-Ouest (Rive-Sud)", "lat": 45.5314, "lng": -73.5248, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous"},
    {"name": "GMF-U Charles-Le Moyne (Longueuil)", "lat": 45.5184, "lng": -73.4831, "platform": "pomelo", "booking_url": "https://qc.pomelo.health/gmfucharleslemoyne"},
    # Brossard
    {"name": "GMF Dix30 (Clinique d'urgence avec rendez-vous Dix30 Brossard)", "lat": 45.4428, "lng": -73.4412, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/gmfdix30"},
    {"name": "Clinique Sans Rendez-Vous Dix30 Brossard (GMF)", "lat": 45.4428, "lng": -73.4412, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/csansrendezvousdix30brossard"},
    {"name": "GMF Samuel-de-Champlain", "lat": 45.4682, "lng": -73.4715, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous"},
    {"name": "GMF Lapinière", "lat": 45.4561, "lng": -73.4623, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous"},
    # West Island
    {"name": "GMF Stillview (West Island - Pointe-Claire)", "lat": 45.4485, "lng": -73.8124, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous"},
    {"name": "GMF Clinique Médicale Brunswick (West Island - Pointe-Claire)", "lat": 45.4498, "lng": -73.8315, "platform": "pomelo", "booking_url": "https://pomelo.health/brunswickmedicalcenter"},
    {"name": "CLSC du Lac-Saint-Louis (West Island - Pointe-Claire)", "lat": 45.4392, "lng": -73.8184, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous"},
    {"name": "CLSC de Pierrefonds (West Island)", "lat": 45.4852, "lng": -73.8742, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous"},
    # North Shore
    {"name": "GMF des Seigneurs (Terrebonne)", "lat": 45.7025, "lng": -73.6514, "platform": "pomelo", "booking_url": "https://qc.pomelo.health/gmfdesseigneurs"},
    {"name": "GMF Clinique Médicale Terrebonne", "lat": 45.6982, "lng": -73.6391, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/cmterrebonne"},
    {"name": "GMF des Affluents (Repentigny)", "lat": 45.7485, "lng": -73.4421, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous"},
    {"name": "GMF-U du Sud de Lanaudière (Repentigny)", "lat": 45.7412, "lng": -73.4563, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous"},
    {"name": "GMF-U de Saint-Charles-Borromée (Lanaudière Joliette)", "lat": 46.0465, "lng": -73.4682, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous"},
    {"name": "GMF L'Assomption", "lat": 45.8312, "lng": -73.4215, "platform": "pomelo", "booking_url": "https://qc.pomelo.health/#/"},
    {"name": "Centre de Médecine Métabolique de Lanaudière (CMML)", "lat": 46.0242, "lng": -73.4356, "platform": "pomelo", "booking_url": "https://qc.pomelo.health/cmml/portal#/patient-triage"},
    # Saint-Jérôme
    {"name": "GMF-R Clinique Médicale Saint-Jérôme", "lat": 45.7785, "lng": -74.0042, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous"},
    {"name": "GMF du Grand Saint-Jérôme (Clinique Saint-Hippolyte)", "lat": 45.8321, "lng": -73.9915, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/santhippolyte"},
    # Rosemère
    {"name": "GMF Clinique Médicale Rosemère", "lat": 45.6382, "lng": -73.7915, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/rosemere"},
    # Vaudreuil
    {"name": "GMF-R Vaudreuil-Dorion (Super-Clinique)", "lat": 45.3982, "lng": -74.0321, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/vaudreuildorion"},
    # Saint-Eustache
    {"name": "GMF Clinique Médicale de la Gare (Saint-Eustache)", "lat": 45.5582, "lng": -73.9015, "platform": "pomelo", "booking_url": "https://qc.pomelo.health/cliniquemedicaledelagare"},
    # Saint-Jean
    {"name": "GMF Clinique Médicale Saint-Luc (Saint-Jean-sur-Richelieu)", "lat": 45.3512, "lng": -73.2842, "platform": "pomelo", "booking_url": "https://qc.pomelo.health/cliniquemedicalesaintluc"},
    # Laurentides
    {"name": "GMF Clinique Médicale Lorraine (Laurentides)", "lat": 45.6512, "lng": -73.7814, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/cmlorraine"},
]

ACTIVE_PLATFORMS = {"bonjour_sante", "pomelo"}


# ══════════════════════════════════════════════════════════════
# 9. DISTANCE-BASED CLINIC DISCOVERY
# ══════════════════════════════════════════════════════════════

def discover_clinics_near(postal_code: str, radius_km: int) -> list:
    print(f"\n🔍 Finding clinics within {radius_km}km of {postal_code}...")
    coords = geocode_postal_code(postal_code)
    if not coords:
        print("❌ Could not geocode postal code")
        return []
    user_lat, user_lng = coords["lat"], coords["lng"]
    nearby = []
    for clinic in CLINICS:
        if KillSwitch.is_active():
            break
        dist = haversine(user_lat, user_lng, clinic["lat"], clinic["lng"])
        if dist <= radius_km and clinic["platform"] in ACTIVE_PLATFORMS:
            nearby.append({
                "name": clinic["name"],
                "platform": clinic["platform"],
                "booking_url": clinic["booking_url"],
                "distance_km": round(dist, 1),
            })
    nearby.sort(key=lambda c: c["distance_km"])
    print(f"✅ Found {len(nearby)} active clinics within {radius_km}km")
    for c in nearby:
        print(f"   📍 {c['name']} — {c['distance_km']}km — {c['platform']}")
    return nearby


# ══════════════════════════════════════════════════════════════
# 10. POMELO HANDLER
# ══════════════════════════════════════════════════════════════

def fill_pomelo_page1_identification(page, user: dict):
    print("      📝 Page 1 — Identification...")
    if KillSwitch.is_active(): return False
    birth_year = user.get("birth_date", "1965-01-15").split("-")[0]
    try: page.locator("input[name='firstName'], #firstName").first.fill(user["first_name"]); human_delay(300, 600)
    except: pass
    try: page.locator("input[name='lastName'], #lastName").first.fill(user["last_name"]); human_delay(300, 600)
    except: pass
    try: page.locator("input[name*='ramq']").first.fill(user["ramq"]); human_delay(300, 600)
    except: pass
    try: page.locator("input[name*='seq']").first.fill(user["ramq_seq"]); human_delay(300, 600)
    except: pass
    try: page.locator("input[name*='birth'], input[name*='year']").first.fill(birth_year); human_delay(300, 600)
    except: pass
    try:
        if user["sex"].upper() == "M":
            page.locator("input[value='M'], label:has-text('Masculin')").first.click()
        else:
            page.locator("input[value='F'], label:has-text('Féminin')").first.click()
        human_delay(500, 800)
    except:
        try:
            page.get_by_text(re.compile(r"Masculin|Homme|Féminin|Femme", re.I)).first.click()
            human_delay(500, 800)
        except: pass
    try: page.get_by_role("button", name=re.compile(r"Continuer|Suivant|Next", re.I)).first.click(); human_delay(2000, 3000)
    except: pass
    return True

def fill_pomelo_page2_contact(page, user: dict):
    print("      📧 Page 2 — Contact...")
    if KillSwitch.is_active(): return False
    try: page.locator("input[type='email']").first.fill(user["email"]); human_delay(300, 600)
    except: pass
    try: page.locator("input[type='tel']").first.fill(user["phone"]); human_delay(300, 600)
    except: pass
    try: page.get_by_role("button", name=re.compile(r"Continuer|Suivant|Next", re.I)).first.click(); human_delay(2000, 3000)
    except: pass
    return True

def fill_pomelo_page3_consent(page):
    print("      ✅ Page 3 — Consent...")
    if KillSwitch.is_active(): return False
    try: page.locator("input[type='checkbox']").first.check(); human_delay(500, 1000)
    except: pass
    try: page.get_by_role("button", name=re.compile(r"Continuer|Suivant|Next", re.I)).first.click(); human_delay(2000, 3000)
    except: pass
    return True

def fill_pomelo_page4_search(page, postal_code: str):
    print(f"      🔍 Page 4 — Search (postal: {postal_code})...")
    if KillSwitch.is_active(): return False
    try:
        page.locator("input[name*='postal'], input[name*='code']").first.fill(postal_code)
        human_delay(500, 1000)
    except: pass
    try: page.get_by_role("button", name=re.compile(r"Rechercher|Search|Chercher", re.I)).first.click(); human_delay(3000, 5000)
    except: pass
    return True

def verify_pomelo_calendar(page, clinic_name: str) -> tuple:
    print("      📅 Checking Pomelo calendar...")
    if KillSwitch.is_active(): return False, "Kill switch", ""
    human_delay(2000, 3000)
    body_text = page.inner_text("body").lower()
    for phrase in ["aucune disponibilité", "no availability", "aucun rendez-vous", "complet", "full", "désolé"]:
        if phrase in body_text: return False, phrase, ""
    has_positive = any(p in body_text for p in ["disponible", "available", "sélectionner", "select"])
    return has_positive, "positive indicators" if has_positive else "no clear slots", page.url

def scrape_pomelo_clinic(clinic: dict, user: dict) -> dict:
    headless = os.getenv("HEADLESS", "true").lower() != "false"
    booking_url = clinic.get("booking_url", "")
    print(f"\n   🔴 POMELO: {clinic['name']} ({clinic.get('distance_km', '?')}km)")
    if KillSwitch.is_active(): return {"found": False, "details": "Kill switch", "booking_url": ""}
    with sync_playwright() as p:
        browser, context = launch_stealth_browser(p, headless=headless)
        page = context.new_page()
        try:
            page.goto(booking_url, wait_until="domcontentloaded", timeout=60000)
            human_delay(2000, 3000)
            if KillSwitch.is_active(): return {"found": False, "details": "Kill switch", "booking_url": ""}
            fill_pomelo_page1_identification(page, user)
            fill_pomelo_page2_contact(page, user)
            fill_pomelo_page3_consent(page)
            fill_pomelo_page4_search(page, user["postal_code"])
            has_slots, details, result_url = verify_pomelo_calendar(page, clinic["name"])
            if has_slots:
                print(f"      🎉 SLOTS FOUND! ({details})")
                return {"found": True, "details": details, "booking_url": result_url}
            else:
                print(f"      ❌ No slots")
                return {"found": False, "details": details, "booking_url": booking_url}
        except Exception as e:
            print(f"      🚨 Pomelo error: {e}")
            return {"found": False, "details": str(e), "booking_url": booking_url}
        finally:
            browser.close()


# ══════════════════════════════════════════════════════════════
# 11. BONJOUR SANTÉ HANDLER — ★ DEBUG MODE
# ══════════════════════════════════════════════════════════════

def fill_bonjoursante_page1(page, user: dict):
    print("      📝 Bonjour Santé — Page 1 (RAMQ + Postal)...")
    if KillSwitch.is_active(): return False
    try: page.locator("input[name*='ramq']").first.fill(user["ramq"]); human_delay(400, 800)
    except: pass
    try: page.locator("input[name*='postal']").first.fill(user["postal_code"]); human_delay(400, 800)
    except: pass
    try: page.get_by_role("button", name=re.compile(r"Continuer|Suivant|Next|Rechercher", re.I)).first.click(); human_delay(2000, 3000)
    except: pass
    return True

def fill_bonjoursante_page2(page, user: dict):
    print("      📝 Bonjour Santé — Page 2 (Patient Info)...")
    if KillSwitch.is_active(): return False
    try: page.locator("input[name*='firstName'], input[name*='prenom']").first.fill(user["first_name"]); human_delay(300, 600)
    except: pass
    try: page.locator("input[name*='lastName'], input[name*='nom']").first.fill(user["last_name"]); human_delay(300, 600)
    except: pass
    try: page.locator("input[name*='sequence'], input[name*='seq']").first.fill(user["ramq_seq"]); human_delay(300, 600)
    except: pass
    try: page.locator("input[type='checkbox']").first.check(); human_delay(500, 1000)
    except: pass
    try: page.get_by_role("button", name=re.compile(r"Continuer|Suivant|Next", re.I)).first.click(); human_delay(2000, 3000)
    except: pass
    return True

def verify_bonjoursante_results(page, clinic_name: str) -> tuple:
    print("      📅 Checking Bonjour Santé results...")
    if KillSwitch.is_active(): return False, "Kill switch", ""

    human_delay(2000, 3000)

    # ★ DEBUG: Save page HTML snapshot
    try:
        html_snippet = page.content()
        # Save first 3000 chars to avoid log overflow
        print(f"      📄 Page HTML (first 3000 chars): {html_snippet[:3000]}")
    except Exception as e:
        print(f"      ⚠️ HTML capture error: {e}")

    # ★ DEBUG: Capture all links on the page
    try:
        all_links = page.locator('a').all()
        print(f"      🔗 Total links found: {len(all_links)}")
        for link in all_links[:20]:
            try:
                href = link.get_attribute('href')
                text = link.inner_text().strip()[:80]
                if href and ('book' in href.lower() or 'reserv' in href.lower() or 'rendez-vous' in href.lower() or 'appointment' in href.lower() or 'slot' in href.lower() or 'creneau' in href.lower()):
                    print(f"      🔗 BOOKING LINK: {text} → {href}")
            except:
                pass
    except Exception as e:
        print(f"      ⚠️ Link capture error: {e}")

    # ★ DEBUG: Look for data attributes with slot info
    try:
        slot_elements = page.locator('[data-slot], [data-appointment], [data-creneau], [data-rendezvous], [data-id]').all()
        print(f"      📊 Slot elements found: {len(slot_elements)}")
        for el in slot_elements[:5]:
            try:
                dataset = el.evaluate('el => JSON.stringify(el.dataset)')
                print(f"      📊 Slot data: {dataset[:200]}")
            except:
                pass
    except Exception as e:
        print(f"      ⚠️ Slot data error: {e}")

    # ★ DEBUG: Look for JSON data in script tags
    try:
        script_tags = page.locator('script').all()
        print(f"      📜 Script tags found: {len(script_tags)}")
        for script in script_tags[:10]:
            try:
                content = script.inner_text()
                if 'slot' in content.lower() or 'appointment' in content.lower() or 'booking' in content.lower() or 'rendez-vous' in content.lower() or 'creneau' in content.lower():
                    print(f"      📜 Script with slot data (first 500 chars): {content[:500]}")
            except:
                pass
    except Exception as e:
        print(f"      ⚠️ Script tag error: {e}")

    # ★ DEBUG: Look for buttons with booking-related text
    try:
        booking_buttons = page.locator('button:has-text("Réserver"), button:has-text("Book"), button:has-text("Choisir"), button:has-text("Select"), button:has-text("Prendre")').all()
        print(f"      🔘 Booking buttons found: {len(booking_buttons)}")
        for btn in booking_buttons[:5]:
            try:
                text = btn.inner_text().strip()[:80]
                parent_html = btn.evaluate('el => el.parentElement.outerHTML')
                print(f"      🔘 Button: {text}")
                print(f"      🔘 Parent HTML (first 300 chars): {parent_html[:300]}")
            except:
                pass
    except Exception as e:
        print(f"      ⚠️ Button error: {e}")

    # ★ DEBUG: Capture current page URL
    current_url = page.url
    print(f"      🌐 Current page URL: {current_url}")

    # Original check
    body_text = page.inner_text("body").lower()
    for phrase in ["aucune disponibilité", "no availability", "aucun rendez-vous", "complet", "full", "désolé"]:
        if phrase in body_text:
            return False, phrase, current_url

    has_positive = any(p in body_text for p in ["disponible", "available", "réserver", "book", "choisir"])
    return has_positive, "positive indicators" if has_positive else "no clear slots", current_url


def scrape_bonjoursante_clinic(clinic: dict, user: dict) -> dict:
    headless = os.getenv("HEADLESS", "true").lower() != "false"
    booking_url = clinic.get("booking_url", "")
    print(f"\n   🟠 BONJOUR SANTÉ: {clinic['name']} ({clinic.get('distance_km', '?')}km)")
    if KillSwitch.is_active(): return {"found": False, "details": "Kill switch", "booking_url": ""}

    # ★ API response interceptor — capture booking data
    captured_api_data = []

    with sync_playwright() as p:
        browser, context = launch_stealth_browser(p, headless=headless)
        page = context.new_page()

        # ★ Intercept API responses (like ClicSanté backdoor)
        def on_response(response):
            try:
                url = response.url
                if response.status == 200:
                    ct = response.headers.get('content-type', '')
                    if 'json' in ct:
                        try:
                            body = response.json()
                            # Look for slot/booking data in JSON responses
                            body_str = json.dumps(body).lower()
                            if any(kw in body_str for kw in ['slot', 'creneau', 'appointment', 'booking', 'rendez-vous', 'disponibilite', 'availability', 'plage']):
                                captured_api_data.append({'url': url, 'data': body})
                                print(f"      📡 API Intercepted: {url[:100]}")
                        except:
                            pass
            except:
                pass

        page.on("response", on_response)

        try:
            page.goto(booking_url, wait_until="domcontentloaded", timeout=60000)
            human_delay(2000, 3000)
            if KillSwitch.is_active(): return {"found": False, "details": "Kill switch", "booking_url": ""}
            fill_bonjoursante_page1(page, user)
            fill_bonjoursante_page2(page, user)
            has_slots, details, result_url = verify_bonjoursante_results(page, clinic["name"])

            # ★ Print captured API data for debugging
            if captured_api_data:
                print(f"      📡 Total API responses captured: {len(captured_api_data)}")
                for i, api in enumerate(captured_api_data[:5]):
                    print(f"      📡 API #{i+1}: {api['url'][:120]}")
                    data_str = json.dumps(api['data'])[:500]
                    print(f"      📡 Data: {data_str}")

            if has_slots:
                # ★ Try to extract direct booking URL from captured API data
                direct_booking_url = result_url
                for api in captured_api_data:
                    data = api['data']
                    data_str = json.dumps(data)
                    # Look for booking URLs in API responses
                    url_matches = re.findall(r'https?://[^\s"\']+(?:book|reserv|appointment|rendez-vous|slot|creneau)[^\s"\']*', data_str, re.I)
                    if url_matches:
                        direct_booking_url = url_matches[0]
                        print(f"      🎯 Direct booking URL from API: {direct_booking_url}")
                        break

                print(f"      🎉 SLOTS FOUND! ({details})")
                return {"found": True, "details": details, "booking_url": direct_booking_url}
            else:
                print(f"      ❌ No slots")
                return {"found": False, "details": details, "booking_url": booking_url}
        except Exception as e:
            print(f"      🚨 Bonjour Santé error: {e}")
            return {"found": False, "details": str(e), "booking_url": booking_url}
        finally:
            browser.close()


# ══════════════════════════════════════════════════════════════
# 12. DISPATCHER
# ══════════════════════════════════════════════════════════════

def route_and_scrape_clinic(clinic: dict, user: dict) -> dict:
    platform = clinic.get("platform", "unknown")
    if platform == "pomelo":
        return scrape_pomelo_clinic(clinic, user)
    elif platform == "bonjour_sante":
        return scrape_bonjoursante_clinic(clinic, user)
    else:
        return {"found": False, "details": f"skipped_{platform}", "booking_url": clinic.get("booking_url", "")}


# ══════════════════════════════════════════════════════════════
# 13. ZONE SEARCH
# ══════════════════════════════════════════════════════════════

def search_clinics_in_zone(user: dict, radius_km: int) -> bool:
    postal = user["postal_code"]
    clinics = discover_clinics_near(postal, radius_km)
    if not clinics:
        return False

    for clinic in clinics:
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
            send_notification(clinic["name"], postal, clinic.get("platform"), result["booking_url"])
            save_availability(clinic["name"], postal, clinic.get("platform"), True, result["booking_url"], result["details"])
            return True
        else:
            save_availability(clinic["name"], postal, clinic.get("platform"), False, clinic.get("booking_url", ""), result["details"])
    return False


# ══════════════════════════════════════════════════════════════
# 14. MAIN ORCHESTRATOR
# ══════════════════════════════════════════════════════════════

def run_single_search(user_postal: str = None, request_id: str = None):
    if user_postal is None:
        user_postal = os.getenv("POSTAL_CODE", "H1Y3H1")
    if request_id is None:
        request_id = os.getenv("REQUEST_ID", "")

    user = get_user_data()
    user["postal_code"] = user_postal
    language = user.get("language", "fr")
    KillSwitch.reset()
    max_date = (datetime.now() + timedelta(days=MAX_DAYS_AHEAD)).strftime("%Y-%m-%d")

    print(f"\n{'='*60}")
    print(f"🚀 MYVITA CLINIC SCRAPER")
    print(f"   Postal: {user_postal} | Max: {max_date}")
    print(f"   Request ID: {request_id if request_id else 'N/A'}")
    print(f"   Tiers: {RADIUS_TIERS}km | 78-clinic map with coordinates")
    print(f"   Platforms: Pomelo + Bonjour Santé")
    print(f"{'='*60}\n")

    for radius in RADIUS_TIERS:
        if KillSwitch.is_active():
            break
        print(f"\n🔵 TIER: {radius}km")
        found = search_clinics_in_zone(user, radius)
        if found:
            print(f"\n🎉 FOUND! {KillSwitch._found_appointment.get('clinic_name')}")
            if request_id:
                update_clinic_request(request_id, "completed", KillSwitch._found_appointment)
            return KillSwitch._found_appointment

    if not KillSwitch.is_active():
        print(f"\n😴 No appointments found in any tier.")
        if request_id:
            update_clinic_request(request_id, "completed", {"found": False, "details": "No appointments found in any tier"})
        send_no_slots_notification(user_postal, language)

    return None


# ══════════════════════════════════════════════════════════════
# 15. MAIN
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("╔══════════════════════════════════════════════════════╗")
    print("║     MYVITA CLINIC SCRAPER — Map-First v2            ║")
    print("║     78 clinics with coordinates                     ║")
    print("╚══════════════════════════════════════════════════════╝")
    postal = os.getenv("POSTAL_CODE", "H1Y3H1")
    request_id = os.getenv("REQUEST_ID", "")
    run_single_search(user_postal=postal, request_id=request_id)
    print("\n✅ Session complete")
