"""Fetch bounded public documents, preserving TLS hostname checks on each hop."""
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener, getproxies, urlopen

HEADERS = {"User-Agent": "MySelfPayPriceReview/0.3", "Accept": "text/html,application/pdf"}
REDIRECTS = (301, 302, 303, 307, 308)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def public_ips(host):
    try:
        addresses = [r[4][0] for r in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)]
    except socket.gaierror:
        # A desktop browser can use secure DNS while Windows getaddrinfo fails.
        # Only query public provider-style names. No URL, path or patient data is sent.
        if not re.fullmatch(r"[a-z0-9.-]+\.(?:com|org|net|gov|edu|us|health|care|hospital)", host):
            raise
        endpoint = "https://dns.google/resolve?" + urlencode({"name": host, "type": "A"})
        try:
            with urlopen(endpoint, timeout=6) as response:
                answer = json.loads(response.read(32768))
            addresses = [r["data"] for r in answer.get("Answer", []) if r.get("type") == 1]
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise OSError(f"DNS lookup failed for {host}") from exc
    if not addresses:
        raise OSError(f"No public DNS address found for {host}")
    for value in addresses:
        ip = ipaddress.ip_address(value)
        if not ip.is_global or ip.is_multicast or (ip.version == 6 and ip.ipv4_mapped):
            raise ValueError("Source must have a public address")
    return list(dict.fromkeys(addresses))


def pinned_response(host, target):
    connection = http.client.HTTPSConnection(host, timeout=8)
    addresses = public_ips(host)
    last_error = None
    for address in addresses[:4]:
        try:
            sock = socket.create_connection((address, 443), timeout=5)
        except OSError as exc:
            last_error = exc
            continue
        try:
            connection.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
            connection.request("GET", target, headers=HEADERS)
            return connection, connection.getresponse()
        except BaseException:
            sock.close()
            connection.close()
            raise
    raise OSError(f"Could not connect to {host}: {last_error}")


def fetch_public(url, limit, *, origin=None, formats=None):
    proxy_hosts = {h.strip().lower() for h in os.getenv("MYSELFPAY_PROXY_HOSTS", "").split(",") if h.strip()}
    for _ in range(5):
        parts = urlsplit(url)
        host = parts.hostname or ""
        if (parts.scheme != "https" or parts.port not in (443, None) or parts.username is not None
                or not host or "\\" in parts.netloc):
            raise ValueError("Use a public HTTPS pricing page")
        if origin is not None and parts.netloc != urlsplit(origin).netloc:
            raise ValueError("Resource redirected outside the provider website")
        proxy = getproxies().get("https")
        connection = None
        if proxy and host in proxy_hosts:
            # Exact operator-trusted names only; redirects are revalidated below.
            opener = build_opener(ProxyHandler({"https": proxy}), NoRedirect())
            try:
                response = opener.open(Request(url, headers=HEADERS), timeout=12)
            except HTTPError as exc:
                response = exc
        else:
            target = parts.path or "/"
            if parts.query:
                target += "?" + parts.query
            connection, response = pinned_response(host, target)
        try:
            if response.status in REDIRECTS and response.headers.get("Location"):
                url = urljoin(url, response.headers["Location"])
                continue
            if response.status != 200:
                raise ValueError(f"Source returned HTTP {response.status}")
            content_type = response.headers.get_content_type()
            if content_type not in (formats or {"text/html", "application/xhtml+xml", "application/pdf"}):
                raise ValueError(f"Unsupported source format: {content_type}")
            content = response.read(limit + 1)
            if len(content) > limit:
                raise ValueError("Source exceeds size limit")
            return (content, url, content_type) if formats else (content, url)
        finally:
            response.close()
            if connection:
                connection.close()
    raise ValueError("Too many redirects")
