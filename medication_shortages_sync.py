"""
MyVita — Medication Shortages Sync
Fetches Health Product Shortages Canada data and syncs to Firestore.
Uses Playwright with anti-Cloudflare measures.
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
# ANTI-CLOUDFLARE PLAYWRIGHT FETCH
# ═══════════════════════════════════════════════════════════════

def fetch_with_anti_cloudflare(url: str) -> str:
    """Fetch page with anti-bot detection measures."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-web-security',
                '--disable-features=IsolateOrigins,site-per-process',
            ]
        )
        
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080},
            locale='fr-CA',
            timezone_id='America/Montreal',
            has_touch=False,
            is_mobile=False,
        )
        
        # Hide webdriver
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        context.add_init_script(
            "delete navigator.__proto__.webdriver"
        )
        context.add_init_script(
            "window.chrome = {runtime: {}}"
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'languages', {get: () => ['fr-CA', 'fr', 'en-CA', 'en']})"
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]})"
        )
        
        page = context.new_page()
        
        # Random mouse movement to look human
        page.mouse.move(500, 500)
        page.wait_for_timeout(500)
        page.mouse.move(800, 300)
        
        page.goto(url, wait_until='networkidle', timeout=60000)
        
        # Wait for Cloudflare to verify
        page.wait_for_timeout(15000)
        
        # Check if Cloudflare challenge is present
        for attempt in range(3):
            body_text = page.locator('body').inner_text()
            if 'Pénurie' in body_text or 'Shortage' in body_text or 'OMEGA' in body_text:
                break
            # Still on Cloudflare page, wait more
            print(f"   Cloudflare detected, waiting... (attempt {attempt+1})")
            page.wait_for_timeout(15000)
        
        body_text = page.locator('body').inner_text()
        browser.close()
    
    return body_text


# ═══════════════════════════════════════════════════════════════
# PARSER (tab-separated table data)
# ═══════════════════════════════════════════════════════════════

def parse_shortages_from_text(body_text: str) -> List[Dict[str, str]]:
    """Parse shortage data from body text."""
    shortages = []
    lines = body_text.split('\n')
    
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        
        # Skip non-data lines
        if any(skip in line_stripped for skip in ['Nom de marque', 'Brand name', 'Liste des rapports', 'Rapports de', 'Légende']):
            continue
        
        # Check for tab-separated data
        if '\t' in line_stripped:
            parts = [p.strip() for p in line_stripped.split('\t') if p.strip()]
            if len(parts) >= 4:
                brand_name = parts[0]
                company_name = parts[1]
                status_text = parts[2]
                strength = parts[3]
                report_id = ''
                
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
    print("   [Anti-Cloudflare Scraper] Loading pages...")
    
    all_shortages = []
    seen_report_ids = set()
    
    urls_to_try = [
        f"{BASE_URL}{SEARCH_PATH}",
        f"{BASE_URL_EN}{SEARCH_PATH}",
    ]
    
    body_text = None
    
    for url in urls_to_try:
        try:
            print(f"   Trying: {url}")
            body_text = fetch_with_anti_cloudflare(url)
            
            if body_text and ('Pénurie' in body_text or 'Shortage' in body_text or 'OMEGA' in body_text):
                print(f"   ✅ Data loaded ({len(body_text)} chars)")
                break
            else:
                print(f"   ⚠️ Cloudflare still blocking, trying next URL")
        except Exception as e:
            print(f"   ❌ Failed: {e}")
    
    if not body_text or ('Pénurie' not in body_text and 'Shortage' not in body_text):
        print("   ❌ Could not bypass Cloudflare")
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
    print("MyVita — Medication Shortages Sync (Anti-Cloudflare)")
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