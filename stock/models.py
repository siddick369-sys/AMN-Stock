from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class Equipment(models.Model):
    name = models.CharField(max_length=200, verbose_name="Nom de l'équipement")
    reference = models.CharField(max_length=100, unique=True, verbose_name="Référence")
    quantity = models.PositiveIntegerField(default=0, verbose_name="Quantité en stock")
    defective_quantity = models.PositiveIntegerField(default=0, verbose_name="Quantité défectueuse")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Équipement"
        verbose_name_plural = "Équipements"
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.reference})"

    @property
    def available_quantity(self):
        return self.quantity

    @property
    def is_low_stock(self):
        from django.conf import settings
        threshold = getattr(settings, 'LOW_STOCK_THRESHOLD', 5)
        return self.quantity <= threshold


class Discharge(models.Model):
    STATUS_CHOICES = [
        ('open', 'En cours'),
        ('closed', 'Clôturée'),
    ]
    user = models.ForeignKey(User, on_delete=models.PROTECT, verbose_name="Technicien")
    date = models.DateTimeField(default=timezone.now, verbose_name="Date de départ")
    destination = models.CharField(max_length=300, verbose_name="Destination / Mission")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='open', verbose_name="Statut")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Décharge"
        verbose_name_plural = "Décharges"
        ordering = ['-date']

    def __str__(self):
        return f"Décharge #{self.pk} - {self.user.get_full_name() or self.user.username} - {self.destination}"


class DischargeItem(models.Model):
    discharge = models.ForeignKey(Discharge, on_delete=models.CASCADE, related_name='items', verbose_name="Décharge")
    equipment = models.ForeignKey(Equipment, on_delete=models.PROTECT, verbose_name="Équipement")
    quantity = models.PositiveIntegerField(verbose_name="Quantité retirée")

    class Meta:
        verbose_name = "Article de décharge"
        verbose_name_plural = "Articles de décharge"
        unique_together = ('discharge', 'equipment')

    def __str__(self):
        return f"{self.equipment.name} x{self.quantity}"


class FieldReport(models.Model):
    discharge = models.OneToOneField(Discharge, on_delete=models.CASCADE, related_name='field_report', verbose_name="Décharge")
    description = models.TextField(verbose_name="Rapport détaillé des actions sur le terrain")
    date = models.DateTimeField(default=timezone.now, verbose_name="Date de retour")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Rapport de terrain"
        verbose_name_plural = "Rapports de terrain"
        ordering = ['-date']

    def __str__(self):
        return f"Rapport #{self.pk} - Décharge #{self.discharge.pk}"


class ReturnedItem(models.Model):
    CONDITION_CHOICES = [
        ('good', 'Bon état'),
        ('defective', 'Défectueux'),
    ]
    field_report = models.ForeignKey(FieldReport, on_delete=models.CASCADE, related_name='returned_items', verbose_name="Rapport")
    equipment = models.ForeignKey(Equipment, on_delete=models.PROTECT, verbose_name="Équipement")
    quantity_returned = models.PositiveIntegerField(verbose_name="Quantité ramenée")
    condition = models.CharField(max_length=10, choices=CONDITION_CHOICES, default='good', verbose_name="État")

    class Meta:
        verbose_name = "Article retourné"
        verbose_name_plural = "Articles retournés"

    def __str__(self):
        return f"{self.equipment.name} x{self.quantity_returned} ({self.get_condition_display()})"


class StockMovement(models.Model):
    MOVEMENT_TYPES = [
        ('in', 'Entrée'),
        ('out', 'Sortie'),
    ]
    equipment = models.ForeignKey(Equipment, on_delete=models.CASCADE, related_name='movements', verbose_name="Équipement")
    movement_type = models.CharField(max_length=3, choices=MOVEMENT_TYPES, verbose_name="Type de mouvement")
    quantity = models.PositiveIntegerField(verbose_name="Quantité")
    date = models.DateTimeField(default=timezone.now, verbose_name="Date")
    note = models.CharField(max_length=300, blank=True, verbose_name="Note")

    class Meta:
        verbose_name = "Mouvement de stock"
        verbose_name_plural = "Mouvements de stock"
        ordering = ['-date']

    def __str__(self):
        return f"{self.get_movement_type_display()} - {self.equipment.name} x{self.quantity}"
