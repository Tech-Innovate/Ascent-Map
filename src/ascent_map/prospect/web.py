from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

USER_AGENT = "AscentMap/0.2 (+local prospect intelligence; public website enrichment)"
MAX_BYTES = 2_000_000

WHATSAPP_HOSTS = {"wa.me", "api.whatsapp.com", "web.whatsapp.com", "whatsapp.com", "www.whatsapp.com"}
SOCIAL_HOSTS = {
    "instagram": ("instagram.com", "www.instagram.com"),
    "facebook": ("facebook.com", "www.facebook.com", "m.facebook.com"),
    "linkedin": ("linkedin.com", "www.linkedin.com"),
}
BOOKING_TERMS = ("book", "booking", "appointment", "reservation", "schedule")
ECOMMERCE_TERMS = ("shop", "store", "cart", "checkout", "order-online", "order_online", "buy")
PORTAL_TERMS = ("portal", "patient-login", "client-login", "customer-login", "member-login", "my-account")
CONTACT_TERMS = ("contact", "enquiry", "inquiry", "quote", "sales", "support")
LANG_CODES = {"ar", "en", "fr", "de", "es", "it", "tr", "ur", "hi", "zh", "ja", "ko", "ru"}


@dataclass(frozen=True)
class WebEvidence:
    predicate: str
    value: object
    extraction_confidence: float = 0.95
    directness: float = 1.0


@dataclass(frozen=True)
class WebFetch:
    requested_url: str
    final_url: str
    status: int
    content_type: str
    body: bytes
    truncated: bool = False


@dataclass
class ParsedPage:
    title: str = ""
    html_lang: str | None = None
    viewport: bool = False
    hreflangs: set[str] = field(default_factory=set)
    anchors: list[tuple[str, str]] = field(default_factory=list)
    forms: list[dict[str, str]] = field(default_factory=list)


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page = ParsedPage()
        self._anchor_href: str | None = None
        self._anchor_text: list[str] = []
        self._in_title = False
        self._title: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = {str(k).casefold(): str(v or "") for k, v in attrs}
        tag = tag.casefold()
        if tag == "html" and values.get("lang"):
            self.page.html_lang = values["lang"].split("-")[0].casefold()
        elif tag == "meta" and values.get("name", "").casefold() == "viewport" and values.get("content"):
            self.page.viewport = True
        elif tag == "link":
            hreflang = values.get("hreflang", "").split("-")[0].casefold()
            if hreflang and hreflang != "x-default":
                self.page.hreflangs.add(hreflang)
        elif tag == "a":
            self._anchor_href = values.get("href", "")
            self._anchor_text = []
            hreflang = values.get("hreflang", "").split("-")[0].casefold()
            if hreflang and hreflang != "x-default":
                self.page.hreflangs.add(hreflang)
        elif tag == "form":
            self.page.forms.append(values)
        elif tag == "title":
            self._in_title = True
            self._title = []

    def handle_data(self, data: str) -> None:
        if self._anchor_href is not None:
            self._anchor_text.append(data)
        if self._in_title:
            self._title.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "a" and self._anchor_href is not None:
            self.page.anchors.append((self._anchor_href, " ".join(" ".join(self._anchor_text).split())))
            self._anchor_href = None
            self._anchor_text = []
        elif tag == "title":
            self._in_title = False
            self.page.title = " ".join(" ".join(self._title).split())


def _is_public_ip(value: str) -> bool:
    return ipaddress.ip_address(value).is_global


def validate_public_url(url: str) -> str:
    candidate = url.strip()
    if not candidate:
        raise ValueError("Website URL is empty")
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Unsupported website scheme: {parsed.scheme}")
    if parsed.username or parsed.password:
        raise ValueError("Credential-bearing website URLs are not allowed")
    host = parsed.hostname
    if not host:
        raise ValueError("Website URL has no hostname")
    host_lower = host.casefold()
    if host_lower in {"localhost", "localhost.localdomain"} or host_lower.endswith(".localhost"):
        raise ValueError("Localhost targets are not allowed")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(
                host,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )}
        except socket.gaierror as exc:
            raise ValueError(f"Could not resolve website hostname: {host}") from exc
        if not addresses or any(not _is_public_ip(address) for address in addresses):
            raise ValueError(f"Website hostname resolves to a non-public address: {host}")
    else:
        if not _is_public_ip(host):
            raise ValueError("Non-public IP targets are not allowed")
    return candidate


class _SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_website(url: str, timeout: float = 12.0, max_bytes: int = MAX_BYTES) -> WebFetch:
    safe_url = validate_public_url(url)
    opener = build_opener(ProxyHandler({}), _SafeRedirect())
    request = Request(safe_url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.5",
        "Accept-Language": "en,ar;q=0.8,*;q=0.5",
    })
    try:
        with opener.open(request, timeout=timeout) as response:
            final_url = validate_public_url(response.geturl())
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
                raise ValueError(f"Unsupported website content type: {content_type}")
            body = response.read(max_bytes + 1)
            truncated = len(body) > max_bytes
            if truncated:
                body = body[:max_bytes]
            return WebFetch(safe_url, final_url, int(getattr(response, "status", 200)), content_type, body, truncated)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Website fetch failed for {safe_url}: {exc}") from exc


def decode_html(body: bytes) -> str:
    return body.decode("utf-8", errors="replace")


def _host_matches(host: str, allowed: tuple[str, ...]) -> bool:
    host = host.casefold()
    return any(host == item or host.endswith("." + item) for item in allowed)


def _whatsapp_number(url: str) -> str | None:
    parsed = urlparse(url)
    if (parsed.hostname or "").casefold() == "wa.me":
        number = parsed.path.strip("/").split("/")[0]
    else:
        number = (parse_qs(parsed.query).get("phone") or [""])[0]
    cleaned = re.sub(r"\D", "", number)
    return cleaned or None


def analyze_html(html: str, page_url: str) -> tuple[list[WebEvidence], list[str]]:
    parser = _PageParser()
    parser.feed(html)
    page = parser.page
    parsed_page_url = urlparse(page_url)
    base_host = (parsed_page_url.hostname or "").casefold()
    base_domain = base_host[4:] if base_host.startswith("www.") else base_host

    whatsapp: list[tuple[str, str]] = []
    emails: set[str] = set()
    phones: set[str] = set()
    social: dict[str, set[str]] = {name: set() for name in SOCIAL_HOSTS}
    internal: list[tuple[int, str]] = []
    booking = ecommerce = checkout = portal = linkedin_company = False
    languages: set[str] = set(page.hreflangs)
    if page.html_lang:
        languages.add(page.html_lang)

    for href, text in page.anchors:
        if not href:
            continue
        href_lower = href.casefold()
        if href_lower.startswith("mailto:"):
            value = href[7:].split("?", 1)[0].strip().casefold()
            if "@" in value:
                emails.add(value)
            continue
        if href_lower.startswith("tel:"):
            value = re.sub(r"[^\d+]", "", href[4:].split("?", 1)[0])
            if value:
                phones.add(value)
            continue

        absolute = urljoin(page_url, href)
        parsed = urlparse(absolute)
        host = (parsed.hostname or "").casefold()
        lowered = f"{href} {text}".casefold()

        if host in WHATSAPP_HOSTS or host.endswith(".whatsapp.com"):
            whatsapp.append((absolute, text))
        for name, hosts in SOCIAL_HOSTS.items():
            if _host_matches(host, hosts):
                social[name].add(absolute)
                if name == "linkedin" and "/company/" in parsed.path.casefold():
                    linkedin_company = True

        booking = booking or any(term in lowered for term in BOOKING_TERMS)
        ecommerce = ecommerce or any(term in lowered for term in ECOMMERCE_TERMS)
        checkout = checkout or "checkout" in lowered or "/cart" in parsed.path.casefold()
        portal = portal or any(term in lowered for term in PORTAL_TERMS)
        short_text = text.strip().casefold()
        if short_text in LANG_CODES:
            languages.add(short_text)

        normalized_host = host[4:] if host.startswith("www.") else host
        if parsed.scheme in {"http", "https"} and normalized_host == base_domain:
            score = 0
            if any(term in lowered for term in CONTACT_TERMS): score = max(score, 100)
            if any(term in lowered for term in BOOKING_TERMS): score = max(score, 90)
            if any(term in lowered for term in ECOMMERCE_TERMS): score = max(score, 70)
            if any(term in lowered for term in PORTAL_TERMS): score = max(score, 60)
            if score:
                internal.append((score, absolute))

    form_present = bool(page.forms)
    form_text = [" ".join(form.values()).casefold() for form in page.forms]
    form_general = any(any(term in text for term in CONTACT_TERMS) for text in form_text)
    form_sales = any(any(term in text for term in ("quote", "sales", "demo")) for text in form_text)
    lowered_html = html.casefold()
    ecommerce = ecommerce or any(term in lowered_html for term in ("shopify", "woocommerce", "add to cart"))
    booking = booking or any(term in lowered_html for term in ("book an appointment", "book appointment", "schedule appointment"))

    evidence = [
        WebEvidence("digital.website_present", True, 0.99),
        WebEvidence("digital.https", parsed_page_url.scheme == "https", 0.99),
    ]
    if page.title:
        evidence.append(WebEvidence("website.page_title", page.title, 0.99))
    if page.viewport:
        evidence.append(WebEvidence("digital.mobile_ready", True, 0.95))
    if whatsapp:
        links = sorted({url for url, _ in whatsapp})
        numbers = sorted({number for url, _ in whatsapp if (number := _whatsapp_number(url))})
        evidence.extend([
            WebEvidence("website.whatsapp_link", links[0], 0.99),
            WebEvidence("website.whatsapp_prominence", min(1.0, 0.65 + 0.1 * min(3, len(links))), 0.80),
            WebEvidence("contact.whatsapp.state", "present", 0.99),
        ])
        if numbers:
            evidence.append(WebEvidence("contact.whatsapp.values", numbers, 0.99))
        if any(any(term in text.casefold() for term in ("whatsapp", "chat", "support", "contact")) for _, text in whatsapp):
            evidence.append(WebEvidence("contact.whatsapp.purpose.customer_support", "likely", 0.72, 0.75))
    if emails:
        evidence.extend([
            WebEvidence("contact.email.state", "present", 0.99),
            WebEvidence("contact.email.values", sorted(emails), 0.99),
            WebEvidence("website.email_prominence", 0.70, 0.78),
        ])
        if any(email.rsplit("@", 1)[-1].removeprefix("www.") == base_domain for email in emails):
            evidence.append(WebEvidence("contact.email.domain_matches_website", True, 0.99))
    if phones:
        evidence.extend([
            WebEvidence("contact.phone.state", "present", 0.99),
            WebEvidence("contact.phone.values", sorted(phones), 0.99),
            WebEvidence("website.phone_prominence", 0.70, 0.78),
        ])
    if form_present:
        evidence.append(WebEvidence("contact.form.state", "present", 0.98))
    if form_general:
        evidence.append(WebEvidence("website.form.purpose.general_enquiry", "likely", 0.78, 0.82))
    if form_sales:
        evidence.append(WebEvidence("website.form.purpose.sales", "likely", 0.78, 0.82))
    if booking:
        evidence.extend([WebEvidence("website.booking_present", True, 0.86, 0.90), WebEvidence("website.appointment_language", True, 0.82, 0.88)])
    if ecommerce:
        evidence.append(WebEvidence("website.ecommerce_present", True, 0.84, 0.88))
    if checkout:
        evidence.append(WebEvidence("website.checkout_present", True, 0.90, 0.92))
    if portal:
        evidence.append(WebEvidence("website.customer_portal_present", True, 0.86, 0.90))
    if languages:
        values = sorted(languages)
        evidence.append(WebEvidence("website.languages", values, 0.94))
        if len(values) >= 2:
            evidence.extend([WebEvidence("website.language_switcher", True, 0.90), WebEvidence("digital.multilingual", True, 0.90)])
    social_links = {name: sorted(urls) for name, urls in social.items() if urls}
    if social_links:
        evidence.append(WebEvidence("website.social_links", social_links, 0.99))
        for name, urls in social_links.items():
            evidence.extend([WebEvidence(f"contact.{name}.state", "present", 0.99), WebEvidence(f"contact.{name}.urls", urls, 0.99)])
            if name == "instagram":
                evidence.append(WebEvidence("website.instagram_prominence", 0.65, 0.75))
    if linkedin_company:
        evidence.append(WebEvidence("organization.linkedin_company_page", True, 0.99))

    seen: set[str] = set()
    links: list[str] = []
    for _, url in sorted(internal, key=lambda item: (-item[0], item[1])):
        normalized = url.split("#", 1)[0]
        if normalized != page_url.split("#", 1)[0] and normalized not in seen:
            seen.add(normalized)
            links.append(normalized)
    return evidence, links


def robots_allowed(base_url: str, target_url: str, fetcher: Callable[..., WebFetch] = fetch_website, timeout: float = 8.0) -> bool:
    parsed = urlparse(base_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        fetched = fetcher(robots_url, timeout=timeout, max_bytes=200_000)
    except Exception:
        return True
    active = False
    rules: list[tuple[bool, str]] = []
    for raw_line in decode_html(fetched.body).splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        if key.casefold() == "user-agent":
            active = value.casefold() in {"*", "ascentmap", USER_AGENT.casefold()}
        elif active and key.casefold() in {"allow", "disallow"}:
            rules.append((key.casefold() == "allow", value))
    path = urlparse(target_url).path or "/"
    matching = [(allow, rule) for allow, rule in rules if rule and path.startswith(rule)]
    if not matching:
        return True
    return max(matching, key=lambda item: len(item[1]))[0]
