from playwright.sync_api import sync_playwright, TimeoutError
import time
import os
import json
import requests
from datetime import datetime
from firebase_admin import credentials, messaging, initialize_app, firestore

# === FIREBASE SETUP ===
FIREBASE_CRED = os.getenv("FIREBASE_CRED_PATH", "firebase-credentials.json")

db = None  # Firestore client
if os.path.exists(FIREBASE_CRED):
    cred = credentials.Certificate(FIREBASE_CRED)
    initialize_app(cred)
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
    postal = os.getenv("POSTAL_CODE", "").replace(" ", "")
    if postal:
        return postal
    if os.path.exists("queue.json"):
        with open("queue.json", "r") as f:
            queue = json.load(f)
        if queue:
            return queue[0].get("postal_code", "H1Y3H1")
    return "H1Y3H1"

def get_service_url():
    service = os.getenv("SERVICE", "blood-test")
    return f"https://portal3.clicsante.ca/services/{service}"

def get_user_token():
    token_file = "user_fcm_token.txt"
    if os.path.exists(token_file):
        with open(token_file, "r") as f:
            return f.read().strip()
    return None

def save_to_firestore(postal_code: str, has_slots: bool, booking_url: str = ""):
    """Save availability to Firestore so the app can read it"""
    if db is None:
        print("⚠️ Firestore not available — skipping")
        return
    
    zone = get_zone(postal_code)
    now = datetime.now().isoformat()
    
    data = {
        "service": os.getenv("SERVICE", "blood-test"),
        "postal_code": postal_code,
        "zone": zone,
        "slots_found": has_slots,
        "booking_url": booking_url,
        "last_checked": now,
    }
    
    try:
        # Save to Firestore collection
        db.collection("availability").document(zone).set(data)
        print(f"🔥 Saved to Firestore: availability/{zone}")
        
        # Also add to history subcollection
        db.collection("availability").document(zone).collection("history").add({
            "slots_found": has_slots,
            "booking_url": booking_url,
            "checked_at": now,
        })
        
    except Exception as e:
        print(f"❌ Firestore save failed: {e}")

def send_firebase_notification(postal_code: str, booking_url: str, slots_found: bool):
    token = get_user_token()
    if not token:
        print("⚠️ No FCM token found — skipping notification")
        return
    
    if slots_found:
        title = "🎉 Appointment Slot Found!"
        body = f"Slots available near {postal_code}. Tap to book now!"
    else:
        title = "🔍 Still Searching"
        body = f"No slots near {postal_code} yet. Will check again."
    
    try:
        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={
                "url": booking_url,
                "postal_code": postal_code,
                "slots_found": str(slots_found).lower()
            },
            token=token,
        )
        response = messaging.send(message)
        print(f"✅ Firebase notification sent: {response}")
    except Exception as e:
        print(f"❌ Firebase notification failed: {e}")

def save_availability(postal_code: str, has_slots: bool, booking_url: str = ""):
    """Save availability to both JSON and Firestore"""
    # Save to Firestore
    save_to_firestore(postal_code, has_slots, booking_url)
    
    # Save to local JSON as backup
    data = {
        "postal_code": postal_code,
        "has_slots": has_slots,
        "booking_url": booking_url,
        "zone": get_zone(postal_code),
        "checked_at": datetime.now().isoformat()
    }
    status_file = f"availability_{get_zone(postal_code)}.json"
    with open(status_file, "w") as f:
        json.dump(data, f, indent=2)
    print(f"💾 Saved to {status_file}")

def check_availability():
    postal_code = get_postal_code()
    zone = get_zone(postal_code)
    service_url = get_service_url()
    
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}]")
    print(f"📍 Postal: {postal_code} | Zone: {zone}")
    print(f"🔗 Service: {service_url}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        try:
            page.goto(service_url, wait_until="networkidle", timeout=30000)
            
            try:
                page.get_by_label("No fees").check(timeout=5000)
            except:
                try:
                    page.locator("text=Sans frais").click(timeout=5000)
                except:
                    pass
            
            try:
                postal_input = page.get_by_placeholder("ex. A1A 1A1")
                if postal_input.count() == 0:
                    postal_input = page.locator("input[type='text']").first
                postal_input.fill(postal_code)
            except:
                print("⚠️ Could not find postal input")
            
            try:
                page.get_by_role("button", name="Search").click(timeout=8000)
            except:
                try:
                    page.get_by_role("button", name="Rechercher").click(timeout=8000)
                except:
                    page.locator("button:has-text('Search')").click(timeout=5000)
            
            page.wait_for_timeout(12000)
            
            page_text = page.inner_text("body").lower()
            has_slots = any(word in page_text for word in [
                "disponible", "available", "réservation", "book",
                "places", "choisir", "prochain", "appointment", "rendez-vous"
            ])
            
            current_url = page.url
            
            if has_slots:
                print(f"🎉 SLOTS FOUND in zone {zone}!")
                send_firebase_notification(postal_code, current_url, True)
                save_availability(postal_code, True, current_url)
                return True
            else:
                print(f"❌ No slots in zone {zone}.")
                save_availability(postal_code, False)
                return False
                
        except Exception as e:
            print(f"❌ Error: {e}")
            return False
        finally:
            browser.close()

if __name__ == "__main__":
    check_availability()
