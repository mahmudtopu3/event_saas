
# companies/management/commands/setup_tenant.py
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django_tenants.utils import schema_context
from companies.models import Company, Domain

class Command(BaseCommand):
    help = 'Complete tenant setup: company, domain, schema, and admin user'

    def add_arguments(self, parser):
        parser.add_argument('company_name', type=str, help='Company name')
        parser.add_argument('domain', type=str, help='Company domain (e.g., abc.localhost)')
        parser.add_argument('--admin-username', type=str, default='admin', help='Admin username')
        parser.add_argument('--admin-email', type=str, help='Admin email')
        parser.add_argument('--admin-password', type=str, default='admin123', help='Admin password')

    def handle(self, *args, **options):
        company_name = options['company_name']
        domain = options['domain']
        admin_username = options['admin_username']
        admin_email = options['admin_email'] or f"admin@{domain}"
        admin_password = options['admin_password']
        
        # Create schema name from company name
        schema_name = company_name.lower().replace(' ', '').replace('-', '').replace('.', '')[:63]
        
        try:
            # Check if company already exists
            if Company.objects.filter(schema_name=schema_name).exists():
                raise CommandError(f'Company with schema "{schema_name}" already exists')
            
            if Domain.objects.filter(domain=domain).exists():
                raise CommandError(f'Domain "{domain}" already exists')
            
            self.stdout.write(f'Creating company: {company_name}')
            self.stdout.write(f'Schema: {schema_name}')
            self.stdout.write(f'Domain: {domain}')
            
            # Create the company (tenant)
            company = Company.objects.create(
                name=company_name,
                schema_name=schema_name,
                paid_until='2099-12-31',
                on_trial=False,
            )
            
            # Create the domain
            domain_obj = Domain.objects.create(
                domain=domain,
                tenant=company,
                is_primary=True
            )
            
            self.stdout.write(self.style.SUCCESS(f'✓ Company and domain created'))
            
            # Migrate the new tenant schema
            self.stdout.write('Migrating tenant schema...')
            from django.core.management import call_command
            try:
                call_command('migrate_schemas', verbosity=0)
            except Exception as e:
                self.stdout.write(
                    self.style.WARNING(f'Migration warning: {e}')
                )
                self.stdout.write('You may need to run: python manage.py migrate_schemas')
            
            # Create admin user in the tenant schema
            User = get_user_model()
            with schema_context(schema_name):
                if User.objects.filter(username=admin_username).exists():
                    self.stdout.write(
                        self.style.WARNING(f'Admin user "{admin_username}" already exists in tenant')
                    )
                else:
                    User.objects.create_superuser(
                        username=admin_username,
                        email=admin_email,
                        password=admin_password
                    )
                    self.stdout.write(
                        self.style.SUCCESS(f'✓ Admin user created: {admin_username}')
                    )
            
            self.stdout.write(self.style.SUCCESS('\n' + '='*50))
            self.stdout.write(self.style.SUCCESS(f'🎉 TENANT SETUP COMPLETE'))
            self.stdout.write(self.style.SUCCESS('='*50))
            self.stdout.write(f'Company: {company_name}')
            self.stdout.write(f'Domain: http://{domain}:8000/')
            self.stdout.write(f'Admin: http://{domain}:8000/admin/')
            self.stdout.write(f'Login: {admin_username} / {admin_password}')
            self.stdout.write(self.style.SUCCESS('='*50))
            
        except Exception as e:
            raise CommandError(f'Error creating tenant: {str(e)}')