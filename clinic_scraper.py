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

# ★ CLINIC BOOKING MAP — Primary lookup (Gemini-verified, 78 clinics)
CLINIC_BOOKING_MAP = {
    # ── Montreal East ──
    "GMF-R Cité Médicale Villeray": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "Centre Médical Mieux-Être (succursale Levasseur)": {
        "booking_url": "https://bonjour-sante.ca/uno/clinique/levasseur",
        "platform": "bonjour_sante"
    },
    "GMF A-R Centre médical Mieux-Être – Levasseur": {
        "booking_url": "https://bonjour-sante.ca/uno/clinique/levasseur",
        "platform": "bonjour_sante"
    },
    "Clinique Médico-Centre Mont-Royal": {
        "booking_url": "https://bonjour-sante.ca/uno/clinique/montroyal",
        "platform": "bonjour_sante"
    },
    "GMF Médi-Centre Chomedey": {
        "booking_url": "https://bonjour-sante.ca/uno/clinique/medicentrechomedey",
        "platform": "bonjour_sante"
    },
    "Polyclinique du cœur-de-l'île GMF-R Jarry-Lajeunesse": {
        "booking_url": "",
        "platform": "no_online_booking"
    },
    "CLSC du Plateau-Mont-Royal": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "CLSC de Dorval-Lachine": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "UnionMD - Clinique Médicale Privée Montreal": {
        "booking_url": "https://unionmd.ca/contact/",
        "platform": "other"
    },
    "CLSC de Saint-Henri (Montréal)": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "CLSC Métro (Montréal)": {
        "booking_url": "https://portal3.clicsante.ca/",
        "platform": "clicsante"
    },
    "CLSC de Hochelaga-Maisonneuve": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "Centre D'Urgence Saint-Laurent (GMF)": {
        "booking_url": "https://bonjour-sante.ca/uno/clinique/cusl",
        "platform": "bonjour_sante"
    },
    "GMF A-R Clinique médicale Angus (Montréal)": {
        "booking_url": "https://bonjour-sante.ca/uno/clinique/angus",
        "platform": "bonjour_sante"
    },

    # ── Montreal Central ──
    "GMF Clinique Médicale St-Denis (Montréal)": {
        "booking_url": "https://bonjour-sante.ca/uno/clinique/stdenis",
        "platform": "bonjour_sante"
    },

    # ── Anjou ──
    "GMF Centre Médical Mieux-Être (Succursale Anjou)": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "Centre médical Mieux-Être – Succursale Anjou": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },

    # ── Mieux-Être network ──
    "Centre Médical Mieux-Être - Lasalle": {
        "booking_url": "https://bonjour-sante.ca/uno/clinique/cmmelasalle",
        "platform": "bonjour_sante"
    },
    "Centre Médical Mieux-Être - Henri-Bourassa": {
        "booking_url": "https://bonjour-sante.ca/uno/clinique/cmmehenribourassa",
        "platform": "bonjour_sante"
    },
    "GMF A-R Centre médical Mieux-Être – St-Léonard": {
        "booking_url": "https://bonjour-sante.ca/uno/clinique/mieuxetre",
        "platform": "bonjour_sante"
    },

    # ── Laval ──
    "GMF Le Carrefour Médical (Laval)": {
        "booking_url": "https://bonjour-sante.ca/uno/clinique/lecarrefour",
        "platform": "bonjour_sante"
    },
    "GMF Clinique Médicale Sainte-Dorothée (Laval)": {
        "booking_url": "https://pomelo.health/cliniquemedicalesaintedorothee",
        "platform": "pomelo"
    },
    "GMF Polyclinique Concorde (Laval)": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "CLSC de Laval-des-Rapides": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "Super-Clinique Polyclinique Médicale Fabreville (GMF)": {
        "booking_url": "https://bonjour-sante.ca/uno/clinique/fabreville",
        "platform": "bonjour_sante"
    },
    "Clinique Médicale Saint-François (GMF)": {
        "booking_url": "https://bonjour-sante.ca/uno/clinique/stfrancois",
        "platform": "bonjour_sante"
    },
    "CLSC Idola-Saint-Jean": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "CLSC des Mille-Îles": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "CLSC de l'Ouest-de-l'Île": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "CLSC de Sainte-Rose": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "CLSC du Ruisseau-Papineau": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "GMF Centre Médical Laval": {
        "booking_url": "https://qc.pomelo.health/centremedicallaval",
        "platform": "pomelo"
    },

    # ── Longueuil / Rive-Sud ──
    "Clinique médicale privée Longueuil - Rive-Sud - UnionMD": {
        "booking_url": "https://bonjour-sante.ca/uno/clinique/unionmdlongueuil",
        "platform": "bonjour_sante"
    },
    "GMF-R Clinique Médicale Longueuil-Ouest": {
        "booking_url": "https://bonjour-sante.ca/uno/clinique/longueuilouest",
        "platform": "bonjour_sante"
    },
    "CLSC de Longueuil-Ouest (Rive-Sud)": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "GMF-U Charles-Le Moyne (Longueuil)": {
        "booking_url": "https://qc.pomelo.health/gmfucharleslemoyne",
        "platform": "pomelo"
    },

    # ── Brossard / Dix30 ──
    "GMF Dix30 (Clinique d'urgence avec rendez-vous Dix30 Brossard)": {
        "booking_url": "https://bonjour-sante.ca/uno/clinique/gmfdix30",
        "platform": "bonjour_sante"
    },
    "Clinique Sans Rendez-Vous Dix30 Brossard (GMF)": {
        "booking_url": "https://bonjour-sante.ca/uno/clinique/csansrendezvousdix30brossard",
        "platform": "bonjour_sante"
    },
    "GMF Samuel-de-Champlain": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "CLSC Samuel-de-Champlain": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "GMF Lapinière": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },

    # ── West Island ──
    "GMF Stillview (West Island - Pointe-Claire)": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "GMF Clinique Médicale Brunswick (West Island - Pointe-Claire)": {
        "booking_url": "https://pomelo.health/brunswickmedicalcenter",
        "platform": "pomelo"
    },
    "CLSC du Lac-Saint-Louis (West Island - Pointe-Claire)": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "CLSC de Pierrefonds (West Island)": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },

    # ── Terrebonne ──
    "GMF des Seigneurs (Terrebonne)": {
        "booking_url": "https://qc.pomelo.health/gmfdesseigneurs",
        "platform": "pomelo"
    },
    "GMF Clinique Médicale Terrebonne": {
        "booking_url": "https://bonjour-sante.ca/uno/clinique/cmterrebonne",
        "platform": "bonjour_sante"
    },

    # ── Repentigny / Lanaudière ──
    "GMF des Affluents (Repentigny)": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "GMF-U du Sud de Lanaudière (Repentigny)": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "GMF-U de Saint-Charles-Borromée (Lanaudière Joliette)": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "GMF L'Assomption": {
        "booking_url": "https://qc.pomelo.health/#/",
        "platform": "pomelo"
    },
    "Centre de Médecine Métabolique de Lanaudière (CMML)": {
        "booking_url": "https://qc.pomelo.health/cmml/portal#/patient-triage",
        "platform": "pomelo"
    },

    # ── Saint-Jérôme ──
    "GMF-R Clinique Médicale Saint-Jérôme": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "GMF du Grand Saint-Jérôme (Clinique Saint-Hippolyte)": {
        "booking_url": "https://bonjour-sante.ca/uno/clinique/santhippolyte",
        "platform": "bonjour_sante"
    },

    # ── Rosemère ──
    "GMF Clinique Médicale Rosemère": {
        "booking_url": "https://bonjour-sante.ca/uno/clinique/rosemere",
        "platform": "bonjour_sante"
    },

    # ── Vaudreuil-Dorion ──
    "GMF-R Vaudreuil-Dorion (Super-Clinique)": {
        "booking_url": "https://bonjour-sante.ca/uno/clinique/vaudreuildorion",
        "platform": "bonjour_sante"
    },

    # ── Saint-Eustache ──
    "GMF Clinique Médicale de la Gare (Saint-Eustache)": {
        "booking_url": "https://qc.pomelo.health/cliniquemedicaledelagare",
        "platform": "pomelo"
    },

    # ── Saint-Jean-sur-Richelieu ──
    "GMF Clinique Médicale Saint-Luc (Saint-Jean-sur-Richelieu)": {
        "booking_url": "https://qc.pomelo.health/cliniquemedicalesaintluc",
        "platform": "pomelo"
    },

    # ── Laurentides ──
    "GMF Clinique Médicale Lorraine (Laurentides)": {
        "booking_url": "https://bonjour-sante.ca/uno/clinique/cmlorraine",
        "platform": "bonjour_sante"
    },

    # ── Québec City ──
    "GMF-U de la Vieille-Capitale (Capitale-Nationale - Québec)": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "GMF La Cité Médicale de Québec (Capitale-Nationale)": {
        "booking_url": "https://bonjour-sante.ca/uno/clinique/lacitemedicalequebec",
        "platform": "bonjour_sante"
    },
    "GMF Clinique Médicale Val-Bélair (Québec)": {
        "booking_url": "https://qc.pomelo.health/cliniquemedicalevalbelair",
        "platform": "pomelo"
    },

    # ── Trois-Rivières ──
    "GMF-U de Trois-Rivières (Mauricie/Centre-du-Québec)": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "GMF du Cap (Trois-Rivières)": {
        "booking_url": "https://bonjour-sante.ca/uno/clinique/gmfducap",
        "platform": "bonjour_sante"
    },
    "GMF Clinique Médicale des Trois-Rivières": {
        "booking_url": "https://qc.pomelo.health/cliniquemedicaledestroisrivieres",
        "platform": "pomelo"
    },

    # ── Sherbrooke / Estrie ──
    "GMF-U de Sherbrooke (Estrie - CHUS)": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "GMF Clinique Médicale des Cantons (Estrie)": {
        "booking_url": "https://bonjour-sante.ca/uno/clinique/cmdescantons",
        "platform": "bonjour_sante"
    },

    # ── Saguenay ──
    "GMF-U de Chicoutimi (Saguenay–Lac-Saint-Jean)": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "Ma Clinique Zone Santé (GMF Alma - Saguenay)": {
        "booking_url": "https://pomelo.health/macliniquezonesante",
        "platform": "pomelo"
    },

    # ── Gatineau / Outaouais ──
    "GMF-R Clinique Médicale Gatineau (Outaouais)": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "GMF Clinique Médicale Hull (Outaouais)": {
        "booking_url": "https://bonjour-sante.ca/uno/clinique/cmhull",
        "platform": "bonjour_sante"
    },
    "GMF Clinique Médicale St-Alexandre (Gatineau)": {
        "booking_url": "https://qc.pomelo.health/cliniquemedstalexandre",
        "platform": "pomelo"
    },

    # ── Bas-Saint-Laurent ──
    "GMF-U de Rimouski (Bas-Saint-Laurent)": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
    "GMF du Grand-Portage (Rivière-du-Loup)": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },

    # ── Abitibi ──
    "GMF-U de Rouyn-Noranda (Abitibi-Témiscamingue)": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },

    # ── Côte-Nord ──
    "CLSC de Sept-Îles (Côte-Nord)": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },

    # ── Gaspésie ──
    "CLSC de Gaspé (Gaspésie-Îles-de-la-Madeleine)": {
        "booking_url": "https://rvsq.gouv.qc.ca/prendrerendezvous",
        "platform": "clicsante"
    },
}

GOOGLE_MAPS_PROXY = "https://us-central1-myvita-app-c5ecd.cloudfunctions.net/googleMapsProxy"
RADIUS_TIERS = [15, 30, 50]
MAX_DAYS_AHEAD = 10

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
# 2. KILL SWITCH
# ══════════════════════════════════════════════════════════════

class KillSwitch:
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
    fsa = postal_code[:3].upper()
    return POSTAL_CODES.get(fsa, f"zone_{fsa}")

def get_user_token():
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
# 8. CLINIC DISCOVERY
# ══════════════════════════════════════════════════════════════

def geocode_postal_code(postal_code: str) -> dict:
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


def discover_clinics_near(postal_code: str, radius_km: int = 15) -> list:
    print(f"\n🔍 Discovering clinics near {postal_code} ({radius_km}km radius)...")

    cached = get_clinic_cache(postal_code)
    if cached:
        return cached

    coords = geocode_postal_code(postal_code)
    if not coords:
        print("❌ Could not geocode postal code")
        return []

    lat, lng = coords["lat"], coords["lng"]
    all_clinics = []

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
                        "radius": radius_km * 1000,
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

                clinic_keywords = [
                    "clinique", "clinic", "gmf", "clsc",
                    "médical", "medical", "centre de santé",
                    "médecin", "doctor", "santé", "health",
                    "hôpital", "hospital",
                ]
                if not any(kw in name_lower for kw in clinic_keywords):
                    continue

                place_id = place.get("place_id")
                if any(c.get("place_id") == place_id for c in all_clinics):
                    continue

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

    unique_clinics = []
    seen_names = set()
    for clinic in all_clinics:
        name_key = clinic["name"].lower().strip()[:30]
        if name_key not in seen_names:
            seen_names.add(name_key)
            unique_clinics.append(clinic)

    print(f"\n✅ Discovered {len(unique_clinics)} unique free clinics")
    if unique_clinics:
        save_clinic_cache(postal_code, unique_clinics)
    return unique_clinics


# ══════════════════════════════════════════════════════════════
# 9. PLATFORM DETECTION — Map-first, website fallback
# ══════════════════════════════════════════════════════════════

BOOKING_LINK_SELECTORS = [
    "a[href*='pomelo.health']",
    "a[href*='bonjour-sante.ca']",
    "a[href*='clicsante.ca']",
    "a[href*='rvsq.gouv.qc.ca']",
    "a[href*='rendez-vous']",
    "a[href*='rdv']",
    "a[href*='booking']",
    "a[href*='appointment']",
    "a[href*='reservation']",
    "a:has-text('Prendre rendez-vous')",
    "a:has-text('Prendre RDV')",
    "a:has-text('Rendez-vous en ligne')",
    "a:has-text('Book appointment')",
    "a:has-text('Book online')",
    "button:has-text('Prendre rendez-vous')",
    "button:has-text('Rendez-vous en ligne')",
    "button:has-text('Book appointment')",
    "a:has-text('Nous joindre')",
    "a:has-text('Contact')",
    "a:has-text('Contactez-nous')",
]


def detect_platform_from_url(url: str) -> str:
    url_lower = url.lower()
    if "pomelo.health" in url_lower or "telus" in url_lower:
        return "pomelo"
    elif "bonjour-sante.ca" in url_lower:
        return "bonjour_sante"
    elif "clicsante.ca" in url_lower or "rvsq.gouv.qc.ca" in url_lower:
        return "clicsante"
    else:
        return "unknown"


def find_booking_link(page, clinic_name: str) -> dict:
    print(f"   🔗 Searching booking link on {clinic_name}...")
    for selector in BOOKING_LINK_SELECTORS:
        if KillSwitch.is_active():
            return {"platform": "kill_switch", "booking_url": ""}
        try:
            element = page.locator(selector).first
            if element.count() == 0 or not element.is_visible():
                continue
            href = element.get_attribute("href") or ""
            print(f"      Trying: {selector[:60]}... → href={href[:80]}")
            if any(p in href.lower() for p in ["pomelo.health", "bonjour-sante.ca", "clicsante.ca", "rvsq"]):
                platform = detect_platform_from_url(href)
                print(f"      🎯 Direct platform link found: {platform}")
                return {"platform": platform, "booking_url": href}
            element.click(timeout=5000)
            human_delay(2000, 4000)
            if len(page.context.pages) > 1:
                new_page = page.context.pages[-1]
                new_page.bring_to_front()
                human_delay(1500, 2500)
                platform = detect_platform_from_url(new_page.url)
                booking_url = new_page.url
                new_page.close()
                return {"platform": platform, "booking_url": booking_url}
            platform = detect_platform_from_url(page.url)
            if platform != "unknown":
                return {"platform": platform, "booking_url": page.url}
            try:
                page.go_back()
                human_delay(500, 1000)
            except:
                pass
        except:
            continue

    print(f"      ⚠️ No booking link found. Trying Contact page...")
    try:
        contact_link = page.locator("a:has-text('Nous joindre'), a:has-text('Contact')").first
        if contact_link.count() > 0:
            contact_link.click(timeout=5000)
            human_delay(2000, 3000)
            for selector in BOOKING_LINK_SELECTORS[:6]:
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
    clinic_name = clinic.get("name", "")

    if KillSwitch.is_active():
        return {**clinic, "platform": "kill_switch", "booking_url": ""}

    # ★ CHECK MAP FIRST
    if clinic_name in CLINIC_BOOKING_MAP:
        mapped = CLINIC_BOOKING_MAP[clinic_name]
        print(f"   📋 Mapped: {clinic_name} → {mapped['platform']}")
        return {
            **clinic,
            "platform": mapped["platform"],
            "booking_url": mapped["booking_url"],
        }

    # ★ FALLBACK: Visit website
    headless = os.getenv("HEADLESS", "true").lower() != "false"
    website = clinic.get("website")
    if not website:
        print(f"   ⛔ {clinic_name}: No website — skipping")
        return {**clinic, "platform": "no_website", "booking_url": ""}

    print(f"\n   🏥 Visiting: {clinic_name}")
    print(f"      🌐 {website}")

    with sync_playwright() as p:
        browser, context = launch_stealth_browser(p, headless=headless)
        page = context.new_page()
        try:
            page.goto(website, wait_until="domcontentloaded", timeout=30000)
            human_delay(2000, 3000)
            try:
                page.keyboard.press("Escape")
                human_delay(300, 500)
            except:
                pass
            result = find_booking_link(page, clinic_name)
            return {
                **clinic,
                "platform": result["platform"],
                "booking_url": result["booking_url"],
            }
        except Exception as e:
            print(f"      🚨 Error visiting {clinic_name}: {e}")
            return {**clinic, "platform": "error", "booking_url": ""}
        finally:
            browser.close()


# ══════════════════════════════════════════════════════════════
# 10. POMELO BY TELUS — 4-STEP FORM HANDLER
# ══════════════════════════════════════════════════════════════

def fill_pomelo_page1_identification(page, user: dict):
    print("      📝 Page 1 — Identification...")
    if KillSwitch.is_active():
        return False
    birth_date = user.get("birth_date", "1965-01-15")
    birth_year = birth_date.split("-")[0]
    try:
        page.locator("input[name='firstName'], input[aria-label*='Prénom'], #firstName").first.fill(user["first_name"])
        human_delay(300, 600)
    except: pass
    try:
        page.locator("input[name='lastName'], input[aria-label*='Nom de famille'], #lastName").first.fill(user["last_name"])
        human_delay(300, 600)
    except: pass
    try:
        page.locator("input[name*='ramq'], input[name*='assurance'], input[aria-label*='RAMQ']").first.fill(user["ramq"])
        human_delay(300, 600)
    except: pass
    try:
        page.locator("input[name*='sequence'], input[name*='seq']").first.fill(user["ramq_seq"])
        human_delay(300, 600)
    except: pass
    try:
        page.locator("input[name*='birth'], input[name*='year'], input[name*='annee']").first.fill(birth_year)
        human_delay(300, 600)
    except: pass
    try:
        if user["sex"].upper() == "M":
            page.locator("input[value='M'], input[value='male'], label:has-text('Masculin')").first.click()
        else:
            page.locator("input[value='F'], input[value='female'], label:has-text('Féminin')").first.click()
        human_delay(500, 800)
    except:
        try:
            if user["sex"].upper() == "M":
                page.get_by_text(re.compile(r"Masculin|Homme", re.I)).first.click()
            else:
                page.get_by_text(re.compile(r"Féminin|Femme", re.I)).first.click()
            human_delay(500, 800)
        except: pass
    try:
        page.get_by_role("button", name=re.compile(r"Continuer|Suivant|Next|Submit", re.I)).first.click()
        human_delay(2000, 3000)
    except: pass
    return True


def fill_pomelo_page2_contact(page, user: dict):
    print("      📧 Page 2 — Contact...")
    if KillSwitch.is_active():
        return False
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
    print("      ✅ Page 3 — Consent...")
    if KillSwitch.is_active():
        return False
    try:
        page.locator("input[type='checkbox']").first.check()
        human_delay(500, 1000)
    except:
        try:
            page.get_by_label(re.compile(r"J'accepte|I accept|Consentement", re.I)).first.check()
            human_delay(500, 1000)
        except: pass
    try:
        page.get_by_role("button", name=re.compile(r"Continuer|Suivant|Next", re.I)).first.click()
        human_delay(2000, 3000)
    except: pass
    return True


def fill_pomelo_page4_search(page, postal_code: str):
    print(f"      🔍 Page 4 — Search (postal: {postal_code})...")
    if KillSwitch.is_active():
        return False
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
    print("      📅 Checking Pomelo calendar...")
    if KillSwitch.is_active():
        return False, "Kill switch"
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
    except: pass
    return has_positive, "positive indicators" if has_positive else "no clear slots"


def scrape_pomelo_clinic(clinic: dict, user: dict) -> dict:
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
# 11. BONJOUR SANTÉ — 2-STEP FORM HANDLER
# ══════════════════════════════════════════════════════════════

def fill_bonjoursante_page1(page, user: dict):
    print("      📝 Bonjour Santé — Page 1 (RAMQ + Postal)...")
    if KillSwitch.is_active():
        return False
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
    print("      📝 Bonjour Santé — Page 2 (Patient Info)...")
    if KillSwitch.is_active():
        return False
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
    print("      📅 Checking Bonjour Santé results...")
    if KillSwitch.is_active():
        return False, "Kill switch"
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
    platform = clinic.get("platform", "unknown")
    if platform == "kill_switch":
        return {"found": False, "details": "Kill switch", "booking_url": ""}
    if platform == "pomelo":
        return scrape_pomelo_clinic(clinic, user)
    elif platform == "bonjour_sante":
        return scrape_bonjoursante_clinic(clinic, user)
    elif platform == "clicsante":
        print(f"   ⏭️ {clinic['name']}: ClicSanté/RVSQ — skipped (free tier)")
        return {"found": False, "details": "clicsante_skipped", "booking_url": ""}
    elif platform in ("unknown", "error", "no_website", "other", "no_online_booking"):
        print(f"   ⏭️ {clinic['name']}: {platform} — skipped")
        return {"found": False, "details": platform, "booking_url": ""}
    else:
        print(f"   ⏭️ {clinic['name']}: Unknown platform '{platform}' — skipped")
        return {"found": False, "details": f"unknown_platform_{platform}", "booking_url": ""}


# ══════════════════════════════════════════════════════════════
# 13. MAIN SEARCH ORCHESTRATOR
# ══════════════════════════════════════════════════════════════

def search_clinics_in_zone(user: dict, radius_km: int) -> bool:
    postal = user["postal_code"]
    clinics = discover_clinics_near(postal, radius_km)
    if not clinics:
        print(f"   No clinics found in {radius_km}km radius")
        return False

    detected_clinics = []
    for clinic in clinics:
        if KillSwitch.is_active():
            return True
        result = visit_clinic_and_detect_platform(clinic)
        detected_clinics.append(result)

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
            send_notification(clinic["name"], postal, clinic.get("platform", "unknown"), result["booking_url"])
            save_availability(clinic["name"], postal, clinic.get("platform", "unknown"), True, result["booking_url"], result["details"])
            return True
        else:
            save_availability(clinic["name"], postal, clinic.get("platform", "unknown"), False, "", result["details"])
    return False


def run_single_search(user_postal: str = None):
    if user_postal is None:
        user_postal = os.getenv("POSTAL_CODE", "H1Y3H1")
    user = get_user_data()
    user["postal_code"] = user_postal
    KillSwitch.reset()
    max_date = (datetime.now() + timedelta(days=MAX_DAYS_AHEAD)).strftime("%Y-%m-%d")

    print(f"\n{'='*60}")
    print(f"🚀 MYVITA CLINIC SCRAPER — Manual Search (User Triggered)")
    print(f"   Postal Code: {user_postal}")
    print(f"   Max Date: {max_date} (under {MAX_DAYS_AHEAD} days)")
    print(f"   Tiers: {RADIUS_TIERS} km")
    print(f"   Platforms: Pomelo + Bonjour Santé (map-first, 78 clinics)")
    print(f"   ClicSanté/RVSQ: SKIPPED")
    print(f"   Mode: SINGLE RUN — no loop, no cooldown")
    print(f"{'='*60}\n")

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
        print(f"\n😴 No appointments found.")
        print(f"   Search complete — waiting for next user trigger.")
    return None


# ══════════════════════════════════════════════════════════════
# 14. MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("╔══════════════════════════════════════════════════════╗")
    print("║        MYVITA CLINIC APPOINTMENT SCRAPER            ║")
    print("║        Free Tier — Pomelo + Bonjour Santé           ║")
    print("║        Map-First: 78 clinics verified               ║")
    print("║        MODE: Manual Trigger (User Request)          ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()
    print("📋 To insert your info, set these environment variables:")
    print("   USER_FIRST_NAME   USER_LAST_NAME   USER_RAMQ")
    print("   USER_RAMQ_SEQ     USER_BIRTH_DATE  USER_SEX")
    print("   USER_EMAIL        USER_PHONE       POSTAL_CODE")
    print("   USER_LANGUAGE")
    print()
    print("🔔 This bot runs ONCE per trigger — no automatic loops.")
    print()

    postal = os.getenv("POSTAL_CODE", "H1Y3H1")
    run_single_search(user_postal=postal)

    print("\n✅ MyVita Clinic Scraper session complete — waiting for next trigger")
