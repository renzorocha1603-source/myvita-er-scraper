"""
MyVita — Medication Shortages Sync
Fetches Health Canada Drug Shortages data and syncs to Firestore.
Runs every 12 hours via GitHub Actions.
"""

import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import firebase_admin
from firebase_admin import credentials, firestore

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# Health Canada Drug Shortages API endpoints
DRUG_SHORTAGES_API = "https://www.drugshortagescanada.ca/api/v1/"
SEARCH_ENDPOINT = f"{DRUG_SHORTAGES_API}search"
REPORT_ENDPOINT = f"{DRUG_SHORTAGES_API}reports"

# Firestore collection name
FIRESTORE_COLLECTION = "medication_shortages"

# Firebase credentials (from environment variable)
FIREBASE_CREDENTIALS_JSON = os.environ.get("FIREBASE_CREDENTIALS_JSON", "")

# Therapeutic categories mapping (for search_keywords)
CATEGORY_KEYWORDS = {
    "antibiotic": ["antibiotic", "antibiotique"],
    "antiviral": ["antiviral", "antiviral"],
    "antifungal": ["antifungal", "antifongique"],
    "analgesic": ["analgesic", "analgesique", "pain", "douleur"],
    "antidepressant": ["antidepressant", "antidepresseur", "depression"],
    "antihypertensive": ["antihypertensive", "antihypertenseur", "hypertension", "blood pressure"],
    "antidiabetic": ["antidiabetic", "antidiabetique", "diabetes", "diabete"],
    "antihistamine": ["antihistamine", "antihistaminique", "allergy", "allergie"],
    "corticosteroid": ["corticosteroid", "corticosteroide", "steroid", "steroide"],
    "anticoagulant": ["anticoagulant", "anticoagulant", "blood thinner"],
    "antipsychotic": ["antipsychotic", "antipsychotique"],
    "anxiolytic": ["anxiolytic", "anxiolytique", "anxiety", "anxiete"],
    "stimulant": ["stimulant", "stimulant", "adhd", "tdah"],
    "ppi": ["ppi", "ipp", "proton pump", "acid reflux", "reflux"],
    "statin": ["statin", "statine", "cholesterol"],
    "bronchodilator": ["bronchodilator", "bronchodilatateur", "asthma", "asthme"],
    "anticonvulsant": ["anticonvulsant", "anticonvulsivant", "epilepsy", "epilepsie"],
    "hormone": ["hormone", "hormone", "thyroid", "thyroide"],
    "vaccine": ["vaccine", "vaccin", "immunization", "immunisation"],
}

# Quebec-relevant statuses (we show all active shortages across Canada)
ACTIVE_STATUSES = ["active", "anticipated", "avoided"]


# ═══════════════════════════════════════════════════════════════
# FIREBASE INITIALIZATION
# ═══════════════════════════════════════════════════════════════

def init_firebase():
    """Initialize Firebase Admin SDK from environment credentials."""
    if not FIREBASE_CREDENTIALS_JSON:
        raise ValueError("FIREBASE_CREDENTIALS_JSON environment variable not set")

    cred_dict = json.loads(FIREBASE_CREDENTIALS_JSON)
    cred = credentials.Certificate(cred_dict)

    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)

    return firestore.client()


# ═══════════════════════════════════════════════════════════════
# API FETCHING
# ═══════════════════════════════════════════════════════════════

def fetch_all_shortages() -> List[Dict[str, Any]]:
    """Fetch all active shortages from Health Canada API."""
    all_shortages = []
    page = 0
    page_size = 100

    while True:
        params = {
            "status": "active",
            "page": page,
            "page_size": page_size,
        }
        url = f"{SEARCH_ENDPOINT}?{urllib.parse.urlencode(params)}"

        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", "MyVita/2.0 (Quebec Health AI Concierge)")
            req.add_header("Accept", "application/json")

            with urllib.request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))

            if not data or len(data) == 0:
                break

            all_shortages.extend(data)

            if len(data) < page_size:
                break

            page += 1

        except urllib.error.HTTPError as e:
            print(f"HTTP Error {e.code}: {e.reason}")
            break
        except urllib.error.URLError as e:
            print(f"URL Error: {e.reason}")
            break
        except Exception as e:
            print(f"Error fetching page {page}: {e}")
            break

    print(f"Fetched {len(all_shortages)} active shortages")
    return all_shortages


def fetch_shortage_details(report_id: str) -> Optional[Dict[str, Any]]:
    """Fetch detailed information for a specific shortage report."""
    url = f"{REPORT_ENDPOINT}/{report_id}"

    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "MyVita/2.0 (Quebec Health AI Concierge)")
        req.add_header("Accept", "application/json")

        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    except Exception as e:
        print(f"Error fetching details for {report_id}: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
# DATA TRANSFORMATION
# ═══════════════════════════════════════════════════════════════

def extract_category(ingredient: str, brand_name: str) -> str:
    """Determine therapeutic category based on ingredient/brand name."""
    text = f"{ingredient} {brand_name}".lower()

    for category, keywords in CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text:
                return category

    return "other"


def generate_search_keywords(shortage: Dict[str, Any]) -> List[str]:
    """Generate comprehensive search keywords for client-side search."""
    keywords = []

    fields = [
        shortage.get("din", ""),
        shortage.get("brand_name_fr", ""),
        shortage.get("brand_name_en", ""),
        shortage.get("active_ingredient", ""),
        shortage.get("manufacturer", ""),
        shortage.get("category", ""),
    ]

    for field in fields:
        if field:
            keywords.append(str(field).lower())
            # Add without accents for French
            keywords.append(
                str(field)
                .lower()
                .replace("é", "e")
                .replace("è", "e")
                .replace("ê", "e")
                .replace("à", "a")
                .replace("ç", "c")
                .replace("ô", "o")
                .replace("î", "i")
                .replace("û", "u")
            )

    # Add category-specific keywords
    category = shortage.get("category", "")
    if category in CATEGORY_KEYWORDS:
        keywords.extend(CATEGORY_KEYWORDS[category])

    # Remove duplicates and empty strings
    return list(set([k for k in keywords if k]))


def transform_shortage(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Transform raw API data into Firestore document format."""
    ingredient = raw.get("active_ingredient", "") or raw.get("ingredient", "") or ""
    brand_fr = raw.get("brand_name_fr", "") or raw.get("brand_name", "") or ""
    brand_en = raw.get("brand_name_en", "") or raw.get("brand_name", "") or ""

    category = extract_category(ingredient, brand_fr or brand_en)

    # Parse resolution date
    resolution_date = None
    if raw.get("estimated_end_date"):
        try:
            resolution_date = datetime.strptime(
                raw["estimated_end_date"], "%Y-%m-%d"
            ).replace(tzinfo=timezone.utc)
        except:
            pass

    shortage = {
        "din": raw.get("din", ""),
        "brand_name_fr": brand_fr,
        "brand_name_en": brand_en,
        "active_ingredient": ingredient,
        "manufacturer": raw.get("manufacturer", ""),
        "category": category,
        "status": raw.get("status", "active"),
        "tier_3": raw.get("tier_3", False),
        "reason": raw.get("reason", ""),
        "expected_resolution_date": resolution_date,
        "alternative_molecules": raw.get("alternatives", []),
        "updated_at": datetime.now(timezone.utc),
    }

    shortage["search_keywords"] = generate_search_keywords(shortage)

    return shortage


# ═══════════════════════════════════════════════════════════════
# FIRESTORE SYNC
# ═══════════════════════════════════════════════════════════════

def sync_to_firestore(db: firestore.Client, shortages: List[Dict[str, Any]]):
    """Sync shortages to Firestore, removing stale entries."""
    collection_ref = db.collection(FIRESTORE_COLLECTION)

    # Track all DINs to sync
    synced_dins = set()

    batch = db.batch()
    batch_count = 0

    for shortage in shortages:
        din = shortage.get("din", "")
        if not din:
            continue

        synced_dins.add(din)
        doc_ref = collection_ref.document(din)
        batch.set(doc_ref, shortage, merge=True)
        batch_count += 1

        # Firestore batch limit is 500
        if batch_count >= 400:
            batch.commit()
            batch = db.batch()
            batch_count = 0

    # Commit remaining
    if batch_count > 0:
        batch.commit()

    # Remove stale entries (shortages that are no longer active)
    existing_docs = collection_ref.stream()
    stale_batch = db.batch()
    stale_count = 0

    for doc in existing_docs:
        if doc.id not in synced_dins:
            stale_batch.delete(doc.reference)
            stale_count += 1

            if stale_count >= 400:
                stale_batch.commit()
                stale_batch = db.batch()
                stale_count = 0

    if stale_count > 0:
        stale_batch.commit()

    print(f"Synced {len(synced_dins)} shortages to Firestore")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("MyVita — Medication Shortages Sync")
    print(f"Started at: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    # Initialize Firebase
    db = init_firebase()
    print("Firebase initialized")

    # Fetch shortages
    raw_shortages = fetch_all_shortages()
    print(f"Fetched {len(raw_shortages)} raw shortage records")

    # Transform
    transformed = []
    for raw in raw_shortages:
        try:
            transformed.append(transform_shortage(raw))
        except Exception as e:
            print(f"Error transforming: {e}")

    print(f"Transformed {len(transformed)} shortages")

    # Sync to Firestore
    sync_to_firestore(db, transformed)

    print("=" * 60)
    print(f"Completed at: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
