from playwright.sync_api import sync_playwright
import time
import os
import json
import requests
from datetime import datetime
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

def save_to_firestore(postal_code: str, results_url: str, places_found: list):
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
        "results_url": results_url,
        "places_found": places_found,
        "slots_found": len(places_found) > 0,
        "last_checked": now,
    }
    
    try:
        db.collection("availability").document(zone).set(data)
        print(f"🔥 Saved to Firestore: availability/{zone}")
    except Exception as e:
        print(f"❌ Firestore save failed: {e}")

def send_notification(postal_code: str, results_url: str, places_found: list):
    """Send push notification with the results link"""
    token = get_user_token()
    if not token:
        print("⚠️ No FCM token found — skipping notification")
        return
    
    place_names = ", ".join([p.get('name', 'Unknown') for p in places_found[:3]])
    
    title = "🏥 Résultats disponibles!"
    body = f"{len(places_found)} lieux trouvés près de {postal_code}: {place_names}..."
    
    try:
        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={
                "url": results_url,
                "postal_code": postal_code,
                "click_action": "OPEN_BOOKING"
            },
            token=token,
        )
        response = messaging.send(message)
        print(f"✅ Notification sent: {response}")
    except Exception as e:
        print(f"❌ Notification failed: {e}")

def check_availability():
    postal_code = get_postal_code()
    zone = get_zone(postal_code)
    
    print(f"\n{'='*50}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] 📍 {postal_code} | {zone}")
    print(f"{'='*50}")

    # ★ Store intercepted API data
    api_response_data = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        # ★ INTERCEPT API RESPONSES ★
        def handle_response(response):
            if response.status == 200:
                url = response.url
                # Look for availability/places/search API calls
                if any(kw in url.lower() for kw in ["availabilities", "places", "establishments", "search", "place", "avail"]):
                    try:
                        ct = response.headers.get('content-type', '')
                        if 'json' in ct:
                            data = response.json()
                            api_response_data.append({"url": url, "data": data})
                            print(f"📡 Intercepted API: {url[:150]}")
                    except:
                        pass

        page.on("response", handle_response)

        try:
            # Load ClicSanté blood test page
            page.goto("https://portal3.clicsante.ca/services/blood-test", 
                     wait_until="networkidle", timeout=60000)
            print(f"📄 Page loaded: {page.url}")
            time.sleep(2)

            # Select "No fees" / "Sans frais"
            try:
                page.locator("input[type='radio']").first.click(timeout=5000)
                print("✅ Selected free filter (radio)")
            except:
                try:
                    page.locator("text=No fees").first.click(timeout=3000)
                    print("✅ Selected 'No fees'")
                except:
                    try:
                        page.locator("text=Sans frais").first.click(timeout=3000)
                        print("✅ Selected 'Sans frais'")
                    except:
                        print("⚠️ Could not find free filter")
            
            time.sleep(1)

            # Enter Postal Code
            try:
                postal_input = page.locator("input[placeholder*='A1A'], input[placeholder*='postal'], input[type='text']").first
                postal_input.click()
                postal_input.fill("")
                time.sleep(0.3)
                postal_input.type(postal_code, delay=100)
                print(f"✅ Postal code entered: {postal_code}")
            except:
                print("⚠️ Could not find postal input")
            
            time.sleep(1.5)

            # Click Search — try multiple approaches
            search_clicked = False
            for btn_text in ["Search", "Rechercher", "Chercher"]:
                try:
                    page.get_by_role("button", name=btn_text).first.click(timeout=5000)
                    print(f"✅ Clicked '{btn_text}' button")
                    search_clicked = True
                    break
                except:
                    continue
            
            if not search_clicked:
                try:
                    page.keyboard.press("Enter")
                    print("✅ Pressed Enter to search")
                    search_clicked = True
                except:
                    pass

            # ★ WAIT FOR DYNAMIC RESULTS ★
            print("⏳ Waiting for dynamic results...")
            time.sleep(8)

            # Check if results loaded on the page
            results_url = page.url
            print(f"📍 Current URL: {results_url}")

            # Try to find establishment cards or result items
            places_found = []
            results_loaded = False

            # Check multiple selectors that indicate results loaded
            result_selectors = [
                ".establishment-card",
                "[class*='establishment']",
                "[class*='result-item']",
                "[class*='place-card']",
                "[class*='clinic-card']",
                "article",
                ".list-item",
                "[data-testid*='result']",
                "[data-testid*='establishment']",
            ]

            for selector in result_selectors:
                try:
                    elements = page.locator(selector).all()
                    if len(elements) > 0:
                        print(f"✅ Found {len(elements)} elements with '{selector}'")
                        for el in elements[:10]:
                            try:
                                name = el.inner_text().strip()
                                if name and len(name) > 5 and name not in ["Skip to main content", "All services", "Cancel an appointment", "Need help?"]:
                                    places_found.append({"name": name[:100]})
                            except:
                                pass
                        if len(places_found) >= 3:
                            results_loaded = True
                            break
                except:
                    continue

            # If no structured elements found, check page text
            if not results_loaded:
                body_text = page.inner_text("body")
                print(f"📄 Body text length: {len(body_text)} chars")
                
                # Check for common result indicators
                result_keywords = ["km", "distance", "disponible", "available", "prochain", "next", "créneau", "creneau"]
                found_keywords = [kw for kw in result_keywords if kw in body_text.lower()]
                if found_keywords:
                    print(f"✅ Found result keywords: {found_keywords}")
                    results_loaded = True
                    # Extract lines that look like establishment names
                    lines = body_text.split('\n')
                    for line in lines:
                        line = line.strip()
                        if line and len(line) > 10 and len(line) < 150:
                            if any(c.isdigit() for c in line) or any(kw in line.lower() for kw in ["clsc", "clinique", "hopital", "hôpital", "pharmacie", "gmf"]):
                                places_found.append({"name": line})
                                if len(places_found) >= 5:
                                    break

            # ★ CHECK INTERCEPTED API DATA ★
            if api_response_data:
                print(f"📡 Captured {len(api_response_data)} API responses")
                for api in api_response_data:
                    print(f"   📡 {api['url'][:120]}")
                    # Try to extract place names from API data
                    data = api['data']
                    data_str = json.dumps(data)
                    # Look for place names in the API response
                    try:
                        if isinstance(data, dict):
                            # Try common key patterns
                            for key in ['places', 'results', 'establishments', 'items', 'data']:
                                items = data.get(key, [])
                                if isinstance(items, list):
                                    for item in items[:5]:
                                        if isinstance(item, dict):
                                            name = item.get('name') or item.get('title') or item.get('label')
                                            if name and name not in [p['name'] for p in places_found]:
                                                places_found.append({"name": str(name)})
                    except:
                        pass

            # ★ USE API URL IF FOUND ★
            if api_response_data:
                # The API URL often has the search parameters we need
                api_url = api_response_data[0]['url']
                print(f"🔗 Using API URL for results: {api_url[:150]}")
                results_url = api_url
            elif results_loaded:
                print(f"🔗 Using page URL for results")
            else:
                print("⚠️ No results detected")
                places_found = [{"name": f"Search near {postal_code} — tap to check ClicSanté"}]

            # Deduplicate
            seen = set()
            unique_places = []
            for p in places_found:
                if p['name'] not in seen:
                    seen.add(p['name'])
                    unique_places.append(p)
            places_found = unique_places[:5]

            print(f"✅ Found {len(places_found)} places")
            for p in places_found:
                print(f"   📍 {p.get('name')}")

            # Save screenshot for debugging
            try:
                page.screenshot(path="clicsante_result.png")
                print("📸 Screenshot saved")
            except:
                pass

            # Save to Firestore & notify user
            save_to_firestore(postal_code, results_url, places_found)
            send_notification(postal_code, results_url, places_found)
            
            print(f"🎉 Done! User can book at: {results_url}")
            return True

        except Exception as e:
            print(f"❌ Error: {e}")
            return False
        finally:
            browser.close()


if __name__ == "__main__":
    check_availability()
