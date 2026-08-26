"""
MyVita — Medication Shortages Sync (API attempt)
Tries RAMQ API directly + falls back to page scraping.
"""

import json
import os
import re
import requests
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
# API ATTEMPTS
# ═══════════════════════════════════════════════════════════════

def try_api_endpoints() -> str:
    """Try different API endpoints for RAMQ data."""
    print("   [API Attempt] Trying direct API endpoints...")
    
    endpoints = [
        "https://www.ramq.gouv.qc.ca/api/ruptures-stock",
        "https://www.ramq.gouv.qc.ca/api/medicaments/ruptures",
        "https://www.ramq.gouv.qc.ca/fr/professionnels/pharmacien-pharmacienne/medicaments/ruptures-stock-signalees?format=json",
        "https://www.ramq.gouv.qc.ca/fr/professionnels/pharmacien-pharmacienne/medicaments/ruptures-stock-signalees?type=json",
        "https://www.ramq.gouv.qc.ca/Services/Medicaments/RupturesStock",
    ]
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/html, */*',
        'Accept-Language': 'fr-CA,fr;q=0.9,en;q=0.8',
        'Referer': 'https://www.ramq.gouv.qc.ca/',
        'X-Requested-With': 'XMLHttpRequest',
        'Connection': 'keep-alive',
    }
    
    for url in endpoints:
        try:
            print(f"   Trying API: {url}")
            response = requests.get(url, headers=headers, timeout=15)
            print(f"   Status: {response.status_code}")
            
            if response.status_code == 200:
                content = response.text
                if 'Dénomination' in content or 'rupture' in content.lower() or 'DIN' in content:
                    print(f"   ✅ API works! Length: {len(content)}")
                    return content
                elif content.startswith('{') or content.startswith('['):
                    # JSON response
                    print(f"   ✅ JSON response! Length: {len(content)}")
                    return content
        except Exception as e:
            print(f"   ❌ Failed: {e}")
    
    print("   ❌ No API endpoints worked")
    return ""


# ═══════════════════════════════════════════════════════════════
# PAGE SCRAPING (fallback)
# ═══════════════════════════════════════════════════════════════

def fetch_page_with_requests(url: str) -> str:
    """Fetch page using requests (non-Playwright)."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'fr-CA,fr;q=0.9,en;q=0.8',
        'Referer': 'https://www.google.com/',
        'Connection': 'keep-alive',
    }
    
    response = requests.get(url, headers=headers, timeout=20)
    return response.text


# ═══════════════════════════════════════════════════════════════
# PARSER
# ═══════════════════════════════════════════════════════════════

STATUS_MAP = {
    "Ce produit ou un produit de l'encadré est disponible": 'available',
    "Ce produit ou un produit de l'encadré sera disponible sous peu": 'available_soon',
    'En cours de vérification': 'verification',
    'Produit retiré après commercialisation': 'withdrawn',
    'Rupture confirmée': 'confirmed_shortage',
}


def parse_ramq_content(content: str) -> List[Dict[str, str]]:
    """Parse RAMQ content (HTML or text)."""
    shortages = []
    
    # Try to find table data
    lines = content.split('\n')
    
    # Check if it's HTML and extract text
    if '<table' in content or '<tr' in content:
        # Extract table rows from HTML
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', content, re.DOTALL)
        
        for row in rows:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            if len(cells) >= 5:
                cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
                
                generic_name = cells[0] if len(cells) > 0 else ''
                brand_name = cells[1] if len(cells) > 1 else ''
                form = cells[2] if len(cells) > 2 else ''
                strength = cells[3] if len(cells) > 3 else ''
                din = ''
                status_text = ''
                date = ''
                
                for cell in cells:
                    match = re.search(r'\b(\d{8})\b', cell)
                    if match:
                        din = match.group(1)
                    for kw in STATUS_MAP.keys():
                        if kw in cell:
                            status_text = kw
                            break
                    match = re.search(r'(\d{4}-\d{2}-\d{2})', cell)
                    if match:
                        date = match.group(1)
                
                if din and generic_name:
                    shortages.append({
                        'report_id': din,
                        'brand_name': brand_name or generic_name,
                        'generic_name': generic_name,
                        'form': form,
                        'strength': strength,
                        'din': din,
                        'status': STATUS_MAP.get(status_text, status_text),
                        'is_active': STATUS_MAP.get(status_text) in ['confirmed_shortage', 'verification'],
                        'date': date,
                    })
    else:
        # Plain text parsing
        print(f"   Content first 2000 chars:")
        print(content[:2000])
    
    return shortages


# ═══════════════════════════════════════════════════════════════
# FETCH ALL
# ═══════════════════════════════════════════════════════════════

def fetch_all_shortages() -> List[Dict[str, str]]:
    """Fetch all shortages."""
    all_shortages = []
    seen_dins = set()
    
    # Try API first
    api_content = try_api_endpoints()
    
    if api_content:
        shortages = parse_ramq_content(api_content)
        for s in shortages:
            din = s.get('din', '')
            if din and din not in seen_dins:
                seen_dins.add(din)
                all_shortages.append(s)
        print(f"   API: {len(shortages)} entries found")
    
    # Try page scraping
    if not all_shortages:
        print("   [Page Scraping] Trying with requests...")
        content = fetch_page_with_requests(RAMQ_URL)
        
        if content and len(content) > 500:
            shortages = parse_ramq_content(content)
            for s in shortages:
                din = s.get('din', '')
                if din and din not in seen_dins:
                    seen_dins.add(din)
                    all_shortages.append(s)
            print(f"   Page: {len(shortages)} entries found")
    
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
    print("MyVita — Medication Shortages Sync (API)")
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
        print("No data to sync — old data remains in Firestore")
    
    print("=" * 60)
    print(f"Completed at: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)


if __name__ == "__main__":
    main()