import csv
import io
import json
import os
import re
import time
import requests
from datetime import datetime

import firebase_admin
from firebase_admin import credentials, firestore

# ============================================================
# FIREBASE SETUP
# ============================================================
FIREBASE_CREDENTIALS_JSON = os.environ.get("FIREBASE_CREDENTIALS", "")

db = None
if FIREBASE_CREDENTIALS_JSON:
    try:
        cred_dict = json.loads(FIREBASE_CREDENTIALS_JSON)
        cred = credentials.Certificate(cred_dict)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred, {'projectId': 'myvita-app-c5ecd'})
        db = firestore.client()
        print("✅ Firebase initialized (GitHub Secret)")
    except Exception as e:
        print(f"⚠️ Firebase init error: {e}")
else:
    print("⚠️ No Firebase credentials found — saving to JSON only")

# ============================================================
# FSA COORDINATES TABLE (Postal code → GPS coordinates)
# ============================================================
FSA_COORDINATES = {
    # Montreal Island
    'H1A': (45.60, -73.52), 'H1B': (45.65, -73.49), 'H1C': (45.66, -73.45),
    'H1E': (45.63, -73.55), 'H1G': (45.60, -73.58), 'H1H': (45.58, -73.58),
    'H1J': (45.59, -73.58), 'H1K': (45.61, -73.55), 'H1L': (45.60, -73.53),
    'H1M': (45.59, -73.55), 'H1N': (45.59, -73.55), 'H1P': (45.59, -73.57),
    'H1R': (45.60, -73.53), 'H1S': (45.58, -73.53), 'H1T': (45.57, -73.57),
    'H1V': (45.58, -73.55), 'H1W': (45.55, -73.55), 'H1X': (45.56, -73.57),
    'H1Y': (45.56, -73.58), 'H1Z': (45.57, -73.58),
    'H2A': (45.57, -73.60), 'H2B': (45.57, -73.62), 'H2C': (45.57, -73.65),
    'H2E': (45.56, -73.67), 'H2G': (45.54, -73.63), 'H2H': (45.53, -73.60),
    'H2J': (45.53, -73.60), 'H2K': (45.53, -73.57), 'H2L': (45.53, -73.56),
    'H2M': (45.55, -73.63), 'H2N': (45.54, -73.63), 'H2P': (45.55, -73.63),
    'H2R': (45.55, -73.60), 'H2S': (45.54, -73.60), 'H2T': (45.53, -73.60),
    'H2V': (45.52, -73.62), 'H2W': (45.52, -73.58), 'H2X': (45.51, -73.57),
    'H2Y': (45.50, -73.56), 'H2Z': (45.51, -73.56),
    'H3A': (45.50, -73.58), 'H3B': (45.50, -73.57), 'H3C': (45.50, -73.56),
    'H3E': (45.50, -73.57), 'H3G': (45.50, -73.58), 'H3H': (45.50, -73.59),
    'H3J': (45.51, -73.58), 'H3K': (45.49, -73.57), 'H3L': (45.52, -73.67),
    'H3M': (45.53, -73.68), 'H3N': (45.53, -73.65), 'H3P': (45.52, -73.65),
    'H3R': (45.52, -73.63), 'H3S': (45.51, -73.63), 'H3T': (45.50, -73.62),
    'H3V': (45.50, -73.62), 'H3W': (45.50, -73.63), 'H3X': (45.48, -73.64),
    'H3Y': (45.48, -73.62),
    'H4A': (45.47, -73.62), 'H4B': (45.46, -73.63), 'H4C': (45.47, -73.63),
    'H4E': (45.46, -73.60), 'H4G': (45.47, -73.63), 'H4H': (45.46, -73.65),
    'H4J': (45.52, -73.68), 'H4K': (45.50, -73.68), 'H4L': (45.52, -73.67),
    'H4M': (45.50, -73.67), 'H4N': (45.53, -73.65), 'H4P': (45.50, -73.65),
    'H4R': (45.49, -73.65), 'H4S': (45.48, -73.65), 'H4T': (45.48, -73.65),
    'H4V': (45.47, -73.63), 'H4W': (45.47, -73.64), 'H4X': (45.46, -73.72),
    'H4Y': (45.46, -73.72), 'H4Z': (45.46, -73.72),
    'H7A': (45.66, -73.63), 'H7B': (45.66, -73.64), 'H7C': (45.66, -73.65),
    'H7E': (45.64, -73.62), 'H7G': (45.65, -73.67), 'H7H': (45.65, -73.68),
    'H7J': (45.66, -73.68), 'H7K': (45.67, -73.69), 'H7L': (45.64, -73.68),
    'H7M': (45.66, -73.70), 'H7N': (45.70, -73.70), 'H7P': (45.62, -73.72),
    'H7R': (45.63, -73.72), 'H7S': (45.64, -73.72), 'H7T': (45.65, -73.72),
    'H7V': (45.63, -73.72), 'H7W': (45.62, -73.72), 'H7X': (45.62, -73.72),
    'H7Y': (45.63, -73.72),
    'H8N': (45.43, -73.62), 'H8P': (45.43, -73.60), 'H8R': (45.43, -73.62),
    'H8S': (45.43, -73.62), 'H8T': (45.42, -73.61), 'H8Y': (45.50, -73.72),
    'H8Z': (45.43, -73.62),
    'H9A': (45.48, -73.82), 'H9B': (45.48, -73.82), 'H9C': (45.48, -73.82),
    'H9E': (45.47, -73.87), 'H9G': (45.46, -73.85), 'H9H': (45.45, -73.85),
    'H9J': (45.43, -73.87), 'H9K': (45.44, -73.87), 'H9R': (45.41, -73.80),
    'H9S': (45.44, -73.79), 'H9W': (45.42, -73.83),
    # Quebec City
    'G1A': (46.80, -71.20), 'G1B': (46.88, -71.15), 'G1C': (46.88, -71.15),
    'G1E': (46.84, -71.20), 'G1G': (46.82, -71.22), 'G1H': (46.84, -71.22),
    'G1J': (46.83, -71.23), 'G1K': (46.81, -71.22), 'G1L': (46.83, -71.24),
    'G1M': (46.83, -71.25), 'G1N': (46.80, -71.26), 'G1P': (46.80, -71.25),
    'G1R': (46.80, -71.22), 'G1S': (46.78, -71.23), 'G1T': (46.78, -71.24),
    'G1V': (46.77, -71.27), 'G1W': (46.76, -71.28),
    'G2A': (46.85, -71.28), 'G2B': (46.86, -71.30),
    # Gatineau
    'J8P': (45.48, -75.65), 'J8R': (45.48, -75.65), 'J8T': (45.48, -75.65),
    'J8V': (45.48, -75.65), 'J8W': (45.48, -75.65), 'J8X': (45.43, -75.70),
    'J8Y': (45.44, -75.73), 'J9A': (45.42, -75.73), 'J9H': (45.42, -75.73),
    'J9J': (45.42, -75.73),
    # Sherbrooke
    'J1A': (45.40, -71.89), 'J1C': (45.40, -71.89), 'J1E': (45.40, -71.89),
    'J1G': (45.40, -71.89), 'J1H': (45.40, -71.89), 'J1J': (45.40, -71.89),
    'J1K': (45.40, -71.89), 'J1L': (45.40, -71.89), 'J1M': (45.40, -71.89),
    'J1N': (45.40, -71.89),
    # Longueuil
    'J4A': (45.53, -73.52), 'J4B': (45.53, -73.52), 'J4C': (45.53, -73.52),
    'J4G': (45.53, -73.52), 'J4H': (45.53, -73.52), 'J4J': (45.53, -73.52),
    'J4K': (45.53, -73.52), 'J4L': (45.53, -73.52), 'J4M': (45.53, -73.52),
    'J4N': (45.53, -73.52), 'J4P': (45.53, -73.52), 'J4R': (45.53, -73.52),
    'J4S': (45.53, -73.52), 'J4T': (45.53, -73.52), 'J4V': (45.53, -73.52),
    'J4W': (45.53, -73.52), 'J4X': (45.53, -73.52), 'J4Y': (45.53, -73.52),
    'J4Z': (45.53, -73.52),
    # Repentigny / Lanaudière
    'J5W': (45.77, -73.45), 'J5Y': (45.76, -73.44), 'J5Z': (45.77, -73.43),
    'J6A': (45.77, -73.44), 'J6B': (45.76, -73.43), 'J6C': (45.75, -73.42),
    'J6D': (45.74, -73.41), 'J6E': (45.73, -73.40), 'J6G': (45.76, -73.42),
    'J6H': (45.75, -73.43), 'J6J': (45.74, -73.44), 'J6K': (45.73, -73.45),
    'J6L': (45.75, -73.44), 'J6M': (45.74, -73.43), 'J6N': (45.73, -73.42),
    'J6P': (45.72, -73.41), 'J6Q': (45.71, -73.40), 'J6R': (45.70, -73.39),
    'J6S': (45.69, -73.38), 'J6T': (45.68, -73.37), 'J6V': (45.67, -73.36),
    'J6W': (45.66, -73.35), 'J6X': (45.65, -73.34), 'J6Y': (45.64, -73.33),
    'J6Z': (45.63, -73.32),
    'J7A': (45.70, -73.60), 'J7B': (45.71, -73.61), 'J7C': (45.72, -73.62),
    'J7D': (45.73, -73.63), 'J7E': (45.74, -73.64), 'J7F': (45.75, -73.65),
    'J7G': (45.76, -73.66), 'J7H': (45.77, -73.67), 'J7J': (45.78, -73.68),
    'J7K': (45.79, -73.69), 'J7L': (45.80, -73.70), 'J7M': (45.81, -73.71),
    'J7N': (45.82, -73.72), 'J7P': (45.83, -73.73), 'J7R': (45.84, -73.74),
    'J7S': (45.85, -73.75), 'J7T': (45.86, -73.76), 'J7V': (45.87, -73.77),
    # Saguenay
    'G7A': (48.43, -71.07), 'G7B': (48.43, -71.07), 'G7G': (48.43, -71.07),
    'G7H': (48.43, -71.07), 'G7J': (48.43, -71.07), 'G7K': (48.43, -71.07),
    'G7N': (48.43, -71.07), 'G7P': (48.43, -71.07), 'G7S': (48.43, -71.07),
    'G7T': (48.43, -71.07), 'G7X': (48.43, -71.07), 'G7Y': (48.43, -71.07),
    # Trois-Rivières
    'G8T': (46.34, -72.58), 'G8V': (46.34, -72.58), 'G8W': (46.34, -72.58),
    'G8Y': (46.34, -72.58), 'G8Z': (46.34, -72.58), 'G9A': (46.34, -72.58),
    'G9B': (46.34, -72.58), 'G9C': (46.34, -72.58),
    # Rimouski
    'G5L': (48.45, -68.52), 'G5M': (48.45, -68.52), 'G5N': (48.45, -68.52),
    # Outaouais
    'J8A': (45.48, -75.62), 'J8B': (45.49, -75.63), 'J8C': (45.50, -75.64),
    'J8D': (45.51, -75.65), 'J8E': (45.52, -75.66), 'J8F': (45.53, -75.67),
    'J8G': (45.54, -75.68), 'J8H': (45.55, -75.69), 'J8J': (45.56, -75.70),
    'J8K': (45.57, -75.71), 'J8L': (45.58, -75.72), 'J8M': (45.59, -75.73),
    'J8N': (45.60, -75.74),
    # Montérégie
    'J3A': (45.48, -73.29), 'J3B': (45.48, -73.29), 'J3E': (45.48, -73.29),
    'J3G': (45.48, -73.29), 'J3H': (45.48, -73.29), 'J3J': (45.48, -73.29),
    'J3K': (45.48, -73.29), 'J3L': (45.48, -73.29), 'J3M': (45.48, -73.29),
    'J3N': (45.48, -73.29), 'J3P': (45.48, -73.29), 'J3R': (45.48, -73.29),
    'J3S': (45.48, -73.29), 'J3T': (45.48, -73.29), 'J3V': (45.48, -73.29),
    'J3W': (45.48, -73.29), 'J3X': (45.48, -73.29), 'J3Y': (45.48, -73.29),
    'J3Z': (45.48, -73.29),
    # Estrie
    'J1A': (45.40, -71.89), 'J1C': (45.40, -71.89), 'J1E': (45.40, -71.89),
    'J1G': (45.40, -71.89), 'J1H': (45.40, -71.89), 'J1J': (45.40, -71.89),
    'J1K': (45.40, -71.89), 'J1L': (45.40, -71.89), 'J1M': (45.40, -71.89),
    'J1N': (45.40, -71.89),
    # Bas-Saint-Laurent
    'G0A': (47.50, -69.50), 'G0L': (47.80, -69.30), 'G5R': (47.40, -69.50),
    'G5V': (47.30, -69.10), 'G5W': (47.20, -69.00),
    # Côte-Nord
    'G0G': (50.30, -66.50), 'G0T': (48.90, -69.20), 'G4R': (50.20, -66.40),
    'G5B': (49.20, -68.15), 'G5C': (49.10, -68.10),
    # Gaspésie
    'G0C': (48.80, -64.60), 'G0E': (48.90, -65.50), 'G4X': (48.80, -64.50),
    'G4V': (48.80, -64.60), 'G0J': (48.60, -65.80),
    # Abitibi
    'J9P': (48.10, -77.80), 'J9T': (48.20, -78.10), 'J9V': (48.50, -78.40),
    'J9X': (48.80, -79.20), 'J9Y': (48.60, -78.40),
    # Mauricie
    'G9X': (47.40, -72.80), 'G9W': (47.30, -72.60), 'G9H': (47.20, -72.50),
    # Chaudière-Appalaches
    'G6E': (46.20, -71.00), 'G6G': (46.10, -71.00), 'G6H': (46.00, -71.00),
    'G6J': (45.90, -71.00), 'G6K': (45.80, -71.00), 'G6L': (45.70, -71.00),
    # Laurentides
    'J0R': (45.80, -74.00), 'J0T': (45.90, -74.10), 'J0W': (46.00, -74.20),
    'J0X': (45.60, -75.00), 'J8C': (45.50, -75.60),
    # Laval
    'H7A': (45.66, -73.63), 'H7B': (45.66, -73.64), 'H7C': (45.66, -73.65),
    'H7E': (45.64, -73.62), 'H7G': (45.65, -73.67), 'H7H': (45.65, -73.68),
    'H7J': (45.66, -73.68), 'H7K': (45.67, -73.69), 'H7L': (45.64, -73.68),
    'H7M': (45.66, -73.70), 'H7N': (45.70, -73.70), 'H7P': (45.62, -73.72),
    'H7R': (45.63, -73.72), 'H7S': (45.64, -73.72), 'H7T': (45.65, -73.72),
    'H7V': (45.63, -73.72), 'H7W': (45.62, -73.72), 'H7X': (45.62, -73.72),
    'H7Y': (45.63, -73.72),
}

def get_coordinates_from_postal(postal_code):
    """Get GPS coordinates from postal code FSA"""
    if not postal_code or len(postal_code) < 3:
        return None
    fsa = postal_code[:3].upper()
    coords = FSA_COORDINATES.get(fsa)
    if coords:
        return {'latitude': coords[0], 'longitude': coords[1]}
    return None

# ============================================================
# DATA SOURCES
# ============================================================
BASE_URL = "https://www.quebec.ca/sante/systeme-et-services-de-sante/organisation-des-services/donnees-systeme-sante-quebecois-services/situation-urgences"

PAGE_URLS = [BASE_URL] + [
    f"{BASE_URL}?tx_solr%5Bpage%5D={n}" for n in range(2, 13)
]

OUTPUT_FILE = "er_data.json"
BACKUP_FILE = "er_data_backup.json"
HEALTH_FILE = "health_check.json"

STEALTH_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'fr-CA,fr;q=0.9,en-CA;q=0.8,en;q=0.7',
    'Referer': 'https://www.quebec.ca/',
}


# ═══════════════════════════════════════════════════════════════
# COPY-PASTE SCRAPER (with anti-merge + GPS coordinates)
# ═══════════════════════════════════════════════════════════════

def extract_hospital_full_text(body_text):
    """Extract each hospital's FULL TEXT and GPS coordinates."""
    hospitals = []
    lines = body_text.split('\n')
    
    current_hospital = None
    current_lines = []
    current_has_postal = False
    
    hospital_start_pattern = re.compile(
        r'^(Centre|H[oô]pital|CHU|CHSLD|CLSC|Institut|Pavillon)',
        re.IGNORECASE
    )
    
    postal_pattern = re.compile(r'[A-Z]\d[A-Z]\s?\d[A-Z]\d')
    
    end_markers = [
        'Trouver une installation',
        'Résultats de la recherche',
        'Pagination',
        'Voir aussi',
        'À consulter aussi',
        'Footer',
        'Navigation de pied de page',
        'Page evaluation',
        'Was the information',
        'Gouvernement du QuébecFooter',
        'Liste des messages',
        'English',
        'Nous joindre',
    ]
    
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        
        if any(marker in line_stripped for marker in end_markers):
            if current_hospital:
                hospitals.append(current_hospital)
            current_hospital = None
            current_lines = []
            current_has_postal = False
            continue
        
        if hospital_start_pattern.match(line_stripped):
            if current_hospital:
                hospitals.append(current_hospital)
            
            current_hospital = {
                'name': line_stripped,
                'full_text': line_stripped
            }
            current_lines = [line_stripped]
            current_has_postal = False
        
        elif current_hospital:
            has_postal = bool(postal_pattern.search(line_stripped))
            
            if has_postal and current_has_postal:
                hospitals.append(current_hospital)
                
                prev_name = current_lines[-1] if current_lines else line_stripped
                current_hospital = {
                    'name': prev_name,
                    'full_text': prev_name + '\n' + line_stripped
                }
                current_lines = [prev_name, line_stripped]
                current_has_postal = True
            else:
                current_lines.append(line_stripped)
                current_hospital['full_text'] = '\n'.join(current_lines)
                if has_postal:
                    current_has_postal = True
    
    if current_hospital:
        hospitals.append(current_hospital)
    
    # Post-process: Add GPS coordinates
    final_hospitals = []
    for h in hospitals:
        full_text = h['full_text']
        postal_match = postal_pattern.search(full_text)
        
        if postal_match:
            postal_code = postal_match.group(0)
            coords = get_coordinates_from_postal(postal_code)
            if coords:
                h['latitude'] = coords['latitude']
                h['longitude'] = coords['longitude']
            else:
                h['latitude'] = None
                h['longitude'] = None
        else:
            h['latitude'] = None
            h['longitude'] = None
        
        final_hospitals.append(h)
    
    return final_hospitals


def get_all_hospitals():
    """Scrape all 12 pages and return all hospitals with GPS coordinates"""
    print("   [Copy-Paste Scraper] Loading all 12 pages...")
    
    try:
        from playwright.sync_api import sync_playwright
        
        all_hospitals = []
        seen_names = set()
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            for i, url in enumerate(PAGE_URLS, 1):
                print(f"   Page {i}/12...")
                
                page.goto(url, wait_until='networkidle', timeout=60000)
                page.wait_for_timeout(15000)
                
                body_text = page.locator('body').inner_text()
                hospitals = extract_hospital_full_text(body_text)
                
                new_count = 0
                for h in hospitals:
                    if h['name'] not in seen_names:
                        seen_names.add(h['name'])
                        all_hospitals.append(h)
                        new_count += 1
                
                print(f"      {new_count} new hospitals (total: {len(all_hospitals)})")
            
            browser.close()
        
        # Count how many have GPS coordinates
        with_coords = sum(1 for h in all_hospitals if h.get('latitude') is not None)
        print(f"   ✅ Copy-Paste SUCCESS: {len(all_hospitals)} hospitals ({with_coords} with GPS)")
        return all_hospitals
    
    except Exception as e:
        print(f"   ❌ Copy-Paste error: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
# CALCULATE GLOBAL STATS
# ═══════════════════════════════════════════════════════════════

def calculate_global_stats(hospitals):
    total_patients = 0
    total_waiting = 0
    total_occupancy = 0
    count_with_occupancy = 0
    
    for h in hospitals:
        full_text = h.get('full_text', '')
        
        match = re.search(r'total de personnes[^:]*:\s*(\d+)', full_text, re.IGNORECASE)
        if match:
            total_patients += int(match.group(1))
        
        match = re.search(r'personnes qui attendent[^:]*:\s*(\d+)', full_text, re.IGNORECASE)
        if match:
            total_waiting += int(match.group(1))
        
        match = re.search(r'Taux d[^0-9]*(\d+)', full_text, re.IGNORECASE)
        if match:
            total_occupancy += int(match.group(1))
            count_with_occupancy += 1
    
    avg_occupancy = round(total_occupancy / count_with_occupancy) if count_with_occupancy > 0 else 0
    
    return {
        'total_patients': int(total_patients),
        'total_waiting': int(total_waiting),
        'avg_occupancy': int(avg_occupancy),
    }


# ═══════════════════════════════════════════════════════════════
# SAVE
# ═══════════════════════════════════════════════════════════════

def save_all(hospitals, global_stats):
    now = datetime.now()
    
    data = {
        "last_update": now.isoformat(),
        "source": "MSSS / Gouvernement du Québec",
        "source_url": BASE_URL,
        "data_freshness": "live",
        "total_hospitals": len(hospitals),
        "global_stats": global_stats,
        "hospitals": hospitals
    }
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    with open(BACKUP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    if db:
        try:
            db.collection('er_live_data').document('current').set({
                'last_update': now.isoformat(),
                'source': 'MSSS / Gouvernement du Québec',
                'data_freshness': 'live',
                'total_hospitals': len(hospitals),
                'global_stats': global_stats,
                'hospitals': hospitals,
            })
            print(f"   ✅ Saved to Firestore: er_live_data/current")
        except Exception as e:
            print(f"   ❌ Firestore save failed: {e}")
    
    print(f"\n✅ SAVED: {len(hospitals)} hospitals")
    print(f"   Total patients: {global_stats['total_patients']}")
    print(f"   Total waiting: {global_stats['total_waiting']}")
    print(f"   Avg occupancy: {global_stats['avg_occupancy']}%")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print(f"MyVita ER Scraper (with GPS) — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    hospitals = get_all_hospitals()
    
    if hospitals:
        global_stats = calculate_global_stats(hospitals)
        save_all(hospitals, global_stats)
        print("\n✅ Scrape complete!")
    else:
        print("\n❌ No hospitals found")


if __name__ == "__main__":
    main()
