import csv
import json
import requests
from datetime import datetime
import os

# Use CKAN API to get the actual download URL
CKAN_API_URL = "https://www.donneesquebec.ca/api/3/action/resource_show?id=b256f87f-40ec-4c79-bdba-a23e9c50e741"
DIRECT_CSV_URL = "https://www.msss.gouv.qc.ca/professionnels/statistiques/documents/urgences/Releve_horaire_urgences_7jours_nbpers.csv"
OUTPUT_FILE = "er_data.json"
BACKUP_FILE = "er_data_backup.json"

def download_csv():
    """Download the latest CSV via CKAN API with fallback to direct URL"""
    print(f"[{datetime.now()}] Getting download URL from CKAN API...")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
    }
    
    csv_url = None
    
    # Step 1: Try CKAN API
    try:
        ckan_response = requests.get(CKAN_API_URL, headers=headers, timeout=30)
        if ckan_response.status_code == 200:
            ckan_data = ckan_response.json()
            csv_url = ckan_data.get("result", {}).get("url", "")
            if csv_url:
                print(f"✅ CKAN API success: {csv_url}")
    except Exception as e:
        print(f"⚠️ CKAN API error: {e}")
    
    # Step 2: Fallback to direct URL
    if not csv_url:
        print("⚠️ CKAN failed, trying direct URL...")
        csv_url = DIRECT_CSV_URL
    
    # Step 3: Download the CSV with multiple encoding attempts
    print(f"[{datetime.now()}] Downloading CSV...")
    csv_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/csv,application/csv,text/plain',
        'Accept-Language': 'fr-CA,fr;q=0.9,en;q=0.8',
        'Referer': 'https://www.donneesquebec.ca/',
    }
    
    try:
        response = requests.get(csv_url, headers=csv_headers, timeout=30)
        
        if response.status_code == 200:
            # Try different encodings to fix French characters
            content = response.content
            text = None
            for encoding in ['utf-8-sig', 'utf-8', 'latin-1', 'cp1252', 'iso-8859-1']:
                try:
                    text = content.decode(encoding)
                    # Check if French characters decoded properly
                    if 'Ô' in text or 'É' in text or 'ê' in text or 'è' in text:
                        print(f"✅ Detected encoding: {encoding}")
                        break
                except:
                    continue
            
            if text is None:
                text = content.decode('utf-8', errors='replace')
                print("⚠️ Using fallback encoding (utf-8 with replace)")
            
            with open("temp_er_data.csv", "w", encoding="utf-8") as f:
                f.write(text)
            print(f"✅ Downloaded successfully ({len(response.text)} bytes)")
            return True
        else:
            print(f"❌ Failed to download CSV: HTTP {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Download error: {e}")
        return False

def fix_french(text):
    """Fix common French character encoding issues"""
    if not text:
        return text
    
    replacements = {
        'Ã‰': 'É', 'Ãˆ': 'È', 'ÃŠ': 'Ê', 'Ã‹': 'Ë',
        'Ã©': 'é', 'Ã¨': 'è', 'Ãª': 'ê', 'Ã«': 'ë',
        'Ã´': 'ô', 'Ã»': 'û', 'Ã¹': 'ù', 'Ã®': 'î',
        'Ã¯': 'ï', 'Ã§': 'ç', 'Ã ': 'à', 'Ã¢': 'â',
        'Å"': 'œ', 'â€"': '–', 'â€™': '\'',
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
    print("Parsing CSV...")
    hospitals = []
    gov_update_time = ""
    
    with open("temp_er_data.csv", "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        # Strip extra spaces/tabs from ALL column names
        reader.fieldnames = [name.strip() for name in reader.fieldnames]
        rows = list(reader)
        
        for row in rows:
            nom = fix_french(row.get("Nom_installation", "").strip())
            if "Total" in nom or not nom:
                continue
            
            total_civieres = safe_int(row.get("Nombre_de_civieres_fonctionnelles", "0"))
            civieres_occupees = safe_int(row.get("Nombre_de_civieres_occupees", "0"))
            patients_24h = safe_int(row.get("Nombre_de_patients_sur_civiere_plus_de_24_heures", "0"))
            patients_48h = safe_int(row.get("Nombre_de_patients_sur_civiere_plus_de_48_heures", "0"))
            total_patients = safe_int(row.get("Nombre_total_de_patients_presents_a_lurgence", "0"))
            patients_attente = safe_int(row.get("Nombre_total_de_patients_en_attente_de_PEC", "0"))
            
            if total_civieres > 0:
                occupancy_rate = round((civieres_occupees / total_civieres) * 100, 1)
            else:
                occupancy_rate = 0
            
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
    
    print(f"Parsed {len(hospitals)} hospitals")
    print(f"Government data timestamp: {gov_update_time}")
    return hospitals, gov_update_time

def calculate_global_stats(hospitals):
    if not hospitals:
        return {"total_patients": 0, "total_waiting": 0, "avg_occupancy": 0, "total_over_24h": 0, "total_over_48h": 0}
    
    total_patients = sum(h["total_patients"] for h in hospitals)
    total_waiting = sum(h["patients_waiting"] for h in hospitals)
    total_over_24h = sum(h["patients_over_24h"] for h in hospitals)
    total_over_48h = sum(h["patients_over_48h"] for h in hospitals)
    
    with_stretchers = [h for h in hospitals if h["total_stretchers"] > 0]
    if with_stretchers:
        avg_occupancy = round(sum(h["occupancy_rate"] for h in with_stretchers) / len(with_stretchers), 1)
    else:
        avg_occupancy = 0
    
    return {
        "total_patients": total_patients,
        "total_waiting": total_waiting,
        "avg_occupancy": avg_occupancy,
        "total_over_24h": total_over_24h,
        "total_over_48h": total_over_48h
    }

def save_json(hospitals, global_stats, gov_update_time, is_fresh=True):
    data = {
        "last_update": datetime.now().isoformat(),
        "source": "MSSS / Gouvernement du Québec",
        "source_url": DIRECT_CSV_URL,
        "gov_data_timestamp": gov_update_time,
        "data_freshness": "live" if is_fresh else "cached",
        "global_stats": global_stats,
        "hospitals": hospitals
    }
    
    # Save main file
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    # Create backup
    with open(BACKUP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"Saved to {OUTPUT_FILE} (backup: {BACKUP_FILE})")
    print(f"Total hospitals: {len(hospitals)}")
    print(f"Global occupancy: {global_stats['avg_occupancy']}%")
    print(f"Government data from: {gov_update_time}")

def main():
    print("=" * 50)
    print("MyVita ER Scraper")
    print("=" * 50)
    
    if download_csv():
        hospitals, gov_update_time = parse_csv()
        global_stats = calculate_global_stats(hospitals)
        save_json(hospitals, global_stats, gov_update_time, is_fresh=True)
        print("✅ Scrape complete!")
    else:
        # Try loading backup
        print("⚠️ Download failed, checking backup...")
        if os.path.exists(BACKUP_FILE):
            print("✅ Using cached backup data")
            with open(BACKUP_FILE, "r", encoding="utf-8") as f:
                backup_data = json.load(f)
            backup_data["data_freshness"] = "cached"
            backup_data["last_update"] = datetime.now().isoformat()
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(backup_data, f, ensure_ascii=False, indent=2)
            print("✅ Backup restored!")
        else:
            print("❌ Scrape failed — no backup available!")

if __name__ == "__main__":
    main()
