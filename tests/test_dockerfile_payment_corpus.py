"""
The image carries the payment corpus (Dockerfile).

GET /api/v1/defense/scenarios and /api/v1/defense/evaluation read data/payment_fraud_corpus.json through payment_eval.load_corpus. The
Dockerfile copies only the data files it names, and a file it leaves out is not a build error: the route answered 500 on the deployed service
while every test, which runs from a checkout, passed. This pins the COPY, as tests/test_evaluation_endpoint.py does for the evaluation reports.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
CORPUS = "data/payment_fraud_corpus.json"


def test_the_corpus_the_defense_routes_read_exists_in_the_repository():
    assert (ROOT / CORPUS).is_file()


def test_the_dockerfile_copies_the_payment_corpus_into_the_image():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    copied = re.findall(r"^COPY\s+(\S+)\s+\S+\s*$", dockerfile, re.MULTILINE)
    assert CORPUS in copied, f"the Dockerfile does not COPY {CORPUS}; the Defense page answers 500 in the image"
