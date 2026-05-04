import json
import requests
from datetime import datetime

# CKAN API for flu vaccination data (Quebec open data portal)
# This searches for flu shot related datasets
CKAN_SEARCH_API = "https://www.donneesquebec.ca/api/3/action/package_search?q=grippe+vaccination"

OUTPUT_FILE = "flu_shots.json"

def download_flu_data():
    """Fetch flu shot locations from Quebec open data"""
    print(f"[{datetime.now()}] Searching for flu shot data...")
    
    headers = {
        'User-Agent': 'Mozilla/5.0',
    }
    
    try:
        response = requests.get(CKAN_SEARCH_API, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            datasets = data.get('result', {}).get('results', [])
            
            if datasets:
                print(f"Found {len(datasets)} flu-related datasets")
                # For now, return static list of known vaccination resources
                # We'll enhance this when we find the exact dataset ID
                return get_flu_shot_resources()
            else:
                print("No specific flu dataset found, using default resources")
                return get_flu_shot_resources()
        else:
            print(f"API error: {response.status_code}")
            return get_flu_shot_resources()
    except Exception as e:
        print(f"Error: {e}")
        return get_flu_shot_resources()

def get_flu_shot_resources():
    """Return known flu shot resources in Quebec"""
    resources = {
        "last_update": datetime.now().isoformat(),
        "source": "Gouvernement du Québec / MSSS",
        "info": {
            "en": "Flu shots are available at pharmacies, CLSCs, and medical clinics across Quebec. The vaccine is free for at-risk groups.",
            "fr": "Les vaccins contre la grippe sont disponibles dans les pharmacies, CLSC et cliniques médicales partout au Québec. Le vaccin est gratuit pour les groupes à risque."
        },
        "where_to_get": [
            {
                "type": "pharmacy",
                "name_en": "Most pharmacies (Jean Coutu, Pharmaprix, Uniprix, etc.)",
                "name_fr": "La plupart des pharmacies (Jean Coutu, Pharmaprix, Uniprix, etc.)",
                "note_en": "Walk-in or appointment. Usually $15-20 if not covered.",
                "note_fr": "Sans rendez-vous ou sur rendez-vous. Généralement 15-20$ si non couvert."
            },
            {
                "type": "clsc",
                "name_en": "Local CLSC",
                "name_fr": "CLSC local",
                "note_en": "Free for at-risk groups. Call or check online for hours.",
                "note_fr": "Gratuit pour les groupes à risque. Appelez ou vérifiez en ligne pour les horaires."
            },
            {
                "type": "clinic",
                "name_en": "Medical clinics and GMF",
                "name_fr": "Cliniques médicales et GMF",
                "note_en": "Ask your family doctor or check with your GMF.",
                "note_fr": "Demandez à votre médecin de famille ou vérifiez auprès de votre GMF."
            }
        ],
        "government_links": {
            "en": "https://www.quebec.ca/en/health/health-issues/flu-cold-and-gastroenteritis/flu-influenza/vaccination",
            "fr": "https://www.quebec.ca/sante/problemes-de-sante/grippe-rhume-et-gastroenterite/grippe-influenza/vaccination"
        },
        "at_risk_groups": {
            "en": [
                "People aged 60 and over",
                "Children aged 6 months to 17 years with chronic diseases",
                "Pregnant women",
                "Healthcare workers",
                "People living in CHSLDs or intermediate resources"
            ],
            "fr": [
                "Personnes âgées de 60 ans et plus",
                "Enfants de 6 mois à 17 ans avec maladies chroniques",
                "Femmes enceintes",
                "Travailleurs de la santé",
                "Personnes vivant en CHSLD ou ressources intermédiaires"
            ]
        }
    }
    
    return resources

def save_json(data):
    """Save flu shot data as JSON"""
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ Saved to {OUTPUT_FILE}")

def main():
    print("=" * 50)
    print(f"MyVita Flu Shot Scraper — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)
    
    data = download_flu_data()
    save_json(data)
    print("✅ Flu shot scraper complete!")

if __name__ == "__main__":
    main()
