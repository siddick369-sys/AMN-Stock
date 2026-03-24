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
    search_query = request.GET.get('q', '').strip()
    equipments = Equipment.objects.all()

    if search_query:
        equipments = equipments.filter(
            Q(name__icontains=search_query) | Q(reference__icontains=search_query)
        )

    context = {
        'equipments': equipments,
        'search_query': search_query,
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
    if request.user.is_staff:
        discharges = Discharge.objects.select_related('user').prefetch_related('items__equipment').all()
    else:
        discharges = Discharge.objects.filter(user=request.user).select_related('user').prefetch_related('items__equipment')
    return render(request, 'stock/discharge_list.html', {'discharges': discharges})


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
