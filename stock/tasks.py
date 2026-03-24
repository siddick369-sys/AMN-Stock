from celery import shared_task
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from django.contrib.auth.models import User
import logging

logger = logging.getLogger(__name__)


@shared_task(bind=True, name='stock.tasks.check_low_stock')
def check_low_stock(self):
    """
    Tâche planifiée (Celery Beat) - s'exécute toutes les 30 minutes.
    Vérifie les équipements en stock faible (<= threshold) et envoie
    une alerte email aux administrateurs.
    """
    from stock.models import Equipment

    threshold = getattr(settings, 'LOW_STOCK_THRESHOLD', 5)
    low_stock_items = Equipment.objects.filter(quantity__lte=threshold)

    if not low_stock_items.exists():
        logger.info("Aucun équipement en stock faible.")
        return "Aucun stock faible détecté."

    admin_emails = list(
        User.objects.filter(is_staff=True, is_active=True)
        .exclude(email='')
        .values_list('email', flat=True)
    )
    if settings.ADMIN_EMAIL and settings.ADMIN_EMAIL not in admin_emails:
        admin_emails.append(settings.ADMIN_EMAIL)

    if not admin_emails:
        logger.warning("Aucun email administrateur trouvé pour l'alerte stock faible.")
        return "Aucun destinataire trouvé."

    items_list = "\n".join(
        [f"  - {item.name} ({item.reference}) : {item.quantity} unités restantes"
         for item in low_stock_items]
    )

    subject = f"[AMN Stock] ⚠️ Alerte Stock Faible - {timezone.now().strftime('%d/%m/%Y %H:%M')}"
    message = (
        f"Bonjour,\n\n"
        f"Les équipements suivants ont atteint un niveau de stock critique "
        f"(seuil : {threshold} unités) :\n\n"
        f"{items_list}\n\n"
        f"Veuillez procéder au réapprovisionnement dès que possible.\n\n"
        f"-- \nSystème de Gestion de Stock AMN\n"
        f"Rapport généré le {timezone.now().strftime('%d/%m/%Y à %H:%M')}"
    )

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=admin_emails,
            fail_silently=False,
        )
        logger.info(f"Alerte stock faible envoyée à : {admin_emails}")
    except Exception as exc:
        logger.error(f"Échec envoi email stock faible : {exc}")
        raise self.retry(exc=exc, countdown=60, max_retries=3)

    return f"{low_stock_items.count()} équipement(s) en stock faible signalé(s)."


@shared_task(bind=True, name='stock.tasks.send_hub_alert')
def send_hub_alert(self, equipment_ids, user_id):
    """
    Envoie un email récapitulatif au Hub listant les équipements défectueux
    sélectionnés par l'administrateur.
    """
    from stock.models import Equipment

    try:
        equipments = Equipment.objects.filter(id__in=equipment_ids)
        user = User.objects.get(id=user_id)

        if not equipments.exists():
            return "Aucun équipement sélectionné."

        items_list = "\n".join(
            [f"  - {eq.name} ({eq.reference}) : "
             f"{eq.defective_quantity} unité(s) défectueuse(s) sur {eq.quantity} en stock"
             for eq in equipments]
        )

        subject = f"[AMN Stock] Rapport Équipements Défectueux - {timezone.now().strftime('%d/%m/%Y')}"
        message = (
            f"Bonjour,\n\n"
            f"L'administrateur {user.get_full_name() or user.username} signale "
            f"les équipements défectueux suivants :\n\n"
            f"{items_list}\n\n"
            f"Veuillez prendre les dispositions nécessaires.\n\n"
            f"-- \nSystème de Gestion de Stock AMN\n"
            f"Rapport généré le {timezone.now().strftime('%d/%m/%Y à %H:%M')}"
        )

        hub_email = getattr(settings, 'ADMIN_EMAIL', 'hub@amn.africa')

        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[hub_email],
            fail_silently=False,
        )
        logger.info(f"Alerte Hub envoyée pour {equipments.count()} équipement(s).")
        return f"Email Hub envoyé pour {equipments.count()} équipement(s)."

    except User.DoesNotExist:
        logger.error(f"Utilisateur introuvable (id={user_id})")
        return "Utilisateur introuvable."
    except Exception as exc:
        logger.error(f"Échec envoi email Hub : {exc}")
        raise self.retry(exc=exc, countdown=30, max_retries=3)


@shared_task(name='stock.tasks.notify_low_stock_realtime')
def notify_low_stock_realtime(equipment_id):
    """
    Envoie une notification en temps réel (stockée en session/cache)
    pour un équipement en stock faible.
    """
    from stock.models import Equipment
    from django.core.cache import cache

    try:
        equipment = Equipment.objects.get(id=equipment_id)
        notification = {
            'id': equipment_id,
            'name': equipment.name,
            'reference': equipment.reference,
            'quantity': equipment.quantity,
            'timestamp': timezone.now().isoformat(),
        }
        # Store in cache for polling by frontend
        key = f'low_stock_notification_{equipment_id}'
        cache.set(key, notification, timeout=3600)

        # Maintain a list of active low-stock notifications
        notifications_list = cache.get('low_stock_notifications', [])
        existing_ids = [n['id'] for n in notifications_list]
        if equipment_id not in existing_ids:
            notifications_list.append(notification)
            cache.set('low_stock_notifications', notifications_list, timeout=3600)

        logger.info(f"Notification stock faible créée pour : {equipment.name}")
    except Equipment.DoesNotExist:
        logger.error(f"Équipement introuvable (id={equipment_id})")
