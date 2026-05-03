import json
import requests
from datetime import datetime
import os
import random

# ============================================================
# DATA SOURCES (3 layers — JSON API primary, CSV fallback, backup)
# ============================================================
CKAN_DATASTORE_API = "https://www.donneesquebec.ca/api/3/action/datastore_search?resource_id=b256f87f-40ec-4c79-bdba-a23e9c50e741"
CKAN_RESOURCE_API = "https://www.donneesquebec.ca/api/3/action/resource_show?id=b256f87f-40ec-4c79-bdba-a23e9c50e741"
MSSS_DIRECT_URL = "https://www.msss.gouv.qc.ca/professionnels/statistiques/documents/urgences/Releve_horaire_urgences_7jours_nbpers.csv"

OUTPUT_FILE = "er_data.json"
BACKUP_FILE = "er_data_backup.json"
HEALTH_FILE = "health_check.json"

def get_headers(content_type='json'):
    """Browser-like headers"""
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json' if content_type == 'json' else 'text/csv,application/csv,text/plain',
        'Accept-Language': 'fr-CA,fr;q=0.9,en;q=0.8',
        'Cache-Control': 'no-cache',
    }

def safe_int(value):
    try: return int(str(value).strip())
    except: return 0

def parse_hospital(record):
    """Parse a single hospital record from CKAN JSON"""
    return {
        "name": record.get("Nom_installation", "").strip(),
        "region": record.get("RSS", "").strip(),
        "total_stretchers": safe_int(record.get("Nombre_de_civieres_fonctionnelles", "0")),
        "patients_on_stretcher": safe_int(record.get("Nombre_de_civieres_occupees", "0")),
        "patients_over_24h": safe_int(record.get("Nombre_de_patients_sur_civiere_plus_de_24_heures", "0")),
        "patients_over_48h": safe_int(record.get("Nombre_de_patients_sur_civiere_plus_de_48_heures", "0")),
        "total_patients": safe_int(record.get("Nombre_total_de_patients_presents_a_lurgence", "0")),
        "patients_waiting": safe_int(record.get("Nombre_total_de_patients_en_attente_de_PEC", "0")),
        "occupancy_rate": 0,
    }

def calculate_occupancy(h):
    """Calculate occupancy rate"""
    if h["total_stretchers"] > 0:
        return round((h["patients_on_stretcher"] / h["total_stretchers"]) * 100, 1)
    return 0

def get_live_data():
    """Layer 1: CKAN Datastore API — returns JSON directly"""
    print("   🔍 LAYER 1: CKAN Datastore API (JSON)...")
    
    for attempt in range(3):
        try:
            # Add random cache buster
            url = f"{CKAN_DATASTORE_API}&limit=200&_cb={random.randint(10000,99999)}"
            response = requests.get(url, headers=get_headers('json'), timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                records = data.get("result", {}).get("records", [])
                
                if records:
                    hospitals = []
                    gov_time = ""
                    
                    for record in records:
                        nom = record.get("Nom_installation", "").strip()
                        # Skip totals
                        if "Total" in nom or "Ensemble" in nom or not nom:
                            continue
                        
                        h = parse_hospital(record)
                        h["occupancy_rate"] = calculate_occupancy(h)
                        hospitals.append(h)
                        
                        if not gov_time:
                            gov_time = record.get("Mise_a_jour", "")
                    
                    if hospitals:
                        print(f"   ✅ Got {len(hospitals)} hospitals via CKAN JSON")
                        return hospitals, gov_time, "live"
            
            elif response.status_code == 429:
                print(f"   ⏳ Rate limited, waiting {attempt + 1}s...")
                import time
                time.sleep(attempt + 1)
                continue
                
        except Exception as e:
            print(f"   ⚠️ Attempt {attempt + 1} error: {e}")
            if attempt < 2:
                import time
                time.sleep(1)
    
    print("   ❌ Layer 1 failed")
    return None, None, None

def get_csv_url():
    """Layer 2a: Get CSV URL from CKAN Resource API"""
    print("   🔍 LAYER 2a: CKAN Resource API...")
    
    try:
        response = requests.get(CKAN_RESOURCE_API, headers=get_headers('json'), timeout=15)
        if response.status_code == 200:
            data = response.json()
            csv_url = data.get("result", {}).get("url", "")
            if csv_url:
                print(f"   ✅ Got CSV URL: {csv_url[:80]}...")
                return csv_url
    except Exception as e:
        print(f"   ⚠️ Error: {e}")
    
    print("   ❌ Layer 2a failed")
    return None

def download_csv_as_json(url):
    """Layer 2b: Download CSV and parse it"""
    print(f"   🔍 LAYER 2b: Downloading CSV...")
    
    try:
        response = requests.get(url, headers=get_headers('csv'), timeout=30)
        if response.status_code != 200:
            print(f"   ❌ HTTP {response.status_code}")
            return None, None
        
        content = response.content
        text = None
        for encoding in ['utf-8-sig', 'utf-8', 'latin-1', 'iso-8859-1']:
            try:
                text = content.decode(encoding)
                if 'Nom_installation' in text:
                    break
            except:
                continue
        
        if not text:
            return None, None
        
        import csv
        import io
        
        reader = csv.DictReader(io.StringIO(text))
        hospitals = []
        gov_time = ""
        
        for row in reader:
            nom = row.get("Nom_installation", "").strip()
            if "Total" in nom or "Ensemble" in nom or not nom:
                continue
            
            h = {
                "name": nom,
                "region": row.get("RSS", "").strip(),
                "total_stretchers": safe_int(row.get("Nombre_de_civieres_fonctionnelles", "0")),
                "patients_on_stretcher": safe_int(row.get("Nombre_de_civieres_occupees", "0")),
                "patients_over_24h": safe_int(row.get("Nombre_de_patients_sur_civiere_plus_de_24_heures", "0")),
                "patients_over_48h": safe_int(row.get("Nombre_de_patients_sur_civiere_plus_de_48_heures", "0")),
                "total_patients": safe_int(row.get("Nombre_total_de_patients_presents_a_lurgence", "0")),
                "patients_waiting": safe_int(row.get("Nombre_total_de_patients_en_attente_de_PEC", "0")),
            }
            h["occupancy_rate"] = calculate_occupancy(h)
            hospitals.append(h)
            
            if not gov_time:
                gov_time = row.get("Mise_a_jour", "").strip()
        
        if hospitals:
            print(f"   ✅ Parsed {len(hospitals)} hospitals from CSV")
            return hospitals, gov_time
        
    except Exception as e:
        print(f"   ❌ CSV error: {e}")
    
    return None, None

def get_backup_data():
    """Layer 3: Load backup JSON file"""
    print("   🔍 LAYER 3: Backup file...")
    if os.path.exists(BACKUP_FILE):
        try:
            with open(BACKUP_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            hospitals = data.get("hospitals", [])
            gov_time = data.get("gov_data_timestamp", "")
            if hospitals:
                print(f"   ✅ Loaded {len(hospitals)} hospitals from backup")
                return hospitals, gov_time, "cached"
        except:
            pass
    print("   ❌ No backup available")
    return None, None, None

def calculate_global_stats(hospitals):
    if not hospitals:
        return {"total_patients": 0, "total_waiting": 0, "avg_occupancy": 0, "total_over_24h": 0, "total_over_48h": 0}
    
    with_stretchers = [h for h in hospitals if h["total_stretchers"] > 0]
    avg_occ = round(sum(h["occupancy_rate"] for h in with_stretchers) / len(with_stretchers), 1) if with_stretchers else 0
    
    return {
        "total_patients": sum(h["total_patients"] for h in hospitals),
        "total_waiting": sum(h["patients_waiting"] for h in hospitals),
        "avg_occupancy": avg_occ,
        "total_over_24h": sum(h["patients_over_24h"] for h in hospitals),
        "total_over_48h": sum(h["patients_over_48h"] for h in hospitals),
    }

def save_all(hospitals, global_stats, gov_time, freshness):
    now = datetime.now()
    
    data = {
        "last_update": now.isoformat(),
        "source": "MSSS / Gouvernement du Québec",
        "source_url": MSSS_DIRECT_URL,
        "gov_data_timestamp": gov_time,
        "data_freshness": freshness,
        "global_stats": global_stats,
        "hospitals": hospitals
    }
    
    # Save main file
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    # Save backup
    with open(BACKUP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    # Health check
    health = {
        "status": "healthy" if freshness == "live" else "degraded",
        "last_successful_run": now.isoformat(),
        "data_freshness": freshness,
        "total_hospitals": len(hospitals),
        "avg_occupancy": global_stats["avg_occupancy"],
        "source_layers": "CKAN Datastore API (primary), CSV fallback, JSON backup"
    }
    with open(HEALTH_FILE, "w", encoding="utf-8") as f:
        json.dump(health, f, indent=2)
    
    print(f"\n✅ SAVED: {len(hospitals)} hospitals | Occupancy: {global_stats['avg_occupancy']}% | Freshness: {freshness}")
    print(f"✅ Health check: {HEALTH_FILE}")

def main():
    print("=" * 60)
    print(f"MyVita ER Scraper — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    hospitals = None
    gov_time = ""
    freshness = "live"
    
    # LAYER 1: CKAN Datastore API (JSON directly — fastest, most reliable)
    hospitals, gov_time, _ = get_live_data()
    
    # LAYER 2: CSV via CKAN Resource API → download → parse
    if not hospitals:
        csv_url = get_csv_url()
        if not csv_url:
            csv_url = MSSS_DIRECT_URL
        hospitals, gov_time = download_csv_as_json(csv_url)
    
    # LAYER 3: Backup JSON file
    if not hospitals:
        hospitals, gov_time, freshness = get_backup_data()
    
    # FINAL: Save everything
    if hospitals:
        global_stats = calculate_global_stats(hospitals)
        save_all(hospitals, global_stats, gov_time, freshness)
        print("\n✅ Scrape complete!")
    else:
        print("\n❌ CRITICAL: All 3 layers failed!")
        health = {"status": "failed", "last_attempt": datetime.now().isoformat()}
        with open(HEALTH_FILE, "w") as f:
            json.dump(health, f, indent=2)

if __name__ == "__main__":
    main()
