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
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,text/csv,application/csv,application/json,*/*;q=0.8',
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
# LAYER 1: HTML SCRAPER WITH PLAYWRIGHT (JS-rendered page)
# ═══════════════════════════════════════════════════════════════

def get_live_data_html():
    """Layer 1: Scrape the Quebec.ca ER page using Playwright"""
    print("   [Layer 1] HTML Scraper (Playwright + Quebec.ca)...")

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            page.goto(HTML_PAGE_URL, wait_until='networkidle', timeout=60000)

            # Wait for data to render
            page.wait_for_timeout(8000)

            # Get the rendered HTML
            html = page.content()

            browser.close()

        print(f"   Debug: Rendered HTML length = {len(html)}")

        # Check if data is present
        if 'Temps d' not in html and 'temps d' not in html:
            print(f"   ⚠️ No ER data found in rendered HTML")
            return None, None

        hospitals = []
        gov_time = ""

        # Extract last update time
        last_update_match = re.search(
            r'Derni[èe]re mise[^:]*:\s*([^<\n]+)',
            html,
            re.IGNORECASE
        )
        if last_update_match:
            gov_time = last_update_match.group(1).strip()
            print(f"   Gov time: {gov_time}")

        # Find all hospital blocks
        hospital_blocks = re.split(
            r'(?=(?:Centre|H[oô]pital|CHU|CHSLD|CLSC|Institut|Pavillon)[^<\n]{2,})',
            html
        )

        for block in hospital_blocks:
            # Extract hospital name
            name_match = re.search(
                r'((?:Centre|H[oô]pital|CHU|CHSLD|CLSC|Institut|Pavillon)[^<\n]{2,80})',
                block
            )
            if not name_match:
                continue

            name = name_match.group(1).strip()

            # Skip navigation blocks
            if 'Trouver' in name or 'Résultats' in name or 'Pagination' in name:
                continue

            # Extract postal code
            postal_match = re.search(r'[A-Z]\d[A-Z]\s?\d[A-Z]\d', block)
            postal = postal_match.group(0) if postal_match else ""

            # Skip if no postal code (not a facility block)
            if not postal:
                continue

            # Extract wait time (HH:MM)
            wait_match = re.search(
                r'(?:Temps d[^:]*:\s*)(\d{2}:\d{2})',
                block,
                re.IGNORECASE
            )
            wait_time_str = wait_match.group(1) if wait_match else ""
            wait_time_minutes = parse_time_to_minutes(wait_time_str) if wait_time_str else 0

            # Extract people waiting
            waiting_match = re.search(
                r'(?:Nombre de personnes qui attendent[^:]*:\s*)(\d+)',
                block,
                re.IGNORECASE
            )
            patients_waiting = safe_int(waiting_match.group(1)) if waiting_match else 0

            # Extract total patients
            total_match = re.search(
                r'(?:Nombre total de personnes[^:]*:\s*)(\d+)',
                block,
                re.IGNORECASE
            )
            total_patients = safe_int(total_match.group(1)) if total_match else 0

            # Extract occupancy rate
            occ_match = re.search(
                r'(?:Taux d[^:]*:\s*)(\d+)%',
                block,
                re.IGNORECASE
            )
            occupancy_rate = safe_int(occ_match.group(1)) if occ_match else 0

            # Extract region
            region_match = re.search(
                r'(?:Montréal|Laval|Montérégie|Capitale-Nationale|Outaouais|'
                r'Mauricie|Estrie|Lanaudière|Laurentides|Saguenay|'
                r'Bas-Saint-Laurent|Chaudière-Appalaches|Abitibi-Témiscamingue|'
                r'Côte-Nord|Nord-du-Québec|Gaspésie|Centre-du-Québec|Eeyou)',
                block
            )
            region = region_match.group(0) if region_match else ""

            # Extract average stay in waiting room (HH:MM)
            stay_room_match = re.search(
                r'(?:Durée moyenne de séjour[^:]*salle d[^:]*:\s*)(\d{2}:\d{2})',
                block,
                re.IGNORECASE
            )
            stay_room_str = stay_room_match.group(1) if stay_room_match else ""
            stay_room_minutes = parse_time_to_minutes(stay_room_str) if stay_room_str else 0

            # Extract average stay on stretcher (HH:MM)
            stay_stretcher_match = re.search(
                r'(?:Durée moyenne de séjour[^:]*civi[èe]re[^:]*:\s*)(\d{2}:\d{2})',
                block,
                re.IGNORECASE
            )
            stay_stretcher_str = stay_stretcher_match.group(1) if stay_stretcher_match else ""
            stay_stretcher_minutes = parse_time_to_minutes(stay_stretcher_str) if stay_stretcher_str else 0

            # Build hospital dict
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
                "stay_room_minutes": stay_room_minutes,
                "stay_stretcher_minutes": stay_stretcher_minutes,
                "postal_code": postal,
            }

            hospitals.append(h)

        if hospitals:
            print(f"   ✅ Layer 1 SUCCESS: {len(hospitals)} hospitals from rendered HTML")
            return hospitals, gov_time
        else:
            print(f"   ⚠️ No hospitals parsed from HTML")

    except Exception as e:
        print(f"   ❌ Layer 1 error: {e}")

    print("   ❌ Layer 1 failed")
    return None, None


# ═══════════════════════════════════════════════════════════════
# LAYER 2: CKAN DATASTORE API (old API — might come back)
# ═══════════════════════════════════════════════════════════════

def get_live_data_ckan():
    """Layer 2: CKAN Datastore API — returns JSON directly"""
    print("   [Layer 2] CKAN Datastore API...")

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

                    if not gov_time:
                        gov_time = safe_str(record.get("Mise_a_jour", ""))

                if hospitals:
                    print(f"   ✅ Layer 2 SUCCESS: {len(hospitals)} hospitals")
                    return hospitals, gov_time
            else:
                print(f"   ⚠️ No records in API response")
        else:
            print(f"   ❌ HTTP {response.status_code}")

    except Exception as e:
        print(f"   ❌ Layer 2 error: {e}")

    print("   ❌ Layer 2 failed")
    return None, None


# ═══════════════════════════════════════════════════════════════
# LAYER 3: CSV DOWNLOAD (old MSSS URL — might come back)
# ═══════════════════════════════════════════════════════════════

def get_csv_url():
    """Layer 3a: Get CSV URL from CKAN Resource API"""
    print("   [Layer 3a] CKAN Resource API...")

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
    """Layer 3b: Download CSV and parse it"""
    print(f"   [Layer 3b] Downloading CSV...")

    try:
        response = requests.get(
            url,
            headers=STEALTH_HEADERS,
            timeout=30
        )
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

        def find_column(headers, keywords):
            for header in headers:
                header_lower = header.lower()
                if all(kw.lower() in header_lower for kw in keywords):
                    return header
            for header in headers:
                if keywords[0].lower() in header.lower():
                    return header
            return ""

        col_nom = find_column(headers, ["installation", "etablissement"])
        col_region = find_column(headers, ["RSS", "region"])
        col_civieres_fonc = find_column(headers, ["civiere", "fonctionnelle"])
        col_civieres_occ = find_column(headers, ["civiere", "occupee"])
        col_24h = find_column(headers, ["24", "heures", "civiere"])
        col_48h = find_column(headers, ["48", "heures", "civiere"])
        col_total = find_column(headers, ["total", "patients", "urgence"])
        col_attente = find_column(headers, ["attente", "PEC"])

        hospitals = []
        gov_time = ""

        for row in reader:
            nom = safe_str(row.get(col_nom, ""))
            if not nom or "Total" in nom or "Ensemble" in nom:
                continue

            h = {
                "name": nom,
                "region": safe_str(row.get(col_region, "")),
                "total_stretchers": safe_int(row.get(col_civieres_fonc, "0")),
                "patients_on_stretcher": safe_int(row.get(col_civieres_occ, "0")),
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
            h["occupancy_rate"] = round((h["patients_on_stretcher"] / h["total_stretchers"]) * 100, 1) if h["total_stretchers"] > 0 else 0
            hospitals.append(h)

        if hospitals:
            print(f"   ✅ Layer 3b SUCCESS: {len(hospitals)} hospitals from CSV")
            return hospitals, gov_time

    except Exception as e:
        print(f"   ❌ CSV error: {e}")

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
        "source_url": HTML_PAGE_URL,
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

    # Layer 1: Playwright HTML Scraper (Quebec.ca ER page)
    hospitals, gov_time = get_live_data_html()

    # Layer 2: CKAN API (old — might come back)
    if not hospitals:
        hospitals, gov_time = get_live_data_ckan()

    # Layer 3: CSV download (old MSSS URL)
    if not hospitals:
        csv_url = get_csv_url()
        hospitals, gov_time = download_csv_as_json(csv_url)

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
