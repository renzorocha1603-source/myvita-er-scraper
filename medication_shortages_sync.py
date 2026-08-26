"""
MyVita — Medication Shortages Sync
Fetches Health Product Shortages Canada data and syncs to Firestore.
Uses Playwright (browser) + copy-paste text extraction like ER scraper.
Runs every 12 hours via GitHub Actions.
"""

import json
import os
import re
import time
from datetime import datetime, timezone
from typing import List, Dict, Any

import firebase_admin
from firebase_admin import credentials, firestore

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# ★ French URL (the site is bilingual)
BASE_URL = "https://penuriesdeproduitsdesante.ca"
SEARCH_PATH = "/search"

# Also try the English URL as fallback
BASE_URL_EN = "https://www.drugshortagescanada.ca"

FIRESTORE_COLLECTION = "medication_shortages"
FIREBASE_CREDENTIALS_JSON = os.environ.get("MYVITA_FIREBASE_SERVICE_ACCOUNT", "")

# Active statuses to fetch
ACTIVE_STATUSES = ["active_confirmed", "anticipated_shortage", "avoided_shortage"]

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
# PLAYWRIGHT COPY-PASTE FETCH
# ═══════════════════════════════════════════════════════════════

def fetch_page_text_with_playwright(url: str) -> str:
    """Fetch page using Playwright and return body inner_text (copy-paste method)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        page.goto(url, wait_until='domcontentloaded', timeout=45000)
        page.wait_for_timeout(8000)
        
        body_text = page.locator('body').inner_text()
        browser.close()
    
    return body_text


# ═══════════════════════════════════════════════════════════════
# COPY-PASTE PARSER (like ER scraper)
# ═══════════════════════════════════════════════════════════════

def parse_shortages_from_text(body_text: str) -> List[Dict[str, str]]:
    """Parse shortage data from body text using line-by-line extraction."""
    shortages = []
    lines = body_text.split('\n')
    
    current_shortage = None
    current_lines = []
    
    # Status translations (French → internal)
    status_map = {
        'Pénurie réelle': 'active_confirmed',
        'Actual shortage': 'active_confirmed',
        'Pénurie prévue': 'anticipated_shortage',
        'Anticipated shortage': 'anticipated_shortage',
        'Pénurie évitée': 'avoided_shortage',
        'Avoided shortage': 'avoided_shortage',
        'Résorbée': 'resolved',
        'Resolved': 'resolved',
        'Discontinué': 'discontinued',
        'Discontinued': 'discontinued',
        'Discontinué sous peu': 'to_be_discontinued',
        'To be discontinued': 'to_be_discontinued',
    }
    
    # Keywords that indicate a new shortage entry
    # Each entry has: Brand Name, Company, Status, Strength, Report ID
    # We detect entries by finding report ID (6-digit number) at end of line
    
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        
        # Check if this line contains a report ID (the "Afficher" link value)
        # Report IDs are 6-digit numbers
        report_match = re.search(r'(\d{6})\s*$', line_stripped)
        
        if report_match and current_shortage is not None:
            # This line has the report ID - it's the LAST line of the entry
            report_id = report_match.group(1)
            current_lines.append(line_stripped)
            
            # Parse the collected lines
            full_text = '\n'.join(current_lines)
            shortage = parse_shortage_entry(full_text, report_id)
            if shortage:
                shortages.append(shortage)
            
            # Reset for next entry
            current_shortage = None
            current_lines = []
        
        elif is_brand_name(line_stripped):
            # New shortage entry starts with brand name (ALL CAPS usually)
            # Save previous if exists
            if current_shortage is not None and len(current_lines) >= 4:
                # Try to parse without report ID
                full_text = '\n'.join(current_lines)
                shortage = parse_shortage_entry(full_text, '')
                if shortage:
                    shortages.append(shortage)
            
            current_shortage = True
            current_lines = [line_stripped]
        
        elif current_shortage is not None:
            current_lines.append(line_stripped)
    
    # Don't forget the last one
    if current_shortage is not None and len(current_lines) >= 4:
        full_text = '\n'.join(current_lines)
        shortage = parse_shortage_entry(full_text, '')
        if shortage:
            shortages.append(shortage)
    
    return shortages


def is_brand_name(text: str) -> bool:
    """Check if a line looks like a brand name (starts with uppercase, contains no numbers for status/dates)."""
    if len(text) < 3:
        return False
    if text.startswith(('Rapports', 'Liste', 'Nom de', 'Légende', 'Télécharger', 'Page', 'Bienvenue', 'Ci-dessous')):
        return False
    # Brand names are usually ALL CAPS or start with uppercase
    return text[0].isupper() and not text.startswith(('Pénurie', 'Actual', 'Anticipated', 'Avoided', 'Résorbée', 'Resolved', 'Discontinué', 'Discontinued', 'Mis à', 'New', 'Nouveau'))


def parse_shortage_entry(full_text: str, report_id: str) -> Dict[str, str]:
    """Parse a single shortage entry from collected lines."""
    lines = [l.strip() for l in full_text.split('\n') if l.strip()]
    
    if len(lines) < 3:
        return None
    
    brand_name = lines[0]
    company_name = ''
    status = ''
    strength = ''
    last_update = ''
    
    # Find the fields in the lines
    for i, line in enumerate(lines[1:], 1):
        if 'Pénurie' in line or 'Shortage' in line or 'Discontinu' in line or 'Résorbée' in line or 'Resolved' in line:
            status = line
        elif 'MG' in line.upper() or 'MCG' in line.upper() or '%' in line or re.match(r'^\d+\.?\d*', line):
            strength = line
        elif 'Mis à jour' in line or 'Nouveau' in line or 'Updated' in line or 'New' in line:
            last_update = line
        elif re.match(r'^\d{4}-\d{2}-\d{2}', line):
            last_update = line
        elif len(line) > 5 and not re.match(r'^\d{4}-\d{2}-\d{2}', line):
            company_name = line
    
    # Map status to internal
    status_map = {
        'Pénurie réelle': 'active_confirmed',
        'Actual shortage': 'active_confirmed',
        'Pénurie prévue': 'anticipated_shortage',
        'Anticipated shortage': 'anticipated_shortage',
        'Pénurie évitée': 'avoided_shortage',
        'Avoided shortage': 'avoided_shortage',
        'Résorbée': 'resolved',
        'Resolved': 'resolved',
        'Discontinué': 'discontinued',
        'Discontinued': 'discontinued',
        'Discontinué sous peu': 'to_be_discontinued',
        'To be discontinued': 'to_be_discontinued',
    }
    
    internal_status = status_map.get(status, status.lower().replace(' ', '_'))
    
    return {
        'report_id': report_id,
        'brand_name': brand_name,
        'company_name': company_name,
        'strength': strength,
        'status': internal_status,
    }


# ═══════════════════════════════════════════════════════════════
# FETCH ALL SHORTAGES
# ═══════════════════════════════════════════════════════════════

def fetch_all_shortages() -> List[Dict[str, str]]:
    """Fetch all shortages from the website using copy-paste method."""
    print("   [Copy-Paste Scraper] Loading pages...")
    
    import urllib.parse
    
    all_shortages = []
    seen_report_ids = set()
    
    # Try French URL first, fallback to English
    urls_to_try = [
        f"{BASE_URL}{SEARCH_PATH}",
        f"{BASE_URL_EN}{SEARCH_PATH}",
    ]
    
    body_text = None
    used_url = None
    
    for url in urls_to_try:
        try:
            print(f"   Trying: {url}")
            body_text = fetch_page_text_with_playwright(url)
            if body_text and len(body_text) > 1000:
                used_url = url
                print(f"   ✅ Page loaded ({len(body_text)} chars)")
                break
        except Exception as e:
            print(f"   ❌ Failed: {e}")
    
    if not body_text:
        print("   ❌ Could not load any page")
        return []
    
    # Parse page 1
    shortages = parse_shortages_from_text(body_text)
    for s in shortages:
        rid = s.get('report_id', '')
        if rid and rid not in seen_report_ids:
            seen_report_ids.add(rid)
            all_shortages.append(s)
    
    print(f"   Page 1: {len(shortages)} shortages")
    
    # Try pagination (pages 2-5)
    base_url = used_url or f"{BASE_URL}{SEARCH_PATH}"
    
    for page_num in range(2, 6):
        try:
            page_url = f"{base_url}?page={page_num}"
            print(f"   Loading page {page_num}...")
            page_text = fetch_page_text_with_playwright(page_url)
            
            if not page_text or len(page_text) < 500:
                break
            
            page_shortages = parse_shortages_from_text(page_text)
            new_count = 0
            
            for s in page_shortages:
                rid = s.get('report_id', '')
                if rid and rid not in seen_report_ids:
                    seen_report_ids.add(rid)
                    all_shortages.append(s)
                    new_count += 1
            
            print(f"   Page {page_num}: {new_count} new (total: {len(all_shortages)})")
            
            if new_count == 0:
                break
                
        except Exception as e:
            print(f"   Page {page_num} error: {e}")
            break
    
    # Also try with search filters for active statuses
    for status in ACTIVE_STATUSES:
        try:
            import urllib.parse
            params = {"status": status, "report_published": "1"}
            filter_url = f"{used_url or f'{BASE_URL}{SEARCH_PATH}'}?{urllib.parse.urlencode(params)}"
            print(f"   Filtering: {status}...")
            
            filter_text = fetch_page_text_with_playwright(filter_url)
            if filter_text and len(filter_text) > 500:
                filter_shortages = parse_shortages_from_text(filter_text)
                new_count = 0
                
                for s in filter_shortages:
                    rid = s.get('report_id', '')
                    if rid and rid not in seen_report_ids:
                        seen_report_ids.add(rid)
                        all_shortages.append(s)
                        new_count += 1
                
                print(f"   {status}: {new_count} new (total: {len(all_shortages)})")
        except Exception as e:
            print(f"   {status} error: {e}")
    
    print(f"   ✅ TOTAL: {len(all_shortages)} unique shortages")
    return all_shortages


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
        shortage.get('status', ''),
    ]

    for field in fields:
        if field:
            keywords.append(field.lower())
            normalized = field.lower()
            for old, new in [('é', 'e'), ('è', 'e'), ('ê', 'e'), ('à', 'a'), ('ç', 'c')]:
                normalized = normalized.replace(old, new)
            if normalized != field.lower():
                keywords.append(normalized)

    return list(set(k for k in keywords if k))


def transform_shortage(raw: Dict[str, str]) -> Dict[str, Any]:
    """Transform raw scraped data into Firestore document format."""
    status = raw.get('status', 'resolved')
    is_active = status in ['active_confirmed', 'anticipated_shortage', 'avoided_shortage']
    
    return {
        'report_id': raw.get('report_id', ''),
        'brand_name': raw.get('brand_name', ''),
        'company_name': raw.get('company_name', ''),
        'strength': raw.get('strength', ''),
        'status': status,
        'is_active': is_active,
        'tier_3': 'N3' in raw.get('brand_name', ''),
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

    # Mark active shortages that are no longer active
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

    print(f"Synced {len(synced_ids)} shortages to Firestore")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("MyVita — Medication Shortages Sync (Copy-Paste)")
    print(f"Started at: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    db = init_firebase()
    print("Firebase initialized")

    raw_shortages = fetch_all_shortages()
    print(f"Fetched {len(raw_shortages)} raw shortage records")

    transformed = []
    for raw in raw_shortages:
        transformed.append(transform_shortage(raw))

    print(f"Transformed {len(transformed)} shortages")

    sync_to_firestore(db, transformed)

    print("=" * 60)
    print(f"Completed at: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
