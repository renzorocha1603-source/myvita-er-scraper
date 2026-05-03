import csv
import json
import requests
from datetime import datetime
import os
import random

# ============================================================
# DATA SOURCES (4 layers — tries each one in order)
# ============================================================
LAYER_1_CKAN_API = "https://www.donneesquebec.ca/api/3/action/resource_show?id=b256f87f-40ec-4c79-bdba-a23e9c50e741"
LAYER_2_MSSS_DIRECT = "https://www.msss.gouv.qc.ca/professionnels/statistiques/documents/urgences/Releve_horaire_urgences_7jours_nbpers.csv"
LAYER_3_MSSS_ALT = "https://www.msss.gouv.qc.ca/professionnels/statistiques/documents/urgences/Releve_horaire_urgences_7jours_nbpers.csv"
LAYER_4_BACKUP_FILE = "er_data_backup.json"

OUTPUT_FILE = "er_data.json"
HEALTH_FILE = "health_check.json"

def download_with_headers(url, content_type='csv'):
    """Download with proper browser headers"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/csv,application/csv,text/plain' if content_type == 'csv' else 'application/json',
        'Accept-Language': 'fr-CA,fr;q=0.9,en;q=0.8',
        'Cache-Control': 'no-cache, no-store, must-revalidate',
        'Pragma': 'no-cache',
        'Referer': 'https://www.quebec.ca/',
    }
    
    # Random cache buster
    cache_buster = random.randint(100000, 999999)
    if '?' in url:
        url = f"{url}&_cb={cache_buster}"
    else:
        url = f"{url}?_cb={cache_buster}"
    
    try:
        response = requests.get(url, headers=headers, timeout=45)
        return response
    except Exception as e:
        print(f"      Connection error: {e}")
        return None

def try_get_csv_url():
    """Try to get CSV URL from CKAN API"""
    print("   🔍 LAYER 1: CKAN API...")
    response = download_with_headers(LAYER_1_CKAN_API, 'json')
    if response and response.status_code == 200:
        try:
            data = response.json()
            csv_url = data.get("result", {}).get("url", "")
            if csv_url:
                print(f"   ✅ CKAN URL found: {csv_url[:80]}...")
                return csv_url
        except:
            pass
    print(f"   ❌ Layer 1 failed (HTTP {response.status_code if response else 'N/A'})")
    return None

def try_download_csv(url, layer_name):
    """Try to download CSV from a URL"""
    print(f"   🔍 {layer_name}: {url[:80]}...")
    response = download_with_headers(url, 'csv')
    
    if response and response.status_code == 200:
        content = response.content
        
        # Try multiple encodings
        for encoding in ['utf-8-sig', 'utf-8', 'latin-1', 'cp1252', 'iso-8859-1']:
            try:
                text = content.decode(encoding)
                if 'Nom_installation' in text or 'civieres' in text.lower():
                    print(f"   ✅ Downloaded ({len(text)} bytes, encoding: {encoding})")
                    return text
            except:
                continue
        
        # Last resort
        text = content.decode('utf-8', errors='replace')
        print(f"   ⚠️ Downloaded with fallback encoding ({len(text)} bytes)")
        return text
    
    print(f"   ❌ {layer_name} failed (HTTP {response.status_code if response else 'N/A'})")
    return None

def download_csv():
    """Multi-layer CSV download — tries 4 sources"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Downloading CSV...")
    
    # Layer 1: CKAN API → direct MSSS URL
    csv_url = try_get_csv_url()
    if csv_url:
        text = try_download_csv(csv_url, "LAYER 2: CKAN→MSSS")
        if text:
            save_csv(text)
            return True, "live"
    
    # Layer 2: Direct MSSS URL
    text = try_download_csv(LAYER_2_MSSS_DIRECT, "LAYER 2: MSSS Direct")
    if text:
        save_csv(text)
        return True, "live"
    
    # Layer 3: MSSS alternate URL (same URL, retry with different headers)
    print("   🔍 LAYER 3: MSSS Alt...")
    response = requests.get(LAYER_3_MSSS_ALT, 
        headers={'User-Agent': 'curl/7.68.0', 'Accept': '*/*'},
        timeout=45)
    if response.status_code == 200:
        text = response.text
        if 'Nom_installation' in text:
            save_csv(text)
            return True, "live"
    print(f"   ❌ Layer 3 failed")
    
    # Layer 4: Use backup file
    print("   🔍 LAYER 4: Backup file...")
    if os.path.exists(LAYER_4_BACKUP_FILE):
        print("   ✅ Using cached backup")
        return False, "cached"
    
    print("   ❌ ALL LAYERS FAILED — no data available")
    return False, "failed"

def save_csv(text):
    """Save downloaded CSV to temp file"""
    with open("temp_er_data.csv", "w", encoding="utf-8") as f:
        f.write(text)

def fix_french(text):
    """Fix common French character encoding issues"""
    if not text:
        return text
    replacements = {
        'Ã‰': 'É', 'Ãˆ': 'È', 'ÃŠ': 'Ê', 'Ã‹': 'Ë',
        'Ã©': 'é', 'Ã¨': 'è', 'Ãª': 'ê', 'Ã«': 'ë',
        'Ã´': 'ô', 'Ã»': 'û', 'Ã¹': 'ù', 'Ã®': 'î',
        'Ã¯': 'ï', 'Ã§': 'ç', 'Ã ': 'à', 'Ã¢': 'â',
        'Å"': 'œ',
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return text

def safe_int(value):
    try:
        return int(str(value).strip())
    except:
        return 0

def parse_csv():
    """Parse CSV and extract hospital data"""
    print("Parsing CSV...")
    hospitals = []
    gov_update_time = ""
    
    try:
        with open("temp_er_data.csv", "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            reader.fieldnames = [name.strip() for name in reader.fieldnames]
            rows = list(reader)
            
            for row in rows:
                nom = fix_french(row.get("Nom_installation", "").strip())
                if "Total" in nom or "Ensemble" in nom or not nom:
                    continue
                
                total_civieres = safe_int(row.get("Nombre_de_civieres_fonctionnelles", "0"))
                civieres_occupees = safe_int(row.get("Nombre_de_civieres_occupees", "0"))
                patients_24h = safe_int(row.get("Nombre_de_patients_sur_civiere_plus_de_24_heures", "0"))
                patients_48h = safe_int(row.get("Nombre_de_patients_sur_civiere_plus_de_48_heures", "0"))
                total_patients = safe_int(row.get("Nombre_total_de_patients_presents_a_lurgence", "0"))
                patients_attente = safe_int(row.get("Nombre_total_de_patients_en_attente_de_PEC", "0"))
                
                occupancy_rate = round((civieres_occupees / total_civieres) * 100, 1) if total_civieres > 0 else 0
                
                hospitals.append({
                    "name": nom,
                    "region": row.get("RSS", "").strip(),
                    "total_stretchers": total_civieres,
                    "patients_on_stretcher": civieres_occupees,
                    "patients_over_24h": patients_24h,
                    "patients_over_48h": patients_48h,
                    "total_patients": total_patients,
                    "patients_waiting": patients_attente,
                    "occupancy_rate": occupancy_rate,
                })
            
            if rows:
                gov_update_time = rows[-1].get("Mise_a_jour", "").strip()
    except Exception as e:
        print(f"❌ Parse error: {e}")
    
    print(f"✅ Parsed {len(hospitals)} hospitals")
    return hospitals, gov_update_time

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

def save_json(hospitals, global_stats, gov_update_time, freshness):
    now = datetime.now()
    data = {
        "last_update": now.isoformat(),
        "source": "MSSS / Gouvernement du Québec",
        "source_url": LAYER_2_MSSS_DIRECT,
        "gov_data_timestamp": gov_update_time,
        "data_freshness": freshness,
        "global_stats": global_stats,
        "hospitals": hospitals
    }
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    with open(LAYER_4_BACKUP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    # Health check
    health = {
        "status": "healthy" if freshness == "live" else "degraded",
        "last_successful_run": now.isoformat(),
        "data_freshness": freshness,
        "total_hospitals": len(hospitals),
        "avg_occupancy": global_stats["avg_occupancy"],
    }
    with open(HEALTH_FILE, "w", encoding="utf-8") as f:
        json.dump(health, f, indent=2)
    
    print(f"✅ Saved {len(hospitals)} hospitals | Occupancy: {global_stats['avg_occupancy']}% | Freshness: {freshness}")

def main():
    print("=" * 50)
    print(f"MyVita ER Scraper — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)
    
    success, freshness = download_csv()
    
    if success or (freshness == "cached" and os.path.exists(LAYER_4_BACKUP_FILE)):
        if freshness == "cached":
            print("⚠️ Using backup data")
            with open(LAYER_4_BACKUP_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            hospitals = data.get("hospitals", [])
            global_stats = data.get("global_stats", {})
            gov_time = data.get("gov_data_timestamp", "")
            save_json(hospitals, global_stats, gov_time, "cached")
        else:
            hospitals, gov_time = parse_csv()
            global_stats = calculate_global_stats(hospitals)
            save_json(hospitals, global_stats, gov_time, freshness)
        print("✅ Scrape complete!")
    else:
        print("❌ CRITICAL: All layers failed, no backup available!")
        health = {"status": "failed", "last_attempt": datetime.now().isoformat()}
        with open(HEALTH_FILE, "w") as f:
            json.dump(health, f, indent=2)

if __name__ == "__main__":
    main()
