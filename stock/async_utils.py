import threading
import logging
from typing import Callable, Any

logger = logging.getLogger(__name__)

def run_async(func: Callable[..., Any], *args: Any, **kwargs: Any) -> threading.Thread:
    """
    Exécute une fonction dans un thread daemon séparé.
    Utilisé pour remplacer les tâches Celery sans broker externe.
    """
    def wrapper():
        try:
            logger.info("Thread-based task started: %s", func.__name__)
            func(*args, **kwargs)
            logger.info("Thread-based task completed: %s", func.__name__)
        except Exception as exc:
            logger.exception("Error in thread-based task %s: %s", func.__name__, exc)

    thread = threading.Thread(target=wrapper, daemon=True)
    thread.start()
    return thread
