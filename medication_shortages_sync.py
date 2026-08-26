"""
MyVita — Medication Shortages Sync (RAMQ Quebec + DEBUG)
Fetches Quebec-specific medication shortages from RAMQ.
"""

import json
import os
import re
from datetime import datetime, timezone
from typing import List, Dict, Any

import firebase_admin
from firebase_admin import credentials, firestore

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

RAMQ_URL = "https://www.ramq.gouv.qc.ca/fr/professionnels/pharmacien-pharmacienne/medicaments/ruptures-stock-signalees"
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
# PLAYWRIGHT FETCH WITH DEBUG
# ═══════════════════════════════════════════════════════════════

def fetch_page_with_playwright(url: str) -> str:
    """Fetch RAMQ page using Playwright with debug output."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        page.goto(url, wait_until='domcontentloaded', timeout=45000)
        page.wait_for_timeout(10000)
        
        body_text = page.locator('body').inner_text()
        
        # ★ DEBUG OUTPUT
        print(f"   Body text length: {len(body_text)}")
        print(f"   Contains 'Dénomination': {'Dénomination' in body_text}")
        print(f"   Contains 'Marque': {'Marque' in body_text}")
        print(f"   Contains 'DIN': {'DIN' in body_text}")
        print(f"   First 3000 chars:")
        print(body_text[:3000])
        print(f"   --- END DEBUG ---")
        
        # Also check if page loaded correctly
        html_content = page.content()
        print(f"   HTML length: {len(html_content)}")
        print(f"   HTML contains table: {'<table' in html_content}")
        print(f"   HTML contains 'rupture': {'rupture' in html_content.lower()}")
        
        browser.close()
    
    return body_text


# ═══════════════════════════════════════════════════════════════
# STATUS MAPPING
# ═══════════════════════════════════════════════════════════════

STATUS_MAP = {
    "Ce produit ou un produit de l'encadré est disponible": 'available',
    "Ce produit ou un produit de l'encadré sera disponible sous peu": 'available_soon',
    'En cours de vérification': 'verification',
    'Produit retiré après commercialisation': 'withdrawn',
    'Rupture confirmée': 'confirmed_shortage',
}


# ═══════════════════════════════════════════════════════════════
# PARSER
# ═══════════════════════════════════════════════════════════════

def parse_ramq_table(body_text: str) -> List[Dict[str, str]]:
    """Parse the RAMQ medication shortage table."""
    shortages = []
    lines = body_text.split('\n')
    
    print(f"   Total lines: {len(lines)}")
    
    # Print first 60 lines
    for i, line in enumerate(lines[:60]):
        print(f"   Line {i}: '{line.strip()[:120]}'")
    
    # Find table data
    start_idx = -1
    for i, line in enumerate(lines):
        if 'Dénomination' in line and 'Marque' in line:
            start_idx = i
            break
    
    if start_idx == -1:
        print("   ❌ Could not find table headers")
        return []
    
    print(f"   Table starts at line {start_idx}")
    
    # Parse entries after header
    current_entry = []
    
    for line in lines[start_idx+1:]:
        line_stripped = line.strip()
        
        # Stop conditions
        if 'Page ' in line_stripped and 'de' in line_stripped:
            break
        if 'Évaluer la page' in line_stripped:
            break
        if 'Il reste' in line_stripped:
            break
        
        # Skip header rows
        if 'Dénomination' in line_stripped or 'Marque de commerce' in line_stripped:
            continue
        
        if not line_stripped:
            if current_entry:
                shortage = parse_entry_with_debug(current_entry)
                if shortage:
                    shortages.append(shortage)
                current_entry = []
            continue
        
        # Skip footer/site nav lines
        if any(skip in line_stripped for skip in ['RAMQ', 'À propos', 'Nous joindre', 'Nous suivre', 'Suivre la RAMQ', 'Accessibilité', 'Politique', '©', 'Application de']):
            continue
        
        current_entry.append(line_stripped)
    
    if current_entry:
        shortage = parse_entry_with_debug(current_entry)
        if shortage:
            shortages.append(shortage)
    
    print(f"   Parsed: {len(shortages)} entries")
    return shortages


def parse_entry_with_debug(lines: List[str]) -> Dict[str, str]:
    """Parse entry with debug output."""
    if len(lines) < 3:
        return None
    
    print(f"   Entry ({len(lines)} lines): {lines[:5]}")
    
    generic_name = lines[0].strip()
    brand_name = lines[1].strip() if len(lines) > 1 else ''
    form = lines[2].strip() if len(lines) > 2 else ''
    strength = lines[3].strip() if len(lines) > 3 else ''
    din = ''
    status_text = ''
    date = ''
    
    # Find DIN (8 digits)
    for line in lines:
        match = re.search(r'\b(\d{8})\b', line.strip())
        if match:
            din = match.group(1)
            break
    
    # Find status
    for line in lines:
        stripped = line.strip()
        if stripped in STATUS_MAP:
            status_text = stripped
            break
        for kw in STATUS_MAP.keys():
            if kw in stripped:
                status_text = kw
                break
    
    # Find date
    for line in lines:
        match = re.search(r'(\d{4}-\d{2}-\d{2})', line.strip())
        if match:
            date = match.group(1)
            break
    
    if not din:
        return None
    
    internal_status = STATUS_MAP.get(status_text, status_text.lower().replace(' ', '_'))
    
    return {
        'report_id': din,
        'brand_name': brand_name or generic_name,
        'generic_name': generic_name,
        'form': form,
        'strength': strength,
        'din': din,
        'status': internal_status,
        'is_active': internal_status in ['confirmed_shortage', 'verification'],
        'date': date,
    }


# ═══════════════════════════════════════════════════════════════
# FETCH ALL
# ═══════════════════════════════════════════════════════════════

def fetch_all_shortages() -> List[Dict[str, str]]:
    """Fetch all shortages from RAMQ."""
    print("   [RAMQ Scraper] Loading pages...")
    
    all_shortages = []
    seen_dins = set()
    
    for page_num in range(1, 4):
        url = RAMQ_URL if page_num == 1 else f"{RAMQ_URL}?page={page_num}"
        
        try:
            print(f"   Page {page_num}...")
            body_text = fetch_page_with_playwright(url)
            
            if not body_text or len(body_text) < 200:
                print(f"   Page {page_num}: empty, stopping")
                break
            
            shortages = parse_ramq_table(body_text)
            new_count = 0
            
            for s in shortages:
                din = s.get('din', '')
                if din and din not in seen_dins:
                    seen_dins.add(din)
                    all_shortages.append(s)
                    new_count += 1
            
            print(f"   Page {page_num}: {new_count} new (total: {len(all_shortages)})")
            
            if new_count == 0 and page_num > 1:
                break
                
        except Exception as e:
            print(f"   Page {page_num} error: {e}")
            break
    
    print(f"   ✅ TOTAL: {len(all_shortages)} unique entries")
    return all_shortages


# ═══════════════════════════════════════════════════════════════
# TRANSFORM + SYNC
# ═══════════════════════════════════════════════════════════════

def generate_search_keywords(shortage: Dict[str, str]) -> List[str]:
    keywords = []
    fields = [
        shortage.get('brand_name', ''),
        shortage.get('generic_name', ''),
        shortage.get('din', ''),
        shortage.get('strength', ''),
        shortage.get('form', ''),
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
    return {
        'report_id': raw.get('din', ''),
        'brand_name': raw.get('brand_name', ''),
        'generic_name': raw.get('generic_name', ''),
        'form': raw.get('form', ''),
        'strength': raw.get('strength', ''),
        'din': raw.get('din', ''),
        'status': raw.get('status', ''),
        'is_active': raw.get('is_active', False),
        'source': 'RAMQ',
        'search_keywords': generate_search_keywords(raw),
        'updated_at': datetime.now(timezone.utc),
    }


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
    
    print(f"Synced {len(synced_ids)} shortages to Firestore")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("MyVita — Medication Shortages Sync (RAMQ + DEBUG)")
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