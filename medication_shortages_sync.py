"""
MyVita — Medication Shortages Sync
Uses Playwright to bypass Cloudflare protection
Primary: Drug Shortages Canada API via browser
Fallback: RAMQ via browser scraping
"""

import json
import os
import re
import time
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import firebase_admin
from firebase_admin import credentials, firestore
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

DRUG_SHORTAGES_URL = "https://www.drugshortagescanada.ca/"
DRUG_SHORTAGES_EMAIL = os.environ.get("DRUG_SHORTAGES_EMAIL", "RENZOROCHA1603@GMAIL.COM")
DRUG_SHORTAGES_PASSWORD = os.environ.get("DRUG_SHORTAGES_PASSWORD", "Angelorea1603$")

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
# PLAYWRIGHT BROWSER SETUP
# ═══════════════════════════════════════════════════════════════

def create_browser_context(playwright):
    """Create a browser context with realistic settings."""
    browser = playwright.chromium.launch(
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
        timezone_id='America/Toronto',
        extra_http_headers={
            'Accept-Language': 'fr-CA,fr;q=0.9,en;q=0.8',
        }
    )
    
    # Add stealth scripts
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5]
        });
        Object.defineProperty(navigator, 'languages', {
            get: () => ['fr-CA', 'fr', 'en-US', 'en']
        });
        window.chrome = {
            runtime: {}
        };
    """)
    
    return browser, context


def wait_for_cloudflare(page, timeout=30):
    """Wait for Cloudflare challenge to complete."""
    print("   Waiting for Cloudflare challenge...")
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            title = page.title()
            if 'Just a moment' not in title and 'Cloudflare' not in title:
                print("   ✅ Cloudflare passed!")
                return True
            
            if page.locator('iframe[title*="challenge"]').count() > 0:
                print("   🔄 Cloudflare challenge detected, waiting...")
            
            time.sleep(2)
        except Exception:
            pass
    
    print("   ⚠️ Cloudflare timeout")
    return False


# ═══════════════════════════════════════════════════════════════
# DRUG SHORTAGES CANADA SCRAPER (via browser)
# ═══════════════════════════════════════════════════════════════

def fetch_drug_shortages_canada() -> List[Dict[str, Any]]:
    """Fetch drug shortages from Drug Shortages Canada using Playwright."""
    print("   [Drug Shortages Canada] Starting browser...")
    all_shortages = []
    seen_ids = set()
    
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()
        
        try:
            # Go to the search page
            print("   Navigating to search page...")
            page.goto(f"{DRUG_SHORTAGES_URL}search", 
                     wait_until='networkidle', timeout=60000)
            
            # Wait for Cloudflare if present
            if 'Just a moment' in page.title() or 'Cloudflare' in page.title():
                if not wait_for_cloudflare(page):
                    print("   ❌ Could not bypass Cloudflare")
                    return []
            
            # Wait for page to load
            page.wait_for_timeout(5000)
            
            # Extract table data
            print("   Extracting table data...")
            page.wait_for_timeout(3000)
            
            # Get all rows
            rows = page.locator('table tbody tr')
            row_count = rows.count()
            print(f"   Found {row_count} rows")
            
            for i in range(row_count):
                try:
                    row = rows.nth(i)
                    cells = row.locator('td')
                    cell_count = cells.count()
                    
                    cell_texts = []
                    for j in range(cell_count):
                        cell_texts.append(cells.nth(j).inner_text().strip())
                    
                    # Parse using known structure:
                    # [0] = Status, [1] = Brand Name, [2] = Company Name
                    # [3] = Strength, [4] = Date, [5] = Report ID
                    
                    if len(cell_texts) >= 6:
                        raw_status = cell_texts[0]
                        brand_name = cell_texts[1]
                        company_name = cell_texts[2]
                        strength = cell_texts[3]
                        date = cell_texts[4]
                        report_id = cell_texts[5]
                        
                        # Map status
                        status = 'unknown'
                        status_lower = raw_status.lower()
                        
                        if 'actual' in status_lower or 'active' in status_lower or 'confirm' in status_lower or 'penurie' in status_lower or 'pénurie' in status_lower:
                            status = 'active_confirmed'
                        elif 'anticipated' in status_lower or 'anticip' in status_lower:
                            status = 'anticipated_shortage'
                        elif 'avoid' in status_lower or 'évit' in status_lower:
                            status = 'avoided_shortage'
                        elif 'resolved' in status_lower or 'résolu' in status_lower:
                            status = 'resolved'
                        elif 'discontinu' in status_lower:
                            status = 'discontinued'
                        
                        shortage = {
                            'report_id': f"dsc_{report_id}",
                            'brand_name': brand_name,
                            'companyName': company_name,
                            'strength': strength,
                            'din': report_id,
                            'status': status,
                            'is_active': status in ['active_confirmed', 'anticipated_shortage'],
                            'updated_date': date,
                            'source': 'DrugShortagesCanada',
                        }
                        
                        if shortage['report_id'] not in seen_ids:
                            seen_ids.add(shortage['report_id'])
                            all_shortages.append(shortage)
                
                except Exception as e:
                    print(f"   ⚠️ Error extracting row {i}: {e}")
            
        except Exception as e:
            print(f"   ❌ Error: {e}")
        finally:
            browser.close()
    
    print(f"   ✅ Total Drug Shortages Canada entries: {len(all_shortages)}")
    return all_shortages


# ═══════════════════════════════════════════════════════════════
# RAMQ SCRAPER (via browser)
# ═══════════════════════════════════════════════════════════════

def fetch_ramq_with_browser() -> List[Dict[str, Any]]:
    """Fetch RAMQ shortages using Playwright."""
    print("   [RAMQ] Starting browser...")
    all_shortages = []
    seen_ids = set()
    
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()
        
        try:
            print("   Navigating to RAMQ page...")
            page.goto(RAMQ_URL, wait_until='networkidle', timeout=60000)
            
            # Wait for Cloudflare if present
            if 'Just a moment' in page.title() or 'Cloudflare' in page.title():
                if not wait_for_cloudflare(page):
                    print("   ❌ Could not bypass Cloudflare")
                    return []
            
            # Wait for page to load
            page.wait_for_timeout(5000)
            
            # Find table
            table_selectors = ['table', '.table', '#ruptures-table', '[class*="table"]']
            
            for selector in table_selectors:
                if page.locator(selector).count() > 0:
                    print(f"   Found table with selector: {selector}")
                    
                    # Get all rows
                    rows = page.locator(f'{selector} tbody tr')
                    if rows.count() == 0:
                        rows = page.locator(f'{selector} tr')
                    
                    row_count = rows.count()
                    print(f"   Found {row_count} rows")
                    
                    for i in range(min(row_count, 200)):
                        try:
                            row = rows.nth(i)
                            cells = row.locator('td')
                            cell_count = cells.count()
                            
                            cell_texts = []
                            for j in range(cell_count):
                                cell_texts.append(cells.nth(j).inner_text().strip())
                            
                            # Parse using known structure:
                            # [0] = Generic Name, [1] = Brand Name, [2] = Form
                            # [3] = Strength, [4] = DIN, [5] = Status, [6] = Date
                            
                            if len(cell_texts) >= 5:
                                generic_name = cell_texts[0]
                                brand_name = cell_texts[1]
                                form = cell_texts[2] if len(cell_texts) > 2 else ''
                                strength = cell_texts[3] if len(cell_texts) > 3 else ''
                                din = cell_texts[4] if len(cell_texts) > 4 else ''
                                status_text = cell_texts[5] if len(cell_texts) > 5 else ''
                                date = cell_texts[6] if len(cell_texts) > 6 else ''
                                
                                # Map status
                                status = 'unknown'
                                is_active = False
                                status_lower = status_text.lower()
                                
                                if 'disponible' in status_lower and 'sous peu' in status_lower:
                                    status = 'available_soon'
                                elif 'disponible' in status_lower:
                                    status = 'available'
                                elif 'rupture' in status_lower and 'anticip' in status_lower:
                                    status = 'anticipated_shortage'
                                    is_active = True
                                elif 'rupture' in status_lower:
                                    status = 'confirmed_shortage'
                                    is_active = True
                                elif 'vérif' in status_lower or 'verif' in status_lower:
                                    status = 'verification'
                                    is_active = True
                                elif 'retir' in status_lower or 'cess' in status_lower:
                                    status = 'discontinued'
                                
                                shortage = {
                                    'report_id': f"ramq_{din}",
                                    'generic_name': generic_name,
                                    'brand_name': brand_name,
                                    'form': form,
                                    'strength': strength,
                                    'din': din,
                                    'status': status,
                                    'is_active': is_active,
                                    'updated_date': date,
                                    'source': 'RAMQ',
                                }
                                
                                if shortage['report_id'] not in seen_ids:
                                    seen_ids.add(shortage['report_id'])
                                    all_shortages.append(shortage)
                        
                        except Exception as e:
                            print(f"   ⚠️ Error extracting row {i}: {e}")
                    
                    break
            
            if not all_shortages:
                print("   ⚠️ No table found on RAMQ page")
                content = page.inner_text('body')
                print(f"   Body text first 1000 chars: {content[:1000]}")
        
        except Exception as e:
            print(f"   ❌ Error: {e}")
        finally:
            browser.close()
    
    print(f"   ✅ Total RAMQ entries: {len(all_shortages)}")
    return all_shortages


# ═══════════════════════════════════════════════════════════════
# TRANSFORM + SYNC
# ═══════════════════════════════════════════════════════════════

def generate_search_keywords(shortage: Dict[str, Any]) -> List[str]:
    """Generate search keywords."""
    keywords = []
    fields = [
        shortage.get('brand_name', ''),
        shortage.get('generic_name', ''),
        shortage.get('din', ''),
        shortage.get('strength', ''),
        shortage.get('form', ''),
        shortage.get('companyName', ''),
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


def transform_shortage(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Transform shortage data for Firestore."""
    return {
        'report_id': raw.get('report_id', ''),
        'brand_name': raw.get('brand_name', ''),
        'generic_name': raw.get('generic_name', ''),
        'companyName': raw.get('companyName', ''),
        'company_name': raw.get('companyName', ''),  # For backward compatibility
        'form': raw.get('form', ''),
        'strength': raw.get('strength', ''),
        'din': raw.get('din', ''),
        'status': raw.get('status', 'unknown'),
        'is_active': raw.get('is_active', False),
        'source': raw.get('source', ''),
        'updated_date': raw.get('updated_date', ''),
        'search_keywords': generate_search_keywords(raw),
        'updated_at': datetime.now(timezone.utc),
    }


def sync_to_firestore(db, shortages):
    """Sync shortages to Firestore."""
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
    print("MyVita — Medication Shortages Sync (Playwright)")
    print(f"Started at: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)
    
    db = init_firebase()
    print("✅ Firebase initialized\n")
    
    all_shortages = []
    all_synced_ids = set()
    
    # ─── PRIMARY: Drug Shortages Canada ───
    print("─" * 60)
    print("PRIMARY: Drug Shortages Canada (via browser)")
    print("─" * 60)
    
    try:
        dsc_shortages = fetch_drug_shortages_canada()
        
        if dsc_shortages:
            transformed = [transform_shortage(s) for s in dsc_shortages]
            all_shortages.extend(transformed)
            
            print(f"\n   Sample DSC data:")
            for shortage in transformed[:5]:
                print(f"     - Brand: {shortage['brand_name']}")
                print(f"       Company: {shortage['companyName']}")
                print(f"       Status: {shortage['status']}")
                print(f"       DIN: {shortage.get('din', 'N/A')}")
                print()
            
            synced_ids = sync_to_firestore(db, transformed)
            all_synced_ids.update(synced_ids)
        else:
            print("   ⚠️ No data from Drug Shortages Canada")
    
    except Exception as e:
        print(f"   ❌ Drug Shortages Canada error: {e}")
    
    # ─── FALLBACK: RAMQ ───
    print(f"\n{'─' * 60}")
    print("FALLBACK: RAMQ (via browser)")
    print("─" * 60)
    
    try:
        ramq_shortages = fetch_ramq_with_browser()
        
        if ramq_shortages:
            transformed = [transform_shortage(s) for s in ramq_shortages]
            all_shortages.extend(transformed)
            
            print(f"\n   Sample RAMQ data:")
            for shortage in transformed[:5]:
                print(f"     - Brand: {shortage['brand_name']}")
                print(f"       Generic: {shortage['generic_name']}")
                print(f"       Status: {shortage['status']}")
                print(f"       DIN: {shortage.get('din', 'N/A')}")
                print()
            
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
