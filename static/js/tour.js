/**
 * AMN-Stock — Tutoriel interactif (driver.js v1.x)
 * ─────────────────────────────────────────────────
 * Le tour est défini par page via data-page="{{ url_name }}" sur <body>.
 * Pour l'instant : tutoriel complet sur le Dashboard.
 *
 * Flux spécial — Modal "Ajouter un équipement" :
 *   Étape 6 → highlight bouton Ajouter
 *   onNextClick → ouvre le modal Bootstrap programmatiquement
 *     → attend l'événement "shown.bs.modal" (fin d'animation CSS)
 *     → avance vers l'étape 7 (champ Nom inside le modal)
 *   onDeselected étape 7 → ferme le modal proprement
 *   onDestroyStarted global → ferme le modal si le tour est quitté en cours
 */

(function () {
  'use strict';

  /* ── Garde : driver.js non chargé ───────────────────────────────────────── */
  if (typeof window.driver === 'undefined') {
    console.warn('[AMN Tour] driver.js non chargé — tutoriel désactivé.');
    return;
  }

  const { driver } = window.driver.js;

  /* ── Helpers ──────────────────────────────────────────────────────────── */
  const STORAGE_KEY = 'amn_tour_v1_done';

  /** Ouvre le modal Bootstrap addModal en supprimant son backdrop natif
   *  (driver.js fournit déjà son propre overlay sombre). */
  function openAddModal(onShown) {
    const modalEl = document.getElementById('addModal');
    if (!modalEl) { onShown(); return; }

    // backdrop:false → pas de double fond sombre (driver.js en a déjà un)
    const bsModal = new bootstrap.Modal(modalEl, {
      backdrop: false,
      keyboard: false,
    });

    /* Écoute la fin de l'animation CSS avant d'avancer le tour */
    modalEl.addEventListener('shown.bs.modal', function handler() {
      modalEl.removeEventListener('shown.bs.modal', handler);
      onShown();
    });

    bsModal.show();
  }

  /** Ferme le modal addModal s'il est ouvert. */
  function closeAddModal() {
    const modalEl = document.getElementById('addModal');
    if (!modalEl) return;
    const bsModal = bootstrap.Modal.getInstance(modalEl);
    if (bsModal) bsModal.hide();
  }

  /* ── Définition des étapes du tutoriel Dashboard ──────────────────────── */
  function buildDashboardSteps() {
    return [

      /* ── Étape 1 : Bienvenue (popover centré, aucun élément ciblé) ── */
      {
        popover: {
          title: '👋 Bienvenue sur AMN Stock !',
          description:
            'Ce court tutoriel vous guide à travers les fonctionnalités '
            + 'principales de l\'application. Utilisez les flèches ou les '
            + 'touches ← → du clavier pour naviguer.',
          side: 'over',
          align: 'center',
        },
      },

      /* ── Étape 2 : Sidebar ── */
      {
        element: '#sidebar',
        popover: {
          title: '<i class="bi bi-layout-sidebar-inset me-1"></i> Navigation',
          description:
            'Le menu latéral donne accès à toutes les sections : '
            + '<strong>Dashboard</strong>, <strong>Décharges</strong>, '
            + '<strong>Rapports terrain</strong> et la '
            + '<strong>Gestion des comptes</strong> (admins uniquement).',
          side: 'right',
          align: 'start',
        },
      },

      /* ── Étape 3 : Cloche de notifications ── */
      {
        element: '#notif-btn',
        popover: {
          title: '<i class="bi bi-bell-fill me-1"></i> Alertes stock',
          description:
            'Cette cloche se colore en rouge dès qu\'un équipement passe '
            + 'en <strong>stock critique</strong> (en dessous du seuil '
            + 'configuré). Un email et une alerte WhatsApp sont également '
            + 'envoyés automatiquement.',
          side: 'bottom',
          align: 'end',
        },
      },

      /* ── Étape 4 : Cartes statistiques ── */
      {
        element: '#tour-stat-cards',
        popover: {
          title: '<i class="bi bi-bar-chart-fill me-1"></i> Indicateurs clés',
          description:
            'Vue d\'ensemble en temps réel : nombre total d\'équipements, '
            + 'articles en stock faible, décharges actives et '
            + 'unités totales disponibles.',
          side: 'bottom',
          align: 'start',
        },
      },

      /* ── Étape 5 : Tableau d'inventaire ── */
      {
        element: '#equipment-table',
        popover: {
          title: '<i class="bi bi-table me-1"></i> Inventaire',
          description:
            'Liste complète de vos équipements avec leur stock et leur statut. '
            + 'Vous pouvez <strong>modifier</strong> ou <strong>supprimer</strong> '
            + 'un équipement via les boutons de chaque ligne.',
          side: 'top',
          align: 'start',
        },
      },

      /* ── Étape 6 : Bouton "Ajouter" ──────────────────────────────────────
         onNextClick ouvre le modal Bootstrap puis avance le tour
         une fois l'animation terminée (shown.bs.modal).              ── */
      {
        element: '#tour-btn-add',
        popover: {
          title: '<i class="bi bi-plus-circle-fill me-1"></i> Ajouter un équipement',
          description:
            'Cliquez sur <strong>Suivant</strong> pour voir comment créer '
            + 'un nouvel équipement dans le formulaire.',
          side: 'bottom',
          align: 'end',
        },
        onNextClick: (_el, _step, { driver: driverInstance }) => {
          /* On ouvre le modal AVANT d'avancer.
             driver.moveNext() est appelé uniquement depuis le callback
             "shown.bs.modal" pour garantir que le DOM est prêt. */
          openAddModal(() => {
            driverInstance.moveNext();
          });
          /* ⚠️ On ne retourne rien → driver.js attend moveNext() manuel */
        },
      },

      /* ── Étape 7 : Champ "Nom" à l'intérieur du modal ───────────────────
         onDeselected ferme le modal quand l'utilisateur quitte cette étape
         (Suivant, Précédent ou fermeture du tour).                   ── */
      {
        element: '#add-name-input',
        popover: {
          title: '<i class="bi bi-pencil-fill me-1"></i> Nom de l\'équipement',
          description:
            'Saisissez ici le nom complet de l\'équipement '
            + '(ex&nbsp;: <em>Routeur 4G LTE</em>). '
            + 'Les champs <strong>Référence</strong> et '
            + '<strong>Quantité</strong> sont également obligatoires '
            + 'pour enregistrer l\'article.',
          side: 'bottom',
          align: 'start',
        },
        onDeselected: () => {
          /* Ferme proprement le modal quelle que soit la raison
             (clic Suivant, Précédent, ou abandon du tour). */
          closeAddModal();
        },
      },

      /* ── Étape 8 : Bouton "Alerter le Hub" ── */
      {
        element: '#tour-btn-hub',
        popover: {
          title: '<i class="bi bi-bell-fill me-1"></i> Alerter le Hub',
          description:
            'Signalez des équipements défectueux directement au Hub central. '
            + 'Une notification WhatsApp est envoyée automatiquement '
            + 'à l\'équipe de maintenance.',
          side: 'bottom',
          align: 'end',
        },
      },

      /* ── Étape 9 : Lien Décharges ── */
      {
        element: '#nav-discharges',
        popover: {
          title: '<i class="bi bi-box-arrow-up-right me-1"></i> Décharges',
          description:
            'Gérez les sorties d\'équipements pour les missions terrain. '
            + 'Chaque décharge est tracée et peut être créée par '
            + '<strong>formulaire manuel</strong> ou par '
            + '<strong>commande vocale</strong> grâce à l\'assistant IA.',
          side: 'right',
          align: 'center',
        },
      },

      /* ── Étape 10 : Fin du tutoriel ── */
      {
        popover: {
          title: '🎉 Vous êtes prêt !',
          description:
            'Vous connaissez maintenant l\'essentiel d\'AMN Stock. '
            + 'Le bouton <strong>?</strong> en haut à droite vous permet '
            + 'de relancer ce tutoriel à tout moment. Bonne gestion !',
          side: 'over',
          align: 'center',
        },
      },

    ]; // fin steps
  }

  /* ── Constructeur du driverObj Dashboard ─────────────────────────────── */
  function createDashboardTour() {
    return driver({
      /* ── Options globales ── */
      animate: true,
      overlayColor: '#000',
      overlayOpacity: 0.72,
      smoothScroll: true,

      /* ── Navigation ── */
      allowKeyboardControl: true,   // flèches clavier
      allowClose: false,            // clic sur l'overlay → ne ferme pas

      /* ── Progression ── */
      showProgress: true,
      progressText: 'Étape {{current}} sur {{total}}',

      /* ── Labels boutons (en français) ── */
      nextBtnText: 'Suivant →',
      prevBtnText: '← Précédent',
      doneBtnText: 'Terminer ✓',

      /* ── Popover global ── */
      popoverClass: 'amn-tour-popover',

      /* ── Hook global : fermeture du tour ────────────────────────────────
         Appelé quand l'utilisateur clique "Terminer" ou ferme le tour.
         Garantit que le modal est bien fermé même si le tour est interrompu
         avant l'étape 8.                                              ── */
      onDestroyStarted: (_el, _step, { driver: driverInstance }) => {
        closeAddModal();
        /* On laisse driver.js terminer la destruction normalement */
        driverInstance.destroy();
      },

      /* ── Hook global : fin effective ── */
      onDestroyed: () => {
        localStorage.setItem(STORAGE_KEY, '1');
      },

      steps: buildDashboardSteps(),
    });
  }

  /* ── API publique : window.startAppTour() ─────────────────────────────── */
  window.startAppTour = function () {
    const page = document.body.dataset.page;

    if (page === 'dashboard') {
      /* Réinitialise le flag pour permettre un re-lancement manuel */
      localStorage.removeItem(STORAGE_KEY);
      createDashboardTour().drive();
      return;
    }

    /* Sur les autres pages : message informatif via toast (si dispo) */
    if (typeof showToast === 'function') {
      showToast('Le tutoriel est disponible sur le <strong>Dashboard</strong>.', 'info');
    }
  };

  /* ── Auto-démarrage à la première connexion ───────────────────────────── */
  document.addEventListener('DOMContentLoaded', function () {
    if (localStorage.getItem(STORAGE_KEY)) return;   // déjà vu
    if (document.body.dataset.page !== 'dashboard') return; // mauvaise page

    /* Délai court pour laisser la page se stabiliser
       (rendu Bootstrap, notifications, etc.)                           */
    setTimeout(function () {
      createDashboardTour().drive();
    }, 900);
  });

}());
