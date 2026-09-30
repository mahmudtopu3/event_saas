# events/registrants.py
"""
Who "owns" a registration on this request, independent of the auth path.

- Keycloak principal → canonical PersonID.
- Legacy local user  → the local user, plus the PersonID it's authoritatively
  linked to (LegacyUserLink), so linked users see one history across both logins.
"""
from django.db.models import Q

from identity.models import LegacyUserLink
from identity.principal import get_principal


def _linked_person_id(user):
    link = LegacyUserLink.objects.filter(user_id=user.pk).only('person_id').first()
    return link.person_id if link else None


def owner_q(request):
    principal = get_principal(request)
    if principal is not None:
        # Also rows made through a legacy account authoritatively linked to this person
        # and not yet stamped with person_id by the backfill.
        linked_users = LegacyUserLink.objects.filter(person_id=principal.person_id).values('user_id')
        return Q(person_id=principal.person_id) | Q(user_id__in=linked_users)
    person_id = _linked_person_id(request.user)
    q = Q(user=request.user)
    return q | Q(person_id=person_id) if person_id else q


def create_kwargs(request):
    principal = get_principal(request)
    if principal is not None:
        return {'person_id': principal.person_id}
    return {'user': request.user, 'person_id': _linked_person_id(request.user)}


def creator_kwargs(request):
    principal = get_principal(request)
    if principal is not None:
        return {'created_by_person_id': principal.person_id}
    return {'created_by': request.user, 'created_by_person_id': _linked_person_id(request.user)}
