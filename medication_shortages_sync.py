"""
MyVita — Medication Shortages Sync
Fetches Health Product Shortages Canada data and syncs to Firestore.
Uses requests + string parsing (no regex, no HTML parser complexity).
Runs every 12 hours via GitHub Actions.
"""

import json
import os
import requests
from datetime import datetime, timezone
from typing import List, Dict, Any

import firebase_admin
from firebase_admin import credentials, firestore

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

BASE_URL = "https://www.drugshortagescanada.ca"
SEARCH_PATH = "/search"
FIRESTORE_COLLECTION = "medication_shortages"
FIREBASE_CREDENTIALS_JSON = os.environ.get("MYVITA_FIREBASE_SERVICE_ACCOUNT", "")

# Active statuses to fetch
ACTIVE_STATUSES = ["active_confirmed", "anticipated_shortage", "avoided_shortage"]

# User-Agent to avoid blocking
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
}

# Timeout in seconds
TIMEOUT = 15


# ═══════════════════════════════════════════════════════════════
# FIREBASE INITIALIZATION
# ═══════════════════════════════════════════════════════════════

def init_firebase():
    """Initialize Firebase Admin SDK from environment credentials."""
    if not FIREBASE_CREDENTIALS_JSON:
        raise ValueError("MYVITA_FIREBASE_SERVICE_ACCOUNT environment variable not set")

    cred_dict = json.loads(FIREBASE_CREDENTIALS_JSON)
    cred = credentials.Certificate(cred_dict)

    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)

    return firestore.client()


# ═══════════════════════════════════════════════════════════════
# HTML SCRAPING (using simple string operations)
# ═══════════════════════════════════════════════════════════════

def extract_rows_from_html(html: str) -> List[Dict[str, str]]:
    """Extract shortage data from HTML using simple string operations."""
    rows = []
    current_pos = 0

    while True:
        # Find next row start
        row_start = html.find('<tr data-index=', current_pos)
        if row_start == -1:
            break

        # Find row end
        row_end = html.find('</tr>', row_start)
        if row_end == -1:
            break

        row_html = html[row_start:row_end]

        # Only include rows with shortage/discontinuance links
        if '/shortage/' in row_html or '/discontinuance/' in row_html:
            shortage = parse_row(row_html)
            if shortage:
                rows.append(shortage)

        current_pos = row_end

    return rows


def parse_row(row_html: str) -> Dict[str, str]:
    """Parse a single HTML table row into a shortage dict."""
    shortage = {
        'report_id': '',
        'brand_name': '',
        'company_name': '',
        'strength': '',
        'status': '',
    }

    # Extract report ID
    report_pos = row_html.find('/shortage/')
    if report_pos == -1:
        report_pos = row_html.find('/discontinuance/')
        id_prefix = '/discontinuance/'
    else:
        id_prefix = '/shortage/'

    if report_pos >= 0:
        id_start = report_pos + len(id_prefix)
        id_end = row_html.find('"', id_start)
        if id_end > id_start:
            shortage['report_id'] = row_html[id_start:id_end].strip()

    # Extract all titles (brand name and company name)
    titles = []
    t_pos = 0
    while True:
        title_start = row_html.find('title="', t_pos)
        if title_start == -1:
            break
        title_content_start = title_start + len('title="')
        title_content_end = row_html.find('"', title_content_start)
        if title_content_end == -1:
            break
        title_text = row_html[title_content_start:title_content_end].strip()
        if title_text and 'View Report' not in title_text:
            titles.append(title_text)
        t_pos = title_content_end

    # First title is typically brand name, second is company name
    if len(titles) >= 1:
        shortage['brand_name'] = titles[0]
    if len(titles) >= 2:
        shortage['company_name'] = titles[1]

    # Extract status from first <td> cell
    td_start = row_html.find('<td>')
    if td_start >= 0:
        td_content_start = td_start + len('<td>')
        td_content_end = row_html.find('</td>', td_content_start)
        if td_content_end > td_content_start:
            shortage['status'] = row_html[td_content_start:td_content_end].strip()

    # Extract strength (look for pattern after title)
    # Simple approach: get text between the 3rd and 4th <td>
    tds = []
    td_pos = 0
    while True:
        td_open = row_html.find('<td>', td_pos)
        if td_open == -1:
            break
        td_close = row_html.find('</td>', td_open)
        if td_close == -1:
            break
        tds.append(row_html[td_open + 4:td_close].strip())
        td_pos = td_close

    if len(tds) >= 4:
        shortage['strength'] = tds[3]

    return shortage


def fetch_search_results(status: str, page: int = 1, limit: int = 100) -> List[Dict[str, str]]:
    """Fetch search results for a specific status."""
    try:
        response = requests.get(
            f"{BASE_URL}{SEARCH_PATH}",
            params={
                "status": status,
                "limit": limit,
                "page": page,
                "report_published": "1",
            },
            headers=HEADERS,
            timeout=TIMEOUT,
        )

        if response.status_code != 200:
            print(f"  HTTP {response.status_code} for {status} page {page}")
            return []

        rows = extract_rows_from_html(response.text)
        print(f"  {status} page {page}: {len(rows)} results")
        return rows

    except requests.exceptions.Timeout:
        print(f"  TIMEOUT for {status} page {page}")
        return []
    except requests.exceptions.ConnectionError as e:
        print(f"  Connection error for {status}: {e}")
        return []
    except Exception as e:
        print(f"  Error fetching {status} page {page}: {e}")
        return []


def fetch_all_shortages() -> List[Dict[str, str]]:
    """Fetch active shortages across all statuses."""
    all_shortages = []

    for status in ACTIVE_STATUSES:
        print(f"Fetching status: {status}...")
        page = 1

        while page <= 5:  # Max 5 pages per status (500 items)
            results = fetch_search_results(status, page=page, limit=100)
            if not results:
                break

            all_shortages.extend(results)

            if len(results) < 100:
                break

            page += 1

    # Remove duplicates by report_id
    unique = {}
    for s in all_shortages:
        rid = s.get('report_id', '')
        if rid and rid not in unique:
            unique[rid] = s

    print(f"Total unique shortages: {len(unique)}")
    return list(unique.values())


# ═══════════════════════════════════════════════════════════════
# DATA TRANSFORMATION
# ═══════════════════════════════════════════════════════════════

def generate_search_keywords(shortage: Dict[str, str]) -> List[str]:
    """Generate search keywords from all fields."""
    keywords = []

    fields = [
        shortage.get('brand_name', ''),
        shortage.get('company_name', ''),
        shortage.get('report_id', ''),
        shortage.get('strength', ''),
    ]

    for field in fields:
        if field:
            keywords.append(field.lower())
            # Add without accents
            normalized = field.lower()
            for old, new in [('é', 'e'), ('è', 'e'), ('ê', 'e'), ('à', 'a'), ('ç', 'c')]:
                normalized = normalized.replace(old, new)
            if normalized != field.lower():
                keywords.append(normalized)

    return list(set(k for k in keywords if k))


def transform_shortage(raw: Dict[str, str], status_filter: str) -> Dict[str, Any]:
    """Transform raw scraped data into Firestore document format."""
    return {
        'report_id': raw.get('report_id', ''),
        'brand_name': raw.get('brand_name', ''),
        'company_name': raw.get('company_name', ''),
        'strength': raw.get('strength', ''),
        'status': status_filter,
        'is_active': True,
        'tier_3': False,  # Will be updated separately from Tier 3 page
        'search_keywords': generate_search_keywords(raw),
        'updated_at': datetime.now(timezone.utc),
    }


# ═══════════════════════════════════════════════════════════════
# FIRESTORE SYNC
# ═══════════════════════════════════════════════════════════════

def sync_to_firestore(db: firestore.Client, shortages: List[Dict[str, Any]]):
    """Sync shortages to Firestore."""
    collection_ref = db.collection(FIRESTORE_COLLECTION)

    synced_ids = set()
    batch = db.batch()
    batch_count = 0

    for shortage in shortages:
        report_id = shortage.get('report_id', '')
        if not report_id:
            continue

        synced_ids.add(report_id)
        doc_ref = collection_ref.document(report_id)
        batch.set(doc_ref, shortage, merge=True)
        batch_count += 1

        if batch_count >= 400:
            batch.commit()
            batch = db.batch()
            batch_count = 0

    if batch_count > 0:
        batch.commit()

    # Mark old docs as inactive
    existing_docs = collection_ref.where('is_active', '==', True).stream()
    stale_batch = db.batch()
    stale_count = 0

    for doc in existing_docs:
        if doc.id not in synced_ids:
            stale_batch.update(doc.reference, {'is_active': False, 'status': 'resolved'})
            stale_count += 1

            if stale_count >= 400:
                stale_batch.commit()
                stale_batch = db.batch()
                stale_count = 0

    if stale_count > 0:
        stale_batch.commit()

    print(f"Synced {len(synced_ids)} active shortages to Firestore")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("MyVita — Medication Shortages Sync")
    print(f"Started at: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    db = init_firebase()
    print("Firebase initialized")

    raw_shortages = fetch_all_shortages()
    print(f"Fetched {len(raw_shortages)} raw shortage records")

    transformed = []
    for raw in raw_shortages:
        # Determine which status filter this came from
        status = raw.get('status', 'active_confirmed')
        transformed.append(transform_shortage(raw, status))

    print(f"Transformed {len(transformed)} shortages")

    sync_to_firestore(db, transformed)

    print("=" * 60)
    print(f"Completed at: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
