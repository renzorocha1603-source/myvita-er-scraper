import csv
import json
import requests
from datetime import datetime

CSV_URL = "https://www.msss.gouv.qc.ca/professionnels/statistiques/documents/urgences/Releve_horaire_urgences_7jours_nbpers.csv"
OUTPUT_FILE = "er_data.json"

def download_csv():
    print(f"[{datetime.now()}] Downloading CSV...")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/csv,application/csv,text/plain',
        'Accept-Language': 'fr-CA,fr;q=0.9,en;q=0.8',
    }
    response = requests.get(CSV_URL, headers=headers)
    response.encoding = 'utf-8'
    if response.status_code == 200:
        with open("temp_er_data.csv", "w", encoding="utf-8") as f:
            f.write(response.text)
        print(f"Downloaded successfully ({len(response.text)} bytes)")
        return True
    else:
        print(f"Failed to download: {response.status_code}")
        return False

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
        rows = list(reader)
        
        for row in rows:
            nom = row.get("Nom_installation", "").strip()
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
        
        # Get government's official timestamp from the last row
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

def save_json(hospitals, global_stats, gov_update_time):
    data = {
        "last_update": datetime.now().isoformat(),
        "source": "MSSS / Gouvernement du Québec",
        "source_url": CSV_URL,
        "gov_data_timestamp": gov_update_time,  # ★ NEW: Government's official update time
        "global_stats": global_stats,
        "hospitals": hospitals
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Saved to {OUTPUT_FILE}")
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
        save_json(hospitals, global_stats, gov_update_time)
        print("✅ Scrape complete!")
    else:
        print("❌ Scrape failed!")

if __name__ == "__main__":
    main()
