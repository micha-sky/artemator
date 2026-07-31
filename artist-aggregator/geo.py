"""
geo.py — offline gazetteer: place names in listing text → country, region
group(s), and map coordinates.

No geocoding API: a bundled table of countries (name aliases, rough centroid,
region groups) and ~200 art-relevant cities (coords). City hits win over
country hits because they're more specific and give better map pins. All
matching is lowercase word-boundary, longest alias first, so "tunis" never
fires inside "tunisia" and "santiago de compostela" wins over "santiago".

Region groups are the residency-search facets the dashboard filters on. A
country can sit in several (Croatia → Balkans + Mediterranean). PREFERRED_GROUPS
is the searcher's own shortlist, surfaced as the "★ my regions" preset.
"""
import re

PREFERRED_GROUPS = (
    "Southeast Asia", "Balkans", "Mediterranean", "Baltic", "Iberia",
    "France/Paris", "Mongolia/Central Asia", "Eastern Europe",
)

# iso: (name aliases…, (lat, lon), region groups…)   — aliases lowercase.
# Nouns only (no "spanish"/"french"): adjectives over-fire on language mentions.
COUNTRIES = {
    # Balkans
    "AL": (("albania",), (41.2, 20.0), ("Balkans", "Mediterranean")),
    "BA": (("bosnia", "herzegovina"), (43.9, 17.7), ("Balkans",)),
    "BG": (("bulgaria",), (42.7, 25.5), ("Balkans", "Eastern Europe")),
    "HR": (("croatia",), (45.1, 15.2), ("Balkans", "Mediterranean")),
    "GR": (("greece",), (39.1, 21.8), ("Balkans", "Mediterranean")),
    "XK": (("kosovo",), (42.6, 20.9), ("Balkans",)),
    "ME": (("montenegro",), (42.7, 19.4), ("Balkans", "Mediterranean")),
    "MK": (("north macedonia", "macedonia"), (41.6, 21.7), ("Balkans",)),
    "RO": (("romania",), (45.9, 25.0), ("Balkans", "Eastern Europe")),
    "RS": (("serbia",), (44.2, 20.9), ("Balkans",)),
    "SI": (("slovenia",), (46.2, 14.8), ("Balkans", "Mediterranean")),
    # Baltic
    "EE": (("estonia",), (58.7, 25.5), ("Baltic",)),
    "LV": (("latvia",), (56.9, 24.9), ("Baltic",)),
    "LT": (("lithuania",), (55.2, 23.9), ("Baltic",)),
    # Iberia / France
    "ES": (("spain", "españa"), (40.2, -3.6), ("Iberia", "Mediterranean")),
    "PT": (("portugal",), (39.6, -8.0), ("Iberia",)),
    "FR": (("france",), (46.6, 2.4), ("France/Paris", "Mediterranean")),
    # Eastern Europe
    "PL": (("poland",), (52.1, 19.4), ("Eastern Europe",)),
    "CZ": (("czech republic", "czechia"), (49.8, 15.5), ("Eastern Europe",)),
    "SK": (("slovakia",), (48.7, 19.7), ("Eastern Europe",)),
    "HU": (("hungary",), (47.2, 19.5), ("Eastern Europe",)),
    "UA": (("ukraine",), (48.8, 31.2), ("Eastern Europe",)),
    "MD": (("moldova",), (47.2, 28.5), ("Eastern Europe",)),
    "BY": (("belarus",), (53.7, 28.0), ("Eastern Europe",)),
    "GE": (("georgia",), (42.3, 43.4), ("Eastern Europe",)),
    "AM": (("armenia",), (40.3, 45.0), ("Eastern Europe",)),
    "AZ": (("azerbaijan",), (40.3, 47.7), ("Eastern Europe",)),
    "RU": (("russia",), (55.8, 45.0), ("Eastern Europe",)),
    # Western Europe
    "DE": (("germany", "deutschland"), (51.1, 10.4), ("Western Europe",)),
    "AT": (("austria", "österreich"), (47.6, 14.1), ("Western Europe",)),
    "CH": (("switzerland", "schweiz", "suisse"), (46.8, 8.2), ("Western Europe",)),
    "NL": (("netherlands", "holland"), (52.2, 5.3), ("Western Europe",)),
    "BE": (("belgium",), (50.6, 4.7), ("Western Europe",)),
    "LU": (("luxembourg",), (49.8, 6.1), ("Western Europe",)),
    "GB": (("united kingdom", "uk", "britain", "england", "scotland", "wales"),
           (52.9, -1.8), ("Western Europe",)),
    "IE": (("ireland",), (53.2, -8.1), ("Western Europe",)),
    # Nordics
    "IS": (("iceland",), (64.9, -18.6), ("Nordics",)),
    "DK": (("denmark",), (56.0, 9.9), ("Nordics",)),
    "SE": (("sweden",), (62.2, 15.3), ("Nordics",)),
    "NO": (("norway",), (61.2, 9.1), ("Nordics",)),
    "FI": (("finland",), (64.5, 26.0), ("Nordics",)),
    # Mediterranean (non-Iberia/France/Balkan)
    "IT": (("italy", "italia"), (42.8, 12.6), ("Mediterranean",)),
    "MT": (("malta",), (35.9, 14.4), ("Mediterranean",)),
    "CY": (("cyprus",), (35.0, 33.2), ("Mediterranean",)),
    "TR": (("turkey", "türkiye"), (39.0, 35.2), ("Mediterranean", "Middle East")),
    # Southeast Asia
    "TH": (("thailand",), (15.8, 101.0), ("Southeast Asia",)),
    "VN": (("vietnam", "viet nam"), (16.0, 107.8), ("Southeast Asia",)),
    "KH": (("cambodia",), (12.6, 105.0), ("Southeast Asia",)),
    "LA": (("laos",), (19.7, 102.5), ("Southeast Asia",)),
    "MM": (("myanmar", "burma"), (21.2, 96.7), ("Southeast Asia",)),
    "MY": (("malaysia",), (4.0, 102.0), ("Southeast Asia",)),
    "SG": (("singapore",), (1.35, 103.82), ("Southeast Asia",)),
    "ID": (("indonesia",), (-2.5, 118.0), ("Southeast Asia",)),
    "PH": (("philippines",), (12.9, 121.8), ("Southeast Asia",)),
    "BN": (("brunei",), (4.5, 114.7), ("Southeast Asia",)),
    "TL": (("timor-leste", "east timor"), (-8.8, 125.7), ("Southeast Asia",)),
    # Mongolia / Central Asia
    "MN": (("mongolia",), (46.9, 103.8), ("Mongolia/Central Asia",)),
    "KZ": (("kazakhstan",), (48.0, 66.9), ("Mongolia/Central Asia",)),
    "KG": (("kyrgyzstan",), (41.2, 74.8), ("Mongolia/Central Asia",)),
    "UZ": (("uzbekistan",), (41.4, 64.6), ("Mongolia/Central Asia",)),
    "TJ": (("tajikistan",), (38.9, 71.3), ("Mongolia/Central Asia",)),
    "TM": (("turkmenistan",), (39.1, 59.4), ("Mongolia/Central Asia",)),
    # East Asia
    "CN": (("china",), (35.0, 104.2), ("East Asia",)),
    "JP": (("japan",), (36.2, 138.3), ("East Asia",)),
    "KR": (("south korea", "korea"), (36.5, 127.9), ("East Asia",)),
    "TW": (("taiwan",), (23.7, 121.0), ("East Asia",)),
    "HK": (("hong kong",), (22.32, 114.17), ("East Asia",)),
    "MO": (("macau", "macao"), (22.2, 113.55), ("East Asia",)),
    # South Asia
    "IN": (("india",), (21.0, 78.0), ("South Asia",)),
    "PK": (("pakistan",), (30.4, 69.4), ("South Asia",)),
    "BD": (("bangladesh",), (23.7, 90.4), ("South Asia",)),
    "LK": (("sri lanka",), (7.9, 80.7), ("South Asia",)),
    "NP": (("nepal",), (28.4, 84.1), ("South Asia",)),
    "BT": (("bhutan",), (27.5, 90.4), ("South Asia",)),
    "MV": (("maldives",), (3.2, 73.2), ("South Asia",)),
    # Middle East (+ Mediterranean shore)
    "IL": (("israel",), (31.4, 35.0), ("Middle East", "Mediterranean")),
    "PS": (("palestine",), (31.9, 35.2), ("Middle East", "Mediterranean")),
    "LB": (("lebanon",), (33.9, 35.9), ("Middle East", "Mediterranean")),
    "SY": (("syria",), (35.0, 38.5), ("Middle East", "Mediterranean")),
    "JO": (("jordan",), (31.3, 36.4), ("Middle East",)),
    "IQ": (("iraq",), (33.1, 43.7), ("Middle East",)),
    "IR": (("iran",), (32.6, 54.3), ("Middle East",)),
    "SA": (("saudi arabia",), (24.2, 44.5), ("Middle East",)),
    "AE": (("united arab emirates", "uae"), (24.0, 54.0), ("Middle East",)),
    "QA": (("qatar",), (25.3, 51.2), ("Middle East",)),
    "KW": (("kuwait",), (29.3, 47.6), ("Middle East",)),
    "BH": (("bahrain",), (26.0, 50.5), ("Middle East",)),
    "OM": (("oman",), (20.6, 56.1), ("Middle East",)),
    # Africa (+ Mediterranean shore)
    "EG": (("egypt",), (26.8, 30.0), ("Mediterranean", "Africa")),
    "LY": (("libya",), (27.0, 17.2), ("Mediterranean", "Africa")),
    "TN": (("tunisia",), (34.1, 9.5), ("Mediterranean", "Africa")),
    "DZ": (("algeria",), (28.0, 2.6), ("Mediterranean", "Africa")),
    "MA": (("morocco",), (31.8, -7.1), ("Mediterranean", "Africa")),
    "ZA": (("south africa",), (-29.0, 25.1), ("Africa",)),
    "NG": (("nigeria",), (9.1, 8.7), ("Africa",)),
    "KE": (("kenya",), (0.2, 37.9), ("Africa",)),
    "GH": (("ghana",), (7.9, -1.2), ("Africa",)),
    "SN": (("senegal",), (14.4, -14.5), ("Africa",)),
    "ET": (("ethiopia",), (9.1, 40.5), ("Africa",)),
    "UG": (("uganda",), (1.4, 32.3), ("Africa",)),
    "TZ": (("tanzania",), (-6.4, 34.9), ("Africa",)),
    "RW": (("rwanda",), (-2.0, 29.9), ("Africa",)),
    "CI": (("ivory coast",), (7.5, -5.5), ("Africa",)),
    "CM": (("cameroon",), (5.7, 12.7), ("Africa",)),
    "ZW": (("zimbabwe",), (-19.0, 29.2), ("Africa",)),
    # Americas
    "US": (("usa", "united states"), (39.8, -98.6), ("North America",)),
    "CA": (("canada",), (56.1, -106.3), ("North America",)),
    "MX": (("mexico",), (23.6, -102.6), ("Latin America",)),
    "BR": (("brazil", "brasil"), (-10.8, -52.9), ("Latin America",)),
    "AR": (("argentina",), (-34.0, -64.0), ("Latin America",)),
    "CL": (("chile",), (-31.8, -71.0), ("Latin America",)),
    "CO": (("colombia",), (4.6, -74.1), ("Latin America",)),
    "PE": (("peru",), (-9.2, -75.0), ("Latin America",)),
    "EC": (("ecuador",), (-1.4, -78.4), ("Latin America",)),
    "BO": (("bolivia",), (-16.3, -63.6), ("Latin America",)),
    "UY": (("uruguay",), (-32.5, -55.8), ("Latin America",)),
    "VE": (("venezuela",), (6.4, -66.6), ("Latin America",)),
    "CU": (("cuba",), (21.5, -79.0), ("Latin America",)),
    "CR": (("costa rica",), (9.7, -84.2), ("Latin America",)),
    "GT": (("guatemala",), (15.8, -90.2), ("Latin America",)),
    "PA": (("panama",), (8.4, -80.1), ("Latin America",)),
    # Oceania
    "AU": (("australia",), (-25.3, 133.8), ("Oceania",)),
    "NZ": (("new zealand",), (-41.8, 172.8), ("Oceania",)),
    "FJ": (("fiji",), (-17.7, 178.0), ("Oceania",)),
}

# city alias (lowercase): (iso, lat, lon). Ambiguous names that are common
# English words (nice, split, bath, cork, reading, mobile…) are deliberately
# absent — a wrong pin is worse than a country-level pin.
CITIES = {
    # Southeast Asia
    "bangkok": ("TH", 13.75, 100.50), "chiang mai": ("TH", 18.79, 98.98),
    "hanoi": ("VN", 21.03, 105.85), "ho chi minh city": ("VN", 10.82, 106.63),
    "saigon": ("VN", 10.82, 106.63), "da nang": ("VN", 16.06, 108.21),
    "phnom penh": ("KH", 11.56, 104.92), "siem reap": ("KH", 13.36, 103.86),
    "vientiane": ("LA", 17.97, 102.63), "luang prabang": ("LA", 19.89, 102.14),
    "yangon": ("MM", 16.87, 96.20), "kuala lumpur": ("MY", 3.14, 101.69),
    "penang": ("MY", 5.42, 100.31), "jakarta": ("ID", -6.21, 106.85),
    "bandung": ("ID", -6.92, 107.61), "yogyakarta": ("ID", -7.80, 110.36),
    "ubud": ("ID", -8.51, 115.26), "bali": ("ID", -8.41, 115.19),
    "manila": ("PH", 14.60, 120.98), "cebu": ("PH", 10.32, 123.89),
    "singapore": ("SG", 1.35, 103.82),
    # Balkans
    "tirana": ("AL", 41.33, 19.82), "sarajevo": ("BA", 43.86, 18.41),
    "sofia": ("BG", 42.70, 23.32), "plovdiv": ("BG", 42.14, 24.75),
    "zagreb": ("HR", 45.81, 15.98), "rijeka": ("HR", 45.33, 14.44),
    "dubrovnik": ("HR", 42.65, 18.09), "athens": ("GR", 37.98, 23.73),
    "thessaloniki": ("GR", 40.64, 22.94), "hydra": ("GR", 37.35, 23.46),
    "pristina": ("XK", 42.66, 21.17), "podgorica": ("ME", 42.44, 19.26),
    "skopje": ("MK", 41.99, 21.43), "bucharest": ("RO", 44.43, 26.10),
    "cluj": ("RO", 46.77, 23.60), "timisoara": ("RO", 45.75, 21.23),
    "belgrade": ("RS", 44.79, 20.45), "novi sad": ("RS", 45.25, 19.85),
    "ljubljana": ("SI", 46.06, 14.51),
    # Baltic
    "tallinn": ("EE", 59.44, 24.75), "tartu": ("EE", 58.38, 26.72),
    "narva": ("EE", 59.38, 28.19), "riga": ("LV", 56.95, 24.11),
    "vilnius": ("LT", 54.69, 25.28), "kaunas": ("LT", 54.90, 23.89),
    "nida": ("LT", 55.30, 20.99),
    # Iberia
    "madrid": ("ES", 40.42, -3.70), "barcelona": ("ES", 41.39, 2.17),
    "valencia": ("ES", 39.47, -0.38), "seville": ("ES", 37.39, -5.99),
    "sevilla": ("ES", 37.39, -5.99), "bilbao": ("ES", 43.26, -2.93),
    "granada": ("ES", 37.18, -3.60), "malaga": ("ES", 36.72, -4.42),
    "málaga": ("ES", 36.72, -4.42), "mallorca": ("ES", 39.57, 2.65),
    "menorca": ("ES", 39.95, 4.05), "santiago de compostela": ("ES", 42.88, -8.54),
    "lisbon": ("PT", 38.72, -9.14), "lisboa": ("PT", 38.72, -9.14),
    "porto": ("PT", 41.15, -8.61), "faro": ("PT", 37.02, -7.93),
    "evora": ("PT", 38.57, -7.91), "évora": ("PT", 38.57, -7.91),
    # France
    "paris": ("FR", 48.86, 2.35), "marseille": ("FR", 43.30, 5.37),
    "lyon": ("FR", 45.76, 4.84), "arles": ("FR", 43.68, 4.63),
    "nantes": ("FR", 47.22, -1.55), "bordeaux": ("FR", 44.84, -0.58),
    "toulouse": ("FR", 43.60, 1.44), "strasbourg": ("FR", 48.57, 7.75),
    "avignon": ("FR", 43.95, 4.81),
    # Mongolia / Central Asia
    "ulaanbaatar": ("MN", 47.89, 106.91), "ulan bator": ("MN", 47.89, 106.91),
    "almaty": ("KZ", 43.24, 76.95), "astana": ("KZ", 51.17, 71.43),
    "bishkek": ("KG", 42.87, 74.59), "tashkent": ("UZ", 41.30, 69.24),
    "samarkand": ("UZ", 39.65, 66.96), "bukhara": ("UZ", 39.77, 64.42),
    "dushanbe": ("TJ", 38.56, 68.79),
    # Eastern Europe
    "warsaw": ("PL", 52.23, 21.01), "krakow": ("PL", 50.06, 19.94),
    "kraków": ("PL", 50.06, 19.94), "wroclaw": ("PL", 51.11, 17.03),
    "wrocław": ("PL", 51.11, 17.03), "gdansk": ("PL", 54.35, 18.65),
    "gdańsk": ("PL", 54.35, 18.65), "lodz": ("PL", 51.76, 19.46),
    "prague": ("CZ", 50.08, 14.44), "praha": ("CZ", 50.08, 14.44),
    "brno": ("CZ", 49.20, 16.61), "bratislava": ("SK", 48.15, 17.11),
    "kosice": ("SK", 48.72, 21.26), "budapest": ("HU", 47.50, 19.04),
    "kyiv": ("UA", 50.45, 30.52), "kiev": ("UA", 50.45, 30.52),
    "lviv": ("UA", 49.84, 24.03), "odesa": ("UA", 46.48, 30.73),
    "odessa": ("UA", 46.48, 30.73), "chisinau": ("MD", 47.01, 28.86),
    "minsk": ("BY", 53.90, 27.56), "tbilisi": ("GE", 41.72, 44.79),
    "yerevan": ("AM", 40.18, 44.51), "baku": ("AZ", 40.41, 49.87),
    "moscow": ("RU", 55.76, 37.62), "st petersburg": ("RU", 59.94, 30.31),
    # Mediterranean / Italy / Turkey / Levant / North Africa
    "rome": ("IT", 41.90, 12.50), "milan": ("IT", 45.46, 9.19),
    "milano": ("IT", 45.46, 9.19), "venice": ("IT", 45.44, 12.32),
    "venezia": ("IT", 45.44, 12.32), "florence": ("IT", 43.77, 11.26),
    "firenze": ("IT", 43.77, 11.26), "naples": ("IT", 40.85, 14.27),
    "napoli": ("IT", 40.85, 14.27), "turin": ("IT", 45.07, 7.69),
    "torino": ("IT", 45.07, 7.69), "bologna": ("IT", 44.49, 11.34),
    "palermo": ("IT", 38.12, 13.36), "istanbul": ("TR", 41.01, 28.98),
    "izmir": ("TR", 38.42, 27.14), "ankara": ("TR", 39.93, 32.86),
    "nicosia": ("CY", 35.19, 33.38), "valletta": ("MT", 35.90, 14.51),
    "cairo": ("EG", 30.04, 31.24), "tunis": ("TN", 36.81, 10.18),
    "marrakech": ("MA", 31.63, -7.99), "marrakesh": ("MA", 31.63, -7.99),
    "casablanca": ("MA", 33.57, -7.59), "rabat": ("MA", 34.02, -6.84),
    "tangier": ("MA", 35.76, -5.83), "beirut": ("LB", 33.89, 35.50),
    "tel aviv": ("IL", 32.08, 34.78), "jerusalem": ("IL", 31.77, 35.22),
    "amman": ("JO", 31.95, 35.93),
    # German-speaking + Benelux + UK/IE + Nordics
    "berlin": ("DE", 52.52, 13.40), "hamburg": ("DE", 53.55, 10.00),
    "munich": ("DE", 48.14, 11.58), "münchen": ("DE", 48.14, 11.58),
    "cologne": ("DE", 50.94, 6.96), "köln": ("DE", 50.94, 6.96),
    "frankfurt": ("DE", 50.11, 8.68), "stuttgart": ("DE", 48.78, 9.18),
    "leipzig": ("DE", 51.34, 12.37), "dresden": ("DE", 51.05, 13.74),
    "düsseldorf": ("DE", 51.23, 6.77), "dusseldorf": ("DE", 51.23, 6.77),
    "kassel": ("DE", 51.31, 9.49), "bremen": ("DE", 53.08, 8.80),
    "vienna": ("AT", 48.21, 16.37), "wien": ("AT", 48.21, 16.37),
    "salzburg": ("AT", 47.81, 13.04), "linz": ("AT", 48.31, 14.29),
    "zurich": ("CH", 47.37, 8.54), "zürich": ("CH", 47.37, 8.54),
    "geneva": ("CH", 46.20, 6.14), "basel": ("CH", 47.56, 7.59),
    "bern": ("CH", 46.95, 7.45), "amsterdam": ("NL", 52.37, 4.90),
    "rotterdam": ("NL", 51.92, 4.48), "the hague": ("NL", 52.08, 4.31),
    "den haag": ("NL", 52.08, 4.31), "maastricht": ("NL", 50.85, 5.69),
    "eindhoven": ("NL", 51.44, 5.47), "brussels": ("BE", 50.85, 4.35),
    "antwerp": ("BE", 51.22, 4.40), "ghent": ("BE", 51.05, 3.72),
    "london": ("GB", 51.51, -0.13), "glasgow": ("GB", 55.86, -4.25),
    "edinburgh": ("GB", 55.95, -3.19), "manchester": ("GB", 53.48, -2.24),
    "liverpool": ("GB", 53.41, -2.98), "bristol": ("GB", 51.45, -2.59),
    "leeds": ("GB", 53.80, -1.55), "cardiff": ("GB", 51.48, -3.18),
    "belfast": ("GB", 54.60, -5.93), "dublin": ("IE", 53.35, -6.26),
    "copenhagen": ("DK", 55.68, 12.57), "aarhus": ("DK", 56.16, 10.20),
    "stockholm": ("SE", 59.33, 18.07), "gothenburg": ("SE", 57.71, 11.97),
    "malmo": ("SE", 55.60, 13.00), "malmö": ("SE", 55.60, 13.00),
    "oslo": ("NO", 59.91, 10.75), "bergen": ("NO", 60.39, 5.32),
    "helsinki": ("FI", 60.17, 24.94), "turku": ("FI", 60.45, 22.27),
    "reykjavik": ("IS", 64.15, -21.94), "reykjavík": ("IS", 64.15, -21.94),
    # Americas
    "new york": ("US", 40.71, -74.01), "brooklyn": ("US", 40.68, -73.94),
    "los angeles": ("US", 34.05, -118.24), "chicago": ("US", 41.88, -87.63),
    "san francisco": ("US", 37.77, -122.42), "boston": ("US", 42.36, -71.06),
    "seattle": ("US", 47.61, -122.33), "miami": ("US", 25.76, -80.19),
    "detroit": ("US", 42.33, -83.05), "philadelphia": ("US", 39.95, -75.17),
    "houston": ("US", 29.76, -95.37), "new orleans": ("US", 29.95, -90.07),
    "santa fe": ("US", 35.69, -105.94), "minneapolis": ("US", 44.98, -93.27),
    "toronto": ("CA", 43.65, -79.38), "montreal": ("CA", 45.50, -73.57),
    "montréal": ("CA", 45.50, -73.57), "vancouver": ("CA", 49.28, -123.12),
    "banff": ("CA", 51.18, -115.57), "mexico city": ("MX", 19.43, -99.13),
    "oaxaca": ("MX", 17.07, -96.73), "guadalajara": ("MX", 20.67, -103.35),
    "sao paulo": ("BR", -23.55, -46.63), "são paulo": ("BR", -23.55, -46.63),
    "rio de janeiro": ("BR", -22.91, -43.17), "buenos aires": ("AR", -34.60, -58.38),
    "bogota": ("CO", 4.71, -74.07), "bogotá": ("CO", 4.71, -74.07),
    "medellin": ("CO", 6.24, -75.58), "medellín": ("CO", 6.24, -75.58),
    "lima": ("PE", -12.05, -77.04), "santiago": ("CL", -33.45, -70.67),
    "havana": ("CU", 23.11, -82.37),
    # Asia (other)
    "tokyo": ("JP", 35.68, 139.69), "kyoto": ("JP", 35.01, 135.77),
    "osaka": ("JP", 34.69, 135.50), "seoul": ("KR", 37.57, 126.98),
    "busan": ("KR", 35.18, 129.08), "gwangju": ("KR", 35.16, 126.85),
    "beijing": ("CN", 39.90, 116.41), "shanghai": ("CN", 31.23, 121.47),
    "shenzhen": ("CN", 22.54, 114.06), "guangzhou": ("CN", 23.13, 113.26),
    "chengdu": ("CN", 30.57, 104.07), "taipei": ("TW", 25.03, 121.57),
    "new delhi": ("IN", 28.61, 77.21), "delhi": ("IN", 28.61, 77.21),
    "mumbai": ("IN", 19.08, 72.88), "bangalore": ("IN", 12.97, 77.59),
    "bengaluru": ("IN", 12.97, 77.59), "kochi": ("IN", 9.93, 76.27),
    "kolkata": ("IN", 22.57, 88.36), "goa": ("IN", 15.30, 74.12),
    "karachi": ("PK", 24.86, 67.00), "lahore": ("PK", 31.55, 74.34),
    "colombo": ("LK", 6.93, 79.86), "kathmandu": ("NP", 27.72, 85.32),
    "dhaka": ("BD", 23.81, 90.41), "dubai": ("AE", 25.20, 55.27),
    "sharjah": ("AE", 25.35, 55.42), "abu dhabi": ("AE", 24.45, 54.38),
    "doha": ("QA", 25.29, 51.53), "riyadh": ("SA", 24.71, 46.68),
    "jeddah": ("SA", 21.49, 39.19), "tehran": ("IR", 35.69, 51.39),
    # Africa
    "cape town": ("ZA", -33.92, 18.42), "johannesburg": ("ZA", -26.20, 28.05),
    "lagos": ("NG", 6.52, 3.38), "nairobi": ("KE", -1.29, 36.82),
    "accra": ("GH", 5.60, -0.19), "dakar": ("SN", 14.72, -17.47),
    "addis ababa": ("ET", 9.03, 38.74), "kampala": ("UG", 0.35, 32.58),
    # Oceania
    "sydney": ("AU", -33.87, 151.21), "melbourne": ("AU", -37.81, 144.96),
    "brisbane": ("AU", -27.47, 153.03), "hobart": ("AU", -42.88, 147.33),
    "auckland": ("NZ", -36.85, 174.76), "wellington": ("NZ", -41.29, 174.78),
}

# Umbrella region phrases: calls that name a region without any country
# ("open to artists from Southeast Asia", "ASEAN nationals", "the Mekong
# region"). They contribute region groups — so the chip filters find them —
# but no coordinates: a phrase has no honest map pin, and the dashboard's
# "unmapped (N)" note covers that. Matched with word boundaries, so "east
# asia" never fires inside "southeast asia".
REGION_PHRASES = [
    (("southeast asia", "southeast asian", "south east asia", "south east asian",
      "south-east asia", "south-east asian", "asean", "mekong"),
     ("Southeast Asia",)),
    (("central asia", "central asian"), ("Mongolia/Central Asia",)),
    (("eastern europe", "eastern european"), ("Eastern Europe",)),
    (("western balkans", "balkans", "balkan"), ("Balkans",)),
    (("baltic states", "baltics", "baltic"), ("Baltic",)),
    (("mediterranean",), ("Mediterranean",)),
    (("iberian peninsula", "iberian", "iberia"), ("Iberia",)),
    (("east asia", "east asian"), ("East Asia",)),
    (("south asia", "south asian"), ("South Asia",)),
    (("middle east", "middle eastern"), ("Middle East",)),
    (("latin america", "latin american", "south america", "south american"),
     ("Latin America",)),
    (("nordic", "scandinavia", "scandinavian"), ("Nordics",)),
    # deliberately broad — the group filter is recall-friendly by design
    (("asia-pacific", "asia pacific"), ("Southeast Asia", "East Asia", "South Asia", "Oceania")),
]
_PHRASE_PATS = [(re.compile(r"(?<!\w)(" + "|".join(re.escape(p) for p in ps) + r")(?!\w)"),
                 groups) for ps, groups in REGION_PHRASES]

# ISO set used to derive the legacy coarse region (DE / EU / Intl).
EUROPE_ISO = {
    "AL", "BA", "BG", "HR", "GR", "XK", "ME", "MK", "RO", "RS", "SI",
    "EE", "LV", "LT", "ES", "PT", "FR", "PL", "CZ", "SK", "HU", "UA", "MD",
    "BY", "GE", "AM", "AZ", "RU", "DE", "AT", "CH", "NL", "BE", "LU", "GB",
    "IE", "IS", "DK", "SE", "NO", "FI", "IT", "MT", "CY", "TR",
}

# alias -> ("city", city_key) | ("country", iso); one compiled alternation,
# longest alias first so multi-word names beat their substrings.
_ALIAS = {}
for _iso, (_names, _c, _g) in COUNTRIES.items():
    for _n in _names:
        _ALIAS[_n] = ("country", _iso)
for _city, (_iso, _la, _lo) in CITIES.items():
    _ALIAS[_city] = ("city", _city)   # city entries override same-name countries (Singapore)
_PAT = re.compile(
    r"(?<![\w])(" +
    "|".join(re.escape(a) for a in sorted(_ALIAS, key=len, reverse=True)) +
    r")(?![\w])")


def locate(text):
    """Best-effort location of a call from its text.

    Returns None when nothing matched, else a dict:
      country  ISO code of the primary place (first city hit, else first country hit)
      place    city name when a city matched ("" for country-level)
      lat/lon  city coords, or country centroid
      precise  True when pin is city-level
      groups   union of region groups over every country mentioned (recall-
               friendly on purpose: the group filter must not miss a call that
               names several eligible countries)
    """
    t = (text or "").lower()
    if not t:
        return None
    first_city, countries = None, []
    for m in _PAT.finditer(t):
        kind, key = _ALIAS[m.group(1)]
        if kind == "city":
            if first_city is None:
                first_city = key
            iso = CITIES[key][0]
        else:
            iso = key
        if iso not in countries:
            countries.append(iso)
    phrase_groups = [g for pat, groups in _PHRASE_PATS if pat.search(t) for g in groups]
    if not countries and not phrase_groups:
        return None
    if first_city:
        iso, lat, lon = CITIES[first_city]
        place = first_city.title()
    elif countries:
        iso = countries[0]
        lat, lon = COUNTRIES[iso][1]
        place = ""
    else:                       # phrase-only hit: groups yes, pin no
        iso, lat, lon, place = "", None, None, ""
    groups = []
    for c in countries:
        for g in COUNTRIES.get(c, ((), (), ()))[2]:
            if g not in groups:
                groups.append(g)
    for g in phrase_groups:
        if g not in groups:
            groups.append(g)
    return {"country": iso, "place": place, "lat": lat, "lon": lon,
            "precise": first_city is not None, "groups": groups}
