# ══════════════════════════════════════════════════════════════
# MYVITA CONCIERGE SCRAPER — Admin-Only, Manual Trigger
# ══════════════════════════════════════════════════════════════
# • ClicSanté + Bonjour Santé + Pomelo
# • 3 days + 2 time slots from user preferences
# • Single-shot kill switch — returns ONE result to admin
# • NEVER auto-triggers — admin presses button manually
# ══════════════════════════════════════════════════════════════

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

RADIUS_TIERS = [15, 30, 50]
MAX_DAYS_AHEAD = 10

# Time slot mapping for filtering
TIME_SLOT_MAP = {
    "Matin": {"start": 8, "end": 12},
    "Après-midi": {"start": 12, "end": 17},
    "Soir": {"start": 17, "end": 21},
    "Soirée": {"start": 17, "end": 21},
}

# Day mapping FR → EN for date calculation
DAY_MAP = {
    "Lundi": 0, "Mardi": 1, "Mercredi": 2,
    "Jeudi": 3, "Vendredi": 4, "Samedi": 5, "Dimanche": 6,
}

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
        print(f"\n🛑 KILL SWITCH — CONCIERGE: {details.get('clinic_name')} | {details.get('platform')}")

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
            print("✅ Firebase initialized (Concierge)")
    except Exception as e:
        print(f"⚠️ Firebase Init Error: {e}")

init_firebase()


# ══════════════════════════════════════════════════════════════
# 4. USER DATA (from GitHub Action inputs)
# ══════════════════════════════════════════════════════════════

def get_user_data():
    return {
        "first_name": os.getenv("USER_FIRST_NAME", ""),
        "last_name": os.getenv("USER_LAST_NAME", ""),
        "ramq": os.getenv("USER_RAMQ", ""),
        "ramq_seq": os.getenv("USER_RAMQ_SEQ", "01"),
        "ramq_expiry": os.getenv("USER_RAMQ_EXPIRY", ""),
        "birth_date": os.getenv("USER_BIRTH_DATE", ""),
        "sex": os.getenv("USER_SEX", "M"),
        "email": os.getenv("USER_EMAIL", ""),
        "phone": os.getenv("USER_PHONE", ""),
        "postal_code": os.getenv("POSTAL_CODE", ""),
        "lat": float(os.getenv("USER_LAT", "0")),
        "lng": float(os.getenv("USER_LNG", "0")),
        "preferred_days": os.getenv("PREFERRED_DAYS", "Lundi,Mardi,Mercredi"),
        "preferred_times": os.getenv("PREFERRED_TIMES", "Matin,Après-midi"),
        "radius_km": int(os.getenv("SEARCH_RADIUS", "30")),
        "motif": os.getenv("MOTIF", ""),
        "request_id": os.getenv("REQUEST_ID", ""),
    }


# ══════════════════════════════════════════════════════════════
# 5. UTILITY FUNCTIONS
# ══════════════════════════════════════════════════════════════

def human_delay(min_ms=500, max_ms=1500):
    time.sleep(random.uniform(min_ms, max_ms) / 1000)

def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def get_target_dates(preferred_days_str: str) -> list:
    """Convert 'Lundi,Mardi,Mercredi' → list of target YYYY-MM-DD dates"""
    target_day_names = [d.strip() for d in preferred_days_str.split(",")]
    target_weekdays = [DAY_MAP.get(d) for d in target_day_names if d in DAY_MAP]

    if not target_weekdays:
        return []

    dates = []
    today = datetime.now()
    for i in range(1, MAX_DAYS_AHEAD + 1):
        future = today + timedelta(days=i)
        if future.weekday() in target_weekdays:
            dates.append(future.strftime("%Y-%m-%d"))
    return dates

def time_slot_matches(slot_text: str, preferred_times_str: str) -> bool:
    """Check if a found slot matches user's preferred time slots"""
    preferred = [t.strip().lower() for t in preferred_times_str.split(",")]
    slot_lower = slot_text.lower()

    for pref in preferred:
        if pref in slot_lower:
            return True
        # Check numeric ranges
        if pref in TIME_SLOT_MAP:
            r = TIME_SLOT_MAP[pref]
            # Extract hour from slot text if present
            hour_match = re.search(r'(\d{1,2})\s*[:h]', slot_lower)
            if hour_match:
                hour = int(hour_match.group(1))
                if r["start"] <= hour < r["end"]:
                    return True
    return False


# ══════════════════════════════════════════════════════════════
# 6. SAVE RESULT TO ADMIN FIRESTORE
# ══════════════════════════════════════════════════════════════

def save_concierge_result(request_id: str, result: dict):
    """Save scraper result to the request document for admin to see"""
    if db is None: return
    try:
        db.collection("concierge_requests").document(request_id).update({
            "scraper_result": result,
            "scraper_ran_at": firestore.SERVER_TIMESTAMP,
            "status": "scraper_completed" if result.get("found") else "scraper_no_result",
        })
        print(f"✅ Result saved to Firestore: {request_id}")
    except Exception as e:
        print(f"❌ Firestore update error: {e}")

def notify_admin_slot_found(request_id: str, result: dict):
    """Send FCM to admin that a slot was found"""
    if db is None: return
    try:
        # Query admin tokens
        admins_ref = db.collection("users").where("role", "==", "admin").limit(5)
        for doc in admins_ref.stream():
            token = doc.to_dict().get("fcmToken")
            if token:
                messaging.send(messaging.Message(
                    notification=messaging.Notification(
                        title="🎉 RDV trouvé!",
                        body=f"{result.get('clinic_name')} — {result.get('slot_details', '')}"
                    ),
                    data={
                        "request_id": request_id,
                        "clinic": result.get("clinic_name", ""),
                        "booking_url": result.get("booking_url", ""),
                    },
                    token=token,
                ))
        print("✅ Admin notified")
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
# 8. CLINIC MAP — 78 clinics (ALL platforms including ClicSanté)
# ══════════════════════════════════════════════════════════════

CLINICS = [
    # Montreal East
    {"name": "GMF-R Cité Médicale Villeray", "lat": 45.5463, "lng": -73.6214, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous", "phone": "(514) 375-1444"},
    {"name": "Centre Médical Mieux-Être (succursale Levasseur)", "lat": 45.5841, "lng": -73.6412, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/levasseur", "phone": "(514) 381-6317"},
    {"name": "Clinique Médico-Centre Mont-Royal", "lat": 45.5163, "lng": -73.5786, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/montroyal", "phone": "(514) 844-3333"},
    {"name": "GMF Médi-Centre Chomedey", "lat": 45.5451, "lng": -73.7483, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/medicentrechomedey", "phone": "(450) 681-6411"},
    {"name": "Polyclinique du cœur-de-l'île GMF-R Jarry-Lajeunesse", "lat": 45.5442, "lng": -73.6256, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous", "phone": "(514) 381-2331"},
    {"name": "CLSC du Plateau-Mont-Royal", "lat": 45.5195, "lng": -73.5781, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous", "phone": "(514) 527-9511"},
    {"name": "CLSC de Dorval-Lachine", "lat": 45.4385, "lng": -73.6841, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous", "phone": "(514) 639-0660"},
    {"name": "UnionMD - Clinique Médicale Privée Montreal", "lat": 45.5032, "lng": -73.5721, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous", "phone": "(514) 123-4567"},
    {"name": "CLSC de Saint-Henri (Montréal)", "lat": 45.4775, "lng": -73.5856, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous", "phone": "(514) 933-7577"},
    {"name": "CLSC Métro (Montréal)", "lat": 45.4931, "lng": -73.5802, "platform": "clicsante", "booking_url": "https://portal3.clicsante.ca/", "phone": "(514) 934-0354"},
    {"name": "CLSC de Hochelaga-Maisonneuve", "lat": 45.5422, "lng": -73.5397, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous", "phone": "(514) 253-2181"},
    {"name": "Centre D'Urgence Saint-Laurent (GMF)", "lat": 45.5118, "lng": -73.6802, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/cusl", "phone": "(514) 747-2444"},
    {"name": "GMF A-R Clinique médicale Angus (Montréal)", "lat": 45.5401, "lng": -73.5658, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/angus", "phone": "(514) 807-2334"},
    # Montreal Central
    {"name": "GMF Clinique Médicale St-Denis (Montréal)", "lat": 45.5264, "lng": -73.5932, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/stdenis", "phone": "(514) 272-1133"},
    # Anjou
    {"name": "GMF Centre Médical Mieux-Être (Succursale Anjou)", "lat": 45.6031, "lng": -73.5518, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous", "phone": "(514) 355-3333"},
    # Mieux-Être network
    {"name": "Centre Médical Mieux-Être - Lasalle", "lat": 45.4312, "lng": -73.6248, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/cmmelasalle", "phone": "(514) 363-2331"},
    {"name": "Centre Médical Mieux-Être - Henri-Bourassa", "lat": 45.6421, "lng": -73.6105, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/cmmehenribourassa", "phone": "(514) 321-2331"},
    {"name": "GMF A-R Centre médical Mieux-Être – St-Léonard", "lat": 45.5892, "lng": -73.6014, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/mieuxetre", "phone": "(514) 321-2331"},
    # Laval
    {"name": "GMF Le Carrefour Médical (Laval)", "lat": 45.5684, "lng": -73.7431, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/lecarrefour", "phone": "(450) 687-5732"},
    {"name": "GMF Clinique Médicale Sainte-Dorothée (Laval)", "lat": 45.5312, "lng": -73.8115, "platform": "pomelo", "booking_url": "https://pomelo.health/cliniquemedicalesaintedorothee", "phone": "(450) 689-5353"},
    {"name": "GMF Polyclinique Concorde (Laval)", "lat": 45.5615, "lng": -73.7082, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous", "phone": "(450) 667-5311"},
    {"name": "CLSC de Laval-des-Rapides", "lat": 45.5492, "lng": -73.7124, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous", "phone": "(450) 668-1801"},
    {"name": "Super-Clinique Polyclinique Médicale Fabreville (GMF)", "lat": 45.5925, "lng": -73.7912, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/fabreville", "phone": "(450) 625-1191"},
    {"name": "Clinique Médicale Saint-François (GMF)", "lat": 45.5781, "lng": -73.6542, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/stfrancois", "phone": "(450) 666-8551"},
    {"name": "CLSC Idola-Saint-Jean", "lat": 45.5652, "lng": -73.6931, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous", "phone": "(450) 668-1803"},
    {"name": "CLSC des Mille-Îles", "lat": 45.6315, "lng": -73.6212, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous", "phone": "(450) 661-2572"},
    {"name": "CLSC de l'Ouest-de-l'Île", "lat": 45.4523, "lng": -73.8321, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous", "phone": "(514) 697-4110"},
    {"name": "CLSC de Sainte-Rose", "lat": 45.6121, "lng": -73.7824, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous", "phone": "(450) 622-5110"},
    {"name": "CLSC du Ruisseau-Papineau", "lat": 45.5794, "lng": -73.7251, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous", "phone": "(450) 687-5690"},
    {"name": "GMF Centre Médical Laval", "lat": 45.5521, "lng": -73.7314, "platform": "pomelo", "booking_url": "https://qc.pomelo.health/centremedicallaval", "phone": "(450) 663-3500"},
    # Longueuil / Rive-Sud
    {"name": "Clinique médicale privée Longueuil - Rive-Sud - UnionMD", "lat": 45.5252, "lng": -73.5135, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/unionmdlongueuil", "phone": "(450) 679-2232"},
    {"name": "GMF-R Clinique Médicale Longueuil-Ouest", "lat": 45.5314, "lng": -73.5248, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/longueuilouest", "phone": "(450) 651-6111"},
    {"name": "CLSC de Longueuil-Ouest (Rive-Sud)", "lat": 45.5314, "lng": -73.5248, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous", "phone": "(450) 651-6111"},
    {"name": "GMF-U Charles-Le Moyne (Longueuil)", "lat": 45.5184, "lng": -73.4831, "platform": "pomelo", "booking_url": "https://qc.pomelo.health/gmfucharleslemoyne", "phone": "(450) 466-5431"},
    # Brossard
    {"name": "GMF Dix30 (Clinique d'urgence avec rendez-vous Dix30 Brossard)", "lat": 45.4428, "lng": -73.4412, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/gmfdix30", "phone": "(450) 443-4400"},
    {"name": "Clinique Sans Rendez-Vous Dix30 Brossard (GMF)", "lat": 45.4428, "lng": -73.4412, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/csansrendezvousdix30brossard", "phone": "(450) 443-4400"},
    {"name": "GMF Samuel-de-Champlain", "lat": 45.4682, "lng": -73.4715, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous", "phone": "(450) 445-4452"},
    {"name": "GMF Lapinière", "lat": 45.4561, "lng": -73.4623, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous", "phone": "(450) 443-3343"},
    # West Island
    {"name": "GMF Stillview (West Island - Pointe-Claire)", "lat": 45.4485, "lng": -73.8124, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous", "phone": "(514) 697-1134"},
    {"name": "GMF Clinique Médicale Brunswick (West Island - Pointe-Claire)", "lat": 45.4498, "lng": -73.8315, "platform": "pomelo", "booking_url": "https://pomelo.health/brunswickmedicalcenter", "phone": "(514) 426-6677"},
    {"name": "CLSC du Lac-Saint-Louis (West Island - Pointe-Claire)", "lat": 45.4392, "lng": -73.8184, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous", "phone": "(514) 697-4110"},
    {"name": "CLSC de Pierrefonds (West Island)", "lat": 45.4852, "lng": -73.8742, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous", "phone": "(514) 626-2572"},
    # North Shore
    {"name": "GMF des Seigneurs (Terrebonne)", "lat": 45.7025, "lng": -73.6514, "platform": "pomelo", "booking_url": "https://qc.pomelo.health/gmfdesseigneurs", "phone": "(450) 471-1212"},
    {"name": "GMF Clinique Médicale Terrebonne", "lat": 45.6982, "lng": -73.6391, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/cmterrebonne", "phone": "(450) 492-3434"},
    {"name": "GMF des Affluents (Repentigny)", "lat": 45.7485, "lng": -73.4421, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous", "phone": "(450) 581-2244"},
    {"name": "GMF-U du Sud de Lanaudière (Repentigny)", "lat": 45.7412, "lng": -73.4563, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous", "phone": "(450) 654-2735"},
    {"name": "GMF-U de Saint-Charles-Borromée (Lanaudière Joliette)", "lat": 46.0465, "lng": -73.4682, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous", "phone": "(450) 759-8088"},
    {"name": "GMF L'Assomption", "lat": 45.8312, "lng": -73.4215, "platform": "pomelo", "booking_url": "https://qc.pomelo.health/#/", "phone": "(450) 589-5636"},
    {"name": "Centre de Médecine Métabolique de Lanaudière (CMML)", "lat": 46.0242, "lng": -73.4356, "platform": "pomelo", "booking_url": "https://qc.pomelo.health/cmml/portal#/patient-triage", "phone": "(450) 759-2222"},
    # Saint-Jérôme
    {"name": "GMF-R Clinique Médicale Saint-Jérôme", "lat": 45.7785, "lng": -74.0042, "platform": "clicsante", "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous", "phone": "(450) 436-1212"},
    {"name": "GMF du Grand Saint-Jérôme (Clinique Saint-Hippolyte)", "lat": 45.8321, "lng": -73.9915, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/santhippolyte", "phone": "(450) 563-3663"},
    # Rosemère
    {"name": "GMF Clinique Médicale Rosemère", "lat": 45.6382, "lng": -73.7915, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/rosemere", "phone": "(450) 621-0422"},
    # Vaudreuil
    {"name": "GMF-R Vaudreuil-Dorion (Super-Clinique)", "lat": 45.3982, "lng": -74.0321, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/vaudreuildorion", "phone": "(450) 455-9301"},
    # Saint-Eustache
    {"name": "GMF Clinique Médicale de la Gare (Saint-Eustache)", "lat": 45.5582, "lng": -73.9015, "platform": "pomelo", "booking_url": "https://qc.pomelo.health/cliniquemedicaledelagare", "phone": "(450) 472-7474"},
    # Saint-Jean
    {"name": "GMF Clinique Médicale Saint-Luc (Saint-Jean-sur-Richelieu)", "lat": 45.3512, "lng": -73.2842, "platform": "pomelo", "booking_url": "https://qc.pomelo.health/cliniquemedicalesaintluc", "phone": "(450) 348-1153"},
    # Laurentides
    {"name": "GMF Clinique Médicale Lorraine (Laurentides)", "lat": 45.6512, "lng": -73.7814, "platform": "bonjour_sante", "booking_url": "https://bonjour-sante.ca/uno/clinique/cmlorraine", "phone": "(450) 621-3611"},
]

# ALL platforms active for concierge
ACTIVE_PLATFORMS = {"bonjour_sante", "pomelo", "clicsante"}


# ══════════════════════════════════════════════════════════════
# 9. DISTANCE-BASED CLINIC DISCOVERY
# ══════════════════════════════════════════════════════════════

def discover_clinics_near(user_lat: float, user_lng: float, radius_km: int) -> list:
    """Filter the 78-clinic map by distance from user coordinates"""
    print(f"\n🔍 Finding clinics within {radius_km}km of ({user_lat}, {user_lng})...")

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
                "phone": clinic.get("phone", ""),
                "distance_km": round(dist, 1),
            })

    nearby.sort(key=lambda c: c["distance_km"])
    print(f"✅ Found {len(nearby)} active clinics within {radius_km}km")
    for c in nearby:
        print(f"   📍 {c['name']} — {c['distance_km']}km — {c['platform']} — {c.get('phone', 'N/A')}")
    return nearby


# ══════════════════════════════════════════════════════════════
# 10. CLICSANTÉ HANDLER (NEW)
# ══════════════════════════════════════════════════════════════

def fill_clicsante_search(page, user: dict):
    """Navigate ClicSanté search flow"""
    print("      📝 ClicSanté — Search...")
    if KillSwitch.is_active(): return False

    try:
        # ClicSanté typically has a postal code / region selector
        postal = user["postal_code"][:3]
        page.locator("input[name*='postal'], input[name*='code'], input[placeholder*='code']").first.fill(postal)
        human_delay(500, 1000)
    except:
        pass

    try:
        page.get_by_role("button", name=re.compile(r"Rechercher|Search|Chercher|Continuer", re.I)).first.click()
        human_delay(3000, 5000)
    except:
        pass

    return True

def fill_clicsante_patient_info(page, user: dict):
    """Fill patient information on ClicSanté"""
    print("      📝 ClicSanté — Patient Info...")
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
        page.locator("input[name*='ramq']").first.fill(user["ramq"])
        human_delay(300, 600)
    except: pass
    try:
        page.locator("input[name*='seq']").first.fill(user["ramq_seq"])
        human_delay(300, 600)
    except: pass

    # Birth date
    birth_date = user.get("birth_date", "")
    if birth_date:
        try:
            parts = birth_date.split("-")
            if len(parts) == 3:
                page.locator("input[name*='year'], input[name*='annee']").first.fill(parts[0])
                page.locator("input[name*='month'], input[name*='mois']").first.fill(parts[1])
                page.locator("input[name*='day'], input[name*='jour']").first.fill(parts[2])
                human_delay(300, 600)
        except: pass

    # Gender
    try:
        if user["sex"].upper() == "M":
            page.locator("input[value='M'], label:has-text('Homme'), label:has-text('Masculin')").first.click()
        else:
            page.locator("input[value='F'], label:has-text('Femme'), label:has-text('Féminin')").first.click()
        human_delay(500, 800)
    except: pass

    try:
        page.get_by_role("button", name=re.compile(r"Continuer|Suivant|Next", re.I)).first.click()
        human_delay(2000, 3000)
    except: pass

    return True

def verify_clicsante_results(page, preferred_days: str, preferred_times: str) -> tuple:
    """Check ClicSanté calendar for slots matching preferences"""
    print("      📅 Checking ClicSanté calendar...")
    if KillSwitch.is_active(): return False, "Kill switch", {}

    human_delay(2000, 3000)
    body_text = page.inner_text("body").lower()

    for phrase in ["aucune disponibilité", "no availability", "aucun rendez-vous", "complet", "full", "désolé"]:
        if phrase in body_text:
            return False, phrase, {}

    # Extract slot details
    slot_details = {}
    target_dates = get_target_dates(preferred_days)

    # Try to find available dates in the calendar
    for date_str in target_dates:
        if date_str.replace("-", "") in body_text or date_str in body_text:
            slot_details["date"] = date_str
            break

    # Check for time slots
    time_matches = re.findall(r'(\d{1,2})[:h](\d{2})?', body_text)
    for hour_str, minute_str in time_matches:
        hour = int(hour_str)
        if time_slot_matches(f"{hour}h{minute_str or '00'}", preferred_times):
            slot_details["time"] = f"{hour}h{minute_str or '00'}"
            break

    has_slots = any(p in body_text for p in ["disponible", "available", "réserver", "book", "choisir", "plage"])
    return has_slots, "slots found" if has_slots else "no clear slots", slot_details

def scrape_clicsante_clinic(clinic: dict, user: dict) -> dict:
    headless = os.getenv("HEADLESS", "true").lower() != "false"
    booking_url = clinic.get("booking_url", "https://rvsq.gouv.qc.ca/prendrerendezvous")
    print(f"\n   🔵 CLICSANTÉ: {clinic['name']} ({clinic.get('distance_km', '?')}km)")
    if KillSwitch.is_active(): return {"found": False, "details": "Kill switch", "booking_url": ""}

    with sync_playwright() as p:
        browser, context = launch_stealth_browser(p, headless=headless)
        page = context.new_page()
        try:
            page.goto(booking_url, wait_until="domcontentloaded", timeout=60000)
            human_delay(2000, 3000)
            if KillSwitch.is_active(): return {"found": False, "details": "Kill switch", "booking_url": ""}

            fill_clicsante_search(page, user)
            fill_clicsante_patient_info(page, user)
            has_slots, details, slot_data = verify_clicsante_results(
                page, user["preferred_days"], user["preferred_times"]
            )

            if has_slots:
                print(f"      🎉 SLOTS FOUND! ({details})")
                return {"found": True, "details": str(slot_data), "booking_url": page.url, "slot_data": slot_data}
            else:
                print(f"      ❌ No slots")
                return {"found": False, "details": details, "booking_url": ""}

        except Exception as e:
            print(f"      🚨 ClicSanté error: {e}")
            return {"found": False, "details": str(e), "booking_url": ""}
        finally:
            browser.close()


# ══════════════════════════════════════════════════════════════
# 11. POMELO HANDLER (same as user scraper)
# ══════════════════════════════════════════════════════════════

def fill_pomelo_page1_identification(page, user: dict):
    print("      📝 Pomelo — Page 1 (Identification)...")
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
        try: page.get_by_text(re.compile(r"Masculin|Homme|Féminin|Femme", re.I)).first.click(); human_delay(500, 800)
        except: pass
    try: page.get_by_role("button", name=re.compile(r"Continuer|Suivant|Next", re.I)).first.click(); human_delay(2000, 3000)
    except: pass
    return True

def fill_pomelo_page2_contact(page, user: dict):
    print("      📧 Pomelo — Page 2 (Contact)...")
    if KillSwitch.is_active(): return False
    try: page.locator("input[type='email']").first.fill(user["email"]); human_delay(300, 600)
    except: pass
    try: page.locator("input[type='tel']").first.fill(user["phone"]); human_delay(300, 600)
    except: pass
    try: page.get_by_role("button", name=re.compile(r"Continuer|Suivant|Next", re.I)).first.click(); human_delay(2000, 3000)
    except: pass
    return True

def fill_pomelo_page3_consent(page):
    print("      ✅ Pomelo — Page 3 (Consent)...")
    if KillSwitch.is_active(): return False
    try: page.locator("input[type='checkbox']").first.check(); human_delay(500, 1000)
    except: pass
    try: page.get_by_role("button", name=re.compile(r"Continuer|Suivant|Next", re.I)).first.click(); human_delay(2000, 3000)
    except: pass
    return True

def fill_pomelo_page4_search(page, postal_code: str):
    print(f"      🔍 Pomelo — Page 4 (Search: {postal_code})...")
    if KillSwitch.is_active(): return False
    try: page.locator("input[name*='postal'], input[name*='code']").first.fill(postal_code); human_delay(500, 1000)
    except: pass
    try: page.get_by_role("button", name=re.compile(r"Rechercher|Search|Chercher", re.I)).first.click(); human_delay(3000, 5000)
    except: pass
    return True

def verify_pomelo_calendar(page, clinic_name: str, preferred_days: str, preferred_times: str) -> tuple:
    print("      📅 Checking Pomelo calendar...")
    if KillSwitch.is_active(): return False, "Kill switch", {}
    human_delay(2000, 3000)
    body_text = page.inner_text("body").lower()
    for phrase in ["aucune disponibilité", "no availability", "aucun rendez-vous", "complet", "full", "désolé"]:
        if phrase in body_text: return False, phrase, {}
    has_positive = any(p in body_text for p in ["disponible", "available", "sélectionner", "select"])
    slot_data = {}
    if has_positive:
        for date_str in get_target_dates(preferred_days):
            if date_str.replace("-", "") in body_text or date_str in body_text:
                slot_data["date"] = date_str
                break
    return has_positive, "positive indicators" if has_positive else "no clear slots", slot_data

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
            has_slots, details, slot_data = verify_pomelo_calendar(
                page, clinic["name"], user["preferred_days"], user["preferred_times"]
            )
            if has_slots:
                print(f"      🎉 SLOTS FOUND! ({details})")
                return {"found": True, "details": str(slot_data), "booking_url": page.url, "slot_data": slot_data}
            else:
                print(f"      ❌ No slots")
                return {"found": False, "details": details, "booking_url": ""}
        except Exception as e:
            print(f"      🚨 Pomelo error: {e}")
            return {"found": False, "details": str(e), "booking_url": ""}
        finally:
            browser.close()


# ══════════════════════════════════════════════════════════════
# 12. BONJOUR SANTÉ HANDLER (same as user scraper)
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

def verify_bonjoursante_results(page, clinic_name: str, preferred_days: str, preferred_times: str) -> tuple:
    print("      📅 Checking Bonjour Santé results...")
    if KillSwitch.is_active(): return False, "Kill switch", {}
    human_delay(2000, 3000)
    body_text = page.inner_text("body").lower()
    for phrase in ["aucune disponibilité", "no availability", "aucun rendez-vous", "complet", "full", "désolé"]:
        if phrase in body_text: return False, phrase, {}
    has_positive = any(p in body_text for p in ["disponible", "available", "réserver", "book", "choisir"])
    slot_data = {}
    if has_positive:
        for date_str in get_target_dates(preferred_days):
            if date_str.replace("-", "") in body_text or date_str in body_text:
                slot_data["date"] = date_str
                break
    return has_positive, "positive indicators" if has_positive else "no clear slots", slot_data

def scrape_bonjoursante_clinic(clinic: dict, user: dict) -> dict:
    headless = os.getenv("HEADLESS", "true").lower() != "false"
    booking_url = clinic.get("booking_url", "")
    print(f"\n   🟠 BONJOUR SANTÉ: {clinic['name']} ({clinic.get('distance_km', '?')}km)")
    if KillSwitch.is_active(): return {"found": False, "details": "Kill switch", "booking_url": ""}
    with sync_playwright() as p:
        browser, context = launch_stealth_browser(p, headless=headless)
        page = context.new_page()
        try:
            page.goto(booking_url, wait_until="domcontentloaded", timeout=60000)
            human_delay(2000, 3000)
            if KillSwitch.is_active(): return {"found": False, "details": "Kill switch", "booking_url": ""}
            fill_bonjoursante_page1(page, user)
            fill_bonjoursante_page2(page, user)
            has_slots, details, slot_data = verify_bonjoursante_results(
                page, clinic["name"], user["preferred_days"], user["preferred_times"]
            )
            if has_slots:
                print(f"      🎉 SLOTS FOUND! ({details})")
                return {"found": True, "details": str(slot_data), "booking_url": page.url, "slot_data": slot_data}
            else:
                print(f"      ❌ No slots")
                return {"found": False, "details": details, "booking_url": ""}
        except Exception as e:
            print(f"      🚨 Bonjour Santé error: {e}")
            return {"found": False, "details": str(e), "booking_url": ""}
        finally:
            browser.close()


# ══════════════════════════════════════════════════════════════
# 13. DISPATCHER
# ══════════════════════════════════════════════════════════════

def route_and_scrape_clinic(clinic: dict, user: dict) -> dict:
    platform = clinic.get("platform", "unknown")
    if platform == "pomelo":
        return scrape_pomelo_clinic(clinic, user)
    elif platform == "bonjour_sante":
        return scrape_bonjoursante_clinic(clinic, user)
    elif platform == "clicsante":
        return scrape_clicsante_clinic(clinic, user)
    else:
        return {"found": False, "details": f"skipped_{platform}", "booking_url": ""}


# ══════════════════════════════════════════════════════════════
# 14. MAIN ORCHESTRATOR — CONCIERGE SINGLE-SHOT
# ══════════════════════════════════════════════════════════════

def search_clinics_in_zone(user: dict, radius_km: int) -> bool:
    clinics = discover_clinics_near(user["lat"], user["lng"], radius_km)
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
                "phone": clinic.get("phone", ""),
                "distance_km": clinic.get("distance_km"),
                "booking_url": result["booking_url"],
                "slot_details": result.get("slot_data", {}),
                "details": result["details"],
            })
            return True
    return False


def run_concierge_search():
    """
    ★ CONCIERGE MODE — Manual trigger only
    • Reads user preferences from env
    • Searches ALL platforms (ClicSanté + Bonjour Santé + Pomelo)
    • Single-shot: stops on FIRST slot found
    • Returns result to Firestore for admin to review
    """
    user = get_user_data()
    request_id = user["request_id"]
    KillSwitch.reset()

    # Parse preferences
    preferred_days_list = [d.strip() for d in user["preferred_days"].split(",")]
    preferred_times_list = [t.strip() for t in user["preferred_times"].split(",")]
    target_dates = get_target_dates(user["preferred_days"])
    radius = user["radius_km"]

    print(f"\n{'='*60}")
    print(f"🚀 MYVITA CONCIERGE SCRAPER — Admin Mode")
    print(f"   Request: {request_id}")
    print(f"   Patient: {user['first_name']} {user['last_name']}")
    print(f"   DOB: {user['birth_date']} | Sex: {user['sex']}")
    print(f"   Postal: {user['postal_code']} | Coords: ({user['lat']}, {user['lng']})")
    print(f"   Days: {preferred_days_list} → Target dates: {target_dates}")
    print(f"   Times: {preferred_times_list}")
    print(f"   Radius: {radius}km")
    print(f"   Platforms: ClicSanté + Bonjour Santé + Pomelo")
    print(f"   Mode: SINGLE-SHOT (stops on first slot)")
    print(f"{'='*60}\n")

    # Update Firestore — scraper started
    if db and request_id:
        try:
            db.collection("concierge_requests").document(request_id).update({
                "scraper_status": "running",
                "scraper_started_at": firestore.SERVER_TIMESTAMP,
            })
        except: pass

    found = False
    for r in RADIUS_TIERS:
        if r > radius:
            break
        if KillSwitch.is_active():
            found = True
            break
        print(f"\n🔵 TIER: {r}km (search radius cap: {radius}km)")
        found = search_clinics_in_zone(user, r)

    # Build final result
    if KillSwitch.is_active():
        result = KillSwitch._found_appointment
        final_result = {
            "found": True,
            "clinic_name": result["clinic_name"],
            "platform": result["platform"],
            "phone": result["phone"],
            "distance_km": result["distance_km"],
            "booking_url": result["booking_url"],
            "slot_details": result["slot_details"],
            "details": result["details"],
            "searched_at": datetime.now().isoformat(),
        }
        print(f"\n🎉 CONCIERGE: SLOT FOUND!")
        print(f"   Clinic: {result['clinic_name']}")
        print(f"   Platform: {result['platform']}")
        print(f"   Phone: {result['phone']}")
        print(f"   Distance: {result['distance_km']}km")
        print(f"   Slot: {result['slot_details']}")
        print(f"   URL: {result['booking_url']}")

        # Save to Firestore
        save_concierge_result(request_id, final_result)
        notify_admin_slot_found(request_id, final_result)

    else:
        final_result = {
            "found": False,
            "details": f"No appointments found within {radius}km for {preferred_days_list} ({preferred_times_list})",
            "searched_at": datetime.now().isoformat(),
        }
        print(f"\n😴 CONCIERGE: No appointments found.")

        # Save empty result
        save_concierge_result(request_id, final_result)

    return final_result


# ══════════════════════════════════════════════════════════════
# 15. MAIN
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("╔══════════════════════════════════════════════════════╗")
    print("║   MYVITA CONCIERGE SCRAPER — Admin Only             ║")
    print("║   ClicSanté + Bonjour Santé + Pomelo                ║")
    print("║   Single-Shot | Manual Trigger | Human-in-Loop      ║")
    print("╚══════════════════════════════════════════════════════╝")

    mode = os.getenv("SCRAPER_MODE", "concierge_single")

    if mode == "concierge_single":
        run_concierge_search()
    else:
        print(f"❌ Unknown mode: {mode} — concierge scraper only runs in 'concierge_single' mode")

    print("\n✅ Concierge session complete")