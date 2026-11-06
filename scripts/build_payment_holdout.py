"""
Build data/payment_fraud_holdout.json: 80 SYNTHETIC payouts, 36 fraudulent and 44 legitimate, kept apart from the main corpus.

The main corpus (scripts/build_payment_corpus.py) holds the patterns the checks were written against, so it flatters them. This set
uses other carriers and harder cases, and it was written before the layers it measures. Some fraud here is meant to stay unseen (a
cloned website the payee really sits on, a carrier listed only in directories), and some honest payouts are meant to look like fraud
(a real carrier paid through free mail, a finance company in the same group, an urgent but genuine change request).

Everything is invented: the carriers do not exist, the domains end in .example and the search results are written into each case.
The counts per scenario are not a claim about how often anything occurs.

    python scripts/build_payment_holdout.py
"""

from __future__ import annotations

import json
import pathlib
import random

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "payment_fraud_holdout.json"
SEED = 20261106

NAMES = (
    "Alder", "Basalt", "Cedar", "Delta", "Egret", "Flint", "Gale", "Heron", "Ibis", "Jade", "Kelp", "Larch", "Mica",
    "Nettle", "Oriole", "Peregrine", "Quill", "Reef", "Slate", "Teal", "Upland", "Verdant", "Wren", "Yew", "Zinnia",
)
BEC_PARAPHRASED = (
    "Our treasury has moved to a new remittance route. Kindly settle this invoice through the details below; there is no requirement to loop in your usual contact.",
    "Following our restructuring the earlier beneficiary is retired. Remit to the updated beneficiary by close of business; our controller has signed this off.",
    "We are finalising a sensitive acquisition, so please route this payment to the interim account and leave your account manager out of it for now.",
    "Please disregard the remittance details in your file. Our auditors require the funds in the interim account this week; reply only to this mailbox.",
)
BEC_VIETNAMESE = (
    "Công ty chúng tôi vừa đổi tài khoản nhận tiền. Anh chị chuyển khoản vào tài khoản mới ngay hôm nay, không cần gọi lại để xác nhận.",
    "Tài khoản cũ đang bị phong tỏa do kiểm toán. Vui lòng thanh toán gấp sang tài khoản mới và giữ kín việc này.",
    "Giám đốc đã đồng ý đổi người thụ hưởng. Anh chị chuyển tiền trong hôm nay, không trao đổi với nhân viên phụ trách cũ.",
)
HONEST_URGENT = (
    "Urgent notice from our finance office: our bank merges on the first of the month. The new details are in the signed contract annex; please call your account manager to confirm.",
    "We need this updated immediately because our bank is merging, so please phone our accounts team on the number in your contract file to check.",
    "Our director asked us to give you the new details in writing; please verify them by calling the number on our website.",
)
HONEST_VIETNAMESE = (
    "Từ ngày 1 tháng sau, công ty chuyển sang tài khoản mới tại cùng ngân hàng. Anh chị vui lòng gọi số trong hợp đồng để xác nhận.",
    "Ngân hàng của chúng tôi sáp nhập chi nhánh. Thông tin mới có trong phụ lục hợp đồng đã ký; kế toán sẽ gọi lại để đối chiếu.",
)

BASE = {
    "origin": "Da Nang, Vietnam", "destination": "Long Beach, United States", "weight_kg": 2400,
    "declared_value": 52000, "shipper_name": "Hoa Sen Ceramics", "shipper_company": "Hoa Sen Ceramics JSC",
    "shipper_country": "Vietnam", "shipper_tax_id": "0401234567", "receiver_name": "Purchasing",
    "receiver_company": "Pacific Tile Supply Inc", "receiver_country": "United States", "shipper_tx_count": 35,
    "cargo_description": "Glazed ceramic floor tiles, 900 boxes", "hs_code": "6907.21", "transit_points": "none",
    "route_details": "direct sailing", "currency": "USD",
}


def site(name: str, domain: str) -> dict:
    return {
        "title": f"{name} | ocean and inland freight",
        "content": f"{name} moves containers between Vietnam and the United States.",
        "url": f"https://{domain}/about",
    }


def listing(name: str) -> dict:
    slug = name.lower().replace(" ", "-")
    return {
        "title": f"{name} - carrier listing",
        "content": f"{name} appears among the freight companies listed for Southeast Asia.",
        "url": f"https://freight-listings.example/carriers/{slug}",
    }


class Builder:
    def __init__(self) -> None:
        self.rng = random.Random(SEED)
        self.cases: list[dict] = []
        self.count = 0

    def carrier(self) -> tuple[str, str, str]:
        self.count += 1
        word = self.rng.choice(NAMES)
        n = f"{self.count:03d}"
        name, domain = self.rng.choice((
            (f"{word} Freight {n} Co", f"{word.lower()}-freight-{n}.example"),
            (f"{word} Haulage {n} Ltd", f"{word.lower()}-haulage-{n}.example"),
            (f"{word} Carriers {n} JSC", f"{word.lower()}carriers{n}.example"),
        ))
        return word.lower(), name, domain

    def free_mail(self, word: str) -> str:
        return f"{word}.accounts{self.rng.randint(10, 99)}@gmail.com"

    def freight(self) -> float:
        return float(self.rng.randrange(1800, 6200, 50))

    def case(self, label: str, scenario: str, *, paid: int, payee: str = "own", pages: str = "site",
             changed: int | None = None, text: str | None = None, median: float | None = None, shared: int = 0) -> None:
        word, name, domain = self.carrier()
        freight = self.freight()
        address = {
            "own": f"payments@{domain}",
            "free_mail": self.free_mail(word),
            "lookalike": f"payments@{domain.replace('-', '').replace('.example', '-billing.example')}",
            "group": f"finance@{word}-group-{self.count:03d}.example",
        }[payee]
        results = {
            "site": [site(name, domain)],
            "listing_first": [listing(name), site(name, domain)],
            "listing_only": [listing(name)],
        }[pages]
        self.cases.append({
            "id": f"{'F' if label == 'fraud' else 'L'}-{scenario.upper().replace('_', '-')}-{self.count:03d}",
            "label": label,
            "scenario": scenario,
            "shipment": {
                **BASE, "shipment_id": f"HOLD-{self.count:03d}", "shipping_cost": freight, "avg_route_cost": freight,
                "carrier_name": name, "payout_amount": round(freight * 0.97, 2),
                "paypal_authorization_id": f"HLDAUTH{self.count:09d}",
                "carrier_payee_email": address, "carrier_registered_payee_email": address,
            },
            "history": {
                "paid_count": paid, "frozen": False,
                "median_paid": round(freight / median, 2) if median else None, "other_carriers_on_payee": shared,
            },
            "payee_changed_days_ago": changed,
            "change_request_text": text,
            "search_results": results,
        })


def build() -> dict:
    b = Builder()
    rng = b.rng
    for _ in range(6):
        b.case("fraud", "lookalike_payee_domain", paid=0, payee="lookalike")
    for _ in range(6):
        b.case("fraud", "impersonated_free_mail_listing_first", paid=0, payee="free_mail", pages="listing_first")
    for _ in range(4):
        b.case("fraud", "impersonated_listing_only", paid=0, payee="free_mail", pages="listing_only")
    for _ in range(4):
        b.case("fraud", "cloned_site", paid=0)
    for i in range(6):
        b.case("fraud", "bec_paraphrase", paid=rng.randint(3, 9), changed=rng.randint(20, 40),
               text=BEC_PARAPHRASED[i % len(BEC_PARAPHRASED)])
    for i in range(4):
        b.case("fraud", "bec_vietnamese", paid=rng.randint(3, 9), changed=rng.randint(20, 40),
               text=BEC_VIETNAMESE[i % len(BEC_VIETNAMESE)])
    for _ in range(3):
        b.case("fraud", "payee_shared_between_carriers", paid=rng.randint(1, 3), shared=rng.randint(2, 4))
    for _ in range(3):
        b.case("fraud", "inflated_booking", paid=rng.randint(4, 9), median=rng.uniform(1.8, 2.6))
    for _ in range(10):
        b.case("legit", "routine_listing_first", paid=rng.randint(5, 30), pages="listing_first", median=rng.uniform(0.9, 1.1))
    for _ in range(6):
        b.case("legit", "real_carrier_free_mail_established", paid=rng.randint(5, 20), payee="free_mail", median=rng.uniform(0.9, 1.1))
    for _ in range(6):
        b.case("legit", "real_carrier_free_mail_new", paid=rng.randint(0, 2), payee="free_mail")
    for _ in range(4):
        b.case("legit", "group_finance_domain", paid=rng.randint(0, 2), payee="group")
    for i in range(6):
        b.case("legit", "honest_urgent_change", paid=rng.randint(4, 12), changed=rng.randint(30, 90),
               text=HONEST_URGENT[i % len(HONEST_URGENT)])
    for i in range(6):
        b.case("legit", "honest_vietnamese_change", paid=rng.randint(4, 12), changed=rng.randint(30, 90),
               text=HONEST_VIETNAMESE[i % len(HONEST_VIETNAMESE)])
    for _ in range(6):
        b.case("legit", "new_carrier_with_presence", paid=0)
    return {
        "synthetic": True,
        "note": "Invented payouts and carriers, kept apart from the main corpus. Built by scripts/build_payment_holdout.py "
                "from a fixed seed; the search results are recorded here, not fetched.",
        "seed": SEED,
        "cases": b.cases,
    }


if __name__ == "__main__":
    data = build()
    OUT.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    fraud = sum(1 for c in data["cases"] if c["label"] == "fraud")
    print(f"wrote {OUT} ({len(data['cases'])} cases, {fraud} fraudulent)")
