"""
MyVita — Medication Shortages Sync
Fetches Health Product Shortages Canada data and syncs to Firestore.
Uses Playwright copy-paste method (like ER scraper).
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
# PLAYWRIGHT COPY-PASTE FETCH
# ═══════════════════════════════════════════════════════════════

def fetch_page_text_with_playwright(url: str) -> str:
    """Fetch page using Playwright and return body inner_text."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        page.goto(url, wait_until='domcontentloaded', timeout=45000)
        page.wait_for_timeout(10000)
        
        body_text = page.locator('body').inner_text()
        browser.close()
    
    return body_text


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
    """Convert French/English status to internal name."""
    return STATUS_MAP.get(status_text.strip(), status_text.strip().lower().replace(' ', '_'))


# ═══════════════════════════════════════════════════════════════
# COPY-PASTE PARSER (like ER scraper)
# ═══════════════════════════════════════════════════════════════

def parse_shortages_from_text(body_text: str) -> List[Dict[str, str]]:
    """
    Parse shortage data from body text.
    The page has a table with these columns:
    Nom de marque | Nom de l'entreprise | État | Concentration(s) | Mise à jour | Dernière mise à jour | Afficher
    Each row is separated by newlines in inner_text().
    """
    shortages = []
    lines = body_text.split('\n')
    
    # Find where the shortage table starts
    start_idx = -1
    for i, line in enumerate(lines):
        if 'Rapports de pénurie' in line or 'Shortage Reports' in line:
            start_idx = i
            break
    
    if start_idx == -1:
        # Try without section header
        start_idx = 0
    
    # Process lines after finding the table
    current_entry = []
    
    for line in lines[start_idx+1:]:
        line_stripped = line.strip()
        
        # Stop when we hit the discontinuation section
        if 'Rapports de cessation' in line_stripped or 'Discontinuation Reports' in line_stripped:
            break
        
        # Skip header rows
        if 'Nom de marque' in line_stripped or 'Brand name' in line_stripped or 'Liste des rapports' in line_stripped:
            continue
        
        if not line_stripped:
            if current_entry:
                shortage = parse_entry(current_entry)
                if shortage:
                    shortages.append(shortage)
                current_entry = []
            continue
        
        # Skip non-data lines
        if line_stripped.startswith(('Rapports', 'Liste', 'Légende', 'Ci-dessous', 'Télécharger', 'Drug', 'Bienvenue', 'Page')):
            continue
        
        current_entry.append(line_stripped)
    
    # Don't forget the last entry
    if current_entry:
        shortage = parse_entry(current_entry)
        if shortage:
            shortages.append(shortage)
    
    return shortages


def parse_entry(lines: List[str]) -> Dict[str, str]:
    """
    Parse a shortage entry from collected lines.
    The structure from inner_text() is typically:
    [brand_name]
    [company_name]
    [status]
    [strength]
    [update_type]
    [date]
    [report_id]
    """
    if len(lines) < 3:
        return None
    
    brand_name = lines[0].strip()
    company_name = lines[1].strip() if len(lines) > 1 else ''
    status_text = ''
    strength = ''
    report_id = ''
    
    # Find status (look for known status words)
    for line in lines:
        stripped = line.strip()
        if stripped in STATUS_MAP:
            status_text = stripped
            break
    
    # Find strength (contains MG, MCG, %, or numeric)
    for line in lines:
        stripped = line.strip()
        if re.search(r'(MG|MCG|%)', stripped.upper()):
            strength = stripped
            break
    
    # Find report ID (6-digit number)
    for line in reversed(lines):
        match = re.search(r'(\d{6})', line.strip())
        if match:
            report_id = match.group(1)
            break
    
    if not brand_name or not report_id:
        return None
    
    # Map status
    internal_status = parse_status(status_text) if status_text else 'resolved'
    
    return {
        'report_id': report_id,
        'brand_name': brand_name,
        'company_name': company_name,
        'strength': strength,
        'status': internal_status,
    }


def parse_discontinuations_from_text(body_text: str) -> List[Dict[str, str]]:
    """
    Parse discontinuation reports from the same page.
    These appear after "Rapports de cessation de vente" section.
    """
    discontinuations = []
    lines = body_text.split('\n')
    
    # Find where the discontinuation table starts
    start_idx = -1
    for i, line in enumerate(lines):
        if 'Rapports de cessation' in line or 'Discontinuation Reports' in line:
            start_idx = i
            break
    
    if start_idx == -1:
        return []
    
    current_entry = []
    
    for line in lines[start_idx+1:]:
        line_stripped = line.strip()
        
        # Stop at page end
        if 'Télécharger' in line_stripped or 'Google Play' in line_stripped:
            break
        
        if 'Nom de marque' in line_stripped or 'Liste des rapports' in line_stripped:
            continue
        
        if not line_stripped:
            if current_entry:
                shortage = parse_entry(current_entry)
                if shortage:
                    discontinuations.append(shortage)
                current_entry = []
            continue
        
        if line_stripped.startswith(('Rapports', 'Liste', 'Légende', 'Télécharger')):
            continue
        
        current_entry.append(line_stripped)
    
    if current_entry:
        shortage = parse_entry(current_entry)
        if shortage:
            discontinuations.append(shortage)
    
    return discontinuations


# ═══════════════════════════════════════════════════════════════
# FETCH ALL DATA
# ═══════════════════════════════════════════════════════════════

def fetch_all_shortages() -> List[Dict[str, str]]:
    """Fetch all shortages and discontinuations from the website."""
    print("   [Copy-Paste Scraper] Loading pages...")
    
    all_shortages = []
    seen_report_ids = set()
    
    # Try the search page which has both shortages and discontinuations
    urls_to_try = [
        f"{BASE_URL}{SEARCH_PATH}",
        "https://www.drugshortagescanada.ca/search",
    ]
    
    body_text = None
    
    for url in urls_to_try:
        try:
            print(f"   Trying: {url}")
            body_text = fetch_page_text_with_playwright(url)
            if body_text and len(body_text) > 1000:
                print(f"   ✅ Page loaded ({len(body_text)} chars)")
                break
        except Exception as e:
            print(f"   ❌ Failed: {e}")
    
    if not body_text:
        print("   ❌ Could not load any page")
        return []
    
    # Parse shortages
    shortages = parse_shortages_from_text(body_text)
    for s in shortages:
        rid = s.get('report_id', '')
        if rid and rid not in seen_report_ids:
            seen_report_ids.add(rid)
            all_shortages.append(s)
    
    print(f"   Shortages: {len(shortages)} found")
    
    # Parse discontinuations
    discontinuations = parse_discontinuations_from_text(body_text)
    for d in discontinuations:
        rid = d.get('report_id', '')
        if rid and rid not in seen_report_ids:
            seen_report_ids.add(rid)
            all_shortages.append(d)
    
    print(f"   Discontinuations: {len(discontinuations)} found")
    
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
    print("MyVita — Medication Shortages Sync (Copy-Paste)")
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