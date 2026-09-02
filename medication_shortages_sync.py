"""
MyVita — Medication Shortages Sync
Primary: Drug Shortages Canada API (official)
Fallback: RAMQ API + page scraping
"""

import json
import os
import re
import requests
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import firebase_admin
from firebase_admin import credentials, firestore

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

RAMQ_URL = "https://www.ramq.gouv.qc.ca/fr/professionnels/pharmacien-pharmacienne/medicaments/ruptures-stock-signalees"
FIRESTORE_COLLECTION = "medication_shortages"
FIREBASE_CREDENTIALS_JSON = os.environ.get("MYVITA_FIREBASE_SERVICE_ACCOUNT", "")

# Drug Shortages Canada API
DRUG_SHORTAGES_API_URL = "https://www.drugshortagescanada.ca/api/v1"
DRUG_SHORTAGES_EMAIL = os.environ.get("DRUG_SHORTAGES_EMAIL", "RENZOROCHA1603@GMAIL.COM")
DRUG_SHORTAGES_PASSWORD = os.environ.get("DRUG_SHORTAGES_PASSWORD", "Angelorea1603$")

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
# DRUG SHORTAGES CANADA API CLIENT
# ═══════════════════════════════════════════════════════════════

class DrugShortagesCanadaClient:
    """Client for the official Drug Shortages Canada API."""
    
    def __init__(self):
        self.token = None
        self.token_expiry = None
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; MyVitaApp/1.0; +https://myvita.app)",
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
        })
    
    def login(self) -> bool:
        """Authenticate and get auth token."""
        print("   [Drug Shortages Canada] Logging in...")
        
        try:
            response = self.session.post(
                f"{DRUG_SHORTAGES_API_URL}/login",
                data={
                    "email": DRUG_SHORTAGES_EMAIL,
                    "password": DRUG_SHORTAGES_PASSWORD
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                timeout=30
            )
            
            if response.status_code == 200:
                self.token = response.headers.get("auth-token")
                self.token_expiry = response.headers.get("expiry-date")
                print(f"   ✅ Drug Shortages Canada login successful!")
                print(f"   Token expires: {self.token_expiry}")
                return True
            else:
                print(f"   ❌ Login failed: {response.status_code}")
                print(f"   Response: {response.text[:300]}")
                return False
        
        except Exception as e:
            print(f"   ❌ Login error: {e}")
            return False
    
    def search(self, term: Optional[str] = None, status: Optional[str] = None, 
               limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """Search for drug shortages."""
        if not self.token:
            if not self.login():
                return []
        
        params = {
            "limit": limit,
            "offset": offset,
            "orderby": "updated_date",
            "order": "desc"
        }
        
        if term:
            params["term"] = term
        if status:
            params["filter_status"] = status
        
        try:
            response = self.session.get(
                f"{DRUG_SHORTAGES_API_URL}/search",
                headers={"auth-token": self.token},
                params=params,
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                return data.get("data", [])
            elif response.status_code == 401:
                # Token expired, re-login and retry
                print("   Token expired, re-logging in...")
                if self.login():
                    return self.search(term, status, limit, offset)
                return []
            else:
                print(f"   ❌ Search failed: {response.status_code}")
                print(f"   Response: {response.text[:300]}")
                return []
        
        except Exception as e:
            print(f"   ❌ Search error: {e}")
            return []
    
    def get_all_shortages(self, status: str = "active_confirmed") -> List[Dict[str, Any]]:
        """Get all shortages with pagination."""
        print(f"   Fetching all '{status}' shortages...")
        all_shortages = []
        offset = 0
        limit = 100
        
        while True:
            shortages = self.search(status=status, limit=limit, offset=offset)
            
            if not shortages:
                break
            
            all_shortages.extend(shortages)
            
            if len(shortages) < limit:
                break
            
            offset += limit
            print(f"   Fetched {len(all_shortages)} so far...")
        
        print(f"   ✅ Total '{status}': {len(all_shortages)}")
        return all_shortages
    
    def get_all_active_and_anticipated(self) -> List[Dict[str, Any]]:
        """Get both active and anticipated shortages."""
        active = self.get_all_shortages("active_confirmed")
        anticipated = self.get_all_shortages("anticipated_shortage")
        
        # Combine and deduplicate by ID
        combined = {}
        for shortage in active + anticipated:
            sid = shortage.get("id")
            if sid and sid not in combined:
                combined[sid] = shortage
        
        return list(combined.values())


# ═══════════════════════════════════════════════════════════════
# RAMQ FALLBACK (original scraper)
# ═══════════════════════════════════════════════════════════════

def try_api_endpoints() -> str:
    """Try different API endpoints for RAMQ data."""
    print("   [RAMQ API Attempt] Trying direct API endpoints...")
    
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
                    print(f"   ✅ JSON response! Length: {len(content)}")
                    return content
        except Exception as e:
            print(f"   ❌ Failed: {e}")
    
    print("   ❌ No RAMQ API endpoints worked")
    return ""


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
    
    lines = content.split('\n')
    
    if '<table' in content or '<tr' in content:
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
                        'report_id': f"ramq_{din}",
                        'brand_name': brand_name or generic_name,
                        'generic_name': generic_name,
                        'form': form,
                        'strength': strength,
                        'din': din,
                        'status': STATUS_MAP.get(status_text, status_text),
                        'is_active': STATUS_MAP.get(status_text) in ['confirmed_shortage', 'verification'],
                        'date': date,
                        'source': 'RAMQ',
                    })
    else:
        print(f"   Content first 2000 chars:")
        print(content[:2000])
    
    return shortages


def fetch_ramq_shortages() -> List[Dict[str, str]]:
    """Fetch RAMQ shortages (fallback)."""
    all_shortages = []
    seen_dins = set()
    
    api_content = try_api_endpoints()
    
    if api_content:
        shortages = parse_ramq_content(api_content)
        for s in shortages:
            din = s.get('din', '')
            if din and din not in seen_dins:
                seen_dins.add(din)
                all_shortages.append(s)
        print(f"   RAMQ API: {len(shortages)} entries found")
    
    if not all_shortages:
        print("   [RAMQ Page Scraping] Trying with requests...")
        content = fetch_page_with_requests(RAMQ_URL)
        
        if content and len(content) > 500:
            shortages = parse_ramq_content(content)
            for s in shortages:
                din = s.get('din', '')
                if din and din not in seen_dins:
                    seen_dins.add(din)
                    all_shortages.append(s)
            print(f"   RAMQ Page: {len(shortages)} entries found")
    
    print(f"   ✅ RAMQ TOTAL: {len(all_shortages)} unique entries")
    return all_shortages


# ═══════════════════════════════════════════════════════════════
# TRANSFORM + SYNC
# ═══════════════════════════════════════════════════════════════

def generate_search_keywords(shortage: Dict[str, Any]) -> List[str]:
    """Generate search keywords for a shortage."""
    keywords = []
    fields = [
        shortage.get('brand_name', ''),
        shortage.get('brandName', ''),
        shortage.get('generic_name', ''),
        shortage.get('din', ''),
        shortage.get('strength', ''),
        shortage.get('form', ''),
    ]
    
    for field in fields:
        if field:
            keywords.append(str(field).lower())
            normalized = str(field).lower()
            for old, new in [('é', 'e'), ('è', 'e'), ('ê', 'e'), ('à', 'a'), ('ç', 'c')]:
                normalized = normalized.replace(old, new)
            if normalized != str(field).lower():
                keywords.append(normalized)
    
    return list(set(k for k in keywords if k))


def transform_drug_shortages_canada(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Transform Drug Shortages Canada data for Firestore."""
    return {
        'report_id': f"dsc_{raw.get('id', '')}",
        'brand_name': raw.get('en_drug_brand_name') or raw.get('fr_drug_brand_name') or '',
        'brandNameFr': raw.get('fr_drug_brand_name') or '',
        'generic_name': raw.get('en_generic_name') or raw.get('fr_generic_name') or '',
        'companyName': raw.get('en_company_name') or raw.get('fr_company_name') or '',
        'companyNameFr': raw.get('fr_company_name') or '',
        'strength': raw.get('en_strength') or raw.get('fr_strength') or '',
        'form': raw.get('en_dosage_form') or raw.get('fr_dosage_form') or '',
        'din': raw.get('din') or '',
        'atcCode': raw.get('atc_code') or '',
        'status': raw.get('status') or '',
        'type': raw.get('type') or '',
        'reason': raw.get('en_reason') or raw.get('fr_reason') or '',
        'reasonFr': raw.get('fr_reason') or '',
        'anticipated_start_date': raw.get('anticipated_start_date') or '',
        'anticipated_end_date': raw.get('anticipated_end_date') or '',
        'actual_start_date': raw.get('actual_start_date') or '',
        'actual_end_date': raw.get('actual_end_date') or '',
        'updated_date': raw.get('updated_date') or '',
        'created_date': raw.get('created_date') or '',
        'is_active': raw.get('status') in ['active_confirmed', 'anticipated_shortage'],
        'source': 'DrugShortagesCanada',
        'search_keywords': generate_search_keywords({
            'brand_name': raw.get('en_drug_brand_name') or raw.get('fr_drug_brand_name') or '',
            'din': raw.get('din') or '',
            'strength': raw.get('en_strength') or '',
            'form': raw.get('en_dosage_form') or '',
        }),
        'updated_at': datetime.now(timezone.utc),
    }


def transform_ramq_shortage(raw: Dict[str, str]) -> Dict[str, Any]:
    """Transform RAMQ data for Firestore."""
    return {
        'report_id': raw.get('report_id', ''),
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
    """Sync shortages to Firestore in batches."""
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
    
    print(f"   Synced {len(synced_ids)} shortages to Firestore")
    return synced_ids


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("MyVita — Medication Shortages Sync")
    print(f"Started at: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)
    
    db = init_firebase()
    print("✅ Firebase initialized\n")
    
    all_shortages = []
    all_synced_ids = set()
    
    # ─── PRIMARY: Drug Shortages Canada API ───
    print("─" * 60)
    print("PRIMARY: Drug Shortages Canada API")
    print("─" * 60)
    
    try:
        dsc_client = DrugShortagesCanadaClient()
        
        if dsc_client.login():
            dsc_shortages = dsc_client.get_all_active_and_anticipated()
            
            if dsc_shortages:
                transformed = [transform_drug_shortages_canada(s) for s in dsc_shortages]
                all_shortages.extend(transformed)
                
                print(f"\n   Sample DSC data:")
                for shortage in transformed[:3]:
                    print(f"     - {shortage['brand_name']} ({shortage['companyName']})")
                    print(f"       Status: {shortage['status']}")
                    print(f"       DIN: {shortage['din']}")
                
                # Sync DSC data
                synced_ids = sync_to_firestore(db, transformed)
                all_synced_ids.update(synced_ids)
            else:
                print("   ⚠️ No data from Drug Shortages Canada")
        else:
            print("   ⚠️ Could not login to Drug Shortages Canada")
    
    except Exception as e:
        print(f"   ❌ Drug Shortages Canada error: {e}")
    
    # ─── FALLBACK: RAMQ ───
    print(f"\n{'─' * 60}")
    print("FALLBACK: RAMQ")
    print("─" * 60)
    
    try:
        ramq_shortages = fetch_ramq_shortages()
        
        if ramq_shortages:
            transformed = [transform_ramq_shortage(s) for s in ramq_shortages]
            all_shortages.extend(transformed)
            
            print(f"\n   Sample RAMQ data:")
            for shortage in transformed[:3]:
                print(f"     - {shortage['brand_name']} ({shortage['generic_name']})")
                print(f"       Status: {shortage['status']}")
                print(f"       DIN: {shortage['din']}")
            
            # Sync RAMQ data
            synced_ids = sync_to_firestore(db, transformed)
            all_synced_ids.update(synced_ids)
        else:
            print("   ⚠️ No data from RAMQ")
    
    except Exception as e:
        print(f"   ❌ RAMQ error: {e}")
    
    # ─── SUMMARY ───
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    print(f"Total shortages fetched: {len(all_shortages)}")
    print(f"Total unique IDs synced: {len(all_synced_ids)}")
    
    if all_shortages:
        print(f"\nBy source:")
        dsc_count = sum(1 for s in all_shortages if s.get('source') == 'DrugShortagesCanada')
        ramq_count = sum(1 for s in all_shortages if s.get('source') == 'RAMQ')
        print(f"  - Drug Shortages Canada: {dsc_count}")
        print(f"  - RAMQ: {ramq_count}")
        
        print(f"\nBy status:")
        active_count = sum(1 for s in all_shortages if s.get('is_active', False))
        inactive_count = sum(1 for s in all_shortages if not s.get('is_active', False))
        print(f"  - Active: {active_count}")
        print(f"  - Inactive/Other: {inactive_count}")
    else:
        print("\n⚠️ No data fetched — old Firestore data remains unchanged")
    
    print(f"\nCompleted at: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
