import json
import os
import re
import time
import requests
from datetime import datetime

# ============================================================
# DATA SOURCES (12 page URLs + Quebec average)
# ============================================================
BASE_URL = "https://www.quebec.ca/sante/systeme-et-services-de-sante/organisation-des-services/donnees-systeme-sante-quebecois-services/situation-urgences"

# All 12 page URLs
PAGE_URLS = [BASE_URL] + [
    f"{BASE_URL}?tx_solr%5Bpage%5D={n}" for n in range(2, 13)
]

# Quebec province average URL
PROVINCE_AVERAGE_URL = "https://www.quebec.ca/en/health/health-system-and-services/service-organization/quebec-health-system-and-its-services/situation-in-emergency-rooms-in-quebec?id=24981&tx_solr%5Blocation%5D=&tx_solr%5Bpt%5D=&tx_solr%5Bsfield%5D=geolocation_location&tx_solr%5Bpage%5D=4#situation-urgences-tab2"

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
# COPY-PASTE SCRAPER
# ═══════════════════════════════════════════════════════════════

def extract_hospital_full_text(body_text):
    """
    Extract each hospital's FULL TEXT from the body.
    Each hospital block starts with a name and includes all metrics.
    """
    hospitals = []
    lines = body_text.split('\n')
    
    current_name = None
    current_lines = []
    
    # Keywords that indicate a new hospital starts
    hospital_start_pattern = re.compile(
        r'^(Centre|H[oô]pital|CHU|CHSLD|CLSC|Institut|Pavillon)',
        re.IGNORECASE
    )
    
    # Keywords that indicate END of results
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
        
        # Check if this is the end of results
        if any(marker in line_stripped for marker in end_markers):
            if current_name and current_lines:
                hospitals.append({
                    'name': current_name,
                    'full_text': '\n'.join(current_lines)
                })
            current_name = None
            current_lines = []
            continue
        
        # Check if this is a new hospital start
        if hospital_start_pattern.match(line_stripped):
            # Save previous hospital
            if current_name and current_lines:
                hospitals.append({
                    'name': current_name,
                    'full_text': '\n'.join(current_lines)
                })
            
            # Start new hospital
            current_name = line_stripped
            current_lines = [line_stripped]
        
        elif current_name:
            # Continue adding lines to current hospital
            current_lines.append(line_stripped)
    
    # Save last hospital
    if current_name and current_lines:
        hospitals.append({
            'name': current_name,
            'full_text': '\n'.join(current_lines)
        })
    
    return hospitals


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
                page.wait_for_timeout(15000)  # Wait for JS to render
                
                # Get body text
                body_text = page.locator('body').inner_text()
                
                # Extract hospitals from this page
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


def get_quebec_average():
    """Fetch the Quebec province average text"""
    print("   [Quebec Average] Loading...")
    
    try:
        from playwright.sync_api import sync_playwright
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            page.goto(PROVINCE_AVERAGE_URL, wait_until='networkidle', timeout=60000)
            page.wait_for_timeout(15000)
            
            body_text = page.locator('body').inner_text()
            
            browser.close()
        
        print(f"   ✅ Quebec Average loaded ({len(body_text)} chars)")
        return body_text
    
    except Exception as e:
        print(f"   ❌ Quebec Average error: {e}")
        return ""


# ═══════════════════════════════════════════════════════════════
# SAVE
# ═══════════════════════════════════════════════════════════════

def save_all(hospitals, quebec_average_text):
    now = datetime.now()
    
    data = {
        "last_update": now.isoformat(),
        "source": "MSSS / Gouvernement du Québec",
        "source_url": BASE_URL,
        "data_freshness": "live",
        "total_hospitals": len(hospitals),
        "quebec_average_text": quebec_average_text,
        "hospitals": hospitals
    }
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    with open(BACKUP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    health = {
        "status": "healthy",
        "last_successful_run": now.isoformat(),
        "data_freshness": "live",
        "total_hospitals": len(hospitals),
    }
    with open(HEALTH_FILE, "w", encoding="utf-8") as f:
        json.dump(health, f, indent=2)
    
    print(f"\n✅ SAVED: {len(hospitals)} hospitals + Quebec average")
    print(f"   File: {OUTPUT_FILE}")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print(f"MyVita ER Scraper (Copy-Paste) — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    # Get all hospitals
    hospitals = get_all_hospitals()
    
    # Get Quebec average
    quebec_avg_text = get_quebec_average()
    
    if hospitals:
        save_all(hospitals, quebec_avg_text)
        print("\n✅ Scrape complete!")
    else:
        print("\n❌ No hospitals found")


if __name__ == "__main__":
    main()
