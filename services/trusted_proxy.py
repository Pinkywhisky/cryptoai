"""Resolve forwarding only from explicitly trusted transport peers.

Run Uvicorn with proxy_headers=False (tools/serve.py). Otherwise Uvicorn may
discard the original peer before the application can validate it.
"""

import ipaddress
from starlette.responses import JSONResponse


def trusted_network(value):
    network = ipaddress.ip_network(value, strict=False)
    if isinstance(network, ipaddress.IPv6Network) and network.network_address.ipv4_mapped is not None and network.prefixlen >= 96:
        network = ipaddress.ip_network((network.network_address.ipv4_mapped, network.prefixlen - 96), strict=False)
    if network.prefixlen == 0:
        raise ValueError("Trusting all proxy addresses is forbidden")
    return network


class TrustedProxyMiddleware:
    def __init__(self, app, networks=()):
        self.app = app
        self.networks = tuple(trusted_network(value) for value in networks)

    def trusted(self, address):
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return any(address.version == net.version and address in net for net in self.networks)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        scope.setdefault("state", {})
        scope = dict(scope)
        peer = (scope.get("client") or ("unknown", 0))[0]
        scope["state"]["transport_peer"] = peer
        try:
            peer_address = ipaddress.ip_address(peer)
        except ValueError:
            peer_address = None
        if peer_address is not None and self.trusted(peer_address):
            headers = {}
            for key, value in scope.get("headers", []):
                headers.setdefault(key.lower(), []).append(value.decode("latin-1"))
            forwarded = headers.get(b"x-forwarded-for", [])
            proto = headers.get(b"x-forwarded-proto", [])
            try:
                if len(forwarded) > 1 or len(proto) > 1:
                    raise ValueError
                if forwarded:
                    parts = forwarded[0].split(",")
                    if not 1 <= len(parts) <= 20:
                        raise ValueError
                    if any("%" in part for part in parts):
                        raise ValueError
                    chain = [ipaddress.ip_address(part.strip()) for part in parts]
                    client = peer_address
                    for candidate in reversed(chain):
                        if not self.trusted(client):
                            break
                        client = candidate
                    scope["client"] = (str(client), 0)
                if proto:
                    if proto[0] not in {"http", "https"}:
                        raise ValueError
                    scope["scheme"] = proto[0]
            except ValueError:
                from services.auth import SECURITY_HEADERS
                await JSONResponse({"success":False, "error":"En-tetes proxy invalides"}, status_code=400,
                                   headers=SECURITY_HEADERS)(scope, receive, send)
                return
        # X-Forwarded-Host and Forwarded are intentionally not used.
        await self.app(scope, receive, send)
