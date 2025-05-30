from django.core.management.base import BaseCommand, CommandError
from companies.models import Company, Domain

class Command(BaseCommand):
    help = 'Setup public domain for super admin access'

    def add_arguments(self, parser):
        parser.add_argument('--domain', type=str, default='admin.localhost', 
                          help='Public domain (default: admin.localhost)')

    def handle(self, *args, **options):
        domain = options['domain']
        
        try:
            # Get or create public tenant
            public_tenant, created = Company.objects.get_or_create(
                schema_name='public',
                defaults={'name': 'Public Schema'}
            )
            
            if created:
                self.stdout.write(self.style.SUCCESS('✓ Public tenant created'))
            
            # Create public domain
            domain_obj, created = Domain.objects.get_or_create(
                domain=domain,
                defaults={
                    'tenant': public_tenant,
                    'is_primary': True
                }
            )
            
            if created:
                self.stdout.write(self.style.SUCCESS(f'✓ Public domain created: {domain}'))
            else:
                self.stdout.write(self.style.WARNING(f'Public domain already exists: {domain}'))
            
            self.stdout.write(self.style.SUCCESS('\n' + '='*40))
            self.stdout.write(self.style.SUCCESS('🔧 PUBLIC SETUP COMPLETE'))
            self.stdout.write(self.style.SUCCESS('='*40))
            self.stdout.write(f'Super Admin: http://{domain}:8000/admin/')
            self.stdout.write(self.style.SUCCESS('='*40))
            
        except Exception as e:
            raise CommandError(f'Error setting up public domain: {str(e)}')