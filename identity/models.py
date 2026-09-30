# identity/models.py
"""
Tenant-schema identity *references*. None of these tables hold profile data.
"""
from django.conf import settings
from django.db import models


class TenantRoleAssignment(models.Model):
    """
    EventOS-specific permission for a person inside this tenant (e.g. event admin).

    This is not employer membership. Organizer access additionally requires an active
    canonical employer membership and an active EventOS entitlement, both re-checked
    on every request (see identity.authorization), so a central revocation takes effect
    even while this row still exists.
    """
    EVENT_ADMIN = 'event_admin'
    ORGANIZER = 'organizer'
    ROLES = [(EVENT_ADMIN, 'Event admin'), (ORGANIZER, 'Organizer')]

    person_id = models.BigIntegerField(db_index=True)
    role = models.CharField(max_length=32, choices=ROLES)
    granted_by = models.CharField(max_length=150)
    granted_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['person_id', 'role'], condition=models.Q(revoked_at__isnull=True),
                name='identity_role_unique_active',
            ),
        ]

    def __str__(self):
        return f"person:{self.person_id} {self.role}"


class LegacyUserLink(models.Model):
    """
    Authoritative mapping of a legacy tenant-local Django user to a canonical PersonID.

    Rows are created only from an authoritative mapping source (never by matching
    email / username / name). See `backfill_eventos_person_ids`.
    """
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='person_link')
    person_id = models.BigIntegerField(db_index=True)
    source = models.CharField(max_length=64, help_text="Identifier of the authoritative mapping source.")
    linked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['person_id'], name='identity_legacy_link_unique_person'),
        ]

    def __str__(self):
        return f"user:{self.user_id} -> person:{self.person_id}"
