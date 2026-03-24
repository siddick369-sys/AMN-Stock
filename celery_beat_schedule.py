"""
Celery Beat periodic task schedule.
Add this to settings.py if you prefer static schedule (without django-celery-beat UI).
Otherwise, configure via Django admin → Periodic Tasks.
"""
from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    # Check low stock every 30 minutes
    'check-low-stock-every-30min': {
        'task': 'stock.tasks.check_low_stock',
        'schedule': crontab(minute='*/30'),
    },
}
