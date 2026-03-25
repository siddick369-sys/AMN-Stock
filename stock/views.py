import json
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .models import (Discharge, DischargeItem, Equipment, FieldReport,
                     ReturnedItem, StockMovement)
from .tasks import (send_hub_alert, whatsapp_discharge_created,
                    whatsapp_field_report_created)


def is_admin(user):
    return user.is_staff or user.is_superuser


def is_technician(user):
    return user.is_authenticated


# ─────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────

@login_required
@user_passes_test(is_admin)
def dashboard(request):
    from django.core.paginator import Paginator

    search_query = request.GET.get('q', '').strip()
    equipments_qs = Equipment.objects.all().order_by('name')

    if search_query:
        equipments_qs = equipments_qs.filter(
            Q(name__icontains=search_query) | Q(reference__icontains=search_query)
        )

    paginator  = Paginator(equipments_qs, 20)
    page_obj   = paginator.get_page(request.GET.get('page'))

    context = {
        'equipments':    page_obj,        # toujours nommé equipments pour compatibilité template
        'page_obj':      page_obj,
        'search_query':  search_query,
        'total_equipment': Equipment.objects.count(),
        'low_stock_count': sum(1 for e in Equipment.objects.all() if e.is_low_stock),
        'total_discharges': Discharge.objects.filter(status='open').count(),
    }
    return render(request, 'stock/dashboard.html', context)


# ─────────────────────────────────────────────
# EQUIPMENT CRUD (JSON API for modals)
# ─────────────────────────────────────────────

@login_required
@user_passes_test(is_admin)
@require_POST
def equipment_create(request):
    try:
        data = json.loads(request.body)
        name = data.get('name', '').strip()
        reference = data.get('reference', '').strip()
        quantity = int(data.get('quantity', 0))
        defective_quantity = int(data.get('defective_quantity', 0))

        if not name or not reference:
            return JsonResponse({'success': False, 'error': 'Nom et référence obligatoires.'}, status=400)

        if Equipment.objects.filter(reference=reference).exists():
            return JsonResponse({'success': False, 'error': f'La référence "{reference}" existe déjà.'}, status=400)

        equipment = Equipment.objects.create(
            name=name,
            reference=reference,
            quantity=quantity,
            defective_quantity=defective_quantity,
        )

        # Log stock movement if initial quantity > 0
        if quantity > 0:
            StockMovement.objects.create(
                equipment=equipment,
                movement_type='in',
                quantity=quantity,
                note='Stock initial',
            )

        return JsonResponse({
            'success': True,
            'equipment': {
                'id': equipment.id,
                'name': equipment.name,
                'reference': equipment.reference,
                'quantity': equipment.quantity,
                'defective_quantity': equipment.defective_quantity,
                'is_low_stock': equipment.is_low_stock,
            }
        })
    except (ValueError, json.JSONDecodeError) as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
@user_passes_test(is_admin)
@require_http_methods(['GET'])
def equipment_detail(request, pk):
    equipment = get_object_or_404(Equipment, pk=pk)
    return JsonResponse({
        'id': equipment.id,
        'name': equipment.name,
        'reference': equipment.reference,
        'quantity': equipment.quantity,
        'defective_quantity': equipment.defective_quantity,
    })


@login_required
@user_passes_test(is_admin)
@require_POST
def equipment_update(request, pk):
    equipment = get_object_or_404(Equipment, pk=pk)
    try:
        data = json.loads(request.body)
        old_quantity = equipment.quantity

        name = data.get('name', '').strip()
        reference = data.get('reference', '').strip()
        quantity = int(data.get('quantity', equipment.quantity))
        defective_quantity = int(data.get('defective_quantity', equipment.defective_quantity))

        if not name or not reference:
            return JsonResponse({'success': False, 'error': 'Nom et référence obligatoires.'}, status=400)

        if Equipment.objects.filter(reference=reference).exclude(pk=pk).exists():
            return JsonResponse({'success': False, 'error': f'La référence "{reference}" est déjà utilisée.'}, status=400)

        equipment.name = name
        equipment.reference = reference
        equipment.quantity = quantity
        equipment.defective_quantity = defective_quantity
        equipment.save()

        # Log stock adjustment
        if quantity != old_quantity:
            diff = quantity - old_quantity
            StockMovement.objects.create(
                equipment=equipment,
                movement_type='in' if diff > 0 else 'out',
                quantity=abs(diff),
                note='Ajustement manuel de stock',
            )

        return JsonResponse({
            'success': True,
            'equipment': {
                'id': equipment.id,
                'name': equipment.name,
                'reference': equipment.reference,
                'quantity': equipment.quantity,
                'defective_quantity': equipment.defective_quantity,
                'is_low_stock': equipment.is_low_stock,
            }
        })
    except (ValueError, json.JSONDecodeError) as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
@user_passes_test(is_admin)
@require_POST
def equipment_delete(request, pk):
    equipment = get_object_or_404(Equipment, pk=pk)
    name = equipment.name
    try:
        equipment.delete()
        return JsonResponse({'success': True, 'message': f'"{name}" supprimé avec succès.'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


# ─────────────────────────────────────────────
# CHART DATA (AJAX)
# ─────────────────────────────────────────────

@login_required
@user_passes_test(is_admin)
def stock_chart_data(request):
    period = request.GET.get('period', 'week')
    now = timezone.now()

    if period == 'day':
        start_date = now - timedelta(days=1)
        date_format = '%H:00'
        trunc_unit = 'hour'
    elif period == 'month':
        start_date = now - timedelta(days=30)
        date_format = '%d/%m'
        trunc_unit = 'day'
    else:  # week
        start_date = now - timedelta(days=7)
        date_format = '%a %d/%m'
        trunc_unit = 'day'

    movements = StockMovement.objects.filter(date__gte=start_date).order_by('date')

    # Build timeline labels and data
    labels = []
    entries = []
    exits = []

    if period == 'day':
        for h in range(24):
            slot_start = now.replace(hour=h, minute=0, second=0, microsecond=0) - timedelta(days=1)
            slot_end = slot_start + timedelta(hours=1)
            slot_label = slot_start.strftime('%H:00')
            labels.append(slot_label)
            in_qty = movements.filter(movement_type='in', date__gte=slot_start, date__lt=slot_end).aggregate(total=Sum('quantity'))['total'] or 0
            out_qty = movements.filter(movement_type='out', date__gte=slot_start, date__lt=slot_end).aggregate(total=Sum('quantity'))['total'] or 0
            entries.append(in_qty)
            exits.append(out_qty)
    else:
        days = 30 if period == 'month' else 7
        for d in range(days, -1, -1):
            day = (now - timedelta(days=d)).date()
            day_label = day.strftime(date_format)
            labels.append(day_label)
            in_qty = movements.filter(movement_type='in', date__date=day).aggregate(total=Sum('quantity'))['total'] or 0
            out_qty = movements.filter(movement_type='out', date__date=day).aggregate(total=Sum('quantity'))['total'] or 0
            entries.append(in_qty)
            exits.append(out_qty)

    return JsonResponse({'labels': labels, 'entries': entries, 'exits': exits})


# ─────────────────────────────────────────────
# LOW STOCK NOTIFICATIONS (AJAX poll)
# ─────────────────────────────────────────────

@login_required
def get_notifications(request):
    notifications = cache.get('low_stock_notifications', [])
    return JsonResponse({'notifications': notifications})


@login_required
@require_POST
def dismiss_notification(request, equipment_id):
    notifications = cache.get('low_stock_notifications', [])
    notifications = [n for n in notifications if n['id'] != equipment_id]
    cache.set('low_stock_notifications', notifications, timeout=3600)
    return JsonResponse({'success': True})


# ─────────────────────────────────────────────
# HUB ALERT
# ─────────────────────────────────────────────

@login_required
@user_passes_test(is_admin)
@require_POST
def send_hub_alert_view(request):
    try:
        data = json.loads(request.body)
        equipment_ids = data.get('equipment_ids', [])

        if not equipment_ids:
            return JsonResponse({'success': False, 'error': 'Sélectionnez au moins un équipement.'}, status=400)

        send_hub_alert.delay(equipment_ids, request.user.id)
        return JsonResponse({'success': True, 'message': 'Alerte Hub envoyée en arrière-plan.'})
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Données invalides.'}, status=400)


# ─────────────────────────────────────────────
# DISCHARGE (DÉCHARGE)
# ─────────────────────────────────────────────

@login_required
def discharge_list(request):
    from django.core.paginator import Paginator

    if request.user.is_staff:
        qs = Discharge.objects.select_related('user').prefetch_related('items__equipment').all()
    else:
        qs = Discharge.objects.filter(user=request.user).select_related('user').prefetch_related('items__equipment')

    # ── Filtres ──
    q         = request.GET.get('q', '').strip()
    status    = request.GET.get('status', '')
    date_from = request.GET.get('date_from', '')
    date_to   = request.GET.get('date_to', '')

    if q:
        qs = qs.filter(
            Q(destination__icontains=q) |
            Q(user__username__icontains=q) |
            Q(user__first_name__icontains=q) |
            Q(user__last_name__icontains=q)
        )
    if status in ('open', 'closed'):
        qs = qs.filter(status=status)
    if date_from:
        qs = qs.filter(date__date__gte=date_from)
    if date_to:
        qs = qs.filter(date__date__lte=date_to)

    qs = qs.order_by('-date')
    paginator = Paginator(qs, 15)
    page_obj  = paginator.get_page(request.GET.get('page'))

    return render(request, 'stock/discharge_list.html', {
        'page_obj':  page_obj,
        'q':         q,
        'status':    status,
        'date_from': date_from,
        'date_to':   date_to,
    })


@login_required
def discharge_create(request):
    equipments = Equipment.objects.filter(quantity__gt=0).order_by('name')

    if request.method == 'POST':
        destination = request.POST.get('destination', '').strip()
        if not destination:
            messages.error(request, 'La destination est obligatoire.')
            return render(request, 'stock/discharge_create.html', {'equipments': equipments})

        # Parse items
        item_equipment_ids = request.POST.getlist('equipment_id[]')
        item_quantities = request.POST.getlist('quantity[]')

        if not item_equipment_ids:
            messages.error(request, 'Ajoutez au moins un équipement.')
            return render(request, 'stock/discharge_create.html', {'equipments': equipments})

        items_data = []
        errors = []

        for eq_id, qty_str in zip(item_equipment_ids, item_quantities):
            try:
                equipment = Equipment.objects.get(id=int(eq_id))
                quantity = int(qty_str)
                if quantity <= 0:
                    errors.append(f'La quantité pour "{equipment.name}" doit être supérieure à 0.')
                    continue
                if quantity > equipment.quantity:
                    errors.append(
                        f'L\'item "{equipment.name}" est insuffisant pour votre décharge. '
                        f'Stock disponible : {equipment.quantity}, demandé : {quantity}.'
                    )
                else:
                    items_data.append({'equipment': equipment, 'quantity': quantity})
            except (Equipment.DoesNotExist, ValueError):
                errors.append('Équipement invalide détecté.')

        if errors:
            return JsonResponse({'success': False, 'errors': errors}, status=400) \
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest' \
                else render(request, 'stock/discharge_create.html', {
                    'equipments': equipments,
                    'errors': errors,
                })

        # All valid – create discharge atomically
        with transaction.atomic():
            discharge = Discharge.objects.create(
                user=request.user,
                destination=destination,
            )
            for item in items_data:
                DischargeItem.objects.create(
                    discharge=discharge,
                    equipment=item['equipment'],
                    quantity=item['quantity'],
                )
                item['equipment'].quantity -= item['quantity']
                item['equipment'].save()

                StockMovement.objects.create(
                    equipment=item['equipment'],
                    movement_type='out',
                    quantity=item['quantity'],
                    note=f'Décharge #{discharge.pk} - {destination}',
                )

                # Trigger low-stock real-time notification if needed
                if item['equipment'].is_low_stock:
                    from .tasks import notify_low_stock_realtime
                    notify_low_stock_realtime.delay(item['equipment'].id)

        # ── WhatsApp : résumé de la décharge envoyé en arrière-plan ──
        whatsapp_discharge_created.delay(discharge.id)

        messages.success(request, f'Décharge #{discharge.pk} créée avec succès.')
        return redirect('discharge_detail', pk=discharge.pk)

    return render(request, 'stock/discharge_create.html', {'equipments': equipments})


@login_required
def discharge_detail(request, pk):
    discharge = get_object_or_404(Discharge, pk=pk)
    if not request.user.is_staff and discharge.user != request.user:
        messages.error(request, 'Accès refusé.')
        return redirect('discharge_list')
    has_report = hasattr(discharge, 'field_report')
    return render(request, 'stock/discharge_detail.html', {
        'discharge': discharge,
        'has_report': has_report,
    })


@login_required
def discharge_edit(request, pk):
    """
    Permet au créateur de la décharge (ou à un admin) de la modifier
    tant qu'elle est encore ouverte.
    Restaure les quantités originales puis applique les nouvelles.
    """
    discharge = get_object_or_404(Discharge, pk=pk)

    # Contrôle d'accès : seul le créateur peut modifier
    if discharge.user != request.user and not request.user.is_staff:
        messages.error(request, 'Accès refusé.')
        return redirect('discharge_list')

    if discharge.status == 'closed':
        messages.warning(request, 'Impossible de modifier une décharge clôturée.')
        return redirect('discharge_detail', pk=pk)

    equipments = Equipment.objects.order_by('name')

    if request.method == 'POST':
        destination = request.POST.get('destination', '').strip()
        if not destination:
            messages.error(request, 'La destination est obligatoire.')
            return render(request, 'stock/discharge_create.html', {
                'equipments': Equipment.objects.filter(quantity__gt=0).order_by('name'),
                'discharge': discharge,
                'is_edit': True,
            })

        item_equipment_ids = request.POST.getlist('equipment_id[]')
        item_quantities    = request.POST.getlist('quantity[]')

        if not item_equipment_ids:
            messages.error(request, 'Ajoutez au moins un équipement.')
            return render(request, 'stock/discharge_create.html', {
                'equipments': Equipment.objects.filter(quantity__gt=0).order_by('name'),
                'discharge': discharge,
                'is_edit': True,
            })

        items_data = []
        errors = []

        # Calculer le stock effectivement disponible en tenant compte
        # des items déjà dans cette décharge (qui seront restaurés)
        original_items = {di.equipment_id: di.quantity for di in discharge.items.all()}

        for eq_id, qty_str in zip(item_equipment_ids, item_quantities):
            try:
                equipment = Equipment.objects.get(id=int(eq_id))
                quantity  = int(qty_str)
                if quantity <= 0:
                    errors.append(f'La quantité pour "{equipment.name}" doit être > 0.')
                    continue
                # Stock disponible = stock actuel + ce qu'on avait déjà pris dans cette décharge
                available = equipment.quantity + original_items.get(equipment.id, 0)
                if quantity > available:
                    errors.append(
                        f'"{equipment.name}" : stock insuffisant (dispo : {available}, demandé : {quantity}).'
                    )
                else:
                    items_data.append({'equipment': equipment, 'quantity': quantity})
            except (Equipment.DoesNotExist, ValueError):
                errors.append('Équipement invalide.')

        if errors:
            return render(request, 'stock/discharge_create.html', {
                'equipments': Equipment.objects.filter(quantity__gt=0).order_by('name'),
                'discharge': discharge,
                'is_edit': True,
                'errors': errors,
            })

        with transaction.atomic():
            # 1. Restaurer les anciennes quantités
            for di in discharge.items.select_related('equipment').all():
                di.equipment.quantity += di.quantity
                di.equipment.save()
                StockMovement.objects.create(
                    equipment=di.equipment,
                    movement_type='in',
                    quantity=di.quantity,
                    note=f'Correction décharge #{discharge.pk} — ancienne valeur restaurée',
                )

            # 2. Supprimer les anciens items
            discharge.items.all().delete()

            # 3. Mettre à jour la destination
            discharge.destination = destination
            discharge.save()

            # 4. Créer les nouveaux items et déduire le stock
            for item in items_data:
                DischargeItem.objects.create(
                    discharge=discharge,
                    equipment=item['equipment'],
                    quantity=item['quantity'],
                )
                item['equipment'].quantity -= item['quantity']
                item['equipment'].save()
                StockMovement.objects.create(
                    equipment=item['equipment'],
                    movement_type='out',
                    quantity=item['quantity'],
                    note=f'Décharge #{discharge.pk} (modifiée) - {destination}',
                )

        messages.success(request, f'Décharge #{discharge.pk} modifiée avec succès.')
        return redirect('discharge_detail', pk=discharge.pk)

    # GET : pré-remplir avec les items existants
    existing_items = discharge.items.select_related('equipment').all()
    # Pour le template, on doit exposer tous les équipements (stock actuel + ce qu'on a pris)
    equip_for_form = []
    orig = {di.equipment_id: di.quantity for di in existing_items}
    for eq in equipments:
        eq._available_for_edit = eq.quantity + orig.get(eq.id, 0)
        equip_for_form.append(eq)

    return render(request, 'stock/discharge_create.html', {
        'equipments':    equip_for_form,
        'discharge':     discharge,
        'existing_items': existing_items,
        'is_edit':       True,
    })


# ─────────────────────────────────────────────
# FIELD REPORT (RAPPORT DE TERRAIN)
# ─────────────────────────────────────────────

@login_required
def field_report_create(request, discharge_pk):
    discharge = get_object_or_404(Discharge, pk=discharge_pk)

    # Access control
    if not request.user.is_staff and discharge.user != request.user:
        messages.error(request, 'Accès refusé.')
        return redirect('discharge_list')

    # Prevent duplicate reports
    if hasattr(discharge, 'field_report'):
        messages.warning(request, 'Un rapport existe déjà pour cette décharge.')
        return redirect('discharge_detail', pk=discharge.pk)

    if discharge.status == 'closed':
        messages.warning(request, 'Cette décharge est déjà clôturée.')
        return redirect('discharge_detail', pk=discharge.pk)

    discharge_items = discharge.items.select_related('equipment').all()

    if request.method == 'POST':
        description = request.POST.get('description', '').strip()
        if not description:
            messages.error(request, 'La description du rapport est obligatoire.')
            return render(request, 'stock/field_report_create.html', {
                'discharge': discharge,
                'discharge_items': discharge_items,
            })

        with transaction.atomic():
            report = FieldReport.objects.create(
                discharge=discharge,
                description=description,
            )

            for item in discharge_items:
                qty_key = f'quantity_returned_{item.id}'
                cond_key = f'condition_{item.id}'
                qty_returned = int(request.POST.get(qty_key, 0))
                condition = request.POST.get(cond_key, 'good')

                if qty_returned < 0:
                    qty_returned = 0
                if qty_returned > item.quantity:
                    qty_returned = item.quantity  # cap at discharged amount

                ReturnedItem.objects.create(
                    field_report=report,
                    equipment=item.equipment,
                    quantity_returned=qty_returned,
                    condition=condition,
                )

                # Reintegrate good items into stock
                if qty_returned > 0:
                    if condition == 'good':
                        item.equipment.quantity += qty_returned
                        StockMovement.objects.create(
                            equipment=item.equipment,
                            movement_type='in',
                            quantity=qty_returned,
                            note=f'Retour rapport #{report.pk} - {discharge.destination}',
                        )
                    else:
                        # Defective items: add to defective count
                        item.equipment.defective_quantity += qty_returned
                    item.equipment.save()

            discharge.status = 'closed'
            discharge.save()

        # ── WhatsApp : récapitulatif du rapport envoyé en arrière-plan ──
        whatsapp_field_report_created.delay(report.id)

        messages.success(request, f'Rapport #{report.pk} créé et décharge #{discharge.pk} clôturée.')
        return redirect('field_report_detail', pk=report.pk)

    return render(request, 'stock/field_report_create.html', {
        'discharge': discharge,
        'discharge_items': discharge_items,
    })


@login_required
def field_report_detail(request, pk):
    report = get_object_or_404(FieldReport, pk=pk)
    discharge = report.discharge
    if not request.user.is_staff and discharge.user != request.user:
        messages.error(request, 'Accès refusé.')
        return redirect('discharge_list')
    return render(request, 'stock/field_report_detail.html', {'report': report})


@login_required
def field_report_list(request):
    if request.user.is_staff:
        reports = FieldReport.objects.select_related('discharge__user').all()
    else:
        reports = FieldReport.objects.filter(
            discharge__user=request.user
        ).select_related('discharge__user')
    return render(request, 'stock/field_report_list.html', {'reports': reports})


# ─────────────────────────────────────────────
# AI — RAPPORT (résumé + PDF)
# ─────────────────────────────────────────────

@login_required
@user_passes_test(is_admin)
@require_POST
def ai_trigger_summary(request):
    """Lance la tâche Celery de génération du rapport IA. Retourne le cache_key."""
    import uuid
    from django.core.cache import cache
    from .tasks import ai_generate_summary

    cache_key = f'ai_report_{request.user.id}_{uuid.uuid4().hex[:8]}'
    # Marquer comme "en cours" immédiatement pour que le frontend sache
    cache.set(cache_key, {'status': 'pending'}, timeout=600)
    ai_generate_summary.delay(cache_key, days=30)
    return JsonResponse({'success': True, 'cache_key': cache_key})


@login_required
@user_passes_test(is_admin)
def ai_report_status(request, cache_key):
    """Poll : retourne l'état du rapport IA depuis le cache."""
    from django.core.cache import cache
    data = cache.get(cache_key)
    if data is None:
        return JsonResponse({'status': 'expired'})
    return JsonResponse(data)


@login_required
@user_passes_test(is_admin)
def ai_generate_pdf(request, cache_key):
    """Génère et retourne le rapport IA en PDF via xhtml2pdf."""
    from io import BytesIO
    from django.core.cache import cache
    from django.template.loader import render_to_string
    from xhtml2pdf import pisa

    data = cache.get(cache_key)
    if not data or data.get('status') != 'ready':
        return JsonResponse({'error': 'Rapport non disponible. Relancez l\'analyse.'}, status=404)

    # Convert Markdown → HTML for xhtml2pdf
    try:
        import re
        md = data['content']
        # Headers
        md = re.sub(r'^### (.+)$', r'<h3>\1</h3>', md, flags=re.MULTILINE)
        md = re.sub(r'^## (.+)$',  r'<h2>\1</h2>', md, flags=re.MULTILINE)
        md = re.sub(r'^# (.+)$',   r'<h2>\1</h2>', md, flags=re.MULTILINE)
        # Bold / italic
        md = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', md)
        md = re.sub(r'\*(.+?)\*',     r'<em>\1</em>',         md)
        md = re.sub(r'_(.+?)_',       r'<em>\1</em>',         md)
        # Bullet lists — group consecutive lines starting with - or •
        lines = md.split('\n')
        html_lines = []
        in_list = False
        for line in lines:
            stripped = line.strip()
            is_item = stripped.startswith('- ') or stripped.startswith('• ')
            if is_item:
                if not in_list:
                    html_lines.append('<ul>')
                    in_list = True
                text = stripped[2:].strip()
                html_lines.append(f'<li>{text}</li>')
            else:
                if in_list:
                    html_lines.append('</ul>')
                    in_list = False
                if stripped == '---' or stripped == '***':
                    html_lines.append('<hr>')
                elif stripped == '':
                    html_lines.append('<br>')
                else:
                    html_lines.append(f'<p>{line}</p>')
        if in_list:
            html_lines.append('</ul>')
        content_html = '\n'.join(html_lines)
    except Exception:
        content_html = f"<pre>{data['content']}</pre>"

    html_string = render_to_string('stock/report_pdf.html', {
        'content_html': content_html,
        'generated_at': data.get('generated_at', ''),
        'period_days': data.get('period_days', 30),
        'stats': data.get('stats', {}),
    })

    pdf_buffer = BytesIO()
    pisa_status = pisa.CreatePDF(html_string, dest=pdf_buffer, encoding='utf-8')

    if pisa_status.err:
        return JsonResponse({'error': 'Erreur lors de la génération du PDF.'}, status=500)

    from django.http import HttpResponse
    from django.utils import timezone as tz
    filename = f"AMN_Rapport_Stock_{tz.now().strftime('%Y%m%d_%H%M')}.pdf"
    response = HttpResponse(pdf_buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# ─────────────────────────────────────────────
# AI — SUGGESTIONS
# ─────────────────────────────────────────────

@login_required
@user_passes_test(is_admin)
@require_POST
def ai_trigger_suggestions(request):
    """Lance la tâche Celery de génération des suggestions IA."""
    import uuid
    from django.core.cache import cache
    from .tasks import ai_generate_suggestions

    cache_key = f'ai_suggestions_{request.user.id}_{uuid.uuid4().hex[:8]}'
    cache.set(cache_key, {'status': 'pending'}, timeout=600)
    ai_generate_suggestions.delay(cache_key, days=30)
    return JsonResponse({'success': True, 'cache_key': cache_key})


@login_required
@user_passes_test(is_admin)
def ai_suggestions_status(request, cache_key):
    """Poll : retourne l'état des suggestions IA depuis le cache."""
    from django.core.cache import cache
    data = cache.get(cache_key)
    if data is None:
        return JsonResponse({'status': 'expired'})
    return JsonResponse(data)


# ─────────────────────────────────────────────
# ASSISTANT VOCAL IA — DÉCHARGE
# ─────────────────────────────────────────────

@login_required
@require_POST
def voice_process_discharge(request):
    """
    Reçoit un fichier audio (multipart), le transcrit avec Groq Whisper,
    extrait la destination et les équipements avec LLaMA, retourne un JSON
    structuré que le JS côté client utilisera pour remplir le formulaire.

    Accessible à tous les utilisateurs authentifiés (techniciens inclus).
    """
    from .voice import process_voice_discharge
    from .models import Equipment

    audio_file = request.FILES.get('audio')
    if not audio_file:
        return JsonResponse({'error': 'Aucun fichier audio reçu.'}, status=400)

    # Limite de taille : 25 Mo (limite Groq Whisper)
    max_size = 25 * 1024 * 1024
    if audio_file.size > max_size:
        return JsonResponse({'error': 'Fichier audio trop volumineux (max 25 Mo).'}, status=400)

    audio_bytes = audio_file.read()
    mime_type   = audio_file.content_type or 'audio/webm'

    # Construire la liste des équipements disponibles pour le NLU
    equipments = Equipment.objects.filter(quantity__gt=0).values('id', 'name', 'reference', 'quantity')
    available  = [{'id': e['id'], 'name': e['name'], 'reference': e['reference'], 'stock': e['quantity']} for e in equipments]

    result = process_voice_discharge(audio_bytes, mime_type, available)

    if 'error' in result:
        status_code = 422 if result.get('step') == 'nlu' else 500
        return JsonResponse(result, status=status_code)

    return JsonResponse(result)


# ─────────────────────────────────────────────
# GESTION DES COMPTES (admin only)
# ─────────────────────────────────────────────

from django.contrib.auth.models import User
from django.core.paginator import Paginator


@login_required
@user_passes_test(is_admin)
def user_list(request):
    q = request.GET.get('q', '').strip()
    role = request.GET.get('role', '')

    users = User.objects.all().order_by('username')

    if q:
        users = users.filter(
            Q(username__icontains=q) |
            Q(first_name__icontains=q) |
            Q(last_name__icontains=q) |
            Q(email__icontains=q)
        )
    if role == 'admin':
        users = users.filter(is_staff=True)
    elif role == 'tech':
        users = users.filter(is_staff=False)

    paginator = Paginator(users, 15)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'stock/user_list.html', {
        'page_obj': page_obj,
        'q': q,
        'role': role,
    })


@login_required
@user_passes_test(is_admin)
def user_create(request):
    if request.method == 'POST':
        username   = request.POST.get('username', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        last_name  = request.POST.get('last_name', '').strip()
        email      = request.POST.get('email', '').strip()
        password   = request.POST.get('password', '')
        is_staff   = request.POST.get('is_staff') == '1'

        errors = []
        if not username:
            errors.append("Le nom d'utilisateur est obligatoire.")
        elif User.objects.filter(username=username).exists():
            errors.append(f'Le nom d\'utilisateur "{username}" est déjà utilisé.')
        if not password:
            errors.append("Le mot de passe est obligatoire.")
        elif len(password) < 6:
            errors.append("Le mot de passe doit contenir au moins 6 caractères.")

        if errors:
            return JsonResponse({'success': False, 'errors': errors}, status=400)

        user = User.objects.create_user(
            username=username,
            password=password,
            first_name=first_name,
            last_name=last_name,
            email=email,
            is_staff=is_staff,
        )
        return JsonResponse({'success': True, 'message': f'Utilisateur "{user.username}" créé avec succès.'})

    return JsonResponse({'success': False, 'error': 'Méthode non autorisée.'}, status=405)


@login_required
@user_passes_test(is_admin)
def user_edit(request, pk):
    target = get_object_or_404(User, pk=pk)

    if request.method == 'GET':
        return JsonResponse({
            'id':         target.id,
            'username':   target.username,
            'first_name': target.first_name,
            'last_name':  target.last_name,
            'email':      target.email,
            'is_staff':   target.is_staff,
        })

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Données invalides.'}, status=400)

        username   = data.get('username', '').strip()
        first_name = data.get('first_name', '').strip()
        last_name  = data.get('last_name', '').strip()
        email      = data.get('email', '').strip()
        is_staff   = bool(data.get('is_staff', False))

        errors = []
        if not username:
            errors.append("Le nom d'utilisateur est obligatoire.")
        elif User.objects.filter(username=username).exclude(pk=pk).exists():
            errors.append(f'Le nom d\'utilisateur "{username}" est déjà utilisé.')

        if errors:
            return JsonResponse({'success': False, 'errors': errors}, status=400)

        # Empêcher de se retirer ses propres droits admin
        if target == request.user and not is_staff:
            return JsonResponse({'success': False, 'errors': ["Vous ne pouvez pas retirer vos propres droits administrateur."]}, status=400)

        target.username   = username
        target.first_name = first_name
        target.last_name  = last_name
        target.email      = email
        target.is_staff   = is_staff
        target.save()

        return JsonResponse({'success': True, 'message': f'Utilisateur "{target.username}" modifié avec succès.'})

    return JsonResponse({'success': False, 'error': 'Méthode non autorisée.'}, status=405)


@login_required
@user_passes_test(is_admin)
@require_POST
def user_delete(request, pk):
    target = get_object_or_404(User, pk=pk)

    if target == request.user:
        return JsonResponse({'success': False, 'error': 'Vous ne pouvez pas supprimer votre propre compte.'}, status=400)

    username = target.username
    target.delete()
    return JsonResponse({'success': True, 'message': f'Utilisateur "{username}" supprimé.'})
