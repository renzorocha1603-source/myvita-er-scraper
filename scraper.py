import csv
import io
import json
import os
import re
import time
import requests
from datetime import datetime

# ============================================================
# DATA SOURCES (4 layers)
# ============================================================
HTML_PAGE_URL = "https://www.quebec.ca/sante/systeme-et-services-de-sante/organisation-des-services/donnees-systeme-sante-quebecois-services/situation-urgences"
CKAN_DATASTORE_API = "https://www.donneesquebec.ca/api/3/action/datastore_search?resource_id=b256f87f-40ec-4c79-bdba-a23e9c50e741"
CKAN_RESOURCE_API = "https://www.donneesquebec.ca/api/3/action/resource_show?id=b256f87f-40ec-4c79-bdba-a23e9c50e741"
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

def parse_time_to_minutes(time_str):
    """Convert HH:MM to minutes"""
    try:
        parts = time_str.strip().split(':')
        if len(parts) == 2:
            hours = int(parts[0])
            minutes = int(parts[1])
            return hours * 60 + minutes
        return 0
    except:
        return 0


# ═══════════════════════════════════════════════════════════════
# LAYER 1: CSV DOWNLOAD (MSSS Direct — WORKS with Referer header)
# ═══════════════════════════════════════════════════════════════

def download_csv_as_json(url):
    """Layer 1: Download CSV from MSSS and parse it"""
    print(f"   [Layer 1] Downloading CSV from MSSS...")

    try:
        response = requests.get(
            url,
            headers=STEALTH_HEADERS,
            timeout=30
        )
        
        print(f"   Debug: HTTP Status = {response.status_code}")
        
        if response.status_code != 200:
            print(f"   ❌ HTTP {response.status_code}")
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

        # Print detected headers for debugging
        print(f"   Headers: {headers[:8]}")

        def find_column(headers, keywords):
            for header in headers:
                header_lower = header.lower().strip()
                if all(kw.lower() in header_lower for kw in keywords):
                    return header
            for header in headers:
                if keywords[0].lower() in header.lower().strip():
                    return header
            return ""

        col_nom = find_column(headers, ["installation"])
        col_region = find_column(headers, ["RSS", "region"])
        col_civieres_fonc = find_column(headers, ["civiere", "fonctionnelle"])
        col_civieres_occ = find_column(headers, ["civiere", "occupee"])
        col_24h = find_column(headers, ["24", "heures"])
        col_48h = find_column(headers, ["48", "heures"])
        col_total = find_column(headers, ["total", "patients", "urgence"])
        col_attente = find_column(headers, ["attente", "PEC"])

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
                "occupancy_rate": 0,
                "wait_time_minutes": 0,
                "wait_time_str": "",
                "stay_room_minutes": 0,
                "stay_stretcher_minutes": 0,
                "postal_code": "",
            }
            h["occupancy_rate"] = round((civieres_occupees / total_civieres) * 100, 1) if total_civieres > 0 else 0
            hospitals.append(h)

        if hospitals:
            print(f"   ✅ Layer 1 SUCCESS: {len(hospitals)} hospitals from CSV")
            return hospitals, gov_time
        else:
            print(f"   ⚠️ No hospitals parsed from CSV")

    except Exception as e:
        print(f"   ❌ CSV error: {e}")

    print("   ❌ Layer 1 failed")
    return None, None


# ═══════════════════════════════════════════════════════════════
# LAYER 2: PLAYWRIGHT HTML SCRAPER (fallback)
# ═══════════════════════════════════════════════════════════════

def parse_hospitals_from_html(html):
    """Parse hospital data from rendered HTML"""
    hospitals = []
    gov_time = ""

    last_update_match = re.search(
        r'Derni[èe]re mise[^:]*:\s*([^<\n]+)',
        html,
        re.IGNORECASE
    )
    if last_update_match:
        gov_time = last_update_match.group(1).strip()

    hospital_blocks = re.split(
        r'(?=(?:Centre|H[oô]pital|CHU|CHSLD|CLSC|Institut|Pavillon)[^<\n]{2,})',
        html
    )

    for block in hospital_blocks:
        name_match = re.search(
            r'((?:Centre|H[oô]pital|CHU|CHSLD|CLSC|Institut|Pavillon)[^<\n]{2,80})',
            block
        )
        if not name_match:
            continue

        name = name_match.group(1).strip()

        if 'Trouver' in name or 'Résultats' in name or 'Pagination' in name:
            continue

        postal_match = re.search(r'[A-Z]\d[A-Z]\s?\d[A-Z]\d', block)
        postal = postal_match.group(0) if postal_match else ""

        if not postal:
            continue

        wait_match = re.search(
            r'(?:Temps d[^:]*:\s*)(\d{1,2})\s*h\s*(\d{2})',
            block,
            re.IGNORECASE
        )
        if wait_match:
            wait_time_str = f"{wait_match.group(1)}:{wait_match.group(2)}"
        else:
            wait_match = re.search(
                r'(?:Temps d[^:]*:\s*)(\d{2}:\d{2})',
                block,
                re.IGNORECASE
            )
            wait_time_str = wait_match.group(1) if wait_match else ""
        
        wait_time_minutes = parse_time_to_minutes(wait_time_str) if wait_time_str else 0

        waiting_match = re.search(
            r'(?:Nombre de personnes qui attendent[^:]*:\s*)(\d+)',
            block,
            re.IGNORECASE
        )
        patients_waiting = safe_int(waiting_match.group(1)) if waiting_match else 0

        total_match = re.search(
            r'(?:Nombre total de personnes[^:]*:\s*)(\d+)',
            block,
            re.IGNORECASE
        )
        total_patients = safe_int(total_match.group(1)) if total_match else 0

        occ_match = re.search(
            r'(?:Taux d[^:]*:\s*)(\d+)%',
            block,
            re.IGNORECASE
        )
        occupancy_rate = safe_int(occ_match.group(1)) if occ_match else 0

        region_match = re.search(
            r'(?:Montréal|Laval|Montérégie|Capitale-Nationale|Outaouais|'
            r'Mauricie|Estrie|Lanaudière|Laurentides|Saguenay|'
            r'Bas-Saint-Laurent|Chaudière-Appalaches|Abitibi-Témiscamingue|'
            r'Côte-Nord|Nord-du-Québec|Gaspésie|Centre-du-Québec|Eeyou)',
            block
        )
        region = region_match.group(0) if region_match else ""

        h = {
            "name": name,
            "region": region,
            "total_stretchers": 0,
            "patients_on_stretcher": 0,
            "patients_over_24h": 0,
            "patients_over_48h": 0,
            "total_patients": total_patients,
            "patients_waiting": patients_waiting,
            "occupancy_rate": occupancy_rate,
            "wait_time_minutes": wait_time_minutes,
            "wait_time_str": wait_time_str,
            "stay_room_minutes": 0,
            "stay_stretcher_minutes": 0,
            "postal_code": postal,
        }

        hospitals.append(h)

    return hospitals, gov_time


def get_live_data_html():
    """Layer 2: Scrape the Quebec.ca page using Playwright"""
    print("   [Layer 2] HTML Scraper (Playwright + Quebec.ca)...")

    try:
        from playwright.sync_api import sync_playwright

        all_hospitals = []
        gov_time = ""
        seen_names = set()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            page.goto(HTML_PAGE_URL, wait_until='networkidle', timeout=60000)
            page.wait_for_timeout(8000)

            html = page.content()
            hospitals, gov_time = parse_hospitals_from_html(html)
            for h in hospitals:
                if h['name'] not in seen_names:
                    seen_names.add(h['name'])
                    all_hospitals.append(h)
            print(f"   Page 1: {len(hospitals)} hospitals (total: {len(all_hospitals)})")

            browser.close()

        if all_hospitals:
            print(f"   ✅ Layer 2 SUCCESS: {len(all_hospitals)} hospitals total")
            return all_hospitals, gov_time
        else:
            print(f"   ⚠️ No hospitals parsed")

    except Exception as e:
        print(f"   ❌ Layer 2 error: {e}")

    print("   ❌ Layer 2 failed")
    return None, None


# ═══════════════════════════════════════════════════════════════
# LAYER 3: CKAN DATASTORE API (old API)
# ═══════════════════════════════════════════════════════════════

def get_live_data_ckan():
    """Layer 3: CKAN Datastore API"""
    print("   [Layer 3] CKAN Datastore API...")

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
            print(f"   Debug: {len(records)} records")

            if records:
                hospitals = []
                gov_time = ""

                for record in records:
                    nom = safe_str(record.get("Nom_installation", ""))
                    if not nom or "Total" in nom or "Ensemble" in nom:
                        continue

                    h = {
                        "name": nom,
                        "region": safe_str(record.get("RSS", "")),
                        "total_stretchers": safe_int(record.get("Nombre_de_civieres_fonctionnelles", 0)),
                        "patients_on_stretcher": safe_int(record.get("Nombre_de_civieres_occupees", 0)),
                        "patients_over_24h": safe_int(record.get("Nombre_de_patients_sur_civiere_plus_de_24_heures", 0)),
                        "patients_over_48h": safe_int(record.get("Nombre_de_patients_sur_civiere_plus_de_48_heures", 0)),
                        "total_patients": safe_int(record.get("Nombre_total_de_patients_presents_a_lurgence", 0)),
                        "patients_waiting": safe_int(record.get("Nombre_total_de_patients_en_attente_de_PEC", 0)),
                        "occupancy_rate": 0,
                        "wait_time_minutes": 0,
                        "wait_time_str": "",
                        "stay_room_minutes": 0,
                        "stay_stretcher_minutes": 0,
                        "postal_code": "",
                    }
                    h["occupancy_rate"] = round((h["patients_on_stretcher"] / h["total_stretchers"]) * 100, 1) if h["total_stretchers"] > 0 else 0
                    hospitals.append(h)

                if hospitals:
                    print(f"   ✅ Layer 3 SUCCESS: {len(hospitals)} hospitals")
                    return hospitals, gov_time

    except Exception as e:
        print(f"   ❌ Layer 3 error: {e}")

    print("   ❌ Layer 3 failed")
    return None, None


# ═══════════════════════════════════════════════════════════════
# LAYER 4: BACKUP FILE
# ═══════════════════════════════════════════════════════════════

def get_backup_data():
    """Layer 4: Load backup JSON file"""
    print("   [Layer 4] Backup file...")
    if os.path.exists(BACKUP_FILE):
        try:
            with open(BACKUP_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            hospitals = data.get("hospitals", [])
            gov_time = data.get("gov_data_timestamp", "")
            if hospitals:
                print(f"   ✅ Layer 4 SUCCESS: {len(hospitals)} hospitals from backup")
                return hospitals, gov_time
        except:
            pass
    print("   ❌ No backup available")
    return None, None


# ═══════════════════════════════════════════════════════════════
# STATS & SAVE
# ═══════════════════════════════════════════════════════════════

def calculate_global_stats(hospitals):
    if not hospitals:
        return {"total_patients": 0, "total_waiting": 0, "avg_occupancy": 0, "total_over_24h": 0, "total_over_48h": 0}

    with_stretchers = [h for h in hospitals if h.get("total_stretchers", 0) > 0]
    avg_occ = round(sum(h["occupancy_rate"] for h in with_stretchers) / len(with_stretchers), 1) if with_stretchers else 0

    return {
        "total_patients": sum(h.get("total_patients", 0) for h in hospitals),
        "total_waiting": sum(h.get("patients_waiting", 0) for h in hospitals),
        "avg_occupancy": avg_occ,
        "total_over_24h": sum(h.get("patients_over_24h", 0) for h in hospitals),
        "total_over_48h": sum(h.get("patients_over_48h", 0) for h in hospitals),
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


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print(f"MyVita ER Scraper — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    hospitals = None
    gov_time = ""
    freshness = "live"

    # Layer 1: CSV download (MSSS direct — WORKS with Referer header)
    hospitals, gov_time = download_csv_as_json(MSSS_DIRECT_URL)

    # Layer 2: Playwright HTML scraper
    if not hospitals:
        hospitals, gov_time = get_live_data_html()

    # Layer 3: CKAN API
    if not hospitals:
        hospitals, gov_time = get_live_data_ckan()

    # Layer 4: Backup
    if not hospitals:
        hospitals, gov_time = get_backup_data()
        freshness = "cached"

    if hospitals:
        global_stats = calculate_global_stats(hospitals)
        save_all(hospitals, global_stats, gov_time, freshness)
        print("\n✅ Scrape complete!")
    else:
        print("\n❌ CRITICAL: All 4 layers failed!")


if __name__ == "__main__":
    main()
