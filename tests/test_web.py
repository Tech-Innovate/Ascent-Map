from ascent_map.prospect.web import WebFetch, analyze_html, robots_allowed, validate_public_url


def _facts(html: str):
    evidence, links = analyze_html(html, "https://example.com/")
    return {item.predicate: item.value for item in evidence}, links


def test_analyze_html_extracts_public_business_capabilities():
    html = """
    <html lang="en">
      <head>
        <title>Example Clinic</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link rel="alternate" hreflang="ar" href="/ar">
      </head>
      <body>
        <a href="https://wa.me/966500000000">WhatsApp Support</a>
        <a href="mailto:sales@example.com">Sales Email</a>
        <a href="tel:+966500000001">Call us</a>
        <form id="contact-sales" action="/contact-sales"></form>
        <a href="/book-appointment">Book Appointment</a>
        <a href="/shop">Shop</a>
        <a href="/patient-login">Patient Portal</a>
        <a href="https://instagram.com/example">Instagram</a>
        <a href="https://linkedin.com/company/example">LinkedIn</a>
        <a href="/ar" hreflang="ar">AR</a>
      </body>
    </html>
    """
    facts, links = _facts(html)

    assert facts["digital.website_present"] is True
    assert facts["digital.https"] is True
    assert facts["digital.mobile_ready"] is True
    assert facts["contact.whatsapp.state"] == "present"
    assert facts["contact.whatsapp.values"] == ["966500000000"]
    assert facts["contact.email.state"] == "present"
    assert facts["contact.email.domain_matches_website"] is True
    assert facts["contact.phone.state"] == "present"
    assert facts["contact.form.state"] == "present"
    assert facts["website.booking_present"] is True
    assert facts["website.ecommerce_present"] is True
    assert facts["website.customer_portal_present"] is True
    assert facts["digital.multilingual"] is True
    assert facts["contact.instagram.state"] == "present"
    assert facts["organization.linkedin_company_page"] is True
    assert any("book-appointment" in link for link in links)


def test_private_and_local_targets_are_rejected_without_network_access():
    for url in ("http://127.0.0.1", "http://10.0.0.1", "http://169.254.1.1", "http://localhost"):
        try:
            validate_public_url(url)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected {url} to be rejected")


def test_robots_longest_matching_rule_wins():
    def fake_fetch(url: str, **kwargs):
        body = b"User-agent: *\nDisallow: /private\nAllow: /private/public\n"
        return WebFetch(url, url, 200, "text/plain", body)

    assert robots_allowed("https://example.com", "https://example.com/contact", fetcher=fake_fetch)
    assert not robots_allowed("https://example.com", "https://example.com/private/report", fetcher=fake_fetch)
    assert robots_allowed("https://example.com", "https://example.com/private/public/info", fetcher=fake_fetch)
