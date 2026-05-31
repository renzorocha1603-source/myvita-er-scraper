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

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        try:
            # Load ClicSanté blood test page
            page.goto("https://portal3.clicsante.ca/services/blood-test", 
                     wait_until="networkidle", timeout=60000)
            print(f"📄 Page loaded: {page.url}")

            # Select "No fees" / "Sans frais"
            try:
                page.locator("input[type='radio']").first.click(timeout=5000)
                print("✅ Selected free filter")
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

            # Enter Postal Code
            try:
                postal_input = page.locator("input[placeholder*='A1A'], input[placeholder*='postal'], input[type='text']").first
                postal_input.fill(postal_code)
                print(f"✅ Postal code entered: {postal_code}")
            except:
                print("⚠️ Could not find postal input")
            
            time.sleep(1)

            # Click Search
            try:
                for btn_text in ["Search", "Rechercher", "Chercher"]:
                    try:
                        page.get_by_role("button", name=btn_text).first.click(timeout=5000)
                        print(f"✅ Clicked '{btn_text}'")
                        break
                    except:
                        continue
            except:
                page.keyboard.press("Enter")
                print("✅ Pressed Enter to search")

            # Wait for results page to load
            print("⏳ Waiting for results...")
            time.sleep(8)

            # ★ THE RESULTS URL IS THE GOLD ★
            # This URL already has all parameters and takes the user
            # directly to the results page where they can press Search
            results_url = page.url
            print(f"📍 Results URL: {results_url}")

            # Try to extract clinic names from the results page
            places_found = []
            try:
                # Look for establishment names
                name_selectors = [
                    ".establishment-card", 
                    "[class*='clinic']", 
                    "[class*='establishment']",
                    "article h3",
                    "article h2",
                    ".card-title"
                ]
                for selector in name_selectors:
                    try:
                        elements = page.locator(selector).all()
                        for el in elements[:5]:
                            name = el.inner_text().strip()
                            if name and len(name) > 2:
                                places_found.append({"name": name})
                    except:
                        continue
                
                if not places_found:
                    # Fallback: grab any meaningful text blocks
                    body = page.inner_text("body")
                    lines = [l.strip() for l in body.split('\n') if l.strip() and len(l.strip()) > 5]
                    places_found = [{"name": l} for l in lines[:5]]
                    
            except Exception as e:
                print(f"⚠️ Could not extract names: {e}")
                places_found = [{"name": f"Results near {postal_code}"}]

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
