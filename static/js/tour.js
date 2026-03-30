/**
 * AMN-Stock — Tutoriel interactif (driver.js v1.x)
 * ─────────────────────────────────────────────────
 * Le tour est défini par page via data-page="{{ url_name }}" sur <body>.
 * Pages couvertes :
 *   - dashboard        (10 étapes, modal addModal async)
 *   - discharge_list   (6 étapes)
 *   - discharge_create (7 étapes, modal voiceModal async)
 *   - discharge_detail (5 étapes)
 *   - field_report_list   (4 étapes)
 *   - field_report_create (5 étapes)
 *   - field_report_detail (4 étapes)
 *   - user_list        (6 étapes, modal userModal async)
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
  const STORAGE_KEY = 'amn_tour_v2_done';
  console.log("[AMN Tour] Chargement du script...");

  /**
   * Ouvre un modal Bootstrap en supprimant son backdrop natif
   * (driver.js fournit déjà son propre overlay sombre).
   */
  function openModal(modalId, onShown) {
    console.log(`[AMN Tour] openModal appelé pour: ${modalId}`);
    if (typeof bootstrap === 'undefined') {
      console.error("[AMN Tour] Bootstrap non trouvé!");
      onShown(); return;
    }
    const modalEl = document.getElementById(modalId);
    if (!modalEl) {
      console.warn(`[AMN Tour] Élément modal non trouvé: ${modalId}`);
      onShown(); return;
    }

    const bsModal = bootstrap.Modal.getOrCreateInstance(modalEl, {
      backdrop: false,
      keyboard: false,
    });

    if (modalEl.classList.contains('show')) {
      console.log(`[AMN Tour] Modal ${modalId} est déjà ouvert.`);
      onShown();
      return;
    }

    const handler = () => {
      console.log(`[AMN Tour] Modal ${modalId} ouvert (événement shown).`);
      modalEl.removeEventListener('shown.bs.modal', handler);
      onShown();
    };
    modalEl.addEventListener('shown.bs.modal', handler);

    // Sécurité accrue : 1.5s
    setTimeout(() => {
      modalEl.removeEventListener('shown.bs.modal', handler);
      if (modalEl.classList.contains('show')) {
        console.log(`[AMN Tour] Modal ${modalId} ouvert (via timeout).`);
        onShown();
      }
    }, 1500);

    bsModal.show();
  }

  /** Ferme un modal Bootstrap s'il est ouvert. */
  function closeModal(modalId) {
    const modalEl = document.getElementById(modalId);
    if (!modalEl) return;
    const bsModal = bootstrap.Modal.getInstance(modalEl);
    if (bsModal) bsModal.hide();
  }

  /* ── Options communes à tous les tours ───────────────────────────────── */
  function commonOpts(extraOpts) {
    return Object.assign({
      animate: true,
      overlayColor: '#000',
      overlayOpacity: 0.75,
      smoothScroll: true,
      allowKeyboardControl: true,
      allowClose: true,
      showProgress: true,
      progressText: 'Étape {{current}} sur {{total}}',
      nextBtnText: 'Suivant →',
      prevBtnText: '← Précédent',
      doneBtnText: 'Terminer ✓',
      popoverClass: 'amn-tour-popover',
      // On multiplie les chances de sauvegarder l'état
      onClose: () => {
        console.log("[AMN Tour] onClose déclenché.");
        localStorage.setItem(STORAGE_KEY, '1');
      },
      onCloseClick: () => {
        console.log("[AMN Tour] onCloseClick déclenché.");
        localStorage.setItem(STORAGE_KEY, '1');
      },
      onDestroyed: () => {
        console.log("[AMN Tour] onDestroyed déclenché.");
        localStorage.setItem(STORAGE_KEY, '1');
      },
    }, extraOpts);
  }

  /* ════════════════════════════════════════════════════════════════════════
   * DASHBOARD
   * ════════════════════════════════════════════════════════════════════════ */
  function buildDashboardSteps() {
    return [
      {
        popover: {
          title: '👋 Bienvenue sur AMN Stock !',
          description:
            'Ce court tutoriel vous guide à travers les fonctionnalités '
            + 'principales de l\'application. Utilisez les flèches ou les '
            + 'touches ← → du clavier pour naviguer.',
          side: 'over', align: 'center',
        },
      },
      {
        element: '#sidebar',
        popover: {
          title: '<i class="bi bi-layout-sidebar-inset me-1"></i> Navigation',
          description:
            'Le menu latéral donne accès à toutes les sections : '
            + '<strong>Dashboard</strong>, <strong>Décharges</strong>, '
            + '<strong>Rapports terrain</strong> et la '
            + '<strong>Gestion des comptes</strong> (Regional Managers uniquement).',
          side: 'right', align: 'start',
        },
      },
      {
        element: '#notif-btn',
        popover: {
          title: '<i class="bi bi-bell-fill me-1"></i> Alertes stock',
          description:
            'Cette cloche se colore en rouge dès qu\'un équipement passe '
            + 'en <strong>stock critique</strong>. Un email et une alerte '
            + 'WhatsApp sont également envoyés automatiquement.',
          side: 'bottom', align: 'end',
        },
      },
      {
        element: '#tour-stat-cards',
        popover: {
          title: '<i class="bi bi-bar-chart-fill me-1"></i> Indicateurs clés',
          description:
            'Vue d\'ensemble en temps réel : nombre total d\'équipements, '
            + 'articles en stock faible, décharges actives et '
            + 'unités totales disponibles.',
          side: 'bottom', align: 'start',
        },
      },
      {
        element: '#equipment-table',
        popover: {
          title: '<i class="bi bi-table me-1"></i> Inventaire',
          description:
            'Liste complète de vos équipements avec leur stock et leur statut. '
            + 'Vous pouvez <strong>modifier</strong> ou <strong>supprimer</strong> '
            + 'un équipement via les boutons de chaque ligne.',
          side: 'top', align: 'start',
        },
      },
      {
        element: '#tour-btn-add',
        popover: {
          title: '<i class="bi bi-plus-circle-fill me-1"></i> Ajouter un équipement',
          description:
            'Cliquez sur <strong>Suivant</strong> pour voir comment créer '
            + 'un nouvel équipement dans le formulaire.',
          side: 'bottom', align: 'end',
        },
        onNextClick: (_el, _step, { driver: d }) => {
          openModal('addModal', () => { d.moveNext(); });
        },
      },
      {
        element: '#add-name-input',
        popover: {
          title: '<i class="bi bi-pencil-fill me-1"></i> Nom de l\'équipement',
          description:
            'Saisissez ici le nom complet de l\'équipement '
            + '(ex&nbsp;: <em>Routeur 4G LTE</em>). '
            + 'Les champs <strong>Référence</strong> et '
            + '<strong>Quantité</strong> sont également obligatoires.',
          side: 'bottom', align: 'start',
        },
        onDeselected: () => { closeModal('addModal'); },
      },
      {
        element: '#tour-btn-hub',
        popover: {
          title: '<i class="bi bi-bell-fill me-1"></i> Alerter le Hub',
          description:
            'Signalez des équipements défectueux directement au Hub central. '
            + 'Une notification WhatsApp est envoyée automatiquement '
            + 'à l\'équipe de maintenance.',
          side: 'bottom', align: 'end',
        },
      },
      {
        element: '#nav-discharges',
        popover: {
          title: '<i class="bi bi-box-arrow-up-right me-1"></i> Décharges',
          description:
            'Gérez les sorties d\'équipements pour les missions terrain. '
            + 'Chaque décharge est tracée et peut être créée par '
            + '<strong>formulaire manuel</strong> ou par '
            + '<strong>commande vocale</strong> grâce à l\'assistant IA.',
          side: 'right', align: 'center',
        },
      },
      {
        popover: {
          title: '🎉 Vous êtes prêt !',
          description: 'Le tableau de bord se mettra à jour en temps réel. Bonne gestion !',
          side: 'bottom',
          align: 'center',
          onNextClick: (_el, _step, { driver: d }) => {
            console.log("[AMN Tour] Fin du tour (Dashboard).");
            localStorage.setItem(STORAGE_KEY, '1');
            d.destroy();
          }
        }
      },
    ];
  }

  function createDashboardTour() {
    return driver(commonOpts({
      onDestroyStarted: (_el, _step, { driver: d }) => {
        console.log("[AMN Tour] Destruction Dashboard...");
        closeModal('addModal');
      },
      steps: buildDashboardSteps(),
    }));
  }

  /* ════════════════════════════════════════════════════════════════════════
   * DISCHARGE LIST
   * ════════════════════════════════════════════════════════════════════════ */
  function buildDischargeListSteps() {
    return [
      {
        popover: {
          title: '<i class="bi bi-box-arrow-up-right me-1"></i> Gestion des Décharges',
          description:
            'Cette page centralise toutes les sorties d\'équipements. '
            + 'Chaque décharge représente un ensemble de matériel confié à un '
            + 'Field Engineer pour une mission terrain.',
          side: 'over', align: 'center',
        },
      },
      {
        element: '#tour-btn-new-discharge',
        popover: {
          title: '<i class="bi bi-plus-circle-fill me-1"></i> Nouvelle décharge',
          description:
            'Créez une nouvelle décharge pour enregistrer les équipements '
            + 'remis à un Field Engineer avant une mission.',
          side: 'bottom', align: 'end',
        },
      },
      {
        element: '#tour-discharge-filters',
        popover: {
          title: '<i class="bi bi-funnel-fill me-1"></i> Filtres de recherche',
          description:
            'Filtrez les décharges par <strong>Field Engineer</strong>, '
            + '<strong>statut</strong> (En cours / Clôturée) ou '
            + '<strong>période de date</strong> pour retrouver rapidement une mission.',
          side: 'bottom', align: 'start',
        },
      },
      {
        element: '#tour-discharge-table',
        popover: {
          title: '<i class="bi bi-table me-1"></i> Liste des décharges',
          description:
            'Le tableau affiche toutes les décharges avec leur Field Engineer, '
            + 'destination, date et statut. Cliquez sur '
            + '<i class="bi bi-eye-fill"></i> pour voir le détail, '
            + '<i class="bi bi-pencil-fill"></i> pour modifier (si en cours), '
            + 'ou <i class="bi bi-file-earmark-plus-fill"></i> pour créer un rapport de retour.',
          side: 'top', align: 'start',
        },
      },
      {
        popover: {
          title: '💡 Cycle de vie d\'une décharge',
          description:
            '<strong>1.</strong> Créer la décharge → les équipements quittent le stock.<br>'
            + '<strong>2.</strong> Mission terrain effectuée.<br>'
            + '<strong>3.</strong> Créer le rapport de retour → le stock est réintégré automatiquement.<br>'
            + 'La décharge passe alors en statut <strong>Clôturée</strong>.',
          side: 'over', align: 'center',
        },
      },
      {
        popover: {
          title: '✅ Prêt à gérer les décharges !',
          description:
            'Utilisez le bouton <strong>?</strong> pour relancer ce tutoriel '
            + 'à tout moment.',
          side: 'over', align: 'center',
        },
      },
    ];
  }

  function createDischargeListTour() {
    return driver(commonOpts({ steps: buildDischargeListSteps() }));
  }

  /* ════════════════════════════════════════════════════════════════════════
   * DISCHARGE CREATE
   * ════════════════════════════════════════════════════════════════════════ */
  function buildDischargeCreateSteps() {
    return [
      {
        popover: {
          title: '<i class="bi bi-plus-circle-fill me-1"></i> Créer une décharge',
          description:
            'Ce formulaire vous permet d\'enregistrer les équipements remis à un '
            + 'Field Engineer avant une mission. Vous pouvez le remplir '
            + '<strong>manuellement</strong> ou par <strong>commande vocale</strong>.',
          side: 'over', align: 'center',
        },
      },
      {
        element: '#destination-input',
        popover: {
          title: '<i class="bi bi-geo-alt-fill me-1"></i> Destination',
          description:
            'Indiquez le lieu ou l\'intitulé de la mission '
            + '(ex&nbsp;: <em>Site Douala - Maintenance BTS</em>). '
            + 'Ce champ est obligatoire.',
          side: 'bottom', align: 'start',
        },
      },
      {
        element: '#items-container',
        popover: {
          title: '<i class="bi bi-boxes me-1"></i> Équipements de la décharge',
          description:
            'Ajoutez ici chaque équipement avec sa quantité. '
            + 'Cliquez sur <strong>Ajouter un équipement</strong> pour insérer '
            + 'une nouvelle ligne. Le stock disponible est vérifié en temps réel.',
          side: 'top', align: 'start',
        },
      },
      {
        element: '#tour-btn-voice',
        popover: {
          title: '<i class="bi bi-mic-fill me-1"></i> Assistant vocal IA',
          description:
            'Cliquez sur <strong>Suivant</strong> pour découvrir l\'assistant vocal '
            + 'qui vous permet de dicter toute la décharge en une seule commande.',
          side: 'bottom', align: 'end',
        },
        onNextClick: (_el, _step, { driver: d }) => {
          openModal('voiceModal', () => { d.moveNext(); });
        },
      },
      {
        element: '#voiceModal',
        popover: {
          title: '<i class="bi bi-mic-fill me-1"></i> Interface vocale',
          description:
            'Appuyez sur le bouton micro et dictez votre décharge en langage naturel. '
            + 'Exemple : <em>« Départ pour Yaoundé avec 3 routeurs 4G et 5 câbles RJ45 »</em>. '
            + 'L\'IA Groq Whisper transcrit et remplit le formulaire automatiquement.',
          side: 'top', align: 'center',
        },
        onDeselected: () => { closeModal('voiceModal'); },
      },
      {
        element: '#submit-btn',
        popover: {
          title: '<i class="bi bi-check-circle-fill me-1"></i> Valider la décharge',
          description:
            'Une fois tous les équipements saisis, cliquez sur '
            + '<strong>Enregistrer</strong> pour créer la décharge. '
            + 'Le stock sera mis à jour immédiatement.',
          side: 'top', align: 'end',
        },
      },
      {
        popover: {
          title: '✅ Décharge prête à créer !',
          description:
            'Remplissez le formulaire et validez. Un rapport de retour '
            + 'devra être créé à la fin de la mission pour réintégrer '
            + 'les équipements au stock.',
          side: 'over', align: 'center',
        },
      },
    ];
  }

  function createDischargeCreateTour() {
    return driver(commonOpts({
      onDestroyStarted: () => {
        closeModal('voiceModal');
      },
      steps: buildDischargeCreateSteps(),
    }));
  }

  /* ════════════════════════════════════════════════════════════════════════
   * DISCHARGE DETAIL
   * ════════════════════════════════════════════════════════════════════════ */
  function buildDischargeDetailSteps() {
    return [
      {
        popover: {
          title: '<i class="bi bi-box-arrow-up-right me-1"></i> Détail de la décharge',
          description:
            'Cette page affiche toutes les informations d\'une décharge : '
            + 'Field Engineer assigné, destination, date de départ et liste complète '
            + 'des équipements emportés.',
          side: 'over', align: 'center',
        },
      },
      {
        element: '#tour-discharge-detail-card',
        popover: {
          title: '<i class="bi bi-info-circle-fill me-1"></i> Informations de la décharge',
          description:
            'Retrouvez ici le Field Engineer responsable, la destination de la mission, '
            + 'la date de départ et le statut actuel '
            + '(<strong>En cours</strong> ou <strong>Clôturée</strong>).',
          side: 'bottom', align: 'start',
        },
      },
      {
        popover: {
          title: '<i class="bi bi-boxes me-1"></i> Équipements emportés',
          description:
            'Le tableau liste chaque équipement avec sa référence et la quantité '
            + 'sortie du stock pour cette mission.',
          side: 'over', align: 'center',
        },
      },
      ...(document.getElementById('tour-btn-create-report') ? [{
        element: '#tour-btn-create-report',
        popover: {
          title: '<i class="bi bi-file-earmark-plus-fill me-1"></i> Rapport de retour',
          description:
            'À la fin de la mission, créez un rapport de retour pour indiquer '
            + 'quels équipements ont été ramenés, leur état (bon état / défectueux) '
            + 'et les actions réalisées sur le terrain.',
          side: 'top', align: 'center',
        },
      }] : []),
      {
        popover: {
          title: '✅ Vue complète de la décharge',
          description:
            'Revenez sur cette page après la mission pour créer le rapport '
            + 'de retour et clôturer la décharge.',
          side: 'over', align: 'center',
        },
      },
    ];
  }

  function createDischargeDetailTour() {
    return driver(commonOpts({ steps: buildDischargeDetailSteps() }));
  }

  /* ════════════════════════════════════════════════════════════════════════
   * FIELD REPORT LIST
   * ════════════════════════════════════════════════════════════════════════ */
  function buildFieldReportListSteps() {
    return [
      {
        popover: {
          title: '<i class="bi bi-file-earmark-text me-1"></i> Rapports de terrain',
          description:
            'Cette page recense tous les rapports de retour de mission. '
            + 'Chaque rapport est lié à une décharge et documente '
            + 'les actions réalisées ainsi que l\'état des équipements ramenés.',
          side: 'over', align: 'center',
        },
      },
      {
        element: '#tour-report-table',
        popover: {
          title: '<i class="bi bi-table me-1"></i> Historique des rapports',
          description:
            'Le tableau affiche le Field Engineer, la décharge associée, la destination '
            + 'et la date de retour. Cliquez sur '
            + '<i class="bi bi-eye-fill"></i> pour consulter le rapport complet.',
          side: 'top', align: 'start',
        },
      },
      {
        popover: {
          title: '<i class="bi bi-arrow-right-circle-fill me-1"></i> Créer un rapport',
          description:
            'Les rapports se créent depuis la page de détail d\'une décharge. '
            + 'Accédez à une décharge <strong>En cours</strong> via le menu '
            + '<strong>Décharges</strong> pour créer son rapport de retour.',
          side: 'over', align: 'center',
        },
      },
      {
        popover: {
          title: '✅ Suivi des missions',
          description:
            'Grâce aux rapports, chaque sortie de matériel est documentée '
            + 'et le stock est automatiquement réintégré après validation.',
          side: 'over', align: 'center',
        },
      },
    ];
  }

  function createFieldReportListTour() {
    return driver(commonOpts({ steps: buildFieldReportListSteps() }));
  }

  /* ════════════════════════════════════════════════════════════════════════
   * FIELD REPORT CREATE
   * ════════════════════════════════════════════════════════════════════════ */
  function buildFieldReportCreateSteps() {
    return [
      {
        popover: {
          title: '<i class="bi bi-file-earmark-plus-fill me-1"></i> Rapport de retour',
          description:
            'Ce formulaire clôture la mission. Renseignez les actions réalisées '
            + 'sur le terrain et l\'état des équipements ramenés. '
            + 'Le stock sera mis à jour automatiquement.',
          side: 'over', align: 'center',
        },
      },
      {
        element: '#tour-report-description',
        popover: {
          title: '<i class="bi bi-journal-text me-1"></i> Rapport détaillé',
          description:
            'Décrivez en détail les travaux effectués, les difficultés rencontrées, '
            + 'l\'état du site, etc. Ce champ est <strong>obligatoire</strong> '
            + 'et sera visible par les Regional Managers.',
          side: 'bottom', align: 'start',
        },
      },
      ...(document.getElementById('tour-report-condition') ? [{
        element: '#tour-report-condition',
        popover: {
          title: '<i class="bi bi-arrow-return-left me-1"></i> État des équipements',
          description:
            'Pour chaque équipement emporté, indiquez la quantité ramenée et '
            + 'son état : <strong class="text-success">Bon état</strong> → réintégré au stock, '
            + '<strong class="text-danger">Défectueux</strong> → comptabilisé séparément.',
          side: 'bottom', align: 'start',
        },
      }] : []),
      {
        element: '#submit-btn',
        popover: {
          title: '<i class="bi bi-check2-all me-1"></i> Valider et clôturer',
          description:
            'Cliquez sur <strong>Valider le rapport & clôturer la mission</strong> '
            + 'pour enregistrer le rapport. La décharge passera en statut '
            + '<strong>Clôturée</strong> et le stock sera mis à jour.',
          side: 'top', align: 'end',
        },
      },
      {
        popover: {
          title: '✅ Mission documentée !',
          description:
            'Un rapport bien renseigné permet un suivi précis du matériel '
            + 'et facilite la gestion des stocks à long terme.',
          side: 'over', align: 'center',
        },
      },
    ];
  }

  function createFieldReportCreateTour() {
    return driver(commonOpts({ steps: buildFieldReportCreateSteps() }));
  }

  /* ════════════════════════════════════════════════════════════════════════
   * FIELD REPORT DETAIL
   * ════════════════════════════════════════════════════════════════════════ */
  function buildFieldReportDetailSteps() {
    return [
      {
        popover: {
          title: '<i class="bi bi-file-earmark-check-fill me-1"></i> Rapport de mission',
          description:
            'Cette page affiche le compte-rendu complet d\'une mission : '
            + 'informations du Field Engineer, date de retour, rapport écrit '
            + 'et récapitulatif des équipements ramenés.',
          side: 'over', align: 'center',
        },
      },
      {
        element: '#tour-report-detail-card',
        popover: {
          title: '<i class="bi bi-info-circle-fill me-1"></i> Informations de mission',
          description:
            'Retrouvez ici le Field Engineer, la date de retour, la destination '
            + 'et le rapport narratif complet de ce qui s\'est passé sur le terrain.',
          side: 'bottom', align: 'start',
        },
      },
      {
        popover: {
          title: '<i class="bi bi-table me-1"></i> Récapitulatif des retours',
          description:
            'Le tableau liste chaque équipement avec la quantité ramenée, '
            + 'son état (bon état / défectueux) et le calcul des unités '
            + 'consommées ou perdues pendant la mission.',
          side: 'over', align: 'center',
        },
      },
      {
        popover: {
          title: '✅ Rapport consulté',
          description:
            'Utilisez le bouton <strong>Voir la décharge</strong> pour revenir '
            + 'à la décharge associée ou <strong>Tous les rapports</strong> '
            + 'pour consulter l\'historique complet.',
          side: 'over', align: 'center',
        },
      },
    ];
  }

  function createFieldReportDetailTour() {
    return driver(commonOpts({ steps: buildFieldReportDetailSteps() }));
  }

  /* ════════════════════════════════════════════════════════════════════════
   * USER LIST (admin only)
   * ════════════════════════════════════════════════════════════════════════ */
  function buildUserListSteps() {
    return [
      {
        popover: {
          title: '<i class="bi bi-people-fill me-1"></i> Gestion des comptes',
          description:
            'Cette section (réservée aux Regional Managers) permet de gérer '
            + 'tous les utilisateurs de l\'application : créer des Field Engineers, '
            + 'promouvoir un Regional Manager ou désactiver un compte.',
          side: 'over', align: 'center',
        },
      },
      {
        element: '#tour-btn-new-user',
        popover: {
          title: '<i class="bi bi-person-plus-fill me-1"></i> Ajouter un utilisateur',
          description:
            'Cliquez sur <strong>Suivant</strong> pour voir le formulaire '
            + 'de création d\'un nouvel utilisateur.',
          side: 'bottom', align: 'end',
        },
        onNextClick: (_el, _step, { driver: d }) => {
          openModal('userModal', () => { d.moveNext(); });
        },
      },
      {
        element: '#f_username',
        popover: {
          title: '<i class="bi bi-person-fill me-1"></i> Nom d\'utilisateur',
          description:
            'Le nom d\'utilisateur est unique et utilisé pour la connexion '
            + '(ex&nbsp;: <em>jean.dupont</em>). Il ne peut pas être modifié '
            + 'après création.',
          side: 'bottom', align: 'start',
        },
        onDeselected: () => { closeModal('userModal'); },
      },
      {
        element: '#tour-user-table',
        popover: {
          title: '<i class="bi bi-table me-1"></i> Liste des utilisateurs',
          description:
            'Le tableau affiche tous les comptes avec leur rôle '
            + '(<strong>Regional Manager</strong> ou <strong>Field Engineer</strong>), '
            + 'leur date d\'inscription et leur dernière connexion. '
            + 'Utilisez les boutons d\'action pour modifier ou supprimer un compte.',
          side: 'top', align: 'start',
        },
      },
      {
        popover: {
          title: '<i class="bi bi-shield-fill-check me-1"></i> Rôles et permissions',
          description:
            '<strong>Field Engineer</strong> : peut créer des décharges et des rapports de terrain.<br>'
            + '<strong>Regional Manager</strong> : accès complet — dashboard, inventaire, '
            + 'gestion des comptes et alertes stock.',
          side: 'over', align: 'center',
        },
      },
      {
        popover: {
          title: '✅ Gestion des comptes maîtrisée !',
          description:
            'Créez les comptes de vos Field Engineers ici. Ils pourront se connecter '
            + 'immédiatement après création. Le bouton <strong>?</strong> '
            + 'relance ce tutoriel à tout moment.',
          side: 'over', align: 'center',
        },
      },
    ];
  }

  function createUserListTour() {
    return driver(commonOpts({
      onDestroyStarted: () => {
        closeModal('userModal');
      },
      steps: buildUserListSteps(),
    }));
  }

  /* ════════════════════════════════════════════════════════════════════════
   * ROUTEUR DE PAGES
   * ════════════════════════════════════════════════════════════════════════ */
  const PAGE_TOURS = {
    dashboard:           createDashboardTour,
    discharge_list:      createDischargeListTour,
    discharge_create:    createDischargeCreateTour,
    discharge_detail:    createDischargeDetailTour,
    field_report_list:   createFieldReportListTour,
    field_report_create: createFieldReportCreateTour,
    field_report_detail: createFieldReportDetailTour,
    user_list:           createUserListTour,
  };

  /* ── API publique : window.startAppTour() ─────────────────────────────── */
  window.startAppTour = function () {
    const page = document.body.dataset.page;
    const factory = PAGE_TOURS[page];

    if (factory) {
      localStorage.removeItem(STORAGE_KEY);
      factory().drive();
      return;
    }

    if (typeof showToast === 'function') {
      showToast('Le tutoriel est disponible sur le <strong>Dashboard</strong> et les pages principales.', 'info');
    }
  };

  /* ── Auto-démarrage à la première visite ─────────────────────────────── */
  document.addEventListener('DOMContentLoaded',  function initAutoTour() {
    console.log("[AMN Tour] Vérification auto-start...");
    if (localStorage.getItem(STORAGE_KEY)) {
      console.log("[AMN Tour] Déjà fini (localStorage présent).");
      return;
    }

    const page = document.body.dataset.page;
    /* Auto-start uniquement sur le Dashboard (première connexion) */
    if (page !== 'dashboard') return;

    setTimeout(function () {
      createDashboardTour().drive();
    }, 900);
  });

}());
