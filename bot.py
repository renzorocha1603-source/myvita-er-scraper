from playwright.sync_api import sync_playwright
import time
import os
import json
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

def save_to_firestore(postal_code: str, places_found: list):
    """Save search results to Firestore"""
    if db is None:
        print("⚠️ Firestore not available — skipping")
        return
    
    zone = get_zone(postal_code)
    now = datetime.now()
    
    # Build a summary URL for the notification
    summary_url = f"https://portal3.clicsante.ca/?postalCode={postal_code.replace(' ', '+')}&serviceId=227"
    
    data = {
        "service": "blood-test",
        "postal_code": postal_code,
        "zone": zone,
        "places_found": places_found,
        "results_url": summary_url,
        "slots_found": len(places_found) > 0,
        "last_checked": now,
    }
    
    try:
        db.collection("availability").document(zone).set(data)
        print(f"🔥 Saved to Firestore: availability/{zone}")
    except Exception as e:
        print(f"❌ Firestore save failed: {e}")

def send_notification(postal_code: str, places_found: list):
    """Send push notification with the top places"""
    token = get_user_token()
    if not token:
        print("⚠️ No FCM token found — skipping notification")
        return
    
    place_names = ", ".join([p.get('name', 'Unknown')[:40] for p in places_found[:3]])
    summary_url = f"https://portal3.clicsante.ca/?postalCode={postal_code.replace(' ', '+')}&serviceId=227"
    
    title = "🏥 Résultats ClicSanté disponibles!"
    body = f"{len(places_found)} lieux trouvés: {place_names}..."
    
    try:
        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={
                "url": summary_url,
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
                if "availabilitiesByGeolocalisation" in url:
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
            print(f"📄 Page loaded")
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

            # Click Search
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
                except:
                    pass

            # ★ WAIT FOR API RESPONSE ★
            print("⏳ Waiting for API response...")
            time.sleep(8)

            # ★ PARSE API DATA TO EXTRACT CLINIC INFO ★
            places_found = []

            if api_response_data:
                print(f"\n📡 Captured {len(api_response_data)} API responses")
                data = api_response_data[0]['data']
                
                # Debug: show structure
                print(f"\n📦 API RESPONSE KEYS: {list(data.keys()) if isinstance(data, dict) else 'not a dict'}")
                
                # Find the array with clinic data
                if isinstance(data, dict):
                    for key, val in data.items():
                        if isinstance(val, list) and len(val) > 0:
                            print(f"\n📦 '{key}' has {len(val)} items")
                            if isinstance(val[0], dict):
                                print(f"📦 First item keys: {list(val[0].keys())}")
                                print(f"📦 First item (full):")
                                print(json.dumps(val[0], indent=2)[:3000])
                            
                            # Extract places from this array
                            for item in val[:5]:
                                if isinstance(item, dict):
                                    place = {}
                                    
                                    # Try to find name
                                    place['name'] = item.get('name') or item.get('title') or item.get('label') or item.get('placeName') or item.get('establishmentName') or 'Unknown'
                                    
                                    # Try to find ID for direct URL
                                    place['id'] = item.get('id') or item.get('placeId') or item.get('establishmentId') or item.get('estId') or ''
                                    
                                    # Try to find address
                                    place['address'] = item.get('address') or item.get('location') or ''
                                    
                                    # Try to find distance
                                    place['distance'] = item.get('distance') or item.get('distanceKm') or ''
                                    
                                    # Try to find phone
                                    place['phone'] = item.get('phone') or item.get('phoneNumber') or ''
                                    
                                    # Build direct URL if we have an ID
                                    if place['id']:
                                        place['direct_url'] = f"https://clients3.clicsante.ca/{place['id']}/take-appt"
                                    
                                    places_found.append(place)
                            break  # Only process the first array found
            
            # Fallback: extract from page if API didn't work
            if not places_found:
                print("⚠️ No API data parsed — extracting from page")
                body_text = page.inner_text("body")
                lines = body_text.split('\n')
                for line in lines:
                    line = line.strip()
                    if line and len(line) > 10 and len(line) < 150:
                        if any(kw in line.lower() for kw in ["clsc", "clinique", "hopital", "hôpital", "pharmacie", "gmf", "km"]):
                            places_found.append({"name": line, "id": "", "direct_url": ""})
                            if len(places_found) >= 5:
                                break

            # ★ RESULTS ★
            print(f"\n✅ Found {len(places_found)} places:")
            for p in places_found:
                print(f"   📍 {p.get('name')}")
                if p.get('id'):
                    print(f"      🔗 {p.get('direct_url')}")
                if p.get('address'):
                    print(f"      📫 {p.get('address')}")
                if p.get('distance'):
                    print(f"      📏 {p.get('distance')}")

            # Save screenshot for debugging
            try:
                page.screenshot(path="clicsante_result.png")
                print("\n📸 Screenshot saved")
            except:
                pass

            # Save to Firestore & notify user
            save_to_firestore(postal_code, places_found)
            send_notification(postal_code, places_found)
            
            print(f"\n🎉 Done!")
            
            # ★ SAVE FULL API RESPONSE FOR DEBUGGING ★
            if api_response_data:
                with open("clicsante_api_response.json", "w") as f:
                    json.dump(api_response_data[0]['data'], f, indent=2)
                print("📁 Full API response saved to clicsante_api_response.json")
            
            return True

        except Exception as e:
            print(f"❌ Error: {e}")
            return False
        finally:
            browser.close()


if __name__ == "__main__":
    check_availability()
