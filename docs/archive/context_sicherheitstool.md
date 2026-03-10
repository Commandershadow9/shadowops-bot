# Sicherheitstool (Security Management System)

## Project Overview
Enterprise-grade security management and shift planning system for security companies.
Customer-facing production application with real customers and active operations.

## Technology Stack
- **Backend**: Node.js with TypeScript
- **Database**: PostgreSQL (production database)
- **ORM**: Prisma
- **API**: RESTful API with JWT authentication
- **Port**: 3001
- **Environment**: Production

## Critical Components

### Database
- **Type**: PostgreSQL
- **Connection**: `postgresql://admin:admin123@localhost:5432/sicherheitsdienst_db?schema=public`
- **Status**: PRODUCTION - Contains live customer data
- **Backup**: Daily backups configured
- **Risk Level**: CRITICAL - Any downtime affects customers

### Key Services
1. **Authentication Service** - JWT-based user authentication
2. **Shift Planning** - Employee shift management and scheduling
3. **Site Management** - Security site/location management
4. **Audit Log Service** - Compliance and forensic tracking
5. **Dashboard** - Critical metrics and monitoring

### API Endpoints (Examples)
- `/api/auth/*` - Authentication
- `/api/dashboard/critical` - Critical security metrics
- `/api/shifts/*` - Shift management
- `/api/sites/*` - Site management
- `/api/audit/*` - Audit logging

## DO-NOT-TOUCH Rules

### NEVER Modify
1. **Database Schema** - Without explicit approval and backup
2. **Authentication System** - Customer access critical
3. **Production API Endpoints** - Breaking changes affect customers
4. **JWT Secret Keys** - Would invalidate all sessions
5. **Database Migrations** - Must be tested in staging first

### ALWAYS Require Approval
1. Any database modification
2. Changes to authentication/authorization
3. API endpoint modifications
4. Service restarts during business hours
5. Configuration changes

## Safe Operations
1. Log analysis and monitoring
2. Read-only database queries
3. Performance metrics collection
4. Security scanning (passive)
5. Backup verification

## Common Security Issues

### Trivy Vulnerabilities
- **Action**: Update npm packages with `npm audit fix`
- **Caution**: Test in dev environment first
- **Rollback**: Keep backup of package-lock.json

### Database Issues
- **Action**: Always backup before any change
- **Recovery**: Automated daily backups available
- **Monitoring**: Connection pool status, query performance

### CrowdSec Threats
- **Action**: Automatic IP banning is safe
- **Review**: Check for false positives (VPN users, office IPs)
- **Whitelist**: Office IPs and known good actors

## Deployment Info
- **Process Manager**: NPM (direct execution)
- **Start Command**: `DATABASE_URL="postgresql://..." PORT=3001 npm start`
- **Restart Policy**: Requires manual restart
- **Health Check**: HTTP GET /api/health

## Dependencies
- Requires PostgreSQL running
- Requires Node.js 18+
- Requires network access on port 3001
- Database migrations must be up-to-date

## Project Location
`/home/cmdshadow/project`

## Security Considerations
- Customer data privacy (GDPR compliance)
- Audit logging for all critical operations
- Role-based access control (ADMIN, MANAGER, EMPLOYEE)
- Session management and timeout
- SQL injection prevention (Prisma ORM)
- XSS prevention on frontend

## Monitoring
- Application logs in project directory
- Database connection monitoring
- API response times
- Authentication failures
- Audit log integrity
