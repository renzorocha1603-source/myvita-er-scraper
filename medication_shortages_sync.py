"""
MyVita — Medication Shortages Sync (RAMQ Quebec)
Fetches Quebec-specific medication shortages from RAMQ.
No Cloudflare, government source.
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
# PLAYWRIGHT FETCH
# ═══════════════════════════════════════════════════════════════

def fetch_page_with_playwright(url: str) -> str:
    """Fetch RAMQ page using Playwright."""
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
# PARSER FOR RAMQ TABLE
# ═══════════════════════════════════════════════════════════════

STATUS_MAP = {
    'Ce produit ou un produit de l\'encadré est disponible': 'available',
    'Ce produit ou un produit de l\'encadré sera disponible sous peu': 'available_soon',
    'En cours de vérification': 'verification',
    'Produit retiré après commercialisation': 'withdrawn',
    'Rupture confirmée': 'confirmed_shortage',
}


def parse_ramq_table(body_text: str) -> List[Dict[str, str]]:
    """Parse the RAMQ medication shortage table."""
    shortages = []
    lines = body_text.split('\n')
    
    # Find table start
    start_idx = -1
    for i, line in enumerate(lines):
        if 'Dénomination commune' in line and 'Marque de commerce' in line:
            start_idx = i
            break
    
    if start_idx == -1:
        return []
    
    current_entry = []
    
    for line in lines[start_idx+1:]:
        line_stripped = line.strip()
        
        # Stop at pagination or page footer
        if 'Page ' in line_stripped and 'de' in line_stripped:
            break
        if 'Évaluer la page' in line_stripped:
            break
        
        # Skip header
        if 'Dénomination commune' in line_stripped or 'Marque de commerce' in line_stripped:
            continue
        
        if not line_stripped:
            if current_entry:
                shortage = parse_ramq_entry(current_entry)
                if shortage:
                    shortages.append(shortage)
                current_entry = []
            continue
        
        # Skip non-data lines
        if line_stripped.startswith(('Page', 'Évaluer', 'RAMQ', 'À propos', 'Nous', 'Suivre', 'Accessibilité', 'Politique', '©')):
            continue
        
        current_entry.append(line_stripped)
    
    # Don't forget last entry
    if current_entry:
        shortage = parse_ramq_entry(current_entry)
        if shortage:
            shortages.append(shortage)
    
    return shortages


def parse_ramq_entry(lines: List[str]) -> Dict[str, str]:
    """
    Parse a RAMQ entry from collected lines.
    Structure:
    [common_name]
    [brand_name]
    [form]
    [strength]
    [DIN]
    [status]
    [date]
    """
    if len(lines) < 4:
        return None
    
    common_name = lines[0].strip() if len(lines) > 0 else ''
    brand_name = lines[1].strip() if len(lines) > 1 else ''
    form = lines[2].strip() if len(lines) > 2 else ''
    strength = lines[3].strip() if len(lines) > 3 else ''
    din = ''
    status_text = ''
    date = ''
    
    # Find DIN (8-digit number)
    for line in lines:
        match = re.search(r'\b(\d{8})\b', line.strip())
        if match:
            din = match.group(1)
            break
    
    # Find status
    for line in lines:
        stripped = line.strip()
        if stripped in STATUS_MAP or any(kw in stripped for kw in STATUS_MAP.keys()):
            status_text = stripped
            break
    
    # Find date (YYYY-MM-DD)
    for line in lines:
        match = re.search(r'(\d{4}-\d{2}-\d{2})', line.strip())
        if match:
            date = match.group(1)
            break
    
    if not common_name or not din:
        return None
    
    internal_status = STATUS_MAP.get(status_text, status_text)
    is_active = internal_status in ['confirmed_shortage', 'verification']
    
    return {
        'report_id': din,
        'brand_name': brand_name or common_name,
        'generic_name': common_name,
        'form': form,
        'strength': strength,
        'din': din,
        'status': internal_status,
        'is_active': is_active,
        'date': date,
    }


# ═══════════════════════════════════════════════════════════════
# FETCH ALL DATA
# ═══════════════════════════════════════════════════════════════

def fetch_all_shortages() -> List[Dict[str, str]]:
    """Fetch all shortages from RAMQ."""
    print("   [RAMQ Scraper] Loading pages...")
    
    all_shortages = []
    seen_dins = set()
    
    # Try pages 1-3
    for page_num in range(1, 4):
        url = RAMQ_URL
        if page_num > 1:
            url = f"{RAMQ_URL}?page={page_num}"
        
        try:
            print(f"   Page {page_num}: {url}")
            body_text = fetch_page_with_playwright(url)
            
            if not body_text or len(body_text) < 200:
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
            
            if new_count == 0:
                break
                
        except Exception as e:
            print(f"   Page {page_num} error: {e}")
            break
    
    print(f"   ✅ TOTAL: {len(all_shortages)} unique entries")
    return all_shortages


# ═══════════════════════════════════════════════════════════════
# DATA TRANSFORMATION
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
            stale_batch.update(doc.reference, {'is_active': False})
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
    print("MyVita — Medication Shortages Sync (RAMQ Quebec)")
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