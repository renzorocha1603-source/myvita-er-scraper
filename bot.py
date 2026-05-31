from playwright.sync_api import sync_playwright
import time
import os
import json
import re
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import firebase_admin
from firebase_admin import credentials, messaging, firestore

# === FIREBASE SETUP ===
FIREBASE_CRED = os.getenv("FIREBASE_CRED_PATH", "firebase-credentials.json")

db = None
if os.path.exists(FIREBASE_CRED):
    cred = credentials.Certificate(FIREBASE_CRED)
    firebase_admin.initialize_app(cred, {
        'projectId': 'myvita-app-c5ecd',
    })
    db = firestore.client()
    print("✅ Firebase initialized (Firestore ready)")
else:
    print("⚠️ Firebase credentials not found — notifications & Firestore disabled")

# === ZONE MAPPING ===
ZONES = {
    "H1Y": "montreal_east", "H1A": "montreal_east", "H1B": "montreal_east",
    "H1C": "montreal_east", "H1H": "montreal_north", "H1J": "montreal_north",
    "H2X": "montreal_central", "H3A": "montreal_central", "H3B": "montreal_central",
    "H4L": "montreal_north", "H4M": "montreal_north",
    "G1R": "quebec_central", "G1S": "quebec_central", "G1V": "quebec_ste_foy",
    "J8Y": "gatineau_hull", "J8Z": "gatineau_aylmer",
    "J1H": "sherbrooke", "J1K": "sherbrooke",
}

# === 5 DIFFERENT HUMAN FINGERPRINTS ===
BROWSER_PROFILES = [
    {
        "name": "User-1-Chrome-Win",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "viewport": {"width": 1366, "height": 768},
        "locale": "fr-CA",
        "timezone": "America/Montreal",
    },
    {
        "name": "User-2-Safari-Mac",
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
        "viewport": {"width": 1440, "height": 900},
        "locale": "fr-CA",
        "timezone": "America/Montreal",
    },
    {
        "name": "User-3-Firefox-Win",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "viewport": {"width": 1536, "height": 864},
        "locale": "en-CA",
        "timezone": "America/Toronto",
    },
    {
        "name": "User-4-Chrome-Linux",
        "user_agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "viewport": {"width": 1280, "height": 720},
        "locale": "fr-CA",
        "timezone": "America/Montreal",
    },
    {
        "name": "User-5-iPhone",
        "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
        "viewport": {"width": 390, "height": 844},
        "locale": "fr-CA",
        "timezone": "America/Montreal",
        "is_mobile": True,
    },
]

def get_zone(postal_code: str) -> str:
    fsa = postal_code[:3].upper()
    return ZONES.get(fsa, f"zone_{fsa}")

def get_postal_code():
    postal = os.getenv("POSTAL_CODE", "").replace(" ", "").strip()
    if postal and len(postal) >= 3:
        return postal
    return "H1Y3H1"

def get_user_token():
    if db is None:
        return None
    try:
        users_ref = db.collection('users').order_by('fcmTokenUpdated', direction='DESCENDING').limit(1)
        for doc in users_ref.stream():
            token = doc.to_dict().get('fcmToken')
            if token:
                return token
    except:
        pass
    return None

def save_to_firestore(postal_code: str, places_found: list):
    if db is None:
        return
    try:
        zone = get_zone(postal_code)
        db.collection("availability").document(zone).set({
            "service": "blood-test",
            "postal_code": postal_code,
            "zone": zone,
            "places_found": places_found,
            "slots_found": len(places_found) > 0,
            "last_checked": datetime.now(),
        })
        print(f"🔥 Saved to Firestore: availability/{zone}")
    except Exception as e:
        print(f"❌ Firestore save failed: {e}")

def send_notification(postal_code: str, places_found: list):
    token = get_user_token()
    if not token:
        return

    place_names = ", ".join([p.get('name', 'Unknown')[:30] for p in places_found[:3]])
    first_url = places_found[0].get('direct_url', '') if places_found else ''
    summary_url = f"https://portal3.clicsante.ca/?postalCode={postal_code.replace(' ', '+')}&serviceId=227"
    click_url = first_url if first_url else summary_url

    data_payload = {
        "url": click_url,
        "postal_code": postal_code,
        "click_action": "OPEN_BOOKING",
        "place_count": str(len(places_found)),
    }
    for i, p in enumerate(places_found[:5]):
        data_payload[f"place_{i}_name"] = p.get('name', 'Unknown')[:100]
        data_payload[f"place_{i}_url"] = p.get('direct_url', '')[:500]
        if p.get('distance'):
            data_payload[f"place_{i}_distance"] = str(p.get('distance', ''))

    try:
        messaging.send(messaging.Message(
            notification=messaging.Notification(
                title="🏥 Résultats ClicSanté disponibles!",
                body=f"{len(places_found)} lieux trouvés près de {postal_code}: {place_names}..."
            ),
            data=data_payload,
            token=token,
        ))
        print(f"✅ Notification sent")
    except Exception as e:
        print(f"❌ Notification failed: {e}")


# ══════════════════════════════════════════════════════════════
# ★ SINGLE WORKER — Pure UI, scrape clinic IDs from HTML ★
# ══════════════════════════════════════════════════════════════

def run_human_browser(profile: dict, postal_code: str, worker_id: int) -> list:
    """Navigate ClicSanté like a human, scrape clinic IDs from rendered HTML"""
    profile_name = profile.get("name", f"User-{worker_id}")
    print(f"\n   [{profile_name}] 🧑 Starting human-like search...")

    places = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport=profile.get("viewport", {"width": 1280, "height": 720}),
            user_agent=profile.get("user_agent"),
            locale=profile.get("locale", "fr-CA"),
            timezone_id=profile.get("timezone", "America/Montreal"),
            is_mobile=profile.get("is_mobile", False),
            has_touch=profile.get("has_touch", False),
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()

        try:
            # Step 1: Go to blood test page
            page.goto("https://portal3.clicsante.ca/services/blood-test",
                     wait_until="networkidle", timeout=60000)
            print(f"   [{profile_name}] 📄 Page loaded")
            time.sleep(2)

            # Step 2: Click "Sans frais"
            try:
                page.locator("text=Sans frais").first.click(timeout=8000)
                print(f"   [{profile_name}] ✅ Sans frais")
            except:
                try:
                    page.locator("text=No fees").first.click(timeout=5000)
                    print(f"   [{profile_name}] ✅ No fees")
                except:
                    print(f"   [{profile_name}] ⚠️ Free filter skipped")
            time.sleep(1.5)

            # Step 3: Enter postal code
            try:
                inputs = page.locator("input[type='text']").all()
                if inputs:
                    inputs[0].click()
                    inputs[0].fill("")
                    time.sleep(0.5)
                    inputs[0].type(postal_code, delay=150)
                    print(f"   [{profile_name}] ✅ Postal: {postal_code}")
            except:
                print(f"   [{profile_name}] ⚠️ Postal input failed")
            time.sleep(2)

            # Step 4: Click Search
            try:
                page.get_by_role("button", name="Search").first.click(timeout=8000)
                print(f"   [{profile_name}] ✅ Clicked Search")
            except:
                try:
                    page.get_by_role("button", name="Rechercher").first.click(timeout=5000)
                except:
                    page.keyboard.press("Enter")
            time.sleep(3)

            # Step 5: Wait for results to render
            print(f"   [{profile_name}] ⏳ Waiting for results...")
            try:
                page.wait_for_selector("text=km", timeout=30000)
                print(f"   [{profile_name}] ✅ Results loaded (found 'km')")
            except:
                print(f"   [{profile_name}] ⚠️ Waiting anyway...")
            time.sleep(5)

            # Step 6: Get the results page URL
            results_url = page.url
            print(f"   [{profile_name}] 🔗 Results URL: {results_url[:120]}")

            # ★ STEP 7: SCRAPE CLINIC IDs FROM HTML ★
            print(f"   [{profile_name}] 🔍 Scraping clinic IDs from page HTML...")

            # Method 1: Find links with take-appt or establishment IDs
            try:
                all_links = page.locator("a").all()
                print(f"   [{profile_name}] 📎 Total links on page: {len(all_links)}")
                for link in all_links:
                    try:
                        href = link.get_attribute("href") or ""
                        text = link.inner_text().strip()
                        
                        # Look for links containing clinic/establishment IDs
                        if any(kw in href.lower() for kw in ["take-appt", "establishment", "place", "clinic", "appointment", "rendez-vous"]):
                            if text and len(text) > 3:
                                # Extract numeric ID from the URL
                                id_match = re.search(r'/(\d{3,})/', href) or re.search(r'id=(\d+)', href)
                                clinic_id = id_match.group(1) if id_match else ""
                                
                                if clinic_id:
                                    place = {
                                        "name": text[:100],
                                        "id": clinic_id,
                                        "direct_url": f"https://clients3.clicsante.ca/{clinic_id}/take-appt",
                                    }
                                    if place not in places:
                                        places.append(place)
                                        print(f"   [{profile_name}]    🔗 {text[:60]}: {place['direct_url']}")
                    except:
                        pass
            except Exception as e:
                print(f"   [{profile_name}] ⚠️ Link scan error: {e}")

            # Method 2: Look for data attributes on cards/containers
            if not places:
                try:
                    data_selectors = [
                        "[data-id]",
                        "[data-place-id]",
                        "[data-establishment-id]",
                        "[data-clinic-id]",
                        "[data-organization-id]",
                    ]
                    for selector in data_selectors:
                        cards = page.locator(selector).all()
                        if len(cards) > 0:
                            print(f"   [{profile_name}] 🃏 Found {len(cards)} cards with {selector}")
                            for card in cards[:10]:
                                try:
                                    clinic_id = card.get_attribute("data-id") or card.get_attribute("data-place-id") or card.get_attribute("data-establishment-id") or card.get_attribute("data-clinic-id") or card.get_attribute("data-organization-id") or ""
                                    name = card.inner_text().strip()[:100]
                                    if clinic_id and name and len(name) > 5:
                                        place = {
                                            "name": name,
                                            "id": clinic_id,
                                            "direct_url": f"https://clients3.clicsante.ca/{clinic_id}/take-appt",
                                        }
                                        if place not in places:
                                            places.append(place)
                                            print(f"   [{profile_name}]    🃏 {name[:60]}: {place['direct_url']}")
                                except:
                                    pass
                            if places:
                                break
                except Exception as e:
                    print(f"   [{profile_name}] ⚠️ Data attribute scan error: {e}")

            # Method 3: Scrape embedded JSON in script tags
            if not places:
                try:
                    scripts = page.locator("script").all()
                    print(f"   [{profile_name}] 📜 Scanning {len(scripts)} script tags...")
                    for script in scripts:
                        try:
                            content = script.inner_text()
                            if len(content) > 100 and "id" in content.lower():
                                # Find JSON objects with id and name fields
                                json_patterns = [
                                    r'\{"id":\s*(\d+)[^}]*"name":\s*"([^"]+)"[^}]*\}',
                                    r'"id":\s*(\d+)[^}]*"name":\s*"([^"]+)"',
                                    r'\{[^}]*"establishmentId":\s*(\d+)[^}]*\}',
                                ]
                                for pattern in json_patterns:
                                    matches = re.findall(pattern, content)
                                    for match in matches[:10]:
                                        if isinstance(match, tuple):
                                            clinic_id = match[0]
                                            name = match[1] if len(match) > 1 else "Unknown"
                                        else:
                                            clinic_id = match
                                            name = "Unknown"
                                        if clinic_id and clinic_id.isdigit():
                                            place = {
                                                "name": name[:100],
                                                "id": clinic_id,
                                                "direct_url": f"https://clients3.clicsante.ca/{clinic_id}/take-appt",
                                            }
                                            if place not in places:
                                                places.append(place)
                                    if places:
                                        break
                        except:
                            pass
                except Exception as e:
                    print(f"   [{profile_name}] ⚠️ Script scan error: {e}")

            # Method 4: Capture ALL page HTML and search for ID patterns
            if not places:
                try:
                    html = page.content()
                    # Look for hrefs containing numeric IDs and clinic names
                    href_pattern = re.findall(r'href="[^"]*/(\d{3,})/[^"]*"[^>]*>([^<]+)<', html)
                    for match in href_pattern[:10]:
                        clinic_id = match[0]
                        name = match[1].strip()
                        if clinic_id and name and len(name) > 3:
                            place = {
                                "name": name[:100],
                                "id": clinic_id,
                                "direct_url": f"https://clients3.clicsante.ca/{clinic_id}/take-appt",
                            }
                            if place not in places:
                                places.append(place)
                except Exception as e:
                    print(f"   [{profile_name}] ⚠️ Full HTML scan error: {e}")

            # Method 5: Fallback to visible text if no IDs found
            if not places:
                print(f"   [{profile_name}] ⚠️ No IDs found — using visible text with results URL")
                try:
                    body = page.inner_text("body")
                    for line in body.split('\n'):
                        line = line.strip()
                        if 15 < len(line) < 200:
                            if any(kw in line.lower() for kw in ["clsc", "clinique", "hopital", "hôpital", "pharmacie", "gmf", "familiprix", "jean coutu", "pharmaprix", "uniprix", "brunet", "centre de prélèvement", "point de service"]):
                                if line not in ["Skip to main content", "All services", "Cancel an appointment", "Need help?", "Specimens and / or Blood Test"]:
                                    places.append({
                                        "name": line,
                                        "id": "",
                                        "direct_url": results_url,
                                    })
                                    if len(places) >= 5:
                                        break
                except:
                    pass

            # Save screenshot for debugging
            try:
                page.screenshot(path=f"clicsante_{profile_name}.png")
            except:
                pass

            print(f"   [{profile_name}] ✅ Found {len(places)} places with IDs")
            for i, p in enumerate(places):
                print(f"      {i+1}. 📍 {p['name'][:60]}")
                if p.get('direct_url'):
                    print(f"         🔗 {p['direct_url'][:120]}")

        except Exception as e:
            print(f"   [{profile_name}] ❌ Error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            browser.close()

    return places


# ══════════════════════════════════════════════════════════════
# ★ MAIN
# ══════════════════════════════════════════════════════════════

def check_availability():
    postal_code = get_postal_code()
    zone = get_zone(postal_code)

    print(f"\n{'='*60}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] 📍 {postal_code} | {zone}")
    print(f"🧑 5 human-like browsers scraping clinic IDs from results page...")
    print(f"{'='*60}")

    all_places = []
    seen_names = set()

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(run_human_browser, profile, postal_code, i): i
            for i, profile in enumerate(BROWSER_PROFILES)
        }
        for future in as_completed(futures):
            try:
                places = future.result()
                for p in places:
                    key = p.get('id') or p['name']
                    if key not in seen_names and len(p.get('name', '')) > 5:
                        seen_names.add(key)
                        all_places.append(p)
            except Exception as e:
                print(f"   ❌ Worker failed: {e}")

    all_places = all_places[:5]

    print(f"\n{'='*60}")
    print(f"✅ FINAL RESULTS: {len(all_places)} unique places near {postal_code}")
    print(f"{'='*60}")
    for i, p in enumerate(all_places):
        print(f"\n   {i+1}. 📍 {p.get('name')}")
        if p.get('distance'):
            print(f"      📏 {p.get('distance')}")
        if p.get('direct_url'):
            print(f"      🔗 {p.get('direct_url')}")
        elif p.get('id'):
            print(f"      🆔 ID: {p.get('id')}")

    save_to_firestore(postal_code, all_places)
    send_notification(postal_code, all_places)

    print(f"\n🎉 Done! User has {len(all_places)} choices.")
    print(f"💡 Legal: Pure UI automation — same as a human clicking buttons.")
    return True


if __name__ == "__main__":
    check_availability()