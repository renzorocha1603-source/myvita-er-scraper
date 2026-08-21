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
# COPY-PASTE SCRAPER (with anti-merge)
# ═══════════════════════════════════════════════════════════════

def extract_hospital_full_text(body_text):
    """Extract each hospital's FULL TEXT from the body."""
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
    
    # Post-process: Split hospitals that still have multiple postal codes
    final_hospitals = []
    for h in hospitals:
        full_text = h['full_text']
        postal_codes = postal_pattern.findall(full_text)
        
        if len(postal_codes) <= 1:
            final_hospitals.append(h)
            continue
        
        text_lines = full_text.split('\n')
        blocks = []
        current_block = []
        
        for line in text_lines:
            if postal_pattern.search(line.strip()):
                if current_block:
                    blocks.append(current_block)
                current_block = [line]
            else:
                current_block.append(line)
        
        if current_block:
            blocks.append(current_block)
        
        for block in blocks:
            if not block:
                continue
            block_text = '\n'.join(block)
            name = ''
            for bl in block:
                stripped = bl.strip()
                if stripped and not postal_pattern.search(stripped):
                    name = stripped
                    break
            if not name:
                name = block[0].strip() if block else 'Unknown'
            
            final_hospitals.append({
                'name': name,
                'full_text': block_text
            })
    
    return final_hospitals


def get_all_hospitals():
    """Scrape all 12 pages and return all hospitals with full text"""
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
        
        print(f"   ✅ Copy-Paste SUCCESS: {len(all_hospitals)} hospitals total")
        return all_hospitals
    
    except Exception as e:
        print(f"   ❌ Copy-Paste error: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
# CALCULATE GLOBAL STATS FROM HOSPITAL DATA
# ═══════════════════════════════════════════════════════════════

def calculate_global_stats(hospitals):
    """Calculate Quebec-wide totals from hospital data"""
    total_patients = 0
    total_waiting = 0
    total_occupancy = 0
    count_with_occupancy = 0
    count_with_patients = 0
    
    for h in hospitals:
        full_text = h.get('full_text', '')
        
        # Extract total patients
        match = re.search(r'total de personnes[^:]*:\s*(\d+)', full_text, re.IGNORECASE)
        if match:
            total_patients += int(match.group(1))
            count_with_patients += 1
        
        # Extract people waiting
        match = re.search(r'personnes qui attendent[^:]*:\s*(\d+)', full_text, re.IGNORECASE)
        if match:
            total_waiting += int(match.group(1))
        
        # Extract occupancy rate
        match = re.search(r'Taux d[^0-9]*(\d+)', full_text, re.IGNORECASE)
        if match:
            total_occupancy += int(match.group(1))
            count_with_occupancy += 1
    
    avg_occupancy = round(total_occupancy / count_with_occupancy, 1) if count_with_occupancy > 0 else 0
    
    return {
        'total_patients': total_patients,
        'total_waiting': total_waiting,
        'avg_occupancy': avg_occupancy,
        'hospitals_with_data': count_with_patients,
        'hospitals_with_occupancy': count_with_occupancy,
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
    
    # Save to JSON files
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    with open(BACKUP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    health = {
        "status": "healthy",
        "last_successful_run": now.isoformat(),
        "data_freshness": "live",
        "total_hospitals": len(hospitals),
        "avg_occupancy": global_stats['avg_occupancy'],
    }
    with open(HEALTH_FILE, "w", encoding="utf-8") as f:
        json.dump(health, f, indent=2)
    
    # Save to Firestore
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
    print(f"MyVita ER Scraper (Copy-Paste + Calculated Stats) — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
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
