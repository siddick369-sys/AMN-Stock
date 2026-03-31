"""
stock/ai.py — Groq LLM integration for AMN Stock
==================================================
Deux fonctions principales appelées par les tâches Celery :

  build_stock_context()   → collecte toutes les données DB et retourne
                             un dict structuré prêt à être injecté dans un prompt.

  generate_summary(ctx)   → appelle Groq (llama-3.3-70b-versatile) et retourne
                             un rapport Markdown détaillé des activités stock.

  generate_suggestions(ctx) → appelle Groq et retourne des recommandations
                               concrètes classées par priorité.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.utils import timezone

logger = logging.getLogger(__name__)

# Seuil d'alerte stock faible — hardcodé
LOW_STOCK_THRESHOLD = 5

# ── Modèle Groq ──────────────────────────────────────────────────────────────
GROQ_MODEL   = "llama-3.3-70b-versatile"
MAX_TOKENS   = 4096
TEMPERATURE  = 0.35        # légèrement créatif mais surtout factuel


# ═══════════════════════════════════════════════════════════════════════════════
# 1. COLLECTE DES DONNÉES
# ═══════════════════════════════════════════════════════════════════════════════

def build_stock_context(days: int = 30) -> dict[str, Any]:
    """
    Interroge la base de données et retourne un contexte complet
    couvrant les <days> derniers jours.
    """
    from stock.models import Discharge, Equipment, FieldReport, StockMovement

    since = timezone.now() - timedelta(days=days)
    now_str = timezone.now().strftime("%d/%m/%Y à %H:%M")
    threshold = LOW_STOCK_THRESHOLD

    # ── équipements ──────────────────────────────────────────────────────────
    all_equip = Equipment.objects.all()
    total_qty  = sum(e.quantity for e in all_equip)
    total_def  = sum(e.defective_quantity for e in all_equip)
    low_stock  = [e for e in all_equip if e.is_low_stock]
    out_of_stock = [e for e in all_equip if e.quantity == 0]

    equip_rows = [
        {
            "name": e.name,
            "reference": e.reference,
            "stock": e.quantity,
            "defective": e.defective_quantity,
            "low": e.is_low_stock,
        }
        for e in all_equip
    ]

    # ── mouvements de stock ──────────────────────────────────────────────────
    movements = StockMovement.objects.filter(date__gte=since)
    total_in   = sum(m.quantity for m in movements if m.movement_type == "in")
    total_out  = sum(m.quantity for m in movements if m.movement_type == "out")

    # Top 5 équipements les plus sortis
    from django.db.models import Sum
    top_out = (
        StockMovement.objects
        .filter(date__gte=since, movement_type="out")
        .values("equipment__name", "equipment__reference")
        .annotate(total=Sum("quantity"))
        .order_by("-total")[:5]
    )

    # ── décharges ────────────────────────────────────────────────────────────
    discharges = Discharge.objects.filter(created_at__gte=since).select_related("user").prefetch_related("items__equipment")
    open_dis   = discharges.filter(status="open")
    closed_dis = discharges.filter(status="closed")

    discharge_rows = []
    for d in discharges:
        items_summary = ", ".join(
            f"{it.equipment.name} ×{it.quantity}" for it in d.items.all()
        )
        discharge_rows.append({
            "id": d.pk,
            "user": d.user.get_full_name() or d.user.username,
            "destination": d.destination,
            "date": d.date.strftime("%d/%m/%Y"),
            "status": d.get_status_display(),
            "items": items_summary or "—",
        })

    # ── rapports de terrain ──────────────────────────────────────────────────
    reports = FieldReport.objects.filter(created_at__gte=since).select_related("discharge__user").prefetch_related("returned_items__equipment")

    returned_good     = 0
    returned_defective = 0
    for r in reports:
        for ri in r.returned_items.all():
            if ri.condition == "good":
                returned_good += ri.quantity_returned
            else:
                returned_defective += ri.quantity_returned

    report_rows = []
    for r in reports:
        items_good = [
            f"{ri.equipment.name} ×{ri.quantity_returned}"
            for ri in r.returned_items.all() if ri.condition == "good"
        ]
        items_def = [
            f"{ri.equipment.name} ×{ri.quantity_returned}"
            for ri in r.returned_items.all() if ri.condition == "defective"
        ]
        report_rows.append({
            "id": r.pk,
            "discharge_id": r.discharge_id,
            "user": r.discharge.user.get_full_name() or r.discharge.user.username,
            "destination": r.discharge.destination,
            "date": r.date.strftime("%d/%m/%Y"),
            "good_returns": ", ".join(items_good) or "—",
            "defective_returns": ", ".join(items_def) or "—",
        })

    return {
        "generated_at": now_str,
        "period_days": days,
        "threshold": threshold,
        # équipements
        "total_equipment_types": all_equip.count(),
        "total_units_in_stock": total_qty,
        "total_defective": total_def,
        "low_stock_count": len(low_stock),
        "out_of_stock_count": len(out_of_stock),
        "equipment_list": equip_rows,
        "low_stock_items": [{"name": e.name, "ref": e.reference, "qty": e.quantity} for e in low_stock],
        "out_of_stock_items": [{"name": e.name, "ref": e.reference} for e in out_of_stock],
        # mouvements
        "total_in": total_in,
        "total_out": total_out,
        "top_5_most_used": list(top_out),
        # décharges
        "total_discharges": discharges.count(),
        "open_discharges": open_dis.count(),
        "closed_discharges": closed_dis.count(),
        "discharge_list": discharge_rows,
        # rapports
        "total_reports": reports.count(),
        "total_returned_good": returned_good,
        "total_returned_defective": returned_defective,
        "report_list": report_rows,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 2. CLIENT GROQ
# ═══════════════════════════════════════════════════════════════════════════════

def _get_groq_client():
    """Retourne une instance du client Groq configurée depuis les settings."""
    try:
        from groq import Groq
    except ImportError as exc:
        raise RuntimeError("Le package 'groq' n'est pas installé. Lancez : pip install groq") from exc

    api_key = getattr(settings, "GROQ_API_KEY", "")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY n'est pas défini dans les variables d'environnement.")

    return Groq(api_key=api_key)


def _chat(system_prompt: str, user_prompt: str) -> str:
    """Appelle Groq et retourne le texte de la réponse."""
    client = _get_groq_client()
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system",  "content": system_prompt},
            {"role": "user",    "content": user_prompt},
        ],
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
    )
    return response.choices[0].message.content.strip()


# ═══════════════════════════════════════════════════════════════════════════════
# 3. GÉNÉRATION DU RAPPORT / RÉSUMÉ
# ═══════════════════════════════════════════════════════════════════════════════

_SYSTEM_SUMMARY = """Tu es un expert en gestion de stock et logistique pour Africa Mobile Networks (AMN),
une entreprise de télécommunications en Afrique. Tu rédiges des rapports professionnels, clairs et détaillés
en français. Tes rapports sont structurés, utilisent des titres, des listes à puces, et donnent des chiffres précis.
Tu mets en évidence les points critiques (stock faible, équipements défectueux, missions non clôturées) en les
signalant clairement. Tu ne fais pas de remplissage : chaque phrase apporte une information utile."""


def generate_summary(ctx: dict) -> str:
    """
    Génère un rapport Markdown détaillé à partir du contexte stock.
    Retourne une chaîne Markdown.
    """
    def _fmt_equip(items: list) -> str:
        if not items:
            return "  _Aucun_"
        return "\n".join(f"  - {e['name']} ({e['ref']}) : {e.get('qty', '?')} unité(s)" for e in items)

    def _fmt_discharges(rows: list) -> str:
        if not rows:
            return "  _Aucune décharge sur la période_"
        return "\n".join(
            f"  - Décharge #{r['id']} | {r['user']} → {r['destination']} ({r['date']}) "
            f"[{r['status']}] : {r['items']}"
            for r in rows
        )

    def _fmt_reports(rows: list) -> str:
        if not rows:
            return "  _Aucun rapport sur la période_"
        return "\n".join(
            f"  - Rapport #{r['id']} | {r['user']} — {r['destination']} ({r['date']})\n"
            f"    Retours OK : {r['good_returns']} | Défectueux : {r['defective_returns']}"
            for r in rows
        )

    def _fmt_top(items: list) -> str:
        if not items:
            return "  _Aucun mouvement_"
        return "\n".join(
            f"  - {t['equipment__name']} ({t['equipment__reference']}) : {t['total']} unités sorties"
            for t in items
        )

    user_prompt = f"""
Génère un rapport de gestion de stock complet et professionnel pour Africa Mobile Networks (AMN).
Période d'analyse : les {ctx['period_days']} derniers jours. Date du rapport : {ctx['generated_at']}.

=== DONNÉES BRUTES ===

--- INVENTAIRE GLOBAL ---
- Nombre de types d'équipements : {ctx['total_equipment_types']}
- Unités totales en stock : {ctx['total_units_in_stock']}
- Unités défectueuses totales : {ctx['total_defective']}
- Équipements en stock faible (≤ {ctx['threshold']}) : {ctx['low_stock_count']}
- Équipements en rupture totale : {ctx['out_of_stock_count']}

Stock faible :
{_fmt_equip(ctx['low_stock_items'])}

Rupture totale :
{_fmt_equip(ctx['out_of_stock_items'])}

--- MOUVEMENTS DE STOCK (30 derniers jours) ---
- Entrées totales : {ctx['total_in']} unités
- Sorties totales : {ctx['total_out']} unités
- Top 5 équipements les plus utilisés :
{_fmt_top(ctx['top_5_most_used'])}

--- DÉCHARGES ---
- Total décharges : {ctx['total_discharges']} ({ctx['open_discharges']} en cours, {ctx['closed_discharges']} clôturées)
{_fmt_discharges(ctx['discharge_list'])}

--- RAPPORTS DE TERRAIN ---
- Total rapports : {ctx['total_reports']}
- Équipements retournés en bon état : {ctx['total_returned_good']} unités
- Équipements retournés défectueux : {ctx['total_returned_defective']} unités
{_fmt_reports(ctx['report_list'])}

=== INSTRUCTIONS ===
Rédige un rapport structuré avec les sections suivantes :
1. Résumé Exécutif (3-5 phrases clés)
2. État de l'Inventaire (analyse détaillée du stock, points critiques)
3. Analyse des Décharges et Missions (patterns, Field Engineers actifs, destinations)
4. Équipements Défectueux et Retours (analyse qualité)
5. Points de Vigilance (alertes, risques immédiats)
6. Conclusion

Utilise des emojis pertinents pour chaque titre de section. Sois précis avec les chiffres.
"""
    return _chat(_SYSTEM_SUMMARY, user_prompt)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. GÉNÉRATION DES SUGGESTIONS
# ═══════════════════════════════════════════════════════════════════════════════

_SYSTEM_SUGGESTIONS = """Tu es un consultant expert en optimisation de chaîne logistique et gestion d'inventaire
pour des entreprises de télécommunications en Afrique. Tu analyses les flux de stock et proposes des
recommandations concrètes, actionnables et priorisées. Tes suggestions sont pratiques, réalistes pour
le contexte africain, et basées uniquement sur les données fournies. Tu classes tes recommandations
par priorité : 🔴 Urgent, 🟠 Important, 🟡 À planifier, 🟢 Amélioration continue."""

def generate_suggestions(ctx: dict) -> str:
    """
    Génère des suggestions d'amélioration priorisées à partir du contexte stock.
    Retourne une chaîne Markdown.
    """
    net_flow = ctx['total_out'] - ctx['total_in']

    user_prompt = f"""
Analyse les données de gestion de stock d'Africa Mobile Networks (AMN) sur les {ctx['period_days']} derniers jours
et propose des recommandations d'optimisation concrètes.

=== MÉTRIQUES CLÉS ===
- Types d'équipements gérés : {ctx['total_equipment_types']}
- Stock total : {ctx['total_units_in_stock']} unités | Défectueux : {ctx['total_defective']} unités
- Taux de défectuosité : {round(ctx['total_defective'] / max(ctx['total_units_in_stock'], 1) * 100, 1)}%
- Équipements en stock critique (≤ {ctx['threshold']}) : {ctx['low_stock_count']}
- Équipements en rupture : {ctx['out_of_stock_count']}
- Flux net (sorties - entrées) : {net_flow:+d} unités (négatif = plus de sorties que d'entrées)
- Décharges actives non clôturées : {ctx['open_discharges']}
- Retours défectueux : {ctx['total_returned_defective']} unités sur {ctx['total_returned_good'] + ctx['total_returned_defective']} retours totaux
- Top équipements les plus utilisés : {', '.join(t['equipment__name'] for t in ctx['top_5_most_used']) or 'N/A'}
- Équipements en rupture : {', '.join(e['name'] for e in ctx['out_of_stock_items']) or 'Aucun'}

=== INSTRUCTIONS ===
Fournis exactement 10 à 15 recommandations réparties sur 4 catégories :

**A. Gestion des Stocks Critiques** (équipements faibles/rupture)
**B. Optimisation des Flux de Décharges** (processus, traçabilité, délais, Field Engineers)
**C. Réduction des Défauts et Pertes** (qualité, maintenance, retours)
**D. Améliorations Systémiques** (processus, formation, indicateurs)

Pour chaque recommandation, indique :
- La priorité (🔴/🟠/🟡/🟢)
- L'action concrète à mener
- Le bénéfice attendu
- Le délai de mise en œuvre suggéré

Termine par un **Tableau de bord de priorités** listant les 3 actions à mener en urgence cette semaine.
"""
    return _chat(_SYSTEM_SUGGESTIONS, user_prompt)
