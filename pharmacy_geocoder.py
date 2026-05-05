import json
import time
import requests

# Google Places API Key (use the same one from your dentist page)
API_KEY = "AIzaSyCfKSK2CW2po79uLpkKX9ZRz8FF_-feO_M"

OUTPUT_FILE = "pharmacies_with_coords.json"

# Major Quebec cities to search in
CITIES = [
    "Montreal", "Quebec City", "Laval", "Gatineau", "Longueuil",
    "Sherbrooke", "Saguenay", "Levis", "Trois-Rivieres", "Terrebonne",
    "Saint-Jean-sur-Richelieu", "Repentigny", "Drummondville", "Saint-Jerome",
    "Granby", "Blainville", "Saint-Hyacinthe", "Rimouski", "Joliette",
    "Victoriaville", "Rouyn-Noranda", "Salaberry-de-Valleyfield", "Sept-Iles"
]

def search_pharmacies(city):
    """Search for pharmacies in a city using Google Places API"""
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {
        "query": f"pharmacy in {city} Quebec Canada",
        "key": API_KEY,
    }
    
    try:
        response = requests.get(url, params=params, timeout=30)
        if response.status_code == 200:
            data = response.json()
            return data.get("results", [])
        else:
            print(f"  Error {response.status_code} for {city}")
            return []
    except Exception as e:
        print(f"  Error: {e}")
        return []

def main():
    print("=" * 60)
    print("MyVita Pharmacy Geocoder")
    print("=" * 60)
    
    all_pharmacies = {}
    total = 0
    
    for city in CITIES:
        print(f"\nSearching: {city}...")
        results = search_pharmacies(city)
        
        for place in results:
            name = place.get("name", "")
            address = place.get("formatted_address", "")
            location = place.get("geometry", {}).get("location", {})
            rating = place.get("rating", 0)
            
            # Avoid duplicates
            if name not in all_pharmacies:
                all_pharmacies[name] = {
                    "name": name,
                    "address": address,
                    "lat": location.get("lat", 0),
                    "lng": location.get("lng", 0),
                    "rating": rating,
                    "city": city,
                }
                total += 1
        
        print(f"  Found {len(results)} in {city}")
        time.sleep(2)  # Avoid rate limiting
    
    # Save results
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(list(all_pharmacies.values()), f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ Saved {total} pharmacies to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
