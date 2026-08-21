import csv
import io
import json
import os
import re
import time
import requests
from datetime import datetime

# ============================================================
# DATA SOURCES
# ============================================================
HTML_PAGE_URL = "https://www.quebec.ca/sante/systeme-et-services-de-sante/organisation-des-services/donnees-systeme-sante-quebecois-services/situation-urgences"
PROVINCE_AVERAGE_URL = "https://www.quebec.ca/en/health/health-system-and-services/service-organization/quebec-health-system-and-its-services/situation-in-emergency-rooms-in-quebec?id=24981&tx_solr%5Blocation%5D=&tx_solr%5Bpt%5D=&tx_solr%5Bsfield%5D=geolocation_location&tx_solr%5Bpage%5D=4#situation-urgences-tab2"
CKAN_DATASTORE_API = "https://www.donneesquebec.ca/api/3/action/datastore_search?resource_id=b256f87f-40ec-4c79-bdba-a23e9c50e741"
MSSS_DIRECT_URL = "https://www.msss.gouv.qc.ca/professionnels/statistiques/documents/urgences/Releve_horaire_urgences_7jours_nbpers.csv"

OUTPUT_FILE = "er_data.json"
BACKUP_FILE = "er_data_backup.json"
HEALTH_FILE = "health_check.json"

STEALTH_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/csv,text/plain,application/csv,text/html,application/xhtml+xml,application/json,*/*;q=0.8',
    'Accept-Language': 'fr-CA,fr;q=0.9,en-CA;q=0.8,en;q=0.7',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Cache-Control': 'max-age=0',
    'Referer': 'https://www.quebec.ca/',
}

def safe_int(value):
    try:
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        return int(str(value).strip())
    except (ValueError, AttributeError, TypeError):
        return 0

def safe_str(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


# ═══════════════════════════════════════════════════════════════
# LAYER 1: PLAYWRIGHT BODY TEXT PARSER (WORKS!)
# ═══════════════════════════════════════════════════════════════

def parse_hospital_data(body_text):
    """Parse hospital data from plain body text"""
    hospitals = []
    lines = body_text.split('\n')
    
    current_hospital = None
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Detect hospital start (Name pattern)
        if re.match(r'^(Centre|H[oô]pital|CHU|CHSLD|CLSC|Institut|Pavillon)', line):
            if current_hospital:
                hospitals.append(current_hospital)
            
            current_hospital = {
                'name': line,
                'address': '',
                'region': '',
                'wait_time': '',
                'people_waiting': 0,
                'total_patients': 0,
                'occupancy': 0,
                'room_duration': '',
                'stretcher_duration': '',
            }
        
        elif current_hospital:
            # Address (contains postal code)
            if re.search(r'[A-Z]\d[A-Z]\s?\d[A-Z]\d', line) and not current_hospital['address']:
                current_hospital['address'] = line
            
            # Region
            elif re.match(r'^(Montréal|Laval|Montérégie|Capitale-Nationale|Outaouais|Mauricie|Estrie|Lanaudière|Laurentides|Saguenay|Bas-Saint-Laurent|Chaudière-Appalaches|Abitibi|Côte-Nord|Nord-du-Québec|Gaspésie|Centre-du-Québec|Eeyou)', line):
                current_hospital['region'] = line
            
            # Wait time
            elif 'Temps d' in line:
                match = re.search(r'(\d{1,2})\s*h\s*(\d{1,2})', line)
                if match:
                    current_hospital['wait_time'] = f"{match.group(1)}:{match.group(2)}"
                else:
                    current_hospital['wait_time'] = 'N/A'
            
            # People waiting
            elif 'personnes qui attendent' in line:
                match = re.search(r'(\d+)', line)
                if match:
                    current_hospital['people_waiting'] = int(match.group(1))
            
            # Total patients
            elif 'total de personnes' in line:
                match = re.search(r'(\d+)', line)
                if match:
                    current_hospital['total_patients'] = int(match.group(1))
            
            # Occupancy
            elif 'Taux d' in line:
                match = re.search(r'(\d+)', line)
                if match:
                    current_hospital['occupancy'] = int(match.group(1))
            
            # Room duration
            elif 'salle d' in line:
                match = re.search(r'(\d{1,2})\s*h\s*(\d{1,2})', line)
                if match:
                    current_hospital['room_duration'] = f"{match.group(1)}:{match.group(2)}"
            
            # Stretcher duration
            elif 'civière' in line and 'durée' in line.lower():
                match = re.search(r'(\d{1,2})\s*h\s*(\d{1,2})', line)
                if match:
                    current_hospital['stretcher_duration'] = f"{match.group(1)}:{match.group(2)}"
    
    # Add last hospital
    if current_hospital:
        hospitals.append(current_hospital)
    
    return hospitals


def get_live_data_html():
    """Layer 1: Scrape all 12 pages using URL navigation + body text parsing"""
    print("   [Layer 1] HTML Scraper (12 pages)...")

    try:
        from playwright.sync_api import sync_playwright

        all_hospitals = []
        seen_names = set()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            for page_num in range(1, 13):
                if page_num == 1:
                    url = HTML_PAGE_URL
                else:
                    url = f"{HTML_PAGE_URL}?tx_solr%5Bpage%5D={page_num}"
                
                page.goto(url, wait_until='networkidle', timeout=60000)
                page.wait_for_timeout(5000)
                
                body_text = page.locator('body').inner_text()
                hospitals = parse_hospital_data(body_text)
                
                new_count = 0
                for h in hospitals:
                    if h['name'] not in seen_names:
                        seen_names.add(h['name'])
                        all_hospitals.append(h)
                        new_count += 1
                
                print(f"   Page {page_num}: {new_count} new (total: {len(all_hospitals)})")

            browser.close()

        if all_hospitals:
            print(f"   ✅ Layer 1 SUCCESS: {len(all_hospitals)} hospitals total")
            return all_hospitals, ""

    except Exception as e:
        print(f"   ❌ Layer 1 error: {e}")

    print("   ❌ Layer 1 failed")
    return None, None


# ═══════════════════════════════════════════════════════════════
# LAYER 2: CSV DOWNLOAD (fallback)
# ═══════════════════════════════════════════════════════════════

def download_csv_as_json(url):
    """Layer 2: Download CSV from MSSS"""
    print(f"   [Layer 2] Downloading CSV...")

    try:
        response = requests.get(url, headers=STEALTH_HEADERS, timeout=30)
        
        if response.status_code != 200:
            print(f"   ❌ HTTP {response.status_code}")
            return None, None

        content = response.content
        text = None
        for encoding in ['utf-8-sig', 'utf-8', 'latin-1']:
            try:
                text = content.decode(encoding)
                break
            except:
                continue

        if not text:
            return None, None

        reader = csv.DictReader(io.StringIO(text))
        hospitals = []

        for row in reader:
            nom = safe_str(row.get('Nom_installation', ''))
            if not nom or 'Total' in nom:
                continue

            h = {
                'name': nom,
                'address': '',
                'region': safe_str(row.get('Region', '')),
                'wait_time': '',
                'people_waiting': safe_int(row.get('Nombre_total_de_patients_en_attente_de_PEC', 0)),
                'total_patients': safe_int(row.get('Nombre_total_de_patients_presents_a_lurgence', 0)),
                'occupancy': 0,
                'room_duration': '',
                'stretcher_duration': '',
            }
            hospitals.append(h)

        if hospitals:
            print(f"   ✅ Layer 2 SUCCESS: {len(hospitals)} hospitals")
            return hospitals, ""

    except Exception as e:
        print(f"   ❌ CSV error: {e}")

    return None, None


# ═══════════════════════════════════════════════════════════════
# LAYER 3: BACKUP FILE
# ═══════════════════════════════════════════════════════════════

def get_backup_data():
    """Layer 3: Load backup JSON file"""
    print("   [Layer 3] Backup file...")
    if os.path.exists(BACKUP_FILE):
        try:
            with open(BACKUP_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            hospitals = data.get("hospitals", [])
            if hospitals:
                print(f"   ✅ Layer 3 SUCCESS: {len(hospitals)} hospitals from backup")
                return hospitals, ""
        except:
            pass
    print("   ❌ No backup available")
    return None, None


# ═══════════════════════════════════════════════════════════════
# STATS & SAVE
# ═══════════════════════════════════════════════════════════════

def calculate_global_stats(hospitals):
    if not hospitals:
        return {"total_patients": 0, "total_waiting": 0, "avg_occupancy": 0}

    total_occ = sum(h.get('occupancy', 0) for h in hospitals)
    avg_occ = round(total_occ / len(hospitals), 1) if hospitals else 0

    return {
        "total_patients": sum(h.get('total_patients', 0) for h in hospitals),
        "total_waiting": sum(h.get('people_waiting', 0) for h in hospitals),
        "avg_occupancy": avg_occ,
    }


def save_all(hospitals, global_stats, freshness):
    now = datetime.now()

    data = {
        "last_update": now.isoformat(),
        "source": "MSSS / Gouvernement du Québec",
        "source_url": HTML_PAGE_URL,
        "data_freshness": freshness,
        "global_stats": global_stats,
        "hospitals": hospitals
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    with open(BACKUP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    health = {
        "status": "healthy" if freshness == "live" else "degraded",
        "last_successful_run": now.isoformat(),
        "data_freshness": freshness,
        "total_hospitals": len(hospitals),
        "avg_occupancy": global_stats["avg_occupancy"],
    }
    with open(HEALTH_FILE, "w", encoding="utf-8") as f:
        json.dump(health, f, indent=2)

    print(f"\n✅ SAVED: {len(hospitals)} hospitals | Occupancy: {global_stats['avg_occupancy']}% | Freshness: {freshness}")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print(f"MyVita ER Scraper — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    hospitals = None
    freshness = "live"

    # Layer 1: Playwright HTML scraper (12 pages)
    hospitals, _ = get_live_data_html()

    # Layer 2: CSV download
    if not hospitals:
        hospitals, _ = download_csv_as_json(MSSS_DIRECT_URL)

    # Layer 3: Backup
    if not hospitals:
        hospitals, _ = get_backup_data()
        freshness = "cached"

    if hospitals:
        global_stats = calculate_global_stats(hospitals)
        save_all(hospitals, global_stats, freshness)
        print("\n✅ Scrape complete!")
    else:
        print("\n❌ CRITICAL: All 3 layers failed!")


if __name__ == "__main__":
    main()
