"""
MyVita — Medication Shortages Sync
Fetches Health Product Shortages Canada data and syncs to Firestore.
Uses Playwright + inner_text debugging.
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
# PLAYWRIGHT FETCH WITH DEBUG
# ═══════════════════════════════════════════════════════════════

def fetch_page_text_with_playwright(url: str) -> str:
    """Fetch page using Playwright and return body inner_text with debug."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        page.goto(url, wait_until='domcontentloaded', timeout=45000)
        page.wait_for_timeout(10000)
        
        body_text = page.locator('body').inner_text()
        
        # ★ DEBUG OUTPUT
        print(f"   Body text length: {len(body_text)}")
        print(f"   Contains tab character: {chr(9) in body_text}")
        print(f"   Contains 'Pénurie': {'Pénurie' in body_text}")
        print(f"   Contains 'Shortage': {'Shortage' in body_text}")
        print(f"   Contains 'OMEGA': {'OMEGA' in body_text}")
        print(f"   First 2000 chars:")
        print(body_text[:2000])
        print(f"   --- END DEBUG ---")
        
        browser.close()
    
    return body_text


# ═══════════════════════════════════════════════════════════════
# PARSER (will be refined after seeing debug output)
# ═══════════════════════════════════════════════════════════════

def parse_shortages_from_text(body_text: str) -> List[Dict[str, str]]:
    """Parse shortage data from body text."""
    shortages = []
    lines = body_text.split('\n')
    
    print(f"   Total lines: {len(lines)}")
    
    # Print first 50 lines with line numbers
    for i, line in enumerate(lines[:50]):
        print(f"   Line {i}: '{line.strip()[:100]}'")
    
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        
        # Skip headers and non-data lines
        if any(skip in line_stripped for skip in ['Nom de marque', 'Brand name', 'Liste des rapports', 'Rapports de']):
            continue
        
        # Check if line contains tab-separated data
        if '\t' in line_stripped:
            parts = line_stripped.split('\t')
            parts = [p.strip() for p in parts if p.strip()]
            
            if len(parts) >= 4:
                brand_name = parts[0]
                company_name = parts[1] if len(parts) > 1 else ''
                status_text = parts[2] if len(parts) > 2 else ''
                strength = parts[3] if len(parts) > 3 else ''
                report_id = ''
                
                # Find report ID in last part
                for part in reversed(parts):
                    match = re.search(r'(\d{6})', part)
                    if match:
                        report_id = match.group(1)
                        break
                
                if brand_name and report_id:
                    shortages.append({
                        'report_id': report_id,
                        'brand_name': brand_name,
                        'company_name': company_name,
                        'strength': strength,
                        'status': parse_status(status_text),
                    })
    
    return shortages


# ═══════════════════════════════════════════════════════════════
# FETCH ALL DATA
# ═══════════════════════════════════════════════════════════════

def fetch_all_shortages() -> List[Dict[str, str]]:
    """Fetch all shortages from the website."""
    print("   [Scraper] Loading pages...")
    
    all_shortages = []
    seen_report_ids = set()
    
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
            if body_text and len(body_text) > 500:
                used_url = url
                print(f"   ✅ Page loaded ({len(body_text)} chars)")
                break
        except Exception as e:
            print(f"   ❌ Failed: {e}")
    
    if not body_text:
        print("   ❌ Could not load any page")
        return []
    
    shortages = parse_shortages_from_text(body_text)
    
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
    print("MyVita — Medication Shortages Sync (Debug)")
    print(f"Started at: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)
    
    db = init_firebase()
    print("Firebase initialized")
    
    raw_shortages = fetch_all_shortages()
    print(f"Fetched {len(raw_shortages)} raw records")
    
    if raw_shortages:
        transformed = []
        for raw in raw_shortages:
            transformed.append(transform_shortage(raw))
        
        print(f"Transformed {len(transformed)} records")
        sync_to_firestore(db, transformed)
    else:
        print("No data to sync")
    
    print("=" * 60)
    print(f"Completed at: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)


if __name__ == "__main__":
    main()