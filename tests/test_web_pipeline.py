import json
from pathlib import Path

from ascent_map.config import project_paths
from ascent_map.db import Database
from ascent_map.prospect.pipeline import evaluate_all, ingest_maps
from ascent_map.prospect.report import latest_business_report
from ascent_map.prospect.web import WebFetch
from ascent_map.prospect.web_pipeline import enrich_websites


def test_website_enrichment_flows_into_profile_and_channel_evaluation(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    paths = project_paths(root)
    db = Database(tmp_path / "ascent-map.duckdb")
    db.initialize(paths.schema)

    source = tmp_path / "maps.json"
    source.write_text(json.dumps([{
        "title": "Example Dental Clinic",
        "place_id": "example-place-web-1",
        "category": "Dental clinic",
        "categories": ["Dental clinic"],
        "address": "Example Street",
        "latitude": 21.5,
        "longitude": 39.2,
        "web_site": "https://example-clinic.invalid",
        "phone": "+966500000000",
        "review_count": 800,
        "review_rating": 4.4,
    }]), encoding="utf-8")
    ingest_maps(db, paths, source)

    def fake_fetch(url: str, **kwargs):
        if url.endswith("/robots.txt"):
            return WebFetch(url, url, 200, "text/plain", b"User-agent: *\nAllow: /\n")
        if "/contact" in url:
            body = b"""
            <html lang='en'><body>
              <form id='contact-sales' action='/contact-sales'></form>
              <a href='mailto:sales@example-clinic.invalid'>Sales</a>
            </body></html>
            """
            return WebFetch(url, url, 200, "text/html", body)
        body = b"""
        <html lang='en'>
          <head><meta name='viewport' content='width=device-width'><link hreflang='ar' href='/ar'></head>
          <body>
            <a href='https://wa.me/966500000000'>WhatsApp Support</a>
            <a href='/contact'>Contact</a>
            <a href='/book-appointment'>Book Appointment</a>
            <a href='https://instagram.com/exampleclinic'>Instagram</a>
          </body>
        </html>
        """
        return WebFetch(url, url, 200, "text/html", body)

    stats = enrich_websites(db, paths, max_pages=2, delay=0, fetcher=fake_fetch)
    assert stats["sites_fetched"] == 1
    assert stats["pages_fetched"] == 2
    assert stats["evidence"] > 0
    assert stats["errors"] == 0

    evaluate_all(db, paths)
    report = latest_business_report(db)[0]
    facts = report["profile"]["facts"]
    signals = report["profile"]["signals"]

    assert facts["contact.whatsapp.state"]["value"] == "present"
    assert facts["contact.form.state"]["value"] == "present"
    assert facts["website.booking_present"]["value"] is True
    assert facts["digital.multilingual"]["value"] is True

    assert signals["whatsapp_presence"]["value"] == "present"
    assert signals["contact_form_presence"]["value"] == "present"
    assert signals["social_messaging_presence"]["state"] == "present"
    assert signals["online_booking"]["value"] is True
    assert signals["multilingual_digital_presence"]["state"] == "present"
    assert signals["digital_maturity"]["state"] == "present"

    whatsapp = next(item for item in report["channels"] if item["channel_id"] == "whatsapp")
    assert whatsapp["suitability"] is not None
    assert "contact.whatsapp.state" in whatsapp["positive_factors"]
