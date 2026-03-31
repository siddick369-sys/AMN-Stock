"""
Async Tasks — AMN Stock (Threaded)
==================================
Chaque événement critique déclenche :
  1. Un email (existant)
  2. Un message WhatsApp via Green API (nouveau)

Événements couverts
-------------------
  • check_low_stock               — déclenché par TaskTriggerView (cron-job.org)
  • notify_low_stock_realtime     — appelé à chaud quand une sortie fait tomber un item en stock faible
  • whatsapp_discharge_created    — décharge créée par un field engineer
  • whatsapp_field_report_created — rapport de terrain soumis / mission clôturée
  • send_hub_alert                — alerte manuelle Hub (équipements défectueux)
  • ai_generate_summary           — génère un rapport IA via Groq et stocke en cache
  • ai_generate_suggestions       — génère des suggestions IA via Groq et stocke en cache
"""

import logging
from django.conf import settings
from django.contrib.auth.models import User

# Seuil d'alerte stock faible — hardcodé
LOW_STOCK_THRESHOLD = 5
from django.core.mail import send_mail, EmailMultiAlternatives
from django.utils import timezone
from django.template.loader import render_to_string

from .whatsapp import send_whatsapp_async

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# HELPERS INTERNES
# ═══════════════════════════════════════════════════════════════════════

def _now_str() -> str:
    return timezone.now().strftime('%d/%m/%Y à %H:%M')


def _header(icon: str, title: str) -> str:
    return f"{icon} *[AMN STOCK]* — {title}\n{'─' * 35}"


# ═══════════════════════════════════════════════════════════════════════
# 1. ALERTE STOCK FAIBLE — PLANIFIÉE (via cron-job.org)
# ═══════════════════════════════════════════════════════════════════════

def check_low_stock():
    """
    Déclenché périodiquement via le webhook cron_worker.
    Si des équipements sont en stock critique (≤ seuil), envoie email et WhatsApp.
    """
    from stock.models import Equipment

    threshold = LOW_STOCK_THRESHOLD
    low_items = Equipment.objects.filter(quantity__lte=threshold)

    if not low_items.exists():
        logger.info("check_low_stock : aucun stock faible détecté.")
        return "Aucun stock faible."

    # ── email ──────────────────────────────────────────────────────────
    admin_emails = list(
        User.objects.filter(is_staff=True, is_active=True)
        .exclude(email='')
        .values_list('email', flat=True)
    )
    if settings.ADMIN_EMAIL:
        for email in settings.ADMIN_EMAIL:
            if email and email not in admin_emails:
                admin_emails.append(email)

    items_text = "\n".join(
        f"  • {item.name} ({item.reference}) : {item.quantity} unité(s)"
        for item in low_items
    )

    if admin_emails:
        subject = f"[AMN Stock] ⚠️ Alerte Stock Faible — {_now_str()}"
        body = (
            f"Bonjour,\n\n"
            f"Les équipements suivants ont atteint un niveau critique (seuil : {threshold}) :\n\n"
            f"{items_text}\n\n"
            f"Veuillez procéder au réapprovisionnement dès que possible.\n\n"
            f"— Système AMN Stock\n{_now_str()}"
        )
        try:
            send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, admin_emails, fail_silently=False)
            logger.info("check_low_stock : email envoyé → %s", admin_emails)
        except Exception as exc:
            logger.error("check_low_stock : échec email : %s", exc)

    # ── WhatsApp ───────────────────────────────────────────────────────
    lines = "\n".join(
        f"  ⚠️ *{item.name}* ({item.reference}) → *{item.quantity}* unité(s) restante(s)"
        for item in low_items
    )
    wa_msg = (
        f"{_header('🚨', 'STOCK CRITIQUE')}\n\n"
        f"Les équipements suivants ont atteint le seuil d'alerte (*≤ {threshold}*) :\n\n"
        f"{lines}\n\n"
        f"📋 Réapprovisionnement requis.\n"
        f"🕐 {_now_str()}"
    )
    send_whatsapp_async(wa_msg)

    return f"{low_items.count()} équipement(s) en stock faible signalé(s)."


# ═══════════════════════════════════════════════════════════════════════
# 2. NOTIFICATION STOCK FAIBLE EN TEMPS RÉEL
# ═══════════════════════════════════════════════════════════════════════

def notify_low_stock_realtime(equipment_id):
    """
    Appelée après chaque sortie faisant tomber un item sous le seuil.
    """
    from stock.models import Equipment
    from django.core.cache import cache

    try:
        equipment = Equipment.objects.get(id=equipment_id)

        # ── cache frontend (polling JS) ────────────────────────────────
        notification = {
            'id': equipment_id,
            'name': equipment.name,
            'reference': equipment.reference,
            'quantity': equipment.quantity,
            'timestamp': timezone.now().isoformat(),
        }
        cache.set(f'low_stock_notification_{equipment_id}', notification, timeout=3600)
        notifs = cache.get('low_stock_notifications', [])
        if equipment_id not in [n['id'] for n in notifs]:
            notifs.append(notification)
            cache.set('low_stock_notifications', notifs, timeout=3600)

        # ── WhatsApp ───────────────────────────────────────────────────
        threshold = LOW_STOCK_THRESHOLD
        wa_msg = (
            f"{_header('⚠️', 'Stock faible détecté')}\n\n"
            f"📦 Équipement : *{equipment.name}*\n"
            f"🔖 Référence  : `{equipment.reference}`\n"
            f"📉 Stock actuel : *{equipment.quantity}* unité(s) _(seuil : {threshold})_\n\n"
            f"➡️ Action requise : réapprovisionnement ou commande urgente.\n"
            f"🕐 {_now_str()}"
        )
        send_whatsapp_async(wa_msg)
        logger.info("notify_low_stock_realtime : WA déclenché en arrière-plan pour %s", equipment.name)

    except Equipment.DoesNotExist:
        logger.error("notify_low_stock_realtime : équipement introuvable (id=%s)", equipment_id)


# ═══════════════════════════════════════════════════════════════════════
# 3. DÉCHARGE CRÉÉE
# ═══════════════════════════════════════════════════════════════════════

def whatsapp_discharge_created(discharge_id):
    """
    Envoie un résumé WhatsApp après création d'une décharge.
    """
    from stock.models import Discharge

    try:
        discharge = Discharge.objects.select_related('user').prefetch_related('items__equipment').get(id=discharge_id)
    except Discharge.DoesNotExist:
        logger.error("whatsapp_discharge_created : décharge introuvable (id=%s)", discharge_id)
        return

    field_engineer = discharge.user.get_full_name() or discharge.user.username
    date_str   = discharge.date.strftime('%d/%m/%Y à %H:%M')

    item_lines = "\n".join(
        f"  • *{item.equipment.name}* ({item.equipment.reference}) × {item.quantity}"
        for item in discharge.items.all()
    )

    wa_msg = (
        f"{_header('📦', 'Nouvelle Décharge Effectuée')}\n\n"
        f"👤 Field Engineer : *{field_engineer}*\n"
        f"🗓️  Date départ : {date_str}\n"
        f"📍 Mission     : _{discharge.destination}_\n"
        f"🔢 Décharge N° : *#{discharge.pk}*\n\n"
        f"📋 *Équipements emportés :*\n{item_lines}\n\n"
        f"✅ Stock mis à jour automatiquement.\n"
        f"🕐 {_now_str()}"
    )

    send_whatsapp_async(wa_msg)
    logger.info("whatsapp_discharge_created : WA déclenché en arrière-plan pour décharge #%s", discharge_id)


# ═══════════════════════════════════════════════════════════════════════
# 4. RAPPORT DE TERRAIN SOUMIS
# ═══════════════════════════════════════════════════════════════════════

def whatsapp_field_report_created(report_id):
    """
    Envoie un résumé WhatsApp après soumission d'un rapport de terrain.
    """
    from stock.models import FieldReport

    try:
        report = (
            FieldReport.objects
            .select_related('discharge__user')
            .prefetch_related('returned_items__equipment', 'discharge__items__equipment')
            .get(id=report_id)
        )
    except FieldReport.DoesNotExist:
        logger.error("whatsapp_field_report_created : rapport introuvable (id=%s)", report_id)
        return

    discharge  = report.discharge
    field_engineer = discharge.user.get_full_name() or discharge.user.username
    date_str   = report.date.strftime('%d/%m/%Y à %H:%M')

    good_lines      = []
    defective_lines = []

    for ret in report.returned_items.all():
        if ret.quantity_returned == 0:
            continue
        line = f"  • *{ret.equipment.name}* × {ret.quantity_returned}"
        if ret.condition == 'good':
            good_lines.append(line)
        else:
            defective_lines.append(f"{line} ⚠️ DÉFECTUEUX")

    good_section = (
        "*✅ Retours en bon état (réintégrés au stock) :*\n" + "\n".join(good_lines)
        if good_lines else "✅ Aucun retour en bon état."
    )
    defective_section = (
        "*🔴 Équipements défectueux :*\n" + "\n".join(defective_lines)
        if defective_lines else "🔴 Aucun équipement défectueux."
    )

    wa_msg = (
        f"{_header('📝', 'Rapport de Terrain Soumis')}\n\n"
        f"👤 Field Engineer : *{field_engineer}*\n"
        f"📍 Mission    : _{discharge.destination}_\n"
        f"📅 Retour     : {date_str}\n"
        f"🔢 Rapport N° : *#{report.pk}* (Décharge #{discharge.pk})\n\n"
        f"{good_section}\n\n"
        f"{defective_section}\n\n"
        f"🏁 Mission clôturée — stock mis à jour.\n"
        f"🕐 {_now_str()}"
    )

    send_whatsapp_async(wa_msg)
    logger.info("whatsapp_field_report_created : WA déclenché en arrière-plan pour rapport #%s", report_id)


# ═══════════════════════════════════════════════════════════════════════
# 5. ALERTE HUB — équipements défectueux
# ═══════════════════════════════════════════════════════════════════════

def send_hub_alert(equipment_ids, user_id):
    from stock.models import Equipment

    try:
        equipments = Equipment.objects.filter(id__in=equipment_ids)
        user       = User.objects.get(id=user_id)

        if not equipments.exists():
            return "Aucun équipement sélectionné."

        admin_name = user.get_full_name() or user.username

        # ── email ──────────────────────────────────────────────────────
        email_lines = "\n".join(
            f"  - {eq.name} ({eq.reference}) : "
            f"{eq.defective_quantity} défectueux / {eq.quantity} en stock"
            for eq in equipments
        )
        subject = f"[AMN Stock] Rapport Équipements Défectueux — {_now_str()}"
        body = (
            f"Bonjour,\n\n"
            f"L'administrateur *{admin_name}* signale les équipements défectueux suivants :\n\n"
            f"{email_lines}\n\n"
            f"Veuillez prendre les dispositions nécessaires.\n\n"
            f"— Système AMN Stock\n{_now_str()}"
        )
        hub_email = getattr(settings, 'ADMIN_EMAIL', ['hub@amn.africa'])
        if isinstance(hub_email, str): hub_email = [hub_email]
        
        try:
            send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, hub_email, fail_silently=False)
        except Exception as exc:
            logger.error("send_hub_alert : échec email : %s", exc)

        # ── WhatsApp ───────────────────────────────────────────────────
        wa_lines = "\n".join(
            f"  🔴 *{eq.name}* ({eq.reference})\n"
            f"     Défectueux : *{eq.defective_quantity}* | Stock : {eq.quantity}"
            for eq in equipments
        )
        wa_msg = (
            f"{_header('🔧', 'Alerte Hub — Équipements Défectueux')}\n\n"
            f"👤 Regional Manager : *{admin_name}*\n"
            f"📅 Date        : {_now_str()}\n\n"
            f"*Équipements concernés :*\n{wa_lines}\n\n"
            f"📬 Un email récapitulatif a également été envoyé au Hub.\n"
            f"⚙️  Veuillez prendre les dispositions nécessaires."
        )
        send_whatsapp_async(wa_msg)
        logger.info("send_hub_alert : WA déclenché en arrière-plan pour %d équipement(s).", equipments.count())
    except User.DoesNotExist:
        logger.error("send_hub_alert : utilisateur introuvable (id=%s)", user_id)
    except Exception as exc:
        logger.error("send_hub_alert : erreur inattendue : %s", exc)


# ═══════════════════════════════════════════════════════════════════════
# 6. RAPPORT IA
# ═══════════════════════════════════════════════════════════════════════

def ai_generate_summary(cache_key: str, days: int = 30):
    from django.core.cache import cache
    from .ai import build_stock_context, generate_summary

    try:
        logger.info("ai_generate_summary : collecte des données…")
        ctx = build_stock_context(days=days)
        markdown = generate_summary(ctx)
        result = {
            "status": "ready",
            "content": markdown,
            "generated_at": ctx["generated_at"],
            "stats": ctx.get("stats", {})
        }
        cache.set(cache_key, result, timeout=7200)
        logger.info("ai_generate_summary : rapport prêt.")
    except Exception as exc:
        logger.error("ai_generate_summary : erreur : %s", exc)
        cache.set(cache_key, {"status": "error", "error": str(exc)}, timeout=600)


# ═══════════════════════════════════════════════════════════════════════
# 7. SUGGESTIONS IA
# ═══════════════════════════════════════════════════════════════════════

def ai_generate_suggestions(cache_key: str, days: int = 30):
    from django.core.cache import cache
    from .ai import build_stock_context, generate_suggestions

    try:
        logger.info("ai_generate_suggestions : collecte des données…")
        ctx = build_stock_context(days=days)
        markdown = generate_suggestions(ctx)
        result = {
            "status": "ready",
            "content": markdown,
            "generated_at": ctx["generated_at"],
        }
        cache.set(cache_key, result, timeout=7200)
    except Exception as exc:
        logger.error("ai_generate_suggestions : erreur : %s", exc)
        cache.set(cache_key, {"status": "error", "error": str(exc)}, timeout=600)


# ─────────────────────────────────────────────────────────────────────────────
# VÉRIFICATION EMAIL / OTP
# ─────────────────────────────────────────────────────────────────────────────

def send_verification_email(user_id: int, code: str):
    try:
        user = User.objects.get(pk=user_id)
        subject = "[AMN Stock] Vérification de votre compte"
        text_body = f"Code OTP : {code}"
        html_body = render_to_string('emails/verification.html', {'user': user, 'code': code})
        msg = EmailMultiAlternatives(subject, text_body, settings.DEFAULT_FROM_EMAIL, [user.email])
        msg.attach_alternative(html_body, "text/html")
        msg.send(fail_silently=False)
        logger.info("send_verification_email envoyé à %s", user.email)
    except Exception as exc:
        logger.error("send_verification_email échec : %s", exc)


def send_password_reset_email(user_id: int, code: str):
    try:
        user = User.objects.get(pk=user_id)
        subject = "[AMN Stock] Réinitialisation de votre mot de passe"
        text_body = f"Code OTP : {code}"
        html_body = render_to_string('emails/password_reset.html', {'user': user, 'code': code})
        msg = EmailMultiAlternatives(subject, text_body, settings.DEFAULT_FROM_EMAIL, [user.email])
        msg.attach_alternative(html_body, "text/html")
        msg.send(fail_silently=False)
        logger.info("send_password_reset_email envoyé à %s", user.email)
    except Exception as exc:
        logger.error("send_password_reset_email échec : %s", exc)
