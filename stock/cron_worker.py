"""
cron_worker.py — Pseudo-Worker Celery déclenché par cron-job.org
═══════════════════════════════════════════════════════════════════
Remplace les workers Celery permanents sur Render Free Tier.

Architecture :
  1. cron-job.org appelle GET /tasks/trigger-celery/?token=<TOKEN> toutes les X minutes
  2. TaskTriggerView valide le token (env var CRON_TRIGGER_TOKEN)
  3. Un verrou Redis (cache Django) empêche deux exécutions simultanées
  4. check_low_stock s'exécute directement (task.apply — synchrone, pas de broker)
  5. Un worker Celery éphémère (subprocess solo, 45 s max) draine la queue
     Redis pour les tâches WhatsApp, IA, alertes Hub…
  6. La vue retourne 200 immédiatement — tout tourne en thread daemon

Configurer dans cron-job.org :
  URL      : https://amn-stock-web.onrender.com/tasks/trigger-celery/?token=<CRON_TRIGGER_TOKEN>
  Méthode  : GET
  Intervalle : toutes les 5 minutes (ou 1 min pour des tâches plus réactives)
"""

import logging
import subprocess
import sys
import threading

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)

# Clé du verrou dans le cache Redis
_LOCK_KEY = 'cron_pseudo_worker_lock'
# TTL du verrou (s) — doit être > durée max d'exécution ET < intervalle du cron
_LOCK_TTL = 55


@method_decorator(csrf_exempt, name='dispatch')
class TaskTriggerView(View):
    """
    Webhook protégé déclenché par un cron job externe.

    Sécurité  : token secret comparé en temps constant (hmac.compare_digest)
    Locking   : cache.add() atomique — empêche les exécutions concurrentes
    Non-bloquant : retourne 200 immédiatement, tout s'exécute en thread daemon
    """

    def get(self, request):
        return self._handle(request)

    def post(self, request):
        return self._handle(request)

    # ─────────────────────────────────────────────────────────────────────────
    # Dispatcher principal
    # ─────────────────────────────────────────────────────────────────────────

    def _handle(self, request):
        # ── 1. Validation du token ────────────────────────────────────────────
        import hmac
        expected = getattr(settings, 'CRON_TRIGGER_TOKEN', '')
        provided = request.GET.get('token', '')

        if not expected:
            logger.error("TaskTriggerView : CRON_TRIGGER_TOKEN non défini dans les env vars !")
            return JsonResponse({'error': 'Server misconfigured'}, status=500)

        # Comparaison en temps constant pour éviter les timing attacks
        if not hmac.compare_digest(provided, expected):
            logger.warning(
                "TaskTriggerView : token invalide — IP=%s",
                request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR')),
            )
            return JsonResponse({'error': 'Unauthorized'}, status=401)

        # ── 2. Verrou anti-concurrence ────────────────────────────────────────
        # Si Redis est indisponible, cache.add() lève une exception.
        # On continue sans verrou plutôt que de planter la vue.
        try:
            locked = cache.add(_LOCK_KEY, timezone.now().isoformat(), _LOCK_TTL)
            if not locked:
                logger.info("TaskTriggerView : verrou actif, exécution déjà en cours — skipped.")
                return JsonResponse({'status': 'skipped', 'reason': 'worker_already_running'})
        except Exception as exc:
            logger.warning("TaskTriggerView : verrou Redis indisponible (%s) — on continue sans verrou.", exc)

        # ── 3. Lancement non-bloquant (thread daemon) ─────────────────────────
        t = threading.Thread(target=self._run_all_tasks, daemon=True)
        t.start()

        logger.info("TaskTriggerView : cycle déclenché à %s", timezone.now().isoformat())
        return JsonResponse({
            'status': 'triggered',
            'triggered_at': timezone.now().isoformat(),
        })

    # ─────────────────────────────────────────────────────────────────────────
    # Exécution des tâches (thread daemon)
    # ─────────────────────────────────────────────────────────────────────────

    def _run_all_tasks(self):
        """
        Exécute toutes les tâches dans le thread daemon :
          A. check_low_stock — directement (task.apply, pas de broker requis)
          B. Worker éphémère  — draine la queue Redis pour WhatsApp, IA, alertes…
        Libère le verrou à la fin dans tous les cas (finally).
        """
        results = {}
        try:
            self._run_check_low_stock(results)
            self._spawn_ephemeral_worker(results)
        except Exception as exc:
            logger.error("TaskTriggerView : erreur inattendue dans _run_all_tasks : %s", exc)
            results['fatal_error'] = str(exc)
        finally:
            cache.delete(_LOCK_KEY)
            logger.info("TaskTriggerView : cycle terminé — %s", results)

    def _run_check_low_stock(self, results: dict):
        """
        Exécute check_low_stock directement via task.apply() (synchrone).
        Pas de broker Redis nécessaire — idéal si Upstash est indisponible.
        """
        try:
            from stock.tasks import check_low_stock
            outcome = check_low_stock.apply()
            results['check_low_stock'] = str(outcome.result)
            logger.info("TaskTriggerView.check_low_stock : %s", outcome.result)
        except Exception as exc:
            results['check_low_stock'] = f'ERROR: {exc}'
            logger.error("TaskTriggerView.check_low_stock : %s", exc)

    def _spawn_ephemeral_worker(self, results: dict):
        """
        Lance un worker Celery éphémère (pool solo) pour drainer la queue Redis.
        - Pool solo : pas de fork → consommation mémoire minimale
        - Timeout 45 s : le worker s'arrête même si la queue reste non vide
        - --without-heartbeat/mingle/gossip : démarrage ultra-rapide (~2 s)

        Les tâches drainées typiquement :
          whatsapp_discharge_created, whatsapp_field_report_created,
          notify_low_stock_realtime, send_hub_alert,
          ai_generate_summary, ai_generate_suggestions
        """
        try:
            proc = subprocess.run(
                [
                    sys.executable, '-m', 'celery',
                    '-A', 'amn_stock', 'worker',
                    '-P', 'solo',           # pool solo : pas de subprocess fils
                    '--concurrency=1',
                    '--without-heartbeat',  # skip heartbeat (inutile ici)
                    '--without-mingle',     # skip sync avec autres workers
                    '--without-gossip',     # skip gossip protocol
                    '--loglevel=warning',   # logs minimaux
                    '-Q', 'celery',         # queue par défaut
                ],
                timeout=45,
                capture_output=True,
                text=True,
            )
            results['worker_exit'] = proc.returncode
            if proc.stderr and proc.returncode not in (0, -15):
                # Loguer seulement les erreurs réelles (pas les SIGTERM normaux)
                logger.warning("Worker stderr: %s", proc.stderr[-300:])
        except subprocess.TimeoutExpired:
            # Comportement normal : le worker est tué après 45 s de traitement
            results['worker_exit'] = 'timeout_45s'
            logger.info("TaskTriggerView : worker éphémère arrêté après 45 s (normal).")
        except FileNotFoundError:
            results['worker_exit'] = 'celery_not_found'
            logger.error("TaskTriggerView : celery introuvable dans PATH — vérifiez le venv.")
        except Exception as exc:
            results['worker_exit'] = f'ERROR: {exc}'
            logger.error("TaskTriggerView : erreur spawn worker : %s", exc)
