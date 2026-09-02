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
            # Go to the search page with active shortages filter
            print("   Navigating to search page...")
            page.goto(f"{DRUG_SHORTAGES_URL}search?filter_status=active_confirmed", 
                     wait_until='networkidle', timeout=60000)
            
            # Wait for Cloudflare if present
            if 'Just a moment' in page.title() or 'Cloudflare' in page.title():
                if not wait_for_cloudflare(page):
                    print("   ❌ Could not bypass Cloudflare")
                    return []
            
            # Wait for page to load
            page.wait_for_timeout(5000)
            
            # Try to click "Active Confirmed" filter if it exists
            try:
                # Look for filter buttons or dropdowns
                active_filter_selectors = [
                    'button:has-text("Active")',
                    'a:has-text("Active")',
                    'select option[value="active_confirmed"]',
                    '[data-filter="active_confirmed"]',
                    'label:has-text("Active")',
                ]
                
                for selector in active_filter_selectors:
                    if page.locator(selector).count() > 0:
                        page.locator(selector).first.click()
                        print("   ✅ Clicked Active filter")
                        page.wait_for_timeout(3000)
                        break
            except Exception as e:
                print(f"   ⚠️ Could not click filter: {e}")
            
            # Try pagination - get multiple pages
            max_pages = 5  # Get up to 5 pages (100 results per page = 500 results)
            
            for page_num in range(max_pages):
                print(f"   Processing page {page_num + 1}...")
                
                # Wait for results to load
                page.wait_for_timeout(3000)
                
                # Extract table rows
                rows = page.locator('table tbody tr')
                row_count = rows.count()
                print(f"   Found {row_count} rows on page {page_num + 1}")
                
                if row_count == 0:
                    break
                
                for i in range(row_count):
                    try:
                        row = rows.nth(i)
                        cells = row.locator('td')
                        cell_count = cells.count()
                        
                        if cell_count >= 5:
                            cell_texts = []
                            for j in range(cell_count):
                                cell_texts.append(cells.nth(j).inner_text().strip())
                            
                            # Parse the cells
                            shortage = parse_dsc_row(cell_texts)
                            
                            if shortage and shortage['report_id'] not in seen_ids:
                                seen_ids.add(shortage['report_id'])
                                all_shortages.append(shortage)
                    
                    except Exception as e:
                        print(f"   ⚠️ Error extracting row {i}: {e}")
                
                # Check if there's a "Next" button
                try:
                    next_button = page.locator('button:has-text("Next")')
                    if next_button.count() > 0 and next_button.is_enabled():
                        next_button.click()
                        page.wait_for_timeout(3000)
                    else:
                        print("   No more pages")
                        break
                except Exception:
                    print("   No pagination found")
                    break
            
        except Exception as e:
            print(f"   ❌ Error: {e}")
        finally:
            browser.close()
    
    print(f"   ✅ Total Drug Shortages Canada entries: {len(all_shortages)}")
    return all_shortages


def parse_dsc_row(cells: List[str]) -> Optional[Dict[str, Any]]:
    """Parse a Drug Shortages Canada table row."""
    if len(cells) < 5:
        return None
    
    # Typical DSC table structure:
    # [0] = Brand name + status
    # [1] = Company name
    # [2] = Strength
    # [3] = Date
    # [4] = Report ID / DIN
    
    raw_brand = cells[0] if len(cells) > 0 else ''
    company = cells[1] if len(cells) > 1 else ''
    strength = cells[2] if len(cells) > 2 else ''
    date = cells[3] if len(cells) > 3 else ''
    report_id = cells[4] if len(cells) > 4 else ''
    
    # Extract status from brand name
    status = 'unknown'
    brand_name = raw_brand
    
    status_patterns = [
        (r'^(Resolved|Résolu)', 'resolved'),
        (r'^(Active Confirmed|Pénurie active confirmée)', 'active_confirmed'),
        (r'^(Anticipated|Anticipée)', 'anticipated_shortage'),
        (r'^(Avoided|Évitée)', 'avoided_shortage'),
        (r'^(Discontinued|Discontinué)', 'discontinued'),
    ]
    
    for pattern, status_value in status_patterns:
        match = re.search(pattern, raw_brand, re.IGNORECASE)
        if match:
            status = status_value
            # Remove status from brand name
            brand_name = re.sub(pattern, '', raw_brand, flags=re.IGNORECASE).strip()
            break
    
    # Extract DIN - look for 6-8 digit numbers in the report ID or brand
    din = ''
    din_match = re.search(r'(\d{6,8})', report_id)
    if din_match:
        din = din_match.group(1)
    else:
        din_match = re.search(r'(\d{6,8})', raw_brand)
        if din_match:
            din = din_match.group(1)
    
    # Clean brand name - remove trailing numbers and special chars
    brand_name = re.sub(r'\s*\d{6,8}\s*$', '', brand_name).strip()
    brand_name = re.sub(r'\s+', ' ', brand_name).strip()
    
    return {
        'report_id': f"dsc_{report_id}" if report_id else f"dsc_{din}",
        'brand_name': brand_name,
        'companyName': company,
        'strength': strength,
        'din': din,
        'status': status,
        'is_active': status in ['active_confirmed', 'anticipated_shortage'],
        'updated_date': date,
        'source': 'DrugShortagesCanada',
    }


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
                                
                                shortage = parse_ramq_row(cell_texts, i)
                                
                                if shortage and shortage['report_id'] not in seen_ids:
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


def parse_ramq_row(cells: List[str], index: int) -> Optional[Dict[str, Any]]:
    """Parse a RAMQ table row."""
    if len(cells) < 3:
        return None
    
    # RAMQ table structure:
    # [0] = Generic name / Brand name
    # [1] = Form/Strength
    # [2] = Status
    # [3] = DIN (might be in text)
    
    full_text = ' '.join(cells)
    
    # Extract brand name and generic name
    # Format: "Brand Name (generic name)"
    brand_name = cells[0] if len(cells) > 0 else ''
    generic_name = ''
    
    # Try to split brand and generic
    match = re.match(r'^(.*?)\s*\((.*?)\)', brand_name)
    if match:
        brand_name = match.group(1).strip()
        generic_name = match.group(2).strip()
    
    # Extract DIN - look for 8-digit number
    din = ''
    din_match = re.search(r'\b(\d{8})\b', full_text)
    if din_match:
        din = din_match.group(1)
    
    # Determine status
    status = 'unknown'
    is_active = False
    
    status_keywords = {
        'rupture': ('confirmed_shortage', True),
        'disponible': ('available', False),
        'vérification': ('verification', True),
        'retiré': ('withdrawn', False),
        'cessé': ('discontinued', False),
    }
    
    full_text_lower = full_text.lower()
    for keyword, (status_value, active) in status_keywords.items():
        if keyword in full_text_lower:
            status = status_value
            is_active = active
            break
    
    return {
        'report_id': f"ramq_{din}" if din else f"ramq_{index}",
        'brand_name': brand_name,
        'generic_name': generic_name,
        'form': cells[1] if len(cells) > 1 else '',
        'din': din,
        'status': status,
        'is_active': is_active,
        'source': 'RAMQ',
    }


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
