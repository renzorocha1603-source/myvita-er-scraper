"""
MyVita — Medication Shortages Sync
Fetches Health Product Shortages Canada data and syncs to Firestore.
Uses Playwright + JavaScript table extraction.
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

BASE_URL = "https://penuriesdeproduitsdesante.ca"
SEARCH_PATH = "/search"
BASE_URL_EN = "https://www.drugshortagescanada.ca"
FIRESTORE_COLLECTION = "medication_shortages"
FIREBASE_CREDENTIALS_JSON = os.environ.get("MYVITA_FIREBASE_SERVICE_ACCOUNT", "")

# ═══════════════════════════════════════════════════════════════
# FIREBASE INITIALIZATION
# ═══════════════════════════════════════════════════════════════

def init_firebase():
    if not FIREBASE_CREDENTIALS_JSON:
        raise ValueError("MYVITA_FIREBASE_SERVICE_ACCOUNT environment variable not set")
    cred_dict = json.loads(FIREBASE_CREDENTIALS_JSON)
    cred = credentials.Certificate(cred_dict)
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)
    return firestore.client()


# ═══════════════════════════════════════════════════════════════
# STATUS MAPPING
# ═══════════════════════════════════════════════════════════════

STATUS_MAP = {
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


def parse_status(status_text: str) -> str:
    return STATUS_MAP.get(status_text.strip(), status_text.strip().lower().replace(' ', '_'))


# ═══════════════════════════════════════════════════════════════
# PLAYWRIGHT TABLE EXTRACTION
# ═══════════════════════════════════════════════════════════════

def extract_tables_with_playwright(url: str) -> List[List[str]]:
    """
    Extract ALL table rows using JavaScript.
    Returns list of rows, each row is a list of cell texts.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        page.goto(url, wait_until='domcontentloaded', timeout=45000)
        page.wait_for_timeout(10000)
        
        # JavaScript to extract all table data
        table_data = page.evaluate('''() => {
            const tables = document.querySelectorAll('table');
            const results = [];
            
            tables.forEach(table => {
                const rows = table.querySelectorAll('tr');
                rows.forEach(row => {
                    const cells = row.querySelectorAll('td, th');
                    if (cells.length >= 4) {
                        const rowData = [];
                        cells.forEach(cell => {
                            // Get text content, clean whitespace
                            let text = cell.innerText.trim().replace(/\\s+/g, ' ');
                            rowData.push(text);
                        });
                        // Only add rows with actual data
                        if (rowData.length > 0 && rowData[0].length > 0) {
                            results.push(rowData);
                        }
                    }
                });
            });
            
            return results;
        }''')
        
        # Also get the page HTML to find report IDs in links
        html_content = page.content()
        
        browser.close()
    
    return table_data, html_content


def extract_report_ids_from_html(html_content: str) -> List[str]:
    """Extract all report IDs from href links in the HTML."""
    report_ids = re.findall(r'/shortage/(\d+)', html_content)
    discontinuation_ids = re.findall(r'/discontinuance/(\d+)', html_content)
    return report_ids + discontinuation_ids


def parse_table_rows(table_data: List[List[str]], report_ids: List[str]) -> List[Dict[str, str]]:
    """
    Parse table rows into shortage entries.
    Each row has: [Brand Name, Company, Status, Strength, Update Type, Date, Report Link]
    """
    shortages = []
    
    for row_idx, row in enumerate(table_data):
        if len(row) < 3:
            continue
        
        brand_name = row[0] if len(row) > 0 else ''
        company_name = row[1] if len(row) > 1 else ''
        status_text = row[2] if len(row) > 2 else ''
        strength = row[3] if len(row) > 3 else ''
        
        # Skip header rows
        if not brand_name or 'Nom de marque' in brand_name or 'Brand name' in brand_name:
            continue
        
        # Find report ID - check if the row has one or use the row index
        report_id = ''
        
        # Look for 6-digit number in any cell
        for cell in row:
            match = re.search(r'(\d{6})', cell)
            if match:
                report_id = match.group(1)
                break
        
        # If no report ID in row, use the report_ids list
        if not report_id and row_idx < len(report_ids):
            report_id = report_ids[row_idx]
        
        if not report_id:
            continue
        
        # Map status
        internal_status = parse_status(status_text)
        
        shortages.append({
            'report_id': report_id,
            'brand_name': brand_name,
            'company_name': company_name,
            'strength': strength,
            'status': internal_status,
        })
    
    return shortages


# ═══════════════════════════════════════════════════════════════
# FETCH ALL DATA
# ═══════════════════════════════════════════════════════════════

def fetch_all_shortages() -> List[Dict[str, str]]:
    """Fetch all shortages and discontinuations from the website."""
    print("   [Table Extraction] Loading pages...")
    
    all_shortages = []
    seen_report_ids = set()
    
    urls_to_try = [
        f"{BASE_URL}{SEARCH_PATH}",
        f"{BASE_URL_EN}{SEARCH_PATH}",
    ]
    
    table_data = None
    html_content = None
    used_url = None
    
    for url in urls_to_try:
        try:
            print(f"   Trying: {url}")
            table_data, html_content = extract_tables_with_playwright(url)
            
            if table_data and len(table_data) > 0:
                used_url = url
                print(f"   ✅ Tables extracted: {len(table_data)} rows")
                break
        except Exception as e:
            print(f"   ❌ Failed: {e}")
    
    if not table_data:
        print("   ❌ Could not extract any table data")
        return []
    
    # Extract report IDs from HTML
    report_ids = extract_report_ids_from_html(html_content)
    print(f"   Report IDs found: {len(report_ids)}")
    
    # Parse table rows
    shortages = parse_table_rows(table_data, report_ids)
    
    for s in shortages:
        rid = s.get('report_id', '')
        if rid and rid not in seen_report_ids:
            seen_report_ids.add(rid)
            all_shortages.append(s)
    
    print(f"   ✅ TOTAL: {len(all_shortages)} unique entries")
    return all_shortages


# ═══════════════════════════════════════════════════════════════
# DATA TRANSFORMATION
# ═══════════════════════════════════════════════════════════════

def generate_search_keywords(shortage: Dict[str, str]) -> List[str]:
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
            normalized = field.lower()
            for old, new in [('é', 'e'), ('è', 'e'), ('ê', 'e'), ('à', 'a'), ('ç', 'c')]:
                normalized = normalized.replace(old, new)
            if normalized != field.lower():
                keywords.append(normalized)
    
    return list(set(k for k in keywords if k))


def transform_shortage(raw: Dict[str, str]) -> Dict[str, Any]:
    status = raw.get('status', 'resolved')
    is_active = status in ['active_confirmed', 'anticipated_shortage', 'avoided_shortage', 'to_be_discontinued']
    
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

def sync_to_firestore(db, shortages):
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
    print("MyVita — Medication Shortages Sync (Table Extraction)")
    print(f"Started at: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)
    
    db = init_firebase()
    print("Firebase initialized")
    
    raw_shortages = fetch_all_shortages()
    print(f"Fetched {len(raw_shortages)} raw records")
    
    transformed = []
    for raw in raw_shortages:
        transformed.append(transform_shortage(raw))
    
    print(f"Transformed {len(transformed)} records")
    
    sync_to_firestore(db, transformed)
    
    print("=" * 60)
    print(f"Completed at: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)


if __name__ == "__main__":
    main()