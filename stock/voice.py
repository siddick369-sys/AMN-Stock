"""
stock/voice.py — Assistant Vocal IA pour les décharges AMN
===========================================================
Pipeline :
  1. transcribe_audio()          → Groq Whisper large-v3-turbo  (STT ultra-rapide)
  2. extract_discharge_intent()  → Groq LLaMA 3.3 70B            (NLU + matching)

Le LLM reçoit la liste complète des équipements disponibles en stock
et fait un matching sémantique fuzzy :
  « deux routeurs »   →  Routeur 4G LTE  (id=3, confiance=high)
  « câbles optiques » →  Câble Fibre 50m (id=7, confiance=medium)
  « onduleur »        →  non résolu      (unmatched)

Format de retour standardisé :
{
  "transcription": "...",
  "destination": "description complète de la mission",
  "items": [
    {
      "equipment_id": 3,
      "matched_name": "Routeur 4G LTE",
      "reference": "RTR-4G-001",
      "quantity": 2,
      "confidence": "high",   # high | medium | low
      "original_mention": "routeurs"
    }
  ],
  "unmatched": ["onduleur"],  # mentionnés mais introuvables en stock
  "notes": "...",             # remarques éventuelles du LLM
  "language_detected": "fr"
}
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

# ── Modèles Groq ──────────────────────────────────────────────────────────────
WHISPER_MODEL = "whisper-large-v3-turbo"   # 216× temps réel, multilingue
NLU_MODEL     = "llama-3.3-70b-versatile"  # meilleur modèle ouvert pour le NLU
NLU_TEMP      = 0.05   # quasi-déterministe pour extraction structurée


# ═══════════════════════════════════════════════════════════════════════════════
# CLIENT GROQ (partagé avec ai.py)
# ═══════════════════════════════════════════════════════════════════════════════

def _get_groq():
    try:
        from groq import Groq
    except ImportError as exc:
        raise RuntimeError("Le package 'groq' n'est pas installé.") from exc
    api_key = getattr(settings, "GROQ_API_KEY", "")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY non défini.")
    return Groq(api_key=api_key)


# ═══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 1 — TRANSCRIPTION AUDIO (Whisper)
# ═══════════════════════════════════════════════════════════════════════════════

def transcribe_audio(audio_bytes: bytes, mime_type: str = "audio/webm") -> str:
    """
    Transcrit le fichier audio en texte avec Groq Whisper large-v3-turbo.

    :param audio_bytes: Contenu brut du fichier audio
    :param mime_type:   Type MIME (audio/webm, audio/mp4, audio/wav, audio/ogg…)
    :return:            Transcription brute en texte
    """
    client = _get_groq()

    # Déterminer l'extension à partir du mime_type
    ext_map = {
        "audio/webm": "webm",
        "audio/ogg":  "ogg",
        "audio/mp4":  "mp4",
        "audio/m4a":  "m4a",
        "audio/wav":  "wav",
        "audio/mpeg": "mp3",
        "audio/flac": "flac",
    }
    ext = ext_map.get(mime_type.split(";")[0].strip(), "webm")
    filename = f"recording.{ext}"

    # Prompt de contexte pour améliorer la précision sur le vocabulaire AMN
    context_prompt = (
        "Transcription d'un technicien de télécommunications AMN (Africa Mobile Networks) "
        "dictant une demande de décharge d'équipements. Vocabulaire probable : "
        "routeur, câble fibre, antenne, BTS, onduleur, switch, batterie, "
        "modem, GPS, talkies-walkie, mission, site, installation, déploiement."
    )

    logger.info("transcribe_audio : envoi à Groq Whisper (%s, %d octets)…", mime_type, len(audio_bytes))

    transcription = client.audio.transcriptions.create(
        file=(filename, audio_bytes, mime_type),
        model=WHISPER_MODEL,
        language="fr",
        response_format="verbose_json",
        prompt=context_prompt,
    )

    text = transcription.text.strip() if hasattr(transcription, "text") else str(transcription).strip()
    logger.info("transcribe_audio : transcription obtenue (%d chars) : %s", len(text), text[:120])
    return text


# ═══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 2 — EXTRACTION D'INTENTION (LLaMA NLU)
# ═══════════════════════════════════════════════════════════════════════════════

_SYSTEM_NLU = """Tu es l'assistant logistique vocal d'Africa Mobile Networks (AMN).
Tu analyses la transcription d'un technicien qui dicte oralement une demande de sortie
d'équipements (décharge). Tu dois en extraire les informations structurées suivantes :
  1. La destination / description de la mission
  2. La liste des équipements avec les quantités

RÈGLES STRICTES :
- Tu dois uniquement associer les équipements mentionnés à ceux qui existent dans la liste
  "equipments_available" fournie ci-dessous.
- Effectue un matching sémantique large : "routeur" → "Routeur 4G LTE", "câble fibre" →
  "Câble Fibre 50m", "antenne" → "Antenne Sectorielle 120°", etc.
- Si plusieurs équipements correspondent, choisis celui avec le stock disponible > 0.
- Si tu n'es pas certain d'un match, indique confidence = "medium" ou "low".
- N'invente JAMAIS un equipment_id qui n'est pas dans la liste fournie.
- Si un équipement mentionné ne correspond à rien dans la liste, mets-le dans "unmatched".
- Si la quantité n'est pas précisée, suppose 1.
- Comprends les nombres en lettres : "deux" → 2, "une dizaine" → 10, "quelques" → 3.
- Comprends les abréviations et termes locaux courants en télécommunications.
- La destination peut être un lieu, un nom de site BTS, ou une description de mission.
- Si la transcription est vide ou incompréhensible, retourne un JSON avec destination="" et items=[].

RETOURNE UNIQUEMENT un objet JSON valide, sans aucun texte avant ou après, sans markdown."""


def extract_discharge_intent(
    transcription: str,
    available_equipment: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Extrait la destination et la liste d'équipements structurée depuis une transcription.

    :param transcription:       Texte transcrit par Whisper
    :param available_equipment: Liste [{id, name, reference, stock}, …] depuis la DB
    :return:                    Dict structuré (voir format en tête de module)
    """
    if not transcription.strip():
        return {
            "transcription": "",
            "destination": "",
            "items": [],
            "unmatched": [],
            "notes": "Transcription vide.",
            "language_detected": "fr",
        }

    client = _get_groq()

    # Sérialiser la liste d'équipements pour le prompt
    equip_json = json.dumps(
        [{"id": e["id"], "name": e["name"], "reference": e["reference"], "stock": e["stock"]}
         for e in available_equipment],
        ensure_ascii=False,
        indent=2,
    )

    user_prompt = f"""Liste des équipements disponibles en stock :
{equip_json}

Transcription du technicien :
"{transcription}"

Retourne UNIQUEMENT ce JSON (sans markdown, sans texte hors JSON) :
{{
  "destination": "description complète de la mission/destination",
  "items": [
    {{
      "equipment_id": <id_entier_de_la_liste>,
      "matched_name": "<nom exact de l'équipement dans la liste>",
      "reference": "<référence>",
      "quantity": <entier>,
      "confidence": "high|medium|low",
      "original_mention": "<mot exact utilisé par le technicien>"
    }}
  ],
  "unmatched": ["<termes mentionnés mais non trouvés dans la liste>"],
  "notes": "<observations importantes ou avertissements>"
}}"""

    logger.info("extract_discharge_intent : appel LLaMA NLU (%d équipements dispo)…", len(available_equipment))

    response = client.chat.completions.create(
        model=NLU_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_NLU},
            {"role": "user",   "content": user_prompt},
        ],
        max_tokens=1024,
        temperature=NLU_TEMP,
    )

    raw = response.choices[0].message.content.strip()
    logger.info("extract_discharge_intent : réponse brute : %s", raw[:300])

    # Extraire le JSON même si le LLM a ajouté du texte parasite
    parsed = _safe_parse_json(raw)

    # Injecter la transcription dans le résultat
    parsed["transcription"] = transcription
    parsed.setdefault("destination", "")
    parsed.setdefault("items", [])
    parsed.setdefault("unmatched", [])
    parsed.setdefault("notes", "")
    parsed["language_detected"] = "fr"

    # Validation : s'assurer que les IDs existent bien dans la liste
    valid_ids = {e["id"] for e in available_equipment}
    validated_items = []
    for item in parsed["items"]:
        eq_id = item.get("equipment_id")
        if eq_id in valid_ids:
            validated_items.append(item)
        else:
            logger.warning("extract_discharge_intent : equipment_id=%s invalide, ignoré.", eq_id)
            parsed["unmatched"].append(item.get("matched_name", f"id={eq_id}"))
    parsed["items"] = validated_items

    logger.info(
        "extract_discharge_intent : %d équipement(s) extrait(s), %d non résolu(s).",
        len(parsed["items"]), len(parsed["unmatched"]),
    )
    return parsed


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _safe_parse_json(text: str) -> dict:
    """
    Tente d'extraire et parser un objet JSON depuis une chaîne de texte,
    même si le LLM a enrobé la réponse dans des balises markdown ou du texte.
    """
    # 1. Essai direct
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Extraire le premier bloc {...} avec regex
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # 3. Retirer les balises markdown ```json … ```
    clean = re.sub(r'```(?:json)?', '', text).strip('` \n')
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    logger.error("_safe_parse_json : impossible de parser : %s", text[:200])
    return {"destination": "", "items": [], "unmatched": [], "notes": f"Erreur de parsing LLM : {text[:100]}"}


# ═══════════════════════════════════════════════════════════════════════════════
# PIPELINE COMPLET (utilisé par la vue Django)
# ═══════════════════════════════════════════════════════════════════════════════

def process_voice_discharge(audio_bytes: bytes, mime_type: str, available_equipment: list) -> dict:
    """
    Pipeline complet : audio → transcription → extraction structurée.
    Retourne le dict final prêt à être sérialisé en JSON pour le frontend.
    Propage les erreurs avec un dict {error: "..."} en cas d'échec.
    """
    try:
        transcription = transcribe_audio(audio_bytes, mime_type)
    except Exception as exc:
        logger.error("process_voice_discharge : erreur transcription : %s", exc)
        return {"error": f"Erreur de transcription audio : {exc}", "step": "transcription"}

    try:
        result = extract_discharge_intent(transcription, available_equipment)
    except Exception as exc:
        logger.error("process_voice_discharge : erreur NLU : %s", exc)
        return {
            "error": f"Erreur d'analyse IA : {exc}",
            "step": "nlu",
            "transcription": transcription,  # retourner quand même la transcription
        }

    return result
