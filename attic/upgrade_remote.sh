#!/bin/bash
# VaultSync Remote Upgrade Helper v3
# This version tries multiple common superusers to fix the migration.

set -e

# --- Configuration ---
NEW_USER="vaultsync"
DB_NAME="vaultsync"
DB_CONTAINER="vaultsync_db"
NEW_PASS="vaultsync_secure_password"

echo "📡 Starting VaultSync Remote Upgrade..."

# 1. Check if DB container is running
if ! docker ps | grep -q "$DB_CONTAINER"; then
    echo "🚀 Starting Database container..."
    docker compose up -d db
    sleep 5
fi

# 2. Brute-force detection of the current Superuser
echo "🔍 Detecting database superuser..."
SUPERUSER=""
for user in "postgres" "neosync" "retrosync" "vaultsync" "admin" "root"; do
    if docker exec "$DB_CONTAINER" psql -U "$user" -c "select 1" >/dev/null 2>&1; then
        SUPERUSER="$user"
        break
    fi
done

if [ -z "$SUPERUSER" ]; then
    echo "❌ Error: Could not find a valid superuser to perform migration."
    echo "Common defaults failed. Please run this command to see available users:"
    echo "  docker exec $DB_CONTAINER psql -c \"SELECT rolname FROM pg_roles;\""
    exit 1
fi

echo "✅ Found valid superuser: $SUPERUSER"

# 3. Create the new role and grant permissions
echo "🔐 Configuring '$NEW_USER' role..."
docker exec "$DB_CONTAINER" psql -U "$SUPERUSER" -c "DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '$NEW_USER') THEN
        CREATE ROLE $NEW_USER WITH LOGIN PASSWORD '$NEW_PASS' SUPERUSER;
    END IF;
END
\$\$;"

# 4. Ensure database ownership
echo "📂 Setting database ownership..."
# We try to ensure the database 'vaultsync' exists, if not we might be using 'neosync' DB
CURRENT_DB=$(docker exec "$DB_CONTAINER" psql -U "$SUPERUSER" -tAc "SELECT datname FROM pg_database WHERE datname IN ('vaultsync', 'neosync', 'retrosync') LIMIT 1;")

if [ -z "$CURRENT_DB" ]; then
    echo "⚠️  No target database found. Creating '$DB_NAME'..."
    docker exec "$DB_CONTAINER" psql -U "$SUPERUSER" -c "CREATE DATABASE $DB_NAME OWNER $NEW_USER;"
else
    echo "✅ Found database: $CURRENT_DB. Transferring ownership to $NEW_USER..."
    docker exec "$DB_CONTAINER" psql -U "$SUPERUSER" -c "ALTER DATABASE $CURRENT_DB OWNER TO $NEW_USER;"
    # If the DB name is different from what the app expects, we might need to rename it
    if [ "$CURRENT_DB" != "$DB_NAME" ]; then
        echo "🔄 Renaming database $CURRENT_DB to $DB_NAME..."
        docker exec "$DB_CONTAINER" psql -U "$SUPERUSER" -c "ALTER DATABASE $CURRENT_DB RENAME TO $DB_NAME;"
    fi
fi

# 5. Restart everything with the new code
echo "🔄 Restarting VaultSync Server with latest build..."
docker compose up --build -d

echo "✅ Upgrade Complete!"
docker compose logs -f vaultsync --tail 20
