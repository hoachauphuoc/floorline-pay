"""
Build data/payment_fraud_corpus.json: 80 SYNTHETIC payouts, 36 fraudulent and 44 legitimate.

Everything here is invented. The carriers do not exist, the domains end in .example, and the
search results the carrier look-up reads are written into each case rather than fetched. The
scenarios are the patterns the payment checks were written against (a payee that does not match
the registry, a payee changed days before a payout, a payout above the booked freight, a change
request written the way payment-diversion messages are) plus two the deterministic checks cannot
see by construction (a shell carrier with a consistent registry entry and no presence online, and
a real carrier whose registered payee is a free-mail account), and three legitimate patterns
that look a little like fraud (a small carrier with no web presence, a long-established payee
change, an honest change request). The fixed seed makes the file reproducible:

    python scripts/build_payment_corpus.py

The counts per scenario are what the evaluation reports; they are not a claim about how often
any of these occur.
"""

from __future__ import annotations

import json
import pathlib
import random

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "payment_fraud_corpus.json"
SEED = 20261023

NAMES = (
    "Aster", "Birch", "Cobalt", "Dune", "Ember", "Fjord", "Garnet", "Harbor", "Indigo", "Juniper", "Kestrel",
    "Lagoon", "Maple", "Nimbus", "Onyx", "Pinewood", "Quartz", "Raven", "Sable", "Tundra", "Umber", "Vesper",
    "Willow", "Xenon", "Yarrow", "Zephyr",
)
BEC_TEXTS = (
    "URGENT: our bank has changed. Please pay the new account today and do not call us to confirm, our line is down.",
    "This is confidential. Our old account is closed after an audit, so send the next payment to the new one immediately.",
    "The managing director asked me to tell you the account has changed. Please act right away and keep this between us.",
    "Kindly update our payment details before the end of the day. Do not phone, we cannot take calls this week.",
)
HONEST_TEXTS = (
    "From the first of next month our payments should go to the account of our finance office, as set out in the attached letter.",
    "Our bank is merging branches. The new details are on our letterhead and in the contract annex; call our accounts team to check.",
    "We have moved our collections to a new account at the same bank. Please confirm with the contact in your contract file.",
)

BASE = {
    "origin": "Ho Chi Minh City, Vietnam", "destination": "Rotterdam, Netherlands", "weight_kg": 1200,
    "declared_value": 40000, "shipper_name": "Lotus Garment Works", "shipper_company": "Lotus Garment Works JSC",
    "shipper_country": "Vietnam", "shipper_tax_id": "0312345678", "receiver_name": "Procurement",
    "receiver_company": "Noordzee Textiel BV", "receiver_country": "Netherlands", "shipper_tx_count": 40,
    "cargo_description": "Cotton T-shirts, 4000 units", "hs_code": "6109.10", "transit_points": "none",
    "route_details": "direct sailing", "currency": "USD",
}


class Builder:
    def __init__(self) -> None:
        self.rng = random.Random(SEED)
        self.cases: list[dict] = []
        self.count = 0

    def carrier(self) -> tuple[str, str]:
        self.count += 1
        word = self.rng.choice(NAMES)
        name = f"{word} Freight {self.count:03d} Co"
        return name, f"{word.lower()}-freight-{self.count:03d}.example"

    def case(self, label: str, scenario: str, *, paid: int, freight: float, payout: float, found: bool = True,
             payee: str | None = None, changed: int | None = None, text: str | None = None,
             value: float | None = None, on_file: str | None = None) -> None:
        name, domain = self.carrier()
        registered = on_file or f"payments@{domain}"
        results = [{
            "title": f"{name} | road and sea freight",
            "content": f"{name} runs scheduled freight between ports in Asia and the United States.",
            "url": f"https://{domain}/about",
        }] if found else []
        self.cases.append({
            "id": f"{'F' if label == 'fraud' else 'L'}-{scenario.upper().replace('_', '-')}-{self.count:03d}",
            "label": label,
            "scenario": scenario,
            "shipment": {
                **BASE, "shipment_id": f"SYN-{self.count:03d}", "shipping_cost": freight, "avg_route_cost": freight,
                **({"declared_value": value} if value else {}),
                "carrier_name": name, "payout_amount": payout, "paypal_authorization_id": f"SYNAUTH{self.count:09d}",
                "carrier_payee_email": payee or registered, "carrier_registered_payee_email": registered,
            },
            "history": {"paid_count": paid, "frozen": False},
            "payee_changed_days_ago": changed,
            "change_request_text": text,
            "search_results": results,
        })

    def freight(self) -> float:
        return float(self.rng.randrange(1800, 6200, 50))


def build() -> dict:
    b = Builder()
    rng = b.rng
    for _ in range(6):
        f = b.freight()
        b.case("fraud", "payee_mismatch", paid=rng.randint(4, 12), freight=f, payout=f,
               payee=f"accounts@{rng.choice(NAMES).lower()}-payments.example")
    for _ in range(6):
        f = b.freight()
        b.case("fraud", "recent_payee_change", paid=rng.randint(3, 9), freight=f, payout=f, changed=rng.randint(1, 10))
    for _ in range(6):
        f = b.freight()
        b.case("fraud", "inflated_payout", paid=rng.randint(3, 9), freight=f, payout=round(f * rng.uniform(1.2, 2.0), 2))
    for i in range(6):
        f = b.freight()
        b.case("fraud", "bec_change_request", paid=rng.randint(3, 9), freight=f, payout=f,
               changed=rng.randint(20, 40), text=BEC_TEXTS[i % len(BEC_TEXTS)])
    for _ in range(8):
        f = b.freight()
        b.case("fraud", "shell_carrier", paid=0, freight=f, payout=round(f * rng.uniform(0.97, 1.0), 2), found=False)
    for _ in range(4):
        f = float(rng.randrange(8000, 12500, 500))
        address = f"{rng.choice(NAMES).lower()}.accounts{rng.randint(10, 99)}@gmail.com"
        b.case("fraud", "impersonated_carrier_free_mail", paid=0, freight=f, payout=f, value=160000.0,
               payee=address, on_file=address)
    for _ in range(12):
        f = b.freight()
        b.case("legit", "routine", paid=rng.randint(5, 30), freight=f, payout=round(f * rng.uniform(0.9, 1.0), 2))
    for _ in range(6):
        f = b.freight()
        b.case("legit", "payee_changed_long_ago", paid=rng.randint(6, 20), freight=f,
               payout=round(f * rng.uniform(0.9, 1.0), 2), changed=rng.randint(60, 200))
    for _ in range(8):
        f = b.freight()
        b.case("legit", "new_carrier_with_presence", paid=0, freight=f, payout=round(f * 0.95, 2))
    for _ in range(6):
        f = b.freight()
        b.case("legit", "underpaid", paid=rng.randint(3, 10), freight=f, payout=round(f * rng.uniform(0.55, 0.85), 2))
    for i in range(6):
        f = b.freight()
        b.case("legit", "honest_change_request", paid=rng.randint(4, 12), freight=f,
               payout=round(f * rng.uniform(0.9, 1.0), 2), changed=rng.randint(30, 90), text=HONEST_TEXTS[i % len(HONEST_TEXTS)])
    for _ in range(6):
        f = b.freight()
        b.case("legit", "small_carrier_not_online", paid=rng.randint(0, 2), freight=f,
               payout=round(f * 0.95, 2), found=False)
    return {
        "synthetic": True,
        "note": "Invented payouts and carriers. Built by scripts/build_payment_corpus.py from a fixed seed; "
                "the search results are recorded here, not fetched.",
        "seed": SEED,
        "cases": b.cases,
    }


if __name__ == "__main__":
    data = build()
    OUT.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    fraud = sum(1 for c in data["cases"] if c["label"] == "fraud")
    print(f"wrote {OUT} ({len(data['cases'])} cases, {fraud} fraudulent)")
