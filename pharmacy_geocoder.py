import json
import time
import requests

API_KEY = "AIzaSyCfKSK2CW2po79uLpkKX9ZRz8FF_-feO_M"
OUTPUT_FILE = "pharmacies_with_coords.json"

# Montreal districts
MONTREAL_AREAS = [
    ("Downtown", 45.5017, -73.5673),
    ("Plateau", 45.5200, -73.5820),
    ("Rosemont", 45.5500, -73.5800),
    ("Hochelaga", 45.5450, -73.5400),
    ("Cote-des-Neiges", 45.4980, -73.6250),
    ("NDG", 45.4700, -73.6150),
    ("Saint-Laurent", 45.5000, -73.7000),
    ("Ahuntsic", 45.5600, -73.6600),
    ("Montreal-Nord", 45.6000, -73.6200),
    ("Saint-Leonard", 45.5900, -73.5950),
    ("Anjou", 45.6100, -73.5500),
    ("Verdun", 45.4600, -73.5500),
    ("LaSalle", 45.4300, -73.6400),
    ("Lachine", 45.4400, -73.6900),
    ("Pierrefonds", 45.4800, -73.8700),
    ("Pointe-aux-Trembles", 45.6500, -73.5000),
    ("Outremont", 45.5200, -73.6100),
    ("Westmount", 45.4870, -73.6000),
]

# Other Quebec cities
OTHER_CITIES = [
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

def search_pharmacies(lat, lng, area_name, radius=3000):
    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    params = {
        "location": f"{lat},{lng}",
        "radius": radius,
        "type": "pharmacy",
        "key": API_KEY,
    }
    
    try:
        response = requests.get(url, params=params, timeout=30)
        if response.status_code == 200:
            data = response.json()
            results = []
            for place in data.get("results", []):
                results.append({
                    "name": place.get("name", ""),
                    "address": place.get("vicinity", ""),
                    "lat": place.get("geometry", {}).get("location", {}).get("lat", 0),
                    "lng": place.get("geometry", {}).get("location", {}).get("lng", 0),
                    "rating": place.get("rating", 0),
                    "area": area_name,
                })
            
            # Get page 2 if available
            next_token = data.get("next_page_token")
            if next_token:
                time.sleep(2)
                params["pagetoken"] = next_token
                response2 = requests.get(url, params=params, timeout=30)
                if response2.status_code == 200:
                    data2 = response2.json()
                    for place in data2.get("results", []):
                        results.append({
                            "name": place.get("name", ""),
                            "address": place.get("vicinity", ""),
                            "lat": place.get("geometry", {}).get("location", {}).get("lat", 0),
                            "lng": place.get("geometry", {}).get("location", {}).get("lng", 0),
                            "rating": place.get("rating", 0),
                            "area": area_name,
                        })
            
            return results
        return []
    except Exception as e:
        print(f"  Error in {area_name}: {e}")
        return []

def main():
    print("=" * 60)
    print("MyVita Pharmacy Geocoder — Full Quebec")
    print("=" * 60)
    
    all_pharmacies = {}
    
    # Montreal districts
    print("\n=== MONTREAL DISTRICTS ===")
    for area_name, lat, lng in MONTREAL_AREAS:
        print(f"Searching: Montreal - {area_name}...")
        results = search_pharmacies(lat, lng, area_name)
        for p in results:
            if p["name"] not in all_pharmacies:
                all_pharmacies[p["name"]] = p
        print(f"  Found {len(results)}, Total: {len(all_pharmacies)}")
        time.sleep(0.3)
    
    # Other cities
    print("\n=== OTHER QUEBEC CITIES ===")
    for city_name, lat, lng in OTHER_CITIES:
        print(f"Searching: {city_name}...")
        results = search_pharmacies(lat, lng, city_name, radius=5000)
        for p in results:
            if p["name"] not in all_pharmacies:
                all_pharmacies[p["name"]] = p
        print(f"  Found {len(results)}, Total: {len(all_pharmacies)}")
        time.sleep(0.3)
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(list(all_pharmacies.values()), f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ Saved {len(all_pharmacies)} pharmacies to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
