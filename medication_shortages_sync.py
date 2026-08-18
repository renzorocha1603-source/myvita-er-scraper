"""
MyVita — Medication Shortages Sync (HTML Scraper)
Scrapes Health Product Shortages Canada website and syncs to Firestore.
Runs every 12 hours via GitHub Actions.
"""

import json
import os
import re
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from html.parser import HTMLParser

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

# Therapeutic categories mapping
CATEGORY_KEYWORDS = {
    "antibiotic": ["antibiotic", "antibiotique", "amoxicillin", "amoxicilline", "azithromycin", "cephalexin", "ciprofloxacin"],
    "antiviral": ["antiviral", "antiviral", "acyclovir", "oseltamivir"],
    "antidepressant": ["antidepressant", "antidepresseur", "depression", "sertraline", "fluoxetine", "escitalopram"],
    "antihypertensive": ["antihypertensive", "antihypertenseur", "hypertension", "amlodipine", "lisinopril", "ramipril"],
    "antidiabetic": ["antidiabetic", "antidiabetique", "diabetes", "metformin", "insulin"],
    "stimulant": ["stimulant", "adhd", "tdah", "methylphenidate", "amphetamine", "concerta", "vyvanse"],
    "ppi": ["ppi", "ipp", "omeprazole", "esomeprazole", "pantoprazole", "lansoprazole"],
    "statin": ["statin", "statine", "cholesterol", "atorvastatin", "rosuvastatin", "simvastatin"],
    "analgesic": ["analgesic", "analgesique", "pain", "douleur", "acetaminophen", "ibuprofen", "naproxen", "morphine"],
    "anticoagulant": ["anticoagulant", "warfarin", "heparin", "apixaban", "rivaroxaban"],
    "corticosteroid": ["corticosteroid", "corticosteroide", "prednisone", "hydrocortisone", "dexamethasone"],
    "bronchodilator": ["bronchodilator", "bronchodilatateur", "asthma", "asthme", "salbutamol", "ventolin", "fluticasone"],
    "anticonvulsant": ["anticonvulsant", "anticonvulsivant", "epilepsy", "epilepsie", "gabapentin", "lamotrigine"],
    "thyroid": ["thyroid", "thyroide", "levothyroxine", "synthroid"],
    "antihistamine": ["antihistamine", "antihistaminique", "allergy", "allergie", "cetirizine", "loratadine"],
}


# ═══════════════════════════════════════════════════════════════
# FIREBASE INITIALIZATION
# ═══════════════════════════════════════════════════════════════

def init_firebase():
    if not FIREBASE_CREDENTIALS_JSON:
        raise ValueError("FIREBASE_CREDENTIALS_JSON environment variable not set")
    cred_dict = json.loads(FIREBASE_CREDENTIALS_JSON)
    cred = credentials.Certificate(cred_dict)
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)
    return firestore.client()


# ═══════════════════════════════════════════════════════════════
# HTML SCRAPER
# ═══════════════════════════════════════════════════════════════

class ShortageHTMLParser(HTMLParser):
    """Parses the search results table from HPSC website."""
    
    def __init__(self):
        super().__init__()
        self.in_table_row = False
        self.current_row = None
        self.current_cell = None
        self.rows = []
        self.cell_index = 0
        self.cell_text = ""
        self.brand_name = ""
        self.company_name = ""
        self.report_id = ""
        self.strength = ""
        self.updated_date = ""
        self.status = ""
        
    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        
        if tag == 'tr' and 'data-index' in attrs_dict:
            self.in_table_row = True
            self.current_row = {}
            self.cell_index = 0
            self.cell_text = ""
            self.brand_name = ""
            self.company_name = ""
            self.report_id = ""
            self.strength = ""
            self.updated_date = ""
            self.status = ""
            
        if tag == 'td' and self.in_table_row:
            self.cell_text = ""
            
        if tag == 'a' and self.in_table_row:
            href = attrs_dict.get('href', '')
            title = attrs_dict.get('title', '')
            
            if '/shortage/' in href or '/discontinuance/' in href:
                match = re.search(r'/(\d+)$', href)
                if match:
                    self.report_id = match.group(1)
            
            if '/drug/' in href and not self.brand_name:
                self.brand_name = title
                
            if '/company/' in href and not self.company_name:
                self.company_name = title
                
        if tag == 'span' and self.in_table_row:
            title = attrs_dict.get('title', '')
            if title and 'EDT' in title or 'EST' in title:
                self.updated_date = title[:10]
                
    def handle_data(self, data):
        if self.in_table_row:
            self.cell_text += data.strip()
            
    def handle_endtag(self, tag):
        if tag == 'td' and self.in_table_row:
            self.cell_index += 1
            
            # Column mapping:
            # 0: Status, 1: Brand name, 2: Company, 3: Strength, 4: Updated, 5: Report ID
            if self.cell_index == 1:
                self.status = self.cell_text.strip()
            elif self.cell_index == 4:
                if not self.strength:
                    self.strength = self.cell_text.strip()
                    
        if tag == 'tr' and self.in_table_row:
            self.in_table_row = False
            if self.brand_name or self.report_id:
                self.rows.append({
                    'status': self.status,
                    'brand_name': self.brand_name,
                    'company_name': self.company_name,
                    'strength': self.strength,
                    'updated_date': self.updated_date,
                    'report_id': self.report_id,
                })


def fetch_search_results(status: str, page: int = 1, limit: int = 100) -> List[Dict[str, Any]]:
    """Fetch search results for a specific status from the HPSC website."""
    all_results = []
    
    url = f"{BASE_URL}{SEARCH_PATH}"
    params = {
        "status": status,
        "limit": limit,
        "page": page,
        "report_published": "1",
    }
    full_url = f"{url}?{urllib.parse.urlencode(params)}"
    
    try:
        req = urllib.request.Request(full_url)
        req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        req.add_header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
        req.add_header("Accept-Language", "en-US,en;q=0.9,fr;q=0.8")
        
        with urllib.request.urlopen(req, timeout=30) as response:
            html_content = response.read().decode("utf-8")
            
        parser = ShortageHTMLParser()
        parser.feed(html_content)
        
        return parser.rows
        
    except urllib.error.HTTPError as e:
        print(f"HTTP Error {e.code} for {status} page {page}: {e.reason}")
        return []
    except Exception as e:
        print(f"Error fetching {status} page {page}: {e}")
        return []


def fetch_all_shortages() -> List[Dict[str, Any]]:
    """Fetch all active shortages across all statuses and pages."""
    all_shortages = []
    
    for status in ACTIVE_STATUSES:
        print(f"Fetching status: {status}...")
        page = 1
        total_fetched = 0
        
        while page <= 5:  # Limit to 5 pages per status (500 items) to avoid rate limits
            results = fetch_search_results(status, page=page, limit=100)
            if not results:
                break
                
            all_shortages.extend(results)
            total_fetched += len(results)
            print(f"  Page {page}: {len(results)} results (total: {total_fetched})")
            
            if len(results) < 100:
                break
                
            page += 1
    
    print(f"Total shortages fetched: {len(all_shortages)}")
    return all_shortages


# ═══════════════════════════════════════════════════════════════
# DATA TRANSFORMATION
# ═══════════════════════════════════════════════════════════════

def extract_category(brand_name: str) -> str:
    """Determine therapeutic category from brand name."""
    text = brand_name.lower()
    
    for category, keywords in CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text:
                return category
    
    return "other"


def generate_search_keywords(shortage: Dict[str, Any]) -> List[str]:
    """Generate search keywords from all available fields."""
    keywords = []
    
    fields = [
        shortage.get("brand_name", ""),
        shortage.get("company_name", ""),
        shortage.get("report_id", ""),
        shortage.get("strength", ""),
    ]
    
    for field in fields:
        if field:
            keywords.append(field.lower())
            # Normalized version without accents
            normalized = field.lower()
            for char_map in [("é", "e"), ("è", "e"), ("ê", "e"), ("à", "a"), ("ç", "c"), ("ô", "o"), ("î", "i"), ("û", "u")]:
                normalized = normalized.replace(char_map[0], char_map[1])
            keywords.append(normalized)
    
    category = extract_category(shortage.get("brand_name", ""))
    if category in CATEGORY_KEYWORDS:
        keywords.extend(CATEGORY_KEYWORDS[category])
    
    return list(set([k for k in keywords if k]))


def transform_shortage(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Transform scraped HTML data into Firestore document."""
    brand_name = raw.get("brand_name", "")
    category = extract_category(brand_name)
    
    # Determine status
    status_map = {
        "Resolved": "resolved",
        "Actual shortage": "active_confirmed",
        "Anticipated shortage": "anticipated_shortage",
        "Avoided shortage": "avoided_shortage",
        "Discontinued": "discontinued",
        "To be discontinued": "to_be_discontinued",
    }
    status = status_map.get(raw.get("status", ""), raw.get("status", "unknown"))
    
    shortage = {
        "report_id": raw.get("report_id", ""),
        "brand_name": brand_name,
        "company_name": raw.get("company_name", ""),
        "strength": raw.get("strength", ""),
        "status": status,
        "is_active": status in ["active_confirmed", "anticipated_shortage", "avoided_shortage"],
        "tier_3": False,  # We'll update this from the Tier 3 page separately
        "updated_at": datetime.now(timezone.utc),
    }
    
    shortage["search_keywords"] = generate_search_keywords(shortage)
    
    return shortage


# ═══════════════════════════════════════════════════════════════
# FIRESTORE SYNC
# ═══════════════════════════════════════════════════════════════

def sync_to_firestore(db: firestore.Client, shortages: List[Dict[str, Any]]):
    """Sync shortages to Firestore."""
    collection_ref = db.collection(FIRESTORE_COLLECTION)
    
    # Only sync active shortages
    active_shortages = [s for s in shortages if s.get("is_active", False)]
    
    synced_ids = set()
    batch = db.batch()
    batch_count = 0
    
    for shortage in active_shortages:
        report_id = shortage.get("report_id", "")
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
    
    # Remove stale entries
    existing_docs = collection_ref.where("is_active", "==", True).stream()
    stale_batch = db.batch()
    stale_count = 0
    
    for doc in existing_docs:
        if doc.id not in synced_ids:
            stale_batch.update(doc.reference, {"is_active": False, "status": "resolved"})
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
    print("MyVita — Medication Shortages Sync (HTML Scraper)")
    print(f"Started at: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)
    
    db = init_firebase()
    print("Firebase initialized")
    
    raw_shortages = fetch_all_shortages()
    print(f"Fetched {len(raw_shortages)} raw shortage records")
    
    transformed = []
    for raw in raw_shortages:
        try:
            transformed.append(transform_shortage(raw))
        except Exception as e:
            print(f"Error transforming: {e}")
    
    print(f"Transformed {len(transformed)} shortages")
    
    sync_to_firestore(db, transformed)
    
    print("=" * 60)
    print(f"Completed at: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
