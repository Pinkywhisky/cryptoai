"""Sanitized transient market-data failures shared by resolvers and API adapters."""
import math
import requests

MESSAGES = {
    "OFFLINE_CONFLICT": "Statut du token et du marche contradictoire",
    "INVALID_PAIR": "Paire Alpha invalide",
    "NO_MARKET": "Aucun marche Alpha exploitable",
    "OFFLINE": "Token Alpha hors ligne",
    "DELISTED": "Token Alpha retire",
    "QUOTA": "Quota de donnees temporairement atteint",
    "SERVICE_UNAVAILABLE": "Service de marche temporairement indisponible",
    "NETWORK_ERROR": "Erreur reseau temporaire des donnees de marche",
    "INVALID_RESPONSE": "Reponse de donnees de marche invalide",
    "STALE_DATA": "Dernieres donnees connues perimees",
    "AMBIGUOUS_SYMBOL": "Symbole Alpha ambigu : plusieurs identifiants",
    "INVALID_CONFIGURATION": "Configuration des donnees de marche invalide",
    "BUSY": "Actualisation des donnees deja en cours",
    "NOT_FOUND": "Symbole introuvable dans le catalogue confirme",
}


class MarketDataError(requests.RequestException):
    def __init__(self, code, *, service="ALPHA", retry_after=None, details=None):
        self.code = code if isinstance(code, str) and code in MESSAGES else "SERVICE_UNAVAILABLE"
        self.service = service if service in {"ALPHA", "SPOT", "MARKET"} else "MARKET"
        self.details = dict(details or {})
        self.retry_after = None
        if isinstance(retry_after, (int, float)) and not isinstance(retry_after, bool) and math.isfinite(retry_after):
            self.retry_after = max(1, math.ceil(retry_after))
        super().__init__(MESSAGES[self.code])

    def payload(self):
        return {"error": str(self), "error_code": self.code, "service": self.service,
                "retryable": self.code not in {"NOT_FOUND", "AMBIGUOUS_SYMBOL"},
                "retry_after": self.retry_after, **self.details}


def market_state(state, *, error=None, **metadata):
    result = {"state": state, "reason": error.code if error else None,
              "retry_after": error.retry_after if error else None, **metadata}
    return result
