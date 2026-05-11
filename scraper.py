import csv
import io
import json
import os
import time
import requests
from datetime import datetime

# ============================================================
# DATA SOURCES (3 layers)
# ============================================================
CKAN_DATASTORE_API = "https://www.donneesquebec.ca/api/3/action/datastore_search?resource_id=b256f87f-40ec-4c79-bdba-a23e9c50e741"
CKAN_RESOURCE_API = "https://www.donneesquebec.ca/api/3/action/resource_show?id=b256f87f-40ec-4c79-bdba-a23e9c50e741"
MSSS_DIRECT_URL = "https://www.msss.gouv.qc.ca/professionnels/statistiques/documents/urgences/Releve_horaire_urgences_7jours_nbpers.csv"

OUTPUT_FILE = "er_data.json"
BACKUP_FILE = "er_data_backup.json"
HEALTH_FILE = "health_check.json"

# ★ STEALTH: Realistic headers to avoid 403 blocks
STEALTH_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/csv,application/csv,text/plain,application/json,*/*',
    'Accept-Language': 'fr-CA,fr;q=0.9,en-CA;q=0.8,en;q=0.7',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Cache-Control': 'max-age=0',
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

def find_column(headers, keywords):
    """Find a column name by matching keywords (case-insensitive)"""
    for header in headers:
        header_lower = header.lower()
        if all(kw.lower() in header_lower for kw in keywords):
            return header
    for header in headers:
        if keywords[0].lower() in header.lower():
            return header
    return ""

def parse_hospital_from_json(record):
    """Parse a single hospital record from CKAN JSON"""
    return {
        "name": safe_str(record.get("Nom_installation", "")),
        "region": safe_str(record.get("RSS", "")),
        "total_stretchers": safe_int(record.get("Nombre_de_civieres_fonctionnelles", 0)),
        "patients_on_stretcher": safe_int(record.get("Nombre_de_civieres_occupees", 0)),
        "patients_over_24h": safe_int(record.get("Nombre_de_patients_sur_civiere_plus_de_24_heures", 0)),
        "patients_over_48h": safe_int(record.get("Nombre_de_patients_sur_civiere_plus_de_48_heures", 0)),
        "total_patients": safe_int(record.get("Nombre_total_de_patients_presents_a_lurgence", 0)),
        "patients_waiting": safe_int(record.get("Nombre_total_de_patients_en_attente_de_PEC", 0)),
        "occupancy_rate": 0,
    }

def calculate_occupancy(h):
    """Calculate occupancy rate"""
    if h["total_stretchers"] > 0:
        return round((h["patients_on_stretcher"] / h["total_stretchers"]) * 100, 1)
    return 0

def get_live_data():
    """Layer 1: CKAN Datastore API — returns JSON directly"""
    print("   [Layer 1] CKAN Datastore API...")
    
    url = f"{CKAN_DATASTORE_API}&limit=200"
    
    try:
        response = requests.get(
            url, 
            headers=STEALTH_HEADERS,
            timeout=30
        )
        
        print(f"   Debug: HTTP Status = {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            records = data.get("result", {}).get("records", [])
            total = data.get("result", {}).get("total", 0)
            print(f"   Debug: API reports {total} total records, received {len(records)}")
            
            if records:
                hospitals = []
                gov_time = ""
                
                for record in records:
                    nom = safe_str(record.get("Nom_installation", ""))
                    if not nom or "Total" in nom or "Ensemble" in nom:
                        continue
                    
                    h = parse_hospital_from_json(record)
                    h["occupancy_rate"] = calculate_occupancy(h)
                    hospitals.append(h)
                    
                    if not gov_time:
                        gov_time = safe_str(record.get("Mise_a_jour", ""))
                
                if hospitals:
                    print(f"   ✅ Layer 1 SUCCESS: {len(hospitals)} hospitals")
                    return hospitals, gov_time
                else:
                    print(f"   ⚠️ No hospitals parsed (filtered out)")
            else:
                print(f"   ⚠️ No records in API response")
        else:
            print(f"   ❌ HTTP {response.status_code}: {response.text[:200]}")
            
    except Exception as e:
        print(f"   ❌ Layer 1 error: {e}")
    
    print("   ❌ Layer 1 failed")
    return None, None

def get_csv_url():
    """Layer 2a: Get CSV URL from CKAN Resource API"""
    print("   [Layer 2a] CKAN Resource API...")
    
    try:
        response = requests.get(
            CKAN_RESOURCE_API, 
            headers=STEALTH_HEADERS, 
            timeout=15
        )
        if response.status_code == 200:
            data = response.json()
            csv_url = data.get("result", {}).get("url", "")
            if csv_url:
                print(f"   ✅ Got CSV URL")
                return csv_url
    except Exception as e:
        print(f"   ⚠️ Error: {e}")
    
    print("   ⚠️ Using direct MSSS URL")
    return MSSS_DIRECT_URL

def download_csv_as_json(url):
    """Layer 2b: Download CSV and parse it — ★ STEALTH HEADERS to avoid 403"""
    print(f"   [Layer 2b] Downloading CSV...")
    
    try:
        # ★ Use stealth headers to avoid 403 Forbidden
        response = requests.get(
            url, 
            headers=STEALTH_HEADERS,
            timeout=30
        )
        if response.status_code != 200:
            print(f"   ❌ HTTP {response.status_code}")
            # Retry once with delay
            time.sleep(2)
            response = requests.get(url, headers=STEALTH_HEADERS, timeout=30)
            if response.status_code != 200:
                print(f"   ❌ Retry also failed: HTTP {response.status_code}")
                return None, None
        
        content = response.content
        text = None
        for encoding in ['utf-8-sig', 'utf-8', 'latin-1', 'iso-8859-1']:
            try:
                text = content.decode(encoding)
                if 'installation' in text.lower() or 'etablissement' in text.lower():
                    break
            except:
                continue
        
        if not text:
            print("   ❌ Could not decode CSV")
            return None, None
        
        reader = csv.DictReader(io.StringIO(text))
        headers = reader.fieldnames or []
        
        col_nom = find_column(headers, ["installation", "etablissement"])
        col_region = find_column(headers, ["RSS", "region"])
        col_civieres_fonc = find_column(headers, ["civiere", "fonctionnelle", "civieres", "fonctionnelles"])
        col_civieres_occ = find_column(headers, ["civiere", "occupee", "occupees", "civieres"])
        col_24h = find_column(headers, ["24", "heures", "civiere"])
        col_48h = find_column(headers, ["48", "heures", "civiere"])
        col_total = find_column(headers, ["total", "patients", "presents", "urgence"])
        col_attente = find_column(headers, ["attente", "PEC"])
        col_miseajour = find_column(headers, ["Mise", "jour"])
        
        print(f"   Detected columns: nom={col_nom}, civieres_fonc={col_civieres_fonc}, civieres_occ={col_civieres_occ}")
        
        hospitals = []
        gov_time = ""
        
        for row in reader:
            nom = safe_str(row.get(col_nom, ""))
            if not nom or "Total" in nom or "Ensemble" in nom:
                continue
            
            total_civieres = safe_int(row.get(col_civieres_fonc, "0"))
            civieres_occupees = safe_int(row.get(col_civieres_occ, "0"))
            
            h = {
                "name": nom,
                "region": safe_str(row.get(col_region, "")),
                "total_stretchers": total_civieres,
                "patients_on_stretcher": civieres_occupees,
                "patients_over_24h": safe_int(row.get(col_24h, "0")),
                "patients_over_48h": safe_int(row.get(col_48h, "0")),
                "total_patients": safe_int(row.get(col_total, "0")),
                "patients_waiting": safe_int(row.get(col_attente, "0")),
            }
            h["occupancy_rate"] = calculate_occupancy(h)
            hospitals.append(h)
            
            if not gov_time and col_miseajour:
                gov_time = safe_str(row.get(col_miseajour, ""))
        
        if hospitals:
            print(f"   ✅ Layer 2b SUCCESS: {len(hospitals)} hospitals from CSV")
            return hospitals, gov_time
        
    except Exception as e:
        print(f"   ❌ CSV error: {e}")
    
    return None, None

def get_backup_data():
    """Layer 3: Load backup JSON file"""
    print("   [Layer 3] Backup file...")
    if os.path.exists(BACKUP_FILE):
        try:
            with open(BACKUP_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            hospitals = data.get("hospitals", [])
            gov_time = data.get("gov_data_timestamp", "")
            if hospitals:
                print(f"   ✅ Layer 3 SUCCESS: {len(hospitals)} hospitals from backup")
                return hospitals, gov_time
        except:
            pass
    print("   ❌ No backup available")
    return None, None

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

def main():
    print("=" * 60)
    print(f"MyVita ER Scraper — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    hospitals = None
    gov_time = ""
    freshness = "live"
    
    # Layer 1: CKAN Datastore API
    hospitals, gov_time = get_live_data()
    
    # Layer 2: CSV fallback with stealth headers
    if not hospitals:
        csv_url = get_csv_url()
        hospitals, gov_time = download_csv_as_json(csv_url)
    
    # Layer 3: Backup
    if not hospitals:
        hospitals, gov_time = get_backup_data()
        freshness = "cached"
    
    if hospitals:
        global_stats = calculate_global_stats(hospitals)
        save_all(hospitals, global_stats, gov_time, freshness)
        print("\n✅ Scrape complete!")
    else:
        print("\n❌ CRITICAL: All 3 layers failed!")

if __name__ == "__main__":
    main()
