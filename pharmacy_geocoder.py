import json
import time
import requests

API_KEY = "AIzaSyCfKSK2CW2po79uLpkKX9ZRz8FF_-feO_M"
OUTPUT_FILE = "pharmacies_with_coords.json"

# Cities with their coordinates for radius search
CITIES = [
    ("Montreal", 45.5017, -73.5673),
    ("Quebec City", 46.8139, -71.2080),
    ("Laval", 45.6066, -73.7124),
    ("Gatineau", 45.4765, -75.7013),
    ("Longueuil", 45.5369, -73.5107),
    ("Sherbrooke", 45.4042, -71.8925),
    ("Saguenay", 48.4284, -71.0687),
    ("Levis", 46.8033, -71.1779),
    ("Trois-Rivieres", 46.3434, -72.5411),
    ("Terrebonne", 45.7072, -73.6329),
    ("Saint-Jean-sur-Richelieu", 45.3068, -73.2620),
    ("Repentigny", 45.7403, -73.4550),
    ("Drummondville", 45.8832, -72.4827),
    ("Saint-Jerome", 45.7780, -74.0030),
    ("Granby", 45.4015, -72.7324),
    ("Blainville", 45.6668, -73.8726),
    ("Saint-Hyacinthe", 45.6263, -72.9566),
    ("Rimouski", 48.4508, -68.5244),
    ("Joliette", 46.0230, -73.4392),
    ("Victoriaville", 46.0550, -71.9588),
    ("Rouyn-Noranda", 48.2411, -79.0208),
    ("Salaberry-de-Valleyfield", 45.2542, -74.1327),
    ("Sept-Iles", 50.2133, -66.3812),
]

def search_pharmacies_radius(lat, lng, radius, next_page_token=None):
    """Search using nearbysearch with radius and pagination"""
    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    params = {
        "location": f"{lat},{lng}",
        "radius": radius,
        "type": "pharmacy",
        "key": API_KEY,
    }
    if next_page_token:
        params["pagetoken"] = next_page_token
    
    try:
        response = requests.get(url, params=params, timeout=30)
        if response.status_code == 200:
            return response.json()
        return {}
    except Exception as e:
        print(f"  Error: {e}")
        return {}

def main():
    print("=" * 60)
    print("MyVita Pharmacy Geocoder")
    print("=" * 60)
    
    all_pharmacies = {}
    
    for city_name, lat, lng in CITIES:
        print(f"\nSearching: {city_name}...")
        
        # Search with 3 different radius sizes to get more results
        for radius in [5000, 10000, 15000]:
            data = search_pharmacies_radius(lat, lng, radius)
            results = data.get("results", [])
            
            for place in results:
                name = place.get("name", "")
                vicinity = place.get("vicinity", "")
                location = place.get("geometry", {}).get("location", {})
                rating = place.get("rating", 0)
                
                if name not in all_pharmacies:
                    all_pharmacies[name] = {
                        "name": name,
                        "address": vicinity,
                        "lat": location.get("lat", 0),
                        "lng": location.get("lng", 0),
                        "rating": rating,
                        "city": city_name,
                    }
            
            # Get next page if available
            next_token = data.get("next_page_token")
            if next_token:
                time.sleep(2)
                data2 = search_pharmacies_radius(lat, lng, radius, next_token)
                for place in data2.get("results", []):
                    name = place.get("name", "")
                    if name not in all_pharmacies:
                        all_pharmacies[name] = {
                            "name": name,
                            "address": place.get("vicinity", ""),
                            "lat": place.get("geometry", {}).get("location", {}).get("lat", 0),
                            "lng": place.get("geometry", {}).get("location", {}).get("lng", 0),
                            "rating": place.get("rating", 0),
                            "city": city_name,
                        }
            
            time.sleep(1)
        
        print(f"  Running total: {len(all_pharmacies)} pharmacies")
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(list(all_pharmacies.values()), f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ Saved {len(all_pharmacies)} pharmacies to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
