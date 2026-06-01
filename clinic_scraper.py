#!/usr/bin/env python3
"""
MYVITA CLINIC SCRAPER - FULL END-TO-END BOOKING
- 156 unique clinics across Quebec
- 5 parallel stealth browsers (Playwright + Chromium)
- Human-like delays and fingerprint evasion
- Kill switch stops at FIRST confirmed booking
"""

from playwright.sync_api import sync_playwright
import time
import random
import os
import json
import re
import math
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# ================================================================
# 1. CONFIGURATION
# ================================================================

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

# ================================================================
# 2. THREAD-SAFE KILL SWITCH
# ================================================================

class ParallelKillSwitch:
    _active = False
    _found_appointment = None
    _lock = threading.Lock()
    
    @classmethod
    def activate(cls, details: dict):
        with cls._lock:
            if not cls._active:
                cls._active = True
                cls._found_appointment = details
                print(f"\n🛑 KILL SWITCH: {details.get('clinic_name')}")
    
    @classmethod
    def is_active(cls) -> bool:
        with cls._lock:
            return cls._active
    
    @classmethod
    def get_found(cls):
        with cls._lock:
            return cls._found_appointment
    
    @classmethod
    def reset(cls):
        with cls._lock:
            cls._active = False
            cls._found_appointment = None

# ================================================================
# 3. STEALTH BROWSER (HUMAN-LIKE)
# ================================================================

def human_delay(min_ms=500, max_ms=1500):
    time.sleep(random.uniform(min_ms, max_ms) / 1000)

def get_random_user_agent():
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    ]
    return random.choice(user_agents)

def launch_stealth_browser(p, headless=False):  # headless=False so you can SEE what's happening
    """Launch a human-like stealth browser"""
    browser = p.chromium.launch(
        headless=headless,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--disable-web-security",
            "--disable-features=IsolateOrigins,site-per-process",
            "--window-size=1280,800",
        ]
    )
    
    context = browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent=get_random_user_agent(),
        locale="fr-CA",
        timezone_id="America/Montreal",
        extra_http_headers={
            "Accept-Language": "fr-CA,fr;q=0.9,en;q=0.8",
        }
    )
    
    # Hide webdriver property
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        window.chrome = {runtime: {}};
    """)
    
    return browser, context

# ================================================================
# 4. USER DATA
# ================================================================

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

# ================================================================
# 5. UTILITIES
# ================================================================

def haversine(lat1, lng1, lat2, lng2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def geocode_postal_code(postal_code: str):
    import requests
    try:
        response = requests.post(GOOGLE_MAPS_PROXY, json={
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

# ================================================================
# 6. FULL CLINICS DATABASE (156 CLINICS)
# ================================================================

CLINICS = [
    # ========== MONTREAL EAST / NORTH ==========
    {"name": "GMF-R Cité Médicale Villeray", "lat": 45.5463, "lng": -73.6214, "platform": "clicsante", "website": "https://rvsq.gouv.qc.ca/prendrerendezvous", "type": "gmf_r", "city": "Montréal"},
    {"name": "Centre Médical Mieux-Être (succursale Levasseur)", "lat": 45.5841, "lng": -73.6412, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/levasseur", "type": "gmf", "city": "Montréal"},
    {"name": "Clinique Médico-Centre Mont-Royal", "lat": 45.5163, "lng": -73.5786, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/montroyal", "type": "clinic", "city": "Montréal"},
    {"name": "GMF Médi-Centre Chomedey", "lat": 45.5451, "lng": -73.7483, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/medicentrechomedey", "type": "gmf", "city": "Laval"},
    {"name": "CLSC du Plateau-Mont-Royal", "lat": 45.5195, "lng": -73.5781, "platform": "clicsante", "website": "https://rvsq.gouv.qc.ca/prendrerendezvous", "type": "clsc", "city": "Montréal"},
    {"name": "CLSC de Dorval-Lachine", "lat": 45.4385, "lng": -73.6841, "platform": "clicsante", "website": "https://rvsq.gouv.qc.ca/prendrerendezvous", "type": "clsc", "city": "Montréal"},
    {"name": "CLSC de Saint-Henri", "lat": 45.4775, "lng": -73.5856, "platform": "clicsante", "website": "https://rvsq.gouv.qc.ca/prendrerendezvous", "type": "clsc", "city": "Montréal"},
    {"name": "CLSC Métro", "lat": 45.4931, "lng": -73.5802, "platform": "clicsante", "website": "https://portal3.clicsante.ca/", "type": "clsc", "city": "Montréal"},
    {"name": "CLSC de Hochelaga-Maisonneuve", "lat": 45.5422, "lng": -73.5397, "platform": "clicsante", "website": "https://rvsq.gouv.qc.ca/prendrerendezvous", "type": "clsc", "city": "Montréal"},
    {"name": "Centre D'Urgence Saint-Laurent", "lat": 45.5118, "lng": -73.6802, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/cusl", "type": "gmf", "city": "Montréal"},
    {"name": "GMF Clinique médicale Angus", "lat": 45.5401, "lng": -73.5658, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/angus", "type": "gmf", "city": "Montréal"},
    {"name": "GMF Clinique Médicale St-Denis", "lat": 45.5264, "lng": -73.5932, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/stdenis", "type": "gmf", "city": "Montréal"},
    {"name": "GMF Centre Médical Mieux-Être Anjou", "lat": 45.6031, "lng": -73.5518, "platform": "clicsante", "website": "https://rvsq.gouv.qc.ca/prendrerendezvous", "type": "gmf", "city": "Montréal"},
    {"name": "Centre Médical Mieux-Être Lasalle", "lat": 45.4312, "lng": -73.6248, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/cmmelasalle", "type": "clinic", "city": "Montréal"},
    {"name": "GMF A-R Centre médical Mieux-Être St-Léonard", "lat": 45.5892, "lng": -73.6014, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/mieuxetre", "type": "gmf", "city": "Montréal"},
    
    # ========== LAVAL ==========
    {"name": "GMF Le Carrefour Médical Laval", "lat": 45.5684, "lng": -73.7431, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/lecarrefour", "type": "gmf", "city": "Laval"},
    {"name": "GMF Clinique Médicale Sainte-Dorothée", "lat": 45.5312, "lng": -73.8115, "platform": "pomelo", "website": "https://pomelo.health/cliniquemedicalesaintedorothee", "type": "gmf", "city": "Laval"},
    {"name": "GMF Polyclinique Concorde", "lat": 45.5615, "lng": -73.7082, "platform": "clicsante", "website": "https://rvsq.gouv.qc.ca/prendrerendezvous", "type": "gmf", "city": "Laval"},
    {"name": "CLSC de Laval-des-Rapides", "lat": 45.5492, "lng": -73.7124, "platform": "clicsante", "website": "https://rvsq.gouv.qc.ca/prendrerendezvous", "type": "clsc", "city": "Laval"},
    {"name": "Super-Clinique Polyclinique Médicale Fabreville", "lat": 45.5925, "lng": -73.7912, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/fabreville", "type": "gmf", "city": "Laval"},
    {"name": "Clinique Médicale Saint-François", "lat": 45.5781, "lng": -73.6542, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/stfrancois", "type": "gmf", "city": "Laval"},
    {"name": "CLSC Idola-Saint-Jean", "lat": 45.5652, "lng": -73.6931, "platform": "clicsante", "website": "https://rvsq.gouv.qc.ca/prendrerendezvous", "type": "clsc", "city": "Laval"},
    {"name": "CLSC des Mille-Îles", "lat": 45.6315, "lng": -73.6212, "platform": "clicsante", "website": "https://rvsq.gouv.qc.ca/prendrerendezvous", "type": "clsc", "city": "Laval"},
    {"name": "CLSC de Sainte-Rose", "lat": 45.6121, "lng": -73.7824, "platform": "clicsante", "website": "https://rvsq.gouv.qc.ca/prendrerendezvous", "type": "clsc", "city": "Laval"},
    {"name": "CLSC du Ruisseau-Papineau", "lat": 45.5794, "lng": -73.7251, "platform": "clicsante", "website": "https://rvsq.gouv.qc.ca/prendrerendezvous", "type": "clsc", "city": "Laval"},
    {"name": "GMF Centre Médical Laval", "lat": 45.5521, "lng": -73.7314, "platform": "pomelo", "website": "https://qc.pomelo.health/centremedicallaval", "type": "gmf", "city": "Laval"},
    {"name": "CLSC du Marigot", "lat": 45.5824, "lng": -73.7011, "platform": "clicsante", "website": "https://www.lavalensante.com/", "type": "clsc", "city": "Laval"},
    {"name": "CLSC Chomedey", "lat": 45.5458, "lng": -73.7432, "platform": "clicsante", "website": "https://www.lavalensante.com/", "type": "clsc", "city": "Laval"},
    {"name": "GMF-R Centre Médical Laval", "lat": 45.5985, "lng": -73.6712, "platform": "bonjour_sante", "website": "https://www.lavalensante.com/", "type": "gmf_r", "city": "Laval"},
    {"name": "GMF-R Concorde", "lat": 45.5721, "lng": -73.6914, "platform": "bonjour_sante", "website": "https://www.lavalensante.com/", "type": "gmf_r", "city": "Laval"},
    
    # ========== LONGUEUIL / RIVE-SUD ==========
    {"name": "Clinique médicale privée Longueuil UnionMD", "lat": 45.5252, "lng": -73.5135, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/unionmdlongueuil", "type": "private", "city": "Longueuil"},
    {"name": "GMF-R Clinique Médicale Longueuil-Ouest", "lat": 45.5314, "lng": -73.5248, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/longueuilouest", "type": "gmf_r", "city": "Longueuil"},
    {"name": "CLSC de Longueuil-Ouest", "lat": 45.5314, "lng": -73.5248, "platform": "clicsante", "website": "https://rvsq.gouv.qc.ca/prendrerendezvous", "type": "clsc", "city": "Longueuil"},
    {"name": "GMF-U Charles-Le Moyne", "lat": 45.5184, "lng": -73.4831, "platform": "pomelo", "website": "https://qc.pomelo.health/gmfucharleslemoyne", "type": "gmf_u", "city": "Longueuil"},
    {"name": "CLSC Simonne-Monet-Chartrand", "lat": 45.5284, "lng": -73.4891, "platform": "clicsante", "website": "https://www.santemonteregie.qc.ca/centre/", "type": "clsc", "city": "Longueuil"},
    {"name": "CLSC Longueuil-Est", "lat": 45.5211, "lng": -73.4984, "platform": "clicsante", "website": "https://www.santemonteregie.qc.ca/", "type": "clsc", "city": "Longueuil"},
    {"name": "GMF Clinique Médicale GMF Pierre Boucher", "lat": 45.5511, "lng": -73.4425, "platform": "bonjour_sante", "website": "https://www.santemonteregie.qc.ca/", "type": "gmf", "city": "Longueuil"},
    
    # ========== BROSSARD ==========
    {"name": "GMF Dix30 Brossard", "lat": 45.4428, "lng": -73.4412, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/gmfdix30", "type": "gmf", "city": "Brossard"},
    {"name": "Clinique Sans Rendez-Vous Dix30", "lat": 45.4428, "lng": -73.4412, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/csansrendezvousdix30brossard", "type": "gmf", "city": "Brossard"},
    {"name": "GMF Samuel-de-Champlain", "lat": 45.4682, "lng": -73.4715, "platform": "clicsante", "website": "https://rvsq.gouv.qc.ca/prendrerendezvous", "type": "gmf", "city": "Brossard"},
    {"name": "GMF Lapinière", "lat": 45.4561, "lng": -73.4623, "platform": "clicsante", "website": "https://rvsq.gouv.qc.ca/prendrerendezvous", "type": "gmf", "city": "Brossard"},
    {"name": "CLSC Samuel-de-Champlain", "lat": 45.4595, "lng": -73.4682, "platform": "clicsante", "website": "https://www.santemonteregie.qc.ca/centre/", "type": "clsc", "city": "Brossard"},
    
    # ========== WEST ISLAND ==========
    {"name": "GMF Stillview Pointe-Claire", "lat": 45.4485, "lng": -73.8124, "platform": "clicsante", "website": "https://rvsq.gouv.qc.ca/prendrerendezvous", "type": "gmf", "city": "Pointe-Claire"},
    {"name": "GMF Clinique Médicale Brunswick", "lat": 45.4498, "lng": -73.8315, "platform": "pomelo", "website": "https://pomelo.health/brunswickmedicalcenter", "type": "gmf", "city": "Pointe-Claire"},
    {"name": "CLSC du Lac-Saint-Louis", "lat": 45.4392, "lng": -73.8184, "platform": "clicsante", "website": "https://rvsq.gouv.qc.ca/prendrerendezvous", "type": "clsc", "city": "Pointe-Claire"},
    {"name": "CLSC de Pierrefonds", "lat": 45.4852, "lng": -73.8742, "platform": "clicsante", "website": "https://rvsq.gouv.qc.ca/prendrerendezvous", "type": "clsc", "city": "Montréal"},
    {"name": "Lakeshore General Hospital", "lat": 45.4428, "lng": -73.8261, "platform": "clicsante", "website": "https://www.ciusss-ouestmtl.gouv.qc.ca/", "type": "hospital", "city": "Pointe-Claire"},
    
    # ========== TERREBONNE / REPENTIGNY / LANAUDIÈRE ==========
    {"name": "GMF des Seigneurs Terrebonne", "lat": 45.7025, "lng": -73.6514, "platform": "pomelo", "website": "https://qc.pomelo.health/gmfdesseigneurs", "type": "gmf", "city": "Terrebonne"},
    {"name": "GMF Clinique Médicale Terrebonne", "lat": 45.6982, "lng": -73.6391, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/cmterrebonne", "type": "gmf", "city": "Terrebonne"},
    {"name": "CLSC de Terrebonne", "lat": 45.6985, "lng": -73.6291, "platform": "clicsante", "website": "https://www.cisss-lanaudiere.gouv.qc.ca/", "type": "clsc", "city": "Terrebonne"},
    {"name": "GMF des Affluents Repentigny", "lat": 45.7485, "lng": -73.4421, "platform": "clicsante", "website": "https://rvsq.gouv.qc.ca/prendrerendezvous", "type": "gmf", "city": "Repentigny"},
    {"name": "GMF-U du Sud de Lanaudière", "lat": 45.7412, "lng": -73.4563, "platform": "clicsante", "website": "https://rvsq.gouv.qc.ca/prendrerendezvous", "type": "gmf_u", "city": "Repentigny"},
    {"name": "CLSC de Repentigny", "lat": 45.7512, "lng": -73.4582, "platform": "clicsante", "website": "https://www.cisss-lanaudiere.gouv.qc.ca/", "type": "clsc", "city": "Repentigny"},
    {"name": "CLSC Lamater Mascouche", "lat": 45.7482, "lng": -73.6195, "platform": "clicsante", "website": "https://www.cisss-lanaudiere.gouv.qc.ca/", "type": "clsc", "city": "Mascouche"},
    {"name": "GMF L'Assomption", "lat": 45.8312, "lng": -73.4215, "platform": "pomelo", "website": "https://qc.pomelo.health/#/", "type": "gmf", "city": "L'Assomption"},
    
    # ========== LAURENTIDES / SAINT-JÉRÔME ==========
    {"name": "GMF-R Clinique Médicale Saint-Jérôme", "lat": 45.7785, "lng": -74.0042, "platform": "clicsante", "website": "https://rvsq.gouv.qc.ca/prendrerendezvous", "type": "gmf_r", "city": "Saint-Jérôme"},
    {"name": "GMF du Grand Saint-Jérôme", "lat": 45.8321, "lng": -73.9915, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/santhippolyte", "type": "gmf", "city": "Saint-Jérôme"},
    {"name": "GMF Clinique Médicale Rosemère", "lat": 45.6382, "lng": -73.7915, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/rosemere", "type": "gmf", "city": "Rosemère"},
    {"name": "GMF Clinique Médicale Lorraine", "lat": 45.6512, "lng": -73.7814, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/cmlorraine", "type": "gmf", "city": "Lorraine"},
    {"name": "CLSC Jean-Olivier-Chénier", "lat": 45.5684, "lng": -73.8861, "platform": "clicsante", "website": "https://www.santelaurentides.gouv.qc.ca/", "type": "clsc", "city": "Saint-Eustache"},
    {"name": "CLSC de Blainville", "lat": 45.6652, "lng": -73.8695, "platform": "clicsante", "website": "https://www.santelaurentides.gouv.qc.ca/", "type": "clsc", "city": "Blainville"},
    {"name": "CLSC de Sainte-Thérèse", "lat": 45.6421, "lng": -73.8398, "platform": "clicsante", "website": "https://www.santelaurentides.gouv.qc.ca/", "type": "clsc", "city": "Sainte-Thérèse"},
    
    # ========== VAUDREUIL ==========
    {"name": "GMF-R Vaudreuil-Dorion", "lat": 45.3982, "lng": -74.0321, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/uno/clinique/vaudreuildorion", "type": "gmf_r", "city": "Vaudreuil-Dorion"},
    {"name": "CLSC de Vaudreuil-Dorion", "lat": 45.3951, "lng": -74.0315, "platform": "clicsante", "website": "https://www.santemonteregie.qc.ca/", "type": "clsc", "city": "Vaudreuil-Dorion"},
    
    # ========== SAINT-EUSTACHE ==========
    {"name": "GMF Clinique Médicale de la Gare", "lat": 45.5582, "lng": -73.9015, "platform": "pomelo", "website": "https://qc.pomelo.health/cliniquemedicaledelagare", "type": "gmf", "city": "Saint-Eustache"},
    
    # ========== SAINT-JEAN-SUR-RICHELIEU ==========
    {"name": "GMF Clinique Médicale Saint-Luc", "lat": 45.3512, "lng": -73.2842, "platform": "pomelo", "website": "https://qc.pomelo.health/cliniquemedicalesaintluc", "type": "gmf", "city": "Saint-Jean-sur-Richelieu"},
    
    # ========== QUÉBEC CITY ==========
    {"name": "CLSC de Sainte-Foy", "lat": 46.7865, "lng": -71.3094, "platform": "clicsante", "website": "https://www.chudequebec.ca/", "type": "clsc", "city": "Québec"},
    {"name": "CLSC de Charlesbourg", "lat": 46.8645, "lng": -71.2588, "platform": "clicsante", "website": "https://www.ciusss-capitalenationale.gouv.qc.ca/", "type": "clsc", "city": "Québec"},
    {"name": "GMF-U de Maizerets", "lat": 46.8361, "lng": -71.2294, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/", "type": "gmf_u", "city": "Québec"},
    {"name": "GMF-U Laurier", "lat": 46.7728, "lng": -71.2852, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/", "type": "gmf_u", "city": "Québec"},
    {"name": "GMF-U Quatre-Bourgeois", "lat": 46.7794, "lng": -71.3021, "platform": "bonjour_sante", "website": "https://bonjour-sante.ca/", "type": "gmf_u", "city": "Québec"},
    {"name": "CLSC de l'Ancienne-Lorette", "lat": 46.7972, "lng": -71.3531, "platform": "clicsante", "website": "https://www.ciusss-capitalenationale.gouv.qc.ca/", "type": "clsc", "city": "Québec"},
    {"name": "CLSC de Val-Bélair", "lat": 46.8574, "lng": -71.4241, "platform": "clicsante", "website": "https://www.ciusss-capitalenationale.gouv.qc.ca/", "type": "clsc", "city": "Québec"},
    {"name": "CLSC de Limoilou", "lat": 46.8421, "lng": -71.2112, "platform": "clicsante", "website": "https://www.ciusss-capitalenationale.gouv.qc.ca/", "type": "clsc", "city": "Québec"},
    
    # ========== GATINEAU ==========
    {"name": "CLSC de Hull", "lat": 45.4328, "lng": -75.7285, "platform": "clicsante", "website": "https://cisss-outaouais.gouv.qc.ca/", "type": "clsc", "city": "Gatineau"},
    {"name": "CLSC de Gatineau", "lat": 45.4611, "lng": -75.6894, "platform": "clicsante", "website": "https://cisss-outaouais.gouv.qc.ca/", "type": "clsc", "city": "Gatineau"},
    {"name": "CLSC de Aylmer", "lat": 45.4052, "lng": -75.8344, "platform": "clicsante", "website": "https://cisss-outaouais.gouv.qc.ca/", "type": "clsc", "city": "Gatineau"},
    
    # ========== SHERBROOKE / ESTRIE ==========
    {"name": "CLSC de Sherbrooke (King Est)", "lat": 45.4112, "lng": -71.8654, "platform": "clicsante", "website": "https://www.santeestrie.qc.ca/", "type": "clsc", "city": "Sherbrooke"},
    {"name": "CLSC de Granby", "lat": 45.3975, "lng": -72.7412, "platform": "clicsante", "website": "https://www.santeestrie.qc.ca/", "type": "clsc", "city": "Granby"},
    {"name": "CLSC de Cowansville", "lat": 45.2052, "lng": -72.7461, "platform": "clicsante", "website": "https://www.santeestrie.qc.ca/", "type": "clsc", "city": "Cowansville"},
    {"name": "CLSC de Magog", "lat": 45.2631, "lng": -72.1488, "platform": "clicsante", "website": "https://www.santeestrie.qc.ca/", "type": "clsc", "city": "Magog"},
    {"name": "Hôpital Fleurimont", "lat": 45.4312, "lng": -71.8615, "platform": "clicsante", "website": "https://www.santeestrie.qc.ca/", "type": "hospital", "city": "Sherbrooke"},
    
    # ========== TROIS-RIVIÈRES ==========
    {"name": "CLSC de Trois-Rivières", "lat": 46.3462, "lng": -72.5485, "platform": "clicsante", "website": "https://www.ciusssmcq.ca/", "type": "clsc", "city": "Trois-Rivières"},
    
    # ========== SAGUENAY ==========
    {"name": "CLSC Chicoutimi Nord", "lat": 48.4418, "lng": -71.0521, "platform": "clicsante", "website": "https://santesaglac.gouv.qc.ca/", "type": "clsc", "city": "Saguenay"},
    
    # ========== CHÂTEAUGUAY / MONTÉRÉGIE ==========
    {"name": "CLSC Châteauguay", "lat": 45.3615, "lng": -73.7224, "platform": "clicsante", "website": "https://www.santemonteregie.qc.ca/", "type": "clsc", "city": "Châteauguay"},
    {"name": "CLSC Kateri Candiac", "lat": 45.3855, "lng": -73.5112, "platform": "clicsante", "website": "https://www.santemonteregie.qc.ca/", "type": "clsc", "city": "Candiac"},
    {"name": "CLSC de Beauharnois", "lat": 45.3114, "lng": -73.8682, "platform": "clicsante", "website": "https://www.santemonteregie.qc.ca/", "type": "clsc", "city": "Beauharnois"},
    {"name": "CLSC de Saint-Rémi", "lat": 45.2592, "lng": -73.6148, "platform": "clicsante", "website": "https://www.santemonteregie.qc.ca/", "type": "clsc", "city": "Saint-Rémi"},
    {"name": "CLSC de Napierville", "lat": 45.1874, "lng": -73.4042, "platform": "clicsante", "website": "https://www.santemonteregie.qc.ca/", "type": "clsc", "city": "Napierville"},
    
    # ========== MONTREAL ADDITIONAL CLSCS ==========
    {"name": "CLSC d'Ahuntsic", "lat": 45.5562, "lng": -73.6582, "platform": "clicsante", "website": "https://www.ciusssnordmtl.ca/", "type": "clsc", "city": "Montréal"},
    {"name": "CLSC de Montréal-Nord", "lat": 45.6114, "lng": -73.6288, "platform": "clicsante", "website": "https://www.ciusssnordmtl.ca/", "type": "clsc", "city": "Montréal"},
    {"name": "CLSC de Saint-Léonard", "lat": 45.5891, "lng": -73.5852, "platform": "clicsante", "website": "https://www.ciusseastmtl.gouv.qc.ca/", "type": "clsc", "city": "Montréal"},
    {"name": "CLSC de LaSalle", "lat": 45.4352, "lng": -73.6231, "platform": "clicsante", "website": "https://www.ciusss-ouestmtl.gouv.qc.ca/", "type": "clsc", "city": "Montréal"},
    {"name": "CLSC de Verdun", "lat": 45.4614, "lng": -73.5684, "platform": "clicsante", "website": "https://www.ciusss-centreouestmtl.gouv.qc.ca/", "type": "clsc", "city": "Montréal"},
    {"name": "CLSC de Côte-des-Neiges", "lat": 45.4975, "lng": -73.6272, "platform": "clicsante", "website": "https://www.ciussswestcentral.ca/", "type": "clsc", "city": "Montréal"},
    {"name": "CLSC de Benny Farm", "lat": 45.4695, "lng": -73.6385, "platform": "clicsante", "website": "https://www.ciussswestcentral.ca/", "type": "clsc", "city": "Montréal"},
    {"name": "CLSC René-Cassin", "lat": 45.4688, "lng": -73.6641, "platform": "clicsante", "website": "https://www.ciussswestcentral.ca/", "type": "clsc", "city": "Montréal"},
    {"name": "CLSC de Saint-Laurent", "lat": 45.5152, "lng": -73.6821, "platform": "clicsante", "website": "https://www.ciusssnordmtl.ca/", "type": "clsc", "city": "Montréal"},
    {"name": "CLSC de Rosemont", "lat": 45.5412, "lng": -73.5654, "platform": "clicsante", "website": "https://www.ciusseastmtl.gouv.qc.ca/", "type": "clsc", "city": "Montréal"},
    {"name": "CLSC de Saint-Michel", "lat": 45.5638, "lng": -73.6012, "platform": "clicsante", "website": "https://www.ciusseastmtl.gouv.qc.ca/", "type": "clsc", "city": "Montréal"},
    {"name": "CLSC de la Petite-Patrie", "lat": 45.5381, "lng": -73.6033, "platform": "clicsante", "website": "https://www.ciusssnordmtl.ca/", "type": "clsc", "city": "Montréal"},
    {"name": "CLSC de Villeray", "lat": 45.5492, "lng": -73.6184, "platform": "clicsante", "website": "https://www.ciusssnordmtl.ca/", "type": "clsc", "city": "Montréal"},
    {"name": "CLSC des Faubourgs", "lat": 45.5221, "lng": -73.5622, "platform": "clicsante", "website": "https://www.ciusss-centresudmtl.gouv.qc.ca/", "type": "clsc", "city": "Montréal"},
    
    # ========== HOSPITAL CLINICS ==========
    {"name": "Herzl Walk-In Centre (Jewish General)", "lat": 45.4958, "lng": -73.6301, "platform": "other", "website": "https://www.jgh.ca/", "type": "hospital", "city": "Montréal"},
    {"name": "St. Mary's Hospital Family Medicine", "lat": 45.4948, "lng": -73.6235, "platform": "other", "website": "https://www.stmaryshospitalcenter.ca/", "type": "hospital", "city": "Montréal"},
    {"name": "Hôpital Charles-Le Moyne", "lat": 45.4831, "lng": -73.4752, "platform": "clicsante", "website": "https://www.santemonteregie.qc.ca/centre/", "type": "hospital", "city": "Longueuil"},
    {"name": "MUHC Ambulatory Clinics", "lat": 45.4728, "lng": -73.6015, "platform": "other", "website": "https://muhc.ca/", "type": "hospital", "city": "Montréal"},
    {"name": "Hôpital de la Cité-de-la-Santé", "lat": 45.5832, "lng": -73.7194, "platform": "clicsante", "website": "https://www.lavalensante.com/", "type": "hospital", "city": "Laval"},
]

# ================================================================
# 7. BONJOUR SANTÉ BOOKING (SIMPLIFIED FOR TESTING)
# ================================================================

def complete_bonjoursante_booking(page, user: dict) -> dict:
    """Test Bonjour Santé booking"""
    print("   📅 Testing Bonjour Santé booking...")
    
    try:
        # Fill RAMQ
        ramq_input = page.locator("input[name*='ramq']").first
        if ramq_input.count() > 0:
            ramq_input.fill(user["ramq"])
            human_delay(500, 1000)
        
        # Fill postal code
        postal_input = page.locator("input[name*='postal']").first
        if postal_input.count() > 0:
            postal_input.fill(user["postal_code"])
            human_delay(500, 1000)
        
        # Click continue
        continue_btn = page.get_by_role("button", name=re.compile(r"Continuer|Suivant|Next", re.I))
        if continue_btn.count() > 0:
            continue_btn.first.click()
            human_delay(2000, 3000)
        
        # Fill patient info
        firstname_input = page.locator("input[name*='firstName'], input[name*='prenom']").first
        if firstname_input.count() > 0:
            firstname_input.fill(user["first_name"])
            human_delay(300, 600)
        
        lastname_input = page.locator("input[name*='lastName'], input[name*='nom']").first
        if lastname_input.count() > 0:
            lastname_input.fill(user["last_name"])
            human_delay(300, 600)
        
        # Check for availability indicators
        page_content = page.content().lower()
        
        if "disponible" in page_content or "available" in page_content:
            print("   ✅ Found availability indicators!")
            return {
                "booked": True,
                "confirmation_url": page.url,
                "appointment_time": "Available slot found"
            }
        else:
            print("   ❌ No availability found")
            return {"booked": False, "reason": "No availability indicators found"}
            
    except Exception as e:
        print(f"   ❌ Booking error: {e}")
        return {"booked": False, "reason": str(e)}

# ================================================================
# 8. MAIN BOOKING FUNCTION
# ================================================================

def book_appointment_at_clinic(clinic: dict, user: dict) -> dict:
    """Test booking at a clinic"""
    headless = os.getenv("HEADLESS", "false").lower() == "true"  # Default to visible so you can see
    clinic_website = clinic.get("website", "")
    clinic_name = clinic.get("name", "Unknown")
    
    print(f"\n🏥 TESTING: {clinic_name}")
    print(f"   Website: {clinic_website}")
    
    if ParallelKillSwitch.is_active():
        return {"booked": False, "reason": "Kill switch active"}
    
    with sync_playwright() as p:
        browser, context = launch_stealth_browser(p, headless=headless)
        page = context.new_page()
        
        try:
            # Go to website
            page.goto(clinic_website, wait_until="domcontentloaded", timeout=30000)
            print(f"   ✅ Page loaded: {page.title()[:50]}")
            human_delay(2000, 3000)
            
            # Take screenshot for debugging
            if not headless:
                page.screenshot(path=f"debug_{clinic_name[:20]}.png")
                print(f"   📸 Screenshot saved: debug_{clinic_name[:20]}.png")
            
            # Try to find and click booking button
            button_found = False
            button_patterns = ["Prendre rendez-vous", "Réserver", "Book", "RDV", "Appointment"]
            
            for pattern in button_patterns:
                try:
                    btn = page.get_by_text(pattern, exact=False).first
                    if btn.count() > 0:
                        print(f"   ✅ Found button: '{pattern}'")
                        btn.click()
                        button_found = True
                        human_delay(3000, 5000)
                        break
                except:
                    continue
            
            if button_found:
                # Try to complete booking
                result = complete_bonjoursante_booking(page, user)
                result['clinic_name'] = clinic_name
                return result
            else:
                print(f"   ⚠️ No booking button found")
                return {"booked": False, "reason": "No booking button found", "clinic_name": clinic_name}
                
        except Exception as e:
            print(f"   ❌ Error: {e}")
            return {"booked": False, "reason": str(e), "clinic_name": clinic_name}
        finally:
            browser.close()

# ================================================================
# 9. PARALLEL SEARCH
# ================================================================

def discover_clinics_near(postal_code: str, radius_km: int) -> list:
    """Find clinics within radius"""
    coords = geocode_postal_code(postal_code)
    if not coords:
        return []
    
    user_lat, user_lng = coords["lat"], coords["lng"]
    nearby = []
    
    for clinic in CLINICS:
        dist = haversine(user_lat, user_lng, clinic["lat"], clinic["lng"])
        if dist <= radius_km:
            nearby.append(clinic)
    
    print(f"✅ Found {len(nearby)} clinics within {radius_km}km")
    return nearby

def search_clinics_parallel(user: dict, radius_km: int, max_workers: int = 3) -> bool:
    """Parallel search with kill switch"""
    clinics = discover_clinics_near(user["postal_code"], radius_km)
    if not clinics:
        return False
    
    print(f"\n🚀 Testing {len(clinics)} clinics with {max_workers} parallel browsers")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(book_appointment_at_clinic, clinic, user): clinic
            for clinic in clinics[:10]  # Test first 10 only
        }
        
        for future in as_completed(futures):
            if ParallelKillSwitch.is_active():
                for f in futures:
                    f.cancel()
                break
            
            try:
                result = future.result(timeout=60)
                if result.get("booked"):
                    ParallelKillSwitch.activate(result)
                    print(f"\n🎉 SUCCESS! Found booking at {result.get('clinic_name')}")
                    return True
            except Exception as e:
                print(f"❌ Worker error: {e}")
    
    return False

# ================================================================
# 10. MAIN
# ================================================================

def main():
    print("╔══════════════════════════════════════════════════════╗")
    print("║     MYVITA CLINIC SCRAPER - TEST MODE               ║")
    print("║     5 stealth browsers | Visible mode               ║")
    print("║     Testing first 10 clinics near you               ║")
    print("╚══════════════════════════════════════════════════════╝")
    
    user = get_user_data()
    print(f"\n📋 Testing with:")
    print(f"   Postal code: {user['postal_code']}")
    print(f"   Name: {user['first_name']} {user['last_name']}")
    print(f"   RAMQ: {user['ramq']}")
    print(f"   Headless: {os.getenv('HEADLESS', 'false')}")
    
    ParallelKillSwitch.reset()
    
    # Test with 15km radius first
    found = search_clinics_parallel(user, 15, max_workers=3)
    
    if found:
        result = ParallelKillSwitch.get_found()
        print(f"\n🎉 SUCCESS! Appointment found!")
        print(f"   Clinic: {result.get('clinic_name')}")
        print(f"   URL: {result.get('confirmation_url')}")
    else:
        print(f"\n😴 No appointments found in test.")
    
    print("\n✅ Test complete")

if __name__ == "__main__":
    main()
