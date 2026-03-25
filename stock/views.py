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


# ═════════════════════════════════════════════════════════════════════════════
# SÉCURITÉ — HELPERS ANTI BRUTE-FORCE / RATE-LIMITING
# ═════════════════════════════════════════════════════════════════════════════
import re
import secrets
from datetime import timedelta

from django.conf import settings as _settings


def _get_client_ip(request):
    """Extrait l'IP cliente (supporte les proxies X-Forwarded-For)."""
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    return xff.split(',')[0].strip() if xff else request.META.get('REMOTE_ADDR', '127.0.0.1')


def _ck(prefix, identifier):
    """Génère une clé de cache sûre (évite l'injection via les identifiants)."""
    safe = re.sub(r'[^a-zA-Z0-9@._\-]', '_', str(identifier))[:80]
    return f'amn_sec:{prefix}:{safe}'


def _is_locked(prefix, identifier, max_attempts, lockout_sec):
    """
    Retourne (locked: bool, seconds_remaining: int).
    Supprime automatiquement le verrou expiré.
    """
    data = cache.get(_ck(prefix, identifier)) or {}
    locked_until = data.get('lu')  # timestamp float
    if locked_until:
        now = timezone.now().timestamp()
        if now < locked_until:
            return True, int(locked_until - now)
        cache.delete(_ck(prefix, identifier))
    return False, 0


def _record_attempt(prefix, identifier, max_attempts, lockout_sec):
    """Enregistre une tentative échouée; pose le verrou si le seuil est atteint."""
    key  = _ck(prefix, identifier)
    data = cache.get(key) or {'c': 0}
    data['c'] = data.get('c', 0) + 1
    if data['c'] >= max_attempts:
        data['lu'] = (timezone.now() + timedelta(seconds=lockout_sec)).timestamp()
    cache.set(key, data, timeout=max(lockout_sec * 2, 3600))


def _clear_attempts(prefix, identifier):
    cache.delete(_ck(prefix, identifier))


def _generate_otp():
    """OTP 6 chiffres cryptographiquement aléatoire (jamais 000000)."""
    return f'{100000 + secrets.randbelow(900000)}'


# ═════════════════════════════════════════════════════════════════════════════
# CONNEXION SÉCURISÉE (remplace LoginView)
# ═════════════════════════════════════════════════════════════════════════════

def secure_login(request):
    """
    Connexion avec protection anti brute-force :
      - Verrou par nom d'utilisateur : 5 échecs → 15 min
      - Verrou par IP               : 10 échecs → 30 min
    Redirige vers la vérification email si le compte n'est pas encore activé.
    """
    from django.contrib.auth import authenticate
    from django.contrib.auth import login as auth_login

    if request.user.is_authenticated:
        return redirect(request.GET.get('next') or _settings.LOGIN_REDIRECT_URL)

    error = None
    lockout_remaining = 0
    username_val = ''

    if request.method == 'POST':
        username_raw = request.POST.get('username', '').strip()[:150]
        password     = request.POST.get('password', '')
        next_url     = request.POST.get('next', '').strip()
        ip           = _get_client_ip(request)
        username_val = username_raw

        # ── Vérification par IP ──
        ip_locked, ip_rem = _is_locked('login_ip', ip, 10, 1800)
        if ip_locked:
            lockout_remaining = ip_rem
            error = f'Trop de tentatives depuis votre réseau. Réessayez dans {ip_rem // 60} min {ip_rem % 60} s.'
            return render(request, 'registration/login.html', {
                'error': error, 'lockout_remaining': lockout_remaining,
                'next': next_url, 'username': username_val,
            })

        # ── Vérification par nom d'utilisateur ──
        if username_raw:
            u_locked, u_rem = _is_locked('login_user', username_raw, 5, 900)
            if u_locked:
                lockout_remaining = u_rem
                error = f'Compte temporairement bloqué. Réessayez dans {u_rem // 60} min {u_rem % 60} s.'
                return render(request, 'registration/login.html', {
                    'error': error, 'lockout_remaining': lockout_remaining,
                    'next': next_url, 'username': username_val,
                })

        # ── Compte non activé (vérification email en attente) ──
        try:
            pending_user = User.objects.get(username=username_raw, is_active=False)
            if hasattr(pending_user, 'email_verification'):
                request.session['pending_verify_uid'] = pending_user.pk
                messages.warning(request, 'Votre compte n\'est pas encore vérifié. Entrez le code envoyé par email.')
                return redirect('verify_email')
        except User.DoesNotExist:
            pass

        # ── Authentification ──
        user = authenticate(request, username=username_raw, password=password)
        if user is not None:
            auth_login(request, user)
            _clear_attempts('login_user', username_raw)
            _clear_attempts('login_ip', ip)
            return redirect(next_url or _settings.LOGIN_REDIRECT_URL)
        else:
            _record_attempt('login_user', username_raw, 5, 900)
            _record_attempt('login_ip', ip, 10, 1800)
            error = 'Identifiants incorrects. Veuillez réessayer.'

    return render(request, 'registration/login.html', {
        'error':             error,
        'lockout_remaining': lockout_remaining,
        'username':          username_val,
        'next':              request.GET.get('next', ''),
    })


# ═════════════════════════════════════════════════════════════════════════════
# INSCRIPTION
# ═════════════════════════════════════════════════════════════════════════════

def register(request):
    """
    Auto-inscription avec :
      - Honeypot anti-bot (champ 'website' invisible)
      - Vérification de timing (> 3 s depuis le chargement du formulaire)
      - Rate-limit par IP : 5 inscriptions/heure
      - Validation stricte des entrées (ORM = pas d'injection SQL)
      - Création du compte inactif + envoi OTP par email (Celery)
    """
    from .models import EmailVerification

    if request.user.is_authenticated:
        return redirect(_settings.LOGIN_REDIRECT_URL)

    ip = _get_client_ip(request)
    now_ts = int(timezone.now().timestamp())

    if request.method == 'POST':
        # ── Rate-limit IP ──
        locked, remaining = _is_locked('register_ip', ip, 5, 3600)
        if locked:
            messages.error(request, f'Trop de tentatives. Réessayez dans {remaining // 60} min.')
            return render(request, 'registration/register.html', {'form_time': now_ts})

        # ── Honeypot ──
        if request.POST.get('website'):
            _record_attempt('register_ip', ip, 5, 3600)
            # Silently ignore (ne pas indiquer au bot qu'il est détecté)
            return render(request, 'registration/register.html', {'form_time': now_ts})

        # ── Timing check ──
        try:
            form_time = int(request.POST.get('form_time', 0))
        except ValueError:
            form_time = 0
        if (now_ts - form_time) < 3:
            _record_attempt('register_ip', ip, 5, 3600)
            messages.error(request, 'Formulaire soumis trop rapidement. Veuillez réessayer.')
            return render(request, 'registration/register.html', {'form_time': now_ts})

        # ── Récupérer et nettoyer les champs (l'ORM se charge de l'anti-injection SQL) ──
        username   = request.POST.get('username',   '').strip()[:150]
        email      = request.POST.get('email',      '').strip()[:254]
        first_name = request.POST.get('first_name', '').strip()[:150]
        last_name  = request.POST.get('last_name',  '').strip()[:150]
        password   = request.POST.get('password',  '')
        password2  = request.POST.get('password2', '')

        errors = []

        # Validation username
        if not username:
            errors.append("Le nom d'utilisateur est obligatoire.")
        elif not re.match(r'^[\w.@+\-]+$', username):
            errors.append("Nom d'utilisateur invalide (lettres, chiffres, @/./+/-/_ uniquement).")
        elif len(username) < 3:
            errors.append("Le nom d'utilisateur doit contenir au moins 3 caractères.")
        elif User.objects.filter(username=username).exists():
            errors.append(f'Le nom d\'utilisateur "{username}" est déjà utilisé.')

        # Validation email
        if not email or not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
            errors.append("Une adresse email valide est obligatoire.")
        elif User.objects.filter(email__iexact=email).exists():
            errors.append("Cette adresse email est déjà associée à un compte.")

        # Validation mot de passe
        if len(password) < 8:
            errors.append("Le mot de passe doit contenir au moins 8 caractères.")
        if password != password2:
            errors.append("Les mots de passe ne correspondent pas.")

        if errors:
            _record_attempt('register_ip', ip, 5, 3600)
            return render(request, 'registration/register.html', {
                'errors':     errors,
                'form_time':  now_ts,
                'username':   username,
                'email':      email,
                'first_name': first_name,
                'last_name':  last_name,
            })

        # ── Créer le compte inactif ──
        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            is_active=False,  # activé uniquement après vérification email
        )

        # ── Générer l'OTP et enregistrer en base ──
        code = _generate_otp()
        EmailVerification.objects.create(
            user=user,
            code=code,
            expires_at=timezone.now() + timedelta(minutes=10),
            last_resend_at=timezone.now(),
        )

        # ── Envoyer l'email via Celery ──
        from .tasks import send_verification_email
        send_verification_email.delay(user.pk, code)

        _clear_attempts('register_ip', ip)
        request.session['pending_verify_uid'] = user.pk
        messages.success(request, f'Compte créé ! Un code à 6 chiffres a été envoyé à {email}.')
        return redirect('verify_email')

    return render(request, 'registration/register.html', {'form_time': now_ts})


# ═════════════════════════════════════════════════════════════════════════════
# VÉRIFICATION EMAIL (saisie du code OTP)
# ═════════════════════════════════════════════════════════════════════════════

def verify_email(request):
    """
    Vérification du code OTP reçu par email.
    Protection :
      - 5 tentatives incorrectes → compte bloqué (+ verrou cache 15 min)
      - Code expiré après 10 min
    """
    from django.contrib.auth import login as auth_login
    from .models import EmailVerification

    if request.user.is_authenticated:
        return redirect(_settings.LOGIN_REDIRECT_URL)

    uid = request.session.get('pending_verify_uid')
    if not uid:
        messages.error(request, 'Session expirée. Veuillez vous inscrire à nouveau.')
        return redirect('register')

    try:
        pending_user = User.objects.get(pk=uid, is_active=False)
        verification = pending_user.email_verification
    except (User.DoesNotExist, EmailVerification.DoesNotExist):
        messages.error(request, 'Compte introuvable ou déjà activé.')
        return redirect('login')

    ctx = {
        'email':              pending_user.email,
        'seconds_until_resend': verification.seconds_until_resend(),
    }

    if request.method == 'POST':
        # Assembler le code depuis les champs d1…d6 ou le champ global 'code'
        digits = [request.POST.get(f'd{i}', '').strip() for i in range(1, 7)]
        code_input = ''.join(digits) if all(d.isdigit() for d in digits if d) else request.POST.get('code', '').strip()

        # ── Verrou brute-force par uid ──
        bf_locked, bf_rem = _is_locked('verify_otp', str(uid), 5, 900)
        if bf_locked:
            ctx['lockout_remaining'] = bf_rem
            messages.error(request, f'Trop de tentatives incorrectes. Réessayez dans {bf_rem // 60} min.')
            return render(request, 'registration/verify_email.html', ctx)

        # ── Code expiré ──
        if verification.is_expired():
            ctx['expired'] = True
            messages.warning(request, 'Le code a expiré. Cliquez sur "Renvoyer le code".')
            return render(request, 'registration/verify_email.html', ctx)

        # ── Code incorrect ──
        if code_input != verification.code:
            _record_attempt('verify_otp', str(uid), 5, 900)
            verification.attempts += 1
            verification.save(update_fields=['attempts'])
            remaining_attempts = max(0, EmailVerification.MAX_ATTEMPTS - verification.attempts)
            messages.error(request, f'Code incorrect. {remaining_attempts} tentative(s) restante(s).')
            ctx['seconds_until_resend'] = verification.seconds_until_resend()
            return render(request, 'registration/verify_email.html', ctx)

        # ── Code correct : activer le compte ──
        _clear_attempts('verify_otp', str(uid))
        pending_user.is_active = True
        pending_user.save(update_fields=['is_active'])
        verification.delete()
        try:
            del request.session['pending_verify_uid']
        except KeyError:
            pass

        # Connexion automatique
        pending_user.backend = 'django.contrib.auth.backends.ModelBackend'
        auth_login(request, pending_user)
        messages.success(request, f'Compte vérifié ! Bienvenue, {pending_user.get_full_name() or pending_user.username} !')
        return redirect(_settings.LOGIN_REDIRECT_URL)

    return render(request, 'registration/verify_email.html', ctx)


# ═════════════════════════════════════════════════════════════════════════════
# RENVOI DU CODE OTP
# ═════════════════════════════════════════════════════════════════════════════

def resend_verification(request):
    """
    Renvoie un nouveau code OTP (uniquement après le compte à rebours de 2 min).
    Génère un nouveau code, réinitialise les tentatives et la date d'expiration.
    """
    from .models import EmailVerification

    if request.user.is_authenticated:
        return redirect(_settings.LOGIN_REDIRECT_URL)

    uid = request.session.get('pending_verify_uid')
    if not uid:
        return redirect('register')

    try:
        pending_user = User.objects.get(pk=uid, is_active=False)
        verification = pending_user.email_verification
    except (User.DoesNotExist, EmailVerification.DoesNotExist):
        return redirect('login')

    if not verification.can_resend():
        messages.warning(request, 'Veuillez attendre avant de redemander un code.')
        return redirect('verify_email')

    # Nouveau code, nouvelles valeurs
    code = _generate_otp()
    verification.code          = code
    verification.expires_at    = timezone.now() + timedelta(minutes=10)
    verification.attempts      = 0
    verification.last_resend_at = timezone.now()
    verification.resend_count  += 1
    verification.save()

    _clear_attempts('verify_otp', str(uid))

    from .tasks import send_verification_email
    send_verification_email.delay(pending_user.pk, code)

    messages.success(request, f'Un nouveau code a été envoyé à {pending_user.email}.')
    return redirect('verify_email')
