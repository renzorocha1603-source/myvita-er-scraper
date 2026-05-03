import csv
import json
import requests
from datetime import datetime

# The official MSSS CSV URL
CSV_URL = "https://www.msss.gouv.qc.ca/professionnels/statistiques/documents/urgences/Releve_horaire_urgences_7jours_nbpers.csv"

# Output file
OUTPUT_FILE = "er_data.json"

def download_csv():
    """Download the latest CSV from MSSS"""
    print(f"[{datetime.now()}] Downloading CSV...")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
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

def safe_int(value):
    try:
        return int(value.strip())
    except (ValueError, AttributeError):
        return 0

def safe_float(value):
    try:
        return float(value.strip().replace(",", "."))
    except (ValueError, AttributeError):
        return 0.0

def parse_csv():
    """Parse the CSV and extract hospital data"""
    print("Parsing CSV...")
    
    hospitals = []
    
    with open("temp_er_data.csv", "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames
        
        col_nom = find_column(headers, ["installation", "établissement", "etablissement"])
        col_region = find_column(headers, ["RSS", "région", "region"])
        col_civieres_fonc = find_column(headers, ["civière", "fonctionnelle", "civiere"])
        col_civieres_occ = find_column(headers, ["civière", "occupée", "civiere", "occupee"])
        col_24h = find_column(headers, ["24", "heures", "civiere", "civière"])
        col_48h = find_column(headers, ["48", "heures", "civiere", "civière"])
        col_total = find_column(headers, ["total", "patients", "presents", "urgence"])
        col_attente = find_column(headers, ["attente", "PEC"])
        
        for row in reader:
            nom_installation = row.get(col_nom, "").strip()
            if "Total" in nom_installation or not nom_installation:
                continue
            
            total_civieres = safe_int(row.get(col_civieres_fonc, "0"))
            civieres_occupees = safe_int(row.get(col_civieres_occ, "0"))
            patients_24h = safe_int(row.get(col_24h, "0"))
            patients_48h = safe_int(row.get(col_48h, "0"))
            total_patients = safe_int(row.get(col_total, "0"))
            patients_attente = safe_int(row.get(col_attente, "0"))
            
            if total_civieres > 0:
                occupancy_rate = round((civieres_occupees / total_civieres) * 100, 1)
            else:
                occupancy_rate = 0
            
            hospital = {
                "name": nom_installation,
                "region": row.get(col_region, "").strip(),
                "total_stretchers": total_civieres,
                "patients_on_stretcher": civieres_occupees,
                "patients_over_24h": patients_24h,
                "patients_over_48h": patients_48h,
                "total_patients": total_patients,
                "patients_waiting": patients_attente,
                "occupancy_rate": occupancy_rate,
            }
            
            if hospital["name"]:
                hospitals.append(hospital)
    
    print(f"Parsed {len(hospitals)} hospitals")
    return hospitals

def calculate_global_stats(hospitals):
    """Calculate Quebec-wide statistics"""
    if not hospitals:
        return {"total_patients": 0, "total_waiting": 0, "avg_occupancy": 0, "total_over_24h": 0, "total_over_48h": 0}
    
    total_patients = sum(h["total_patients"] for h in hospitals)
    total_waiting = sum(h["patients_waiting"] for h in hospitals)
    total_over_24h = sum(h["patients_over_24h"] for h in hospitals)
    total_over_48h = sum(h["patients_over_48h"] for h in hospitals)
    avg_occupancy = round(sum(h["occupancy_rate"] for h in hospitals) / len(hospitals), 1)
    
    return {
        "total_patients": total_patients,
        "total_waiting": total_waiting,
        "avg_occupancy": avg_occupancy,
        "total_over_24h": total_over_24h,
        "total_over_48h": total_over_48h
    }

def save_json(hospitals, global_stats):
    """Save parsed data as JSON"""
    data = {
        "last_update": datetime.now().isoformat(),
        "source": "MSSS / Gouvernement du Québec",
        "source_url": CSV_URL,
        "global_stats": global_stats,
        "hospitals": hospitals
    }
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"Saved to {OUTPUT_FILE}")
    print(f"Total hospitals: {len(hospitals)}")
    print(f"Global occupancy: {global_stats['avg_occupancy']}%")
    print(f"Patients over 24h: {global_stats['total_over_24h']}")
    print(f"Patients over 48h: {global_stats['total_over_48h']}")

def main():
    """Main scraper function"""
    print("=" * 50)
    print("MyVita ER Scraper")
    print("=" * 50)
    
    if download_csv():
        hospitals = parse_csv()
        global_stats = calculate_global_stats(hospitals)
        save_json(hospitals, global_stats)
        print("✅ Scrape complete!")
    else:
        print("❌ Scrape failed!")

if __name__ == "__main__":
    main()
