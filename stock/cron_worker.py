"""
cron_worker.py — Tâches périodiques locales (Threaded)
======================================================
Remplace les travailleurs Celery par des exécutions locales directes pour
simplifier l'architecture sur Render Free Tier.

Architecture :
  1. cron-job.org appelle GET /tasks/trigger-celery/?token=<TOKEN> périodiquement.
  2. TaskTriggerView valide le token (env var CRON_TRIGGER_TOKEN).
  3. Un verrou dans le cache Django empêche les exécutions simultanées.
  4. Les tâches sont lancées dans un thread daemon pour ne pas bloquer HTTP.
"""

import logging
import threading
import hmac

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .tasks import check_low_stock

logger = logging.getLogger(__name__)

# Clé du verrou dans le cache
_LOCK_KEY = 'cron_pseudo_worker_lock'
_LOCK_TTL = 55


@method_decorator(csrf_exempt, name='dispatch')
class TaskTriggerView(View):
    """
    Webhook protégé pour déclencher les tâches planifiées.
    """

    def get(self, request):
        return self._handle(request)

    def post(self, request):
        return self._handle(request)

    def _handle(self, request):
        # 1. Validation du token
        expected = getattr(settings, 'CRON_TRIGGER_TOKEN', '')
        provided = request.GET.get('token', '')

        if not expected:
            logger.error("TaskTriggerView : CRON_TRIGGER_TOKEN non défini !")
            return JsonResponse({'error': 'Server misconfigured'}, status=500)

        if not hmac.compare_digest(provided, expected):
            logger.warning("TaskTriggerView : token invalide — IP=%s", 
                           request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR')))
            return JsonResponse({'error': 'Unauthorized'}, status=401)

        # 2. Verrou anti-concurrence
        try:
            locked = cache.add(_LOCK_KEY, timezone.now().isoformat(), _LOCK_TTL)
            if not locked:
                logger.info("TaskTriggerView : déjà en cours d'exécution.")
                return JsonResponse({'status': 'skipped', 'reason': 'already_running'})
        except Exception as exc:
            logger.warning("TaskTriggerView : verrou indisponible (%s).", exc)

        # 3. Exécution non-bloquante
        threading.Thread(target=self._run_all_tasks, daemon=True).start()

        logger.info("TaskTriggerView : cycle déclenché à %s", timezone.now().isoformat())
        return JsonResponse({
            'status': 'triggered',
            'triggered_at': timezone.now().isoformat(),
        })

    def _run_all_tasks(self):
        try:
            logger.info("Démarrage des tâches périodiques...")
            # Lancement de la tâche principale de vérification de stock
            check_low_stock()
            logger.info("Tâches périodiques terminées avec succès.")
        except Exception as exc:
            logger.error("Erreur dans _run_all_tasks (cron_worker) : %s", exc)
        finally:
            cache.delete(_LOCK_KEY)
