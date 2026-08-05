"""
Comprehensive mock data generator for merge-engine testing.

Generates realistic test data with edge cases for:
- PostgreSQL origins (2 databases)
- MongoDB origin
- PostgreSQL destination

Entity types: companies, contacts, products, orders
"""

import random
import uuid
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

random.seed(42)


def random_uuid() -> str:
    return str(uuid.UUID(int=random.getrandbits(128), version=4))


def random_email(first: str, last: str, variant: str = "original") -> str:
    """Generate email with various edge-case formats."""
    base = f"{first.lower()}.{last.lower()}"
    domain = random.choice(["gmail.com", "yahoo.com", "outlook.com", "company.org", "email.com"])

    if variant == "dots_removed":
        base = base.replace(".", "")
    elif variant == "plus_addressing":
        base = f"{base}+test{random.randint(1, 999)}"
    elif variant == "no_dot":
        base = f"{first.lower()}{last.lower()}"
    elif variant == "reversed":
        base = f"{last.lower()}.{first.lower()}"

    return f"{base}@{domain}"


def random_phone(variant: str = "original") -> str:
    """Generate phone numbers in various formats."""
    area = random.randint(200, 999)
    prefix = random.randint(200, 999)
    line = random.randint(1000, 9999)

    if variant == "dashes":
        return f"{area}-{prefix}-{line}"
    elif variant == "dots":
        return f"{area}.{prefix}.{line}"
    elif variant == "international":
        return f"+1{area}{prefix}{line}"
    elif variant == "parens":
        return f"({area}) {prefix}-{line}"
    elif variant == "no_country":
        return f"{area}-{prefix}-{line}"
    else:
        return f"+1-{area}-{prefix}-{line}"


def random_address(number: int = None) -> dict:
    """Generate a random address with edge-case variations."""
    streets = [
        "Main St",
        "Main Street",
        "Main St.",
        "Oak Avenue",
        "Oak Ave.",
        "Oak Avenue.",
        "Elm Boulevard",
        "Elm Blvd",
        "Elm Blvd.",
        "Broadway",
        "Broadway",
        "Broadway",
        "Park Road",
        "Park Rd.",
        "Park Road.",
    ]
    cities = [
        "New York",
        "Los Angeles",
        "Chicago",
        "Houston",
        "Phoenix",
        "Philadelphia",
        "San Antonio",
        "San Diego",
        "Dallas",
        "San Jose",
    ]
    states = [
        "NY",
        "New York",
        "CA",
        "California",
        "IL",
        "Illinois",
        "TX",
        "Texas",
        "AZ",
        "Arizona",
    ]

    if number is None:
        number = random.randint(100, 9999)

    street = random.choice(streets)
    city = random.choice(cities)
    state = random.choice(states)
    zip_code = f"{random.randint(10000, 99999)}"

    return {
        "street": f"{number} {street}",
        "city": city,
        "state": state,
        "zip": zip_code,
        "country": "US",
    }


FIRST_NAMES_MALE = [
    "James",
    "John",
    "Robert",
    "Michael",
    "William",
    "David",
    "Richard",
    "Joseph",
    "Thomas",
    "Charles",
    "Christopher",
    "Daniel",
    "Matthew",
    "Anthony",
    "Mark",
    "Donald",
    "Steven",
    "Paul",
    "Andrew",
    "Joshua",
    "Robert",
    "Kenneth",
    "George",
    "Timothy",
    "Ronald",
    "Edward",
    "Jason",
    "Jeffrey",
    "Ryan",
    "Jacob",
    "Gary",
    "Nicholas",
    "Eric",
    "Jonathan",
    "Stephen",
    "Larry",
    "Justin",
    "Scott",
    "Brandon",
    "Benjamin",
    "Bob",
    "Bobby",
    "Rob",
    "Mick",
    "Mike",
    "Dick",
    "Rick",
]

FIRST_NAMES_FEMALE = [
    "Mary",
    "Patricia",
    "Jennifer",
    "Linda",
    "Barbara",
    "Elizabeth",
    "Susan",
    "Jessica",
    "Sarah",
    "Karen",
    "Nancy",
    "Lisa",
    "Betty",
    "Margaret",
    "Sandra",
    "Ashley",
    "Dorothy",
    "Kimberly",
    "Emily",
    "Donna",
    "Michelle",
    "Carol",
    "Amanda",
    "Melissa",
    "Deborah",
    "Stephanie",
    "Rebecca",
    "Sharon",
    "Laura",
    "Cynthia",
    "Kathleen",
    "Amy",
    "Angela",
    "Brenda",
    "Amy",
    "Anna",
    "Sarah",
    "Sally",
    "Betsy",
    "Betty",
    "Tina",
    "Liz",
    "Lizzy",
    "Megan",
    "Meg",
]

LAST_NAMES = [
    "Smith",
    "Johnson",
    "Williams",
    "Brown",
    "Jones",
    "Garcia",
    "Miller",
    "Davis",
    "Rodriguez",
    "Martinez",
    "Hernandez",
    "Lopez",
    "Gonzalez",
    "Wilson",
    "Anderson",
    "Thomas",
    "Taylor",
    "Moore",
    "Jackson",
    "Martin",
    "Lee",
    "Perez",
    "Thompson",
    "White",
    "Harris",
    "Sanchez",
    "Clark",
    "Ramirez",
    "Lewis",
    "Robinson",
    "Walker",
    "Young",
    "Allen",
    "King",
    "Wright",
    "Scott",
    "Torres",
    "Nguyen",
    "Hill",
    "Flores",
    "Von",
    "Van",
    "De",
    "La",
    "El",
]

COMPANY_PREFIXES = [
    "Acme",
    "Global",
    "International",
    "United",
    "Premier",
    "Elite",
    "Apex",
    "Summit",
    "Pinnacle",
    "Cornerstone",
    "Blue Sky",
    "Sunrise",
    "Pacific",
    "Atlantic",
    "Metro",
]

COMPANY_SUFFIXES = [
    "Corporation",
    "Corp.",
    "Corporations",
    "LLC",
    "L.L.C.",
    "Inc.",
    "Incorporated",
    "Ltd.",
    "Limited",
    "Group",
    "Holdings",
    "Enterprises",
    "Solutions",
    "Technologies",
    "Systems",
    "Services",
]

PRODUCT_CATEGORIES = [
    "Electronics",
    "Software",
    "Hardware",
    "Services",
    "Supplies",
    "Furniture",
    "Clothing",
    "Food & Beverage",
    "Automotive",
    "Medical",
]

PRODUCT_NAMES = [
    "Widget A",
    "Widget B",
    "Gadget X",
    "Gadget Y",
    "Tool Pro",
    "Device Plus",
    "System Ultra",
    "Pack Basic",
    "Pack Premium",
    "Connector Type-A",
    "Connector Type-B",
    "Adapter Dual-Port",
    "Cable USB-C 3ft",
    "Cable HDMI 6ft",
    "Cable Ethernet 10ft",
    "Mount Wall",
    "Mount Desk",
    "Stand Floor",
]

ORDER_STATUSES = ["pending", "confirmed", "shipped", "delivered", "cancelled", "returned"]

# ---------------------------------------------------------------------------
# Companies
# ---------------------------------------------------------------------------


def generate_company_name(variant: str = "original") -> tuple[str, str]:
    """Generate a company name. Returns (display_name, canonical_form)."""
    prefix = random.choice(COMPANY_PREFIXES)
    suffix = random.choice(COMPANY_SUFFIXES)
    name = f"{prefix} {suffix}"

    if variant == "all_caps":
        name = name.upper()
    elif variant == "lowercase":
        name = name.lower()
    elif variant == "abbreviated":
        name = f"{prefix} {suffix[:3]}." if len(suffix) > 3 else f"{prefix} {suffix}"
    elif variant == "no_dot":
        name = suffix.replace(".", "")
        name = f"{prefix} {name}"

    canonical = f"{prefix.lower()} {suffix.lower().replace('.', '')}"
    return name, canonical


def generate_companies(count: int = 200) -> list[dict]:
    """Generate company records with duplicate edge cases."""
    companies = []

    # --- Normal unique companies ---
    for i in range(count):
        name, canonical = generate_company_name("original")
        companies.append(
            {
                "id": random_uuid(),
                "name": name,
                "legal_name": f"{name} Legal Entity",
                "phone": random_phone("original"),
                "email": f"info@{canonical.replace(' ', '')}.com",
                "website": f"https://{canonical.replace(' ', '')}.com",
                "industry": random.choice(
                    ["Technology", "Healthcare", "Finance", "Retail", "Manufacturing"]
                ),
                "employee_count": random.randint(10, 10000),
                "annual_revenue": random.randint(100000, 100000000),
                "address": random_address(),
                "created_at": (datetime.now() - timedelta(days=random.randint(1, 730))).isoformat(),
                "updated_at": (datetime.now() - timedelta(days=random.randint(0, 30))).isoformat(),
                "record_status": "active",
                "source_id": None,  # will be set per-origin
            }
        )

    # --- Duplicate edge cases: same canonical name, different display ---
    edge_companies = []
    for i in range(30):
        name, canonical = generate_company_name("original")
        display_variants = [
            name.upper(),
            name.lower(),
            name.title().replace(".", ""),
            name.replace(" Corporation", " LLC"),
            name.replace("Inc.", "Ltd."),
            f"{name}",
        ]
        for j, display_name in enumerate(display_variants[:4]):
            edge_companies.append(
                {
                    "id": random_uuid(),
                    "name": display_name,
                    "legal_name": f"{display_name} Legal Entity",
                    "phone": random_phone("dashes" if j == 0 else "original"),
                    "email": f"contact{random.randint(1, 99)}@{canonical.replace(' ', '')}.com",
                    "address": random_address(),
                    "source_id": None,
                    "industry": random.choice(["Technology", "Finance"]),
                    "employee_count": random.randint(50, 5000),
                    "created_at": (
                        datetime.now() - timedelta(days=random.randint(1, 730))
                    ).isoformat(),
                    "updated_at": (
                        datetime.now() - timedelta(days=random.randint(0, 30))
                    ).isoformat(),
                    "record_status": "active",
                }
            )

    companies.extend(edge_companies)

    # --- Near-duplicates: similar but different names ---
    for i in range(10):
        name, _ = generate_company_name("original")
        companies.append(
            {
                "id": random_uuid(),
                "name": f"{name} II",
                "phone": random_phone("original"),
                "email": f"info2@similar{random.randint(1, 999)}.com",
                "address": random_address(),
                "source_id": None,
                "industry": random.choice(["Technology", "Retail"]),
                "employee_count": random.randint(10, 1000),
                "created_at": (datetime.now() - timedelta(days=random.randint(1, 730))).isoformat(),
                "updated_at": (datetime.now() - timedelta(days=random.randint(0, 30))).isoformat(),
                "record_status": "active",
            }
        )
        companies.append(
            {
                "id": random_uuid(),
                "name": f"{name} Inc.",
                "phone": random_phone("original"),
                "email": f"info3@similar{random.randint(1, 999)}.com",
                "address": random_address(),
                "source_id": None,
                "industry": random.choice(["Technology", "Retail"]),
                "employee_count": random.randint(10, 1000),
                "created_at": (datetime.now() - timedelta(days=random.randint(1, 730))).isoformat(),
                "updated_at": (datetime.now() - timedelta(days=random.randint(0, 30))).isoformat(),
                "record_status": "active",
            }
        )

    # --- Null/empty field edge cases ---
    for i in range(5):
        companies.append(
            {
                "id": random_uuid(),
                "name": None,
                "phone": None,
                "email": None,
                "address": None,
                "source_id": None,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "record_status": "active",
            }
        )

    # --- Empty string edge cases ---
    for i in range(5):
        companies.append(
            {
                "id": random_uuid(),
                "name": "",
                "phone": "",
                "email": "",
                "address": {},
                "source_id": None,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "record_status": "active",
            }
        )

    return companies


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------


def generate_contact(first: str = None, last: str = None, variant: str = "original") -> dict:
    """Generate a contact record."""
    if first is None:
        first = random.choice(FIRST_NAMES_MALE + FIRST_NAMES_FEMALE)
    if last is None:
        last = random.choice(LAST_NAMES)

    email = random_email(first, last, variant=variant)
    phone = random_phone(variant=variant)

    # Nickname variations
    nicknames_map = {
        "Robert": ["Bob", "Rob", "Bobby"],
        "William": ["Bill", "Billy", "Will"],
        "James": ["Jim", "Jimmy"],
        "Richard": ["Dick", "Rick"],
        "Elizabeth": ["Liz", "Lizzy", "Beth", " Ellie"],
        "Jennifer": ["Jenny", "Jen"],
        "Jessica": ["Jess"],
        "Nicholas": ["Nick"],
        "Daniel": ["Dan", "Danny"],
        "Michael": ["Mike"],
        "Christopher": ["Chris"],
        "Matthew": ["Matt"],
        "Anthony": ["Tony"],
        "Mark": ["Marc"],
        "Steven": ["Steve"],
        "Andrew": ["Andy"],
        "Benjamin": ["Ben"],
        "Joseph": ["Joe"],
        "Patricia": ["Pat", "Patty"],
        "Kathleen": ["Kathy", "Kathy"],
        "Margaret": ["Meg", "Maggie"],
        "Thomas": ["Tom"],
        "Charles": ["Charlie"],
        "Stephen": ["Steve"],
        "Timothy": ["Tim"],
        "Jonathan": ["Jon"],
    }

    nicks = nicknames_map.get(first, [])
    nick = random.choice(nicks) if nicks else None

    return {
        "id": random_uuid(),
        "first_name": first,
        "last_name": last,
        "middle_name": random.choice(["A", "B", "C", None, None, None]),  # 50% null
        "nickname": nick,
        "full_name": f"{first} {last}" if not nick else f"{nick} {last}",
        "email": email,
        "emails": [email, random_email(first, last, "plus_addressing")]
        if variant == "original"
        else [email],
        "phone": phone,
        "phones": [phone, random_phone()] if variant == "original" else [phone],
        "title": random.choice(
            [
                "Software Engineer",
                "Product Manager",
                "Sales Director",
                "VP of Engineering",
                "CTO",
                "CEO",
                "Marketing Manager",
                "HR Director",
                "Financial Analyst",
                "Operations Manager",
            ]
        ),
        "company_id": None,  # linked later
        "address": random_address(),
        "linkedin": f"https://linkedin.com/in/{first.lower()}-{last.lower()}"
        if variant == "original"
        else None,
        "date_of_birth": None,  # some will have
        "gender": random.choice(["M", "F", None, None]),
        "notes": random.choice(
            [
                None,
                "",
                "Preferred contact method: email",
                "Met at conference 2024",
                f"Referral from {random.choice(LAST_NAMES)}",
            ]
        ),
        "source_id": None,
        "created_at": (datetime.now() - timedelta(days=random.randint(1, 730))).isoformat(),
        "updated_at": (datetime.now() - timedelta(days=random.randint(0, 30))).isoformat(),
        "record_status": random.choice(["active", "active", "active", "inactive", "unsubscribed"]),
    }


def generate_contacts(count: int = 500) -> list[dict]:
    """Generate contact records with extensive duplicate edge cases."""
    contacts = []

    # --- Normal unique contacts ---
    for _ in range(count):
        c = generate_contact()
        contacts.append(c)

    # --- Same email, different name casing ---
    base_first = "John"
    base_last = "Smith"
    for i, variant in enumerate(["original", "dots_removed", "plus_addressing", "no_dot"]):
        c = generate_contact(base_first, base_last, variant=variant)
        c["first_name"] = (
            base_first
            if variant == "original"
            else (base_first.upper() if i == 1 else base_first.lower())
        )
        c["last_name"] = (
            base_last
            if variant == "original"
            else (base_last.upper() if i == 1 else base_last.lower())
        )
        contacts.append(c)

    # --- Same name, different emails (one person, multiple emails) ---
    for i in range(15):
        first = random.choice(FIRST_NAMES_MALE)
        last = random.choice(LAST_NAMES)
        base_email = random_email(first, last)
        for j in range(3):
            c = generate_contact(first, last)
            c["email"] = f"{first.lower()}.{last.lower()}{j}@{base_email.split('@')[1]}"
            c["emails"] = [c["email"]]
            contacts.append(c)

    # --- Nickname vs full name ---
    for first_full, nicks in [("Robert", ["Bob", "Rob", "Bobby"]), ("William", ["Bill", "Will"])]:
        last = random.choice(LAST_NAMES)
        for nick in nicks[:2]:
            c = generate_contact(nick, last)
            c["nickname"] = nick
            c["first_name"] = nick
            contacts.append(c)
        # Also add the full name version
        c = generate_contact(first_full, last)
        contacts.append(c)

    # --- Transposed names (first/last swapped) ---
    for _ in range(10):
        first = random.choice(FIRST_NAMES_MALE)
        last = random.choice(LAST_NAMES)
        c1 = generate_contact(first, last)
        c2 = generate_contact(last, first)  # transposed!
        c2["email"] = random_email(last, first)
        contacts.append(c1)
        contacts.append(c2)

    # --- Missing/null fields ---
    for _ in range(10):
        c = generate_contact()
        c["email"] = None
        c["phone"] = None
        c["address"] = None
        contacts.append(c)

    # --- Empty strings ---
    for _ in range(5):
        c = generate_contact()
        c["email"] = ""
        c["phone"] = ""
        contacts.append(c)

    # --- Special characters in names ---
    special_names = [
        ("José", "García"),
        ("María", "López"),
        ("Björk", "Gunnarsdóttir"),
        ("François", "Durand"),
        ("René", "van der Berg"),
        ("O'Connor", "Smith"),
        ("D'Angelo", "Brown"),
    ]
    for first, last in special_names:
        c = generate_contact(first, last)
        contacts.append(c)

    # --- Different data types for same field (simulating MongoDB vs Postgres) ---
    for i in range(10):
        first = random.choice(FIRST_NAMES_MALE)
        last = random.choice(LAST_NAMES)
        c = generate_contact(first, last)
        c["phone"] = str(random.randint(1000000000, 9999999999))  # phone as plain number
        contacts.append(c)

    return contacts


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------


def generate_products(count: int = 300) -> list[dict]:
    """Generate product records with SKU variations and similar descriptions."""
    products = []

    for i in range(count):
        category = random.choice(PRODUCT_CATEGORIES)
        name = random.choice(PRODUCT_NAMES)
        sku_base = f"SKU-{category[:3].upper()}-{random.randint(1000, 9999)}"

        products.append(
            {
                "id": random_uuid(),
                "name": name,
                "description": f"High-quality {name.lower()} in {category.lower()} category",
                "sku": sku_base,
                "category": category,
                "price": round(random.uniform(9.99, 999.99), 2),
                "cost": round(random.uniform(5.00, 500.00), 2),
                "weight": round(random.uniform(0.1, 50.0), 2),
                "units": random.choice(["pcs", "kg", "lbs", "m", "ft"]),
                "stock_quantity": random.randint(0, 10000),
                "reorder_level": random.randint(10, 100),
                "supplier": random.choice(COMPANY_PREFIXES) + " Supplies",
                "created_at": (datetime.now() - timedelta(days=random.randint(1, 730))).isoformat(),
                "updated_at": (datetime.now() - timedelta(days=random.randint(0, 30))).isoformat(),
                "record_status": random.choice(["active", "active", "discontinued", "draft"]),
                "source_id": None,
                "specifications": {
                    "color": random.choice(["Red", "Blue", "Green", "Black", "White", None]),
                    "size": random.choice(["Small", "Medium", "Large", "XL", None]),
                    "material": random.choice(["Plastic", "Metal", "Wood", "Fabric", None]),
                },
            }
        )

    # --- SKU variations (same product, different SKU representation) ---
    for _ in range(15):
        name = random.choice(PRODUCT_NAMES)
        base_sku = f"SKU-{random.randint(1000, 9999)}"
        variations = [
            base_sku.upper(),
            base_sku.lower(),
            base_sku.replace("-", "_"),
            base_sku.replace("-", "").lower(),
            f"  {base_sku}  ",  # leading/trailing spaces
        ]
        for sku in variations[:4]:
            products.append(
                {
                    "id": random_uuid(),
                    "name": name,
                    "description": f"{name} description",
                    "sku": sku,
                    "category": "Hardware",
                    "price": round(random.uniform(9.99, 999.99), 2),
                    "cost": round(random.uniform(5.0, 500.0), 2),
                    "stock_quantity": random.randint(0, 10000),
                    "source_id": None,
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                    "record_status": "active",
                }
            )

    # --- Similar names (differ by one word) ---
    for _ in range(10):
        base = random.choice(PRODUCT_NAMES)
        products.append(
            {
                "id": random_uuid(),
                "name": base,
                "sku": f"SKU-NEAR-{random.randint(1000, 9999)}",
                "category": random.choice(PRODUCT_CATEGORIES),
                "price": round(random.uniform(9.99, 999.99), 2),
                "source_id": None,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "record_status": "active",
            }
        )
        products.append(
            {
                "id": random_uuid(),
                "name": f"{base} Plus",
                "sku": f"SKU-NEAR-{random.randint(1000, 9999)}",
                "category": random.choice(PRODUCT_CATEGORIES),
                "price": round(random.uniform(9.99, 999.99), 2),
                "source_id": None,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "record_status": "active",
            }
        )

    # --- Null/empty ---
    for _ in range(5):
        products.append(
            {
                "id": random_uuid(),
                "name": None,
                "sku": None,
                "price": None,
                "source_id": None,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "record_status": "draft",
            }
        )

    return products


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


def generate_orders(
    contacts: list[dict],
    companies: list[dict],
    products: list[dict],
    count: int = 400,
) -> list[dict]:
    """Generate order records linked to contacts and companies."""
    orders = []
    active_contacts = [c for c in contacts if c.get("email")]
    active_companies = [c for c in companies if c.get("name")]
    active_products = [p for p in products if p.get("sku")]

    for i in range(count):
        contact = random.choice(active_contacts) if active_contacts else None
        company = random.choice(active_companies) if active_companies else None
        num_items = random.randint(1, 5)
        items = []
        for _ in range(num_items):
            product = random.choice(active_products) if active_products else None
            if product:
                items.append(
                    {
                        "product_id": product["id"],
                        "product_name": product["name"],
                        "sku": product["sku"],
                        "quantity": random.randint(1, 100),
                        "unit_price": product.get("price", random.uniform(9.99, 999.99)),
                        "total": round(random.uniform(10, 10000), 2),
                    }
                )

        total = sum(item["total"] for item in items)

        orders.append(
            {
                "id": random_uuid(),
                "order_number": f"ORD-{random.randint(100000, 999999)}",
                "contact_id": contact["id"] if contact else None,
                "company_id": company["id"] if company else None,
                "items": items,
                "subtotal": round(total, 2),
                "tax": round(total * 0.08, 2),
                "shipping": round(random.uniform(5, 50), 2),
                "total": round(total * 1.08 + random.uniform(5, 50), 2),
                "currency": "USD",
                "status": random.choice(ORDER_STATUSES),
                "shipping_address": random_address(),
                "billing_address": random_address(),
                "notes": random.choice([None, "", "Rush delivery", "Gift wrap"]),
                "placed_at": (datetime.now() - timedelta(days=random.randint(1, 365))).isoformat(),
                "shipped_at": None,
                "delivered_at": None,
                "source_id": None,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "record_status": "active",
            }
        )

    return orders


# ---------------------------------------------------------------------------
# MongoDB-specific documents (with nested/embedded structures)
# ---------------------------------------------------------------------------


def generate_mongodb_contacts(contacts: list[dict]) -> list[dict]:
    """Transform contact records into MongoDB-style documents with nested structures."""
    docs = []
    for c in contacts[:100]:  # subset for MongoDB
        doc = {
            "_id": uuid.UUID(c["id"]),
            "personalInfo": {
                "firstName": c.get("first_name"),
                "lastName": c.get("last_name"),
                "middleName": c.get("middle_name"),
                "nickName": c.get("nickname"),
            },
            "contactInfo": {
                "primaryEmail": c.get("email"),
                "secondaryEmails": c.get("emails", [])[1:],
                "primaryPhone": c.get("phone"),
                "alternatePhones": c.get("phones", [])[1:],
            },
            "professionalInfo": {
                "title": c.get("title"),
                "companyRef": c.get("company_id"),
            },
            "location": c.get("address"),
            "social": {
                "linkedin": c.get("linkedin"),
            },
            "meta": {
                "createdAt": c.get("created_at"),
                "updatedAt": c.get("updated_at"),
                "status": c.get("record_status"),
                "source": "mongodb_origin",
            },
            "tags": random.sample(
                ["vip", "lead", "customer", "partner", "influencer", "prospect"],
                k=random.randint(0, 3),
            ),
        }
        docs.append(doc)
    return docs


def generate_mongodb_companies(companies: list[dict]) -> list[dict]:
    """Transform company records into MongoDB-style documents."""
    docs = []
    for c in companies[:80]:
        doc = {
            "_id": uuid.UUID(c["id"]),
            "businessInfo": {
                "displayName": c.get("name"),
                "legalName": c.get("legal_name"),
                "industry": c.get("industry"),
                "employeeCount": c.get("employee_count"),
                "annualRevenue": c.get("annual_revenue"),
            },
            "contactInfo": {
                "phone": c.get("phone"),
                "email": c.get("email"),
                "website": c.get("website"),
            },
            "address": c.get("address"),
            "meta": {
                "createdAt": c.get("created_at"),
                "updatedAt": c.get("updated_at"),
                "status": c.get("record_status"),
                "source": "mongodb_origin",
            },
            "socialProfiles": {
                "linkedin": f"https://linkedin.com/company/{c.get('name', 'unknown').lower().replace(' ', '-')}"
                if c.get("name")
                else None,
                "twitter": f"https://twitter.com/{c.get('name', 'unknown').lower().replace(' ', '')}"
                if c.get("name")
                else None,
            },
        }
        docs.append(doc)
    return docs


def generate_mongodb_products(products: list[dict]) -> list[dict]:
    """Transform product records into MongoDB-style documents with embedded specs."""
    docs = []
    for p in products[:100]:
        doc = {
            "_id": uuid.UUID(p["id"]),
            "productInfo": {
                "name": p.get("name"),
                "description": p.get("description"),
                "sku": p.get("sku"),
                "category": p.get("category"),
            },
            "pricing": {
                "retailPrice": p.get("price"),
                "cost": p.get("cost"),
                "currency": "USD",
            },
            "inventory": {
                "weight": p.get("weight"),
                "units": p.get("units"),
                "stockQuantity": p.get("stock_quantity"),
                "reorderLevel": p.get("reorder_level"),
                "supplier": p.get("supplier"),
            },
            "specifications": p.get("specifications", {}),
            "meta": {
                "createdAt": p.get("created_at"),
                "updatedAt": p.get("updated_at"),
                "status": p.get("record_status"),
                "source": "mongodb_origin",
            },
        }
        docs.append(doc)
    return docs


# ---------------------------------------------------------------------------
# Cross-origin linkage: assign same source entities to different origins
# ---------------------------------------------------------------------------


def assign_source_ids(
    companies: list[dict],
    contacts: list[dict],
    products: list[dict],
    orders: list[dict],
    postgres_origin_id: str = "origin_postgres_1",
    mongodb_origin_id: str = "origin_mongodb_1",
    postgres_origin_2_id: str = "origin_postgres_2",
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """
    Split records across origins, with intentional overlaps for dedup testing.

    Strategy:
    - ~60% of records appear in BOTH origins (true duplicates to detect)
    - ~20% are unique to origin 1
    - ~20% are unique to origin 2 (MongoDB)
    """

    def split_records(records: list[dict]) -> tuple[list, list, list]:
        n = len(records)
        both = records[: int(n * 0.6)]
        origin1_only = records[int(n * 0.6) : int(n * 0.8)]
        origin2_only = records[int(n * 0.8) :]
        return both, origin1_only, origin2_only

    companies_both, comp1_only, comp2_only = split_records(companies)
    contacts_both, cont1_only, cont2_only = split_records(contacts)
    products_both, prod1_only, prod2_only = split_records(products)

    for c in companies_both:
        c["source_id"] = postgres_origin_id
    for c in comp1_only:
        c["source_id"] = postgres_origin_id
    for c in comp2_only:
        c["source_id"] = mongodb_origin_id

    for c in contacts_both:
        c["source_id"] = postgres_origin_id
    for c in cont1_only:
        c["source_id"] = postgres_origin_id
    for c in cont2_only:
        c["source_id"] = mongodb_origin_id

    for p in products_both:
        p["source_id"] = postgres_origin_id
    for p in prod1_only:
        p["source_id"] = postgres_origin_id
    for p in prod2_only:
        p["source_id"] = mongodb_origin_id

    # Orders: link to postgres-origin contacts/companies, stored in MongoDB
    for o in orders:
        o["source_id"] = mongodb_origin_id
        # Update contact/company references based on source
        if o.get("contact_id"):
            for c in contacts_both + cont1_only:
                if c["id"] == o["contact_id"]:
                    o["contact_id"] = c["id"]
                    break
        if o.get("company_id"):
            for c in companies_both + comp1_only:
                if c["id"] == o["company_id"]:
                    o["company_id"] = c["id"]
                    break

    return (
        companies_both + comp1_only,
        companies_both + comp2_only,
        contacts_both + cont1_only,
        contacts_both + cont2_only,
        products_both + prod1_only,
        products_both + prod2_only,
        orders,
    )


# ---------------------------------------------------------------------------
# PostgreSQL schema representations
# ---------------------------------------------------------------------------


def get_postgres_company_schema() -> dict:
    return {
        "table_name": "companies",
        "columns": {
            "id": "UUID PRIMARY KEY",
            "name": "VARCHAR(255)",
            "legal_name": "VARCHAR(255)",
            "phone": "VARCHAR(50)",
            "email": "VARCHAR(255)",
            "website": "VARCHAR(500)",
            "industry": "VARCHAR(100)",
            "employee_count": "INTEGER",
            "annual_revenue": "NUMERIC(15,2)",
            "street": "VARCHAR(255)",
            "city": "VARCHAR(100)",
            "state": "VARCHAR(100)",
            "zip_code": "VARCHAR(20)",
            "country": "VARCHAR(100) DEFAULT 'US'",
            "created_at": "TIMESTAMPTZ",
            "updated_at": "TIMESTAMPTZ",
            "record_status": "VARCHAR(50)",
            "source_id": "VARCHAR(100)",
        },
    }


def get_postgres_contact_schema() -> dict:
    return {
        "table_name": "contacts",
        "columns": {
            "id": "UUID PRIMARY KEY",
            "first_name": "VARCHAR(100)",
            "last_name": "VARCHAR(100)",
            "middle_name": "VARCHAR(100)",
            "nickname": "VARCHAR(100)",
            "full_name": "VARCHAR(255)",
            "email": "VARCHAR(255)",
            "phone": "VARCHAR(50)",
            "title": "VARCHAR(200)",
            "company_id": "UUID REFERENCES companies(id)",
            "street": "VARCHAR(255)",
            "city": "VARCHAR(100)",
            "state": "VARCHAR(100)",
            "zip_code": "VARCHAR(20)",
            "country": "VARCHAR(100) DEFAULT 'US'",
            "linkedin": "VARCHAR(500)",
            "date_of_birth": "DATE",
            "gender": "CHAR(1)",
            "notes": "TEXT",
            "source_id": "VARCHAR(100)",
            "created_at": "TIMESTAMPTZ",
            "updated_at": "TIMESTAMPTZ",
            "record_status": "VARCHAR(50)",
        },
    }


def get_postgres_product_schema() -> dict:
    return {
        "table_name": "products",
        "columns": {
            "id": "UUID PRIMARY KEY",
            "name": "VARCHAR(255)",
            "description": "TEXT",
            "sku": "VARCHAR(100) UNIQUE",
            "category": "VARCHAR(100)",
            "price": "NUMERIC(10,2)",
            "cost": "NUMERIC(10,2)",
            "weight": "NUMERIC(8,2)",
            "units": "VARCHAR(20)",
            "stock_quantity": "INTEGER",
            "reorder_level": "INTEGER",
            "supplier": "VARCHAR(255)",
            "specifications": "JSONB",
            "source_id": "VARCHAR(100)",
            "created_at": "TIMESTAMPTZ",
            "updated_at": "TIMESTAMPTZ",
            "record_status": "VARCHAR(50)",
        },
    }


def get_postgres_order_schema() -> dict:
    return {
        "table_name": "orders",
        "columns": {
            "id": "UUID PRIMARY KEY",
            "order_number": "VARCHAR(50) UNIQUE",
            "contact_id": "UUID REFERENCES contacts(id)",
            "company_id": "UUID REFERENCES companies(id)",
            "items": "JSONB",
            "subtotal": "NUMERIC(12,2)",
            "tax": "NUMERIC(12,2)",
            "shipping": "NUMERIC(12,2)",
            "total": "NUMERIC(12,2)",
            "currency": "VARCHAR(10) DEFAULT 'USD'",
            "status": "VARCHAR(50)",
            "shipping_street": "VARCHAR(255)",
            "shipping_city": "VARCHAR(100)",
            "shipping_state": "VARCHAR(100)",
            "shipping_zip": "VARCHAR(20)",
            "billing_street": "VARCHAR(255)",
            "billing_city": "VARCHAR(100)",
            "billing_state": "VARCHAR(100)",
            "billing_zip": "VARCHAR(20)",
            "notes": "TEXT",
            "placed_at": "TIMESTAMPTZ",
            "shipped_at": "TIMESTAMPTZ",
            "delivered_at": "TIMESTAMPTZ",
            "source_id": "VARCHAR(100)",
            "created_at": "TIMESTAMPTZ",
            "updated_at": "TIMESTAMPTZ",
            "record_status": "VARCHAR(50)",
        },
    }


def get_mongodb_contact_schema() -> dict:
    return {
        "collection": "contacts",
        "fields": {
            "_id": "ObjectId",
            "personalInfo.firstName": "String",
            "personalInfo.lastName": "String",
            "personalInfo.middleName": "String (nullable)",
            "personalInfo.nickName": "String",
            "contactInfo.primaryEmail": "String",
            "contactInfo.secondaryEmails": "String[]",
            "contactInfo.primaryPhone": "String",
            "contactInfo.alternatePhones": "String[]",
            "professionalInfo.title": "String",
            "professionalInfo.companyRef": "ObjectId (nullable)",
            "location": "Object {street, city, state, zip, country}",
            "social.linkedin": "String",
            "meta.createdAt": "Date",
            "meta.updatedAt": "Date",
            "meta.status": "String",
            "meta.source": "String",
            "tags": "String[]",
        },
    }


def get_mongodb_company_schema() -> dict:
    return {
        "collection": "companies",
        "fields": {
            "_id": "ObjectId",
            "businessInfo.displayName": "String",
            "businessInfo.legalName": "String",
            "businessInfo.industry": "String",
            "businessInfo.employeeCount": "Number",
            "businessInfo.annualRevenue": "Number",
            "contactInfo.phone": "String",
            "contactInfo.email": "String",
            "contactInfo.website": "String",
            "address": "Object {street, city, state, zip, country}",
            "meta.createdAt": "Date",
            "meta.updatedAt": "Date",
            "meta.status": "String",
            "meta.source": "String",
            "socialProfiles.linkedin": "String",
            "socialProfiles.twitter": "String",
        },
    }


def get_mongodb_product_schema() -> dict:
    return {
        "collection": "products",
        "fields": {
            "_id": "ObjectId",
            "productInfo.name": "String",
            "productInfo.description": "String",
            "productInfo.sku": "String",
            "productInfo.category": "String",
            "pricing.retailPrice": "Number",
            "pricing.cost": "Number",
            "pricing.currency": "String",
            "inventory.weight": "Number",
            "inventory.units": "String",
            "inventory.stockQuantity": "Number",
            "inventory.reorderLevel": "Number",
            "inventory.supplier": "String",
            "specifications": "Object",
            "meta.createdAt": "Date",
            "meta.updatedAt": "Date",
            "meta.status": "String",
            "meta.source": "String",
        },
    }


# ---------------------------------------------------------------------------
# Destination schema (PostgreSQL)
# ---------------------------------------------------------------------------


def get_destination_company_schema() -> dict:
    return {
        "database": "merge_destination",
        "schema_name": "public",
        "table_name": "companies",
        "columns": {
            "id": "UUID PRIMARY KEY",
            "name": "VARCHAR(255)",
            "legal_name": "VARCHAR(255)",
            "phone": "VARCHAR(50)",
            "email": "VARCHAR(255)",
            "website": "VARCHAR(500)",
            "industry": "VARCHAR(100)",
            "employee_count": "INTEGER",
            "annual_revenue": "NUMERIC(15,2)",
            "street": "VARCHAR(255)",
            "city": "VARCHAR(100)",
            "state": "VARCHAR(100)",
            "zip_code": "VARCHAR(20)",
            "country": "VARCHAR(100)",
            "source_ids": "TEXT[]",
            "origin_names": "TEXT[]",
            "last_updated": "TIMESTAMPTZ",
            "record_status": "VARCHAR(50)",
            "merged_at": "TIMESTAMPTZ",
            "merge_version": "INTEGER DEFAULT 1",
        },
    }


def get_destination_contact_schema() -> dict:
    return {
        "database": "merge_destination",
        "schema_name": "public",
        "table_name": "contacts",
        "columns": {
            "id": "UUID PRIMARY KEY",
            "first_name": "VARCHAR(100)",
            "last_name": "VARCHAR(100)",
            "middle_name": "VARCHAR(100)",
            "nickname": "VARCHAR(100)",
            "full_name": "VARCHAR(255)",
            "email": "VARCHAR(255)",
            "emails": "TEXT[]",
            "phone": "VARCHAR(50)",
            "phones": "TEXT[]",
            "title": "VARCHAR(200)",
            "company_id": "UUID",
            "street": "VARCHAR(255)",
            "city": "VARCHAR(100)",
            "state": "VARCHAR(100)",
            "zip_code": "VARCHAR(20)",
            "country": "VARCHAR(100)",
            "linkedin": "VARCHAR(500)",
            "date_of_birth": "DATE",
            "gender": "CHAR(1)",
            "notes": "TEXT",
            "source_ids": "TEXT[]",
            "origin_names": "TEXT[]",
            "last_updated": "TIMESTAMPTZ",
            "record_status": "VARCHAR(50)",
            "merged_at": "TIMESTAMPTZ",
            "merge_version": "INTEGER DEFAULT 1",
        },
    }


def get_destination_product_schema() -> dict:
    return {
        "database": "merge_destination",
        "schema_name": "public",
        "table_name": "products",
        "columns": {
            "id": "UUID PRIMARY KEY",
            "name": "VARCHAR(255)",
            "description": "TEXT",
            "sku": "VARCHAR(100)",
            "category": "VARCHAR(100)",
            "price": "NUMERIC(10,2)",
            "cost": "NUMERIC(10,2)",
            "weight": "NUMERIC(8,2)",
            "units": "VARCHAR(20)",
            "stock_quantity": "INTEGER",
            "reorder_level": "INTEGER",
            "supplier": "VARCHAR(255)",
            "specifications": "JSONB",
            "source_ids": "TEXT[]",
            "origin_names": "TEXT[]",
            "last_updated": "TIMESTAMPTZ",
            "record_status": "VARCHAR(50)",
            "merged_at": "TIMESTAMPTZ",
            "merge_version": "INTEGER DEFAULT 1",
        },
    }


def get_destination_order_schema() -> dict:
    return {
        "database": "merge_destination",
        "schema_name": "public",
        "table_name": "orders",
        "columns": {
            "id": "UUID PRIMARY KEY",
            "order_number": "VARCHAR(50)",
            "contact_id": "UUID",
            "company_id": "UUID",
            "items": "JSONB",
            "subtotal": "NUMERIC(12,2)",
            "tax": "NUMERIC(12,2)",
            "shipping": "NUMERIC(12,2)",
            "total": "NUMERIC(12,2)",
            "currency": "VARCHAR(10)",
            "status": "VARCHAR(50)",
            "shipping_address": "JSONB",
            "billing_address": "JSONB",
            "notes": "TEXT",
            "placed_at": "TIMESTAMPTZ",
            "shipped_at": "TIMESTAMPTZ",
            "delivered_at": "TIMESTAMPTZ",
            "source_ids": "TEXT[]",
            "origin_names": "TEXT[]",
            "last_updated": "TIMESTAMPTZ",
            "record_status": "VARCHAR(50)",
            "merged_at": "TIMESTAMPTZ",
            "merge_version": "INTEGER DEFAULT 1",
        },
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def generate_all(counts: dict = None) -> dict:
    """Generate all mock data and return organized by entity type and origin."""
    counts = counts or {}
    n_companies = counts.get("companies", 200)
    n_contacts = counts.get("contacts", 500)
    n_products = counts.get("products", 300)
    n_orders = counts.get("orders", 400)

    # Generate raw records
    companies = generate_companies(n_companies)
    contacts = generate_contacts(n_contacts)
    products = generate_products(n_products)
    orders = generate_orders(contacts, companies, products, n_orders)

    # Split across origins
    (
        pg1_companies,
        mongo_companies,
        pg1_contacts,
        mongo_contacts,
        pg1_products,
        mongo_products,
        mongo_orders,
    ) = assign_source_ids(
        companies,
        contacts,
        products,
        orders,
    )

    # MongoDB documents (nested format)
    mongo_contact_docs = generate_mongodb_contacts(mongo_contacts)
    mongo_company_docs = generate_mongodb_companies(mongo_companies)
    mongo_product_docs = generate_mongodb_products(mongo_products)

    return {
        "postgres_origin_1": {
            "companies": pg1_companies,
            "contacts": pg1_contacts,
            "products": pg1_products,
            "orders": [],
            "schemas": {
                "companies": get_postgres_company_schema(),
                "contacts": get_postgres_contact_schema(),
                "products": get_postgres_product_schema(),
                "orders": get_postgres_order_schema(),
            },
        },
        "mongodb_origin": {
            "companies": mongo_company_docs,
            "contacts": mongo_contact_docs,
            "products": mongo_product_docs,
            "orders": mongo_orders,
            "schemas": {
                "companies": get_mongodb_company_schema(),
                "contacts": get_mongodb_contact_schema(),
                "products": get_mongodb_product_schema(),
            },
        },
        "destination": {
            "schemas": {
                "companies": get_destination_company_schema(),
                "contacts": get_destination_contact_schema(),
                "products": get_destination_product_schema(),
                "orders": get_destination_order_schema(),
            },
        },
        "statistics": {
            "postgres_companies": len(pg1_companies),
            "postgres_contacts": len(pg1_contacts),
            "postgres_products": len(pg1_products),
            "mongodb_companies": len(mongo_companies),
            "mongodb_contacts": len(mongo_contacts),
            "mongodb_products": len(mongo_products),
            "orders": len(mongo_orders),
            "total_companies": len(set(c["id"] for c in companies)),
            "total_contacts": len(set(c["id"] for c in contacts)),
            "total_products": len(set(p["id"] for p in products)),
        },
    }


if __name__ == "__main__":
    import json
    import os

    data = generate_all()
    output_dir = os.path.join(os.path.dirname(__file__), "generated")
    os.makedirs(output_dir, exist_ok=True)

    # Save each origin
    for origin_name, origin_data in data.items():
        if origin_name == "statistics":
            continue
        out_path = os.path.join(output_dir, f"{origin_name}_data.json")
        with open(out_path, "w") as f:
            json.dump(origin_data, f, indent=2, default=str)
        print(f"Written: {out_path}")

    # Save statistics
    stats_path = os.path.join(output_dir, "statistics.json")
    with open(stats_path, "w") as f:
        json.dump(data["statistics"], f, indent=2)
    print(f"Written: {stats_path}")

    print("\n=== Statistics ===")
    for key, val in data["statistics"].items():
        print(f"  {key}: {val}")
