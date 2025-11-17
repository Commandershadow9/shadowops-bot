# GuildScout (Discord Guild Management Bot)

## Project Overview
Discord bot for ranking and managing guild members based on activity and membership duration.
Used for fair guild recruitment and community management.

## Technology Stack
- **Language**: Python 3.11
- **Framework**: Discord.py 2.3.0+
- **Database**: SQLite (aiosqlite) for caching
- **Data Processing**: pandas, openpyxl
- **Configuration**: YAML (PyYAML)
- **Environment**: python-dotenv

## Core Functionality

### Ranking System
- **40%** Membership Duration (days in Discord server)
- **60%** Activity Level (message count)
- Configurable weights in YAML config
- Fair and transparent scoring algorithm

### Main Commands
1. `/analyze` - Analyze users by role and generate rankings
2. `/my-score` - Users check their own ranking
3. `/guild-status` - View all guild members with scores
4. `/set-max-spots` - Configure maximum guild size

### Performance Features
- **SQLite Caching**: 60-70% cache hit rate
- **5x Faster**: Channel-first algorithm with parallel processing
- **Auto-Retry**: Rate-limit handling with exponential backoff
- **Batch Progress**: Real-time updates during long operations

## Guild Management (V2.0)

### Interactive Features
- **WWM Release Timer**: Auto-updating countdown (every 10s)
- **Role Assignment**: Button confirmation before mass role changes
- **Welcome Messages**: Auto-updating channel info with debouncing
- **Spot Management**: Correctly counts exclusion roles

### Data Export
- CSV export of complete rankings
- Excel-compatible format
- Timestamp and metadata included

## Architecture

### Directory Structure
```
/home/cmdshadow/GuildScout/
├── config/          # YAML configuration files
├── data/            # SQLite cache database
├── exports/         # CSV export files
├── logs/            # Bot operation logs
├── output/          # Analysis results
├── src/             # Source code
│   ├── commands/    # Discord slash commands
│   ├── core/        # Core bot logic
│   ├── database/    # SQLite cache manager
│   ├── models/      # Data models
│   ├── services/    # Business logic
│   └── utils/       # Helper functions
└── tests/           # Unit tests
```

### Key Components
1. **Bot Core** - Discord client and event handling
2. **Analyzer** - User ranking and scoring engine
3. **Cache Manager** - SQLite-based result caching
4. **Export Service** - CSV/Excel generation
5. **Role Manager** - Mass role assignment with safety

## Configuration
- **Config File**: `config/config.yaml`
- **Bot Token**: Discord application token
- **Guild ID**: Target Discord server ID
- **Admin Roles**: Role IDs with admin permissions
- **Scoring Weights**: Duration vs. activity balance

## Discord Intents Required
- **Server Members Intent** - Access member list
- **Message Content Intent** - Count user messages

## DO-NOT-TOUCH Rules

### NEVER Modify
1. **Database Schema** - Cache integrity critical
2. **Scoring Algorithm** - Would change historical rankings
3. **Role Assignment Logic** - Could assign wrong roles
4. **Bot Token** - Would break authentication
5. **Active Analysis Operations** - Data corruption risk

### ALWAYS Require Approval
1. Mass role assignments
2. Database schema changes
3. Configuration changes (weights, thresholds)
4. Bot token rotation
5. Discord permissions changes

## Safe Operations
1. Log analysis and monitoring
2. Cache statistics viewing
3. CSV export generation
4. Read-only database queries
5. Performance metrics collection

## Common Security Issues

### Rate Limiting
- **Issue**: Discord API rate limits during analysis
- **Fix**: Already handled with exponential backoff
- **Action**: No intervention needed, auto-resolves

### Cache Corruption
- **Issue**: SQLite database corruption
- **Fix**: Delete `data/*.db`, will rebuild on next analysis
- **Prevention**: Regular backups

### Permission Issues
- **Issue**: Bot lacks permissions to read channels/roles
- **Fix**: Check bot role hierarchy and channel permissions
- **Action**: Ensure bot role is high enough in server

### Token Exposure
- **Issue**: Bot token leaked in logs/config
- **Fix**: Rotate token immediately in Discord Developer Portal
- **Prevention**: Use environment variables, .gitignore config

## Deployment Info
- **Process Manager**: Direct Python execution
- **Start Command**: `python run.py`
- **PID File**: `bot-service.pid`
- **Logs**: `bot.log`, `logs/`
- **Restart Policy**: Manual restart required

## Dependencies
- Discord API access
- SQLite database
- Python 3.11+ environment
- Network access for Discord gateway
- Read permissions for all analyzed channels

## Project Location
`/home/cmdshadow/GuildScout`

## Logging
- **Main Log**: `bot.log`
- **Operation Logs**: `logs/`
- **Cache Stats**: Logged during analysis
- **Error Tracking**: Discord errors logged with full traceback

## Critical Features

### Caching System
- SQLite-based persistent cache
- 24-hour cache validity
- Automatic cache invalidation
- Hit rate monitoring (~60-70%)

### Progress Updates
- Real-time Discord embed updates
- Batch progress indicators
- ETA calculations
- Error reporting

### Role Management
- Interactive confirmation before mass changes
- Spot limit enforcement
- Exclusion role handling
- Dry-run capability

## Security Considerations
- Bot has role management permissions
- Can read all server messages (for counting)
- Access to member list and join dates
- Admin commands restricted by role IDs
- All operations logged

## Startup Process
1. Load YAML configuration
2. Initialize Discord client with intents
3. Connect to SQLite cache database
4. Register slash commands
5. Connect to Discord gateway
6. Ready for commands

## Common Issues

### Analysis Taking Too Long
- **Cause**: Large server with many channels
- **Fix**: Normal, cache will speed up next run
- **Optimization**: Already uses channel-first algorithm

### Cache Not Working
- **Cause**: Database file permissions or corruption
- **Fix**: Check `data/` directory permissions
- **Recovery**: Delete cache, will rebuild

### Commands Not Appearing
- **Cause**: Slash commands not synced to guild
- **Fix**: Bot auto-syncs on startup, wait ~5 minutes
- **Force Sync**: Restart bot

### Role Assignment Failed
- **Cause**: Bot role lower than target roles
- **Fix**: Move bot role higher in server settings
- **Check**: Role hierarchy in Discord server settings

## Monitoring
- Bot login confirmation in logs
- Command usage tracking
- Cache hit/miss statistics
- Analysis performance metrics
- Error rate monitoring

## Performance
- **Typical Analysis**: 30-120 seconds (depending on server size)
- **Cache Hit**: <1 second response
- **Cache Miss**: Full analysis required
- **Message Scanning**: Parallel processing across channels
- **Memory Usage**: Low (~50-100MB)

## Future Enhancements (Planned)
- Web dashboard for rankings
- Historical trend analysis
- Advanced filtering options
- Multi-guild support
- API endpoint for external integrations
