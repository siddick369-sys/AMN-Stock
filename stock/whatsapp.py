"""
Green API — Client WhatsApp pour AMN Stock
==========================================
Toute la logique d'envoi WhatsApp est centralisée ici.
Green API docs : https://green-api.com/en/docs/api/sending/SendMessage/

Configuration requise (settings.py / .env) :
  GREENAPI_INSTANCE_ID    — identifiant de l'instance Green API
  GREENAPI_TOKEN          — token API de l'instance
  GREENAPI_RECIPIENT      — numéro cible au format international sans +
                            (ex: 237678317658  pour +237 678 317 658)
  GREENAPI_BASE_URL       — (optionnel) override de l'URL de base
"""

import logging
import time
from typing import Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# ── constantes ──────────────────────────────────────────────────────────────
DEFAULT_BASE_URL = "https://api.green-api.com"
REQUEST_TIMEOUT  = 30   # secondes
MAX_RETRIES      = 4
RETRY_DELAYS     = [5, 10, 20, 30]   # secondes entre chaque tentative


class GreenAPIError(Exception):
    """Levée quand Green API retourne une erreur non-récupérable."""


# ── client bas niveau ────────────────────────────────────────────────────────

class GreenAPIClient:
    """
    Encapsule les appels HTTP à Green API.
    Gère les retries avec backoff exponentiel.
    """

    def __init__(
        self,
        instance_id: Optional[str] = None,
        token: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.instance_id = instance_id or getattr(settings, 'GREENAPI_INSTANCE_ID', '')
        self.token       = token       or getattr(settings, 'GREENAPI_TOKEN', '')
        self.base_url    = (base_url   or getattr(settings, 'GREENAPI_BASE_URL', DEFAULT_BASE_URL)).rstrip('/')

        if not self.instance_id or not self.token:
            raise GreenAPIError(
                "GREENAPI_INSTANCE_ID et GREENAPI_TOKEN doivent être définis "
                "dans les variables d'environnement."
            )

    # ── construction des URLs ─────────────────────────────────────────────

    def _url(self, method: str) -> str:
        return f"{self.base_url}/waInstance{self.instance_id}/{method}/{self.token}"

    # ── envoi générique avec retry ────────────────────────────────────────

    def _post(self, method: str, payload: dict) -> dict:
        url = self._url(method)
        last_exc = None

        for attempt, delay in enumerate(RETRY_DELAYS[:MAX_RETRIES], start=1):
            try:
                resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                data = resp.json()

                # Green API renvoie {"idMessage": "..."} en succès
                if 'idMessage' in data:
                    logger.info(
                        "[GreenAPI] Message envoyé (tentative %d) — idMessage=%s",
                        attempt, data['idMessage']
                    )
                    return data

                # Erreur logique retournée par l'API
                logger.warning("[GreenAPI] Réponse inattendue (tentative %d) : %s", attempt, data)
                last_exc = GreenAPIError(f"Réponse inattendue : {data}")

            except requests.exceptions.Timeout:
                logger.warning("[GreenAPI] Timeout (tentative %d/%d)", attempt, MAX_RETRIES)
                last_exc = GreenAPIError("Timeout de connexion Green API")

            except requests.exceptions.ConnectionError as exc:
                logger.warning("[GreenAPI] Erreur réseau (tentative %d/%d) : %s", attempt, MAX_RETRIES, exc)
                last_exc = exc

            except requests.exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else '?'
                logger.warning("[GreenAPI] HTTP %s (tentative %d/%d)", status, attempt, MAX_RETRIES)
                # 4xx → inutile de réessayer
                if exc.response is not None and 400 <= exc.response.status_code < 500:
                    raise GreenAPIError(f"Erreur HTTP {status} : {exc.response.text}") from exc
                last_exc = exc

            if attempt < MAX_RETRIES:
                time.sleep(delay)

        raise GreenAPIError(f"Échec après {MAX_RETRIES} tentatives : {last_exc}") from last_exc

    # ── méthodes publiques ────────────────────────────────────────────────

    def send_message(self, phone: str, message: str) -> dict:
        """
        Envoie un message texte WhatsApp.

        :param phone:   Numéro sans '+' ni espaces (ex: 237678317658)
        :param message: Texte du message (supporte les émojis Unicode)
        :return:        Dict {"idMessage": "..."} si succès
        """
        chat_id = _format_chat_id(phone)
        return self._post('sendMessage', {'chatId': chat_id, 'message': message})

    def check_whatsapp(self, phone: str) -> bool:
        """Vérifie si un numéro est enregistré sur WhatsApp."""
        chat_id = _format_chat_id(phone)
        try:
            data = self._post('checkWhatsapp', {'phoneNumber': int(phone)})
            return data.get('existsWhatsapp', False)
        except GreenAPIError:
            return False


# ── helpers ──────────────────────────────────────────────────────────────────

def _format_chat_id(phone: str) -> str:
    """
    Convertit un numéro de téléphone en chatId Green API.
    Exemples :
      "237678317658"  → "237678317658@c.us"
      "+237678317658" → "237678317658@c.us"
    """
    phone = str(phone).strip().lstrip('+').replace(' ', '').replace('-', '')
    if not phone.endswith('@c.us'):
        phone = f"{phone}@c.us"
    return phone


def get_client() -> GreenAPIClient:
    """Retourne une instance configurée depuis les settings Django."""
    return GreenAPIClient()


def get_default_recipient() -> str:
    """Retourne le numéro cible par défaut depuis les settings."""
    return str(getattr(settings, 'GREENAPI_RECIPIENT', '237678317658'))


# ── fonction d'envoi simplifiée (utilisée par les tâches Celery) ─────────────

def send_whatsapp(message: str, phone: Optional[str] = None) -> bool:
    """
    Point d'entrée unique pour envoyer un message WhatsApp.
    Retourne True si succès, False si erreur (sans propager l'exception).

    :param message: Texte à envoyer
    :param phone:   Numéro cible. Si None, utilise GREENAPI_RECIPIENT du settings.
    """
    target = phone or get_default_recipient()
    try:
        client = get_client()
        client.send_message(target, message)
        return True
    except GreenAPIError as exc:
        logger.error("[GreenAPI] Échec envoi WhatsApp → %s : %s", target, exc)
        return False
    except Exception as exc:
        logger.exception("[GreenAPI] Erreur inattendue : %s", exc)
        return False
