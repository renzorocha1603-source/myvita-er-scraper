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
            # Check if we're past the Cloudflare page
            title = page.title()
            if 'Just a moment' not in title and 'Cloudflare' not in title:
                print("   ✅ Cloudflare passed!")
                return True
            
            # Check for challenge iframe
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
    
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()
        
        try:
            # Go to the search page
            print("   Navigating to search page...")
            page.goto(f"{DRUG_SHORTAGES_URL}search", wait_until='networkidle', timeout=60000)
            
            # Wait for Cloudflare if present
            if 'Just a moment' in page.title() or 'Cloudflare' in page.title():
                if not wait_for_cloudflare(page):
                    print("   ❌ Could not bypass Cloudflare")
                    return []
            
            # Wait for page to load
            page.wait_for_timeout(3000)
            
            # Try to find and click the search/filter options
            # Look for status filter
            try:
                # Try to select "Active Confirmed" status
                status_selectors = [
                    'select[name="status"]',
                    '#status',
                    '[data-testid="status-filter"]',
                    'select[class*="filter"]',
                ]
                
                for selector in status_selectors:
                    if page.locator(selector).count() > 0:
                        page.select_option(selector, 'active_confirmed')
                        print("   ✅ Selected 'Active Confirmed' filter")
                        break
            except Exception as e:
                print(f"   ⚠️ Could not set status filter: {e}")
            
            # Try to find search results
            page.wait_for_timeout(3000)
            
            # Look for results in the page
            # Try different selectors for shortage items
            result_selectors = [
                '.shortage-item',
                '[data-shortage-id]',
                '.search-result',
                'table tbody tr',
                '.card',
                '.list-item',
            ]
            
            results_found = False
            for selector in result_selectors:
                if page.locator(selector).count() > 0:
                    count = page.locator(selector).count()
                    print(f"   Found {count} results with selector: {selector}")
                    results_found = True
                    
                    # Extract data from each result
                    for i in range(min(count, 100)):  # Limit to 100 for testing
                        try:
                            item = page.locator(selector).nth(i)
                            
                            # Try to extract text content
                            text = item.inner_text()
                            
                            # Parse the text content
                            shortage = parse_shortage_text(text, i)
                            if shortage:
                                all_shortages.append(shortage)
                        except Exception as e:
                            print(f"   ⚠️ Error extracting result {i}: {e}")
                    
                    break
            
            if not results_found:
                print("   ⚠️ No results found on page")
                # Print page content for debugging
                content = page.content()
                print(f"   Page content length: {len(content)}")
                print(f"   Page title: {page.title()}")
                
                # Try to find any table or list
                text_content = page.inner_text('body')
                print(f"   Body text first 500 chars: {text_content[:500]}")
            
        except Exception as e:
            print(f"   ❌ Error: {e}")
        finally:
            browser.close()
    
    print(f"   ✅ Total Drug Shortages Canada entries: {len(all_shortages)}")
    return all_shortages


def parse_shortage_text(text: str, index: int) -> Optional[Dict[str, Any]]:
    """Parse shortage text from the page."""
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    
    if not lines:
        return None
    
    # Try to extract meaningful data
    shortage = {
        'report_id': f"dsc_{index}",
        'brand_name': lines[0] if len(lines) > 0 else '',
        'companyName': '',
        'status': 'unknown',
        'source': 'DrugShortagesCanada',
    }
    
    # Look for status keywords
    full_text = text.lower()
    if 'active' in full_text or 'actif' in full_text:
        shortage['status'] = 'active_confirmed'
    elif 'anticipated' in full_text or 'anticip' in full_text:
        shortage['status'] = 'anticipated_shortage'
    elif 'resolved' in full_text or 'résolu' in full_text:
        shortage['status'] = 'resolved'
    elif 'discontinued' in full_text or 'discontinu' in full_text:
        shortage['status'] = 'discontinued'
    
    # Look for DIN (8-digit number)
    din_match = re.search(r'\b(\d{8})\b', text)
    if din_match:
        shortage['din'] = din_match.group(1)
        shortage['report_id'] = f"dsc_{din_match.group(1)}"
    
    # Look for dates
    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', text)
    if date_match:
        shortage['updated_date'] = date_match.group(1)
    
    return shortage


# ═══════════════════════════════════════════════════════════════
# RAMQ SCRAPER (via browser)
# ═══════════════════════════════════════════════════════════════

def fetch_ramq_with_browser() -> List[Dict[str, Any]]:
    """Fetch RAMQ shortages using Playwright."""
    print("   [RAMQ] Starting browser...")
    all_shortages = []
    
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
            
            # Try to find table with shortages
            table_selectors = [
                'table',
                '.table',
                '#ruptures-table',
                '[class*="table"]',
            ]
            
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
                            
                            if cell_count >= 3:
                                cell_texts = []
                                for j in range(cell_count):
                                    cell_texts.append(cells.nth(j).inner_text().strip())
                                
                                shortage = {
                                    'report_id': f"ramq_{i}",
                                    'generic_name': cell_texts[0] if len(cell_texts) > 0 else '',
                                    'brand_name': cell_texts[1] if len(cell_texts) > 1 else '',
                                    'form': cell_texts[2] if len(cell_texts) > 2 else '',
                                    'strength': cell_texts[3] if len(cell_texts) > 3 else '',
                                    'source': 'RAMQ',
                                    'status': 'unknown',
                                }
                                
                                # Look for DIN in text
                                full_text = ' '.join(cell_texts)
                                din_match = re.search(r'\b(\d{8})\b', full_text)
                                if din_match:
                                    shortage['din'] = din_match.group(1)
                                    shortage['report_id'] = f"ramq_{din_match.group(1)}"
                                
                                # Look for status
                                if 'rupture' in full_text.lower():
                                    shortage['status'] = 'confirmed_shortage'
                                    shortage['is_active'] = True
                                elif 'disponible' in full_text.lower():
                                    shortage['status'] = 'available'
                                    shortage['is_active'] = False
                                
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
        'form': raw.get('form', ''),
        'strength': raw.get('strength', ''),
        'din': raw.get('din', ''),
        'status': raw.get('status', 'unknown'),
        'is_active': raw.get('is_active', raw.get('status') in ['active_confirmed', 'confirmed_shortage', 'anticipated_shortage']),
        'source': raw.get('source', ''),
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
                print(f"     - {shortage['brand_name']}")
                print(f"       Status: {shortage['status']}")
                print(f"       DIN: {shortage.get('din', 'N/A')}")
            
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
                print(f"     - {shortage['brand_name']} ({shortage['generic_name']})")
                print(f"       Status: {shortage['status']}")
                print(f"       DIN: {shortage.get('din', 'N/A')}")
            
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
    else:
        print("\n⚠️ No data fetched — old Firestore data remains unchanged")
    
    print(f"\nCompleted at: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
