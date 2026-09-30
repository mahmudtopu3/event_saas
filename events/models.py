# events/models.py
from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()

class Event(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('published', 'Published'),
        ('cancelled', 'Cancelled'),
    ]
    
    title = models.CharField(max_length=200)
    description = models.TextField()
    location = models.CharField(max_length=200, blank=True)
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    max_attendees = models.PositiveIntegerField(null=True, blank=True, 
                                               help_text="Leave blank for unlimited")
    registration_deadline = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    # Legacy local-user author. Nullable so Keycloak-created events need no local user;
    # existing rows keep their value. Superseded by created_by_person_id.
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_events',
                                   null=True, blank=True)
    created_by_person_id = models.BigIntegerField(
        null=True, blank=True, db_index=True,
        help_text="Canonical Skill.jobs PersonID of the creator (Keycloak-era events).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-start_date']

    def __str__(self):
        return self.title

    @property
    def is_registration_open(self):
        """Check if registration is still open"""
        if self.status != 'published':
            return False
        if self.registration_deadline and timezone.now() > self.registration_deadline:
            return False
        if self.max_attendees and self.registrations.count() >= self.max_attendees:
            return False
        return True

    @property
    def available_spots(self):
        """Return number of available spots, None if unlimited"""
        if not self.max_attendees:
            return None
        return self.max_attendees - self.registrations.count()

    @property
    def is_past(self):
        """Check if event has already happened"""
        return timezone.now() > self.end_date

class Registration(models.Model):
    STATUS_CHOICES = [
        ('confirmed', 'Confirmed'),
        ('waitlist', 'Waitlist'),
        ('cancelled', 'Cancelled'),
    ]
    
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='registrations')
    # Legacy local-user registrant, kept during the migration. New Keycloak
    # registrations set person_id instead.
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='registrations',
                             null=True, blank=True)
    person_id = models.BigIntegerField(
        null=True, blank=True, db_index=True,
        help_text="Canonical Skill.jobs PersonID. Profile data is read live from the Person API.",
    )
    # What was submitted *for this event at that time* (badge name, event answers,
    # accessibility request...). Historical record only; never an editable profile.
    registration_snapshot = models.JSONField(default=dict, blank=True)
    snapshot_version = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='confirmed')
    notes = models.TextField(blank=True, help_text="Any special requirements or notes")
    registered_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('event', 'user')
        ordering = ['-registered_at']
        constraints = [
            models.UniqueConstraint(
                fields=['event', 'person_id'],
                condition=models.Q(person_id__isnull=False),
                name='events_registration_unique_event_person',
            ),
            models.CheckConstraint(
                check=models.Q(user__isnull=False) | models.Q(person_id__isnull=False),
                name='events_registration_has_identity',
            ),
        ]

    @property
    def registrant_label(self):
        if self.person_id is not None:
            return f"person:{self.person_id}"
        return self.user.username if self.user_id else '-'

    def __str__(self):
        return f"{self.registrant_label} - {self.event.title}"