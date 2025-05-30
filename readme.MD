# Event SaaS Multi-Tenant Application Setup

## Prerequisites

1. **Python 3.8+** installed
2. **PostgreSQL** running locally
3. **Git** (optional, for version control)

## Installation Steps

### 1. Create Project Structure

```bash
# Create and navigate to project directory
mkdir eventsaas
cd eventsaas

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install Django==5.0 django-tenants psycopg2-binary

# Create Django project
django-admin startproject eventsaas .
cd eventsaas

# Create apps
python manage.py startapp companies
python manage.py startapp events
```

### 2. Database Setup

```bash
# Create PostgreSQL database
createdb eventsaas

# Or using psql:
psql -U postgres
CREATE DATABASE eventsaas;
\q
```

### 3. Create Directory Structure

Create the following directories and files:

```
eventsaas/
├── companies/
│   ├── management/
│   │   ├── __init__.py
│   │   └── commands/
│   │       ├── __init__.py
│   │       └── create_company.py
│   ├── models.py
│   ├── admin.py
│   └── ...
├── events/
│   ├── models.py
│   ├── views.py
│   ├── urls.py
│   ├── admin.py
│   └── ...
├── templates/
│   ├── base.html
│   ├── events/
│   │   ├── event_list.html
│   │   ├── event_detail.html
│   │   └── my_registrations.html
│   └── registration/
│       ├── login.html
│       ├── company_login.html
│       └── signup.html
├── eventsaas/
│   ├── settings.py
│   ├── urls.py
│   ├── tenant_urls.py
│   └── ...
└── manage.py
```

### 4. Apply All Code Files

Copy all the provided code files into their respective locations:

- `settings.py` - Main Django settings
- `companies/models.py` - Company and Domain models
- `companies/admin.py` - Admin interface for companies
- `events/models.py` - Event and Registration models
- `events/views.py` - Event views
- `events/urls.py` - Event URL patterns
- `events/admin.py` - Admin interface for events
- `eventsaas/urls.py` and `eventsaas/tenant_urls.py` - URL configurations
- All template files in the `templates/` directory
- `companies/management/commands/create_company.py` - Management command

### 5. Run Migrations

```bash
# Create migrations
python manage.py makemigrations companies
python manage.py makemigrations events

# Migrate shared apps (public schema)
python manage.py migrate_schemas --shared

# Create superuser for public admin
python manage.py createsuperuser
```

### 6. Create Companies

```bash
# Create companies with their domains
python manage.py create_company "ABC Corporation" abc.localhost
python manage.py create_company "XYZ Limited" xyz.localhost

# Migrate tenant schemas (creates tables for each company)
python manage.py migrate_schemas
```

### 7. Setup Local Domains (macOS)

Edit `/etc/hosts` file:

```bash
sudo nano /etc/hosts
```

Add these lines:

```
127.0.0.1   abc.localhost
127.0.0.1   xyz.localhost
127.0.0.1   admin.localhost
```

### 8. Run the Server

```bash
python manage.py runserver 0.0.0.0:8000
```

## Access Points

### Super Admin (Public Schema)
- URL: `http://admin.localhost:8000/admin/`
- Purpose: Manage all companies, domains, and global settings
- Login: Use the superuser account you created

### Company Admin Panels
- ABC Corp: `http://abc.localhost:8000/admin/`
- XYZ Ltd: `http://xyz.localhost:8000/admin/`
- Login: `admin` / `admin123` (created by management command)

### Company Event Sites
- ABC Corp: `http://abc.localhost:8000/`
- XYZ Ltd: `http://xyz.localhost:8000/`

## Key Features

### Multi-Tenancy
- **Shared Database, Isolated Schemas**: Each company gets its own PostgreSQL schema
- **Domain-based Routing**: Different domains automatically switch to the correct tenant
- **Isolated Data**: Companies can only see their own events, users, and registrations

### User Management
- **Tenant-Specific Users**: Users created in one company cannot login to another
- **Company Admins**: Each company has its own admin panel
- **Super Admin**: Can access all companies from the public schema

### Event Management
- **Event Creation**: Company admins can create and manage events
- **User Registration**: Users can register for events within their company
- **Registration Limits**: Support for maximum attendees and registration deadlines
- **Status Tracking**: Events can be draft, published, or cancelled

## Creating Sample Data

### 1. Create Events (via admin)
1. Go to company admin: `http://abc.localhost:8000/admin/`
2. Login with `admin` / `admin123`
3. Add events under "Events" section

### 2. Create Regular Users
1. Go to `http://abc.localhost:8000/accounts/signup/`
2. Create user accounts
3. Users can then register for events

### 3. Test Registration Flow
1. Login as a regular user
2. Browse events at `http://abc.localhost:8000/`
3. Register for events
4. View registrations at "My Registrations"

## Troubleshooting

### Domain Resolution Issues
If domains don't work:
```bash
# Flush DNS cache on macOS
sudo dscacheutil -flushcache
sudo killall -HUP mDNSResponder
```

### Database Connection Issues
Check your PostgreSQL settings in `settings.py`:
```python
DATABASES = {
    'default': {
        'ENGINE': 'django_tenants.postgresql_backend',
        'NAME': 'eventsaas',
        'USER': 'postgres',
        'PASSWORD': 'your_password',
        'HOST': '127.0.0.1',
        'PORT': '5432',
    }
}
```

### Migration Issues
If you encounter migration problems:
```bash
# Reset migrations (careful - this will delete data)
python manage.py migrate_schemas --shared
python manage.py migrate_schemas
```

## Security Notes

1. **Change default passwords** in production
2. **Update SECRET_KEY** in settings.py
3. **Set DEBUG = False** in production
4. **Configure proper ALLOWED_HOSTS**
5. **Use environment variables** for sensitive data

## Next Steps

- Add email notifications for event registrations
- Implement payment integration for paid events
- Add event categories and filtering
- Create mobile-responsive design improvements
- Add API endpoints for mobile apps