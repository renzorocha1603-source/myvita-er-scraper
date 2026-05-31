from playwright.sync_api import sync_playwright
import time
import os
import json
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

# === ZONE MAPPING (FSA → Zone) ===
ZONES = {
    "H1Y": "montreal_east", "H1A": "montreal_east", "H1B": "montreal_east",
    "H1C": "montreal_east", "H1H": "montreal_north", "H1J": "montreal_north",
    "H2X": "montreal_central", "H3A": "montreal_central", "H3B": "montreal_central",
    "H4L": "montreal_north", "H4M": "montreal_north",
    "G1R": "quebec_central", "G1S": "quebec_central", "G1V": "quebec_ste_foy",
    "J8Y": "gatineau_hull", "J8Z": "gatineau_aylmer",
    "J1H": "sherbrooke", "J1K": "sherbrooke",
}

# === 5 DIFFERENT BROWSER FINGERPRINTS ===
BROWSER_PROFILES = [
    {
        "name": "Windows Chrome",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "viewport": {"width": 1366, "height": 768},
        "locale": "en-CA",
        "timezone": "America/Toronto",
    },
    {
        "name": "Mac Safari",
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
        "viewport": {"width": 1440, "height": 900},
        "locale": "fr-CA",
        "timezone": "America/Montreal",
    },
    {
        "name": "Windows Firefox",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "viewport": {"width": 1536, "height": 864},
        "locale": "en-US",
        "timezone": "America/Toronto",
    },
    {
        "name": "Linux Chrome",
        "user_agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "viewport": {"width": 1280, "height": 720},
        "locale": "fr-FR",
        "timezone": "America/Montreal",
    },
    {
        "name": "iPhone Safari",
        "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
        "viewport": {"width": 390, "height": 844},
        "locale": "en-CA",
        "timezone": "America/Toronto",
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
    if os.path.exists("queue.json"):
        with open("queue.json", "r") as f:
            queue = json.load(f)
        if queue:
            return queue[0].get("postal_code", "H1Y3H1")
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
    """Save search results to Firestore"""
    if db is None:
        print("⚠️ Firestore not available — skipping")
        return

    zone = get_zone(postal_code)
    now = datetime.now()

    data = {
        "service": "blood-test",
        "postal_code": postal_code,
        "zone": zone,
        "places_found": places_found,
        "slots_found": len(places_found) > 0,
        "last_checked": now,
    }

    try:
        db.collection("availability").document(zone).set(data)
        print(f"🔥 Saved to Firestore: availability/{zone}")
    except Exception as e:
        print(f"❌ Firestore save failed: {e}")

def send_notification(postal_code: str, places_found: list):
    """Send push notification with top 5 choices and direct booking links"""
    token = get_user_token()
    if not token:
        print("⚠️ No FCM token found — skipping notification")
        return

    place_names = ", ".join([p.get('name', 'Unknown')[:30] for p in places_found[:3]])

    first_url = places_found[0].get('direct_url', '') if places_found else ''
    summary_url = f"https://portal3.clicsante.ca/?postalCode={postal_code.replace(' ', '+')}&serviceId=227"
    click_url = first_url if first_url else summary_url

    title = "🏥 Résultats ClicSanté disponibles!"
    body = f"{len(places_found)} lieux trouvés près de {postal_code}: {place_names}..."

    data_payload = {
        "url": click_url,
        "postal_code": postal_code,
        "click_action": "OPEN_BOOKING",
        "place_count": str(len(places_found)),
    }

    for i, p in enumerate(places_found[:5]):
        data_payload[f"place_{i}_name"] = p.get('name', 'Unknown')[:100]
        data_payload[f"place_{i}_url"] = p.get('direct_url', '')[:500]
        if p.get('address'):
            data_payload[f"place_{i}_address"] = p.get('address', '')[:200]
        if p.get('distance'):
            data_payload[f"place_{i}_distance"] = str(p.get('distance', ''))

    try:
        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data=data_payload,
            token=token,
        )
        response = messaging.send(message)
        print(f"✅ Notification sent with {len(places_found)} places: {response}")
    except Exception as e:
        print(f"❌ Notification failed: {e}")


# ══════════════════════════════════════════════════════════════
# ★ SINGLE BROWSER WORKER — Runs one profile
# ══════════════════════════════════════════════════════════════

def run_browser_worker(profile: dict, postal_code: str, worker_id: int) -> list:
    """Run one browser profile and return places found"""
    profile_name = profile.get("name", f"Worker-{worker_id}")
    print(f"\n   [{profile_name}] 🚀 Starting...")

    api_response_data = []
    places = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        context_options = {
            "viewport": profile.get("viewport", {"width": 1280, "height": 720}),
            "user_agent": profile.get("user_agent"),
            "locale": profile.get("locale", "en-CA"),
            "timezone_id": profile.get("timezone", "America/Toronto"),
        }

        if profile.get("is_mobile"):
            context_options["is_mobile"] = True
            context_options["has_touch"] = True

        context = browser.new_context(**context_options)
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        page = context.new_page()

        # Intercept API responses
        def handle_response(response):
            if response.status == 200:
                url = response.url
                if "availabilitiesByGeolocalisation" in url:
                    try:
                        ct = response.headers.get('content-type', '')
                        if 'json' in ct:
                            data = response.json()
                            api_response_data.append({"url": url, "data": data})
                            print(f"   [{profile_name}] 📡 API intercepted!")
                    except:
                        pass

        page.on("response", handle_response)

        try:
            # Load page
            page.goto("https://portal3.clicsante.ca/services/blood-test",
                     wait_until="networkidle", timeout=60000)
            print(f"   [{profile_name}] 📄 Page loaded")
            time.sleep(2)

            # Select "No fees"
            try:
                page.locator("input[type='radio']").first.click(timeout=5000)
                print(f"   [{profile_name}] ✅ Free filter")
            except:
                try:
                    page.locator("text=No fees").first.click(timeout=3000)
                except:
                    try:
                        page.locator("text=Sans frais").first.click(timeout=3000)
                    except:
                        pass

            time.sleep(1)

            # Enter postal code
            try:
                postal_input = page.locator("input[placeholder*='A1A'], input[placeholder*='postal'], input[type='text']").first
                postal_input.click()
                postal_input.fill("")
                time.sleep(0.3)
                postal_input.type(postal_code, delay=100)
                print(f"   [{profile_name}] ✅ Postal: {postal_code}")
            except:
                print(f"   [{profile_name}] ⚠️ Postal input failed")

            time.sleep(1.5)

            # Click Search
            for btn_text in ["Search", "Rechercher", "Chercher"]:
                try:
                    page.get_by_role("button", name=btn_text).first.click(timeout=5000)
                    print(f"   [{profile_name}] ✅ Clicked '{btn_text}'")
                    break
                except:
                    continue
            else:
                page.keyboard.press("Enter")

            # ★ WAIT — keep page alive for 15 minutes, break when API arrives ★
            print(f"   [{profile_name}] ⏳ Keeping page alive (15 min max, breaks on API)...")
            total_wait = 0
            while total_wait < 900:
                time.sleep(30)
                total_wait += 30
                if api_response_data:
                    print(f"   [{profile_name}] ✅ API arrived at {total_wait}s ({total_wait//60} min)!")
                    break
                if total_wait % 120 == 0:
                    print(f"   [{profile_name}] Still waiting... ({total_wait//60} min)")

            # ★ STEP 1: Extract page text FIRST (fast, always works) ★
            page_places = []
            print(f"   [{profile_name}] 📄 Extracting page text as backup...")
            try:
                body_text = page.inner_text("body")
                lines = body_text.split('\n')
                for line in lines:
                    line = line.strip()
                    if line and len(line) > 10 and len(line) < 200:
                        if any(kw in line.lower() for kw in ["clsc", "clinique", "hopital", "hôpital", "pharmacie", "gmf", "km", "laboratoire"]):
                            skip_words = ["Skip to main content", "All services", "Cancel an appointment", "Need help?", "Specimens", "Blood Test"]
                            if not any(sw in line for sw in skip_words):
                                page_places.append({"name": line, "id": "", "direct_url": "", "address": "", "distance": ""})
                                if len(page_places) >= 5:
                                    break
            except:
                pass

            # ★ STEP 2: Check if API arrived (even during page extraction) ★
            if api_response_data:
                print(f"   [{profile_name}] 📡 API data arrived! Processing...")

                # Save full response for debugging
                try:
                    with open(f"clicsante_api_{profile_name.replace(' ', '_')}.json", "w", encoding="utf-8") as f:
                        json.dump(api_response_data[0]['data'], f, indent=2, ensure_ascii=False)
                    print(f"   [{profile_name}] 📁 API saved to file")
                except:
                    pass

                data = api_response_data[0]['data']

                if isinstance(data, dict):
                    print(f"   [{profile_name}] 📦 Response keys: {list(data.keys())}")

                    for key, val in data.items():
                        if isinstance(val, list) and len(val) > 0:
                            print(f"   [{profile_name}] 📦 '{key}' has {len(val)} items")

                            if isinstance(val[0], dict):
                                first_item = val[0]
                                print(f"   [{profile_name}] 📦 Item keys: {list(first_item.keys())}")
                                print(f"   [{profile_name}] 📦 First item:")
                                print(json.dumps(first_item, indent=2, ensure_ascii=False)[:3000])

                            # Extract places from API
                            for item in val[:5]:
                                if isinstance(item, dict):
                                    place_id = str(
                                        item.get('id') or
                                        item.get('placeId') or
                                        item.get('establishmentId') or
                                        item.get('estId') or
                                        item.get('clinicId') or
                                        item.get('organizationId') or
                                        ''
                                    )
                                    name = str(
                                        item.get('name') or
                                        item.get('placeName') or
                                        item.get('establishmentName') or
                                        item.get('title') or
                                        item.get('label') or
                                        'Unknown'
                                    )

                                    api_place = {
                                        'name': name,
                                        'id': place_id,
                                        'address': str(item.get('address') or item.get('location') or item.get('fullAddress') or ''),
                                        'distance': '',
                                        'phone': str(item.get('phone') or item.get('phoneNumber') or ''),
                                    }

                                    raw_dist = item.get('distance') or item.get('distanceKm') or item.get('dist') or ''
                                    if raw_dist:
                                        try:
                                            api_place['distance'] = f"{float(raw_dist):.1f} km"
                                        except:
                                            api_place['distance'] = str(raw_dist)

                                    if api_place['id']:
                                        api_place['direct_url'] = f"https://clients3.clicsante.ca/{api_place['id']}/take-appt"

                                    if name and name != 'Unknown' and len(name) > 3:
                                        places.append(api_place)
                            break  # Only process first array

            # ★ STEP 3: Use page text only if API gave nothing ★
            if not places:
                print(f"   [{profile_name}] ⚠️ Using page text results ({len(page_places)} places)")
                places = page_places
            else:
                print(f"   [{profile_name}] ✅ Using API data ({len(places)} places)")

            # Save screenshot
            try:
                page.screenshot(path=f"clicsante_{profile_name.replace(' ', '_')}.png")
            except:
                pass

            print(f"   [{profile_name}] ✅ Final: {len(places)} places")
            for p in places:
                if p.get('direct_url'):
                    print(f"      🔗 {p['name'][:60]}: {p['direct_url']}")

        except Exception as e:
            print(f"   [{profile_name}] ❌ Error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            browser.close()

    return places


# ══════════════════════════════════════════════════════════════
# ★ MAIN — Run all 5 browsers in parallel
# ══════════════════════════════════════════════════════════════

def check_availability():
    postal_code = get_postal_code()
    zone = get_zone(postal_code)

    print(f"\n{'='*60}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] 📍 {postal_code} | {zone}")
    print(f"🚀 Launching {len(BROWSER_PROFILES)} parallel browsers...")
    print(f"⏱️  Max wait: 15 minutes per browser (breaks when API arrives)")
    print(f"{'='*60}")

    all_places = []
    seen_names = set()

    # Run all 5 browsers in parallel
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(run_browser_worker, profile, postal_code, i): i
            for i, profile in enumerate(BROWSER_PROFILES)
        }

        for future in as_completed(futures):
            worker_id = futures[future]
            try:
                places = future.result()
                for p in places:
                    if p['name'] not in seen_names and p['name'] != 'Unknown':
                        seen_names.add(p['name'])
                        all_places.append(p)
            except Exception as e:
                print(f"   [Worker-{worker_id}] ❌ Failed: {e}")

    # Deduplicate and limit to 5
    all_places = all_places[:5]

    # ★ RESULTS ★
    print(f"\n{'='*60}")
    print(f"✅ FINAL RESULTS: {len(all_places)} unique places near {postal_code}")
    print(f"{'='*60}")
    for i, p in enumerate(all_places):
        print(f"\n   {i+1}. 📍 {p.get('name')}")
        if p.get('address'):
            print(f"      📫 {p.get('address')}")
        if p.get('distance'):
            print(f"      📏 {p.get('distance')}")
        if p.get('phone'):
            print(f"      📞 {p.get('phone')}")
        if p.get('direct_url'):
            print(f"      🔗 {p.get('direct_url')}")
        elif p.get('id'):
            print(f"      🆔 ID: {p.get('id')}")

    # Save & notify
    save_to_firestore(postal_code, all_places)
    send_notification(postal_code, all_places)

    print(f"\n🎉 Done! User has {len(all_places)} unique choices.")
    return True


if __name__ == "__main__":
    check_availability()