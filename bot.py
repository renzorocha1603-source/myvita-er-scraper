from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
import time
import random
import os
import json
import re
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, messaging, firestore

# ══════════════════════════════════════════════════════════════
# 1. CONFIGURATION
# ══════════════════════════════════════════════════════════════

ZONES = {
    "H1Y": "montreal_east", "H1A": "montreal_east", "H1B": "montreal_east",
    "H1C": "montreal_east", "H1H": "montreal_north", "H1J": "montreal_north",
    "H2X": "montreal_central", "H3A": "montreal_central", "H3B": "montreal_central",
    "H4L": "montreal_north", "H4M": "montreal_north",
    "G1R": "quebec_central", "G1S": "quebec_central", "G1V": "quebec_ste_foy",
    "J8Y": "gatineau_hull", "J8Z": "gatineau_aylmer",
    "J1H": "sherbrooke", "J1K": "sherbrooke",
}

# ══════════════════════════════════════════════════════════════
# 2. FIREBASE SETUP
# ══════════════════════════════════════════════════════════════

db = None
try:
    creds_json = os.getenv("FIREBASE_CREDENTIALS")
    if creds_json:
        cred_dict = json.loads(creds_json)
        cred = credentials.Certificate(cred_dict)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred, {'projectId': 'myvita-app-c5ecd'})
        db = firestore.client()
        print("✅ Firebase & Firestore initialized (via GitHub Secret)")
    elif os.path.exists("firebase-credentials.json"):
        cred = credentials.Certificate("firebase-credentials.json")
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred, {'projectId': 'myvita-app-c5ecd'})
        db = firestore.client()
        print("✅ Firebase & Firestore initialized (via local file)")
    else:
        print("⚠️ No Firebase credentials found — running without Firestore")
except Exception as e:
    print(f"⚠️ Firebase Init Error: {e}")

# ══════════════════════════════════════════════════════════════
# 3. UTILITY FUNCTIONS
# ══════════════════════════════════════════════════════════════

def human_delay(min_ms=800, max_ms=2500):
    """Random human-like delay"""
    time.sleep(random.uniform(min_ms, max_ms) / 1000)

def get_zone(postal_code: str) -> str:
    fsa = postal_code[:3].upper()
    return ZONES.get(fsa, f"zone_{fsa}")

def get_postal_code():
    postal = os.getenv("POSTAL_CODE", "").replace(" ", "").strip()
    return postal if (postal and len(postal) >= 3) else "H1Y3H1"

def get_service_url():
    service = os.getenv("SERVICE", "blood-test")
    return f"https://portal3.clicsante.ca/services/{service}"

def debug_screenshot(page, name="debug"):
    """Save screenshot for debugging"""
    try:
        filename = f"debug_{name}_{datetime.now().strftime('%H%M%S')}.png"
        page.screenshot(path=filename)
        print(f"📸 Screenshot saved: {filename}")
    except:
        pass

def debug_page_text(page, step_name):
    """Print page text preview for debugging"""
    try:
        body_text = page.inner_text("body")
        print(f"\n📄 PAGE TEXT [{step_name}]:")
        print(body_text[:500])
        print("...\n")
    except:
        pass

# ══════════════════════════════════════════════════════════════
# 4. NOTIFICATION & DATA SAVING
# ══════════════════════════════════════════════════════════════

def get_user_token():
    """Get FCM token from Firestore (most recent user)"""
    if db is None:
        print("❌ Firestore not connected")
        return None
    try:
        users_ref = db.collection('users').order_by('fcmTokenUpdated', direction='DESCENDING').limit(1)
        docs = users_ref.stream()
        for doc in docs:
            data = doc.to_dict()
            token = data.get('fcmToken')
            if token:
                print(f"📱 Found FCM token from Firestore")
                return token
        print("⚠️ No FCM token found in Firestore")
        return None
    except Exception as e:
        print(f"❌ Error reading FCM token: {e}")
        return None

def send_notification(postal_code: str, booking_url: str):
    """Send FCM push notification with deep link"""
    token = get_user_token()
    if not token:
        print("⚠️ No FCM token — notification skipped")
        return
    try:
        message = messaging.Message(
            notification=messaging.Notification(
                title="🎉 Rendez-vous disponible!",
                body=f"Créneau trouvé près de {postal_code}. Touchez pour réserver."
            ),
            data={
                "url": booking_url,
                "postal": postal_code,
            },
            token=token,
        )
        messaging.send(message)
        print(f"✅ FCM Notification Sent → {booking_url[:80]}...")
    except Exception as e:
        print(f"❌ FCM Error: {e}")

def save_availability(postal_code: str, has_slots: bool, booking_url: str, slot_details: str):
    """Save availability check result to Firestore"""
    if db is None:
        return
    zone = get_zone(postal_code)
    now = datetime.now().isoformat()
    data = {
        "service": os.getenv("SERVICE", "blood-test"),
        "postal_code": postal_code,
        "zone": zone,
        "slots_found": has_slots,
        "booking_url": booking_url,
        "slot_details": slot_details,
        "last_checked": now,
    }
    try:
        db.collection("availability").document(zone).set(data)
        db.collection("availability").document(zone).collection("history").add({
            "slots_found": has_slots,
            "checked_at": now
        })
        print(f"🔥 Firestore Updated: {zone}")
    except Exception as e:
        print(f"❌ Firestore Error: {e}")

# ══════════════════════════════════════════════════════════════
# 5. BROWSER SETUP
# ══════════════════════════════════════════════════════════════

def launch_stealth_browser(p, headless=True):
    """Launch Playwright browser with stealth settings"""
    browser = p.chromium.launch(
        headless=headless,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
        ]
    )
    context = browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"
    )
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    # Block unnecessary resources for speed
    context.route("**/*.{png,jpg,jpeg,gif,svg,css,font,woff,woff2}", 
                  lambda route: route.abort())
    return browser, context

# ══════════════════════════════════════════════════════════════
# 6. NO FEES FILTER — Multiple fallback strategies
# ══════════════════════════════════════════════════════════════

def try_select_no_fees(page):
    """
    Select 'No fees' / 'Sans frais' option.
    Uses multiple strategies — checks, clicks, labels, radio buttons.
    """
    strategies = [
        # Strategy 1: Checkbox by label
        lambda: page.get_by_label("No fees").check(timeout=3000),
        lambda: page.get_by_label("Sans frais").check(timeout=3000),
        # Strategy 2: Click exact text
        lambda: page.get_by_text("No fees", exact=True).first.click(timeout=3000),
        lambda: page.get_by_text("Sans frais", exact=True).first.click(timeout=3000),
        # Strategy 3: Label filter
        lambda: page.locator("label").filter(has_text=re.compile(r"^No fees$")).first.click(timeout=3000),
        lambda: page.locator("label").filter(has_text=re.compile(r"^Sans frais$")).first.click(timeout=3000),
        # Strategy 4: Radio button
        lambda: page.locator("input[type='radio']").first.click(timeout=3000),
        lambda: page.locator("[role='radio']").first.click(timeout=3000),
    ]

    for i, strategy in enumerate(strategies):
        try:
            strategy()
            human_delay(500, 1000)
            print(f"✅ 'No fees' selected (strategy {i+1})")
            return True
        except Exception as e:
            continue

    print("❌ CRITICAL: Could not select 'No fees' — search will fail")
    return False

# ══════════════════════════════════════════════════════════════
# 7. DEEP CALENDAR VERIFICATION — Hybrid (Grok + Deep)
# ══════════════════════════════════════════════════════════════

def verify_real_slots(page):
    """
    ★ BULLETPROOF DEEP CALENDAR VERIFICATION ★
    Combines Grok's quick detection + Deep's ID extraction + month navigation.
    Returns (has_real_slots, details_string, deep_link_url)
    """
    body_text = page.inner_text("body")
    text_lower = body_text.lower()

    # ── QUICK NEGATIVE CHECK (Grok's approach) ──
    no_slot_phrases = [
        "aucune disponibilité", "no availability", "aucun rendez-vous",
        "no appointments", "désolé", "sorry",
        "aucun résultat", "no results",
    ]
    for phrase in no_slot_phrases:
        if phrase in text_lower:
            return False, f"No slots ({phrase})", None

    # ── QUICK POSITIVE ON RESULTS PAGE (Grok's optimization) ──
    quick_positive = [
        "disponible", "places disponibles", "prochain rendez-vous",
        "availabilities", "disponibilités",
    ]
    if any(x in text_lower for x in quick_positive):
        print("   ⚡ Quick positive detected on results page")
        return True, "Availability detected on results page", page.url

    # ── DEEP CLINIC VERIFICATION (Deep's approach) ──
    print("   🔍 Starting deep clinic verification...")

    clinic_selectors = [
        "article",
        ".establishment-card",
        "[class*='establishment']",
        "[class*='result-item']",
        "a[href*='establishment']",
        ".clinic-item",
        "[class*='clinic']",
        "[class*='result']",
    ]

    for selector in clinic_selectors:
        try:
            clinics = page.locator(selector)
            count = clinics.count()
            if count == 0:
                continue

            # Check up to 5 clinics
            for i in range(min(count, 5)):
                try:
                    clinic = clinics.nth(i)
                    if not clinic.is_visible():
                        continue

                    clinic_name = clinic.inner_text()[:60].replace('\n', ' ')
                    print(f"   🏥 Clinic #{i+1}: {clinic_name}...")
                    clinic.click(timeout=7000)
                    human_delay(2000, 3500)

                    # ── EXTRACT IDs from URL ──
                    current_url = page.url
                    establishment_id = None
                    service_id = None

                    id_match = re.search(r'establishmentId[=:]\s*(\d+)', current_url)
                    if id_match:
                        establishment_id = id_match.group(1)

                    svc_match = re.search(r'serviceId[=:]\s*(\d+)', current_url)
                    if svc_match:
                        service_id = svc_match.group(1)

                    # ── FIND BOOKING BUTTON (Grok's selectors + Deep's selectors) ──
                    booking_btn = None
                    booking_selectors = [
                        "text=Prendre RDV",
                        "text=Prendre rendez-vous",
                        "text=Réserver",
                        "text=Book",
                        "a:has-text('Prendre RDV')",
                        "button:has-text('Prendre RDV')",
                        "a:has-text('Prendre rendez-vous')",
                        "button:has-text('Prendre rendez-vous')",
                        "[class*='booking']",
                        "a[href*='appointment']",
                        "a[href*='rdv']",
                    ]

                    for btn_sel in booking_selectors:
                        try:
                            btn = page.locator(btn_sel).first
                            if btn.count() > 0 and btn.is_visible():
                                booking_btn = btn
                                break
                        except:
                            continue

                    if booking_btn:
                        print("      → Clicking booking button...")
                        booking_btn.click(timeout=8000)
                        human_delay(3000, 5000)

                        # ── WE'RE ON THE CALENDAR PAGE ──
                        calendar_url = page.url
                        calendar_text = page.inner_text("body")
                        calendar_lower = calendar_text.lower()

                        # Try extracting IDs from calendar URL too
                        if not establishment_id:
                            id_match = re.search(r'establishmentId[=:]\s*(\d+)', calendar_url)
                            if id_match:
                                establishment_id = id_match.group(1)
                        if not service_id:
                            svc_match = re.search(r'serviceId[=:]\s*(\d+)', calendar_url)
                            if svc_match:
                                service_id = svc_match.group(1)

                        # ── CALENDAR DETECTION (Grok's month check) ──
                        cal_indicators = [
                            "mai", "juin", "juillet", "août", "sept", "oct", "nov", "déc",
                            "janvier", "février", "mars", "avril",
                            "may", "june", "july", "august", "september", "october",
                            "january", "february", "march", "april",
                            "lun", "mar", "mer", "jeu", "ven", "sam", "dim",
                            "mon", "tue", "wed", "thu", "fri", "sat", "sun",
                        ]
                        is_calendar = any(ind in calendar_lower for ind in cal_indicators)

                        if is_calendar:
                            complet_count = calendar_lower.count("complet")

                            # ── POSITIVE INDICATORS ──
                            positive_indicators = [
                                "disponible", "available", "ouvert", "open",
                                "à venir", "coming soon", "réserver", "book now",
                                "sélectionner", "select", "choisir", "choose",
                            ]
                            has_positive = any(ind in calendar_lower for ind in positive_indicators)

                            # ── CLICKABLE DATES ──
                            date_selectors = [
                                "[class*='available']",
                                "[class*='disponible']",
                                "[class*='open']",
                                "[class*='selectable']",
                                "[class*='clickable']",
                                "button[class*='day']:not([disabled])",
                                "td:not([class*='complet']):not([class*='full'])",
                            ]

                            clickable_dates = 0
                            for date_sel in date_selectors:
                                try:
                                    dates = page.locator(date_sel)
                                    if dates.count() > 0:
                                        clickable_dates = dates.count()
                                        break
                                except:
                                    continue

                            # ── NEXT MONTH NAVIGATION (Deep's improvement) ──
                            if complet_count > 20 and clickable_dates == 0:
                                try:
                                    next_month_selectors = [
                                        "[aria-label*='Next']",
                                        "[aria-label*='Suivant']",
                                        "[class*='next']",
                                        "button:has-text('›')",
                                        "button:has-text('»')",
                                    ]
                                    for nm_sel in next_month_selectors:
                                        try:
                                            next_month = page.locator(nm_sel).first
                                            if next_month.count() > 0 and next_month.is_visible():
                                                print("      → Current month full, checking next month...")
                                                next_month.click()
                                                human_delay(1000, 2000)
                                                calendar_url = page.url
                                                calendar_text = page.inner_text("body")
                                                calendar_lower = calendar_text.lower()
                                                complet_count = calendar_lower.count("complet")
                                                has_positive = any(ind in calendar_lower for ind in positive_indicators)
                                                for date_sel in date_selectors:
                                                    try:
                                                        dates = page.locator(date_sel)
                                                        if dates.count() > 0:
                                                            clickable_dates = dates.count()
                                                            break
                                                    except:
                                                        continue
                                                break
                                        except:
                                            continue
                                except:
                                    pass

                            print(f"      📅 Calendar: Complet={complet_count}, Positive={has_positive}, Clickable={clickable_dates}")

                            # ── BUILD DEEP LINK ──
                            if establishment_id and service_id:
                                deep_link = f"https://portal3.clicsante.ca/portail/index.html#/appointments/new?establishmentId={establishment_id}&serviceId={service_id}"
                            elif establishment_id:
                                deep_link = f"https://portal3.clicsante.ca/portail/index.html#/appointments/new?establishmentId={establishment_id}"
                            else:
                                deep_link = calendar_url

                            # ── VERDICT ──
                            if clickable_dates > 0 or (has_positive and complet_count < 25):
                                print(f"      🎉 REAL SLOTS FOUND! → {deep_link[:80]}...")
                                return True, f"Calendar has open dates (Complet: {complet_count}, Clickable: {clickable_dates})", deep_link
                            else:
                                print(f"      ❌ Calendar full (Complet: {complet_count})")
                                page.go_back()
                                human_delay(1000, 2000)
                                continue
                        else:
                            print(f"      ⚠️ Not a calendar page — skipping")
                            page.go_back()
                            human_delay(500, 1000)
                            continue
                    else:
                        print(f"      ℹ️ No booking button — skipping")
                        page.go_back()
                        human_delay(500, 1000)
                        continue

                except Exception as e:
                    print(f"      ⚠️ Error on clinic: {e}")
                    try:
                        page.go_back()
                        human_delay(500, 1000)
                    except:
                        pass
                    continue

        except Exception as e:
            print(f"   ⚠️ Selector error: {e}")
            continue

    return False, "No real slots found after deep check", page.url

# ══════════════════════════════════════════════════════════════
# 8. MAIN AVAILABILITY CHECK
# ══════════════════════════════════════════════════════════════

def check_availability(postal_code_override=None):
    postal_code = postal_code_override if postal_code_override else get_postal_code()
    service_url = get_service_url()
    headless = os.getenv("HEADLESS", "true").lower() != "false"

    print(f"\n{'='*60}")
    print(f"🚀 ClicSanté Search: {postal_code} @ {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}")

    with sync_playwright() as p:
        browser, context = launch_stealth_browser(p, headless=headless)
        page = context.new_page()

        try:
            # Load the service page
            print("📄 Loading ClicSanté...")
            page.goto(service_url, wait_until="networkidle", timeout=45000)
            human_delay(1500, 3000)

            # Dismiss any popups
            try:
                page.keyboard.press("Escape")
                human_delay(300, 500)
            except:
                pass

            # Select "No fees" filter
            print("🎯 Selecting 'No fees'...")
            filter_applied = try_select_no_fees(page)
            if not filter_applied:
                human_delay(1000, 2000)
                filter_applied = try_select_no_fees(page)

            human_delay(500, 1000)

            # Enter postal code
            print("⌨️  Entering postal code...")
            postal_selectors = [
                "input[placeholder*='A1A']",
                "input[placeholder*='postal']",
                "input[autocomplete='postal-code']",
                "input[type='text']",
            ]

            postal_box = None
            for selector in postal_selectors:
                try:
                    postal_box = page.locator(selector).first
                    if postal_box.is_visible():
                        print(f"   Found input: {selector}")
                        break
                except:
                    continue

            if postal_box:
                postal_box.click()
                human_delay(200, 400)
                postal_box.fill("")
                human_delay(100, 200)
                for char in postal_code:
                    postal_box.type(char, delay=random.randint(50, 150))
                human_delay(400, 800)
                print(f"   Entered: {postal_code}")
            else:
                print("   ❌ Could not find postal input — trying fallback")
                try:
                    page.get_by_placeholder("ex. A1A 1A1").first.fill(postal_code)
                except:
                    pass

            human_delay(500, 1000)

            # Click Search
            search_btn = page.get_by_role("button", name=re.compile(r"Search|Rechercher|Chercher", re.I))
            if search_btn.count() > 0 and search_btn.first.is_visible():
                search_btn.first.click()
                print("   🔍 Clicked Search")

            # Wait for results
            print("⏳ Waiting for results...")
            try:
                page.wait_for_selector(
                    ".establishment-card, .results-list, .no-results, [class*='result'], [class*='clinic'], article",
                    timeout=25000
                )
                human_delay(3000, 5000)
            except:
                print("   ⚠️ Results container not found — checking anyway")

            # Debug
            debug_screenshot(page, f"results_{postal_code}")
            debug_page_text(page, "results")

            # Verify real slots
            print("🔍 Verifying real slots (deep calendar check)...")
            has_slots, detail, booking_url = verify_real_slots(page)

            if has_slots:
                print(f"\n🎉 SUCCESS! Real free slots found!")
                print(f"   Details: {detail}")
                print(f"   Deep link: {booking_url}")
                send_notification(postal_code, booking_url)
                save_availability(postal_code, True, booking_url, detail)
            else:
                print(f"\n❌ No free slots found")
                print(f"   Details: {detail}")
                save_availability(postal_code, False, page.url, detail)

            return has_slots

        except Exception as e:
            print(f"🚨 Script Error: {e}")
            import traceback
            traceback.print_exc()
            debug_screenshot(page, f"ERROR_{postal_code}")
            return False
        finally:
            if headless:
                browser.close()
            else:
                print("🔍 Browser left open for debugging — close manually")
                input("Press Enter to close browser...")
                browser.close()

# ══════════════════════════════════════════════════════════════
# 9. MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    postal_codes = ["H1Y3H1", "H4L2B5", "H2X1Y7", "G1R2A3", "J8Y3H1"]

    for postal in postal_codes:
        print(f"\n{'='*50}")
        print(f"🔍 Searching: {postal}")
        print(f"{'='*50}")
        check_availability(postal_code_override=postal)
        time.sleep(3)
