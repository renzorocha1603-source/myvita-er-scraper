import json
import requests
from datetime import datetime

# We'll search known CLSCs for direct booking pages
# This is a test — we're just checking which ones have online booking

CLSC_LIST = [
    "CLSC Hochelaga-Maisonneuve",
    "CLSC Rosemont",
    "CLSC Cote-des-Neiges",
    "CLSC Saint-Michel",
    "CLSC Verdun",
    "CLSC Ahuntsic",
    "CLSC Montreal-Nord",
    "CLSC Riviere-des-Prairies",
    "CLSC Plateau-Mont-Royal",
    "CLSC Saint-Laurent",
]

def check_direct_booking(clsc_name):
    """Try to find a direct booking page for a CLSC"""
    # Common booking URL patterns used by Quebec health institutions
    patterns = [
        f"https://{clsc_name.lower().replace(' ', '-')}.ca/rendez-vous",
        f"https://www.santemontreal.qc.ca/{clsc_name.lower().replace(' ', '-')}/rendez-vous",
    ]
    
    results = {
        "name": clsc_name,
        "checked_at": datetime.now().isoformat(),
        "booking_urls": [],
        "has_direct_booking": False,
    }
    
    for url in patterns:
        try:
            response = requests.head(url, timeout=5)
            if response.status_code == 200:
                results["booking_urls"].append(url)
                results["has_direct_booking"] = True
        except:
            pass
    
    return results

def main():
    print("=" * 50)
    print("MyVita Lab Test Finder — CLSC Scan")
    print("=" * 50)
    
    findings = []
    
    for clsc in CLSC_LIST:
        print(f"\nChecking: {clsc}...")
        result = check_direct_booking(clsc)
        findings.append(result)
        
        if result["has_direct_booking"]:
            print(f"  ✅ Found {len(result['booking_urls'])} possible booking pages")
        else:
            print(f"  ❌ No direct booking found")
    
    # Save results
    with open("lab_booking_test.json", "w", encoding="utf-8") as f:
        json.dump(findings, f, ensure_ascii=False, indent=2)
    
    direct_count = sum(1 for f in findings if f["has_direct_booking"])
    print(f"\n✅ Results: {direct_count}/{len(CLSC_LIST)} have possible direct booking")

if __name__ == "__main__":
    main()
