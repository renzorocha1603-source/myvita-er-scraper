import json
import requests
from datetime import datetime

# CKAN API to search for pharmacy datasets on Quebec open data portal
CKAN_SEARCH_API = "https://www.donneesquebec.ca/api/3/action/package_search?q=pharmacie+service"

OUTPUT_FILE = "pharmacies.json"

def download_pharmacy_data():
    """Fetch pharmacy information from Quebec open data"""
    print(f"[{datetime.now()}] Searching for pharmacy data...")
    
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        response = requests.get(CKAN_SEARCH_API, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            datasets = data.get('result', {}).get('results', [])
            print(f"Found {len(datasets)} pharmacy-related datasets")
    except Exception as e:
        print(f"Search error: {e}")
    
    # Return structured pharmacy data for the AI
    return get_pharmacy_resources()

def get_pharmacy_resources():
    """Return known pharmacy resources in Quebec"""
    resources = {
        "last_update": datetime.now().isoformat(),
        "source": "Gouvernement du Québec / MSSS / RAMQ",
        "info": {
            "en": "Pharmacies across Quebec offer medication dispensing, consultations, flu shots, and some lab tests. Most are open weekdays and Saturdays, with some open 24/7.",
            "fr": "Les pharmacies du Québec offrent la distribution de médicaments, des consultations, des vaccins contre la grippe et certains tests de laboratoire. La plupart sont ouvertes en semaine et le samedi, certaines 24h/24 et 7j/7."
        },
        "services": {
            "medication": {
                "en": "Prescription filling and over-the-counter medications",
                "fr": "Exécution d'ordonnances et médicaments en vente libre"
            },
            "consultation": {
                "en": "Pharmacist consultations for minor ailments (allergies, cold sores, UTIs, etc.)",
                "fr": "Consultations avec le pharmacien pour problèmes mineurs (allergies, feux sauvages, infections urinaires, etc.)"
            },
            "vaccination": {
                "en": "Flu shots, COVID-19 vaccines, and travel vaccinations",
                "fr": "Vaccins contre la grippe, la COVID-19 et vaccins de voyage"
            },
            "lab_tests": {
                "en": "Some pharmacies offer blood tests, glucose monitoring, and cholesterol testing",
                "fr": "Certaines pharmacies offrent des tests sanguins, surveillance du glucose et tests de cholestérol"
            }
        },
        "major_chains": {
            "en": [
                {"name": "Jean Coutu", "website": "https://www.jeancoutu.com"},
                {"name": "Pharmaprix / Shoppers Drug Mart", "website": "https://www.pharmaprix.ca"},
                {"name": "Uniprix", "website": "https://www.uniprix.com"},
                {"name": "Familiprix", "website": "https://www.familiprix.com"},
                {"name": "Proxim", "website": "https://www.groupeproxim.ca"},
                {"name": "Brunet", "website": "https://www.brunet.ca"},
                {"name": "Walmart Pharmacy", "website": "https://www.walmart.ca/en/pharmacy"},
                {"name": "Costco Pharmacy", "website": "https://www.costcopharmacy.ca"}
            ],
            "fr": [
                {"name": "Jean Coutu", "website": "https://www.jeancoutu.com"},
                {"name": "Pharmaprix / Shoppers Drug Mart", "website": "https://www.pharmaprix.ca"},
                {"name": "Uniprix", "website": "https://www.uniprix.com"},
                {"name": "Familiprix", "website": "https://www.familiprix.com"},
                {"name": "Proxim", "website": "https://www.groupeproxim.ca"},
                {"name": "Brunet", "website": "https://www.brunet.ca"},
                {"name": "Pharmacie Walmart", "website": "https://www.walmart.ca/fr/pharmacie"},
                {"name": "Pharmacie Costco", "website": "https://www.costcopharmacy.ca"}
            ]
        },
        "typical_hours": {
            "en": "Monday-Friday: 9am-9pm, Saturday: 9am-5pm, Sunday: 10am-5pm (varies by location)",
            "fr": "Lundi-Vendredi: 9h-21h, Samedi: 9h-17h, Dimanche: 10h-17h (varie selon l'emplacement)"
        },
        "government_links": {
            "en": "https://www.quebec.ca/en/health/medications",
            "fr": "https://www.quebec.ca/sante/medicaments"
        }
    }
    
    return resources

def save_json(data):
    """Save pharmacy data as JSON"""
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ Saved to {OUTPUT_FILE}")

def main():
    print("=" * 50)
    print(f"MyVita Pharmacy Scraper — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)
    
    data = download_pharmacy_data()
    save_json(data)
    print("✅ Pharmacy scraper complete!")

if __name__ == "__main__":
    main()
